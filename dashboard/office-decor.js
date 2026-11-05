/* Furniture and decor builders (spec Sec 15.4): one function per kind in
   office-floor.js's KINDS, plus the desk. A builder only adds parts through
   `part(shape, colour, [w, h, d], [x, y, z], opts)` in the piece's own
   frame (x across, y up, z toward its front, footprint centred on the
   origin; wall pieces start at the wall, z = 0) and registers its light
   bulbs through `bulb(x, y, z, r, {color, pool})`: bulbs always glow, and
   at night a `pool` bulb also throws a pool of light on the floor below it
   (office-scenery.js). Colours are palette roles, so every builder works in every theme.

   Kept boring on purpose: boxes, cylinders and low-poly balls, a handful
   of parts each, no geometry of their own. */

/* Gold is not a theme colour: it marks the richest model tiers everywhere. */
export const GOLD = '#d4a53c';
const BOOKS = ['book1', 'book2', 'book3', 'paper', 'book2', 'book1'];

/* A row of book spines from x0 to x1 on a shelf at height y, depth z. */
function books(part, x0, x1, y, z, seed) {
  let x = x0, i = seed || 0;
  while (x < x1 - 0.05) {
    const w = 0.045 + ((i * 7) % 3) * 0.015, h = 0.2 + ((i * 5) % 4) * 0.025;
    part('box', BOOKS[i % BOOKS.length], [w, h, 0.18], [x + w / 2, y, z]);
    x += w + 0.008;
    i += 1;
    if (i % 9 === 4) x += 0.12;                  // a gap, so a shelf is not a solid wall of spines
  }
}

function pot(part, r, h, y) {
  part('cyl', 'pot', [r, h, r], [0, y || 0, 0]);
  part('cyl', 'soil', [r * 0.86, 0.02, r * 0.86], [0, (y || 0) + h - 0.01, 0]);
}

function bushes(part, spots, size, seed) {
  spots.forEach((s, i) => {
    part('bush', (i + (seed || 0)) % 2 ? 'leaf2' : 'leaf', [size * s[3], size * s[3] * 0.9, size * s[3]], [s[0], s[1], s[2]],
      { ry: i * 0.7 });
  });
}

function legs(part, w, d, h, color, inset) {
  const i = inset || 0.05;
  [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(([sx, sz]) => {
    part('box', color, [0.04, h, 0.04], [sx * (w / 2 - i), 0, sz * (d / 2 - i)]);
  });
}

function seatBlock(part, w, d, h, back, color, cushion) {
  part('box', color, [w, h * 0.55, d], [0, 0.08, 0]);
  part('box', cushion, [w - 0.12, 0.1, d - 0.2], [0, 0.08 + h * 0.55, 0.06]);
  part('box', color, [w, back, 0.18], [0, 0.08, -d / 2 + 0.09]);
}

export const BUILDERS = {
  // -------------------------------------------------------------- plants
  plant(part, it) {
    pot(part, 0.32, 0.32);
    bushes(part, [[0, 0.5, 0, 1], [0.09, 0.66, -0.05, 0.75], [-0.08, 0.62, 0.06, 0.7]], 0.36, it.seed);
  },
  tallPlant(part, it) {
    pot(part, 0.4, 0.42);
    part('cyl', 'woodDark', [0.04, 0.7, 0.04], [0, 0.42, 0]);
    bushes(part, [[0, 0.95, 0, 1], [0.12, 1.2, 0.04, 0.8], [-0.1, 1.38, -0.05, 0.7], [0.04, 0.72, 0.1, 0.65]], 0.42, it.seed);
  },
  palm(part, it) {
    pot(part, 0.46, 0.4);
    part('cyl', 'woodDark', [0.07, 1.1, 0.07], [0, 0.4, 0]);
    for (let i = 0; i < 7; i++) {
      const a = i / 7 * Math.PI * 2 + (it.seed || 0) % 3;
      part('box', i % 2 ? 'leaf' : 'leaf2', [0.1, 0.03, 0.62], [Math.sin(a) * 0.26, 1.42, Math.cos(a) * 0.26],
        { ry: a, rx: 0.5 });
    }
  },
  cactus(part) {
    pot(part, 0.26, 0.2);
    part('cyl', 'leaf', [0.14, 0.5, 0.14], [0, 0.2, 0]);
    part('cyl', 'leaf2', [0.08, 0.18, 0.08], [0.09, 0.42, 0]);
    part('cyl', 'leaf2', [0.08, 0.14, 0.08], [-0.09, 0.32, 0]);
  },
  planter(part, it) {
    part('box', 'woodDark', [1.3, 0.38, 0.45], [0, 0, 0]);
    part('box', 'soil', [1.2, 0.02, 0.38], [0, 0.37, 0]);
    bushes(part, [[-0.45, 0.5, 0, 1], [-0.15, 0.55, 0.02, 0.9], [0.15, 0.5, -0.02, 1], [0.45, 0.56, 0, 0.85]], 0.36, it.seed);
  },
  trellis(part, it) {
    part('box', 'pot', [1.2, 0.3, 0.25], [0, 0, 0]);
    [-0.55, 0, 0.55].forEach((x) => part('box', 'woodDark', [0.03, 1.5, 0.03], [x, 0.3, -0.06]));
    part('box', 'woodDark', [1.14, 0.03, 0.03], [0, 1.0, -0.06]);
    part('box', 'woodDark', [1.14, 0.03, 0.03], [0, 1.6, -0.06]);
    bushes(part, [[-0.4, 0.55, 0, 0.8], [0.3, 0.8, -0.02, 0.9], [-0.1, 1.15, -0.02, 0.8], [0.45, 1.4, -0.04, 0.7],
                  [-0.45, 1.5, -0.04, 0.6]], 0.34, it.seed);
  },
  bed(part, it) {
    part('box', 'wood', [2.4, 0.42, 1.4], [0, 0, 0]);
    part('box', 'soil', [2.26, 0.02, 1.26], [0, 0.41, 0]);
    for (let i = 0; i < 10; i++) {
      const x = -0.95 + (i % 5) * 0.47, z = i < 5 ? -0.3 : 0.3;
      part('bush', (i + (it.seed || 0)) % 3 ? 'leaf' : 'leaf2', [0.36, 0.3 + (i % 3) * 0.08, 0.36], [x, 0.56, z], { ry: i });
    }
    part('cyl', 'accent', [0.08, 0.1, 0.08], [-0.7, 0.72, -0.3]);
    part('cyl', 'accent2', [0.08, 0.1, 0.08], [0.25, 0.7, 0.3]);
  },

  // ------------------------------------------------------------ storage
  shelf(part, it) {
    part('box', 'wood', [0.04, 1.2, 0.35], [-0.48, 0, 0]);
    part('box', 'wood', [0.04, 1.2, 0.35], [0.48, 0, 0]);
    [0.02, 0.42, 0.82, 1.18].forEach((y) => part('box', 'wood', [1.0, 0.03, 0.35], [0, y, 0]));
    books(part, -0.44, 0.1, 0.05, 0, it.seed);
    books(part, -0.1, 0.44, 0.45, 0, (it.seed || 0) + 3);
    part('cyl', 'pot', [0.14, 0.12, 0.14], [0.25, 0.85, 0]);
    part('bush', 'leaf', [0.2, 0.18, 0.2], [0.25, 1.04, 0]);
    part('box', 'accent3', [0.2, 0.14, 0.14], [-0.28, 0.85, 0]);
  },
  bookcase(part, it) {
    part('box', 'woodDark', [1.0, 1.85, 0.4], [0, 0, -0.01]);
    part('box', 'wood', [0.92, 1.75, 0.02], [0, 0.05, -0.18]);
    [0.05, 0.48, 0.91, 1.34].forEach((y, i) => {
      part('box', 'wood', [0.92, 0.03, 0.36], [0, y, 0.01]);
      books(part, -0.42, 0.42, y + 0.03, 0.04, (it.seed || 0) + i * 4);
    });
  },
  crate(part, it) {
    const stack = 1 + ((it.seed || 0) % 2);
    for (let i = 0; i < stack; i++) {
      part('box', 'wood', [0.58, 0.36, 0.58], [0, i * 0.37, 0], { ry: i * 0.15 });
      part('box', 'woodDark', [0.6, 0.05, 0.6], [0, i * 0.37 + 0.16, 0], { ry: i * 0.15 });
    }
    books(part, -0.2, 0.2, stack * 0.37, 0, it.seed);
  },
  cabinet(part, it) {
    const w = it.w || 2;
    part('box', 'desk', [w, 0.5, 0.35], [0, it.h, 0.18]);
    for (let x = -w / 2 + 0.3; x < w / 2; x += 0.6) part('box', 'trim', [0.02, 0.42, 0.01], [x + 0.3, it.h + 0.04, 0.36]);
  },

  // ------------------------------------------------------------- seating
  sofa(part) {
    part('box', 'fabric', [2.4, 0.25, 0.85], [0, 0.08, 0]);
    part('box', 'fabric', [2.4, 0.55, 0.22], [0, 0.08, -0.32]);
    part('box', 'fabric', [0.2, 0.45, 0.85], [-1.1, 0.08, 0]);
    part('box', 'fabric', [0.2, 0.45, 0.85], [1.1, 0.08, 0]);
    part('box', 'cushion', [0.96, 0.12, 0.6], [-0.5, 0.33, 0.08]);
    part('box', 'cushion', [0.96, 0.12, 0.6], [0.5, 0.33, 0.08]);
    part('box', 'accent', [0.3, 0.28, 0.1], [-0.82, 0.42, -0.16], { rz: 0.2 });
    part('box', 'accent2', [0.3, 0.28, 0.1], [0.82, 0.42, -0.16], { rz: -0.2 });
    legs(part, 2.4, 0.85, 0.08, 'woodDark', 0.1);
  },
  armchair(part, it) {
    seatBlock(part, 0.8, 0.8, 0.5, 0.75, (it.seed || 0) % 2 ? 'fabric' : 'fabric2', 'cushion');
    part('box', (it.seed || 0) % 2 ? 'fabric' : 'fabric2', [0.12, 0.5, 0.7], [-0.34, 0.08, 0.05]);
    part('box', (it.seed || 0) % 2 ? 'fabric' : 'fabric2', [0.12, 0.5, 0.7], [0.34, 0.08, 0.05]);
    legs(part, 0.8, 0.8, 0.08, 'woodDark', 0.08);
  },
  beanbag(part, it) {
    part('ball', (it.seed || 0) % 2 ? 'accent' : 'accent2', [0.75, 0.42, 0.75], [0, 0.2, 0]);
    part('ball', (it.seed || 0) % 2 ? 'accent' : 'accent2', [0.6, 0.35, 0.3], [0, 0.4, -0.2]);
  },
  bench(part) {
    part('box', 'wood', [1.3, 0.06, 0.42], [0, 0.4, 0]);
    legs(part, 1.3, 0.42, 0.4, 'dark', 0.12);
    part('box', 'cushion', [0.5, 0.06, 0.36], [-0.3, 0.46, 0]);
  },
  chair(part, it) {
    const seat = it.gold ? GOLD : 'dark';
    part('box', seat, [0.42, 0.06, 0.42], [0, 0.44, 0]);
    part('box', seat, [0.42, 0.42, 0.05], [0, 0.5, -0.19]);
    part('cyl', 'metal', [0.05, 0.44, 0.05], [0, 0, 0]);
    part('box', 'metal', [0.4, 0.03, 0.06], [0, 0, 0]);
    part('box', 'metal', [0.06, 0.03, 0.4], [0, 0, 0]);
  },

  // -------------------------------------------------------------- tables
  meetTable(part) {
    part('box', 'wood', [2.0, 0.05, 1.0], [0, 0.72, 0]);
    part('box', 'dark', [0.08, 0.72, 0.6], [-0.7, 0, 0]);
    part('box', 'dark', [0.08, 0.72, 0.6], [0.7, 0, 0]);
    part('box', 'paper', [0.3, 0.01, 0.22], [-0.4, 0.77, 0.15], { ry: 0.2 });
    part('box', 'dark', [0.36, 0.02, 0.24], [0.3, 0.77, -0.1], { ry: -0.15 });
    part('cyl', 'paper', [0.08, 0.1, 0.08], [0.6, 0.77, 0.2]);
  },
  highTable(part) {
    part('cyl', 'dark', [0.36, 0.03, 0.36], [0, 0, 0]);
    part('cyl', 'metal', [0.06, 1.0, 0.06], [0, 0, 0]);
    part('cyl', 'wood', [0.55, 0.04, 0.55], [0, 1.0, 0]);
    part('cyl', 'paper', [0.07, 0.09, 0.07], [0.1, 1.04, 0.06]);
  },
  coffeeTable(part) {
    part('box', 'woodDark', [0.9, 0.05, 0.5], [0, 0.36, 0]);
    legs(part, 0.9, 0.5, 0.36, 'woodDark');
    part('box', 'accent3', [0.26, 0.02, 0.2], [-0.2, 0.41, 0], { ry: 0.3 });
    part('cyl', 'paper', [0.07, 0.08, 0.07], [0.22, 0.41, 0.05]);
  },
  sideTable(part, it) {
    part('cyl', 'woodDark', [0.45, 0.04, 0.45], [0, 0.5, 0]);
    part('cyl', 'woodDark', [0.05, 0.5, 0.05], [0, 0, 0]);
    part('cyl', 'woodDark', [0.3, 0.02, 0.3], [0, 0, 0]);
    if ((it.seed || 0) % 2) books(part, -0.12, 0.12, 0.54, 0, it.seed);
    else part('cyl', 'accent', [0.12, 0.18, 0.12], [0, 0.54, 0]);
  },
  pingpong(part) {
    part('box', 'accent2', [2.4, 0.05, 1.4], [0, 0.74, 0]);
    part('box', 'paper', [2.4, 0.052, 0.02], [0, 0.74, 0]);
    part('box', 'paper', [0.03, 0.15, 1.44], [0, 0.79, 0]);
    legs(part, 2.4, 1.4, 0.74, 'dark', 0.25);
    part('ball', 'paper', [0.05, 0.05, 0.05], [0.5, 0.85, 0.3]);
    part('box', 'accent', [0.16, 0.02, 0.24], [-0.9, 0.8, -0.3], { ry: 0.4 });
  },

  // ------------------------------------------------------------ the kit
  counter(part, it, bulb) {
    const w = it.w;
    part('box', 'desk', [w, 0.86, 0.6], [0, 0, 0]);
    part('box', 'woodDark', [w + 0.04, 0.05, 0.64], [0, 0.86, 0]);
    for (let x = -w / 2 + 0.3; x < w / 2; x += 0.6) part('box', 'trim', [0.02, 0.7, 0.01], [x + 0.3, 0.08, 0.31]);
    // coffee machine, kettle, fruit, sink
    part('box', 'dark', [0.38, 0.45, 0.36], [-w / 2 + 0.4, 0.91, -0.05]);
    part('box', 'metal', [0.3, 0.06, 0.2], [-w / 2 + 0.4, 0.91, 0.12]);
    part('cyl', 'paper', [0.08, 0.1, 0.08], [-w / 2 + 0.4, 0.97, 0.12]);
    bulb(-w / 2 + 0.52, 1.26, 0.13, 0.02);
    part('cyl', 'metal', [0.16, 0.22, 0.16], [-w / 2 + 0.9, 0.91, 0]);
    part('cyl', 'accent3', [0.32, 0.06, 0.32], [0, 0.91, 0.05]);
    part('ball', 'accent', [0.09, 0.09, 0.09], [-0.05, 1.0, 0.05]);
    part('ball', 'leaf2', [0.09, 0.09, 0.09], [0.06, 1.0, 0.02]);
    part('box', 'metal', [0.5, 0.02, 0.36], [w / 2 - 0.7, 0.9, 0]);
    part('cyl', 'metal', [0.03, 0.25, 0.03], [w / 2 - 0.7, 0.91, -0.2]);
    part('box', 'dark', [0.42, 0.26, 0.32], [w / 2 - 0.25, 0.91, -0.08]);   // microwave
  },
  fridge(part) {
    part('box', 'metal', [0.72, 1.85, 0.68], [0, 0, 0]);
    part('box', 'trim', [0.7, 0.01, 0.01], [0, 1.2, 0.345]);
    part('box', 'dark', [0.03, 0.4, 0.04], [0.28, 1.3, 0.36]);
    part('box', 'dark', [0.03, 0.3, 0.04], [0.28, 0.8, 0.36]);
    part('box', 'accent', [0.08, 0.08, 0.01], [-0.15, 1.5, 0.345]);
    part('box', 'accent3', [0.1, 0.07, 0.01], [0.05, 1.42, 0.345]);
  },
  cooler(part) {
    part('box', 'paper', [0.4, 0.95, 0.4], [0, 0, 0]);
    part('box', 'dark', [0.16, 0.08, 0.04], [0, 0.72, 0.21]);
    part('cyl', 'screen', [0.32, 0.42, 0.32], [0, 0.95, 0], { layer: 'glass' });
    part('cyl', 'screen', [0.26, 0.3, 0.26], [0, 0.97, 0]);
    part('cyl', 'paper', [0.06, 0.08, 0.06], [0.14, 0.95, 0.12]);
  },
  printer(part, it, bulb) {
    part('box', 'trim', [0.9, 0.62, 0.62], [0, 0, 0]);
    part('box', 'paper', [0.84, 0.25, 0.56], [0, 0.62, 0]);
    part('box', 'dark', [0.3, 0.04, 0.16], [0.18, 0.87, 0.18]);
    part('box', 'paper', [0.3, 0.03, 0.22], [-0.12, 0.88, 0.0]);
    part('box', 'dark', [0.8, 0.02, 0.02], [0, 0.3, 0.32]);
    bulb(0.36, 0.9, 0.22, 0.015);
  },
  reception(part, it, bulb) {
    part('box', 'wood', [2.4, 1.0, 0.7], [0, 0, 0]);
    part('box', 'desk', [2.5, 0.05, 0.8], [0, 1.0, 0]);
    part('box', 'accent', [2.4, 0.08, 0.02], [0, 0.55, 0.36]);
    part('box', 'dark', [0.45, 0.3, 0.03], [-0.5, 1.05, -0.15]);
    bulb(-0.5, 1.22, -0.12, 0.012);
    part('cyl', 'pot', [0.18, 0.16, 0.18], [0.8, 1.05, 0]);
    part('bush', 'leaf', [0.26, 0.24, 0.26], [0.8, 1.3, 0]);
    part('cyl', 'accent3', [0.1, 0.14, 0.1], [0.3, 1.05, 0.1]);
  },
  rack(part, it, bulb) {
    part('box', 'dark', [0.6, 2.0, 0.8], [0, 0, 0]);
    for (let i = 0; i < 6; i++) {
      part('box', 'metal', [0.52, 0.02, 0.01], [0, 0.22 + i * 0.3, 0.405]);
      bulb(-0.2 + (i % 3) * 0.06, 0.32 + i * 0.3, 0.41, 0.012, { color: i % 4 === 0 ? 'accent' : 'leaf2' });
    }
  },
  ac(part) {
    part('box', 'paper', [0.7, 1.7, 0.5], [0, 0, 0]);
    for (let i = 0; i < 6; i++) part('box', 'metal', [0.6, 0.02, 0.01], [0, 1.0 + i * 0.1, 0.255]);
  },

  // ------------------------------------------------------------- things
  floorLamp(part, it, bulb) {
    part('cyl', 'dark', [0.3, 0.03, 0.3], [0, 0, 0]);
    part('cyl', 'dark', [0.03, 1.4, 0.03], [0, 0, 0]);
    part('cone', 'accent3', [0.36, 0.26, 0.36], [0, 1.36, 0]);
    bulb(0, 1.36, 0, 0.06, { pool: true });
  },
  umbrella(part) {
    part('cyl', 'dark', [0.26, 0.5, 0.26], [0, 0, 0]);
    part('cyl', 'accent', [0.05, 0.7, 0.05], [0.04, 0.2, 0], { rz: 0.1 });
    part('cyl', 'accent2', [0.05, 0.75, 0.05], [-0.04, 0.2, 0.03], { rz: -0.12 });
  },
  bin(part) {
    part('cyl', 'metal', [0.28, 0.42, 0.28], [0, 0, 0]);
    part('cyl', 'dark', [0.29, 0.03, 0.29], [0, 0.42, 0]);
  },
  coatRack(part) {
    part('cyl', 'dark', [0.3, 0.03, 0.3], [0, 0, 0]);
    part('cyl', 'woodDark', [0.04, 1.7, 0.04], [0, 0, 0]);
    part('box', 'accent2', [0.2, 0.5, 0.12], [0.1, 1.1, 0], { rz: 0.1 });
    part('box', 'fabric2', [0.18, 0.4, 0.12], [-0.08, 1.2, 0.05], { rz: -0.1 });
  },
  bike(part) {
    part('ring', 'dark', [0.62, 0.62, 0.62], [-0.45, 0.31, 0]);
    part('ring', 'dark', [0.62, 0.62, 0.62], [0.45, 0.31, 0]);
    part('box', 'accent', [0.9, 0.04, 0.04], [0, 0.52, 0], { rz: -0.15 });
    part('box', 'accent', [0.04, 0.45, 0.04], [-0.15, 0.3, 0], { rz: 0.3 });
    part('box', 'dark', [0.22, 0.04, 0.1], [-0.2, 0.78, 0]);
    part('box', 'metal', [0.04, 0.04, 0.4], [0.4, 0.82, 0]);
  },

  // -------------------------------------------------- on walls, overhead
  whiteboard(part, it) {
    part('box', 'trim', [it.w + 0.06, 0.96, 0.03], [0, it.h - 0.48, 0.015]);
    part('box', 'paper', [it.w, 0.9, 0.03], [0, it.h - 0.45, 0.03]);
    part('box', 'accent2', [0.6, 0.02, 0.01], [-0.3, it.h + 0.2, 0.05], { rz: 0.05 });
    part('box', 'accent', [0.4, 0.02, 0.01], [-0.35, it.h + 0.05, 0.05]);
    part('box', 'accent2', [0.5, 0.02, 0.01], [0.3, it.h - 0.1, 0.05], { rz: -0.1 });
    part('box', 'dark', [0.3, 0.3, 0.01], [0.4, it.h + 0.05, 0.045], { layer: 'glass' });
  },
  art(part, it) {
    const w = it.w || 1;
    part('box', 'frame', [w, w * 0.7, 0.04], [0, it.h - w * 0.35, 0.02]);
    part('box', 'paper', [w - 0.08, w * 0.7 - 0.08, 0.02], [0, it.h - w * 0.35 + 0.04, 0.04]);
    part('box', 'accent', [w * 0.38, w * 0.3, 0.01], [-w * 0.15, it.h - w * 0.2, 0.05]);
    part('box', 'accent2', [w * 0.3, w * 0.42, 0.01], [w * 0.18, it.h - w * 0.28, 0.052]);
    part('box', 'accent3', [w * 0.22, w * 0.18, 0.01], [-w * 0.05, it.h - w * 0.06, 0.054]);
  },
  clock(part, it) {
    part('cyl', 'frame', [0.42, 0.04, 0.42], [0, it.h, 0.02], { rx: Math.PI / 2 });
    part('cyl', 'paper', [0.36, 0.02, 0.36], [0, it.h, 0.045], { rx: Math.PI / 2 });
    part('box', 'dark', [0.02, 0.13, 0.01], [0, it.h, 0.06]);
    part('box', 'dark', [0.1, 0.02, 0.01], [0.04, it.h, 0.06], { rz: 0.4 });
  },
  tv(part, it, bulb) {
    part('box', 'dark', [it.w, it.w * 0.58, 0.05], [0, it.h - it.w * 0.29, 0.03]);
    part('box', 'screen', [it.w - 0.06, it.w * 0.58 - 0.06, 0.01], [0, it.h - it.w * 0.29 + 0.03, 0.06], { layer: 'glass' });
    bulb(it.w / 2 - 0.05, it.h - it.w * 0.29 + 0.02, 0.06, 0.008, { color: 'accent' });
  },
  door(part, it) {
    part('box', 'frame', [0.08, 2.3, 0.12], [-it.w / 2, 0, 0]);
    part('box', 'frame', [0.08, 2.3, 0.12], [it.w / 2, 0, 0]);
    part('box', 'frame', [it.w + 0.08, 0.12, 0.12], [0, 2.25, 0]);
    part('box', 'glass', [it.w / 2 - 0.06, 2.2, 0.03], [-it.w / 4, 0.02, 0], { layer: 'glass' });
    part('box', 'glass', [it.w / 2 - 0.06, 2.2, 0.03], [it.w / 4 + 0.04, 0.02, 0.05], { layer: 'glass' });
    part('box', 'metal', [0.03, 0.4, 0.04], [-0.06, 0.9, 0.04]);
    part('box', 'accent', [0.5, 0.01, 0.9], [0, 0, 0.7]);                     // the welcome mat
  },
  pendant(part, it, bulb) {
    part('cyl', 'dark', [0.015, 0.6, 0.015], [0, 2.0, 0]);
    part('cone', 'accent3', [0.4, 0.22, 0.4], [0, 1.85, 0]);
    bulb(0, 1.84, 0, 0.07, { pool: true });
  },
  sign(part) {
    part('box', 'dark', [0.06, 1.3, 0.06], [-0.24, 0, 0]);
    part('box', 'dark', [0.06, 1.3, 0.06], [0.24, 0, 0]);
    part('box', 'dark', [0.6, 0.04, 0.2], [0, 0, 0]);
  },
  board(part, it) {
    part('box', 'woodDark', [it.w + 0.08, 0.92, 0.04], [0, it.h - 0.46, 0.02]);
  }
};

/* What sits on a desk (office-floor.js picks it per session). The desk
   faces +x: the sitter is on -x, the monitor at +x with its back to the
   camera. Monitor, keyboard and lamp are the live parts office-scenery.js
   keeps for itself. */
const PROPS = {
  mug(part) { part('cyl', 'accent', [0.08, 0.1, 0.08], [0.18, 0, 0.42]); },
  notebook(part) {
    part('box', 'accent2', [0.2, 0.025, 0.28], [-0.05, 0, 0.42], { ry: 0.2 });
    part('box', 'paper', [0.18, 0.01, 0.26], [-0.05, 0.025, 0.42], { ry: 0.2 });
  },
  plant(part) {
    part('cyl', 'pot', [0.12, 0.1, 0.12], [0.2, 0, -0.45]);
    part('bush', 'leaf', [0.18, 0.16, 0.18], [0.2, 0.17, -0.45]);
  },
  photo(part) { part('box', 'frame', [0.03, 0.14, 0.12], [0.25, 0, 0.42], { rz: -0.15 }); },
  books(part) { books(part, -0.12, 0.14, 0, -0.5, 3); },
  trophy(part) {                                           // the lead tier's ornament
    part('box', 'dark', [0.1, 0.04, 0.1], [-0.25, 0, -0.3]);
    part('cyl', GOLD, [0.03, 0.08, 0.03], [-0.25, 0.04, -0.3]);
    part('cone', GOLD, [0.11, 0.1, 0.11], [-0.25, 0.22, -0.3], { rx: Math.PI });
  },
  orchid(part) {                                           // the muse tier's
    part('cyl', 'paper', [0.1, 0.09, 0.1], [-0.25, 0, -0.3]);
    part('cyl', 'leaf', [0.012, 0.22, 0.012], [-0.25, 0.09, -0.3], { rz: 0.15 });
    part('ball', '#d77fc0', [0.07, 0.06, 0.07], [-0.27, 0.3, -0.3]);
    part('ball', '#e9a6d8', [0.06, 0.05, 0.06], [-0.22, 0.26, -0.27]);
  }
};

/* The desk itself: top, legs, a drawer pedestal, the monitor (its back to
   the camera), keyboard, mouse, the lamp's arm and the props, in the
   desk's own frame (origin at its centre). The lamp's bulb and the
   monitor's glow are live parts office-scenery.js adds: they follow the
   session's state. */
export function buildDesk(part, props, w, d) {
  part('box', 'desk', [w, 0.04, d], [0, 0.72, 0]);
  part('box', 'deskLeg', [w - 0.1, 0.7, 0.04], [0, 0.02, -d / 2 + 0.04]);
  part('box', 'deskLeg', [w - 0.1, 0.7, 0.04], [0, 0.02, d / 2 - 0.04]);
  part('box', 'deskLeg', [0.4, 0.55, 0.36], [0.1, 0.02, d / 2 - 0.25]);
  part('box', 'metal', [0.12, 0.05, d - 0.2], [0.25, 0.6, 0]);
  part('box', 'dark', [0.12, 0.012, 0.16], [0.2, 0.76, 0]);               // monitor foot
  part('box', 'dark', [0.03, 0.14, 0.03], [0.22, 0.76, 0]);
  part('box', 'dark', [0.035, 0.36, 0.62], [0.22, 0.86, 0]);
  part('box', 'dark', [0.16, 0.02, 0.46], [-0.12, 0.76, 0]);              // keyboard
  part('box', 'dark', [0.1, 0.02, 0.08], [-0.12, 0.76, 0.33]);            // mouse
  part('cyl', 'dark', [0.1, 0.02, 0.1], [0.2, 0.76, -0.5]);              // lamp base and arm
  part('box', 'dark', [0.02, 0.4, 0.02], [0.2, 0.76, -0.5], { rx: 0.25 });
  part('cone', 'dark', [0.16, 0.1, 0.16], [0.2, 1.08, -0.4]);
  props.forEach((name) => PROPS[name]((s, c, size, pos, o) => part(s, c, size, [pos[0], pos[1] + 0.76, pos[2]], o)));
}
