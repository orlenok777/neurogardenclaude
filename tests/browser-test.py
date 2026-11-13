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
            ok('10 sign-out: the old token no longer works on the server', status('ng_owner_list', tok_o)[0] == 401)
            await reload(ow); await ow.evaluate("location.hash='admin'"); await ow.wait_for_timeout(600)
            ok('10 sign-out holds after reload', await ow.evaluate("!AU&&!isOwner&&people.length===0"))
            ok('10 tokens never reach localStorage or the page HTML', await ad.evaluate("!JSON.stringify(Object.assign({},localStorage)).includes(AU.access_token)&&!document.documentElement.outerHTML.includes(AU.access_token)"))
            he_ad = await page(await ctx(), 'he.html#admin'); await he_ad.wait_for_timeout(600)
            ok('10 HE sign-in card is translated', not re.search('[А-Яа-яЁё]', await he_ad.evaluate(ADMIN)) and await he_ad.evaluate("!!document.querySelector('[data-act=alogin]')"))
            # players are unaffected by all of the above
            await reload(a)
            ok('10 players unaffected: names, history and apples as before', json.loads(await a.evaluate("JSON.stringify(Object.values(ACC.profiles).map(q=>[q.nick,q.hist.length,q.apples]))")) == [['QA-One', 1, 1], ['QA-Two', 1, 1]])
            await o.evaluate("heartbeat()"); await o.wait_for_timeout(700)
            ok('10 the cheered player sees the cheer in the garden', await o.evaluate("cheer&&cheer.text") == 'QA cheer')

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
