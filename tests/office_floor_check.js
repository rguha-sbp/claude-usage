// Drives dashboard/office-floor.js without a browser: lays out floors of 1,
// 4 and 12 projects in every theme, checks the plan's geometry with its own
// collision code (not the floor's), then runs office-sim.js over the real
// places and watches every walker. Exits non-zero on a broken expectation.
// Run by tests/test_office_sim.py.
'use strict';
global.window = {};
require('../dashboard/office-cast.js');
require('../dashboard/office-themes.js');
require('../dashboard/office-floor.js');
require('../dashboard/office-sim.js');
var F = window.OfficeFloor, THEMES = window.OfficeThemes.THEMES;

var failures = 0;
function expect(cond, msg) { if (!cond) { failures += 1; if (failures < 25) console.error('FAIL: ' + msg); process.exitCode = 1; } }

function payload(projects) {
  var rooms = [];
  for (var i = 0; i < projects; i++) {
    var workers = [];
    for (var k = 0; k < 1 + (i * 5) % 6; k++) {
      workers.push({ session_id: 'p' + i + 's' + k, title: 'S', model: ['Opus 5', 'Sonnet 5', 'Haiku 4.5'][k % 3],
                     state: 'working', agents: [], agents_hidden: 0 });
    }
    rooms.push({ project: 'project-' + i, cwd: '/x/' + i, workers: workers });
  }
  return { generated_at: '2026-11-05T10:00:00+00:00', rooms: rooms, board: {} };
}

// ------------------------------------------------------------ geometry
function box(x0, y0, x1, y1) { return { x0: Math.min(x0, x1), y0: Math.min(y0, y1), x1: Math.max(x0, x1), y1: Math.max(y0, y1) }; }
function grow(b, pad) { return box(b.x0 - pad, b.y0 - pad, b.x1 + pad, b.y1 + pad); }
function hit(a, b) { return a.x0 < b.x1 - 1e-6 && b.x0 < a.x1 - 1e-6 && a.y0 < b.y1 - 1e-6 && b.y0 < a.y1 - 1e-6; }
function pointIn(pt, b) { return pt.x > b.x0 && pt.x < b.x1 && pt.y > b.y0 && pt.y < b.y1; }
/* Liang-Barsky: does the segment a-b pass through box b? */
function segHits(a, b, r) {
  var t0 = 0, t1 = 1, dx = b.x - a.x, dy = b.y - a.y;
  var checks = [[-dx, a.x - r.x0], [dx, r.x1 - a.x], [-dy, a.y - r.y0], [dy, r.y1 - a.y]];
  for (var i = 0; i < 4; i++) {
    var pp = checks[i][0], q = checks[i][1];
    if (Math.abs(pp) < 1e-12) { if (q <= 0) return false; continue; }
    var t = q / pp;
    if (pp < 0) { if (t > t1) return false; if (t > t0) t0 = t; } else { if (t < t0) return false; if (t < t1) t1 = t; }
  }
  return t0 < t1;
}
function footprint(item) {
  var odd = item.rot % 2 !== 0, w = odd ? item.d : item.w, d = odd ? item.w : item.d;
  return box(item.x - w / 2, item.y - d / 2, item.x + w / 2, item.y + d / 2);
}

/* The walks the simulation really takes, rebuilt from the places alone:
   off the lane, along `via`, onto the spot (office-sim.js walkTo). */
function trips(pl) {
  var lanePt = function (s) { return { x: s.x, y: s.lane.at }; };
  var chain = function (from, s) { return [from].concat(s.via || [], [{ x: s.x, y: s.y }]); };
  var out = [[{ x: pl.seat.x, y: pl.seat.y }, lanePt(pl.seat)]];
  [pl.stand, pl.meet, pl.meet2, pl.door].concat(pl.canteen).forEach(function (s) { out.push(chain(lanePt(pl.seat), s)); });
  pl.pod.forEach(function (s) {
    out.push(chain({ x: pl.door.x, y: pl.door.y }, s));
    out.push(chain(lanePt(s), pl.handoff));
    out.push(chain(lanePt(s), pl.door));
  });
  return out;
}

var report = { floors: {}, decor: {}, themesDiffer: false };

[1, 4, 12].forEach(function (projects) {
  var data = payload(projects);
  var placesByTheme = [];
  THEMES.forEach(function (theme) {
    var plan = F.build(data, theme);
    var tag = projects + '/' + theme.key;
    var floorBox = box(0, 0, plan.W, plan.D);
    placesByTheme.push(JSON.stringify(plan.places));
    var floorItems = plan.decor.filter(function (d) { return d.mount === 'floor'; });
    var desks = plan.desks.map(function (d) { return box(d.x, d.y, d.x + d.w, d.y + d.d); });
    var parts = plan.partitions.map(function (pt) { return grow(box(pt.x1, pt.y1, pt.x2, pt.y2), 0.08); });
    report.decor[tag] = floorItems.length;

    // every worker has places, and every place is on the floor
    var sessions = data.rooms.reduce(function (s, r) { return s + r.workers.length; }, 0);
    expect(Object.keys(plan.places).length === sessions, tag + ': every worker gets places');
    var spots = [];
    Object.keys(plan.places).forEach(function (sid) {
      var pl = plan.places[sid];
      expect(pl.pod.length === F.POD && pl.canteen.length >= 4, tag + ': pods and canteen spots for ' + sid);
      [pl.seat, pl.stand, pl.meet, pl.meet2, pl.handoff, pl.door].concat(pl.pod, pl.canteen).forEach(function (s) {
        expect(s.x > 0 && s.x < plan.W && s.y > 0 && s.y < plan.D, tag + ': ' + sid + ' spot on the floor');
        expect(s.lane && s.lane.axis !== 'v' && isFinite(s.lane.at), tag + ': every lane is an aisle line');
        spots.push(s);
      });
    });

    // furniture and decor: on the floor, never overlapping each other,
    // a desk or a partition, never on a walk or a spot
    floorItems.forEach(function (item, i) {
      var b = footprint(item);
      expect(hit(b, floorBox) && b.x0 >= 0 && b.y0 >= 0 && b.x1 <= plan.W && b.y1 <= plan.D, tag + ': ' + item.kind + ' inside the floor');
      for (var j = i + 1; j < floorItems.length; j++) {
        expect(!hit(b, footprint(floorItems[j])), tag + ': ' + item.kind + ' overlaps ' + floorItems[j].kind);
      }
      desks.forEach(function (d) { expect(!hit(b, d), tag + ': ' + item.kind + ' on a desk'); });
      parts.forEach(function (pt) { expect(!hit(b, pt), tag + ': ' + item.kind + ' in a partition'); });
      spots.forEach(function (s) { expect(!pointIn(s, grow(b, 0.15)), tag + ': ' + item.kind + ' on a standing spot'); });
    });
    var segs = 0;
    Object.keys(plan.places).forEach(function (sid) {
      trips(plan.places[sid]).forEach(function (chain) {
        for (var k = 1; k < chain.length; k++) {
          var a = chain[k - 1], c = chain[k];
          segs += 1;
          floorItems.forEach(function (item) {
            expect(!segHits(a, c, grow(footprint(item), 0.1)), tag + ': ' + item.kind + ' blocks a walk at ' + a.x.toFixed(1) + ',' + a.y.toFixed(1));
          });
          desks.forEach(function (d) { expect(!segHits(a, c, d), tag + ': a walk crosses a desk'); });
          parts.forEach(function (pt) { expect(!segHits(a, c, pt), tag + ': a walk crosses a partition at ' + a.x.toFixed(1) + ',' + a.y.toFixed(1) + ' -> ' + c.x.toFixed(1) + ',' + c.y.toFixed(1)); });
        }
      });
    });
    for (var i = 0; i < desks.length; i++) {
      for (var j = i + 1; j < desks.length; j++) expect(!hit(desks[i], desks[j]), tag + ': desks overlap');
      parts.forEach(function (pt) { expect(!hit(desks[i], pt), tag + ': a desk in a partition'); });
    }
    plan.decor.filter(function (d) { return d.mount === 'wall'; }).forEach(function (d) {
      var onWall = d.x < 0.05 || d.y < 0.05 || Math.abs(d.x - plan.W) < 0.05 ||
        plan.partitions.some(function (pt) { return (pt.x1 === pt.x2 && Math.abs(d.x - pt.x1) < 0.1) || (pt.y1 === pt.y2 && Math.abs(d.y - pt.y1) < 0.1); });
      expect(onWall, tag + ': ' + d.kind + ' hangs on a wall');
    });
    report.floors[tag] = { w: Math.round(plan.W), d: Math.round(plan.D), segs: segs };
  });
  expect(placesByTheme.every(function (s) { return s === placesByTheme[0]; }), projects + ': places do not depend on the theme');
});
var a = F.build(payload(4), THEMES[0]).decor.map(function (d) { return d.kind; }).join();
var b = F.build(payload(4), THEMES[2]).decor.map(function (d) { return d.kind; }).join();
report.themesDiffer = a !== b;
expect(F.build({ rooms: [] }, THEMES[0]).W > 0, 'an empty office still has a floor');

// ---------------------------------------------- the sim over the real floor
function drive(projects) {
  var data = payload(projects);
  var plan = F.build(data, THEMES[0]);
  var solids = plan.desks.map(function (d) { return box(d.x, d.y, d.x + d.w, d.y + d.d); })
    .concat(plan.decor.filter(function (d) { return d.mount === 'floor'; }).map(footprint))
    .map(function (bx) { return grow(bx, -0.05); });
  var sim = new window.OfficeSim.Sim({ wander: true });
  var workers = [];
  data.rooms.forEach(function (room) { room.workers.forEach(function (w) { workers.push(w); }); });
  sim.sync(workers, plan.places);
  // a running intern for the first lead: it walks in from the door
  var first = workers[0];
  var withIntern = function (state) {
    return workers.map(function (w) {
      return w === first ? Object.assign({}, w, { agents: [{ agent_id: 'i1', type: 'Explore', state: state, parent: null, model: 'Haiku 4.5' }] }) : w;
    });
  };
  sim.sync(withIntern('running'), plan.places);
  var seen = { canteen: false, sofa: false, cooler: false, pod: false, door: 0, home: 0 };
  var blocked = null, dt = 1 / 30;
  var sofas = plan.seats.filter(function (s) { return !s.desk; });
  for (var t = 0; t < 420 * 30; t++) {
    if (t === 300 * 30) sim.sync(withIntern('running').map(function (w) { return Object.assign({}, w, { state: 'asleep' }); }), plan.places);
    if (t === 360 * 30) sim.sync([], plan.places);                  // every session ends: everyone walks out
    sim.tick(dt);
    sim.list().forEach(function (ag) {
      var pose = ag.state.pose;
      if (pose === 'lounging') {
        seen.canteen = true;
        if (sofas.some(function (s) { return Math.hypot(s.x - ag.pos.x, s.y - ag.pos.y) < 0.05; })) seen.sofa = true;
      }
      if (pose === 'drinking' && Math.hypot(ag.pos.x - ag.places.stand.x, ag.pos.y - ag.places.stand.y) < 0.1) seen.cooler = true;
      if (ag.kind === 'intern' && pose === 'standing') {
        seen.pod = seen.pod || Math.hypot(ag.pos.x - ag.places.seat.x, ag.pos.y - ag.places.seat.y) < 0.1;
      }
      if (pose === 'walking' && !blocked) {
        solids.forEach(function (s) { if (pointIn(ag.pos, s)) blocked = ag.id + ' at ' + ag.pos.x.toFixed(2) + ',' + ag.pos.y.toFixed(2); });
      }
      if (ag.pos.x < 0 || ag.pos.y < 0 || ag.pos.x > plan.W || ag.pos.y > plan.D) blocked = blocked || ag.id + ' off the floor';
    });
    if (t === 360 * 30 - 1) {
      seen.home = sim.list().filter(function (ag) {
        return ag.kind === 'lead' && ag.state.pose === 'dozing' && Math.hypot(ag.pos.x - ag.places.seat.x, ag.pos.y - ag.places.seat.y) < 0.1;
      }).length;
    }
  }
  seen.door = sim.list().length === 0;
  expect(seen.canteen && seen.pod, projects + ': workers reach the canteen, interns their pod');
  expect(blocked === null, projects + ': nobody walks through furniture (' + blocked + ')');
  expect(seen.home === workers.length, projects + ': every sleeper is back at its own desk (' + seen.home + '/' + workers.length + ')');
  expect(seen.door, projects + ': everyone walks out of the door');
  return seen;
}
report.sim = { 1: drive(1), 4: drive(4), 12: drive(12) };
expect(report.sim[4].cooler || report.sim[12].cooler, 'workers drink at a water cooler');

/* A session can end wherever its worker is: at the canteen, at the cooler
   of another row, or halfway along a walk out or home. End sessions as
   their workers reach each of those places and check every leg of the walk
   to the door against the desks, the partitions and the decor. */
function leaveAnywhere(projects) {
  var data = payload(projects);
  var plan = F.build(data, THEMES[0]);
  var desks = plan.desks.map(function (d) { return box(d.x, d.y, d.x + d.w, d.y + d.d); });
  var parts = plan.partitions.map(function (pt) { return grow(box(pt.x1, pt.y1, pt.x2, pt.y2), 0.08); });
  var decor = plan.decor.filter(function (d) { return d.mount === 'floor'; }).map(function (d) { return grow(footprint(d), 0.05); });
  var workers = [];
  data.rooms.forEach(function (room) { room.workers.forEach(function (w) { workers.push(w); }); });
  var sim = new window.OfficeSim.Sim({ wander: true });
  sim.sync(workers, plan.places);
  var from = { canteen: 0, otherRowCooler: 0, walkingOut: 0, walkingHome: 0 };
  var gone = {}, pending = {}, crossed = null, dt = 1 / 30;
  var startOf = function (ag) {
    var st = ag.state;
    if (st.pose === 'lounging') return 'canteen';
    if (st.pose === 'drinking' && ag.places.row > 0) return 'otherRowCooler';
    if (st.pose === 'walking' && st.opts.errand && ag.wp > 1) return 'walkingOut';
    if (st.pose === 'walking' && st.opts.home && ag.wp > 1) return 'walkingHome';
    return null;
  };
  for (var t = 0; t < 400 * 30; t++) {
    sim.tick(dt);
    Object.keys(pending).forEach(function (id) {
      var ag = sim.agents[id];
      if (!ag || !ag.state.opts || !ag.state.opts.leaving) return;
      delete pending[id];
      var legs = [copyPt(ag.pos)].concat(ag.route);
      for (var k = 1; k < legs.length; k++) {
        var a = legs[k - 1], c = legs[k], at = gone[id] + ': ' + a.x.toFixed(2) + ',' + a.y.toFixed(2) + ' -> ' + c.x.toFixed(2) + ',' + c.y.toFixed(2);
        desks.concat(parts, decor).forEach(function (s) { if (!crossed && segHits(a, c, s)) crossed = at; });
      }
    });
    var leaver = sim.list().filter(function (ag) {
      var kind = ag.kind === 'lead' && !gone[ag.id] && startOf(ag);
      return kind && from[kind] < 3;
    })[0];
    if (leaver) {
      gone[leaver.id] = startOf(leaver);
      from[gone[leaver.id]] += 1;
      pending[leaver.id] = true;
      sim.sync(workers.filter(function (w) { return !gone[w.session_id]; }), plan.places);
    }
  }
  expect(crossed === null, projects + ': a leaver walks through furniture (' + crossed + ')');
  Object.keys(from).forEach(function (k) { expect(from[k] > 0, projects + ': somebody leaves from ' + k); });
  return from;
}
function copyPt(pt) { return { x: pt.x, y: pt.y }; }
report.leavers = leaveAnywhere(12);
console.log(JSON.stringify(report));
