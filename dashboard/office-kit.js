/* The office's 3D toolkit (spec Sec 15.4): unit shapes, the batcher that
   turns thousands of static parts into a handful of instanced draws, the
   material cache for the live parts, and canvas textures. Shared by
   office-scenery.js, office-decor.js and office-people.js.

   A static part is `(shape, colour, size, position)` in the local frame of
   the piece it belongs to. Colours are palette roles ("wood") or literal
   "#rrggbb"; the batcher resolves roles against the theme, so a theme swap
   is a rebuild with a different palette and nothing else. */
import * as THREE from 'three';

/* Module-level resources that outlive any one floor: `dispose` skips them.
   three's own Sprite quad is one geometry shared by every sprite, so it
   belongs here too, or a scenery rebuild would free the people's glyphs. */
const SHARED = new WeakSet([new THREE.Sprite().geometry]);
export function shared(resource) { SHARED.add(resource); return resource; }

/* Unit shapes. All but the balls stand on y = 0 and are centred on x and z,
   so a part's position is the middle of its base. */
export const SHAPES = {
  box: new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0),
  cyl: new THREE.CylinderGeometry(0.5, 0.5, 1, 14).translate(0, 0.5, 0),
  cone: new THREE.ConeGeometry(0.5, 1, 7).translate(0, 0.5, 0),
  ball: new THREE.IcosahedronGeometry(0.5, 1),
  bush: new THREE.IcosahedronGeometry(0.5, 0),
  ring: new THREE.TorusGeometry(0.5, 0.08, 6, 18)
};
Object.values(SHAPES).forEach(shared);

/* How a batch layer is drawn: solid parts cast and catch the frozen
   shadows; glass is see-through; glow ignores the light (LEDs, screens). */
const LAYERS = {
  solid: () => new THREE.MeshLambertMaterial({ flatShading: true }),
  glass: () => new THREE.MeshLambertMaterial({ transparent: true, opacity: 0.28, depthWrite: false }),
  glow: () => new THREE.MeshBasicMaterial()
};

const scratch = { m: new THREE.Matrix4(), l: new THREE.Matrix4(), q: new THREE.Quaternion(), e: new THREE.Euler(),
                  v: new THREE.Vector3(), s: new THREE.Vector3(), c: new THREE.Color() };

/* Collects parts, then builds one InstancedMesh per (layer, shape). */
export class Batch {
  constructor(palette) {
    this.palette = palette;
    this.groups = new Map();
  }

  color(c) { return scratch.c.set(this.palette[c] || c); }

  /* A placer for one piece standing at tile (x, y), turned `rot` quarter
     turns (0: its front faces +y, toward the front of the floor; 1: +x).
     The placer adds parts in the piece's own frame: x across, y up, z
     toward its front; `placer.matrix` maps that frame to the world. */
  at(x, y, rot, lift) {
    const piece = new THREE.Matrix4().compose(
      new THREE.Vector3(x, lift || 0, y),
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), (rot || 0) * Math.PI / 2),
      new THREE.Vector3(1, 1, 1));
    const placer = (shape, color, size, pos, opts) => this.put(piece, shape, color, size, pos, opts || {});
    placer.matrix = piece;
    return placer;
  }

  put(piece, shape, color, size, pos, opts) {
    scratch.e.set(opts.rx || 0, opts.ry || 0, opts.rz || 0);
    scratch.q.setFromEuler(scratch.e);
    scratch.l.compose(scratch.v.set(pos[0], pos[1], pos[2]), scratch.q, scratch.s.set(size[0], size[1], size[2]));
    const key = (opts.layer || 'solid') + '|' + shape;
    let g = this.groups.get(key);
    if (!g) { g = { layer: opts.layer || 'solid', shape: shape, matrices: [], colors: [] }; this.groups.set(key, g); }
    g.matrices.push(new THREE.Matrix4().multiplyMatrices(piece, scratch.l));
    g.colors.push(this.color(color).clone());
  }

  build() {
    const root = new THREE.Group();
    for (const g of this.groups.values()) {
      const mesh = new THREE.InstancedMesh(SHAPES[g.shape], LAYERS[g.layer](), g.matrices.length);
      g.matrices.forEach((m, i) => { mesh.setMatrixAt(i, m); mesh.setColorAt(i, g.colors[i]); });
      mesh.castShadow = g.layer === 'solid';
      mesh.receiveShadow = g.layer !== 'glow';
      mesh.computeBoundingSphere();
      mesh.matrixAutoUpdate = false;
      mesh.userData.layer = g.layer;
      root.add(mesh);
    }
    return root;
  }
}

/* One Lambert material per colour for the live parts (figures, desk
   screens): few programs, few uniform switches. */
const materials = new Map();
export function mat(color) {
  const key = String(color);
  if (!materials.has(key)) materials.set(key, shared(new THREE.MeshLambertMaterial({ color: new THREE.Color(color), flatShading: true })));
  return materials.get(key);
}

/* A texture painted on a canvas: signs, the notice board, glyphs, the floor. */
export function canvasTexture(w, h, draw) {
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  draw(canvas.getContext('2d'), w, h);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

/* Free everything under `root`: geometries, materials and their maps,
   except the shared ones. */
export function dispose(root) {
  const free = (r) => { if (r && !SHARED.has(r)) r.dispose(); };
  root.traverse((o) => {
    free(o.geometry);
    if (o.material && !SHARED.has(o.material)) { free(o.material.map); o.material.dispose(); }
    if (o.isInstancedMesh) o.dispose();
  });
}
