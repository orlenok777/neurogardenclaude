"""Browser regression tests for server saving, run against a local stand (never the real database).

    npm install @electric-sql/pglite
    pip install playwright && playwright install chromium
    python3 tests/browser-test.py

The script copies the three built pages to a temp folder with the database address replaced by a local one,
serves them from one origin (like GitHub Pages), starts tests/db-test.mjs as the database and drives Chromium.
Only synthetic names are used.
"""
import asyncio, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PORT, WEB_PORT = 8787, 8788
SITE = f'http://127.0.0.1:{WEB_PORT}/'
P = F = 0


def ok(name, cond, extra=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  -> ' + str(extra)))


def rpc(fn, args):
    req = urllib.request.Request(f'http://127.0.0.1:{DB_PORT}/rest/v1/rpc/{fn}', data=json.dumps(args).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req))


def post(path, body=None):
    req = urllib.request.Request(f'http://127.0.0.1:{DB_PORT}{path}', data=json.dumps(body or {}).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req))


def players():
    return post('/__test/players')


def status(fn, token=None):
    """HTTP status and body of a direct API call, the way an outsider would make it."""
    h = {'Content-Type': 'application/json'}
    if token: h['Authorization'] = 'Bearer ' + token
    try:
        r = urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{DB_PORT}/rest/v1/rpc/{fn}', data=b'{}', headers=h))
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def stand():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='ng-site-'))
    for f in ('index.html', 'he.html', 'en.html'):
        s = (ROOT / f).read_text(encoding='utf-8')
        s, n = re.subn(r"const SB_URL='[^']*', SB_KEY='[^']*';", f"const SB_URL='http://127.0.0.1:{DB_PORT}', SB_KEY='local-test';", s)
        assert n == 1, f
        (tmp / f).write_text(s, encoding='utf-8')
    node_dir = os.environ.get('NG_NODE_DIR', str(ROOT))
    db = subprocess.Popen(['node', str(ROOT / 'tests' / 'db-test.mjs'), str(ROOT / 'supabase.sql'), str(DB_PORT)], cwd=node_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    web = subprocess.Popen([sys.executable, '-m', 'http.server', str(WEB_PORT), '--bind', '127.0.0.1'], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            rpc('ng_public', {}); urllib.request.urlopen(SITE); return tmp, db, web
        except Exception:
            time.sleep(0.5)
    db.kill(); web.kill(); sys.exit('stand did not start')


FIN = "S.hist.filter(h=>h.s!=null).length"
QUIT = "S.hist.filter(h=>h.s==null).length"


async def plant(pg, name):
    await pg.wait_for_selector('#nick'); await pg.fill('#nick', name); await pg.click('[data-act=plant]'); await pg.wait_for_timeout(500)


async def start(pg, gid, lvl=0):
    await pg.evaluate(f"openGame('{gid}');G.lvl={lvl};ACT.start()"); await pg.wait_for_timeout(500)


async def finish_schulte(pg):
    for i in range(1, 10):
        await pg.click(f'.cell[data-v="{i}"]')
    await pg.wait_for_selector('[data-act=again]'); await pg.evaluate("ACT.home()"); await pg.wait_for_timeout(700)


async def finish_color(pg):
    """Colour and word, level 1: answers every try with the first button, so the score is low on purpose."""
    for _ in range(40):
        if await pg.evaluate("!!(G&&G.res)"): break
        await pg.evaluate("(()=>{const b=document.querySelector('#box .opts button:not(.good):not(.bad)');if(b)b.click()})()")
        await pg.wait_for_timeout(350)
    await pg.wait_for_selector('[data-act=again]')
    res = await pg.evaluate("[G.res.score,G.res.ok]")
    await pg.evaluate("ACT.home()"); await pg.wait_for_timeout(700)
    return res


async def google(pg, cond="true"):
    """Clicks the Google button, follows the redirect to the (fake) provider and back, waits for the page to settle."""
    async with pg.expect_navigation():
        await pg.click('[data-act=alogin]')
    await pg.wait_for_function("typeof ready!=='undefined'&&ready===true&&location.search===''&&(" + cond + ")")
    await pg.wait_for_timeout(500)


async def reload(pg):
    await pg.reload(); await pg.wait_for_function("ready===true"); await pg.wait_for_timeout(400)


async def main():
    tmp, db, web = stand()
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(); errs = []

            async def ctx():
                return await b.new_context(viewport={'width': 390, 'height': 844})

            async def page(c, f=''):
                pg = await c.new_page(); pg.on('pageerror', lambda e: errs.append(str(e)))
                await pg.goto(SITE + f); await pg.wait_for_function("ready===true"); return pg

            # 1. one name, one tab
            c1 = await ctx(); a = await page(c1); await plant(a, 'QA-One')
            ok('1 server mode is on', await a.evaluate("mode==='account'&&sbOK"))
            await start(a, 'schulte'); await finish_schulte(a); await reload(a)
            ok('1 one tab: finished round and ripe apple survive a full reload', await a.evaluate(f"[{FIN},{QUIT},S.apples].join()") == '1,0,1', await a.evaluate("JSON.stringify(S.hist)"))
            ok('1 the round is on the server', [r['state']['hist'][0]['s'] for r in players() if r['state']['nick'] == 'QA-One'] == [100])

            # 2. the reported defect: RU round in progress, EN page opened in a second tab, RU round finished
            c2 = await ctx(); ru = await page(c2); await plant(ru, 'QA-Tabs')
            await start(ru, 'color')
            en = await page(c2, 'en.html')
            ok('2 EN tab sees the same name', await en.evaluate("S.nick") == 'QA-Tabs')
            ok('2 EN tab does not turn the running RU round into an unfinished one', await en.evaluate(f"[{QUIT},!!S.cur].join()") == '0,true', await en.evaluate("JSON.stringify([S.hist,S.cur])"))
            score, was_ok = await finish_color(ru)
            await reload(ru)
            h = json.loads(await ru.evaluate("JSON.stringify(S.hist)"))
            ok('2 RU after reload: the round is finished with its score, not "not finished"', len(h) == 1 and h[0]['g'] == 'color' and h[0]['s'] == score, h)
            ok('2 RU after reload: apples match the result', await ru.evaluate("S.apples") == (1 if was_ok else 0))
            await reload(en)
            ok('2 EN after reload shows the same finished round', await en.evaluate("JSON.stringify(S.hist.map(h=>[h.g,h.s]))") == json.dumps([['color', score]], separators=(',', ':')))
            ok('2 no "playing now" left on the server', all(r['state'].get('cur') is None for r in players() if r['state']['nick'] == 'QA-Tabs'))

            # 3. same with the Hebrew page, and the second tab saves something while the round runs
            c3 = await ctx(); ru3 = await page(c3); await plant(ru3, 'QA-He')
            await start(ru3, 'schulte')
            he = await page(c3, 'he.html')
            await he.evaluate("ACT.water()"); await he.wait_for_timeout(1900)
            await finish_schulte(ru3); await reload(ru3)
            ok('3 HE tab saved during the RU round: round finished, apple kept, watering kept', await ru3.evaluate(f"[{FIN},{QUIT},S.apples,S.waters].join()") == '1,0,1,1', await ru3.evaluate("JSON.stringify([S.hist,S.apples,S.waters])"))

            # 4. both tabs play at the same time
            c4 = await ctx(); t1 = await page(c4); await plant(t1, 'QA-Both')
            await start(t1, 'schulte')
            t2 = await page(c4, 'en.html')
            await start(t2, 'schulte'); await finish_schulte(t2)
            await finish_schulte(t1)
            await reload(t1); await reload(t2)
            for name, t in (('RU', t1), ('EN', t2)):
                ok(f'4 two rounds played in two tabs: {name} tab has both finished, two apples', await t.evaluate(f"[{FIN},{QUIT},S.apples].join()") == '2,0,2', await t.evaluate("JSON.stringify([S.hist,S.apples])"))

            # 5. a round that really was abandoned still leaves a bitten apple
            c5 = await ctx(); q = await page(c5); await plant(q, 'QA-Quit')
            await start(q, 'schulte'); await reload(q)
            ok('5 reload of the same tab mid-round: recorded as not finished at once', await q.evaluate(f"[{FIN},{QUIT},!!S.cur].join()") == '0,1,false', await q.evaluate("JSON.stringify([S.hist,S.cur])"))
            await start(q, 'schulte'); await q.close()
            q2 = await page(c5)
            ok('5 tab closed mid-round: a new tab leaves the fresh round alone', await q2.evaluate(f"[{QUIT},!!S.cur].join()") == '1,true')
            await start(q2, 'schulte'); await finish_schulte(q2); await reload(q2)
            ok('5 ...and records it as not finished when the next round starts', await q2.evaluate(f"[{FIN},{QUIT},S.apples].join()") == '1,2,1', await q2.evaluate("JSON.stringify(S.hist)"))
            await start(q2, 'schulte'); await q2.evaluate("S.cur.at-=31*60*1000;S.cur.sid='other';save()"); await q2.wait_for_timeout(600); await q2.close()
            q3 = await page(c5)
            ok('5 a round older than 30 minutes from another tab is recorded as not finished on load', await q3.evaluate(f"[{QUIT},!!S.cur].join()") == '3,false', await q3.evaluate("JSON.stringify([S.hist,S.cur])"))

            # 6. returning to a tab picks up the other tab's result without a reload
            c6 = await ctx(); v1 = await page(c6); await plant(v1, 'QA-Sync')
            v2 = await page(c6, 'en.html')
            await start(v1, 'schulte'); await finish_schulte(v1)
            await v2.evaluate("tabSync()"); await v2.wait_for_timeout(700)
            ok('6 the other tab shows the new round after tabSync, no reload', await v2.evaluate(f"[{FIN},S.apples].join()") == '1,1')

            # 7. isolation
            c7 = await ctx(); o = await page(c7); await plant(o, 'QA-Other')
            ok('7 another browser sees none of the other names', await o.evaluate("Object.values(ACC.profiles).map(q=>q.nick).join()") == 'QA-Other')
            await a.click('[data-act=addname]'); await a.fill('#nick', 'QA-Two'); await a.click('[data-act=plant]'); await a.wait_for_timeout(500)
            await start(a, 'schulte'); await finish_schulte(a); await reload(a)
            ok('7 two names in one browser keep separate histories', json.loads(await a.evaluate("JSON.stringify(Object.values(ACC.profiles).map(q=>[q.nick,q.hist.length,q.apples]))")) == [['QA-One', 1, 1], ['QA-Two', 1, 1]], await a.evaluate("JSON.stringify(Object.values(ACC.profiles).map(q=>[q.nick,q.hist.length,q.apples]))"))
            rows = players()
            ok('7 server holds exactly the eight synthetic names', sorted(r['state']['nick'] for r in rows) == sorted(['QA-One', 'QA-Two', 'QA-Tabs', 'QA-He', 'QA-Both', 'QA-Quit', 'QA-Sync', 'QA-Other']), [r['state']['nick'] for r in rows])

            # 8. "On this device" mode with two language tabs
            c8 = await ctx(); d1 = await page(c8)
            await d1.evaluate("setMode('device').then(render)"); await d1.wait_for_timeout(300); await plant(d1, 'QA-Local')
            await start(d1, 'schulte')
            d2 = await page(c8, 'en.html')
            await finish_schulte(d1)
            await d2.evaluate("ACT.water()"); await d2.wait_for_timeout(1900)
            await reload(d1)
            ok('8 device mode, two tabs: finished round and watering both kept', await d1.evaluate(f"[mode,{FIN},{QUIT},S.apples,S.waters].join()") == 'device,1,0,1,1', await d1.evaluate("JSON.stringify([mode,S.hist,S.apples,S.waters])"))
            ok('8 device mode sent nothing to the server', all(r['state']['nick'] != 'QA-Local' for r in players()))

            # 10. owner sign-in for the panel (fake Google step, real client code and real SQL checks)
            ADMIN = "document.querySelector('#main').innerText"
            ca = await ctx(); ad = await page(ca, '#admin'); await ad.wait_for_timeout(700)
            ok('10 signed out: Google button shown, no player data', await ad.evaluate("!!document.querySelector('[data-act=alogin]')&&people.length===0&&!isOwner"))
            ok('10 signed out: direct API calls are refused', status('ng_owner_list')[0] in (401, 403) and status('ng_owner_whoami')[0] in (401, 403))
            post('/__test/settings', {'google': False}); g0 = await page(await ctx(), '#admin'); await g0.wait_for_timeout(700)
            ok('10 provider switched off: no button, says not set up', await g0.evaluate("!document.querySelector('[data-act=alogin]')") and 'Google' in await g0.evaluate(ADMIN))
            post('/__test/settings', {'google': True})
            post('/__test/google', {})   # nobody picked at the Google step
            await google(ad, "route==='admin'")
            ok('10 cancelled Google step: still signed out, message shown, address bar clean', await ad.evaluate("!AU&&!isOwner&&adminMsg.length>0&&location.search===''"))

            stranger = post('/__test/google', {'email': 'stranger@example.test'})
            await google(ad, "!!AU&&AD.state!=='checking'&&AD.state!=='out'")
            ok('10 signed in, nobody assigned: closed, says owner not assigned', await ad.evaluate("AD.state==='denied'&&AD.assigned===false&&!isOwner&&people.length===0"), await ad.evaluate("JSON.stringify(AD)"))
            ok('10 the page shows the account UID for assignment, not player data', await ad.evaluate("document.querySelector('#auid').textContent") == stranger['uid'] and 'QA-' not in await ad.evaluate(ADMIN))
            ok('10 the one-time code is gone from the address bar', await ad.evaluate("location.search===''&&location.hash==='#admin'"))
            tok_s = await ad.evaluate("AU.access_token")
            ok('10 not owner: direct API call with a valid session is forbidden', status('ng_owner_list', tok_s)[1] == {'ok': False, 'error': 'forbidden'})

            cb = await ctx(); ow = await page(cb, 'en.html#admin')
            owner = post('/__test/google', {'email': 'owner@example.test'})
            await google(ow, "!!AU&&AD.state==='denied'")
            ok('10 owner assigned by UID through the template', post('/__test/owner', {'uid': await ow.evaluate("AU.uid")})['owners'] == 1 and await ow.evaluate("AU.uid") == owner['uid'])
            await ow.evaluate("sbRoute()"); await ow.wait_for_function("isOwner===true")
            names = json.loads(await ow.evaluate("JSON.stringify(people.map(p=>p.nick).sort())"))
            ok('10 owner sees the panel with all synthetic names (EN page)', 'QA-One' in names and 'QA-Tabs' in names and len(names) == len(players()), names)
            ok('10 another confirmed user still sees nothing after the owner is assigned', (await ad.evaluate("sbRoute()") or True) and await ad.evaluate("new Promise(r=>setTimeout(()=>r(AD.state==='denied'&&AD.assigned===true&&people.length===0),800))"))
            ok('10 a second owner cannot be added', post('/__test/owner', {'uid': stranger['uid']})['owners'] == 1 and status('ng_owner_list', tok_s)[1]['error'] == 'forbidden')
            target = await ow.evaluate("people.find(p=>p.nick==='QA-Other').id")
            await ow.evaluate(f"adminTab='live';sel='{target}';render()"); await ow.fill('#cheer', 'QA cheer'); await ow.click('[data-act=cheersend]'); await ow.wait_for_timeout(700)
            ok('10 owner action reaches the player: cheer delivered', [r['cheer']['text'] for r in players() if r['id'] == target and r['cheer']] == ['QA cheer'])
            post('/__test/expire'); await ow.evaluate("sbRoute()"); await ow.wait_for_timeout(900)
            ok('10 expired session is refreshed and the panel keeps working', await ow.evaluate("isOwner===true&&people.length>0&&!!AU"))
            tok_o = await ow.evaluate("AU.access_token")
            await reload(ow); await ow.wait_for_function("isOwner===true")
            ok('10 owner session survives a reload of the same tab', True)
            ow2 = await page(await ctx(), '#admin'); await ow2.wait_for_timeout(600)
            ok('10 another browser is not signed in', await ow2.evaluate("!AU&&!isOwner&&people.length===0"))
            tok_o = await ow.evaluate("AU.access_token")
            await ow.click('[data-act=alogout]'); await ow.wait_for_timeout(600)
            ok('10 sign-out: panel closed, session removed from the tab', await ow.evaluate("!AU&&!isOwner&&people.length===0&&sessionStorage.getItem('ng_auth')===null") and 'QA-' not in await ow.evaluate(ADMIN))
            # the stand, like real Supabase, still accepts the signed token itself after sign-out; the SQL session check must refuse it
            ok('10 sign-out: the old token is refused by the server-side session check', status('ng_owner_list', tok_o) == (200, {'ok': False, 'error': 'forbidden'}), status('ng_owner_list', tok_o))
            ok('10 sign-out: whoami with the old token reports no session and no owner', (lambda w: w['session'] is False and w['owner'] is False)(status('ng_owner_whoami', tok_o)[1]))
            await reload(ow); await ow.evaluate("location.hash='admin'"); await ow.wait_for_timeout(600)
            ok('10 sign-out holds after reload', await ow.evaluate("!AU&&!isOwner&&people.length===0"))
            ok('10 tokens never reach localStorage or the page HTML', await ad.evaluate("!JSON.stringify(Object.assign({},localStorage)).includes(AU.access_token)&&!document.documentElement.outerHTML.includes(AU.access_token)"))
            # signed out everywhere (sessions removed on the server) while a tab still holds a token
            post('/__test/google', {'email': 'owner@example.test'}); await google(ow, "isOwner===true")
            held = await ow.evaluate("AU.access_token")
            post('/__test/kill-sessions', {'uid': owner['uid']})
            await ow.evaluate("sbRoute()"); await ow.wait_for_timeout(900)
            ok('10 session removed on the server: the open panel closes itself and drops the token', await ow.evaluate("!AU&&!isOwner&&people.length===0&&adminMsg.length>0") and 'QA-' not in await ow.evaluate(ADMIN), await ow.evaluate("JSON.stringify([!!AU,isOwner,people.length,AD])"))
            ok('10 ...and the held token gets nothing from the server', status('ng_owner_list', held)[1] == {'ok': False, 'error': 'forbidden'})
            he_ad = await page(await ctx(), 'he.html#admin'); await he_ad.wait_for_timeout(600)
            ok('10 HE sign-in card is translated', not re.search('[А-Яа-яЁё]', await he_ad.evaluate(ADMIN)) and await he_ad.evaluate("!!document.querySelector('[data-act=alogin]')"))
            # players are unaffected by all of the above
            await reload(a)
            ok('10 players unaffected: names, history and apples as before', json.loads(await a.evaluate("JSON.stringify(Object.values(ACC.profiles).map(q=>[q.nick,q.hist.length,q.apples]))")) == [['QA-One', 1, 1], ['QA-Two', 1, 1]])
            await o.evaluate("heartbeat()"); await o.wait_for_timeout(700)
            ok('10 the cheered player sees the cheer in the garden', await o.evaluate("cheer&&cheer.text") == 'QA cheer')

            # 11. lifetime "abandoned" counter (tot.quit): a round wrongly counted as abandoned by another tab
            #     and then finished must not stay counted; real abandons and old totals must be kept
            COUNTS = "JSON.stringify([S.hist.filter(h=>h.s!=null).length,S.hist.filter(h=>h.s==null).length,(S.tot&&S.tot.quit)||0,S.apples])"

            async def twin(c, src, f='en.html'):
                """A second tab that carries the same tab id, as a duplicated browser tab does."""
                sid = await src.evaluate("SID"); pg = await c.new_page(); pg.on('pageerror', lambda e: errs.append(str(e)))
                await pg.add_init_script(f"try{{sessionStorage.setItem('ng_sid','{sid}')}}catch(e){{}}")
                await pg.goto(SITE + f); await pg.wait_for_function("ready===true"); await pg.wait_for_timeout(500); return pg

            unit = json.loads(await a.evaluate("""(()=>{
              const R={},h=(at,s,q)=>Object.assign({g:'seq',l:0,at,d:5,s,ok:s!=null&&s>=60},q?{q:1}:{});
              const st=(hist,tot,apples)=>Object.assign(blank(),{nick:'QA',hist,tot:tot||{},apples:apples||0});
              const q=x=>x.tot.quit,fin=x=>x.hist.filter(e=>e.s!=null).length,un=x=>x.hist.filter(e=>e.s==null).length;
              let m=mergeState(st([h(1,100)],{quit:3},1),st([h(1,null,1)],{quit:4}));R.remoteFalse=[q(m),fin(m),un(m),m.apples];
              m=mergeState(st([h(1,null,1)],{quit:4}),st([h(1,100)],{quit:3},1));R.localFalse=[q(m),fin(m),un(m),m.apples];
              m=mergeState(st([h(1,null,1)],{quit:4}),st([],{quit:3}));R.realLocal=[q(m),un(m)];
              m=mergeState(st([],{quit:3}),st([h(1,null,1)],{quit:4}));R.realRemote=[q(m),un(m)];
              const loc=st([h(1,100)],{quit:3},1),rem=st([h(1,null,1)],{quit:4});
              const m1=mergeState(loc,rem),m2=mergeState(m1,rem),m3=mergeState(m2,m2),m4=mergeState(rem,m1),m5=mergeState(m4,rem);
              R.repeat=[q(m1),q(m2),q(m3),q(m4),q(m5),m2.apples,m3.apples,m4.apples,m5.apples,fin(m5),un(m5)];
              const many=[];for(let i=1;i<=170;i++)many.push(h(i,i%10===0?null:100,i%10===0));
              const big=cleanState(st(many,{quit:40},153));
              m=mergeState(big,st([h(170,100)],{quit:2},1));R.long=[big.hist.length,m.hist.length,q(m),un(m)];
              m=mergeState(big,st([],{quit:2}));R.longNoFix=[m.hist.length,q(m),un(m)];
              m=mergeState(st([h(1,100)],{quit:3},1),st([h(1,null)],{quit:4}));R.legacy=[q(m),un(m)];
              m=mergeState(st([h(1,100)],{},1),st([h(1,null,1)],{quit:0}));R.floor=[q(m)];
              m=mergeState(st([h(1,100)],{},1),st([h(2,100)],{},1));R.noKey=['quit' in m.tot];
              m=mergeState(st([h(1,100),h(2,100),h(3,null,1)],{quit:5},2),st([h(1,null,1),h(2,null,1)],{quit:6}));R.mixed=[q(m),fin(m),un(m)];
              R.flag=[cleanState(st([h(1,null,1)])).hist[0].q,'q' in cleanState(st([Object.assign(h(1,100),{q:1})])).hist[0]];
              m=mergeState(st([h(1,100)],{quit:3,open:7,fin:2},1),st([h(1,null,1)],{quit:4,open:5,fin:9}));R.others=[m.tot.open,m.tot.fin,m.tot.quit];
              return JSON.stringify(R)})()"""))
            ok('11 merge: remote wrongly-counted abandon replaced by local finish lowers the counter by one', unit['remoteFalse'] == [3, 1, 0, 1], unit['remoteFalse'])
            ok('11 merge: local wrongly-counted abandon replaced by remote finish lowers the counter by one', unit['localFalse'] == [3, 1, 0, 1], unit['localFalse'])
            ok('11 merge: a real abandon (no finish for that round) stays counted, from either side', unit['realLocal'] == [4, 1] and unit['realRemote'] == [4, 1], [unit['realLocal'], unit['realRemote']])
            ok('11 merge: repeating the merge any number of times and in either direction changes nothing more', unit['repeat'] == [3, 3, 3, 3, 3, 1, 1, 1, 1, 1, 0], unit['repeat'])
            ok('11 merge: history cut to 150 rounds, lifetime counter 40 is not recomputed from it (40 -> 39 for the one corrected round)', unit['long'] == [150, 150, 39, 14], unit['long'])
            ok('11 merge: long history without a correction keeps the lifetime counter exactly', unit['longNoFix'] == [150, 40, 15], unit['longNoFix'])
            ok('11 merge: older records without the marker are left alone (no guessing)', unit['legacy'] == [4, 0], unit['legacy'])
            ok('11 merge: counter never goes below zero and is not invented where neither side had it', unit['floor'] == [0] and unit['noKey'] == [False], [unit['floor'], unit['noKey']])
            ok('11 merge: two wrong counts and one real abandon in one merge', unit['mixed'] == [5, 2, 1], unit['mixed'])
            ok('11 merge: the marker lives only on unfinished records; other counters are untouched', unit['flag'] == [1, False] and unit['others'] == [7, 9, 3], [unit['flag'], unit['others']])

            for label, device in (('server', False), ('device', True)):
                cq = await ctx(); q1 = await page(cq)
                if device: await q1.evaluate("setMode('device').then(render)"); await q1.wait_for_timeout(300)
                name = 'QA-Dup-' + label; await plant(q1, name)
                await start(q1, 'schulte')
                q2 = await twin(cq, q1)
                mid = json.loads(await q2.evaluate(COUNTS))
                ok(f'11 {label}: duplicated tab counts the running round as abandoned (the cause)', mid[1] == 1 and mid[2] == 1, mid)
                await finish_schulte(q1); await reload(q1); await reload(q2)
                ok(f'11 {label}: after the round is finished the wrong count is gone in both tabs', [json.loads(await x.evaluate(COUNTS)) for x in (q1, q2)] == [[1, 0, 0, 1], [1, 0, 0, 1]], [await x.evaluate(COUNTS) for x in (q1, q2)])
                if not device:
                    srv = [r['state'] for r in players() if r['state']['nick'] == name][0]
                    ok('11 server: stored state agrees (finished record, counter 0)', [h['s'] for h in srv['hist']] == [100] and srv['tot'].get('quit', 0) == 0, srv['tot'])
                # a real abandon afterwards is counted once and survives another duplicated-tab episode
                await start(q1, 'schulte'); await q1.evaluate("ACT.quit()"); await q1.wait_for_timeout(600)
                await start(q1, 'schulte'); q3 = await twin(cq, q1, 'he.html')
                await finish_schulte(q1); await reload(q1); await reload(q2); await reload(q3)
                ok(f'11 {label}: one real abandon stays counted exactly once after a second episode', [json.loads(await x.evaluate(COUNTS)) for x in (q1, q2, q3)] == [[2, 1, 1, 2]] * 3, [await x.evaluate(COUNTS) for x in (q1, q2, q3)])

            cs = await ctx(); s1 = await page(cs); await plant(s1, 'QA-Stale')
            await start(s1, 'schulte'); await s1.evaluate("S.cur.at-=31*60*1000;G.t0=S.cur.at;save()"); await s1.wait_for_timeout(600)
            s2 = await page(cs, 'en.html'); await s2.wait_for_timeout(500)
            ok('11 round older than 30 minutes: the other tab counts it as abandoned (the cause)', json.loads(await s2.evaluate(COUNTS))[1:3] == [1, 1])
            await finish_schulte(s1); await reload(s1); await reload(s2)
            ok('11 ...and the count is corrected once that round is finished', [json.loads(await x.evaluate(COUNTS)) for x in (s1, s2)] == [[1, 0, 0, 1]] * 2, [await x.evaluate(COUNTS) for x in (s1, s2)])
            cr = await ctx(); r1 = await page(cr); await plant(r1, 'QA-RealQuit')
            await start(r1, 'schulte'); await reload(r1)
            r2 = await page(cr, 'en.html'); await r2.evaluate("ACT.water()"); await r2.wait_for_timeout(1900); await reload(r1); await reload(r2)
            ok('11 a round really left by reloading stays abandoned and counted once across tabs', [json.loads(await x.evaluate(COUNTS)) for x in (r1, r2)] == [[0, 1, 1, 0]] * 2, [await x.evaluate(COUNTS) for x in (r1, r2)])

            # 12. version 3.5: live panel tools, journal, harvest check on the server, reconnect, contrast, no-save mode
            V4 = os.environ.get('NG_SCHEMA') != '3'
            ok('12 honest play in every scenario above was never cut by the server', all(r['act'] != 'clamp' for r in post('/__test/log')), post('/__test/log')[:3])
            cl1 = await ctx(); l1 = await page(cl1); await plant(l1, 'QA-LiveA')
            cl2 = await ctx(); l2 = await page(cl2, 'he.html'); await plant(l2, 'QA-LiveB')
            post('/__test/google', {'email': 'owner@example.test'})
            co = await ctx(); pn = await page(co, 'en.html#admin'); await google(pn, "isOwner===true")
            REF = "adminRefresh().then(()=>{render();return true})"
            MAIN = "document.querySelector('#main').innerText"
            await start(l1, 'schulte'); await start(l2, 'seq', 2); await l1.wait_for_timeout(600); await pn.evaluate(REF)
            feed = await pn.evaluate("[...document.querySelectorAll('#feed p')].map(x=>x.innerText).join(' | ')")
            ok('12 two people play at once: the panel shows both, with game, level and start time, without a reload',
               'QA-LiveA' in feed and 'QA-LiveB' in feed and 'level 1 of 5' in feed and 'level 3 of 5' in feed and 'Numbers in order' in feed, feed)
            await pn.select_option('#afilter', 'play'); await pn.wait_for_timeout(200)
            ok('12 filter "playing now" leaves exactly the two players', json.loads(await pn.evaluate("JSON.stringify([...document.querySelectorAll('.trees .tcard b')].map(x=>x.textContent).sort())")) == ['QA-LiveA', 'QA-LiveB'])
            before = await pn.evaluate("people.find(p=>p.nick==='QA-LiveA').apples")
            await finish_schulte(l1); await pn.evaluate(REF)
            ok('12 the finished round reaches the panel: a ripe apple appears and the player leaves "playing now"',
               await pn.evaluate("people.find(p=>p.nick==='QA-LiveA').apples") == before + 1 and 'QA-LiveA' not in await pn.evaluate("[...document.querySelectorAll('#feed p')].map(x=>x.innerText).join()"))
            await l2.evaluate("ACT.quit()"); await l2.wait_for_timeout(600); await pn.evaluate(REF)
            ok('12 a player who left the game has no ripe apple and an abandoned round in the panel',
               json.loads(await pn.evaluate("JSON.stringify((p=>[p.apples,p.hist.length,p.hist[0].s,!!p.cur])(people.find(p=>p.nick==='QA-LiveB')))")) == [0, 1, None, False])
            await pn.select_option('#afilter', 'all'); await pn.fill('#aq', 'livea'); await pn.wait_for_timeout(200)
            ok('12 search by name keeps focus and narrows the list', await pn.evaluate("document.activeElement.id==='aq'&&document.querySelectorAll('.trees .tcard').length===1"))
            await pn.fill('#aq', ''); await pn.select_option('#asort', 'apples'); await pn.wait_for_timeout(200)
            ap = json.loads(await pn.evaluate("JSON.stringify((()=>{const m={};people.forEach(p=>m[p.nick]=p.apples);return [...document.querySelectorAll('.trees .tcard b')].map(x=>m[x.textContent])})())"))
            ok('12 sort by apples is in descending order', ap == sorted(ap, reverse=True) and len(ap) > 3, ap)
            await pn.click('.trees .tcard'); await pn.wait_for_timeout(200)
            ok('12 participant card shows planting date and identifier', 'Tree planted:' in await pn.evaluate(MAIN) and 'Identifier:' in await pn.evaluate(MAIN))
            ok('12 new panel controls are translated (EN)', not re.search('[А-Яа-яЁё]', await pn.evaluate(MAIN)), re.findall('[А-Яа-яЁё][^\n]{0,30}', await pn.evaluate(MAIN))[:3])
            # replaying the save of a finished round, and an inflated counter, straight to the API
            key, pid, rev = await l1.evaluate("KEY"), await l1.evaluate("ACC.active"), await l1.evaluate("REV[ACC.active]")
            stt = json.loads(await l1.evaluate("JSON.stringify(S)")); real = stt['apples']
            stt['apples'] = real + 5
            ans = rpc('ng_save', {'p_key': key, 'p_pid': pid, 'p_state': stt, 'p_rev': rev})
            stored = [r['state']['apples'] for r in players() if r['state']['nick'] == 'QA-LiveA'][0]
            ok('12 re-sending the finished round with a bigger counter does not grow the harvest' if V4 else '12 (schema 3 stand) the server stores the counter it is sent', stored == (real if V4 else real + 5) and 'rev' in ans, stored)
            if V4:
                await reload(l1)
                ok('12 after a reload the player still has exactly the earned apples', await l1.evaluate("S.apples") == real and await l1.evaluate(FIN) == 1)
                rpc('ng_save', {'p_key': 'f' * 40, 'p_pid': 'p1', 'p_state': {'nick': 'QA-Fake', 'apples': 999999, 'hist': []}, 'p_rev': None})
                await pn.evaluate("location.hash='common'"); await pn.wait_for_timeout(900)
                ok('12 a tree created with a made-up harvest shows zero apples in the shared garden', await pn.evaluate("(trees.find(t=>t.nick==='QA-Fake')||{}).apples") == 0)
                await pn.evaluate("location.hash='admin'"); await pn.wait_for_timeout(900)
            await pn.evaluate("sel=people.find(p=>p.nick==='QA-LiveB').id;render()"); await pn.fill('#cheer', 'QA journal cheer'); await pn.click('[data-act=cheersend]'); await pn.wait_for_timeout(500)
            await pn.click('[data-act=atab][data-t=log]'); await pn.wait_for_timeout(900)
            jt = await pn.evaluate(MAIN)
            if V4:
                ok('12 journal shows the owner action and the cut harvest', 'Encouragement sent' in jt and 'QA journal cheer' in jt and 'apples sent: 999999, accepted: 0' in jt and 'QA-Fake' in jt, jt[-400:])
                ok('12 journal is closed to outsiders', status('ng_owner_log')[0] in (401, 403))
            else:
                ok('12 (schema 3 stand) journal tab says the server part is not connected yet, the rest of the panel works', 'version 4' in jt and await pn.evaluate("isOwner===true&&people.length>0"), jt[-300:])
            # many participants: the panel and the shared garden draw in portions
            for i in range(130):
                rpc('ng_save', {'p_key': f'{i:040d}', 'p_pid': 'p1', 'p_state': {'nick': f'QA-Bulk{i:03d}', 'apples': 0, 'hist': []}, 'p_rev': None})
            await pn.click('[data-act=atab][data-t=live]'); await pn.evaluate(REF)
            n0 = await pn.evaluate("document.querySelectorAll('.trees .tcard').length"); await pn.click('[data-act=amore]'); await pn.wait_for_timeout(200)
            ok('12 panel draws 60 trees at a time and "show more" adds the next 60', n0 == 60 and await pn.evaluate("document.querySelectorAll('.trees .tcard').length") == 120 and await pn.evaluate("people.length") > 130)
            await pn.evaluate("location.hash='common'"); await pn.wait_for_timeout(900)
            n1 = await pn.evaluate("document.querySelectorAll('.trees .tcard').length"); await pn.click('[data-act=cmore]'); await pn.wait_for_timeout(200)
            ok('12 shared garden draws 60 trees at a time, search still covers all of them', n1 == 60 and await pn.evaluate("document.querySelectorAll('.trees .tcard').length") == 120)
            await pn.fill('#q', 'bulk129'); await pn.wait_for_timeout(300)
            ok('12 ...a tree outside the first portion is found by name', await pn.evaluate("document.querySelectorAll('.trees .tcard').length") == 1)
            # connection lost and restored
            cn = await ctx(); n1p = await page(cn); await plant(n1p, 'QA-Net'); await n1p.wait_for_timeout(500)
            await cn.set_offline(True); await start(n1p, 'schulte')
            for i in range(1, 10): await n1p.click(f'.cell[data-v="{i}"]')
            await n1p.wait_for_selector('[data-act=again]'); await n1p.wait_for_timeout(900)
            ok('12 offline: the failed save is reported right on the result screen', await n1p.evaluate("(document.querySelector('#savenote')||{}).textContent===NOSAVE"))
            await n1p.evaluate("ACT.home()"); await n1p.wait_for_timeout(500)
            srv = lambda: [r['state'] for r in players() if r['state']['nick'] == 'QA-Net'][0]
            ok('12 offline: the round is kept in the browser, the player is told, the server has nothing yet', await n1p.evaluate("S.apples===1&&unsaved===true&&notice===NOSAVE") and srv().get('apples', 0) == 0, await n1p.evaluate("JSON.stringify([S.apples,unsaved,notice])"))
            await cn.set_offline(False); await n1p.evaluate("window.dispatchEvent(new Event('online'))"); await n1p.wait_for_timeout(1500)
            ok('12 connection back: the result is sent by itself, once, and the message changes', srv()['apples'] == 1 and len(srv()['hist']) == 1 and await n1p.evaluate("unsaved===false&&notice!==NOSAVE"), await n1p.evaluate("JSON.stringify([unsaved,notice])"))
            # no-save mode leaves no trace
            cz = await ctx(); z = await page(cz, '#prefs'); await z.check('#m_nosave'); await z.wait_for_timeout(400)
            npl = len(players()); await z.evaluate("location.hash='garden'"); await z.wait_for_timeout(300); await start(z, 'schulte'); await finish_schulte(z); await z.wait_for_timeout(600)
            ok('12 no-save mode: the round is played and counted on screen', await z.evaluate("mode==='nosave'&&S.apples===1"))
            ok('12 no-save mode: nothing on the server and no history in browser storage', len(players()) == npl and await z.evaluate("localStorage.getItem('ng_state')===null&&!JSON.stringify(Object.assign({},localStorage)).includes('schulte')"), await z.evaluate("JSON.stringify(Object.assign({},localStorage))"))
            await reload(z)
            ok('12 no-save mode: after a reload the history is gone', await z.evaluate("mode==='nosave'&&S.apples===0&&S.hist.length===0"))
            # high contrast
            ch = await ctx(); hc = await page(ch, 'he.html#prefs'); await hc.check('#hc'); await reload(hc)
            ok('12 high contrast: stays on after a reload, black on white', await hc.evaluate("document.documentElement.classList.contains('hc')&&getComputedStyle(document.body).backgroundColor==='rgb(255, 255, 255)'&&getComputedStyle(document.body).color==='rgb(0, 0, 0)'"))
            ok('12 high contrast and the new texts are translated (HE)', not re.search('[А-Яа-яЁё]', await hc.evaluate(MAIN)))

            for f, ver in (('', 'Версия'), ('he.html', 'גרסה'), ('en.html', 'Version')):
                pg = await page(await ctx(), f)
                rel = json.load(open(ROOT / 'releases.json'))
                t = await pg.evaluate("[document.querySelector('#ver').textContent,document.querySelector('#prev').textContent,...[...document.querySelectorAll('#vers time')].map(x=>x.getAttribute('datetime'))]")
                ok(f'9 {f or "index.html"}: version block shows {rel[-1]["version"]} and {rel[-2]["version"]} with dates from releases.json',
                   t[0] == f'{ver} {rel[-1]["version"]}' and rel[-2]['version'] in t[1] and t[2:] == [rel[-1]['at'], rel[-2]['at']], t)
            ok('no script errors in any tab', not errs, errs[:3])
            await b.close()
    finally:
        db.kill(); web.kill(); shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{P} passed, {F} failed')
    sys.exit(1 if F else 0)


asyncio.run(main())
