"""Game checks: the catalogue of 27 exercises, their generators, exact play and random play on a phone-size screen.

    pip install playwright && playwright install chromium
    python3 tests/games-test.py            full run (about ten minutes)
    python3 tests/games-test.py quick      catalogue, generators and exact play only

Runs on local copies of the three built pages with the database address removed ("On this device" mode),
so nothing leaves this machine. Timers are sped up eight times; the game code itself is not changed.

What is checked
- catalogue: 27 exercises in 4 groups, each with a name, instructions, a "trains" line and five levels that differ;
- generators: number series, water puzzle (solvable, shortest path is really the shortest), mini sudoku (valid, one solution);
- exact play (the test knows the right answers): eight exercises give 100% and a ripe apple, wrong answers lower the result
  and a result below 60% gives no ripe apple;
- random play: every exercise on every level runs to the end from taps alone, the result is 0..100, a ripe apple is
  counted only for 60% and more, a trial round and merely opening an exercise write nothing, leaving gives a bitten apple;
- screen: nothing sticks out at 360 px, no leftover "undefined"/"NaN", buttons are at least 40 px, no script errors;
  on the Hebrew and English pages no Russian text is left.
"""
import asyncio, collections, json, pathlib, re, shutil, subprocess, sys, tempfile
from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = 8789
QUICK = 'quick' in sys.argv[1:]
P = F = 0
CYR = re.compile(r'[А-Яа-яЁё]')
FAST = "window._st=window.setTimeout.bind(window);window.setTimeout=(f,ms,...a)=>window._st(f,(ms||0)/8,...a);"


def ok(name, cond, extra=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  -> ' + str(extra)[:400]))


def stand():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='ng-games-'))
    for f in ('index.html', 'he.html', 'en.html'):
        s = (ROOT / f).read_text(encoding='utf-8')
        s, n = re.subn(r"const SB_URL='[^']*', SB_KEY='[^']*';", "const SB_URL='', SB_KEY='';", s)
        assert n == 1, f
        (tmp / f).write_text(s, encoding='utf-8')
    web = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT), '--bind', '127.0.0.1'], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tmp, web


# ---- in-page helpers -------------------------------------------------------------------------------------------
JS = r"""
window.T = {
  sleep: ms => new Promise(r => _st(r, ms)),
  async until(f, ms = 20000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = f(); if (v) return v; await T.sleep(15) } throw new Error('timeout: ' + f) },
  begin(id, lvl) { openGame(id); G.lvl = lvl; ACT.start() },
  result: () => T.until(() => G && G.res).then(r => ({ score: r.score, err: r.err, ok: r.ok })),
  box: () => document.querySelector('#box'),
  all: s => [...document.querySelectorAll('#box ' + s)],
  hint: () => (document.querySelector('#box .hint') || {}).textContent || '',
  async schulte(lvl, wrong = 0) {
    T.begin('schulte', lvl); const n = T.all('.cell').length;
    for (let i = 0; i < wrong; i++) T.all('.cell').find(b => b.dataset.v === String(n)).click();
    for (let v = 1; v <= n; v++) T.all('.cell').find(b => b.dataset.v === String(v)).click();
    return T.result() },
  async leaves(lvl, right = true) {
    T.begin('leaves', lvl);
    for (let r = 0; r < 3; r++) {
      await T.until(() => T.all('.cell.on').length && T.all('.cell:disabled').length);
      const on = new Set(T.all('.cell.on').map(b => b.dataset.i)), k = on.size;
      await T.until(() => !T.all('.cell:disabled').length);
      const pickFrom = T.all('.cell').filter(b => on.has(b.dataset.i) === right).slice(0, k);
      const rest = T.all('.cell').filter(b => on.has(b.dataset.i) !== right);
      [...pickFrom, ...rest].slice(0, k).forEach(b => b.click());
      await T.until(() => T.all('.cell:disabled').length);
      if (r < 2) await T.until(() => !T.all('.cell.pick').length && T.all('.cell.on').length) }
    return T.result() },
  async gone(lvl, right = true) {
    T.begin('gone', lvl);
    for (let r = 0; r < 4; r++) {
      await T.until(() => T.all('.emo span').length && !T.all('#o button').length);
      const before = T.all('.emo span').map(x => x.textContent);
      await T.until(() => T.all('#o button:not(:disabled)').length);
      const left = new Set(T.all('.emo span').map(x => x.textContent)), miss = before.find(x => !left.has(x));
      T.all('#o button').find(b => (b.dataset.v === miss) === right).click();
      if (r < 3) await T.until(() => !T.all('#o button').length) }
    return T.result() },
  async seq(lvl, right = true) {
    T.begin('seq', lvl);
    for (let r = 0; r < 2; r++) {
      const seen = [], start = T.hint();
      const obs = new MutationObserver(ms => ms.forEach(m => { if (m.target.classList.contains('lit') && !(m.oldValue || '').includes('lit')) seen.push(+m.target.dataset.i) }));
      await T.until(() => T.all('.pad').length && T.hint().length);
      const h0 = T.hint(); T.all('.pad').forEach(p => obs.observe(p, { attributes: true, attributeFilter: ['class'], attributeOldValue: true }));
      await T.until(() => T.hint() !== h0); obs.disconnect();
      const pads = T.all('.pad');
      if (right) seen.slice().forEach(i => pads[i].click()); else pads[(seen[0] + 1) % pads.length].click();
      const h1 = T.hint();
      if (r < 1) await T.until(() => T.all('.pad').length && T.all('.pad')[0] !== pads[0]) }
    return T.result() },
  async water(lvl, right = true) {
    T.begin('water', lvl);
    const m = /(\d+)[^\d]+(\d+)/.exec(T.hint()), caps = ['#ca', '#cb'].map(s => +/(\d+)\D+(\d+)/.exec(document.querySelector(s).textContent)[2]);
    const t = +m[1], left = +m[2], sol = waterSolve(caps[0], caps[1], t), names = WMOVES.map(x => x[0]);
    const press = i => T.all('.opts button')[i].click();
    if (right) sol.forEach(n => press(names.indexOf(n))); else for (let i = 0; i < left; i++) press(i % 2 ? 4 : 0);
    return Object.assign(await T.result(), { steps: sol.length, allowed: left }) },
  async sudoku(lvl, wrong = 0) {
    T.begin('sudoku', lvl);
    const g = T.all('.sud .cell').map(b => +b.textContent || 0), sol = T.solve(g);
    const digit = v => T.all('[data-v]').find(b => b.dataset.v === String(v)).click();
    const empty = g.map((v, i) => v ? -1 : i).filter(i => i >= 0);
    for (let w = 0; w < wrong; w++) { T.all('.sud .cell')[empty[0]].click(); digit(sol[empty[0]] % 4 + 1) }
    empty.forEach(i => { T.all('.sud .cell')[i].click(); digit(sol[i]) });
    return Object.assign(await T.result(), { empty: empty.length }) },
  fits(a, i, v) { const r = i >> 2, c = i & 3; for (let j = 0; j < 16; j++) if (a[j] === v && j !== i && ((j >> 2) === r || (j & 3) === c || ((j >> 3) === (r >> 1) && ((j & 3) >> 1) === (c >> 1)))) return false; return true },
  count(a) { const i = a.indexOf(0); if (i < 0) return 1; let n = 0; for (let v = 1; v <= 4; v++) if (T.fits(a, i, v)) { a[i] = v; n += T.count(a); a[i] = 0 } return n },
  solve(a) { a = [...a]; const go = () => { const i = a.indexOf(0); if (i < 0) return true; for (let v = 1; v <= 4; v++) if (T.fits(a, i, v)) { a[i] = v; if (go()) return true; a[i] = 0 } return false }; go(); return a },
  async dual(lvl, wrong = '') {
    T.begin('dual', lvl); let places = 0, leaves = 0;
    for (let r = 0; r < 6; r++) {
      const dp = await T.until(() => T.all('.dp[data-i]')[0]);
      const fruit = +T.all('.dc')[0].dataset.f, bug = +dp.dataset.i; leaves = T.all('.dp').length - 1;
      const bs = await T.until(() => T.all('.opts button[data-f]').length === 2 && T.all('.opts button[data-f]'));
      bs.find(b => (+b.dataset.f === fruit) === (wrong !== 'fruit')).click();
      const qs = await T.until(() => T.all('.dq').length && T.all('.dq')); places = qs.length;
      qs.find(q => (+q.dataset.i === bug) === (wrong !== 'place')).click();
      if (r < 5) await T.until(() => !T.all('.dq').length) }
    return Object.assign(await T.result(), { places, leaves }) },
  async equation(lvl, right = true) {
    T.begin('equation', lvl);
    const val = (t, x) => { const [l, r] = t.replace('?', x).replace(/×/g, '*').replace(/:/g, '/').replace(/−/g, '-').split('='); return Math.abs(Function('return ' + l)() - Function('return ' + r)()) < 1e-9 };
    for (let r = 0; r < 5; r++) {
      await T.until(() => T.all('.stim').length && T.all('.opts button:not(:disabled)').length >= 2);
      const t = T.all('.stim')[0].textContent, bs = T.all('.opts button'), good = bs.filter(b => val(t, b.textContent));
      if (good.length !== 1) throw new Error('not exactly one right option: ' + t + ' / ' + bs.map(b => b.textContent));
      (right ? good[0] : bs.find(b => b !== good[0])).click();
      await T.until(() => (G && G.res) || (T.all('.stim')[0] && T.all('.stim')[0].textContent !== t) || T.all('[data-go]').length);
      const go = T.all('[data-go]')[0]; if (go) { go.click(); await T.sleep(30) } }
    return T.result() },
};
"""

CHECK = """()=>{const d=document.documentElement,out={over:d.scrollWidth-window.innerWidth,text:document.querySelector('#main').innerText};
 const small=[];for(const b of document.querySelectorAll('#main button:not([disabled])')){const r=b.getBoundingClientRect();if(r.width>0&&(r.height<40||r.width<40))small.push(b.textContent.trim().slice(0,12)+':'+Math.round(r.width)+'x'+Math.round(r.height))}
 out.small=small.slice(0,4);return out}"""


async def main():
    tmp, web = stand()
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(); errs = []

            async def page(f='index.html', name='QA-Games'):
                cx = await b.new_context(viewport={'width': 360, 'height': 740}, has_touch=True, is_mobile=True)
                await cx.add_init_script(FAST)
                pg = await cx.new_page()
                pg.on('pageerror', lambda e: errs.append(f + ': ' + str(e)))
                await pg.goto(f'http://127.0.0.1:{PORT}/{f}'); await pg.wait_for_selector('#nick')
                await pg.fill('#nick', name); await pg.click('[data-act=plant]'); await pg.wait_for_timeout(200)
                await pg.evaluate(JS)
                return pg

            pg = await page()
            # ---- catalogue ----
            cat = json.loads(await pg.evaluate("""JSON.stringify(Object.values(GAMES).map(g=>{const src=g.run.toString();
              const arrs=[...src.matchAll(/(\\[(?:[^\\[\\]]|\\[[^\\[\\]]*\\])*\\])\\[(?:c\\.lvl|l)\\]/g)].map(m=>{try{return Function('return '+m[1])()}catch(e){return null}}).filter(Boolean);
              return{id:g.id,name:g.name,cat:g.cat,how:g.how,desc:g.desc,trains:TRAINS[g.id]||'',lvl:/c\\.lvl/.test(src),arrs:arrs.map(a=>[a.length,JSON.stringify(a[0])!==JSON.stringify(a[a.length-1])])}}))"""))
            ok('catalogue: 27 exercises', len(cat) == 27, len(cat))
            ok('catalogue: four groups, none empty', collections.Counter(g['cat'] for g in cat).keys() == {'mem', 'att', 'log', 'spa'}, collections.Counter(g['cat'] for g in cat))
            ok('catalogue: every exercise has a name, a description, instructions and a "trains" line', all(len(g['name']) > 2 and len(g['desc']) > 10 and len(g['how']) > 20 and len(g['trains']) > 3 for g in cat), [g['id'] for g in cat if not g['trains']])
            ok('catalogue: names are unique and every exercise is on the exercises page', len({g['name'] for g in cat}) == 27 and sorted(await pg.evaluate('ORDER')) == sorted(g['id'] for g in cat))
            ok('levels: every exercise reads the chosen level', all(g['lvl'] for g in cat), [g['id'] for g in cat if not g['lvl']])
            ok('levels: every per-level table has five entries and level 1 differs from level 5', all(n == 5 and diff for g in cat for n, diff in g['arrs']), [(g['id'], g['arrs']) for g in cat if any(n != 5 or not d for n, d in g['arrs'])])
            # ---- generators ----
            ser = json.loads(await pg.evaluate("JSON.stringify([0,1,2,3,4].map(l=>Array.from({length:200},()=>mkSeries(l))))"))
            ok('number series: 1000 generated rows have five shown numbers, four different positive options and the answer among them',
               all(len(q['seq']) == 5 and len(set(q['opts'])) == 4 and q['ans'] in q['opts'] and all(isinstance(x, int) and x > 0 for x in q['opts'] + q['seq']) and q['rule'] for row in ser for q in row))
            ok('number series: rows vary and higher levels use other rules', all(len({tuple(q['seq']) for q in row}) > 20 for row in ser) and len({q['rule'] for q in ser[0]} & {q['rule'] for q in ser[4]}) == 0, [[len({tuple(q['seq']) for q in row}) for row in ser], sorted({q['rule'] for q in ser[0]})[:3], sorted({q['rule'] for q in ser[4]})[:3]])
            wat = json.loads(await pg.evaluate(r"""JSON.stringify([0,1,2,3,4].map(l=>{const m=/\[(\[[\d,\[\]]+\])\]\[c\.lvl\]/.exec(GAMES.water.run.toString()),[a,b,t]=JSON.parse('['+m[1]+']')[l];const sol=waterSolve(a,b,t);let x=0,y=0;
              sol.forEach(n=>{[x,y]=WMOVES.find(m=>m[0]===n)[1](x,y,a,b)});
              let best=null,seen=new Set(['0,0']),q=[[0,0,0]];while(q.length){const [u,v,d]=q.shift();if(u===t||v===t){best=d;break}WMOVES.forEach(m=>{const [nu,nv]=m[1](u,v,a,b),k=nu+','+nv;if(!seen.has(k)){seen.add(k);q.push([nu,nv,d+1])}})}
              return{len:sol.length,reach:x===t||y===t,best}}))"""))
            ok('water puzzle: all five levels are solvable and the shown solution is the shortest one', all(w['reach'] and w['len'] == w['best'] and w['len'] > 0 for w in wat), wat)
            ok('water puzzle: a higher level never needs fewer moves than a lower one', [w['len'] for w in wat] == sorted(w['len'] for w in wat) and wat[0]['len'] < wat[4]['len'], [w['len'] for w in wat])
            sud = json.loads(await pg.evaluate("""JSON.stringify([4,6,8,9,10].map(k=>Array.from({length:40},()=>{const {sol,puz}=mkSudoku(k);
              const valid=[0,1,2,3].every(r=>new Set(sol.slice(r*4,r*4+4)).size===4&&new Set([0,1,2,3].map(i=>sol[i*4+r])).size===4)&&[0,2,8,10].every(s=>new Set([0,1,4,5].map(d=>sol[s+d])).size===4);
              return{valid,match:puz.every((v,i)=>!v||v===sol[i]),empty:puz.filter(v=>!v).length,unique:T.count([...puz])===1,k}})))"""))
            ok('mini sudoku: 200 generated grids are valid, agree with their solution and have exactly one solution', all(s['valid'] and s['match'] and s['unique'] and 1 <= s['empty'] <= s['k'] for row in sud for s in row))
            ok('mini sudoku: higher levels leave more empty cells', sum(s['empty'] for s in sud[0]) < sum(s['empty'] for s in sud[2]) < sum(s['empty'] for s in sud[4]))

            # ---- exact play ----
            async def run(expr):
                r = await pg.evaluate(expr); await pg.evaluate("G=null;location.hash='garden'"); await pg.wait_for_timeout(30); return r
            apples = lambda: pg.evaluate('S.apples')
            a0 = await apples()
            r = [await run(f'T.schulte({l})') for l in range(5)]
            ok('numbers in order: exact play gives 100% on all five levels', all(x == {'score': 100, 'err': 0, 'ok': True} for x in r), r)
            ok('...and each of the five rounds added exactly one ripe apple', await apples() == a0 + 5)
            r = await run('T.schulte(0,3)'); ok('numbers in order: three wrong taps cost 24 points', r == {'score': 76, 'err': 3, 'ok': True}, r)
            a0 = await apples(); r = await run('T.schulte(1,6)')
            ok('numbers in order: six wrong taps give 52%, a green apple, the ripe count does not grow', r == {'score': 52, 'err': 6, 'ok': False} and await apples() == a0, r)
            for g in ('leaves', 'gone', 'seq'):
                r = [await run(f'T.{g}({l})') for l in (0, 2, 4)]
                ok(f'{g}: remembering exactly gives 100% on levels 1, 3 and 5', all(x['score'] == 100 and x['ok'] for x in r), r)
                a0 = await apples(); r = await run(f'T.{g}(1,false)')
                ok(f'{g}: wrong answers give a result below 60% and no ripe apple', r['score'] < 60 and not r['ok'] and r['err'] > 0 and await apples() == a0, r)
            r = [await run(f'T.water({l})') for l in range(5)]
            ok('water puzzle: the shortest path gives 100% on all five levels', all(x['score'] == 100 and x['ok'] for x in r), r)
            a0 = await apples(); r = await run('T.water(2,false)')
            ok('water puzzle: running out of moves gives 0% and no ripe apple', r['score'] == 0 and not r['ok'] and await apples() == a0, r)
            r = [await run(f'T.sudoku({l})') for l in range(5)]
            ok('mini sudoku: a solved grid gives 100% on all five levels', all(x['score'] == 100 and x['ok'] for x in r), r)
            r = await run('T.sudoku(2,2)'); ok('mini sudoku: two wrong digits cost 24 points and are counted as errors', r['score'] == 76 and r['err'] == 2, r)
            r = [await run(f'T.equation({l})') for l in range(5)]
            ok('find the number: every example has exactly one right option; right answers give 100% on all five levels', all(x['score'] == 100 and x['ok'] for x in r), r)
            a0 = await apples(); r = await run('T.equation(3,false)')
            ok('find the number: wrong answers give 0% and no ripe apple', r['score'] == 0 and r['err'] == 5 and await apples() == a0, r)
            r = [await run(f'T.dual({l})') for l in range(5)]
            ok('double decision: naming the fruit and the place of the beetle gives 100% on all five levels', all(x['score'] == 100 and x['ok'] and x['err'] == 0 for x in r), r)
            ok('double decision: higher levels show the picture for a shorter time and offer more places', [x['places'] for x in r] == [4, 6, 8, 8, 8] and [x['leaves'] for x in r] == [0, 0, 0, 2, 4], r)
            a0 = await apples(); r = await run('T.dual(1,"fruit")'); r2 = await run('T.dual(1,"place")')
            ok('double decision: a wrong fruit or a wrong place alone makes the round count as an error, no ripe apple', r['score'] == 0 and r['err'] == 6 and r2['score'] == 0 and r2['err'] == 6 and await apples() == a0, [r, r2])
            st = json.loads(await pg.evaluate("JSON.stringify({a:S.apples,ripe:S.hist.filter(h=>h.ok).length,fin:S.hist.filter(h=>h.s!=null).length,all:S.hist.length,lv:S.lv})"))
            ok('history: every finished round is recorded once, ripe apples equal rounds with 60% and more', st['a'] == st['ripe'] and st['fin'] == st['all'], st)
            ok('progress: the highest level passed is remembered', st['lv'].get('schulte') == 4 and st['lv'].get('water') == 4, st['lv'])
            # opening, trial, leaving
            h0 = await pg.evaluate('S.hist.length'); a0 = await apples()
            await pg.evaluate("openGame('pairs')"); await pg.evaluate("G=null;location.hash='garden'")
            ok('just opening an exercise writes nothing', await pg.evaluate('S.hist.length') == h0 and await apples() == a0)
            await pg.evaluate("openGame('schulte');ACT.trial();for(let v=1,n=T.all('.cell').length;v<=n;v++)T.all('.cell').find(b=>b.dataset.v===String(v)).click()"); await pg.wait_for_timeout(100)
            ok('a trial round shows its result and writes nothing', await pg.evaluate('G.res&&G.res.trial===1&&G.res.score') == 100 and await pg.evaluate('S.hist.length') == h0 and await apples() == a0)
            await pg.evaluate("ACT.real()"); await pg.wait_for_timeout(50); await pg.evaluate("ACT.quit()"); await pg.wait_for_timeout(50)
            ok('leaving a real round leaves a bitten apple and no ripe one', json.loads(await pg.evaluate("JSON.stringify([S.hist.length,S.hist.at(-1).s,fruitsOf(S).at(-1),S.apples])")) == [h0 + 1, None, 'b', a0])
            await pg.reload(); await pg.wait_for_function('ready===true')
            ok('after a reload apples and history are the same (nothing doubled)', json.loads(await pg.evaluate("JSON.stringify([S.hist.length,S.apples])")) == [h0 + 1, a0])

            # ---- random play on a phone screen ----
            async def monkey(f, lang, levels):
                pg = await page(f, 'QA-' + lang); bad = collections.defaultdict(list); n = 0

                async def look(where):
                    r = await pg.evaluate(CHECK); t = r['text']
                    if r['over'] > 1: bad['overflow'].append(where)
                    if r['small']: bad['small'].append((where, r['small']))
                    if any(w in t for w in ('undefined', 'NaN', '[object')): bad['junk'].append(where)
                    if lang != 'ru' and CYR.search(t): bad['russian'].append((where, t[max(0, CYR.search(t).start() - 20):CYR.search(t).start() + 30]))
                for gid in await pg.evaluate('ORDER'):
                    for lvl in levels:
                        await pg.evaluate(f"openGame('{gid}');G.lvl={lvl};render()"); await look(f'{gid} intro')
                        a0 = await pg.evaluate('S.apples'); await pg.evaluate('ACT.start()'); res = None
                        for step in range(8000):
                            st = await pg.evaluate("""()=>{if(!G)return{gone:1};if(G.res)return{res:G.res};
                              const bs=[...document.querySelectorAll('#box button:not([disabled])')].filter(e=>e.getBoundingClientRect().width>0);
                              if(bs.length)bs[Math.floor(Math.random()*bs.length)].click();return{}}""")
                            if st.get('res') or st.get('gone'): res = st.get('res'); break
                            if step in (3, 40): await look(f'{gid} L{lvl + 1} play')
                            await pg.wait_for_timeout(20)
                        n += 1
                        if not res: bad['unfinished'].append(f'{gid} L{lvl + 1}'); await pg.evaluate("abandon();location.hash='garden'"); continue
                        s = res.get('score')
                        if not isinstance(s, int) or not 0 <= s <= 100 or not isinstance(res.get('err'), int) or res['err'] < 0: bad['score'].append((gid, lvl, res))
                        elif (await pg.evaluate('S.apples') - a0) != (1 if s >= 60 else 0) or res['ok'] != (s >= 60): bad['apple'].append((gid, lvl, res))
                        await look(f'{gid} L{lvl + 1} result'); await pg.evaluate("G=null;location.hash='garden'"); await pg.wait_for_timeout(20)
                for r in await pg.evaluate('Object.keys(ROUTES)'):
                    await pg.evaluate(f"location.hash='{r}'"); await pg.wait_for_timeout(200); await look('page ' + r)
                await pg.evaluate("ACT.big()")
                for r in await pg.evaluate('Object.keys(ROUTES)'):
                    await pg.evaluate(f"location.hash='{r}'"); await pg.wait_for_timeout(200); await look('page ' + r + ' (large text)')
                return n, bad

            plan = [('index.html', 'ru', [0] if QUICK else [0, 1, 2, 3, 4])] + ([] if QUICK else [('he.html', 'he', [0, 4]), ('en.html', 'en', [0, 4])])
            for f, lang, levels in plan:
                n, bad = await monkey(f, lang, levels)
                lv = ', '.join(str(l + 1) for l in levels)
                ok(f'{lang}: {n} rounds (27 exercises, levels {lv}) run to the end from taps alone', not bad['unfinished'], bad['unfinished'])
                ok(f'{lang}: every result is 0..100 with a whole error count', not bad['score'], bad['score'][:3])
                ok(f'{lang}: a ripe apple is counted for 60% and more, and only then', not bad['apple'], bad['apple'][:3])
                ok(f'{lang}: nothing sticks out of a 360 px screen, also with large text', not bad['overflow'], bad['overflow'][:5])
                ok(f'{lang}: every active button is at least 40 px', not bad['small'], bad['small'][:3])
                ok(f'{lang}: no "undefined" or "NaN" on screen', not bad['junk'], bad['junk'][:5])
                if lang != 'ru': ok(f'{lang}: no Russian text left in exercises and pages', not bad['russian'], bad['russian'][:3])
            ok('no script errors in any tab', not errs, errs[:3])
            await b.close()
    finally:
        web.kill(); shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{P} passed, {F} failed')
    sys.exit(1 if F else 0)


asyncio.run(main())
