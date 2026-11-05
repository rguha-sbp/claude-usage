/* The people (spec Sec 15.5): one jointed figure for leads and interns
   alike, posed from the simulation's pose names.

   A figure is a hierarchy of joints, all rotations about the joint:

     root (feet, heading)
       hips -- spine -- neck -- head (face, hair, glasses, tier headgear)
            |        `- shoulder L/R -- elbow -- hand (cup, tablet, folder)
            `- hip L/R -- knee -- shoe

   A pose is a set of joint angles (`NEUTRAL` lists them all), computed
   each frame by a function of the clock, so typing hands and walking legs
   are part of the pose itself. A change of pose crossfades from the angles
   the figure had to the new pose over BLEND seconds.

   Sign conventions, the figure facing +z: an arm or leg swings forward
   with a negative angle; an elbow bends forward negative, a knee bends
   back positive; the spine and the head lean forward positive.

   Looks (skin, hair, clothes, glasses, beard) follow from the agent's id,
   so a session looks the same on every visit and in every theme. */
import * as THREE from 'three';
import { SHAPES, mat, canvasTexture } from './office-kit.js';
import { GOLD } from './office-decor.js';

const BLEND = 0.35;
const SKIN = ['#f3cfb3', '#e5b48f', '#c98e66', '#a86f4c', '#7c4f35', '#5b3826'];
const HAIR = ['#2a1d15', '#5b3a22', '#8a5a2c', '#caa25c', '#151515', '#9b9b9b', '#a5432b'];
const SHIRT = ['#4f7cac', '#d9675b', '#5aa36f', '#e0b24f', '#8a6fb8', '#3c9a9a', '#e08a4f', '#6b7f99', '#c45f8a'];
const TROUSERS = ['#2f3640', '#3d4a5c', '#5b4636', '#4a4f45', '#6e6a62'];
const SHOES = ['#22252a', '#5a3a26', '#ecebe6'];
const COAT = '#3b3f46', MUSE_COAT = '#5a3f78';

// lengths, in tiles (a tile is about a metre); heads a little large, for legibility
const LEG = { thigh: 0.38, shin: 0.36, foot: 0.07 };
const STAND = LEG.thigh + LEG.shin + LEG.foot;
const ARM = { upper: 0.27, fore: 0.25 };
const SPINE = 0.5, HEAD = 0.15;

// ----------------------------------------------------------------- shapes
const GEO = {
  limb: new THREE.CapsuleGeometry(0.5, 1, 2, 8).translate(0, -0.5, 0),
  chest: new THREE.CylinderGeometry(0.5, 0.4, 1, 10).translate(0, 0.5, 0),
  head: new THREE.IcosahedronGeometry(1, 2),
  shoe: new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0.25),
  ring: new THREE.TorusGeometry(1, 0.1, 6, 24).rotateX(Math.PI / 2),
  half: new THREE.TorusGeometry(1, 0.12, 6, 16, Math.PI),
  disc: new THREE.CircleGeometry(1, 24).rotateX(-Math.PI / 2),
  proxy: new THREE.BoxGeometry(0.55, 1.75, 0.55).translate(0, 0.875, 0)
};
const hidden = new THREE.MeshBasicMaterial({ visible: false });

function mesh(geo, color, size, pos, rot) {
  const m = new THREE.Mesh(geo, mat(color));
  if (size) m.scale.set(size[0], size[1], size[2]);
  if (pos) m.position.set(pos[0], pos[1], pos[2]);
  if (rot) m.rotation.set(rot[0], rot[1], rot[2]);
  return m;
}
function joint(parent, x, y, z) {
  const j = new THREE.Group();
  j.position.set(x, y, z);
  parent.add(j);
  return j;
}

/* Sprites drawn on a canvas: shared by every figure. ASCII only. */
function spriteMaterial(draw, size) {
  return new THREE.SpriteMaterial({ map: canvasTexture(size || 64, size || 64, draw), transparent: true, depthWrite: false });
}
const ZZZ = spriteMaterial((g, w, h) => {
  g.font = '700 52px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.lineWidth = 8; g.strokeStyle = '#ffffff'; g.strokeText('z', w / 2, h / 2);
  g.fillStyle = '#3b4a6b'; g.fillText('z', w / 2, h / 2);
});
const BUBBLE = spriteMaterial((g, w) => {
  g.fillStyle = '#ffffff'; g.strokeStyle = '#9aa3ad'; g.lineWidth = 3;
  g.beginPath(); g.roundRect(4, 10, w - 8, 36, 18); g.moveTo(16, 44); g.lineTo(12, 58); g.lineTo(28, 44); g.fill(); g.stroke();
  g.fillStyle = '#4a5560';
  [w / 2 - 14, w / 2, w / 2 + 14].forEach((x) => { g.beginPath(); g.arc(x, 28, 4.5, 0, Math.PI * 2); g.fill(); });
});
const ROUND = (g, w, h, path) => {
  g.fillStyle = '#ffffff'; g.strokeStyle = '#47515c'; g.lineWidth = 4;
  g.beginPath(); g.arc(w / 2, h / 2, w / 2 - 4, 0, Math.PI * 2); g.fill(); g.stroke();
  g.lineWidth = 4; g.lineCap = 'round'; g.lineJoin = 'round';
  g.beginPath(); path(g); g.stroke();
};
/* One glyph per tool family (office-cast.js familyOf), floating over the head. */
const GLYPHS = {
  read: spriteMaterial((g, w, h) => ROUND(g, w, h, (p) => { p.rect(20, 16, 24, 32); p.moveTo(25, 26); p.lineTo(39, 26); p.moveTo(25, 33); p.lineTo(39, 33); p.moveTo(25, 40); p.lineTo(34, 40); })),
  edit: spriteMaterial((g, w, h) => ROUND(g, w, h, (p) => { p.moveTo(20, 44); p.lineTo(40, 22); p.lineTo(44, 26); p.lineTo(24, 48); p.closePath(); })),
  shell: spriteMaterial((g, w, h) => ROUND(g, w, h, (p) => { p.moveTo(20, 24); p.lineTo(30, 32); p.lineTo(20, 40); p.moveTo(33, 42); p.lineTo(44, 42); })),
  web: spriteMaterial((g, w, h) => ROUND(g, w, h, (p) => { p.arc(32, 32, 13, 0, Math.PI * 2); p.moveTo(19, 32); p.lineTo(45, 32); p.ellipse(32, 32, 6, 13, 0, 0, Math.PI * 2); })),
  delegate: spriteMaterial((g, w, h) => ROUND(g, w, h, (p) => { p.arc(27, 28, 7, 0, Math.PI * 2); p.moveTo(43, 40); p.arc(39, 40, 4, 0, Math.PI * 2); p.moveTo(32, 33); p.lineTo(36, 37); })),
  mcp: spriteMaterial((g, w, h) => ROUND(g, w, h, (p) => { p.moveTo(26, 18); p.lineTo(26, 26); p.moveTo(38, 18); p.lineTo(38, 26); p.rect(21, 26, 22, 10); p.moveTo(32, 36); p.lineTo(32, 46); })),
  think: spriteMaterial((g, w, h) => ROUND(g, w, h, (p) => { p.arc(24, 32, 2, 0, Math.PI * 2); p.moveTo(34, 32); p.arc(32, 32, 2, 0, Math.PI * 2); p.moveTo(42, 32); p.arc(40, 32, 2, 0, Math.PI * 2); }))
};
const SHADOW = new THREE.MeshBasicMaterial({
  map: canvasTexture(64, 64, (g, w, h) => {
    const r = g.createRadialGradient(w / 2, h / 2, 4, w / 2, h / 2, w / 2);
    r.addColorStop(0, 'rgba(0,0,0,0.42)'); r.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = r; g.fillRect(0, 0, w, h);
  }),
  transparent: true, depthWrite: false
});
const RING = { linked: new THREE.MeshBasicMaterial({ color: '#2f7cf6', transparent: true, opacity: 0.85, depthWrite: false }),
               lead: new THREE.MeshBasicMaterial({ color: GOLD, transparent: true, opacity: 0.45, depthWrite: false }),
               muse: new THREE.MeshBasicMaterial({ color: '#b07ad6', transparent: true, opacity: 0.4, depthWrite: false }) };

// ------------------------------------------------------------------ poses
const NEUTRAL = {
  hipY: STAND, hipZ: 0, lean: 0, twist: 0, tilt: 0, nod: 0, turn: 0, cock: 0,
  lArm: 0, lOut: 0.08, lEl: -0.12, rArm: 0, rOut: 0.08, rEl: -0.12,
  lLeg: 0, lKnee: 0, rLeg: 0, rKnee: 0, eyes: 1
};
const KEYS = Object.keys(NEUTRAL);

/* Sitting on whatever `s.seat` is: hips on the cushion, thighs level.
   A figure in a seated pose with no chair under it (situate() finds a
   seat only within reach) keeps the upper body and stands on its legs, so
   every seated pose is defined everywhere and the frame loop never throws. */
function seated(s, o) {
  if (!s.seat) return Object.assign({}, o, { hipY: STAND, hipZ: 0, lLeg: 0, lKnee: 0, rLeg: 0, rKnee: 0 });
  return Object.assign({ hipY: s.seat.height + 0.06, hipZ: s.seat.back, lLeg: -1.5, lKnee: 1.45, rLeg: -1.5, rKnee: 1.45 }, o);
}
function walkLegs(s, o) {
  const p = s.phase, swing = Math.sin(p) * 0.55;
  return Object.assign({
    hipY: STAND - 0.02 + Math.abs(Math.cos(p)) * 0.03, lean: 0.08,
    lLeg: -swing, rLeg: swing, lKnee: 0.15 + Math.max(0, Math.sin(p + 1.4)) * 0.8,
    rKnee: 0.15 + Math.max(0, -Math.sin(p + 1.4)) * 0.8,
    lArm: swing * 0.8, rArm: -swing * 0.8, lEl: -0.3, rEl: -0.3
  }, o);
}
/* A cup at the chest, raised to the lips every few seconds. */
function sipping(t, s) {
  const sip = Math.max(0, Math.sin(t * 1.3 + s.seed) - 0.6) / 0.4;
  return { rArm: -0.25 - sip * 0.25, rOut: -0.05, rEl: -1.35 - sip * 1.0, nod: -sip * 0.25 };
}

/* Every pose the simulation names, plus the seated and standing variants
   it implies. `s` is the figure's situation: seat, seed, walk phase,
   talking, tool family. */
const POSES = {
  typing: (t, s) => seated(s, {
    lean: 0.2, nod: 0.12 + Math.sin(t * 0.7 + s.seed) * 0.04, turn: Math.sin(t * 0.31 + s.seed) * 0.08,
    lArm: -0.55, lOut: 0.02, lEl: -1.0 + Math.sin(t * 17 + s.seed) * 0.09,
    rArm: -0.55, rOut: 0.02, rEl: -1.0 + Math.sin(t * 17 + 1.9 + s.seed) * 0.09
  }),
  thinking: (t, s) => seated(s, {
    lean: 0.02, nod: -0.12, turn: 0.25 + Math.sin(t * 0.8) * 0.06, cock: 0.1,
    rArm: -0.4, rOut: -0.32, rEl: -2.35, lArm: -0.55, lEl: -0.95
  }),
  /* Asleep at the desk: slumped back with the head lolling, or head down
     on folded arms. Eyes shut either way, and the z's rise above. */
  dozing: (t, s) => {
    const breath = Math.sin(t * 1.4) * 0.025;
    if (!s.seat) {
      return { lean: 0.14 + breath, nod: 0.62, cock: 0.22, lArm: 0.04, rArm: 0.04, lEl: -0.1, rEl: -0.1,
               lKnee: 0.1, rKnee: 0.1, hipY: STAND - 0.02, tilt: Math.sin(t * 0.6) * 0.03, eyes: 0 };
    }
    if (s.seed % 2) {
      return seated(s, { lean: 0.95 + breath, nod: 0.45, cock: 0.25, lArm: -1.25, lOut: -0.25, lEl: -1.35,
                         rArm: -1.25, rOut: -0.25, rEl: -1.35, eyes: 0 });
    }
    return seated(s, { lean: -0.32 + breath, nod: 0.55, cock: 0.38, lArm: -0.3, lEl: -0.55, rArm: -0.3, rEl: -0.55,
                       lLeg: -1.3, rLeg: -1.3, lKnee: 1.1, rKnee: 1.1, eyes: 0 });
  },
  walking: (t, s) => walkLegs(s, {}),
  waving: (t, s) => walkLegs(s, { rArm: -0.1, rOut: 2.55 + Math.sin(t * 10) * 0.3, rEl: -0.4 }),
  drinking: (t, s) => Object.assign({ lOut: 0.12, lEl: -0.2 }, sipping(t, s)),
  lounging: (t, s) => {
    const talk = s.talking ? { lArm: -0.6 + Math.sin(t * 2.6) * 0.3, lEl: -1.0, lOut: 0.25 } : {};
    if (s.seat) {
      return seated(s, Object.assign({ lean: -0.3, lLeg: -1.35, lKnee: 1.2, lArm: 0.05, lOut: 0.75, lEl: -0.25 },
        sipping(t, s), talk));
    }
    return Object.assign({ tilt: Math.sin(t * 0.5) * 0.04, lKnee: 0.12, lArm: -0.55, lEl: -0.5 }, sipping(t, s), talk);
  },
  chatting: (t, s) => ({
    nod: Math.sin(t * 2.1) * 0.08, cock: Math.sin(t * 0.9) * 0.1, tilt: Math.sin(t * 0.7) * 0.03,
    rArm: -0.45 + (s.talking ? Math.sin(t * 2.7) * 0.25 : 0), rOut: 0.15, rEl: -1.2 + (s.talking ? Math.sin(t * 3.3) * 0.35 : 0),
    lArm: 0.1, lOut: 0.5, lEl: -1.7
  }),
  /* An intern at work beside its lead's desk: tablet in hand, tapping. */
  standing: (t, s) => s.family === 'delegate'
    ? { lArm: -0.4, lEl: -1.2, rArm: -1.35, rOut: 0.1, rEl: -0.15, turn: 0.2 }
    : { nod: 0.3, lArm: -0.35, lOut: -0.1, lEl: -1.3, rArm: -0.38, rOut: -0.18, rEl: -1.3 + Math.sin(t * 12 + s.seed) * 0.12,
        tilt: Math.sin(t * 0.5 + s.seed) * 0.03 },
  handing: () => ({ lean: 0.1, nod: 0.1, rArm: -1.3, rOut: 0.05, rEl: -0.2, lArm: 0.05, lEl: -0.2 }),
  gone: () => ({})
};
/* What each pose holds. */
const PROPS = { drinking: 'cup', lounging: 'cup', standing: 'tablet', handing: 'folder' };
/* The poses a figure takes in a chair when one is under it. */
export const SITTING = { typing: 1, thinking: 1, dozing: 1, lounging: 1 };

function evaluate(name, t, s) { return Object.assign({}, NEUTRAL, POSES[name](t, s)); }

// ------------------------------------------------------------------ figure
/* `spec`: { id, seed, look {hair, glasses, coat}, tier (office-cast.js),
   intern, badge (lanyard colour or null), scale }. */
export class Person {
  constructor(spec) {
    const h = spec.seed;
    const pick = (list, shift) => list[(h >>> shift) % list.length];
    this.spec = spec;
    this.seed = h % 97;
    const skin = pick(SKIN, 2), hair = HAIR[(spec.look.hair * 3 + (h >>> 9)) % HAIR.length];
    const shirt = pick(SHIRT, 5), coat = spec.tier.key === 'muse' ? MUSE_COAT : spec.look.coat ? COAT : null;
    const trousers = pick(TROUSERS, 11), shoes = pick(SHOES, 14);

    const root = this.root = new THREE.Group();
    const body = this.body = joint(root, 0, 0, 0);
    body.scale.setScalar(spec.scale);
    const j = this.j = {};
    j.hips = joint(body, 0, STAND, 0);
    j.hips.add(mesh(GEO.limb, trousers, [0.3, 0.06, 0.2], [0, 0.07, 0]));

    // legs
    [['l', 1], ['r', -1]].forEach(([side, sx]) => {
      const hip = j[side + 'Leg'] = joint(j.hips, sx * 0.09, -0.02, 0);
      hip.add(mesh(GEO.limb, trousers, [0.13, LEG.thigh - 0.1, 0.13], [0, -0.05, 0]));
      const knee = j[side + 'Knee'] = joint(hip, 0, -LEG.thigh, 0);
      knee.add(mesh(GEO.limb, trousers, [0.11, LEG.shin - 0.08, 0.11], [0, -0.04, 0]));
      knee.add(mesh(GEO.shoe, shoes, [0.11, LEG.foot, 0.22], [0, -LEG.shin - LEG.foot, 0]));
    });

    // torso
    const spine = j.spine = joint(j.hips, 0, 0.04, 0);
    spine.add(mesh(GEO.chest, coat || shirt, [0.38, SPINE - 0.06, 0.24], [0, 0, 0]));
    if (coat) spine.add(mesh(SHAPES.box, shirt, [0.1, 0.3, 0.02], [0, SPINE - 0.36, 0.12]));
    if (spec.badge) {
      spine.add(mesh(SHAPES.box, spec.badge, [0.02, 0.22, 0.01], [-0.05, SPINE - 0.28, 0.125], [0, 0, -0.35]));
      spine.add(mesh(SHAPES.box, spec.badge, [0.02, 0.22, 0.01], [0.05, SPINE - 0.28, 0.125], [0, 0, 0.35]));
      spine.add(mesh(SHAPES.box, '#ffffff', [0.08, 0.1, 0.015], [0, SPINE - 0.4, 0.13]));
    }

    // arms
    [['l', 1], ['r', -1]].forEach(([side, sx]) => {
      const sh = j[side + 'Arm'] = joint(spine, sx * 0.21, SPINE - 0.1, 0);
      sh.add(mesh(GEO.limb, coat || shirt, [0.1, ARM.upper - 0.06, 0.1], [0, -0.03, 0]));
      const el = j[side + 'El'] = joint(sh, 0, -ARM.upper, 0);
      el.add(mesh(GEO.limb, coat || shirt, [0.085, ARM.fore - 0.08, 0.085], [0, -0.03, 0]));
      j[side + 'Hand'] = joint(el, 0, -ARM.fore, 0);
      j[side + 'Hand'].add(mesh(SHAPES.ball, skin, [0.085, 0.095, 0.085], [0, 0, 0]));
    });
    this.props = {
      cup: mesh(SHAPES.cyl, '#f4f1ea', [0.07, 0.1, 0.07], [0, -0.02, 0.05]),
      tablet: mesh(SHAPES.box, '#2c3138', [0.2, 0.015, 0.27], [-0.06, 0.02, 0.08], [0, 0, 0.2]),
      folder: mesh(SHAPES.box, spec.badge || '#e0b24f', [0.22, 0.02, 0.3], [0, 0, 0.1], [0.2, 0, 0])
    };
    j.rHand.add(this.props.cup, this.props.folder);
    j.lHand.add(this.props.tablet);

    // neck and head
    j.nod = joint(spine, 0, SPINE, 0);
    j.nod.add(mesh(SHAPES.cyl, skin, [0.09, 0.07, 0.09], [0, 0, 0]));
    const head = j.head = joint(j.nod, 0, 0.06, 0);
    head.add(mesh(GEO.head, skin, [HEAD * 0.92, HEAD, HEAD * 0.98], [0, HEAD, 0]));
    head.add(mesh(SHAPES.ball, skin, [0.05, 0.06, 0.05], [0, HEAD, HEAD * 0.96]));                    // nose
    this.eyes = [-1, 1].map((sx) => mesh(SHAPES.box, '#1e1e22', [0.03, 0.035, 0.012], [sx * 0.05, HEAD * 1.12, HEAD * 0.93]));
    this.eyes.forEach((e) => head.add(e));
    head.add(mesh(SHAPES.box, '#9a5a4a', [0.05, 0.012, 0.01], [0, HEAD * 0.62, HEAD * 0.93]));          // mouth
    hairStyle(head, spec.look.hair, hair);
    if ((h >>> 17) % 5 === 0 && !spec.intern) head.add(mesh(GEO.head, hair, [HEAD * 0.82, HEAD * 0.5, HEAD * 0.7], [0, HEAD * 0.62, HEAD * 0.3]));
    headgear(head, spec.tier.crown, spec.look.glasses);

    // the floor: blob shadow, the tier's ring, the linked ring
    const shadow = new THREE.Mesh(GEO.disc, SHADOW);
    shadow.scale.setScalar(0.32 * spec.scale);
    shadow.position.y = 0.012;
    root.add(shadow);
    this.ring = new THREE.Mesh(GEO.ring, RING.linked);
    this.ring.scale.setScalar(0.38 * spec.scale);
    this.ring.position.y = 0.02;
    this.ring.visible = false;
    root.add(this.ring);
    if (spec.tier.aura) {
      const aura = new THREE.Mesh(GEO.disc, RING[spec.tier.key]);
      aura.scale.setScalar(0.42 * spec.scale);
      aura.position.y = 0.01;
      root.add(aura);
    }

    // over the head
    this.zs = [0, 1, 2].map(() => { const z = new THREE.Sprite(ZZZ); z.visible = false; root.add(z); return z; });
    this.glyph = new THREE.Sprite(GLYPHS.think);
    this.glyph.scale.setScalar(0.3);
    this.bubble = new THREE.Sprite(BUBBLE);
    this.bubble.scale.set(0.42, 0.42, 1);
    root.add(this.glyph, this.bubble);

    this.proxy = new THREE.Mesh(GEO.proxy, hidden);
    this.proxy.scale.setScalar(spec.scale);
    this.proxy.userData.agent = spec.id;
    root.add(this.proxy);

    this.name = null;
    this.from = Object.assign({}, NEUTRAL);
    this.now = Object.assign({}, NEUTRAL);
    this.blend = 1;
    this.phase = 0;
    this.heading = null;
    this.target = null;
    this.speed = 0;
    this.pop = 0;
    this.situation = { seat: null, seed: this.seed, phase: 0, talking: false, family: null };
  }

  /* Where the figure is and what it is doing; `still` skips the
     crossfade (reduced motion). `f`: {pose, x, y, heading {x, y} or null,
     seat {height, back} or null, family, talking, linked, speed, popped}. */
  pose(f, still) {
    this.root.position.set(f.x, 0, f.y);
    const name = f.pose;
    if (name !== this.name) {
      this.from = Object.assign({}, this.now);
      this.blend = still || this.name === null ? 1 : 0;
      this.name = name;
    }
    this.situation = { seat: SITTING[name] ? f.seat : null, seed: this.seed, phase: this.phase, talking: f.talking, family: f.family };
    if (f.popped && !still) this.pop = 0.32;
    if (f.heading) {
      const want = Math.atan2(f.heading.x, f.heading.y);
      if (this.heading === null || still) this.heading = want;
      this.target = want;
    }
    this.speed = f.speed || 0;
    this.root.visible = name !== 'gone';
    this.ring.visible = !!f.linked;
    const prop = PROPS[name];
    Object.keys(this.props).forEach((k) => { this.props[k].visible = k === prop; });
    this.glyph.visible = !!f.family && name !== 'dozing';
    if (this.glyph.visible) this.glyph.material = GLYPHS[f.family];
    this.bubble.visible = !!f.talking;
  }

  /* Advance the clock by dt and set every joint. */
  step(dt, t) {
    this.phase += dt * this.speed * 5.2;
    this.situation.phase = this.phase;
    this.blend = Math.min(1, this.blend + dt / BLEND);
    const w = this.blend * this.blend * (3 - 2 * this.blend);
    const to = evaluate(this.name, t, this.situation);
    KEYS.forEach((k) => { this.now[k] = this.from[k] + (to[k] - this.from[k]) * w; });
    const p = this.now, j = this.j;

    j.hips.position.set(0, p.hipY, p.hipZ);
    j.spine.rotation.set(p.lean, p.twist, p.tilt);
    j.nod.rotation.set(p.nod * 0.4, p.turn * 0.4, p.cock * 0.4);
    j.head.rotation.set(p.nod * 0.6, p.turn * 0.6, p.cock * 0.6);
    j.lArm.rotation.set(p.lArm, 0, p.lOut);
    j.rArm.rotation.set(p.rArm, 0, -p.rOut);
    j.lEl.rotation.x = p.lEl;
    j.rEl.rotation.x = p.rEl;
    j.lLeg.rotation.x = p.lLeg; j.lKnee.rotation.x = p.lKnee;
    j.rLeg.rotation.x = p.rLeg; j.rKnee.rotation.x = p.rKnee;
    this.eyes.forEach((e) => { e.scale.y = 0.006 + 0.029 * p.eyes; });

    if (this.target !== null) {
      let d = this.target - this.heading;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      this.heading += d * Math.min(1, dt * 10);
      this.body.rotation.y = this.heading;
    }
    const s = this.spec.scale;
    const squash = this.pop > 0 ? Math.sin((1 - this.pop / 0.32) * Math.PI * 2) * 0.08 * (this.pop / 0.32) : 0;
    this.pop = Math.max(0, this.pop - dt);
    this.body.scale.set(s * (1 + squash), s * (1 - squash), s * (1 + squash));

    const top = (p.hipY + SPINE + HEAD * 2.4) * s;
    this.glyph.position.set(0, top + 0.22 + Math.sin(t * 2.2 + this.seed) * 0.025, 0);
    this.bubble.position.set(0.22, top + 0.2, 0);
    const asleep = this.name === 'dozing';
    this.zs.forEach((z, i) => {
      z.visible = asleep;
      if (!asleep) return;
      const k = ((t * 0.45 + i / 3) % 1);
      z.position.set(0.12 + k * 0.25, top - 0.1 + k * 0.5, 0.05);
      z.scale.setScalar(0.14 + k * 0.14);
      z.visible = k < 0.92;
    });
  }

  /* The head's world position, for the hover card and the tethers. */
  headAt(out) { return this.j.head.getWorldPosition(out); }
}

/* Five hair styles, by look.hair: short, bob, bun, long, cropped. */
function hairStyle(head, style, color) {
  const at = (s, p) => head.add(mesh(GEO.head, color, s, p));
  const cap = [HEAD * 1.04, HEAD * 0.72, HEAD * 1.06];
  if (style === 0) at(cap, [0, HEAD * 1.32, -0.012]);
  if (style === 1) { at([HEAD * 1.12, HEAD * 0.95, HEAD * 1.1], [0, HEAD * 1.12, -0.03]); at([HEAD * 0.9, HEAD * 0.3, HEAD * 0.5], [0, HEAD * 1.6, 0.06]); }
  if (style === 2) { at(cap, [0, HEAD * 1.32, -0.012]); at([HEAD * 0.45, HEAD * 0.45, HEAD * 0.45], [0, HEAD * 1.85, -HEAD * 0.75]); }
  if (style === 3) { at(cap, [0, HEAD * 1.3, -0.015]); head.add(mesh(SHAPES.box, color, [HEAD * 1.9, HEAD * 1.9, HEAD * 0.5], [0, -HEAD * 0.2, -HEAD * 0.75])); }
  if (style === 4) at([HEAD * 0.98, HEAD * 0.5, HEAD * 1.0], [0, HEAD * 1.5, -0.01]);
}

/* What the model's tier wears on its head (office-cast.js `crown`), and
   the figure's own glasses unless a monocle takes their place. */
function headgear(head, crown, glasses) {
  const eyeY = HEAD * 1.12, eyeZ = HEAD * 0.97;
  if (crown === 'crown') {
    head.add(mesh(SHAPES.cyl, GOLD, [HEAD * 1.3, 0.06, HEAD * 1.3], [0, HEAD * 1.82, 0]));
    for (let i = 0; i < 5; i++) {
      const a = i / 5 * Math.PI * 2;
      head.add(mesh(SHAPES.cone, GOLD, [0.04, 0.07, 0.04], [Math.sin(a) * HEAD * 0.6, HEAD * 1.82 + 0.06, Math.cos(a) * HEAD * 0.6]));
    }
  }
  if (crown === 'headphones') {
    head.add(mesh(GEO.half, '#2a2d33', [HEAD * 1.08, HEAD * 1.08, HEAD * 1.08], [0, HEAD * 1.05, 0]));
    [-1, 1].forEach((sx) => head.add(mesh(SHAPES.cyl, '#2a2d33', [0.07, 0.05, 0.07], [sx * HEAD * 1.0, HEAD * 0.95, 0], [0, 0, Math.PI / 2])));
  }
  if (crown === 'monocle') {
    head.add(mesh(GEO.ring, GOLD, [0.04, 0.04, 0.04], [-0.05, eyeY, eyeZ + 0.01], [Math.PI / 2, 0, 0]));
    head.add(mesh(SHAPES.box, GOLD, [0.006, 0.14, 0.006], [-0.085, eyeY - 0.09, eyeZ - 0.01], [0, 0, 0.3]));
    return;
  }
  if (glasses) {
    [-1, 1].forEach((sx) => head.add(mesh(GEO.ring, '#1e1e22', [0.036, 0.036, 0.036], [sx * 0.05, eyeY, eyeZ + 0.012], [Math.PI / 2, 0, 0])));
    head.add(mesh(SHAPES.box, '#1e1e22', [0.03, 0.006, 0.006], [0, eyeY, eyeZ + 0.015]));
  }
}
