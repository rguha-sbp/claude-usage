// Drives dashboard/office-sim.js without a browser: fake `window`, seat
// workers and sleepers in two rooms plus a session that goes away, then tick
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
  { session_id: 'a', state: 'asleep', model: 'Opus 5' }, { session_id: 'b', state: 'working', model: 'Sonnet 5' },
  { session_id: 'c', state: 'working', model: 'Sonnet 5' }, { session_id: 'd', state: 'asleep', model: 'Fable 5.1' },
  { session_id: 'e', state: 'working', model: 'Haiku 4.5' }];
function live() { return workers.filter(function (w) { return w.session_id !== 'd'; }); }

var sim = new Sim({ wander: true });
sim.sync(workers, P);
var seen = { a: {}, b: {}, c: {}, d: {}, e: {} }, top = { a: 0, b: 0, c: 0, d: 0, e: 0 };
var maxSpeed = 0, t = 0, dt = 1 / 60;
var minDist = Infinity, doorPass = Infinity, loungeBubble = false, crossProject = false, spotDist = Infinity;
var sleeperWalked = false, bAwayAtSleep = null;
for (var i = 0; i < 240 * 60; i++) {
  t += dt;
  if (Math.abs(t - 60) < dt / 2) sim.sync(live(), P);                                   // d's session ends
  if (Math.abs(t - 150) < dt / 2) { workers[0].state = 'working'; sim.sync(live(), P); }  // a wakes up
  if (Math.abs(t - 200) < dt / 2) {                                                      // b falls asleep
    bAwayAtSleep = sim.agents.b.state.pose !== 'typing' && sim.agents.b.state.pose !== 'thinking';
    workers[1].state = 'asleep'; sim.sync(live(), P);
  }
  sim.tick(dt);
  if (t < 150 && sim.agents.a.state.pose !== 'dozing') sleeperWalked = true;
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
            bAtSeat: Math.hypot(sim.agents.b.pos.x - 2.5, sim.agents.b.pos.y - 1) < 0.2, bAwayAtSleep: bAwayAtSleep,
            aPose: sim.agents.a.state.pose, bPose: sim.agents.b.state.pose };

function expect(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exitCode = 1; } }
expect(seen.c.typing > 0 && seen.c.thinking > 0, 'a working session types and thinks');
expect(!sleeperWalked, 'a sleeper stays asleep at its desk');
expect((seen.b.walking || 0) > 0 && (seen.c.walking || 0) > 0, 'workers take breaks between thoughts');
expect(['b', 'c', 'e'].some(function (id) { return (seen[id].chatting || 0) + (seen[id].drinking || 0) > 0; }),
       'a break can be a chat or a drink at the cooler');
expect((seen.b.lounging || 0) + (seen.e.lounging || 0) > 0, 'somebody walks out to the canteen');
expect(out.canteenSpotDist !== null && out.canteenSpotDist < 0.2, 'a lounger stands on a canteen spot');
expect(out.crossProject, 'workers from different projects meet at the canteen');
expect(out.loungeBubble, 'the canteen has conversation');
expect(out.agentsLeft.indexOf('d') === -1 && (seen.d.walking || 0) > 0, 'a gone session walks out and is removed');
expect(maxSpeed <= 2.35, 'steering never exceeds the fastest tier');
expect(top.e > top.b + 0.15, 'the swift tier really is quicker');
expect(out.aPose !== 'dozing', 'a sleeper called back to work wakes up (pose: ' + out.aPose + ')');
expect(out.bPose === 'dozing' && out.bAtSeat, 'a worker that falls asleep walks home and dozes (pose: ' + out.bPose + ')');
if (out.chatMinDist !== null) expect(out.chatMinDist < 1.0, 'chat partners stand together');

// A worker whose session falls asleep in the middle of a break walks home and dozes.
var nap = new Sim({ wander: true });
var napper = [{ session_id: 'b', state: 'working', model: 'Sonnet 5' }];
nap.sync(napper, { b: P.b });
var away = false, k;
for (k = 0; k < 600 * 60 && !away; k++) { nap.tick(dt); away = nap.agents.b.state.pose !== 'typing' && nap.agents.b.state.pose !== 'thinking'; }
napper[0].state = 'asleep';
nap.sync(napper, { b: P.b });
for (k = 0; k < 30 * 60; k++) nap.tick(dt);
var nb = nap.agents.b;
expect(away && nb.state.pose === 'dozing' && Math.hypot(nb.pos.x - 2.5, nb.pos.y - 1) < 0.2,
       'a break ends when the session falls asleep: home to the desk, then dozing');

// --- interns (spec Sec 13.4) ---------------------------------------------
function podPlaces(seat) {
  var p = places(seat, 2);
  p.pod = [0, 1, 2, 3].map(function (s) { return outsideToRoom(seat.x + 1 + (s % 2) * 0.55, seat.y - 0.7 + Math.floor(s / 2) * 0.6); });
  p.handoff = inRoom(seat.x + 0.5, seat.y);
  return p;
}
function outsideToRoom(x, y) { return { x: x, y: y, lane: { axis: 'y', at: AISLE }, via: [DOOR, { x: x, y: AISLE }] }; }
function agentRec(id, state, parent) { return { agent_id: id, type: 'Explore', model: 'Haiku 4.5', state: state, parent: parent || null, background: false }; }

var lone = new Sim({ wander: true });                   // the server alone decides whether a lead works
lone.sync([{ session_id: 'Q', state: 'asleep', model: 'Opus 5', agents: [agentRec('bg', 'running')] }], { Q: podPlaces({ x: 1, y: 1 }) });
expect(lone.agents.Q.server === 'asleep', 'a running sub-agent does not wake a lead the server calls asleep');

var isim = new Sim({ wander: true });
var IP = { L: podPlaces({ x: 1, y: 1 }) };
var lead = { session_id: 'L', state: 'working', model: 'Opus 5', agents: [agentRec('pre', 'running')] };   // the server wakes a lead for its foreground agents
isim.sync([lead], IP);
var preAtSlot = Math.hypot(isim.agents['L/pre'].pos.x - IP.L.pod[0].x, isim.agents['L/pre'].pos.y - IP.L.pod[0].y) < 1e-6;
var emptied = false, leadStays = true, napped = false, woke = false, arrived = false, handed = false, left = false, overflow = null, nestedParent = null;
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
    // 'new' is done but has not walked away yet: its slot is still its own
    overflow = Object.keys(isim.agents).filter(function (id) { return id.indexOf('L/') === 0; }).length;
  }
  if (Math.abs(it - 50) < dt / 2) {                        // 'pre' goes quiet, then picks up again
    lead.agents[0] = agentRec('pre', 'asleep'); isim.sync([lead], IP);
  }
  if (Math.abs(it - 51) < dt / 2) napped = isim.agents['L/pre'].state.pose === 'dozing';
  if (Math.abs(it - 60) < dt / 2) { lead.agents[0] = agentRec('pre', 'running'); isim.sync([lead], IP); }
  if (Math.abs(it - 61) < dt / 2) woke = isim.agents['L/pre'].state.pose === 'standing';
  if (Math.abs(it - 80) < dt / 2) { lead.agents = []; isim.sync([lead], IP); emptied = true; }       // every agent gone
  isim.tick(dt);
  var L = isim.agents.L;
  if (!emptied && L.state.pose === 'dozing') leadStays = false;
  var n = isim.agents['L/new'];
  if (n && n.state.pose === 'standing' && Math.hypot(n.pos.x - IP.L.pod[n.slot].x, n.pos.y - IP.L.pod[n.slot].y) < 0.1) arrived = true;
  if (n && n.state.pose === 'handing') handed = true;
  if (handed && !isim.agents['L/new']) left = true;
}
expect(napped && woke, 'an asleep intern dozes in its slot and wakes when its agent runs again');
expect(preAtSlot, 'an intern present at the first sync starts in its slot');
expect(arrived, 'a new intern walks in to a pod slot');
expect(nestedParent === 'L/new', 'a nested intern is tethered to the intern that spawned it');
expect(handed && left, 'a done intern hands back at the desk and walks out');
expect(leadStays, 'a lead waiting on its foreground interns never dozes');
expect(overflow === 4, 'the pod holds four, a done intern still in its slot among them; the rest wait in the rail (' + overflow + ')');
expect(Object.keys(isim.agents).filter(function (id) { return id.indexOf('L/') === 0; }).length === 0, 'interns leave when the payload drops them');
out.interns = { preAtSlot: preAtSlot, arrived: arrived, handed: handed, left: left,
                leadStays: leadStays, overflow: overflow, nestedParent: nestedParent };
console.log(JSON.stringify(out));
