/* The office floor (spec Sec 15.1): one open-plan floor for every live
   session. Pure: `build(data, theme)` turns the /api/office payload into a
   plan, with no DOM, no state and no three.js, so tests/office_floor_check.js
   drives it under node. office-3d.js turns a plan into meshes.

   Tile space: one tile is about a metre. x runs across the floor (world X),
   y runs from the back wall to the front (world Z); the camera looks in from
   the front-right, so y = 0 and x = 0 are the window walls.

     y = 0     back band: server room, meeting room, kitchen, lounge, library
     y = BACK  main corridor, the full width of the floor
               bands of neighbourhoods, one per project, each band with a
               corridor strip above it; the trunk walkway runs down the east
               side of the bands to the door lane
     x = W     right column: a second meeting room, the printer corner and
               reception with the entrance door

   A neighbourhood is a fenced block of desk units: a desk, the chair the
   lead sits on (facing +x, so the camera sees faces), and a pod of four
   standing slots for interns. A spine walkway runs down its west side to
   every row's aisle; the water cooler stands at the east end of row 0.

   Every route is a chain of straight walks along aisles, the spine, the
   corridors and the trunk. A *spot* is the simulation's contract
   (office-sim.js): `{x, y, lane, via}`, every lane a line y = at. Decor is
   placed last, and only where it touches no desk, partition, route or spot;
   the floor grows to fit, the figures never shrink. */
window.OfficeFloor = (function () {
  'use strict';

  var hash = window.OfficeCast.hash, rng = window.OfficeCast.rng;

  // --------------------------------------------------------------- sizes
  var DESK = { w: 0.75, d: 1.3 };   // across x by along y
  var UNIT = 2.85;                  // one desk unit across: chair, desk, pod
  var ROW = 2.4;                    // desk band plus aisle, front to back
  var COLS = 3;                     // desk units per row at most
  var LEAD_IN = 1.0;                // neighbourhood west edge to the first unit
  var SPINE = 0.5;                  // spine walkway, from the west edge
  var STRIP = 1.0;                  // east strip: cooler, sign and decor
  var CORR = 1.3;                   // corridor strip depth
  var BACK = 4.2;                   // back band depth
  var MARGIN = 1.0;                 // left window wall to the first neighbourhood
  var NB_GAP = 0.6;
  var RIGHT = 5.6;                  // right column width
  var FRONT = 1.2;                  // front strip below the last band
  var POD = 4;
  var WALL_H = 2.6;
  var ROUTE_CLEAR = 0.15;           // decor keeps this far from every walk
  var SPOT_CLEAR = 0.22;            // and this far from every standing spot
  var FILL_GAP = 0.12;              // fill pieces keep this far from anything solid
  var EDGE = 1.0;                   // fill lines a region's edges, its middle stays open

  /* Every piece the floor can place. `w` x `d` is the footprint before
     rotation; floor pieces block, wall and ceiling pieces do not. The fill
     kinds are the ones a theme can weight. */
  var KINDS = {
    plant: { w: 0.5, d: 0.5 }, tallPlant: { w: 0.6, d: 0.6 }, palm: { w: 0.7, d: 0.7 }, cactus: { w: 0.35, d: 0.35 },
    shelf: { w: 1.0, d: 0.35 }, bookcase: { w: 1.0, d: 0.4 }, floorLamp: { w: 0.35, d: 0.35 },
    beanbag: { w: 0.75, d: 0.75 }, bench: { w: 1.3, d: 0.45 }, armchair: { w: 0.8, d: 0.8 },
    sideTable: { w: 0.45, d: 0.45 }, umbrella: { w: 0.3, d: 0.3 }, bin: { w: 0.3, d: 0.3 },
    planter: { w: 1.3, d: 0.45 }, printer: { w: 0.9, d: 0.65 }, crate: { w: 0.6, d: 0.6 },
    bike: { w: 1.4, d: 0.35 }, trellis: { w: 1.2, d: 0.25 },
    rack: { w: 0.6, d: 0.8 }, ac: { w: 0.7, d: 0.5 }, meetTable: { w: 2.0, d: 1.0 }, chair: { w: 0.5, d: 0.5 },
    counter: { w: 1, d: 0.6 }, fridge: { w: 0.75, d: 0.7 }, highTable: { w: 0.55, d: 0.55 },
    sofa: { w: 2.4, d: 0.85 }, coffeeTable: { w: 0.9, d: 0.5 }, reception: { w: 2.4, d: 0.8 },
    cooler: { w: 0.45, d: 0.45 }, sign: { w: 0.6, d: 0.25 }, coatRack: { w: 0.35, d: 0.35 },
    pingpong: { w: 2.4, d: 1.4 }, bed: { w: 2.4, d: 1.4 },
    board: { mount: 'wall' }, whiteboard: { mount: 'wall' }, art: { mount: 'wall' }, clock: { mount: 'wall' },
    cabinet: { mount: 'wall' }, tv: { mount: 'wall' }, door: { mount: 'wall' },
    pendant: { mount: 'ceiling' }
  };
  var FILL_KINDS = ['plant', 'tallPlant', 'palm', 'cactus', 'shelf', 'bookcase', 'floorLamp', 'beanbag', 'bench',
                    'armchair', 'sideTable', 'umbrella', 'bin', 'planter', 'printer', 'crate', 'bike', 'trellis'];

  // ---------------------------------------------------------- geometry
  function rect(x0, y0, x1, y1) { return { x0: Math.min(x0, x1), y0: Math.min(y0, y1), x1: Math.max(x0, x1), y1: Math.max(y0, y1) }; }
  function overlaps(a, b, pad) {
    return a.x0 < b.x1 + pad && b.x0 < a.x1 + pad && a.y0 < b.y1 + pad && b.y0 < a.y1 + pad;
  }
  function inside(a, b) { return a.x0 >= b.x0 - 1e-9 && a.x1 <= b.x1 + 1e-9 && a.y0 >= b.y0 - 1e-9 && a.y1 <= b.y1 + 1e-9; }
  function p(x, y) { return { x: x, y: y }; }
  function spot(x, y, at, via) { return { x: x, y: y, lane: { axis: 'y', at: at }, via: via }; }

  // ------------------------------------------------------------- sizing
  function sizeNb(room, index) {
    var n = Math.max(1, room.workers.length);
    var cols = Math.min(COLS, n);
    var rows = Math.ceil(n / cols);
    return { room: room, index: index, cols: cols, rows: rows,
             w: LEAD_IN + cols * UNIT + STRIP, d: rows * ROW };
  }

  /* Pack neighbourhoods into bands about twice as wide as the floor is deep. */
  function pack(nbs) {
    var area = nbs.reduce(function (s, nb) { return s + nb.w * nb.d; }, 0);
    var widest = nbs.reduce(function (s, nb) { return Math.max(s, nb.w); }, 0);
    var limit = Math.max(widest, Math.min(40, Math.sqrt(area * 2.6)));
    var bands = [], band = null;
    nbs.forEach(function (nb) {
      if (!band || band.w + NB_GAP + nb.w > limit) { band = { nbs: [], w: -NB_GAP, d: 0 }; bands.push(band); }
      band.nbs.push(nb);
      band.w += NB_GAP + nb.w;
      band.d = Math.max(band.d, nb.d);
    });
    return bands;
  }

  // -------------------------------------------------------------- build
  function build(data, theme) {
    var rooms = data.rooms || [];
    var plan = { decor: [], zones: [], desks: [], partitions: [], places: {}, routes: [], seats: [],
                 regions: [], wallH: WALL_H, pod: POD };
    var bands = pack(rooms.map(sizeNb));
    var bandsW = bands.reduce(function (s, b) { return Math.max(s, b.w); }, 0);
    var mY = BACK + CORR / 2;

    // bands, top to bottom: band 0 hangs off the main corridor
    var y = BACK + CORR;
    bands.forEach(function (band, k) {
      if (k > 0) { band.corrY = y + CORR / 2; band.corrTop = y; y += CORR; } else { band.corrY = mY; }
      band.y = y;
      var x = MARGIN;
      band.nbs.forEach(function (nb) { nb.x = x; nb.y = y; nb.corrY = band.corrY; x += nb.w + NB_GAP; });
      y += band.d;
    });
    var bandsEnd = y;
    var D = Math.max(bandsEnd + FRONT, BACK + CORR + 6.4);
    var backW = 3.4 + 4.4 + 6.0 + 6.2 + 2.6;
    var W = Math.max(MARGIN + bandsW + 1.6 + RIGHT, backW);
    var trunkX = W - RIGHT - 0.5;
    var doorY = D - 2.0;
    plan.W = W; plan.D = D; plan.mainY = mY; plan.trunkX = trunkX; plan.doorY = doorY;
    plan.door = p(W, doorY);

    var lay = layer(plan);
    var door = spot(W - 0.45, doorY, doorY, null);
    var corridorTo = function (fromY, toY) { return fromY === toY ? [] : [p(trunkX, fromY), p(trunkX, toY)]; };

    // back band and right column first: the canteen spots are shared
    var canteen = backBand(lay, W, mY);
    rightColumn(lay, W, D, doorY);

    bands.forEach(function (band) {
      band.nbs.forEach(function (nb) {
        neighbourhood(lay, nb, {
          door: door, canteen: canteen, corridorTo: corridorTo, mainY: mY, doorY: doorY
        });
      });
      // the gap between the band and the trunk is open floor for decor
      var east = MARGIN + band.w + NB_GAP;
      if (trunkX - 0.6 - east > 0.8) lay.region(rect(east, band.y, trunkX - 0.6, band.y + band.d), 0);
      if (band.corrTop !== undefined) lay.region(rect(0.05, band.corrTop + 0.05, MARGIN - 0.1, band.y), 1);
      lay.region(rect(0.05, band.y, MARGIN - 0.15, band.y + band.d), 1);
    });
    lay.region(rect(0.05, bandsEnd + 0.15, trunkX - 0.6, D - 0.1), 0);

    plan.zones.unshift({ kind: 'corridor', x: 0, y: BACK, w: W, d: CORR });
    plan.zones.unshift({ kind: 'corridor', x: trunkX - 0.6, y: BACK, w: 1.2, d: doorY - BACK + 0.6 });
    walls(plan, W, D, doorY);
    fill(lay, theme);
    return plan;
  }

  /* The placement layer: what is solid, which walks and spots must stay
     clear, and the regions the fill pass may decorate. */
  function layer(plan) {
    var solids = [];
    return {
      plan: plan,
      solids: solids,
      add: function (kind, cx, cy, rot, extra) {
        var k = KINDS[kind];
        var item = Object.assign({ kind: kind, x: cx, y: cy, rot: rot || 0 }, extra || {});
        item.mount = k.mount || 'floor';
        if (item.mount === 'floor') {
          var w = item.w || k.w, d = item.d || k.d;
          item.w = w; item.d = d;
          var odd = (item.rot % 2) !== 0;
          item.box = rect(cx - (odd ? d : w) / 2, cy - (odd ? w : d) / 2, cx + (odd ? d : w) / 2, cy + (odd ? w : d) / 2);
          solids.push(item.box);
        }
        plan.decor.push(item);
        return item;
      },
      solid: function (box) { solids.push(box); },
      partition: function (x1, y1, x2, y2, style, h) {
        var part = { x1: x1, y1: y1, x2: x2, y2: y2, style: style, h: h };
        part.box = rect(x1 - (x1 === x2 ? 0.08 : 0), y1 - (y1 === y2 ? 0.08 : 0), x2 + (x1 === x2 ? 0.08 : 0), y2 + (y1 === y2 ? 0.08 : 0));
        plan.partitions.push(part);
        solids.push(part.box);
      },
      route: function (points) {
        for (var i = 1; i < points.length; i++) plan.routes.push([points[i - 1], points[i]]);
      },
      zone: function (kind, x, y, w, d) { plan.zones.push({ kind: kind, x: x, y: y, w: w, d: d }); },
      region: function (box, rot) { plan.regions.push({ box: box, rot: rot }); }
    };
  }

  // ---------------------------------------------------------- back band
  /* Server room, meeting room A, kitchen, lounge and the library, left to
     right along the back wall. Returns the canteen spots: standing room at
     the kitchen's high tables and the seats on the lounge sofas,
     interleaved so a crowd uses both. */
  function backBand(lay, W, mY) {
    var x = 0;
    var room = function (w) { var r = { x0: x, x1: x + w }; x += w; return r; };
    var server = room(3.4), meet = room(4.4), kitchen = room(6.0), lounge = room(6.2);
    var library = { x0: x, x1: W };

    // server room: two rows of racks, an air conditioner, solid walls
    lay.zone('tiles', server.x0, 0, 3.4, BACK);
    for (var r = 0; r < 3; r++) {
      lay.add('rack', server.x0 + 0.7 + r * 0.8, 0.6, 0);
      lay.add('rack', server.x0 + 0.7 + r * 0.8, 2.2, 0);
    }
    lay.add('ac', server.x0 + 3.0, 3.4, 3);
    lay.partition(server.x1, 0, server.x1, BACK, 'solid', WALL_H);
    frontWall(lay, server, 'solid');

    // meeting room A: a table for six, a whiteboard, a pendant
    lay.zone('carpet', meet.x0, 0, 4.4, BACK);
    meetingRoom(lay, (meet.x0 + meet.x1) / 2, 2.0);
    lay.add('whiteboard', meet.x0 + 0.02, 1.9, 1, { h: 1.0, w: 1.8 });
    lay.add('plant', meet.x1 - 0.45, 0.45, 0);
    lay.partition(meet.x1, 0, meet.x1, BACK, 'glass', WALL_H);
    frontWall(lay, meet, 'glass');

    // kitchen: counter and fridge on the back wall, high tables to stand at
    lay.zone('tiles', kitchen.x0, 0, 6.0, BACK);
    var counterW = 4.4;
    lay.add('counter', kitchen.x0 + 0.2 + counterW / 2, 0.35, 0, { w: counterW });
    lay.add('fridge', kitchen.x0 + 0.2 + counterW + 0.55, 0.4, 0);
    lay.add('cabinet', kitchen.x0 + 0.2 + counterW / 2, 0.02, 0, { w: counterW, h: 1.3 });
    lay.add('board', kitchen.x0 + 0.02, 1.7, 1, { h: 1.15, w: 1.6 });
    lay.add('clock', kitchen.x0 + 0.02, 3.2, 1, { h: 1.9 });
    var spots = [];
    [kitchen.x0 + 1.6, kitchen.x0 + 4.2].forEach(function (tx) {
      lay.add('highTable', tx, 2.6, 0);
      lay.add('pendant', tx, 2.6, 0);
      spots.push(p(tx - 0.55, 2.6), p(tx + 0.55, 2.6));
    });
    lay.add('bin', kitchen.x1 - 0.3, 1.5, 3);

    // lounge: two sofas facing the corridor, a rug, lamps and art
    lay.zone('rug', lounge.x0 + 0.4, 0.5, 5.4, 2.6);
    var seats = [];
    [lounge.x0 + 1.6, lounge.x0 + 4.6].forEach(function (sx) {
      lay.add('sofa', sx, 0.95, 0);
      lay.add('coffeeTable', sx, 2.3, 0);
      lay.add('pendant', sx, 2.0, 0);
      seats.push(p(sx - 0.75, 1.62), p(sx + 0.75, 1.62));
    });
    lay.add('floorLamp', lounge.x0 + 3.1, 0.6, 0);
    lay.add('art', lounge.x0 + 1.6, 0.02, 0, { h: 1.7, w: 1.4 });
    lay.add('art', lounge.x0 + 4.6, 0.02, 0, { h: 1.7, w: 1.0 });
    lay.add('tv', lounge.x0 + 3.1, 0.02, 0, { h: 1.6, w: 1.2 });

    // library: bookcases on the back wall, the rest is the fill pass's
    lay.zone('zone', library.x0, 0, library.x1 - library.x0, BACK);
    for (var bx = library.x0 + 0.7; bx + 0.5 <= library.x1 - 0.2; bx += 1.1) lay.add('bookcase', bx, 0.3, 0);
    lay.region(rect(library.x0 + 0.2, 0.8, library.x1 - 0.2, BACK - 0.1), 0);

    // canteen spots alternate between a sofa seat and a high table
    var list = [];
    for (var i = 0; i < Math.max(spots.length, seats.length); i++) {
      if (seats[i]) list.push({ at: seats[i], sit: true });
      if (spots[i]) list.push({ at: spots[i], sit: false });
    }
    return list.map(function (c) {
      var s = spot(c.at.x, c.at.y, mY, null);
      if (c.sit) lay.plan.seats.push({ x: c.at.x, y: c.at.y, face: p(0, 1), height: 0.42, back: -0.1 });
      return s;
    });
  }

  /* A glass or solid wall along the corridor edge of a back-band room,
     with a doorway at its east end. */
  function frontWall(lay, area, style) {
    lay.partition(area.x0, BACK, area.x1 - 1.2, BACK, style, WALL_H);
    lay.partition(area.x1 - 0.3, BACK, area.x1, BACK, style, WALL_H);
  }

  function meetingRoom(lay, cx, cy) {
    lay.add('meetTable', cx, cy, 0);
    [-0.6, 0, 0.6].forEach(function (dx) {
      lay.add('chair', cx + dx, cy - 0.8, 0);
      lay.add('chair', cx + dx, cy + 0.8, 2);
    });
    lay.add('pendant', cx, cy, 0);
  }

  // -------------------------------------------------------- right column
  /* Meeting room B and the printer corner up top, reception at the door. */
  function rightColumn(lay, W, D, doorY) {
    var x0 = W - RIGHT + 0.2, x1 = W - 0.2;
    var top = BACK + CORR + 0.4, bottom = doorY - 2.1;
    if (bottom - top >= 3.4) {
      var h = Math.min(4.2, bottom - top);
      lay.zone('carpet', x0, top, x1 - x0, h);
      meetingRoom(lay, (x0 + x1) / 2, top + h / 2);
      lay.partition(x0, top, x1, top, 'glass', WALL_H);
      lay.partition(x0, top, x0, top + h, 'glass', WALL_H);
      lay.partition(x0, top + h, x1 - 1.4, top + h, 'glass', WALL_H);
      lay.add('plant', x1 - 0.4, top + 0.4, 0);
      top += h + 0.3;
    }
    if (bottom - top >= 1.2) {
      lay.add('printer', x1 - 0.55, top + 0.45, 3);
      lay.add('shelf', x1 - 0.3, top + 1.4 <= bottom ? top + 1.45 : top + 0.45, 3);
      lay.region(rect(x0, top, x1 - 1.0, bottom), 0);
    }
    // reception: desk above the door lane, a waiting sofa below it
    lay.zone('rug', x0, doorY - 1.8, x1 - x0, 3.6);
    lay.add('reception', (x0 + x1) / 2 - 0.3, doorY - 1.25, 0);
    lay.add('sofa', (x0 + x1) / 2 - 0.3, D - 0.5, 2);
    lay.add('plant', x0 + 0.35, D - 0.45, 0);
    lay.add('umbrella', W - 0.35, doorY + 0.65, 3);
    lay.add('coatRack', W - 0.35, doorY - 0.75, 3);
    lay.add('door', W, doorY, 3, { w: 1.2, h: 0 });
  }

  // ------------------------------------------------------- neighbourhood
  /* One project: rows of desk units fenced by low partitions, with a gap
     where the spine meets the corridor. Every worker's places, and the
     walks between them, are laid out here. */
  function neighbourhood(lay, nb, shared) {
    var plan = lay.plan;
    var style = 'low';
    var spineX = nb.x + SPINE;
    var stripX = nb.x + LEAD_IN + nb.cols * UNIT;
    var aisle = function (r) { return nb.y + r * ROW + 1.95; };
    var a0 = aisle(0);

    lay.zone('nb', nb.x, nb.y, nb.w, nb.d);
    lay.partition(nb.x + 1.0, nb.y, nb.x + nb.w, nb.y, style, 0.75);
    lay.partition(nb.x, nb.y, nb.x, nb.y + nb.d, style, 0.75);
    lay.partition(nb.x, nb.y + nb.d, nb.x + nb.w, nb.y + nb.d, style, 0.75);
    lay.partition(nb.x + nb.w, nb.y, nb.x + nb.w, nb.y + nb.d, style, 0.75);

    var cooler = lay.add('cooler', stripX + 0.5, a0 - 0.5, 3);
    var stand = p(stripX - 0.05, a0);
    lay.add('sign', stripX + 0.5, nb.y + 0.35, 0, { text: nb.room.project, w: 0.6 });
    for (var r = 1; r < nb.rows; r++) lay.region(rect(stripX + 0.05, nb.y + r * ROW, nb.x + nb.w - 0.1, nb.y + r * ROW + 1.4), 3);

    /* Out of the neighbourhood: down the aisle to the spine, up the spine
       to the corridor, then along the corridor to the trunk if needed. */
    var out = function (r, toY) {
      return [p(spineX, aisle(r)), p(spineX, nb.corrY)].concat(shared.corridorTo(nb.corrY, toY));
    };
    var toRow0 = function (r) { return r === 0 ? [] : [p(spineX, aisle(r)), p(spineX, a0)]; };
    var local = function (r, at) { return spot(at.x, at.y, a0, toRow0(r)); };

    nb.room.workers.forEach(function (worker, k) {
      var col = k % nb.cols, r = Math.floor(k / nb.cols);
      var ux = nb.x + LEAD_IN + col * UNIT, ry = nb.y + r * ROW, ay = aisle(r);
      var seat = spot(ux + 0.55, ry + 0.8, ay, [p(ux + 0.55, ay)]);
      var desk = { sid: worker.session_id, room: nb.index, row: r, x: ux + 0.95, y: ry + 0.15, w: DESK.w, d: DESK.d,
                   seat: seat, props: deskProps(worker.session_id) };
      plan.desks.push(desk);
      plan.seats.push({ x: seat.x, y: seat.y, face: p(1, 0), height: 0.48, back: -0.04, desk: desk });
      lay.solid(rect(desk.x, desk.y, desk.x + desk.w, desk.y + desk.d));
      lay.solid(rect(seat.x - 0.28, seat.y - 0.28, seat.x - 0.1, seat.y + 0.28));     // the chair back

      var pod = [];
      for (var s = 0; s < POD; s++) pod.push({ x: ux + 2.05 + (s % 2) * 0.45, y: ry + 0.45 + Math.floor(s / 2) * 0.65 });
      var doorWay = out(r, shared.doorY);
      var placeDoor = spot(shared.door.x, shared.door.y, shared.doorY, doorWay);
      var places = {
        room: nb.index, row: r, seat: seat,
        cooler: local(r, p(cooler.x, cooler.y)), stand: local(r, stand),
        meet: local(r, p(nb.x + 1.3, a0)), meet2: local(r, p(nb.x + 2.0, a0)),
        pod: pod.map(function (q) { return spot(q.x, q.y, ay, doorWay.slice().reverse().concat([p(q.x, ay)])); }),
        handoff: spot(ux + 1.1, ry + 1.72, ay, [p(ux + 1.1, ay)]),
        door: placeDoor,
        canteen: shared.canteen.map(function (c) {
          return spot(c.x, c.y, shared.mainY, out(r, shared.mainY).concat([p(c.x, shared.mainY)]));
        })
      };
      plan.places[worker.session_id] = places;
      tripsOf(lay, places);
    });
  }

  /* Every walk the simulation can take from these places, so decor keeps
     clear of them: seat to each errand and back, door to each pod slot,
     pod slot to the hand-off spot, seat to the door. */
  function tripsOf(lay, pl) {
    var from = function (s) { return { x: s.x, y: s.lane.at }; };
    var walk = function (start, s) { lay.route([start].concat(s.via || [], [p(s.x, s.y)])); };
    var origin = from(pl.seat);
    lay.route([p(pl.seat.x, pl.seat.y), origin]);
    [pl.stand, pl.meet, pl.meet2, pl.door].concat(pl.canteen).forEach(function (s) { walk(origin, s); });
    pl.pod.forEach(function (s) {
      walk(p(pl.door.x, pl.door.y), s);
      lay.route([p(s.x, s.y), from(s)]);
      walk(from(s), pl.handoff);
    });
  }

  /* What sits on a desk besides the monitor, keyboard and lamp: the same
     few things for the same session, every time. */
  var PROPS = ['mug', 'notebook', 'plant', 'photo', 'books', 'mug', 'plant'];
  function deskProps(sid) {
    var h = hash(sid), out = [];
    for (var i = 0; i < 3; i++) {
      var prop = PROPS[(h >>> (i * 5)) % PROPS.length];
      if (out.indexOf(prop) === -1) out.push(prop);
    }
    return out;
  }

  // -------------------------------------------------------------- walls
  /* Tall window walls at the back (y = 0) and on the left (x = 0); a low
     kerb on the open front and right edges, broken by the entrance door.
     The back wall has a clerestory band above the wall-mounted pieces;
     the left wall alternates window bays and solid panels for wall art. */
  function walls(plan, W, D, doorY) {
    var bays = [];
    for (var y = BACK + 0.2; y + 3.0 < D; y += 3.2) {
      plan.decor.push({ kind: 'art', mount: 'wall', x: 0.02, y: y + 0.6, rot: 1, h: 1.45, w: 0.9 });
      bays.push({ from: y + 1.4, to: y + 3.0, low: 0.8, high: 2.4 });
    }
    plan.walls = [
      { x1: 0, y1: 0, x2: W, y2: 0, h: WALL_H, windows: [{ from: 3.6, to: W - 0.2, low: 1.8, high: 2.45 }] },
      { x1: 0, y1: 0, x2: 0, y2: D, h: WALL_H, windows: bays },
      { x1: 0, y1: D, x2: W, y2: D, h: 0.15, windows: [] },
      { x1: W, y1: 0, x2: W, y2: doorY - 0.7, h: 0.15, windows: [] },
      { x1: W, y1: doorY + 0.7, x2: W, y2: D, h: 0.15, windows: [] }
    ];
  }

  // --------------------------------------------------------------- fill
  /* The theme's decor, on a one-tile grid inside the fill regions, wherever
     a piece touches nothing solid and keeps clear of every walk and spot.
     Seeded by the slot, so the same floor dresses the same way every poll. */
  function fill(lay, theme) {
    var plan = lay.plan;
    var decor = theme.decor;
    var kinds = Object.keys(decor.weights).filter(function (k) { return FILL_KINDS.indexOf(k) !== -1; });
    var total = kinds.reduce(function (s, k) { return s + decor.weights[k]; }, 0);
    var spots = [];
    Object.keys(plan.places).forEach(function (sid) {
      var pl = plan.places[sid];
      [pl.seat, pl.stand, pl.meet, pl.meet2, pl.handoff, pl.door].concat(pl.pod, pl.canteen).forEach(function (s) { spots.push(s); });
    });
    var floor = rect(0.05, 0.05, plan.W - 0.05, plan.D - 0.05);
    var clear = function (box) {
      if (!inside(box, floor)) return false;
      for (var i = 0; i < lay.solids.length; i++) if (overlaps(box, lay.solids[i], FILL_GAP)) return false;
      for (var j = 0; j < plan.routes.length; j++) {
        var a = plan.routes[j][0], b = plan.routes[j][1];
        if (overlaps(box, rect(a.x, a.y, b.x, b.y), ROUTE_CLEAR + 0.05)) return false;
      }
      for (var s = 0; s < spots.length; s++) {
        if (overlaps(box, rect(spots[s].x, spots[s].y, spots[s].x, spots[s].y), SPOT_CLEAR + 0.05)) return false;
      }
      return true;
    };
    var place = function (kind, gx, gy, rot, within) {
      var k = KINDS[kind], odd = rot % 2 !== 0;
      var hw = (odd ? k.d : k.w) / 2, hd = (odd ? k.w : k.d) / 2;
      var box = rect(gx - hw, gy - hd, gx + hw, gy + hd);
      if (!inside(box, within) || !clear(box)) return false;
      lay.add(kind, gx, gy, rot, { fill: true, seed: hash(gx + ',' + gy) });
      return true;
    };
    plan.regions.forEach(function (region, ri) {
      var b = region.box;
      var w = b.x1 - b.x0, d = b.y1 - b.y0;
      // a big open patch gets one centrepiece; the fill only lines its edges
      if (w >= 5 && d >= 3.4) place(decor.centrepiece, Math.round((b.x0 + b.x1) / 2), Math.round((b.y0 + b.y1) / 2), 0, b);
      for (var gy = Math.floor(b.y0) + 0.5; gy < b.y1; gy += 1) {
        for (var gx = Math.floor(b.x0) + 0.5; gx < b.x1; gx += 1) {
          if (Math.min(gx - b.x0, b.x1 - gx, gy - b.y0, b.y1 - gy) > EDGE) continue;
          var r = rng(hash(theme.key + ':' + ri + ':' + gx + ':' + gy));
          if (r() > decor.density) continue;
          var pick = r() * total, kind = kinds[0];
          for (var i = 0; i < kinds.length; i++) { pick -= decor.weights[kinds[i]]; if (pick <= 0) { kind = kinds[i]; break; } }
          if (!place(kind, gx, gy, region.rot, b)) place('plant', gx, gy, region.rot, b);
        }
      }
    });
  }

  return { build: build, KINDS: KINDS, FILL_KINDS: FILL_KINDS, POD: POD, ROUTE_CLEAR: ROUTE_CLEAR, SPOT_CLEAR: SPOT_CLEAR };
})();
