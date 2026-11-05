/* The floor's scenery (spec Sec 15.4): turns an office-floor.js plan and a
   theme into meshes. Everything static goes through one Batch, so the whole
   floor is a few dozen instanced draws whatever its size; the few live
   parts (each desk's monitor glow and lamp, the notice board) are their own
   meshes, switched by `setDesk` and `setBoard` without a rebuild.

   `dress` is what the plan does not know: `night` (lamps throw light
   pools), and per desk the sitter's `tier`, `title` and `extra` (sub-agents
   beyond the pod's four slots). */
import * as THREE from 'three';
import { Batch, canvasTexture, dispose } from './office-kit.js';
import { BUILDERS, buildDesk } from './office-decor.js';

const WALL_T = 0.15;

// ----------------------------------------------------------------- floor
/* Floor patterns, painted in tile units on the floor canvas. */
const PATTERNS = {
  planks(g, W, D, s, pal) {
    g.strokeStyle = pal.floorLine;
    for (let y = 0, row = 0; y < D; y += s, row++) {
      if (row % 2) { g.fillStyle = 'rgba(0,0,0,0.025)'; g.fillRect(0, y, W, s); }
      line(g, 0, y, W, y);
      for (let x = ((row * 7) % 5) * 0.37; x < W; x += 1.85) line(g, x, y, x, y + s);
    }
  },
  concrete(g, W, D, s, pal) {
    const r = seq(7);
    for (let i = 0; i < W * D * 6; i++) {
      g.fillStyle = r() > 0.5 ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.05)';
      g.fillRect(r() * W, r() * D, 0.03 + r() * 0.05, 0.03 + r() * 0.05);
    }
    g.strokeStyle = pal.floorLine;
    for (let x = s; x < W; x += s) line(g, x, 0, x, D);
    for (let y = s; y < D; y += s) line(g, 0, y, W, y);
  },
  terracotta(g, W, D, s, pal) {
    const r = seq(11);
    for (let y = 0; y < D; y += s) {
      for (let x = 0; x < W; x += s) {
        g.fillStyle = r() > 0.5 ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)';
        g.fillRect(x, y, s, s);
      }
    }
    g.strokeStyle = pal.floorLine;
    for (let x = 0; x < W; x += s) line(g, x, 0, x, D);
    for (let y = 0; y < D; y += s) line(g, 0, y, W, y);
  }
};

/* Zones painted over the pattern: walkways, room floors, rugs. */
const ZONES = {
  corridor(g, z, pal) { fill(g, z, pal.zone, 0.7); },
  zone(g, z, pal) { fill(g, z, pal.zone, 0.5); },
  nb(g, z, pal) { fill(g, z, pal.zone, 0.35); },
  carpet(g, z, pal) { fill(g, z, pal.carpet, 1); },
  tiles(g, z, pal) {
    fill(g, z, pal.tiles, 1);
    g.strokeStyle = pal.floorLine;
    for (let x = z.x + 0.5; x < z.x + z.w; x += 0.5) line(g, x, z.y, x, z.y + z.d);
    for (let y = z.y + 0.5; y < z.y + z.d; y += 0.5) line(g, z.x, y, z.x + z.w, y);
  },
  rug(g, z, pal) {
    fill(g, z, pal.accent2, 0.55);
    g.strokeStyle = pal.cushion;
    g.lineWidth = 0.06;
    g.strokeRect(z.x + 0.15, z.y + 0.15, z.w - 0.3, z.d - 0.3);
  }
};

function fill(g, z, color, alpha) {
  g.globalAlpha = alpha;
  g.fillStyle = color;
  g.fillRect(z.x, z.y, z.w, z.d);
  g.globalAlpha = 1;
}
function line(g, x1, y1, x2, y2) { g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke(); }
function seq(seed) { let t = seed; return () => { t = (t * 16807) % 2147483647; return t / 2147483647; }; }

function floorMesh(plan, theme) {
  const ppt = Math.min(48, Math.floor(2048 / Math.max(plan.W, plan.D)));
  const tex = canvasTexture(Math.ceil(plan.W * ppt), Math.ceil(plan.D * ppt), (g) => {
    g.scale(ppt, ppt);
    g.lineWidth = 1 / ppt;
    g.fillStyle = theme.palette.floor;
    g.fillRect(0, 0, plan.W, plan.D);
    PATTERNS[theme.floor.pattern](g, plan.W, plan.D, theme.floor.size, theme.palette);
    plan.zones.forEach((z) => ZONES[z.kind](g, z, theme.palette));
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(plan.W, plan.D).rotateX(-Math.PI / 2),
    new THREE.MeshLambertMaterial({ map: tex }));
  mesh.position.set(plan.W / 2, 0, plan.D / 2);
  mesh.receiveShadow = true;
  return mesh;
}

// ----------------------------------------------------------------- walls
/* An axis-aligned run of wall, `side` the offset of its middle from the
   line it stands on: walls stand outside the floor, partitions on the line. */
function run(part, w, a0, a1, y0, y1, t, side, color, opts) {
  if (a1 - a0 < 0.01 || y1 - y0 < 0.01) return;
  const along = w.x1 === w.x2 ? 'y' : 'x';
  if (along === 'x') part('box', color, [a1 - a0, y1 - y0, t], [(a0 + a1) / 2, y0, w.y1 + side], opts);
  else part('box', color, [t, y1 - y0, a1 - a0], [w.x1 + side, y0, (a0 + a1) / 2], opts);
}

function span(w) { return w.x1 === w.x2 ? [w.y1, w.y2] : [w.x1, w.x2]; }

/* The outer walls: solid between windows, glass in them, a sill and a
   head, mullions every metre or so, a skirting board along the inside. */
function outerWall(part, w) {
  const [a0, a1] = span(w);
  const outside = (w.x1 === w.x2 ? w.x1 : w.y1) === 0 ? -1 : 1;
  const side = outside * WALL_T / 2;
  let at = a0;
  w.windows.forEach((win) => {
    run(part, w, at, win.from, 0, w.h, WALL_T, side, 'wall');
    run(part, w, win.from, win.to, 0, win.low, WALL_T, side, 'wall');
    run(part, w, win.from, win.to, win.high, w.h, WALL_T, side, 'wall');
    run(part, w, win.from, win.to, win.low, win.high, 0.03, side, 'glass', { layer: 'glass' });
    run(part, w, win.from, win.to, win.low - 0.04, win.low, WALL_T + 0.08, side, 'frame');
    run(part, w, win.from, win.to, win.high, win.high + 0.04, WALL_T + 0.02, side, 'frame');
    const n = Math.max(1, Math.round((win.to - win.from) / 1.2));
    for (let i = 1; i < n; i++) {
      const m = win.from + (win.to - win.from) * i / n;
      run(part, w, m - 0.025, m + 0.025, win.low, win.high, WALL_T, side, 'frame');
    }
    at = win.to;
  });
  run(part, w, at, a1, 0, w.h, WALL_T, side, 'wall');
  if (w.h > 1) {
    run(part, w, a0, a1, 0, 0.09, 0.03, -outside * 0.015, 'trim');                    // skirting
    run(part, w, a0, a1, w.h, w.h + 0.05, WALL_T + 0.04, side, 'trim');               // cap
  }
}

/* Partitions: solid and glass walls for the rooms, and the theme's own
   low divider around each neighbourhood. */
const PARTITIONS = {
  solid(part, w) {
    const [a0, a1] = span(w);
    run(part, w, a0, a1, 0, w.h, 0.1, 0, 'wall');
    run(part, w, a0, a1, w.h, w.h + 0.04, 0.14, 0, 'trim');
  },
  glass(part, w) {
    const [a0, a1] = span(w);
    run(part, w, a0, a1, 0.05, w.h - 0.05, 0.03, 0, 'glass', { layer: 'glass' });
    run(part, w, a0, a1, 0, 0.06, 0.07, 0, 'frame');
    run(part, w, a0, a1, w.h - 0.06, w.h, 0.07, 0, 'frame');
    for (let m = a0; m <= a1 + 1e-6; m += Math.max(0.6, (a1 - a0) / Math.max(1, Math.round((a1 - a0) / 1.2)))) {
      run(part, w, Math.min(m, a1 - 0.05), Math.min(m, a1 - 0.05) + 0.05, 0, w.h, 0.07, 0, 'frame');
    }
  },
  planter(part, w) {
    const [a0, a1] = span(w);
    run(part, w, a0, a1, 0, 0.42, 0.22, 0, 'woodDark');
    run(part, w, a0, a1, 0.42, 0.44, 0.2, 0, 'soil');
    hedge(part, w, a0, a1, 0.5, 0.42);
  },
  brick(part, w) {
    const [a0, a1] = span(w);
    for (let y = 0, k = 0; y < w.h - 0.01; y += 0.15, k++) {
      run(part, w, a0, a1, y, y + 0.14, 0.18, 0, k % 2 ? 'wall' : 'trim');
    }
    run(part, w, a0, a1, w.h, w.h + 0.05, 0.24, 0, 'woodDark');
  },
  vines(part, w) {
    const [a0, a1] = span(w);
    run(part, w, a0, a1, 0, 0.55, 0.08, 0, 'wood');
    run(part, w, a0, a1, 0.55, 0.59, 0.12, 0, 'woodDark');
    hedge(part, w, a0, a1, 0.9, 0.5);
  }
};

/* A row of leafy balls along a partition top. */
function hedge(part, w, a0, a1, step, y) {
  for (let a = a0 + 0.25, i = 0; a < a1 - 0.1; a += step * 0.5, i++) {
    const s = 0.26 + ((i * 7) % 3) * 0.05;
    const at = w.x1 === w.x2 ? [w.x1, y, a] : [a, y, w.y1];
    part('bush', i % 2 ? 'leaf2' : 'leaf', [s, s * 0.85, s], at, { ry: i });
  }
}

// ----------------------------------------------------------- live parts
const HALO = canvasTexture(64, 64, (g, w, h) => {
  const r = g.createRadialGradient(w / 2, h / 2, 2, w / 2, h / 2, w / 2);
  r.addColorStop(0, 'rgba(255,255,255,1)');
  r.addColorStop(0.5, 'rgba(255,255,255,0.45)');
  r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r;
  g.fillRect(0, 0, w, h);
});

function glowMaterial(color, opacity) {
  return new THREE.MeshBasicMaterial({ map: HALO, color: new THREE.Color(color), transparent: true, opacity: opacity,
                                       depthWrite: false, blending: THREE.AdditiveBlending });
}

/* Lines of text on a canvas: sized to the words, or to `opts.size` (world
   units) when the panel has a fixed frame. `w` and `h` are world units. */
function textPanel(lines, opts) {
  const px = 64, pad = 18;
  const font = (l) => (l.bold ? '600 ' : '') + (l.size || 1) * 30 + 'px system-ui, sans-serif';
  const probe = document.createElement('canvas').getContext('2d');
  const fit = Math.max(...lines.map((l) => { probe.font = font(l); return probe.measureText(l.text).width; })) + pad * 2;
  const tall = lines.reduce((s, l) => s + (l.size || 1) * 38, 0) + pad;
  const width = opts.size ? opts.size[0] * px / opts.scale : fit;
  const height = opts.size ? opts.size[1] * px / opts.scale : tall;
  const tex = canvasTexture(Math.ceil(width), Math.ceil(height), (g, w, h) => {
    g.fillStyle = opts.bg;
    g.beginPath(); g.roundRect(0, 0, w, h, 14); g.fill();
    let y = (h - tall) / 2 + pad / 2;
    lines.forEach((l) => {
      g.font = font(l);
      g.fillStyle = l.color || opts.ink;
      g.textAlign = 'center'; g.textBaseline = 'top';
      g.fillText(l.text, w / 2, y + 4);
      y += (l.size || 1) * 38;
    });
  });
  return { tex: tex, w: width / px * opts.scale, h: height / px * opts.scale };
}

function truncate(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 3) + '...' : s; }

function sprite(panel, x, y, z) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: panel.tex, transparent: true }));
  s.center.set(0.5, 0);
  s.scale.set(panel.w, panel.h, 1);
  s.position.set(x, y, z);
  return s;
}

/* A flat panel on a piece of decor, `y` up and `z` out in its own frame. */
function hang(item, w, h, y, z, material) {
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(w, h).translate(0, y, z), material);
  plane.position.set(item.x, 0, item.y);
  plane.rotation.y = item.rot * Math.PI / 2;
  return plane;
}
function panelMaterial(panel) { return new THREE.MeshBasicMaterial({ map: panel.tex, transparent: true }); }

// ----------------------------------------------------------------- build
export function buildScenery(plan, theme, dress) {
  const pal = theme.palette;
  const batch = new Batch(pal);
  const root = new THREE.Group();
  const pools = [];
  const bulbOf = (place) => (x, y, z, r, opts) => {
    const o = opts || {};
    place('ball', o.color || 'bulb', [r * 2, r * 2, r * 2], [x, y, z], { layer: 'glow' });
    if (o.pool) pools.push(new THREE.Vector3(x, y, z).applyMatrix4(place.matrix));
  };

  root.add(floorMesh(plan, theme));
  const world = batch.at(0, 0, 0);
  plan.walls.forEach((w) => outerWall(world, w));
  plan.partitions.forEach((w) => PARTITIONS[w.style === 'low' ? theme.partition : w.style](world, w));

  let board = null;
  plan.decor.forEach((item) => {
    const place = batch.at(item.x, item.y, item.rot);
    BUILDERS[item.kind](place, item, bulbOf(place));
    if (item.kind === 'board') board = item;
    if (item.kind === 'sign') {
      const panel = textPanel([{ text: truncate(item.text, 22), bold: true }], { bg: pal.paper, ink: pal.dark, scale: 0.36 });
      root.add(hang(item, panel.w, panel.h, 1.0, 0.04, panelMaterial(panel)));
    }
  });

  const live = new Map();
  const on = { halo: glowMaterial(pal.screen, 0.85), bulb: new THREE.MeshBasicMaterial({ color: pal.bulb }) };
  const off = new THREE.MeshLambertMaterial({ color: pal.dark });
  plan.desks.forEach((desk) => {
    const d = dress.desks[desk.sid] || {};
    const cx = desk.x + desk.w / 2, cz = desk.y + desk.d / 2;
    const ornament = d.tier && d.tier.ornament;
    buildDesk(batch.at(cx, cz, 0), ornament ? desk.props.concat([ornament]) : desk.props, desk.w, desk.d);
    BUILDERS.chair(batch.at(desk.seat.x, desk.seat.y, 1), { gold: d.tier && d.tier.chair === 'gold' });

    const halo = new THREE.Mesh(new THREE.PlaneGeometry(1.0, 0.7).rotateY(Math.PI / 2), on.halo);
    halo.position.set(cx + 0.25, 1.05, cz);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.035, 8, 6), off);
    bulb.position.set(cx + 0.2, 1.06, cz - 0.4);
    root.add(halo, bulb);
    live.set(desk.sid, { halo: halo, bulb: bulb });

    root.add(sprite(textPanel([{ text: truncate(d.title, 26), bold: true }], { bg: 'rgba(255,255,255,0.88)', ink: '#2a2e33', scale: 0.3 }),
      desk.seat.x + 0.45, 0.02, desk.y + desk.d + 0.25));
    if (d.extra > 0) {
      const pod = plan.places[desk.sid].pod;
      root.add(sprite(textPanel([{ text: '+' + d.extra, bold: true }], { bg: pal.accent, ink: '#ffffff', scale: 0.3 }),
        pod[pod.length - 1].x + 0.3, 1.3, pod[pod.length - 1].y));
    }
  });

  root.add(batch.build());
  if (dress.night && pools.length) root.add(poolMesh(pools, pal.bulb, 0.35 + 0.3 * theme.light.warm));

  const boardMaterial = new THREE.MeshBasicMaterial({ color: pal.dark });
  const boardSize = board ? [board.w - 0.06, 0.84] : null;
  if (board) root.add(hang(board, boardSize[0], boardSize[1], board.h, 0.045, boardMaterial));

  return {
    root: root,
    setDesk(sid, working) {
      const parts = live.get(sid);
      if (!parts) return;
      parts.halo.visible = working;
      parts.bulb.material = working ? on.bulb : off;
    },
    /* The notice board: the day's numbers, one line each. */
    setBoard(lines) {
      if (!board) return;
      if (boardMaterial.map) boardMaterial.map.dispose();
      boardMaterial.map = textPanel(lines, { bg: pal.dark, ink: '#ffffff', scale: 0.32, size: boardSize }).tex;
      boardMaterial.color.set('#ffffff');
      boardMaterial.needsUpdate = true;
    },
    dispose() {
      dispose(root);
      [on.halo, on.bulb, off, boardMaterial].forEach((m) => { if (m.map && m.map !== HALO) m.map.dispose(); m.dispose(); });
    }
  };
}

/* Pools of lamplight on the floor at night: one instanced draw. */
function poolMesh(points, color, opacity) {
  const mesh = new THREE.InstancedMesh(new THREE.PlaneGeometry(2.4, 2.4).rotateX(-Math.PI / 2), glowMaterial(color, opacity), points.length);
  const m = new THREE.Matrix4();
  points.forEach((p, i) => mesh.setMatrixAt(i, m.makeTranslation(p.x, 0.015, p.z)));
  mesh.computeBoundingSphere();
  return mesh;
}
