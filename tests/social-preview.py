"""Manual preview on loopback only, with two explicitly synthetic opted-in trees.
No production connection/configuration. Ctrl+C stops and removes the in-memory test stand.
"""
import importlib.util, pathlib, shutil, signal
spec=importlib.util.spec_from_file_location('stand',pathlib.Path(__file__).with_name('browser-test.py'))
stand=importlib.util.module_from_spec(spec);spec.loader.exec_module(stand)
tmp,db,web=stand.stand()
try:
    for i,name in enumerate(['Тест: Яблоня','Test: Cedar']):
        key=('local-preview-'+str(i))*4
        stand.rpc('ng_save',{'p_key':key,'p_pid':'p1','p_state':{'nick':name,'style':i,'apples':1,'hist':[{'g':'seq','at':1000,'l':0,'d':5,'s':100,'ok':True}]},'p_rev':None})
        stand.rpc('ng_garden_share',{'p_key':key,'p_pid':'p1','p_shared':True})
    print('Synthetic trees only. Production is not used.',flush=True)
    for file in ['index.html','en.html','he.html']:print(stand.SITE+file+'#common',flush=True)
    print('Ctrl+C stops the local stand.',flush=True)
    signal.pause()
except KeyboardInterrupt:pass
finally:
    db.terminate();web.terminate();db.wait(timeout=5);web.wait(timeout=5);shutil.rmtree(tmp,ignore_errors=True)
