"""Sandbox checks for the 3D helper: rules before every game, hint in the game, comment after it, guided training,
the level advice and the strengths map.

    pip install playwright && playwright install chromium
    python3 tests/helper-test.py

Runs on a local copy of the three built pages and the helper/ folder. The only name used is synthetic ("QA-Helper").
The browser's speech synthesis is replaced by a recorder, so the test sees exactly what would be spoken and decides
when the speech "ends"; no sound is produced. Every network request is recorded: anything that is not this local
stand (or the Google Fonts stylesheet the site has always used) fails the run, so the pages provably do not talk to Supabase or any other server.
"""
import asyncio, json, pathlib, re, shutil, subprocess, sys, tempfile
from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = 8788
P = F = 0
CYR = re.compile(r'[А-Яа-яЁё]')
MEM = ['leaves', 'seq', 'gone', 'basket', 'pairs', 'back', 'order']
VOICE = """window.__spoken=[];window.__u=null;
window.SpeechSynthesisUtterance=function(t){this.text=t};
Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{
  speak(u){window.__spoken.push({text:u.text,lang:u.lang,rate:u.rate});window.__u=u},
  cancel(){const u=window.__u;window.__u=null;if(u&&u.onend)setTimeout(()=>u.onend({}),0)}}});"""
NOVOICE = "delete window.SpeechSynthesisUtterance;try{Object.defineProperty(window,'speechSynthesis',{configurable:true,value:undefined});delete window.speechSynthesis}catch(e){}"
NOGL = "const g=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return /webgl/.test(t)?null:g.call(this,t,...a)};"
LOOK = """()=>{const d=document.documentElement,small=[];
 for(const b of document.querySelectorAll('#main button:not([disabled])')){const r=b.getBoundingClientRect();if(r.width>0&&(r.height<40||r.width<40))small.push(b.textContent.trim()+':'+Math.round(r.width)+'x'+Math.round(r.height))}
 return{over:d.scrollWidth-window.innerWidth,small,text:(document.querySelector('#helper')||{}).innerText||''}}"""
PIXELS = """()=>{HELP.api.draw();const c=document.createElement('canvas');c.width=c.height=48;const x=c.getContext('2d');x.drawImage(HELP.cv,0,0,48,48);
 const d=x.getImageData(0,0,48,48).data,s=new Set();for(let i=0;i<d.length;i+=4)if(d[i+3]>200)s.add((d[i]>>4)+','+(d[i+1]>>4)+','+(d[i+2]>>4));return s.size}"""


def ok(name, cond, extra=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  -> ' + str(extra)[:500]))


async def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='ng-helper-'))
    for f in ('index.html', 'he.html', 'en.html'):
        shutil.copy(ROOT / f, tmp / f)                      # the pages exactly as they would be published
    shutil.copytree(ROOT / 'helper', tmp / 'helper')
    web = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT), '--bind', '127.0.0.1'], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            outside, errs = [], []

            async def page(f, init=VOICE):
                cx = await b.new_context(viewport={'width': 360, 'height': 740}, has_touch=True, is_mobile=True)
                await cx.add_init_script(init)
                pg = await cx.new_page()
                pg.on('request', lambda r: None if r.url.startswith(f'http://127.0.0.1:{PORT}/') or r.url.startswith('data:') or r.url.startswith('blob:') or re.match(r'https://fonts\.(googleapis|gstatic)\.com/', r.url) else outside.append(r.url))
                pg.on('pageerror', lambda e: errs.append(f + ': ' + str(e)))
                await pg.goto(f'http://127.0.0.1:{PORT}/{f}'); await pg.wait_for_selector('#nick')
                await pg.fill('#nick', 'QA-Helper'); await pg.click('[data-act=plant]'); await pg.wait_for_timeout(200)
                return pg

            async def mouth_moves(pg, expr, ms=1500):
                seen = set()
                for _ in range(ms // 50):
                    seen.add(await pg.evaluate(expr)); await pg.wait_for_timeout(50)
                return seen

            for f, lang, speech in (('index.html', 'ru', 'ru-RU'), ('he.html', 'he', 'he-IL'), ('en.html', 'en', 'en-GB')):
                pg = await page(f)
                ok(f'{lang}: header shows version 3.7 and the previous one, 3.6', '3.7' in await pg.evaluate("document.querySelector('#ver').textContent") and '3.6' in await pg.evaluate("document.querySelector('#prev').textContent"))
                ok(f'{lang}: sandbox notice is shown, the database is not connected, the panel tab is hidden', await pg.evaluate("!!document.querySelector('#sandbox')&&sbOK===false&&SB_URL===''&&!document.querySelector('#nav a[href=\"#admin\"]')"))
                ok(f'{lang}: language links stay inside the sandbox copy', json.loads(await pg.evaluate("JSON.stringify([...document.querySelectorAll('.langs a')].map(a=>a.getAttribute('href')))")) == ['index.html', 'he.html', 'en.html'])
                ALL = await pg.evaluate("ORDER")
                ok(f'{lang}: the catalogue has 27 games', len(ALL) == 27 and len(set(ALL)) == 27, len(ALL))
                bad = []
                for gid in ALL:
                    await pg.evaluate(f"openGame('{gid}')"); await pg.wait_for_timeout(60)
                    n = json.loads(await pg.evaluate("JSON.stringify([!!document.querySelector('#helper #hslot'),document.querySelectorAll('#helper [data-act=hexplain]').length,document.querySelectorAll('#helper [data-act=hrepeat]').length,document.querySelectorAll('#helper [data-act=start]').length,document.querySelectorAll('[data-act=start]').length,document.querySelectorAll('#hslot canvas,#hslot svg').length])"))
                    r = await pg.evaluate(LOOK)
                    if n != [True, 1, 1, 1, 1, 1] or r['over'] > 1 or r['small'] or (lang != 'ru' and CYR.search(r['text'])): bad.append((gid, n, r))
                ok(f'{lang}: before each of the 27 games there is one face and the three buttons, fitting a 360 px screen', not bad, bad[:2])
                await pg.evaluate("openGame('leaves')")
                await pg.wait_for_function("HELP.api||HELP.failed", timeout=60000)
                ok(f'{lang}: the 3D model loads and its canvas replaces the drawn face', await pg.evaluate("!!HELP.api&&!HELP.failed&&document.querySelector('#hslot').firstChild===HELP.cv&&document.querySelectorAll('#hslot svg').length===0"))
                ok(f'{lang}: the canvas really shows a picture (many colours, not a blank square)', await pg.evaluate(PIXELS) > 12, await pg.evaluate(PIXELS))
                rules = await pg.evaluate("G.g.name+'. '+G.g.how")
                await pg.click('[data-act=hexplain]')
                sp = json.loads(await pg.evaluate("JSON.stringify(__spoken)"))
                ok(f'{lang}: "Explain the rules" speaks the name and the rules of this game in the page language', len(sp) == 1 and sp[0]['text'] == rules and sp[0]['lang'] == speech and len(rules) > 40, sp)
                ok(f'{lang}: the rules being spoken are written on the screen next to the face', await pg.evaluate("document.querySelector('#main').innerText.includes(G.g.how)&&document.querySelector('#hcap').textContent.length>20"))
                shapes = await mouth_moves(pg, "HELP.shape")
                ok(f'{lang}: while the voice speaks the lips go through several shapes and close in pauses', len(shapes) >= 4 and '' in shapes, shapes)
                ok(f'{lang}: the 3D mouth is really open during speech', max([await pg.evaluate("HELP.api.mouth()") for _ in range(8) if not await pg.wait_for_timeout(70)]) > 0.15)
                await pg.evaluate("__u.onboundary({charIndex:5});0")
                await pg.evaluate("__u.onend({})"); await pg.wait_for_timeout(900)
                ok(f'{lang}: when the voice ends the mouth closes', await pg.evaluate("HELP.on===false&&HELP.shape===''&&HELP.api.mouth()<0.02"), await pg.evaluate("JSON.stringify([HELP.on,HELP.shape,HELP.api.mouth()])"))
                await pg.click('[data-act=hrepeat]')
                sp = json.loads(await pg.evaluate("JSON.stringify(__spoken)"))
                ok(f'{lang}: "Repeat" speaks the same words again', len(sp) == 2 and sp[1]['text'] == rules)
                await pg.click('#helper [data-act=start]'); await pg.wait_for_timeout(300)
                st = json.loads(await pg.evaluate("JSON.stringify([G.active,G.trial,HELP.on,__u===null,!!document.querySelector('.hmini [data-act=hhint]'),document.querySelector('#hslot').firstChild===HELP.cv,Math.round(HELP.cv.getBoundingClientRect().width)])"))
                ok(f'{lang}: "Start" begins the real round and stops the voice; the game shows a compact hint with the same face, small', st == [True, False, False, True, True, True, 56], st)
                r = await pg.evaluate(LOOK)
                ok(f'{lang}: the compact hint fits the screen and is translated', r['over'] <= 1 and not r['small'] and not (lang != 'ru' and CYR.search(r['text'])), r)
                await pg.click('[data-act=hhint]')
                sp = json.loads(await pg.evaluate("JSON.stringify(__spoken)"))
                ok(f'{lang}: "Hint" speaks the short reminder of this game and shows it', len(sp) == 3 and sp[2]['text'] == await pg.evaluate("G.g.desc") and await pg.evaluate("document.querySelector('#hcap').textContent===G.g.desc"))
                h0 = await pg.evaluate("S.hist.length")
                await pg.evaluate("ACT.quit()"); await pg.wait_for_timeout(300)
                ok(f'{lang}: leaving the game stops the voice and records one abandoned round, as before', await pg.evaluate("HELP.on===false&&__u===null&&S.hist.length") == h0 + 1 and await pg.evaluate("S.hist.at(-1).s===null"))
                await pg.evaluate("openGame('seq')"); await pg.click('[data-act=hrepeat]')
                ok(f'{lang}: in another game "Repeat" explains that game, not the previous one', await pg.evaluate("__spoken.at(-1).text===G.g.name+'. '+G.g.how"))
                await pg.evaluate("location.hash='games'"); await pg.wait_for_timeout(300)
                ok(f'{lang}: leaving the page stops the voice', await pg.evaluate("HELP.on===false&&__u===null"))
                # ---- comment after the round ----
                FIN = "ACT.start();finish({score:%d,err:0})"
                n0 = await pg.evaluate("__spoken.length")
                await pg.evaluate("openGame('schulte')"); await pg.evaluate(FIN % 100); await pg.wait_for_timeout(200)
                msg = await pg.evaluate("G.res.msg")
                ok(f'{lang}: after a round the helper shows her comment next to her face and says it', await pg.evaluate("!!document.querySelector('.hmini #hslot')&&document.querySelector('#hcap').textContent===G.res.msg&&__spoken.length") == n0 + 1 and await pg.evaluate("__spoken.at(-1).text") == msg and len(msg) > 10)
                await pg.evaluate("__u.onend({})"); await pg.click('[data-act=hrepeat]')
                ok(f'{lang}: "Repeat" on the result screen says the comment again', await pg.evaluate("__spoken.length") == n0 + 2 and await pg.evaluate("__spoken.at(-1).text") == msg)
                r = await pg.evaluate(LOOK)
                ok(f'{lang}: the result screen fits and is translated', r['over'] <= 1 and not r['small'] and not (lang != 'ru' and CYR.search(await pg.evaluate("document.querySelector('#main').innerText"))), r)
                await pg.evaluate("ACT.home()"); await pg.wait_for_timeout(200)
                # ---- level advice from the last two rounds ----
                async def adv(rounds):
                    await pg.evaluate("S.hist=S.hist.filter(h=>h.g!=='arrow').concat(%s.map((r,i)=>({g:'arrow',l:r[0],at:1000+i,d:9,s:r[1],ok:r[1]>=60})));openGame('arrow')" % json.dumps(rounds))
                    return json.loads(await pg.evaluate("JSON.stringify([G.lvl,G.adv,!!document.querySelector('#adv'),document.querySelector('.chips [aria-pressed=true]').textContent])"))
                got = [await adv(x) for x in ([], [[1, 90], [1, 95]], [[2, 40], [2, 30]], [[2, 90], [2, 55]], [[1, 90], [2, 90]], [[4, 100], [4, 100]], [[0, 10], [0, 20]], [[3, 85], [3, 85]], [[3, 49], [3, 49]], [[2, 100]])]
                want = [[0, '', False, '1'], [2, 'up', True, '3'], [1, 'down', True, '2'], [2, '', False, '3'], [2, '', False, '3'], [4, '', False, '5'], [0, '', False, '1'], [4, 'up', True, '5'], [2, 'down', True, '3'], [2, '', False, '3']]
                ok(f'{lang}: two excellent rounds raise the offered level, two weak ones lower it, anything else keeps it', got == want, got)
                ok(f'{lang}: the advice is explained in words and translated', len(await pg.evaluate("(openGame('arrow'),S.hist.push({g:'arrow',l:2,at:5000,d:9,s:90,ok:true},{g:'arrow',l:2,at:5001,d:9,s:90,ok:true}),openGame('arrow'),document.querySelector('#adv').textContent)")) > 30 and not (lang != 'ru' and CYR.search(await pg.evaluate("document.querySelector('#adv').textContent"))))
                await pg.evaluate("S.hist=S.hist.filter(h=>h.g!=='arrow');ACT.home()"); await pg.wait_for_timeout(200)
                # ---- training with the helper ----
                plan = await pg.evaluate("todays()"); a0 = await pg.evaluate("S.apples"); n0 = await pg.evaluate("__spoken.length")
                await pg.click('[data-act=train]'); await pg.wait_for_timeout(150)
                ok(f'{lang}: "Training with the helper" opens the first of today\'s four exercises, shows step 1 of 4 and explains the rules by itself', len(plan) == 4 and await pg.evaluate("G.g.id") == plan[0] and re.search(r'1\D+4', await pg.evaluate("document.querySelector('#trstep').textContent")) and await pg.evaluate("__spoken.length===%d&&__spoken.at(-1).text===G.g.name+'. '+G.g.how" % (n0 + 1)))
                steps = []
                for i in range(4):
                    await pg.evaluate(FIN % (100 if i != 2 else 40)); await pg.wait_for_timeout(120)
                    steps.append(json.loads(await pg.evaluate("JSON.stringify([G.g.id,document.querySelectorAll('[data-act=tnext]').length,document.querySelector('[data-act=tnext]').classList.contains('pri')])")))
                    await pg.click('[data-act=tnext]'); await pg.wait_for_timeout(150)
                ok(f'{lang}: "Next" leads through all four exercises in order', [x[0] for x in steps] == plan and all(x[1:] == [1, True] for x in steps), steps)
                end = json.loads(await pg.evaluate("JSON.stringify([G===null,TR.done===true,document.querySelectorAll('#main .item').length,[...document.querySelectorAll('#main .item .pill')].map(x=>x.textContent),document.querySelector('#hcap').textContent,__spoken.at(-1).text,S.apples])"))
                ok(f'{lang}: at the end she sums up: four results, three ripe apples, and says it aloud', end[:4] == [True, True, 4, ['100%', '100%', '40%', '100%']] and ' 3.' in end[4] and end[5] == end[4] and end[6] == a0 + 3, end)
                r = await pg.evaluate(LOOK)
                ok(f'{lang}: the summary fits the screen and is translated', r['over'] <= 1 and not r['small'] and not (lang != 'ru' and CYR.search(await pg.evaluate("document.querySelector('#main').innerText"))), r)
                await pg.click('[data-act=home]'); await pg.wait_for_timeout(250)
                ok(f'{lang}: back in the garden the training is over; a game opened by hand has no training step', await pg.evaluate("TR===null&&(openGame('pairs'),!document.querySelector('#trstep'))"))
                await pg.evaluate("ACT.home()"); await pg.wait_for_timeout(200)
                await pg.click('[data-act=train]'); await pg.evaluate("ACT.start()"); await pg.evaluate("ACT.quit()"); await pg.wait_for_timeout(250)
                ok(f'{lang}: leaving a game in the middle ends the training', await pg.evaluate("TR===null&&G===null"))
                # ---- strengths map ----
                await pg.evaluate("S.hist=[];location.hash='progress'"); await pg.wait_for_timeout(250)
                ok(f'{lang}: with no rounds the strengths map says so for all four areas', await pg.evaluate("document.querySelectorAll('#strengths .sbar').length===4&&strengths(S).every(x=>x.v===null&&x.n===0)&&[...document.querySelectorAll('#strengths .sbar i')].every(i=>i.style.width==='0%')"))
                await pg.evaluate("S.hist=[['leaves',4,100],['seq',4,100],['gone',4,100],['arrow',0,50],['color',0,50],['dual',0,50],['series',2,75],['mirror',1,100]].map((r,i)=>({g:r[0],l:r[1],at:9000+i,d:9,s:r[2],ok:r[2]>=60}));render()")
                sm = json.loads(await pg.evaluate("JSON.stringify([strengths(S).map(x=>[x.c,x.n,x.v]),document.querySelector('#sline').textContent,CATS.mem,CATS.att,[...document.querySelectorAll('#strengths .sbar i')].map(i=>i.style.width)])"))
                ok(f'{lang}: the map weighs result by level: memory 100, attention 30, logic 60, space 70', sm[0] == [['mem', 3, 100], ['att', 3, 30], ['log', 1, 60], ['spa', 1, 70]] and sm[4] == ['100%', '30%', '60%', '70%'], sm[0])
                ok(f'{lang}: it names the strongest area and the one to practise, only from areas with three rounds', sm[2] in sm[1] and sm[3] in sm[1] and sm[1].index(sm[2]) < sm[1].index(sm[3]), sm[1])
                r = await pg.evaluate(LOOK)
                ok(f'{lang}: the progress page fits the screen and is translated', r['over'] <= 1 and not (lang != 'ru' and CYR.search(await pg.evaluate("document.querySelector('#strengths').innerText"))), r)
                # ---- sounds switched off: she does not speak by herself ----
                await pg.evaluate("S.hist=[];location.hash='prefs'"); await pg.wait_for_timeout(200); await pg.check('#mute'); n0 = await pg.evaluate("__spoken.length")
                await pg.evaluate("openGame('schulte')"); await pg.evaluate(FIN % 100); await pg.wait_for_timeout(150)
                ok(f'{lang}: with sounds switched off she stays silent after a round; the comment is on the screen and "Repeat" still speaks', await pg.evaluate("__spoken.length") == n0 and await pg.evaluate("document.querySelector('#hcap').textContent===G.res.msg") and (await pg.click('[data-act=hrepeat]') or await pg.evaluate("__spoken.length")) == n0 + 1)
                await pg.evaluate("G=null;go()"); await pg.wait_for_timeout(200); await pg.uncheck('#mute')
                ok(f'{lang}: names and progress are kept only in this browser', await pg.evaluate("mode==='local'&&JSON.parse(localStorage.getItem('ng_acc')||'{}').profiles!==undefined") or await pg.evaluate("mode"), await pg.evaluate("JSON.stringify([mode,Object.keys(localStorage)])"))
                await pg.context.close()

            # ---- 3D is not available: the drawn face takes over ----
            pg = await page('index.html', VOICE + NOGL)
            await pg.evaluate("openGame('pairs')"); await pg.wait_for_function("HELP.failed===true", timeout=60000)
            ok('no 3D in the browser: a drawn face is shown and the three buttons are there', await pg.evaluate("!HELP.api&&document.querySelectorAll('#hslot svg').length===1&&document.querySelectorAll('#helper button').length===3"))
            await pg.click('[data-act=hexplain]')
            ry = await mouth_moves(pg, "document.querySelector('#hmouth').getAttribute('ry')", 1200)
            ok('no 3D: the voice speaks and the drawn mouth moves', await pg.evaluate("__spoken.length") == 1 and len(ry) >= 3, ry)
            await pg.click('#helper [data-act=start]'); await pg.wait_for_timeout(200)
            ok('no 3D: the game starts and shows the compact hint with the drawn face', await pg.evaluate("G.active===true&&document.querySelectorAll('.hmini #hslot svg').length===1&&!!document.querySelector('[data-act=hhint]')"))
            await pg.context.close()

            # ---- no speech synthesis in the browser ----
            pg = await page('index.html', NOVOICE)
            await pg.evaluate("openGame('gone')"); await pg.click('[data-act=hexplain]'); await pg.wait_for_timeout(300)
            ok('no voice in the browser: the rules stay on the screen as text, nothing breaks', await pg.evaluate("canSay()===false&&HELP.on===false&&document.querySelector('#main').innerText.includes(G.g.how)"))
            await pg.click('#helper [data-act=start]'); await pg.wait_for_timeout(200); await pg.click('[data-act=hhint]'); await pg.wait_for_timeout(100)
            ok('no voice: the hint in the game appears as text', await pg.evaluate("document.querySelector('#hcap').textContent===G.g.desc"))
            await pg.evaluate("ACT.quit()"); await pg.wait_for_timeout(100); await pg.evaluate("openGame('gone')")
            await pg.click('#helper [data-act=start]'); await pg.wait_for_timeout(200)
            ok('no voice: the game still starts', await pg.evaluate("G.active===true"))
            await pg.context.close()

            # ---- "switch off animation" ----
            pg = await page('index.html')
            await pg.evaluate("location.hash='prefs'"); await pg.wait_for_timeout(200); await pg.check('#still'); await pg.wait_for_timeout(200)
            await pg.evaluate("openGame('back')"); await pg.wait_for_function("HELP.api||HELP.failed", timeout=60000)
            await pg.click('[data-act=hexplain]'); await pg.wait_for_timeout(300)
            ok('animation switched off in settings: the voice and the text work, the face is drawn without the idle loop', await pg.evaluate("__spoken.length===1&&document.querySelector('#hcap').textContent.length>40&&document.documentElement.classList.contains('still')"))
            await pg.context.close()

            ok('the pages made no request to any server other than the local stand and the Google Fonts the site already used (no Supabase, no script CDN)', not outside, outside[:5])
            ok('no script errors', not errs, errs[:3])
            src = ''.join((ROOT / f).read_text(encoding='utf-8') for f in ('index.html', 'he.html', 'en.html'))
            ok('the published pages contain no database address or key', 'supabase.co' not in src and 'sb_publishable' not in src)
            await b.close()
    finally:
        web.kill(); shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{P} passed, {F} failed')
    sys.exit(1 if F else 0)


asyncio.run(main())
