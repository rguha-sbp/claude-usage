/* The office campus (spec Sec 12.3, 12.6-12.8): where every room, desk,
   plant, canteen spot and rack stands, the pod of standing slots beside each
   desk where interns work (spec Sec 13.4), and the static SVG that draws them.

   Pure: `build(data, maxWidth)` turns the /api/office payload into a world
   of blocks and *spots*, `draw(world, sky, board, counts)` turns a world
   into markup. No DOM, no state, no fetch -- tests/office_world_check.js
   drives both under node. office.js owns the live parts: the workers, the
   frame loop and the hover card.

   Tile space: x runs back-right, y runs back-left. Screen position is
   u = x - y across and v = x + y into the picture, so a block placed at
   back corner (u, v) with footprint w x d covers u - d .. u + w across and
   v .. v + w + d in depth. Rooms are packed along u and wrap to a new row;
   the plaza and the server room take the front row, reached by a corridor
   lane that runs at a constant v in front of every office. */
window.OfficeWorld = (function () {
  'use strict';

  var TW = 92, TH = 46;         // one isometric floor tile, 2:1
  var DESK = 0.72;              // desk footprint in tiles
  var PITCH = 1.5;              // desk rows, front to back
  var PITCH_X = 2.25;           // across a room: a desk plus its pod of interns
  var POD = 4;                  // standing slots for interns beside every desk
  var AISLE = 1.45;             // extra depth in front of the last desk row
  var DESK_H = 14, BAR_H = 16, WALL_H = 34, PAD = 44;
  var GAP = 0.9;                // between blocks along the row
  var ROW_GAP = 1.6;            // between rows of rooms
  var LANE_GAP = 1.2;           // corridor distance in front of the rooms
  var PLAZA_W = 4.4, PLAZA_D = 2.8, SERVER_W = 2.0, SERVER_D = 2.0, RACKS = 3, STOOLS = 3;
  var MAX_SCALE = 1.6;

  // ----------------------------------------------------------- geometry
  function iso(x, y) { return { x: (x - y) * TW / 2, y: (x + y) * TH / 2 }; }
  function P(x, y, lift) { var p = iso(x, y); return p.x.toFixed(1) + ',' + (p.y - (lift || 0)).toFixed(1); }
  function diamond(x, y, w, d, lift) {
    return [P(x, y, lift), P(x + w, y, lift), P(x + w, y + d, lift), P(x, y + d, lift)].join(' ');
  }
  function poly(cls, points) { return '<polygon class="' + cls + '" points="' + points + '"/>'; }
  /* An upright prop standing on the floor at `p`, `lift` pixels above it
     (a desk top, a counter) with its own origin at its feet. */
  function at(p, body, cls, lift) {
    var s = iso(p.x, p.y);
    return '<g class="' + cls + '" transform="translate(' + s.x.toFixed(1) + ',' + (s.y - (lift || 0)).toFixed(1) + ')">' +
      body + '</g>';
  }
  var esc = window.UsageFormat.esc, fmtTokens = window.UsageFormat.fmtTokens;
  function truncate(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function tierOf(model) { return window.OfficeCast.tierOf(model); }

  /* A block is a footprint on the floor: back corner (u, v), w by d tiles. */
  function blockAt(u, v, w, d) {
    return { x: (u + v) / 2, y: (v - u) / 2, u: u, v: v, w: w, d: d };
  }
  function front(b) { return b.v + b.w + b.d; }            // depth of its nearest corner

  /* The point on a corridor lane (x + y = lane.at) directly out from `u`. */
  function lanePoint(lane, u) { return { x: (lane.at + u) / 2, y: (lane.at - u) / 2 }; }
  function spot(x, y, lane, via) { return { x: x, y: y, lane: lane, via: via }; }

  function deskAt(b, k) {
    var i = k % b.cols, j = Math.floor(k / b.cols);
    var dx = b.x + 0.35 + i * PITCH_X, dy = b.y + 0.35 + j * PITCH;
    var pod = [];
    for (var s = 0; s < POD; s++) {
      pod.push({ x: dx + DESK + 0.3 + (s % 2) * 0.55, y: dy + 0.15 + Math.floor(s / 2) * 0.6 });
    }
    return { x: dx, y: dy, i: i, j: j, seat: { x: dx + DESK * 0.5, y: dy + DESK + 0.14 }, pod: pod };
  }

  // -------------------------------------------------------------- build
  function build(data, maxWidth) {
    var maxU = Math.max(9, (maxWidth || 900) / (TW / 2));
    var blocks = [], u = 0, v = 0, rowDepth = 0;
    data.rooms.forEach(function (room) {
      var n = room.workers.length;
      var cols = Math.max(1, Math.ceil(Math.sqrt(n)));
      var rows = Math.max(1, Math.ceil(n / cols));
      var w = cols * PITCH_X;
      var d = (rows - 1) * PITCH + DESK + 0.6 + AISLE;
      if (blocks.length && u + d + w > maxU) { v += rowDepth + ROW_GAP; u = 0; rowDepth = 0; }
      var b = blockAt(u + d, v, w, d);
      b.room = room; b.cols = cols; b.rows = rows;
      b.aisleY = b.y + d - 0.5;
      b.cooler = { x: b.x + w - 0.4, y: b.aisleY + 0.1 };
      b.plant = { x: b.x + 0.3, y: b.aisleY + 0.1 };
      b.doorX = b.x + w + 0.45;
      blocks.push(b);
      u += d + w + GAP;
      rowDepth = Math.max(rowDepth, w + d);
    });

    var spanU = Math.max(u - GAP, 9);
    var deepest = blocks.length ? Math.max.apply(null, blocks.map(front)) : 2;
    var lane = { axis: 'v', at: deepest + LANE_GAP };
    /* The plaza and the server room take the front row, the plaza centred
       under the offices. A block spans u - d .. u + w across the screen, so
       its middle sits at u + (w - d) / 2 -- that is what gets centred. */
    var plazaV = lane.at + 0.5;
    var plazaU = Math.max(PLAZA_D, spanU / 2 - (PLAZA_W - PLAZA_D) / 2);
    var plaza = blockAt(plazaU, plazaV, PLAZA_W, PLAZA_D);
    var server = blockAt(plazaU + PLAZA_W + GAP + SERVER_D, plazaV, SERVER_W, SERVER_D);

    // the canteen: a counter along the plaza's back edge with standing room
    plaza.canteen = [];
    for (var i = 0; i < STOOLS; i++) {
      plaza.canteen.push({ x: plaza.x + 0.55 + i * 0.75, y: plaza.y + PLAZA_D - 1.2 });
    }
    plaza.board = { x: plaza.x + PLAZA_W - 1.0, y: plaza.y + 0.35 };   // clear of the coffee bar
    plaza.plants = [{ x: plaza.x + 0.15, y: plaza.y + PLAZA_D - 0.3 },
                    { x: plaza.x + PLAZA_W - 0.25, y: plaza.y + PLAZA_D - 0.35 }];
    plaza.benches = [{ x: plaza.x + PLAZA_W * 0.42, y: plaza.y + PLAZA_D - 0.4 }];
    server.racks = RACKS;

    var places = {};
    blocks.forEach(function (b, r) {
      var roomLane = { axis: 'y', at: b.aisleY };
      var aisle = function (x) { return { x: x, y: b.aisleY }; };
      var door = aisle(b.doorX);
      var onLane = lanePoint(lane, door.x - door.y);
      var inRoom = function (p) { return spot(p.x, p.y, roomLane, [aisle(p.x)]); };
      var toPlaza = function (p) { return spot(p.x, p.y, lane, [door, onLane, lanePoint(lane, p.x - p.y)]); };
      b.room.workers.forEach(function (w, k) {
        var dk = deskAt(b, k);
        places[w.session_id] = {
          room: r, row: dk.j, aisleY: b.aisleY,
          seat: inRoom(dk.seat),
          cooler: inRoom(b.cooler),
          stand: inRoom({ x: b.cooler.x - 0.55, y: b.aisleY }),
          meet: inRoom({ x: b.x + b.w * 0.42, y: b.aisleY }),
          meet2: inRoom({ x: b.x + b.w * 0.42 + 0.6, y: b.aisleY }),
          pod: dk.pod.map(function (p) { return spot(p.x, p.y, roomLane, [door, aisle(p.x)]); }),
          handoff: inRoom({ x: dk.x + DESK + 0.15, y: dk.y + DESK + 0.1 }),
          door: spot(onLane.x, onLane.y, lane, [door]),
          canteen: plaza.canteen.map(toPlaza)
        };
      });
    });
    return { blocks: blocks, plaza: plaza, server: server, lane: lane, places: places, maxWidth: maxWidth };
  }

  // ------------------------------------------------------------ statics
  function walls(b, cls) {
    return poly(cls + ' wall-l', [P(b.x, b.y), P(b.x, b.y + b.d), P(b.x, b.y + b.d, WALL_H), P(b.x, b.y, WALL_H)].join(' ')) +
      poly(cls + ' wall-r', [P(b.x, b.y), P(b.x + b.w, b.y), P(b.x + b.w, b.y, WALL_H), P(b.x, b.y, WALL_H)].join(' '));
  }

  function nameplate(b, text, lift) {
    var c = iso(b.x, b.y);
    return '<text class="room-name" x="' + c.x.toFixed(1) + '" y="' + (c.y - (lift || WALL_H) - 8).toFixed(1) +
      '" text-anchor="middle">' + esc(truncate(text, 28)) + '</text>';
  }

  function drawRoom(b) {
    return poly('floor', diamond(b.x, b.y, b.w, b.d)) + walls(b, '') +
      // doorway: a lighter strip on the front-right floor edge where the aisle meets it
      poly('door', [P(b.x + b.w, b.aisleY - 0.3), P(b.x + b.w + 0.12, b.aisleY - 0.3),
                    P(b.x + b.w + 0.12, b.aisleY + 0.3), P(b.x + b.w, b.aisleY + 0.3)].join(' '));
  }

  function drawDesk(dk, worker, lamps) {
    var x = dk.x, y = dk.y, tier = tierOf(worker.model);
    var working = worker.state === 'working';
    var out = '';
    out += poly('desk-r', [P(x + DESK, y), P(x + DESK, y + DESK), P(x + DESK, y + DESK, DESK_H), P(x + DESK, y, DESK_H)].join(' '));
    out += poly('desk-l', [P(x, y + DESK), P(x + DESK, y + DESK), P(x + DESK, y + DESK, DESK_H), P(x, y + DESK, DESK_H)].join(' '));
    out += poly('desk-top' + (tier.chair === 'gold' ? ' rich' : ''), diamond(x, y, DESK, DESK, DESK_H));
    var m = iso(x + DESK * 0.5, y + DESK * 0.18);
    var mx = m.x, my = m.y - DESK_H;
    out += '<rect class="monitor" x="' + (mx - 9).toFixed(1) + '" y="' + (my - 16).toFixed(1) + '" width="18" height="12" rx="1.5"/>' +
      '<rect class="screen' + (working ? ' on' : '') + '" x="' + (mx - 7.5).toFixed(1) + '" y="' + (my - 14.5).toFixed(1) + '" width="15" height="9" rx="1"/>' +
      '<rect class="monitor" x="' + (mx - 1).toFixed(1) + '" y="' + (my - 4).toFixed(1) + '" width="2" height="4"/>';
    if (working) {
      out += at({ x: x + DESK * 0.82, y: y + DESK * 0.55 },
        '<rect x="-3" y="-6" width="6" height="6" rx="1"/><path d="M3 -4.5 q3 0 3 1.5 q0 1.5 -3 1.5" fill="none"/>', 'mug', DESK_H);
    }
    if (tier.ornament) out += ornament(tier.ornament, { x: x + DESK * 0.18, y: y + DESK * 0.6 });
    if (lamps) out += lamp({ x: x + DESK * 0.15, y: y + DESK * 0.2 });
    // chair behind the seat
    out += at({ x: dk.seat.x, y: dk.seat.y + 0.1 },
      '<rect x="-10" y="-4" width="20" height="7" rx="3"/><rect x="-8" y="-20" width="16" height="17" rx="4"/>' +
      '<rect x="-1.5" y="3" width="3" height="5"/><rect x="-9" y="7" width="18" height="2" rx="1"/>',
      'chair ' + tier.chair);
    return out;
  }

  /* Desk ornaments: what the two richest tiers get to keep on the desk. */
  function ornament(kind, p) {
    var body = kind === 'trophy'
      ? '<path class="gold" d="M-3.5 -20 h7 l-1 6 h-5 Z"/><path class="gold" d="M-1.2 -14 h2.4 v3 h-2.4 Z"/>' +
        '<rect class="plinth" x="-3.5" y="-11" width="7" height="3" rx="1"/>' +
        '<path class="gold" d="M-3.5 -20 q-3 1 -1.5 4 M3.5 -20 q3 1 1.5 4" fill="none"/>'
      : '<path class="stem" d="M0 -8 q-1 -6 1 -11" fill="none"/>' +
        '<ellipse class="bloom" cx="1" cy="-19" rx="2.6" ry="3.2"/><ellipse class="bloom" cx="-2" cy="-16" rx="2.2" ry="2.6"/>' +
        '<path class="pot" d="M-3 -8 h6 l-1 6 h-4 Z"/>';
    return at(p, body, 'ornament ' + kind, DESK_H);
  }

  function lamp(p) {
    return at({ x: p.x, y: p.y },
      '<circle class="pool" cx="0" cy="-4" r="13"/><rect class="stalk" x="-0.8" y="-16" width="1.6" height="16"/>' +
      '<path class="shade" d="M-5 -16 h10 l-2.5 5 h-5 Z"/>', 'lamp', DESK_H);
  }

  function drawPlant(p) {
    return at(p, '<path class="pot" d="M-6 0 L6 0 L4.5 9 L-4.5 9 Z"/>' +
      '<ellipse class="leaf" cx="-5" cy="-8" rx="4.5" ry="8" transform="rotate(-28 -5 -8)"/>' +
      '<ellipse class="leaf" cx="5" cy="-8" rx="4.5" ry="8" transform="rotate(28 5 -8)"/>' +
      '<ellipse class="leaf" cx="0" cy="-11" rx="4" ry="9"/>', 'plant');
  }

  function drawCooler(p) {
    return at(p, '<rect class="base" x="-7" y="-22" width="14" height="22" rx="2"/>' +
      '<rect class="jug" x="-6" y="-38" width="12" height="15" rx="4"/>' +
      '<rect class="tap" x="-2" y="-16" width="4" height="3" rx="1"/>', 'cooler');
  }

  function drawLabel(worker, seat) {
    var c = iso(seat.x, seat.y);
    var extra = (worker.agents || []).filter(function (sub) { return sub.state !== 'done'; }).length - POD;
    return '<text class="label" x="' + c.x.toFixed(1) + '" y="' + (c.y + 22).toFixed(1) + '" text-anchor="middle">' +
      esc(truncate(worker.title, 16)) + '</text>' +
      (extra > 0 ? '<text class="pod-more" x="' + (c.x + 40).toFixed(1) + '" y="' + (c.y + 22).toFixed(1) +
        '" text-anchor="middle">+' + extra + '</text>' : '');
  }

  // --------------------------------------------------------- the plaza
  function money(v) { return '$' + Number(v || 0).toFixed(2); }

  function drawPlaza(plaza, board, counts, lamps) {
    var out = poly('plaza-floor', diamond(plaza.x, plaza.y, plaza.w, plaza.d)) + walls(plaza, 'plaza-');
    out += drawBar(plaza);
    out += drawBoard(plaza.board, board, counts);
    plaza.benches.forEach(function (p) {
      out += at(p, '<rect class="seat" x="-13" y="-6" width="26" height="5" rx="2"/>' +
        '<rect class="leg" x="-11" y="-1" width="3" height="5"/><rect class="leg" x="8" y="-1" width="3" height="5"/>', 'bench');
    });
    plaza.plants.forEach(function (p) { out += drawPlant(p); });
    if (lamps) out += at({ x: plaza.x + plaza.w * 0.5, y: plaza.y + plaza.d - 0.2 },
      '<circle class="pool" cx="0" cy="-30" r="22"/><rect class="post" x="-1.5" y="-44" width="3" height="44"/>' +
      '<circle class="bulb" cx="0" cy="-46" r="4"/>', 'street-lamp');
    return '<g class="plaza">' + out + '</g>';
  }

  function drawBar(plaza) {
    var bx = plaza.x + 0.3, by = plaza.y + 0.35, bw = Math.min(1.9, plaza.w - 1.4), bd = 0.45;
    var out = poly('bar-r', [P(bx + bw, by), P(bx + bw, by + bd), P(bx + bw, by + bd, BAR_H), P(bx + bw, by, BAR_H)].join(' ')) +
      poly('bar-l', [P(bx, by + bd), P(bx + bw, by + bd), P(bx + bw, by + bd, BAR_H), P(bx, by + bd, BAR_H)].join(' ')) +
      poly('bar-top', diamond(bx, by, bw, bd, BAR_H));
    out += at({ x: bx + 0.35, y: by + 0.2 },
      '<rect class="body" x="-6" y="-14" width="12" height="14" rx="2"/><rect class="spout" x="-2" y="-6" width="4" height="3"/>' +
      '<rect class="steam" x="-1" y="-20" width="2" height="5" rx="1"/>', 'machine', BAR_H);
    out += at({ x: bx + bw - 0.4, y: by + 0.2 },
      '<rect class="cup" x="-3" y="-5" width="5" height="5" rx="1"/><rect class="cup" x="3" y="-5" width="5" height="5" rx="1"/>', 'cups', BAR_H);
    return '<g class="canteen">' + out + '</g>';
  }

  /* The notice board carries the day's real numbers, straight from the
     server (spec Sec 12.7). */
  function drawBoard(p, board, counts) {
    board = board || {};
    var value = board.unit === 'usd' ? money(board.value) : fmtTokens(board.value) + ' tokens';
    var top = board.top_project ? truncate(board.top_project, 16) : 'nobody yet';
    var awake = counts.working + ' working · ' + Math.max(0, counts.total - counts.working) + ' asleep';
    return at(p,
      '<rect class="panel" x="-52" y="-66" width="104" height="54" rx="4"/>' +
      '<rect class="post" x="-2" y="-14" width="4" height="14"/>' +
      '<text class="title" x="0" y="-54" text-anchor="middle">TODAY</text>' +
      '<text class="big" x="0" y="-40" text-anchor="middle">' + esc(value) + '</text>' +
      '<text class="line" x="0" y="-28" text-anchor="middle">busiest: ' + esc(top) + '</text>' +
      '<text class="line" x="0" y="-17" text-anchor="middle">' + esc(awake) + '</text>', 'board');
  }

  /* The machine room: racks whose lights run faster while sessions work. */
  function drawServer(server, counts) {
    var out = poly('server-floor', diamond(server.x, server.y, server.w, server.d)) + walls(server, 'server-');
    for (var i = 0; i < server.racks; i++) {
      var p = { x: server.x + 0.45 + i * 0.5, y: server.y + 0.5 };
      var lights = '';
      for (var j = 0; j < 4; j++) {
        lights += '<rect class="light l' + j + '" x="-5" y="' + (-26 + j * 6) + '" width="10" height="2.5" rx="1"/>';
      }
      out += at(p, '<rect class="case" x="-7" y="-32" width="14" height="32" rx="2"/>' + lights, 'rack');
    }
    return '<g class="server' + (counts.working ? ' busy' : '') + '">' + out + '</g>';
  }

  // --------------------------------------------------------------- draw
  function bounds(world) {
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    function add(x, y, lift) {
      var p = iso(x, y); p.y -= lift || 0;
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    world.blocks.concat([world.plaza, world.server]).forEach(function (b) {
      add(b.x, b.y, WALL_H + 22);
      add(b.x + b.w, b.y, WALL_H);
      add(b.x, b.y + b.d, WALL_H);
      add(b.x + b.w + 0.5, b.y + b.d, -34);
    });
    return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
  }

  function stars(box, rng) {
    var out = '';
    for (var i = 0; i < 46; i++) {
      var x = box.minX + rng() * (box.maxX - box.minX);
      var y = box.minY + rng() * (box.maxY - box.minY) * 0.55;
      out += '<circle class="star s' + (i % 3) + '" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) +
        '" r="' + (0.7 + rng() * 1.1).toFixed(2) + '"/>';
    }
    return out;
  }

  function seededRng(seed) {
    var t = seed >>> 0;
    return function () {
      t = (t + 0x6D2B79F5) >>> 0;
      var r = Math.imul(t ^ (t >>> 15), 1 | t);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  function draw(world, sky, board, counts) {
    var box = bounds(world);
    var w = box.maxX - box.minX + PAD * 2, h = box.maxY - box.minY + PAD * 2;
    var view = { x: box.minX - PAD, y: box.minY - PAD, w: w, h: h };
    var body = '<defs><linearGradient id="office-sky" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + sky.top + '"/><stop offset="100%" stop-color="' + sky.bottom + '"/>' +
      '</linearGradient></defs>';
    body += '<rect class="sky" x="' + view.x.toFixed(1) + '" y="' + view.y.toFixed(1) + '" width="' + w.toFixed(1) +
      '" height="' + h.toFixed(1) + '" fill="url(#office-sky)"/>';
    if (sky.stars) {
      body += '<g class="sky-stars">' +
        stars({ minX: view.x, minY: view.y, maxX: view.x + w, maxY: view.y + h }, seededRng(9176)) + '</g>';
    }

    var labels = '';
    var rooms = '';
    world.blocks.forEach(function (b, r) {
      rooms += '<g class="room">' + drawRoom(b);
      for (var j = 0; j < b.rows; j++) {
        var row = '';
        b.room.workers.forEach(function (worker, k) {
          var dk = deskAt(b, k);
          if (dk.j !== j) return;
          row += drawDesk(dk, worker, sky.lamps);
          labels += drawLabel(worker, dk.seat);
        });
        rooms += '<g class="desks">' + row + '</g><g class="seated" data-layer="' + r + ':' + j + '"></g>';
      }
      // furniture stands in the aisle, nearer the viewer than every desk
      rooms += '<g class="furniture">' + drawPlant(b.plant) + drawCooler(b.cooler) + '</g></g>';
      labels += nameplate(b, b.room.project);
    });
    // name plates sit in the overlay, outside the lighting, so they stay
    // readable after dark
    labels += nameplate(world.plaza, 'the plaza') + nameplate(world.server, 'server room');

    body += '<g class="lit" style="--lit:' + sky.light + '">' + rooms +
      drawPlaza(world.plaza, board, counts, sky.lamps) + drawServer(world.server, counts) +
      '<g class="tethers"></g><g class="mobile"></g></g><g class="labels">' + labels + '</g>';

    var scale = Math.min(MAX_SCALE, (world.maxWidth || w) / w);
    return { svg: body, viewBox: [view.x.toFixed(1), view.y.toFixed(1), w.toFixed(1), h.toFixed(1)].join(' '),
             width: w * scale, height: h * scale, bounds: box };
  }

  return { build: build, draw: draw, iso: iso, deskAt: deskAt, TW: TW, TH: TH, POD: POD };
})();
