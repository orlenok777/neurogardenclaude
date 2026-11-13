// Test stand: runs supabase.sql in an in-process Postgres and serves /rest/v1/rpc/<fn> the way PostgREST does,
// executing every call as the low-privilege role "anon". `node server.mjs <sql> <port>` serves; `--selftest` runs SQL-level checks.
import { PGlite } from '@electric-sql/pglite';
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto';
import http from 'node:http';
import fs from 'node:fs';

const sqlPath = process.argv[2], port = +process.argv[3] || 8787, selftest = process.argv.includes('--selftest');
const db = new PGlite({ extensions: { pgcrypto } });
await db.exec(`create schema if not exists extensions; create role anon nologin; create role authenticated nologin; grant usage on schema extensions to anon;`);
const sql = fs.readFileSync(sqlPath, 'utf8');
await db.exec(sql);
await db.exec(sql); // must be re-runnable
await db.exec(`insert into public.ng_config(k,v) values('admin', extensions.crypt('test-pass-123', extensions.gen_salt('bf'))) on conflict(k) do update set v=excluded.v;`);
console.log('sql ok (applied twice)');

async function asAnon(q, vals = []) {
  return db.transaction(async tx => { await tx.exec('set local role anon'); return tx.query(q, vals); });
}
async function rpc(fn, args = {}) {
  const keys = Object.keys(args);
  const vals = keys.map(k => (args[k] !== null && typeof args[k] === 'object') ? JSON.stringify(args[k]) : args[k]);
  const out = await asAnon(`select public.${fn}(${keys.map((k, i) => `${k} => $${i + 1}`).join(', ')}) as r`, vals);
  return out.rows[0].r ?? null;
}

if (selftest) {
  let pass = 0, fail = 0;
  const ok = (name, cond, extra = '') => { cond ? pass++ : fail++; console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : ' ' + extra)); };
  const denied = async (name, q) => { try { await asAnon(q); ok(name, false, 'was allowed'); } catch (e) { ok(name, /permission denied/.test(e.message), e.message); } };
  const throws = async (name, f, re) => { try { await f(); ok(name, false, 'no error'); } catch (e) { ok(name, re.test(e.message), e.message); } };
  const K1 = 'a'.repeat(40), K2 = 'b'.repeat(40), st = (o = {}) => ({ nick: 'Тест-QA', apples: 1, hist: [{ g: 'seq', at: 1, l: 0, d: 5, s: 100, ok: true }], ...o });

  await denied('anon cannot read ng_players', 'select * from public.ng_players');
  await denied('anon cannot read ng_config', 'select * from public.ng_config');
  await denied('anon cannot read ng_words', 'select * from public.ng_words');
  await denied('anon cannot read ng_auth', 'select * from public.ng_auth');
  await denied('anon cannot write ng_players', `insert into public.ng_players(key_hash,pid) values('x','y')`);
  await denied('anon cannot call ng_h', `select public.ng_h('x')`);
  await denied('anon cannot call ng_admin_check', `select public.ng_admin_check('x')`);
  await denied('anon cannot call ng_meta', `select public.ng_meta(null)`);

  const a = await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st(), p_rev: null });
  ok('save new name returns id and rev 1', a && a.id && a.rev === 1, JSON.stringify(a));
  const a2 = await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st({ apples: 2 }), p_rev: 1 });
  ok('save with current rev bumps rev', a2.rev === 2, JSON.stringify(a2));
  const c = await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st({ apples: 9 }), p_rev: 1 });
  ok('stale rev returns conflict with stored state', c.conflict === true && c.rev === 2 && c.state.apples === 2, JSON.stringify(c));
  const c2 = await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st({ apples: 9 }), p_rev: null });
  ok('null rev on existing row returns conflict', c2.conflict === true);
  ok('load own rows', (await rpc('ng_load', { p_key: K1 })).length === 1);
  ok('other key loads nothing', (await rpc('ng_load', { p_key: K2 })).length === 0);
  await rpc('ng_delete', { p_key: K2, p_pid: 'p1' });
  ok('other key cannot delete', (await rpc('ng_load', { p_key: K1 })).length === 1);
  ok('other key ping returns null', (await rpc('ng_ping', { p_key: K2, p_pid: 'p1' })) === null);

  await throws('hist:[null] rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st({ hist: [null] }), p_rev: null }), /bad hist/);
  await throws('hist entry without at rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st({ hist: [{ g: 'seq' }] }), p_rev: null }), /bad hist/);
  await throws('hist not array rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st({ hist: 'x' }), p_rev: null }), /bad hist/);
  await throws('short key rejected', () => rpc('ng_save', { p_key: 'short', p_pid: 'p1', p_state: st(), p_rev: null }), /bad key/);
  await throws('bad pid rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'a b', p_state: st(), p_rev: null }), /bad key/);
  await throws('empty nick rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st({ nick: '  ' }), p_rev: null }), /bad nick/);
  await throws('long nick rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st({ nick: 'x'.repeat(25) }), p_rev: null }), /bad nick/);
  await throws('oversized state rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st({ pad: 'x'.repeat(90000) }), p_rev: null }), /bad state/);
  const odd = await rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st({ style: 'abc', apples: '<b>', hidden: 'yes', flowers: -5 }), p_rev: null });
  const pub = await rpc('ng_public');
  const mine = pub.find(t => t.id === odd.id);
  ok('junk typed fields stored as safe numbers', mine && mine.style === 0 && mine.apples === 0 && mine.flowers === 0, JSON.stringify(mine));
  ok('public list has no state or key', pub.every(t => !('state' in t) && !('key_hash' in t)));
  for (let i = 2; i <= 8; i++) await rpc('ng_save', { p_key: K2, p_pid: 'p' + i, p_state: st(), p_rev: null });
  await throws('ninth name rejected', () => rpc('ng_save', { p_key: K2, p_pid: 'p9', p_state: st(), p_rev: null }), /too many names/);

  await rpc('ng_delete', { p_key: K1, p_pid: 'p1' });
  const g = await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st(), p_rev: 2 });
  ok('deleted name is not resurrected by a late save', g.gone === true && (await rpc('ng_load', { p_key: K1 })).length === 0, JSON.stringify(g));

  await throws('word without a tree rejected', () => rpc('ng_word', { p_key: K1, p_nick: 'Тест-QA', p_text: 'привет' }), /no tree/);
  await rpc('ng_word', { p_key: K2, p_nick: 'Тест-QA', p_text: 'привет' });
  const w1 = await rpc('ng_words', { p_key: K1 }), w2 = await rpc('ng_words', { p_key: K2 });
  ok('words: mine flag only for author', w1[0].mine === false && w2[0].mine === true);
  await rpc('ng_word_del', { p_key: K1 });
  ok('other key cannot delete a word', (await rpc('ng_words', { p_key: K2 })).length === 1);

  ok('admin: wrong password forbidden', (await rpc('ng_admin_list', { p_pass: 'nope' })).error === 'forbidden');
  const good = await rpc('ng_admin_list', { p_pass: 'test-pass-123' });
  ok('admin: right password lists rows with state', good.ok === true && good.rows.length === 8 && good.rows[0].state);
  ok('admin: cheer needs password', (await rpc('ng_admin_cheer', { p_pass: 'x', p_id: odd.id, p_text: 'hi' })).ok === false);
  ok('admin: delete needs password', (await rpc('ng_admin_delete', { p_pass: 'x', p_id: odd.id })).ok === false);
  ok('admin: word delete needs password', (await rpc('ng_admin_word_del', { p_pass: 'x', p_id: w2[0].id })).ok === false);
  ok('admin: failed calls changed nothing', (await rpc('ng_load', { p_key: K2 })).length === 8 && (await rpc('ng_words', { p_key: K2 })).length === 1);
  // three wrong so far (x, x, x) after the successful reset; two more lock the address
  await rpc('ng_admin_list', { p_pass: 'bad4' });
  const fifth = await rpc('ng_admin_list', { p_pass: 'bad5' });
  ok('admin: fifth wrong password still forbidden', fifth.error === 'forbidden');
  ok('admin: locked after five wrong passwords, even with the right one', (await rpc('ng_admin_list', { p_pass: 'test-pass-123' })).error === 'locked');
  await db.exec(`update public.ng_auth set locked_until = now() - interval '1 second'`);
  ok('admin: works again after lock expires', (await rpc('ng_admin_list', { p_pass: 'test-pass-123' })).ok === true);
  await rpc('ng_admin_cheer', { p_pass: 'test-pass-123', p_id: odd.id, p_text: 'Молодец' });
  const ld = await rpc('ng_load', { p_key: K2 });
  ok('cheer reaches only that name', ld.find(r => r.id === odd.id).cheer.text === 'Молодец' && ld.filter(r => r.cheer).length === 1);
  await rpc('ng_cheer_ack', { p_key: K1, p_pid: 'p1' });
  ok('other key cannot clear a cheer', (await rpc('ng_load', { p_key: K2 })).find(r => r.id === odd.id).cheer !== null);
  await rpc('ng_admin_delete', { p_pass: 'test-pass-123', p_id: odd.id });
  ok('admin delete leaves a tombstone', (await rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st(), p_rev: null })).gone === true);
  const sp = await db.query(`select proname, proconfig from pg_proc where pronamespace='public'::regnamespace and proname like 'ng_%'`);
  ok('every function pins an empty search_path', sp.rows.length === 17 && sp.rows.every(r => (r.proconfig || []).some(c => c === 'search_path=""' || c === 'search_path=')), JSON.stringify(sp.rows.map(r => [r.proname, r.proconfig])));
  const rls = await db.query(`select relname, relrowsecurity from pg_class where relnamespace='public'::regnamespace and relname like 'ng_%' and relkind='r'`);
  ok('RLS enabled on all five tables', rls.rows.length === 5 && rls.rows.every(r => r.relrowsecurity));
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

http.createServer((req, res) => {
  const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'POST,OPTIONS' };
  if (req.method === 'OPTIONS') { res.writeHead(204, cors); return res.end(); }
  const m = req.url.match(/^\/rest\/v1\/rpc\/(ng_[a-z_]+)$/);
  if (!m) { res.writeHead(404, cors); return res.end('{}'); }
  let body = '';
  req.on('data', c => body += c);
  req.on('end', async () => {
    try {
      const out = await rpc(m[1], body ? JSON.parse(body) : {});
      res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
      res.end(JSON.stringify(out));
    } catch (e) {
      res.writeHead(400, { ...cors, 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ message: String(e.message || e) }));
    }
  });
}).listen(port, () => console.log('listening', port));
