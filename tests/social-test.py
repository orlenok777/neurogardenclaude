"""Consent and optional speech on local PGlite only. All non-local browser traffic is aborted.
Run: python3 tests/social-test.py; NG_SOCIAL=0 python3 tests/social-test.py tests missing migration.
Speech is stubbed: this checks controls/fallbacks, not audible quality or installed OS voices.
"""
import asyncio, importlib.util, json, os, pathlib, shutil
from playwright.async_api import async_playwright
spec=importlib.util.spec_from_file_location('stand',pathlib.Path(__file__).with_name('browser-test.py'))
stand=importlib.util.module_from_spec(spec);spec.loader.exec_module(stand)
FAKE="""(lang)=>{
  window.__voice={calls:[],cancels:0,voices:lang?[{lang,name:'test voice',localService:true}]:[],current:null};
  Object.defineProperty(window,'speechSynthesis',{value:{
    getVoices:()=>__voice.voices,addEventListener:()=>{},cancel:()=>{__voice.cancels++},
    speak:u=>{__voice.current=u;__voice.calls.push(u.text)}
  },configurable:true});
  window.SpeechSynthesisUtterance=class{constructor(text){this.text=text}};
}"""
async def main():
    tmp,db,web=stand.stand();errors=[];blocked=[];passed=0
    def ok(name,condition):
        nonlocal passed
        assert condition,name
        passed+=1;print('PASS',name,flush=True)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            async def context(lang='ru-RU'):
                c=await browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                async def guard(route):
                    if route.request.url.startswith((stand.SITE,f'http://127.0.0.1:{stand.DB_PORT}/')):await route.continue_()
                    else:blocked.append(route.request.url);await route.abort()
                await c.route('**/*',guard)
                await c.add_init_script('('+FAKE+')('+json.dumps(lang)+')')
                return c
            async def page(c,file='index.html'):
                pg=await c.new_page();pg.on('pageerror',lambda e:errors.append(str(e)))
                await pg.goto(stand.SITE+file);await pg.wait_for_selector('#nick');return pg
            a=await page(await context());await stand.plant(a,'Тест: Яблоня')
            ok('language links stay inside the isolated preview',await a.locator('.langs a').evaluate_all('(links)=>links.every(a=>new URL(a.href).origin===location.origin)'))
            await a.locator('a[href="#common"]').click()
            if os.environ.get('NG_SOCIAL')=='0':
                await a.wait_for_function("document.querySelector('#main').innerText.includes('пока недоступен')")
                ok('missing migration fails closed with no trees or consent button',await a.locator('.trees .tcard').count()==0 and await a.locator('[data-act=gardenshare]').count()==0)
            else:
                await a.wait_for_selector('[data-act=gardenshare]')
                ok('new tree is private without opt-in',stand.rpc('ng_public',{})==[] and await a.locator('.trees .tcard').count()==0)
                await a.locator('[data-act=gardenshare]').click();await a.wait_for_selector('.trees .tcard')
                ok('explicit choice publishes only the selected tree',len(stand.rpc('ng_public',{}))==1 and 'Тест: Яблоня' in await a.locator('.trees').inner_text())
                b=await page(await context('en-GB'),'en.html');await stand.plant(b,'Test: Cedar')
                await b.locator('a[href="#common"]').click();await b.wait_for_selector('[data-act=gardenshare]')
                ok('another player sees the shared tree but their own stays private',await b.locator('.trees .tcard').count()==1 and len(stand.rpc('ng_public',{}))==1)
                await a.locator('[data-act=gardenshare]').click();await a.wait_for_function("document.querySelectorAll('.trees .tcard').length===0")
                ok('withdrawal removes the tree from the public RPC',stand.rpc('ng_public',{})==[])
                await a.reload();await a.wait_for_selector('[data-act=gardenshare]')
                ok('withdrawal survives reload and ordinary saving',stand.rpc('ng_public',{})==[])
            if os.environ.get('NG_SOCIAL')!='0':
                await a.locator('[data-act=gardenshare]').click();await a.wait_for_selector('.trees .tcard')
                await a.locator('a[href="#garden"]').click();await a.locator('[data-act=addname]').click()
                await a.locator('#nick').fill('Тест: Второе имя');await a.locator('[data-act=plant]').click()
                await a.locator('a[href="#common"]').click();await a.wait_for_selector('[data-act=gardenshare]')
                ok('same-browser second profile does not inherit consent', 'Показывать' in await a.locator('[data-act=gardenshare]').inner_text() and len(stand.rpc('ng_public',{}))==1)
                await a.locator('[data-act=gardenshare]').click();await a.wait_for_function("document.querySelectorAll('.trees .tcard').length===2")
                await a.locator('a[href="#garden"]').click();await a.get_by_role('button',name='Тест: Яблоня',exact=True).click()
                await a.locator('a[href="#common"]').click();await a.wait_for_selector('[data-act=gardenshare]')
                await a.locator('[data-act=gardenshare]').click();await a.wait_for_function("document.querySelectorAll('.trees .tcard').length===1")
                ok('withdrawing first profile keeps second profile choice', [x['nick'] for x in stand.rpc('ng_public',{})]==['Тест: Второе имя'])
            ok('no speech on page load or navigation',await a.evaluate('__voice.calls.length')==0)
            await a.locator('[data-act=gardensay]').click()
            ok('speech reads exactly the visible encouragement',await a.evaluate('__voice.calls[0]')==await a.locator('#garden-encouragement').inner_text())
            await a.locator('[data-act=gardenstop]').click()
            ok('stop cancels speech and resets controls',await a.evaluate('__voice.cancels')>=1 and await a.locator('[data-act=gardenstop]').is_disabled())
            await a.locator('[data-act=gardensay]').click();await a.evaluate('window.oldVoice=__voice.current')
            await a.locator('[data-act=gardenstop]').click();await a.locator('[data-act=gardensay]').click();await a.evaluate('oldVoice.onend()')
            ok('late completion of old speech does not stop new playback',not await a.locator('[data-act=gardenstop]').is_disabled())
            await a.locator('[data-in=voiceOff]').check()
            ok('disable stops and prevents speech',await a.locator('[data-act=gardensay]').is_disabled() and await a.locator('[data-act=gardenstop]').is_disabled())
            await a.reload();await a.wait_for_selector('[data-in=voiceOff]')
            ok('voice preference persists without autoplay',await a.locator('[data-in=voiceOff]').is_checked() and await a.evaluate('__voice.calls.length')==0)
            await a.locator('[data-in=voiceOff]').uncheck();await a.locator('[data-act=gardensay]').click();await a.evaluate('__voice.current.onerror()')
            ok('speech error leaves readable text and a fallback', 'Не удалось' in await a.locator('#garden-voice-status').inner_text() and bool(await a.locator('#garden-encouragement').inner_text()))
            await a.locator('[data-act=gardensay]').click();await a.locator('a[href="#games"]').click();await a.get_by_role('heading',name='Все упражнения',exact=True).wait_for()
            ok('leaving shared garden stops speech',await a.evaluate('socialUtterance===null'))
            for file,lang,heading in [('en.html','en-GB','A little encouragement'),('he.html','he-IL','קצת עידוד')]:
                pg=await page(await context(lang),file);await pg.locator('a[href="#common"]').click();await pg.get_by_role('heading',name=heading).wait_for()
                ok(file+' localized text and correct direction',await pg.evaluate("document.documentElement.dir") == ('rtl' if lang=='he-IL' else '') and not any('\u0400'<=x<='\u04ff' for x in await pg.locator('#garden-encouragement').inner_text()))
                ok(file+' no unsolicited audio',await pg.evaluate('__voice.calls.length')==0)
            missing=await page(await context('en-GB'),'he.html');await missing.locator('a[href="#common"]').click();await missing.wait_for_selector('#garden-voice-status')
            ok('no matching language voice disables play and keeps text',await missing.locator('[data-act=gardensay]').is_disabled() and bool(await missing.locator('#garden-encouragement').inner_text()))
            legacy=await context();await legacy.add_init_script('''
              window.__legacyWrites=[];
              window.claude={use:async n=>n==='db'?{
                doc:path=>({get:async()=>({exists:true,data:()=>({profiles:{p1:{nick:'Legacy test',hist:[]}},active:'p1'})}),
                  set:async()=>{__legacyWrites.push(path)},delete:async()=>{__legacyWrites.push(path)},onSnapshot:()=>{}}),
                collection:()=>({onSnapshot:()=>{}})
              }:n==='user'?{id:async()=> 'legacy-test',isOwner:async()=>false}:null};
            ''')
            lp=await legacy.new_page();lp.on('pageerror',lambda e:errors.append(str(e)))
            await lp.goto(stand.SITE);await lp.wait_for_function('ready===true')
            await lp.locator('a[href="#common"]').click();await lp.wait_for_selector('#garden-voice-status')
            ok('legacy Claude profiles are not auto-published',await lp.evaluate("__legacyWrites.every(x=>!x.startsWith('trees/'))") and await lp.locator('[data-act=gardenshare]').count()==0)
            remote=await context();await remote.add_init_script('__voice.voices.forEach(v=>v.localService=false)')
            rp=await page(remote);await rp.locator('a[href="#common"]').click();await rp.wait_for_selector('#garden-voice-status')
            ok('remote-only voices are not selected',await rp.locator('[data-act=gardensay]').is_disabled())
            noapi=await context();await noapi.add_init_script("Object.defineProperty(window,'speechSynthesis',{value:undefined})")
            pg=await page(noapi);await pg.locator('a[href="#common"]').click();await pg.wait_for_selector('#garden-voice-status')
            ok('missing speech API is a text-only fallback',await pg.locator('[data-act=gardensay]').is_disabled())
            blocked_result=await pg.evaluate("fetch('https://production-supabase.invalid/rest/v1/ng_players').then(()=>false,()=>true)")
            ok('even an accidental production URL is aborted by the test guard',blocked_result and any('production-supabase.invalid' in x for x in blocked))
            ok('no uncaught JavaScript errors',not errors)
            await browser.close()
    finally:
        db.kill();web.kill();shutil.rmtree(tmp,ignore_errors=True)
    print(f'{passed} passed; production traffic blocked; all data synthetic/local')
if __name__=='__main__':asyncio.run(main())
