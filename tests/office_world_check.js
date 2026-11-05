// Drives dashboard/office-world.js without a browser: the layout and the
// static scene are pure functions of the payload, so node can check the
// geometry and the markup. Exits non-zero on a broken expectation.
// Run by tests/test_office_sim.py.
'use strict';
global.window = {};
require('../dashboard/format.js');
require('../dashboard/office-cast.js');
require('../dashboard/office-world.js');
var W = window.OfficeWorld;

function expect(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exitCode = 1; } }

function worker(id, model, agents) {
  return { session_id: id, title: 'S ' + id, model: model, state: 'asleep', agents: agents || [], agents_hidden: 0 };
}
function sub(id, state) { return { agent_id: id, type: 'Explore', state: state, parent: null, model: 'claude-haiku-4-5' }; }
var crowd = ['1', '2', '3', '4', '5', '6'].map(function (k) { return sub('x' + k, 'running'); }).concat([sub('d', 'done')]);
var data = {
  generated_at: '2026-09-16T10:00:00+00:00',
  rooms: [
    { project: 'usage', cwd: '/x/usage', workers: [worker('a', 'Opus 5', crowd), worker('b', 'Sonnet 5'), worker('c', 'Haiku 4.5')] },
    { project: 'other', cwd: '/x/other', workers: [worker('d', 'Fable 5.1')] }
  ],
  board: { day: '2026-09-16', unit: 'usd', value: 12.5, tokens: 99, top_project: 'usage', top_value: 9.25 }
};

var world = W.build(data, 1200);
expect(world.blocks.length === 2, 'one block per project');
expect(Object.keys(world.places).length === 4, 'every worker gets a place');
expect(world.plaza && world.plaza.canteen.length >= 2, 'the plaza has standing room at the canteen');
expect(world.server && world.server.racks > 0, 'the server room has racks');

// rooms may not overlap: along the screen (u = x - y) or in depth (v = x + y)
function span(b) {
  return { u: [b.x - b.y - b.d, b.x - b.y + b.w], v: [b.x + b.y, b.x + b.y + b.w + b.d] };
}
var all = world.blocks.concat([world.plaza, world.server]);
for (var i = 0; i < all.length; i++) {
  for (var j = i + 1; j < all.length; j++) {
    var A = span(all[i]), B = span(all[j]);
    var clearU = A.u[1] <= B.u[0] + 1e-9 || B.u[1] <= A.u[0] + 1e-9;
    var clearV = A.v[1] <= B.v[0] + 1e-9 || B.v[1] <= A.v[0] + 1e-9;
    expect(clearU || clearV, 'blocks ' + i + ' and ' + j + ' do not overlap');
  }
}
var frontOfRooms = Math.max.apply(null, world.blocks.map(function (b) { return span(b).v[1]; }));
expect(span(world.plaza).v[0] >= frontOfRooms, 'the plaza sits in front of the offices');
expect(world.lane.axis === 'v' && world.lane.at > 0, 'a corridor lane runs in front of the rooms');

var places = world.places.a;
expect(places.seat.lane.axis === 'y' && places.seat.via.length === 1, 'a desk is reached along the room aisle');
expect(places.canteen.length === world.plaza.canteen.length, 'every worker can reach every canteen spot');
var spot = places.canteen[0];
expect(spot.lane === world.lane, 'a canteen spot stands on the corridor lane');
expect(spot.via.length >= 3, 'the canteen route goes aisle -> door -> corridor');
var onLane = spot.via[spot.via.length - 1];
expect(Math.abs(onLane.x + onLane.y - world.lane.at) < 1e-6, 'the last waypoint is on the corridor');
expect(places.door.lane === world.lane, 'leaving puts a worker on the corridor');
var inside = world.plaza.canteen.every(function (s) {
  return s.x >= world.plaza.x && s.x <= world.plaza.x + world.plaza.w &&
         s.y >= world.plaza.y && s.y <= world.plaza.y + world.plaza.d;
});
expect(inside, 'the canteen spots are inside the plaza');

// pods: four standing slots beside every desk, clear of every desk and of each other
var deskRects = [];
world.blocks.forEach(function (b) {
  b.room.workers.forEach(function (w, k) { var dk = W.deskAt(b, k); deskRects.push({ x: dk.x, y: dk.y, b: b }); });
});
var podOk = true, podInside = true;
world.blocks.forEach(function (b) {
  b.room.workers.forEach(function (w) {
    var pod = world.places[w.session_id].pod;
    if (pod.length !== W.POD) podOk = false;
    pod.forEach(function (p, i) {
      deskRects.forEach(function (d) {
        if (p.x > d.x - 0.15 && p.x < d.x + 0.72 + 0.15 && p.y > d.y - 0.15 && p.y < d.y + 0.72 + 0.15) podOk = false;
      });
      pod.forEach(function (q, j) { if (i !== j && Math.hypot(p.x - q.x, p.y - q.y) < 0.45) podOk = false; });
      if (p.x < b.x || p.x > b.x + b.w || p.y < b.y || p.y > b.y + b.d) podInside = false;
    });
  });
});
expect(podOk, 'pod slots stand clear of every desk and of each other');
expect(podInside, 'pod slots are inside their room');
var pa = world.places.a;
expect(pa.pod[0].via[0].x === pa.door.via[0].x && pa.pod[0].lane.axis === 'y', 'an intern walks in through the door to its slot');
expect(pa.handoff && pa.handoff.lane.axis === 'y', 'there is a hand-off spot at the desk');

var scene = W.draw(world, window.OfficeCast.skyOf(22), data.board, { total: 4, working: 1 });
expect(typeof scene.svg === 'string' && scene.svg.length > 500, 'the scene is drawn');
expect(scene.svg.indexOf('NaN') === -1 && scene.svg.indexOf('undefined') === -1, 'no NaN or undefined in the markup');
['plaza', 'rack', 'board', 'canteen', 'room-name', 'desk-top', 'seated', 'star', 'lamp'].forEach(function (cls) {
  expect(scene.svg.indexOf(cls) !== -1, 'the scene contains ' + cls);
});
expect(scene.svg.indexOf('usage') !== -1 && scene.svg.indexOf('$12.50') !== -1, 'the board shows the day and the busiest project');
expect(scene.width > 0 && scene.height > 0 && scene.viewBox.split(' ').length === 4, 'the scene has a viewBox');
expect(scene.svg.indexOf('class="tethers"') !== -1 && scene.svg.indexOf('class="tethers"') < scene.svg.indexOf('class="mobile"'),
  'a tether layer sits under the walkers');
expect(scene.svg.indexOf('class="pod-more"') !== -1 && scene.svg.indexOf('>+2<') !== -1, 'six running interns: four at the pod, +2 on the plate');
var noStars = W.draw(world, window.OfficeCast.skyOf(12), data.board, { total: 4, working: 1 });
expect(noStars.svg.indexOf('star') === -1, 'no stars at midday');

// --- the simulation over the real geometry, not a hand-written fixture ---
require('../dashboard/office-sim.js');
var sim = new window.OfficeSim.Sim({ wander: true });
var live = [];
data.rooms.forEach(function (room) {          // only working leads take breaks
  room.workers.forEach(function (w) { live.push(Object.assign({}, w, { state: 'working' })); });
});
sim.sync(live, world.places);
var reachedCanteen = false, strayed = null, dt = 1 / 60;
var seats = {};
live.forEach(function (w) { seats[w.session_id] = world.places[w.session_id].seat; });
for (var t = 0; t < 300 * 60; t++) {
  if (t === 240 * 60) {                        // everyone falls asleep: breaks end at the desk
    sim.sync(live.map(function (w) { return Object.assign({}, w, { state: 'asleep' }); }), world.places);
  }
  sim.tick(dt);
  sim.list().forEach(function (ag) {
    if (ag.state.pose === 'lounging') {
      reachedCanteen = world.plaza.canteen.some(function (s) {
        return Math.hypot(ag.pos.x - s.x, ag.pos.y - s.y) < 0.1;
      }) || reachedCanteen;
    }
    var u = ag.pos.x - ag.pos.y, v = ag.pos.x + ag.pos.y;
    if (u < -4 || u > 60 || v < -4 || v > world.lane.at + 8) strayed = ag.id + ' at ' + u.toFixed(1) + ',' + v.toFixed(1);
  });
}
var home = sim.list().filter(function (ag) {
  var seat = seats[ag.id];
  return ag.state.pose === 'dozing' && Math.hypot(ag.pos.x - seat.x, ag.pos.y - seat.y) < 0.2;
});
expect(reachedCanteen, 'a worker reaches a canteen spot on the real campus');
expect(strayed === null, 'nobody walks off the campus (' + strayed + ')');
expect(home.length > 0, 'workers find their own desk again');

console.log(JSON.stringify({ blocks: world.blocks.length, canteen: world.plaza.canteen.length,
                             width: Math.round(scene.width), height: Math.round(scene.height),
                             reachedCanteen: reachedCanteen, podOk: podOk, home: home.length }));
