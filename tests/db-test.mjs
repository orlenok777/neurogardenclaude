// Local test stand. Never touches the real database.
//
//   node tests/db-test.mjs supabase.sql 0 --selftest     SQL-level checks, then exit
//   node tests/db-test.mjs supabase.sql 8787             serve a stand for tests/browser-test.py
//
// It runs Postgres inside Node (pglite), applies supabase.sql (schema 2) and supabase-auth.sql (schema 3),
// and answers /rest/v1/rpc/<fn> the way PostgREST does: without a token as role "anon", with a valid
// token as role "authenticated" with that user's id in the JWT claims.
// It also imitates the few Supabase Auth endpoints the site uses for Google sign-in (PKCE), with a fake
// Google step: a test picks who "signs in" through /__test/google. Real Google and real Supabase Auth
// are NOT exercised here.
import { PGlite } from '@electric-sql/pglite';
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const sqlPath = process.argv[2], port = +process.argv[3] || 8787, selftest = process.argv.includes('--selftest');
const dir = path.dirname(path.resolve(sqlPath));
const schema2 = fs.readFileSync(sqlPath, 'utf8');
const schema3 = fs.readFileSync(path.join(dir, 'supabase-auth.sql'), 'utf8');
const ownerSql = fs.readFileSync(path.join(dir, 'supabase-owner.sql'), 'utf8');
const ZERO = '00000000-0000-0000-0000-000000000000';

const db = new PGlite({ extensions: { pgcrypto } });
await db.exec(`
  create schema if not exists extensions;
  create role anon nologin; create role authenticated nologin;
  grant usage on schema extensions to anon, authenticated;
  create schema auth;
  create table auth.users (id uuid primary key default gen_random_uuid(), email text unique, email_confirmed_at timestamptz);
  create table auth.identities (user_id uuid references auth.users(id), provider text, primary key (user_id, provider));
  create function auth.jwt() returns jsonb language sql stable as $$ select coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb) $$;
  create function auth.uid() returns uuid language sql stable as $$ select nullif(auth.jwt() ->> 'sub', '')::uuid $$;
  grant usage on schema auth to anon, authenticated;
`);
await db.exec(schema2);
await db.exec(schema2); // must be re-runnable
console.log('schema 2 ok (applied twice)');

async function as(role, claims, q, vals = []) {
  return db.transaction(async tx => {
    await tx.query(`select set_config('request.jwt.claims', $1, true)`, [claims ? JSON.stringify(claims) : '']);
    await tx.exec(`set local role ${role}`);
    return tx.query(q, vals);
  });
}
async function rpc(fn, args = {}, who = null) {
  const keys = Object.keys(args);
  const vals = keys.map(k => (args[k] !== null && typeof args[k] === 'object') ? JSON.stringify(args[k]) : args[k]);
  const q = `select public.${fn}(${keys.map((k, i) => `${k} => $${i + 1}`).join(', ')}) as r`;
  const out = who ? await as('authenticated', { sub: who.uid, email: who.email, role: 'authenticated', is_anonymous: !!who.anonymous }, q, vals)
                  : await as('anon', null, q, vals);
  return out.rows[0].r ?? null;
}
async function googleUser(email, { confirmed = true, provider = 'google' } = {}) {
  const u = await db.query(`insert into auth.users(email, email_confirmed_at) values ($1, ${confirmed ? 'now()' : 'null'})
    on conflict (email) do update set email = excluded.email returning id`, [email]);
  await db.query(`insert into auth.identities(user_id, provider) values ($1, $2) on conflict do nothing`, [u.rows[0].id, provider]);
  return { uid: u.rows[0].id, email };
}
const assignOwner = uid => db.exec(ownerSql.replace(ZERO, uid));
const owners = async () => +(await db.query('select count(*) as n from public.ng_owner')).rows[0].n;

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => { cond ? pass++ : fail++; console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : ' ' + extra)); };
const denied = async (name, f) => { try { await f(); ok(name, false, 'was allowed'); } catch (e) { ok(name, /permission denied/.test(e.message), e.message); } };
const throws = async (name, f, re) => { try { await f(); ok(name, false, 'no error'); } catch (e) { ok(name, re.test(e.message), e.message); } };
const K1 = 'a'.repeat(40), K2 = 'b'.repeat(40), st = (o = {}) => ({ nick: 'Тест-QA', apples: 1, hist: [{ g: 'seq', at: 1, l: 0, d: 5, s: 100, ok: true }], ...o });

if (selftest) {
  // ---------- schema 2 (as applied to the live project) ----------
  await db.exec(`insert into public.ng_config(k,v) values('admin', extensions.crypt('test-pass-123', extensions.gen_salt('bf'))) on conflict(k) do update set v=excluded.v;`);
  for (const t of ['ng_players', 'ng_config', 'ng_words', 'ng_auth']) await denied(`anon cannot read ${t}`, () => as('anon', null, `select * from public.${t}`));
  await denied('anon cannot write ng_players', () => as('anon', null, `insert into public.ng_players(key_hash,pid) values('x','y')`));
  for (const f of [`ng_h('x')`, `ng_admin_check('x')`, `ng_meta(null)`]) await denied(`anon cannot call ${f.split('(')[0]}`, () => as('anon', null, `select public.${f}`));

  const a = await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st(), p_rev: null });
  ok('save new name returns id and rev 1', a && a.id && a.rev === 1, JSON.stringify(a));
  ok('save with current rev bumps rev', (await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st({ apples: 2 }), p_rev: 1 })).rev === 2);
  const c = await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st({ apples: 9 }), p_rev: 1 });
  ok('stale rev returns conflict with stored state', c.conflict === true && c.rev === 2 && c.state.apples === 2, JSON.stringify(c));
  ok('null rev on existing row returns conflict', (await rpc('ng_save', { p_key: K1, p_pid: 'p1', p_state: st({ apples: 9 }), p_rev: null })).conflict === true);
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
  const pub = await rpc('ng_public'), mine = pub.find(t => t.id === odd.id);
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
  ok('password: wrong password forbidden', (await rpc('ng_admin_list', { p_pass: 'nope' })).error === 'forbidden');
  ok('password: right password lists rows', (await rpc('ng_admin_list', { p_pass: 'test-pass-123' })).rows.length === 8);
  for (const p of ['b1', 'b2', 'b3', 'b4', 'b5']) await rpc('ng_admin_list', { p_pass: p });
  ok('password: locked after five wrong passwords, even with the right one', (await rpc('ng_admin_list', { p_pass: 'test-pass-123' })).error === 'locked');
  const sp2 = await db.query(`select proname, proconfig from pg_proc where pronamespace='public'::regnamespace and proname like 'ng_%'`);
  ok('schema 2: every function pins an empty search_path', sp2.rows.length === 17 && sp2.rows.every(r => (r.proconfig || []).some(c => c === 'search_path=""' || c === 'search_path=')));

  // ---------- schema 3 on top of data that already exists ----------
  const before = {
    players: JSON.stringify((await db.query('select id, key_hash, pid, state, rev, cheer from public.ng_players order by id')).rows),
    words: JSON.stringify((await db.query('select * from public.ng_words order by id')).rows),
    gone: JSON.stringify((await db.query('select key_hash, pid from public.ng_gone order by 1,2')).rows),
    locks: JSON.stringify((await db.query('select * from public.ng_auth order by ip')).rows),
    config: JSON.stringify((await db.query('select * from public.ng_config order by k')).rows),
  };
  await db.exec(schema3);
  await db.exec(schema3); // must be re-runnable
  console.log('schema 3 ok (applied twice)');
  const after = {
    players: JSON.stringify((await db.query('select id, key_hash, pid, state, rev, cheer from public.ng_players order by id')).rows),
    words: JSON.stringify((await db.query('select * from public.ng_words order by id')).rows),
    gone: JSON.stringify((await db.query('select key_hash, pid from public.ng_gone order by 1,2')).rows),
    locks: JSON.stringify((await db.query('select * from public.ng_auth order by ip')).rows),
    config: JSON.stringify((await db.query('select * from public.ng_config order by k')).rows),
  };
  for (const k of Object.keys(before)) ok(`migration 3 leaves ${k} untouched`, before[k] === after[k]);
  ok('migration 3: existing lock is still in force (not reset)', JSON.parse(after.locks).some(r => r.locked_until && new Date(r.locked_until) > new Date()));
  for (const f of ['ng_admin_list', 'ng_admin_delete', 'ng_admin_cheer', 'ng_admin_word_del'])
    await denied(`password route closed: anon cannot call ${f}`, () => as('anon', null, `select public.${f}('test-pass-123'${f === 'ng_admin_list' ? '' : `, '${odd.id}'`}${f === 'ng_admin_cheer' ? `, 'x'` : ''})`));
  ok('players: save still works after migration 3', (await rpc('ng_save', { p_key: K2, p_pid: 'p2', p_state: st({ apples: 5 }), p_rev: 1 })).rev === 2);
  ok('players: shared garden still works', (await rpc('ng_public')).length === 8);
  ok('players: kind words still work', (await rpc('ng_words', { p_key: K2 })).length === 1);

  const owner = await googleUser('owner@example.test'), stranger = await googleUser('stranger@example.test');
  const mailOnly = await googleUser('mail-only@example.test', { provider: 'email' }), unconfirmed = await googleUser('unconfirmed@example.test', { confirmed: false });
  for (const f of ['ng_owner_whoami()', 'ng_owner_list()', `ng_owner_delete('${odd.id}')`, `ng_owner_cheer('${odd.id}','x')`, `ng_owner_word_del('${w2[0].id}')`, 'ng_is_owner()'])
    await denied(`anon cannot call ${f.split('(')[0]}`, () => as('anon', null, `select public.${f}`));
  await denied('signed-in user cannot call ng_is_owner directly', () => as('authenticated', { sub: stranger.uid }, 'select public.ng_is_owner()'));
  await denied('signed-in user cannot read ng_owner', () => as('authenticated', { sub: stranger.uid }, 'select * from public.ng_owner'));
  await denied('signed-in user cannot write ng_owner', () => as('authenticated', { sub: stranger.uid }, `insert into public.ng_owner(uid) values ('${stranger.uid}')`));
  await denied('signed-in user cannot read ng_players', () => as('authenticated', { sub: stranger.uid }, 'select * from public.ng_players'));

  // closed state: nobody is owner yet
  const w0 = await rpc('ng_owner_whoami', {}, owner);
  ok('before assignment: whoami says not owner, not assigned', w0.owner === false && w0.assigned === false, JSON.stringify(w0));
  ok('before assignment: list is forbidden for every signed-in user', (await rpc('ng_owner_list', {}, owner)).error === 'forbidden' && (await rpc('ng_owner_list', {}, stranger)).error === 'forbidden');
  ok('before assignment: delete/cheer/word delete are forbidden', (await rpc('ng_owner_delete', { p_id: odd.id }, owner)).ok === false && (await rpc('ng_owner_cheer', { p_id: odd.id, p_text: 'x' }, owner)).ok === false && (await rpc('ng_owner_word_del', { p_id: w2[0].id }, owner)).ok === false);

  // assignment template refuses the wrong accounts
  await assignOwner(crypto.randomUUID());
  ok('assignment: unknown UID is refused', await owners() === 0);
  await assignOwner(mailOnly.uid);
  ok('assignment: account without Google sign-in is refused', await owners() === 0);
  await assignOwner(unconfirmed.uid);
  ok('assignment: unconfirmed account is refused', await owners() === 0);
  await assignOwner(owner.uid);
  ok('assignment: confirmed Google account becomes the owner', await owners() === 1);
  await assignOwner(stranger.uid);
  ok('assignment: a second owner is refused by the template', await owners() === 1 && (await rpc('ng_owner_whoami', {}, stranger)).owner === false);
  let second = false; try { await db.query(`insert into public.ng_owner(one, uid) values (true, $1)`, [stranger.uid]); second = true; } catch (e) {}
  ok('assignment: the table itself cannot hold two owners', !second && await owners() === 1);

  const w = await rpc('ng_owner_whoami', {}, owner);
  ok('owner: whoami says owner', w.owner === true && w.assigned === true);
  const list = await rpc('ng_owner_list', {}, owner);
  ok('owner: list returns all rows with state', list.ok === true && list.rows.length === 8 && list.rows[0].state);
  ok('other signed-in user: whoami not owner, list forbidden', (await rpc('ng_owner_whoami', {}, stranger)).owner === false && (await rpc('ng_owner_list', {}, stranger)).error === 'forbidden');
  ok('other signed-in user: cannot cheer, delete or remove a word', (await rpc('ng_owner_cheer', { p_id: odd.id, p_text: 'x' }, stranger)).ok === false && (await rpc('ng_owner_delete', { p_id: odd.id }, stranger)).ok === false && (await rpc('ng_owner_word_del', { p_id: w2[0].id }, stranger)).ok === false);
  ok('...and nothing changed', (await rpc('ng_load', { p_key: K2 })).length === 8 && (await rpc('ng_words', { p_key: K2 })).length === 1 && (await rpc('ng_load', { p_key: K2 })).every(r => !r.cheer));
  ok('anonymous Supabase session with the owner UID is refused', (await rpc('ng_owner_list', {}, { ...owner, anonymous: true })).error === 'forbidden');
  ok('claims without sub are refused', (await as('authenticated', { email: 'owner@example.test', role: 'authenticated' }, 'select public.ng_owner_list() as r')).rows[0].r.error === 'forbidden');
  ok('a forged email claim with another UID is refused', (await as('authenticated', { sub: stranger.uid, email: 'owner@example.test' }, 'select public.ng_owner_list() as r')).rows[0].r.error === 'forbidden');
  ok('owner: cheer works', (await rpc('ng_owner_cheer', { p_id: odd.id, p_text: 'Молодец' }, owner)).ok === true && (await rpc('ng_load', { p_key: K2 })).find(r => r.id === odd.id).cheer.text === 'Молодец');
  ok('owner: word delete works', (await rpc('ng_owner_word_del', { p_id: w2[0].id }, owner)).ok === true && (await rpc('ng_words', { p_key: K2 })).length === 0);
  ok('owner: delete works and leaves a tombstone', (await rpc('ng_owner_delete', { p_id: odd.id }, owner)).ok === true && (await rpc('ng_save', { p_key: K2, p_pid: 'p1', p_state: st(), p_rev: null })).gone === true);
  await db.exec('delete from public.ng_owner');
  ok('owner removed: access ends at once', (await rpc('ng_owner_list', {}, owner)).error === 'forbidden');
  const sp3 = await db.query(`select proname, proconfig from pg_proc where pronamespace='public'::regnamespace and proname like 'ng_%'`);
  ok('schema 3: all 23 functions pin an empty search_path', sp3.rows.length === 23 && sp3.rows.every(r => (r.proconfig || []).some(c => c === 'search_path=""' || c === 'search_path=')), String(sp3.rows.length));
  const rls = await db.query(`select relname, relrowsecurity from pg_class where relnamespace='public'::regnamespace and relname like 'ng_%' and relkind='r'`);
  ok('RLS enabled on all six tables', rls.rows.length === 6 && rls.rows.every(r => r.relrowsecurity));
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

// ---------- serving mode ----------
await db.exec(schema3);
await db.exec(schema3);
console.log('schema 3 ok (applied twice)');
const sessions = new Map(), refreshes = new Map(), codes = new Map();
let nextGoogle = null, googleOn = true;
const tok = () => crypto.randomBytes(24).toString('hex');
function issue(user) {
  const access = tok(), refresh = tok();
  sessions.set(access, { ...user, expired: false, refresh }); refreshes.set(refresh, { user, access });
  return { access_token: access, refresh_token: refresh, token_type: 'bearer', expires_in: 3600, user: { id: user.uid, email: user.email } };
}
const readBody = req => new Promise(r => { let b = ''; req.on('data', c => b += c); req.on('end', () => { try { r(b ? JSON.parse(b) : {}); } catch (e) { r({}); } }); });

http.createServer(async (req, res) => {
  const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS' };
  const send = (code, obj, extra = {}) => { res.writeHead(code, { ...cors, 'Content-Type': 'application/json', ...extra }); res.end(obj === undefined ? '' : JSON.stringify(obj)); };
  if (req.method === 'OPTIONS') { res.writeHead(204, cors); return res.end(); }
  const url = new URL(req.url, 'http://x'), bearer = (req.headers.authorization || '').replace(/^Bearer /i, '');
  try {
    // test controls
    if (url.pathname === '/__test/google') { const b = await readBody(req); nextGoogle = b.email ? await googleUser(b.email) : null; return send(200, nextGoogle); }
    if (url.pathname === '/__test/settings') { const b = await readBody(req); googleOn = !!b.google; return send(200, { google: googleOn }); }
    if (url.pathname === '/__test/expire') { sessions.forEach(s => s.expired = true); return send(200, {}); }
    if (url.pathname === '/__test/owner') { const b = await readBody(req); await assignOwner(b.uid); return send(200, { owners: await owners() }); }
    if (url.pathname === '/__test/players') return send(200, (await db.query(`select id, pid, state, cheer from public.ng_players order by created_at`)).rows);
    // Supabase Auth imitation
    if (url.pathname === '/auth/v1/settings') return send(200, { external: { google: googleOn, email: true } });
    if (url.pathname === '/auth/v1/authorize') {
      const back = new URL(url.searchParams.get('redirect_to'));
      if (url.searchParams.get('provider') !== 'google' || !googleOn) { back.searchParams.set('error', 'validation_failed'); back.searchParams.set('error_description', 'Unsupported provider'); }
      else if (!nextGoogle || url.searchParams.get('code_challenge_method') !== 's256' || !url.searchParams.get('code_challenge')) { back.searchParams.set('error', 'access_denied'); back.searchParams.set('error_description', 'cancelled'); }
      else { const code = crypto.randomUUID(); codes.set(code, { user: nextGoogle, challenge: url.searchParams.get('code_challenge') }); back.searchParams.set('code', code); }
      res.writeHead(302, { ...cors, Location: back.toString() }); return res.end();
    }
    if (url.pathname === '/auth/v1/token') {
      const b = await readBody(req), grant = url.searchParams.get('grant_type');
      if (grant === 'pkce') {
        const c = codes.get(b.auth_code); codes.delete(b.auth_code);
        const good = c && crypto.createHash('sha256').update(String(b.code_verifier || '')).digest('base64url') === c.challenge;
        return good ? send(200, issue(c.user)) : send(400, { error: 'invalid_grant' });
      }
      if (grant === 'refresh_token') {
        const r = refreshes.get(b.refresh_token); if (!r) return send(400, { error: 'invalid_grant' });
        refreshes.delete(b.refresh_token); sessions.delete(r.access); return send(200, issue(r.user));
      }
      return send(400, { error: 'unsupported_grant_type' });
    }
    if (url.pathname === '/auth/v1/logout') { const s = sessions.get(bearer); if (s) { refreshes.delete(s.refresh); sessions.delete(bearer); } return send(204); }
    // PostgREST imitation
    const m = url.pathname.match(/^\/rest\/v1\/rpc\/(ng_[a-z_]+)$/);
    if (!m) return send(404, {});
    let who = null;
    if (bearer) { const s = sessions.get(bearer); if (!s || s.expired) return send(401, { message: 'JWT expired' }); who = s; }
    return send(200, await rpc(m[1], await readBody(req), who));
  } catch (e) {
    const msg = String(e.message || e);
    send(/does not exist/.test(msg) ? 404 : /permission denied/.test(msg) ? 403 : 400, { message: msg });
  }
}).listen(port, () => console.log('listening', port));
