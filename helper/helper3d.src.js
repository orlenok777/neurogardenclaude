// НейроСад: 3D-лицо помощницы. Исходник; собранный файл helper3d.js лежит рядом (сборка описана в helper/README.md).
// Модуль только рисует голову и двигает её губы по командам страницы. Он ничего не слушает, не записывает и не отправляет.
import { WebGLRenderer, Scene, PerspectiveCamera, HemisphereLight, DirectionalLight, SRGBColorSpace, Vector3 } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';

const SHAPES = ['viseme_aa', 'viseme_E', 'viseme_I', 'viseme_O', 'viseme_U', 'viseme_PP', 'viseme_FF', 'viseme_SS', 'viseme_DD', 'viseme_kk', 'viseme_nn', 'viseme_RR', 'viseme_CH', 'jawOpen'];

export async function mount(canvas, url) {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.outputColorSpace = SRGBColorSpace;
  const scene = new Scene(), camera = new PerspectiveCamera(20, 1, 0.05, 10);
  scene.add(new HemisphereLight(0xfff6e8, 0x7a8f6a, 2.1));
  const key = new DirectionalLight(0xfff1dd, 2.2); key.position.set(0.6, 0.9, 1.6); scene.add(key);
  const gltf = await new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).loadAsync(url);
  const model = gltf.scene; scene.add(model);
  const morphs = [], bones = {};
  model.traverse(n => {
    if (n.isMesh) { n.frustumCulled = false; if (n.morphTargetDictionary) morphs.push(n) }
    if (n.isBone) bones[n.name] = n;
  });
  model.updateMatrixWorld(true);
  const head = new Vector3(); (bones.Head || model).getWorldPosition(head);
  const look = head.clone().add(new Vector3(0, 0.075, 0));
  camera.position.copy(look).add(new Vector3(0, 0.0, 1.05)); camera.lookAt(look);
  const baseHead = bones.Head ? bones.Head.rotation.clone() : null;

  const want = {}, have = {}; let alive = true, running = false, still = false, raf = 0, last = 0, w = 0, h = 0;
  let blinkAt = performance.now() + 2500, blink = 0;
  const put = (name, v) => { for (const m of morphs) { const i = m.morphTargetDictionary[name]; if (i !== undefined) m.morphTargetInfluences[i] = v } };
  const size = () => {
    const cw = canvas.clientWidth | 0, ch = canvas.clientHeight | 0;
    if (!cw || !ch || (cw === w && ch === h)) return;
    w = cw; h = ch; renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  };
  const frame = t => {
    size();
    const k = still ? 1 : 0.35;
    for (const s of SHAPES) { const a = have[s] || 0, b = want[s] || 0; if (a !== b) { have[s] = Math.abs(b - a) < 0.01 ? b : a + (b - a) * k; put(s, have[s]) } }
    if (!still) {
      if (t > blinkAt) { blink = 1; blinkAt = t + 2600 + Math.random() * 3200 }
      if (blink > 0) { blink = Math.max(0, blink - 0.22); const v = blink > 0.5 ? (1 - blink) * 2 : blink * 2; put('eyeBlinkLeft', v); put('eyeBlinkRight', v) }
      if (baseHead) { bones.Head.rotation.y = baseHead.y + Math.sin(t / 2300) * 0.035; bones.Head.rotation.x = baseHead.x + Math.sin(t / 3100) * 0.012 }
    }
    put('mouthSmileLeft', 0.22); put('mouthSmileRight', 0.22);
    renderer.render(scene, camera);
  };
  const loop = t => { if (!alive || !running) return; raf = requestAnimationFrame(loop); if (t - last < 30) return; last = t; frame(t) };
  const api = {
    /** shape: one of SHAPES or null (mouth closed); amount 0..1 */
    set(shape, amount = 1) { for (const s of SHAPES) want[s] = 0; if (shape && SHAPES.includes(shape)) { want[shape] = amount; if (shape !== 'jawOpen' && shape !== 'viseme_PP') want.jawOpen = 0.08 * amount } if (still) frame(performance.now()) },
    mouth: () => Math.max(0, ...SHAPES.map(s => have[s] || 0)),
    start() { if (!running && !still && alive) { running = true; raf = requestAnimationFrame(loop) } frame(performance.now()) },
    stop() { running = false; cancelAnimationFrame(raf) },
    still(v) { still = !!v; if (still) { api.stop(); frame(performance.now()) } },
    draw() { frame(performance.now()) },
    dispose() { alive = false; api.stop(); renderer.dispose() },
  };
  frame(performance.now());
  return api;
}
