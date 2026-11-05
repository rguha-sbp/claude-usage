// Drives dashboard/office-sim.js without a browser: fake `window`, seat
// sleepers and workers in two rooms plus a session that goes away, then tick
// 240 simulated seconds and report what happened. Exits non-zero on any
// broken expectation. Run by tests/test_office_sim.py.
'use strict';
global.window = {};
require('../dashboard/office-cast.js');
require('../dashboard/office-sim.js');
var Sim = window.OfficeSim.Sim;

var AISLE = 3;
var CORRIDOR = { axis: 'v', at: 12 };          // the lane in front of every room
var DOOR = { x: 5, y: AISLE };

function inRoom(x, y) { return { x: x, y: y, lane: { axis: 'y', at: AISLE }, via: [{ x: x, y: AISLE }] }; }
function outside(x, y) {                       // desk -> door -> corridor -> spot
  return { x: x, y: y, lane: CORRIDOR, via: [DOOR, { x: 6.5, y: 6.5 }, { x: x, y: y - 1 }] };
}
var CANTEEN = [outside(7, 8), outside(8.2, 8)];

function places(seat, room) {
  return { room: room, row: 0, aisleY: AISLE, seat: inRoom(seat.x, seat.y),
           cooler: inRoom(4, 3), stand: inRoom(3.5, 3), door: outside(5.6, 5.6),
           meet: inRoom(2, 3), meet2: inRoom(2.6, 3), canteen: CANTEEN };
}
var P = { a: places({ x: 1, y: 1 }, 0), b: places({ x: 2.5, y: 1 }, 0), c: places({ x: 1, y: 2.5 }, 0),
          d: places({ x: 1, y: 1 }, 1), e: places({ x: 2.5, y: 1 }, 1) };
var workers = [
  { session_id: 'a', state: 'asleep', model: 'Opus 5' }, { session_id: 'b', state: 'asleep', model: 'Sonnet 5' },
  { session_id: 'c', state: 'working', model: 'Sonnet 5' }, { session_id: 'd', state: 'asleep', model: 'Fable 5.1' },
  { session_id: 'e', state: 'asleep', model: 'Haiku 4.5' }];

var sim = new Sim({ wander: true });
sim.sync(workers, P);
var seen = { a: {}, b: {}, c: {}, d: {}, e: {} }, top = { a: 0, b: 0, c: 0, d: 0, e: 0 };
var maxSpeed = 0, t = 0, dt = 1 / 60;
var minDist = Infinity, doorPass = Infinity, loungeBubble = false, crossProject = false, spotDist = Infinity;
for (var i = 0; i < 240 * 60; i++) {
  t += dt;
  if (Math.abs(t - 60) < dt / 2) sim.sync(workers.filter(function (w) { return w.session_id !== 'd'; }), P);  // d's session ends
  if (Math.abs(t - 150) < dt / 2) { workers[0].state = 'working'; sim.sync(workers.filter(function (w) { return w.session_id !== 'd'; }), P); }
  sim.tick(dt);
  var lounging = [];
  sim.list().forEach(function (ag) {
    seen[ag.id][ag.state.pose] = (seen[ag.id][ag.state.pose] || 0) + 1;
    var v = Math.hypot(ag.vel.x, ag.vel.y);
    if (v > maxSpeed) maxSpeed = v;
    if (v > top[ag.id]) top[ag.id] = v;
    if (ag.state.pose === 'chatting') {
      var partner = ag.state.partner;
      minDist = Math.min(minDist, Math.hypot(ag.pos.x - partner.pos.x, ag.pos.y - partner.pos.y));
    }
    if (ag.state.pose === 'lounging') {
      lounging.push(ag);
      if (ag.bubble) loungeBubble = true;
      spotDist = Math.min(spotDist, Math.min.apply(null, CANTEEN.map(function (s) {
        return Math.hypot(ag.pos.x - s.x, ag.pos.y - s.y);
      })));
      doorPass = Math.min(doorPass, 0);        // reaching the canteen at all means the route ran
    }
    if (ag.state.pose === 'walking') doorPass = Math.min(doorPass, Math.hypot(ag.pos.x - DOOR.x, ag.pos.y - DOOR.y));
  });
  if (lounging.length > 1 && lounging[0].places.room !== lounging[1].places.room) crossProject = true;
}
var out = { seen: seen, maxSpeed: +maxSpeed.toFixed(2), top: top,
            agentsLeft: sim.list().map(function (a) { return a.id; }).sort(),
            chatMinDist: minDist === Infinity ? null : +minDist.toFixed(2),
            canteenSpotDist: spotDist === Infinity ? null : +spotDist.toFixed(2),
            crossProject: crossProject, loungeBubble: loungeBubble,
            aBackAtSeat: (function () { var a = sim.agents.a; return a ? Math.hypot(a.pos.x - 1, a.pos.y - 1) < 0.2 : null; })(),
            aPose: sim.agents.a ? sim.agents.a.state.pose : null };

function expect(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exitCode = 1; } }
expect(seen.c.typing > 0 && seen.c.thinking > 0, 'a working session types and thinks');
expect(seen.c.walking === undefined, 'a working session never leaves its desk');
expect((seen.a.walking || 0) > 0 && (seen.b.walking || 0) > 0, 'sleepers get up for errands');
expect((seen.a.chatting || 0) + (seen.a.drinking || 0) + (seen.a.lounging || 0) > 0, 'an errand is a chat, a drink or the canteen');
expect((seen.b.lounging || 0) + (seen.e.lounging || 0) > 0, 'somebody walks out to the canteen');
expect(out.canteenSpotDist !== null && out.canteenSpotDist < 0.2, 'a lounger stands on a canteen spot');
expect(out.crossProject, 'workers from different projects meet at the canteen');
expect(out.loungeBubble, 'the canteen has conversation');
expect(out.agentsLeft.indexOf('d') === -1 && (seen.d.walking || 0) > 0, 'a gone session walks out and is removed');
expect(maxSpeed <= 2.35, 'steering never exceeds the fastest tier');
expect(top.e > top.b + 0.15, 'the swift tier really is quicker');
expect(out.aPose === 'typing' || out.aPose === 'thinking' || out.aPose === 'walking', 'a sleeper called back to work heads home and types (pose: ' + out.aPose + ')');
if (out.chatMinDist !== null) expect(out.chatMinDist < 1.0, 'chat partners stand together');

// --- interns (spec Sec 13.4) ---------------------------------------------
function podPlaces(seat) {
  var p = places(seat, 2);
  p.pod = [0, 1, 2, 3].map(function (s) { return outsideToRoom(seat.x + 1 + (s % 2) * 0.55, seat.y - 0.7 + Math.floor(s / 2) * 0.6); });
  p.handoff = inRoom(seat.x + 0.5, seat.y);
  return p;
}
function outsideToRoom(x, y) { return { x: x, y: y, lane: { axis: 'y', at: AISLE }, via: [DOOR, { x: x, y: AISLE }] }; }
function agentRec(id, state, parent) { return { agent_id: id, type: 'Explore', model: 'Haiku 4.5', state: state, parent: parent || null, background: false }; }

var isim = new Sim({ wander: true });
var IP = { L: podPlaces({ x: 1, y: 1 }) };
var lead = { session_id: 'L', state: 'asleep', model: 'Opus 5', agents: [agentRec('pre', 'running')] };
isim.sync([lead], IP);
var preAtSlot = Math.hypot(isim.agents['L/pre'].pos.x - IP.L.pod[0].x, isim.agents['L/pre'].pos.y - IP.L.pod[0].y) < 1e-6;
var emptied = false, leadStays = true, arrived = false, handed = false, left = false, overflow = null, nestedParent = null;
var it = 0;
for (var s2 = 0; s2 < 120 * 60; s2++) {
  it += dt;
  if (Math.abs(it - 2) < dt / 2) {                         // a second agent spawns, and a nested one under it
    lead.agents = [agentRec('pre', 'running'), agentRec('new', 'running'), agentRec('kid', 'running', 'new')];
    isim.sync([lead], IP);
    nestedParent = isim.agents['L/kid'] && isim.agents['L/kid'].parentId;
  }
  if (Math.abs(it - 30) < dt / 2) {                        // 'new' finishes; three more crowd the pod
    lead.agents = [agentRec('pre', 'running'), agentRec('new', 'done'), agentRec('kid', 'running', 'new'),
                   agentRec('m1', 'running'), agentRec('m2', 'running'), agentRec('m3', 'running')];
    isim.sync([lead], IP);
    overflow = Object.keys(isim.agents).filter(function (id) { return id.indexOf('L/') === 0 && isim.agents[id].server !== 'done'; }).length;
  }
  if (Math.abs(it - 80) < dt / 2) { lead.agents = []; isim.sync([lead], IP); emptied = true; }       // every agent gone
  isim.tick(dt);
  var L = isim.agents.L;
  if (!emptied && (L.state.pose === 'walking' || L.state.pose === 'dozing')) leadStays = false;
  var n = isim.agents['L/new'];
  if (n && n.state.pose === 'standing' && Math.hypot(n.pos.x - IP.L.pod[n.slot].x, n.pos.y - IP.L.pod[n.slot].y) < 0.1) arrived = true;
  if (n && n.state.pose === 'handing') handed = true;
  if (handed && !isim.agents['L/new']) left = true;
}
expect(preAtSlot, 'an intern present at the first sync starts in its slot');
expect(arrived, 'a new intern walks in to a pod slot');
expect(nestedParent === 'L/new', 'a nested intern is tethered to the intern that spawned it');
expect(handed && left, 'a done intern hands back at the desk and walks out');
expect(leadStays, 'a lead with running interns never leaves its desk');
expect(overflow === 4, 'the pod holds four; the rest wait in the rail (' + overflow + ')');
expect(Object.keys(isim.agents).filter(function (id) { return id.indexOf('L/') === 0; }).length === 0, 'interns leave when the payload drops them');
out.interns = { preAtSlot: preAtSlot, arrived: arrived, handed: handed, left: left,
                leadStays: leadStays, overflow: overflow, nestedParent: nestedParent };
console.log(JSON.stringify(out));
