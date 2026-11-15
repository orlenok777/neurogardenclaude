// Builds the helper model for NeuroGarden from the CC0 MPFB example avatar (TalkingHead repo, avatars/mpfb.glb).
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { prune, dedup, textureCompress, sparse, meshopt, reorder, quantize } from '@gltf-transform/functions';
import { MeshoptEncoder } from 'meshoptimizer';
import sharp from 'sharp';
const KEEP = new Set(['viseme_aa','viseme_E','viseme_I','viseme_O','viseme_U','viseme_PP','viseme_FF','viseme_SS','viseme_DD','viseme_kk','viseme_nn','viseme_RR','viseme_CH','jawOpen','mouthSmileLeft','mouthSmileRight','eyeBlinkLeft','eyeBlinkRight','browInnerUp']);
const SIZE = { young_lightskinned_female_diffuse2: 1024, female_casualsuit01_diffuse: 512, brown_eye: 256, ponytail01_diffuse: 1024, teeth: 256, tongue01_diffuse: 128 };
await MeshoptEncoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
const doc = await io.read(process.argv[2]);
for (const m of doc.getRoot().listMeshes()) {
  const names = m.getExtras().targetNames || [];
  for (const p of m.listPrimitives()) {
    const ts = p.listTargets();
    ts.forEach((t, i) => { if (!KEEP.has(names[i])) p.removeTarget(t); else { t.setAttribute('NORMAL', null); t.setAttribute('TANGENT', null) } });
  }
  const kept = names.filter(n => KEEP.has(n));
  m.setExtras({ targetNames: kept }); m.setWeights(kept.map(() => 0));
}
for (const mat of doc.getRoot().listMaterials()) mat.setNormalTexture(null);
for (const t of doc.getRoot().listTextures()) {
  const s = SIZE[t.getName()]; if (!s) continue;
  const png = t.getMimeType() === 'image/png';
  const buf = await sharp(t.getImage()).resize(s, s).webp({ quality: 82, alphaQuality: 90 }).toBuffer();
  t.setImage(buf).setMimeType('image/webp');
}
doc.getRoot().getAsset().copyright = 'CC0 1.0. MPFB/MakeHuman example avatar from the TalkingHead project; reduced for NeuroGarden.';
doc.createExtension((await import('@gltf-transform/extensions')).EXTTextureWebP).setRequired(true);
await doc.transform(prune(), dedup(), sparse({ ratio: 0.3 }), reorder({ encoder: MeshoptEncoder }), meshopt({ encoder: MeshoptEncoder, level: 'medium' }));
await io.write(process.argv[3], doc);
