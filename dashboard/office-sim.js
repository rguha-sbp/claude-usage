/* Office simulation (spec Sec 12; built with the game-ai skill's three
   layers). DECIDE: one small state machine per worker, transitions live
   inside the states. STEER: an `arrive` behaviour in tile space, frame-rate
   independent. PATH: a waypoint list per errand, built from the spot the
   worker is heading for. No DOM here: office.js feeds `sync()` the server
   payload plus room geometry, calls `tick(dt)` every frame, and reads each
   agent's `pos`, `pose`, `facing` and `bubble`.

   A *spot* is everything the simulation knows about a place: `{x, y, lane,
   via}`. `via` are the waypoints between the worker's own aisle and the
   spot, so a trip to the canteen in the plaza is the same code path as a
   trip to the water cooler two desks away. `lane` is the line a worker
   standing there walks off along -- the room aisle (constant y) or the
   corridor in front of the rooms (constant x + y).

   Interns (spec Sec 13.4) are agents too, keyed `session/agent_id`: Standing,
   Napping and HandingBack sit beside the worker states and reuse Walk,
   Leaving and Gone. Whether a lead works is the server's call alone (it
   counts a lead blocked on a foreground sub-agent as working). */
window.OfficeSim = (function () {
  'use strict';

  var SPEED = 1.7;          // tiles per second, before the model's own multiplier
  var MAX_FORCE = 7;        // tiles per second squared
  var GRIP = 8;             // per second: how quickly a walker matches the velocity it wants
  var SLOW_RADIUS = 0.5;    // start braking this close to a waypoint
  var ARRIVE = 0.05;        // "there" when this close
  var WALK_TIMEOUT = 14;    // seconds: a stuck walker snaps to its target
  var CHAT_RANGE = 2.5;     // tiles: how far apart two loungers still talk
  var BREAK_CHANCE = 0.3;   // a working lead between thoughts steps away this often

  // ------------------------------------------------------------ helpers
  var hash = window.OfficeCast.hash, rng = window.OfficeCast.rng;   // office-cast.js loads first
  function between(r, a, b) { return a + r() * (b - a); }
  function copy(p) { return { x: p.x, y: p.y }; }
  /* Structural equality of plain place data: numbers, spots, lists. */
  function same(p, q) {
    if (p === q) return true;
    if (!p || !q || typeof p !== 'object' || typeof q !== 'object') return false;
    var keys = Object.keys(p);
    return keys.length === Object.keys(q).length && keys.every(function (k) { return same(p[k], q[k]); });
  }
  function spotKey(p) { return p.x.toFixed(2) + ',' + p.y.toFixed(2); }
  function tierOf(model) {
    return (window.OfficeCast && window.OfficeCast.tierOf(model)) || { key: 'core', speed: 1 };
  }

  // ------------------------------------------------------------- STEER
  /* Arrive: accelerate toward `target`, ramp speed down inside SLOW_RADIUS.
     GRIP keeps a walker on its walkway: without it a turn at a corner
     drifts a tile wide, through whatever furniture stands there.
     Returns true once the agent is there. */
  function arrive(agent, target, dt) {
    var top = SPEED * (agent.speed || 1);
    var dx = target.x - agent.pos.x, dy = target.y - agent.pos.y;
    var dist = Math.hypot(dx, dy);
    var fx, fy;
    if (dist < 0.001) {
      fx = -agent.vel.x * GRIP; fy = -agent.vel.y * GRIP;
    } else {
      var ramped = top * Math.min(dist / SLOW_RADIUS, 1);
      fx = (dx / dist * ramped - agent.vel.x) * GRIP;
      fy = (dy / dist * ramped - agent.vel.y) * GRIP;
    }
    var f = Math.hypot(fx, fy);
    if (f > MAX_FORCE) { fx *= MAX_FORCE / f; fy *= MAX_FORCE / f; }
    agent.vel.x += fx * dt;
    agent.vel.y += fy * dt;
    agent.pos.x += agent.vel.x * dt;
    agent.pos.y += agent.vel.y * dt;
    // screen x grows with (x - y): face the way we are heading
    var screenDx = (agent.vel.x - agent.vel.y);
    if (Math.abs(screenDx) > 0.05) agent.facing = screenDx < 0 ? -1 : 1;
    return dist <= ARRIVE + Math.hypot(agent.vel.x, agent.vel.y) * dt;
  }

  // -------------------------------------------------------------- PATH
  /* The point where a worker standing at `pos` steps onto its lane. */
  function laneAt(lane, pos) {
    if (!lane) return copy(pos);
    if (lane.axis === 'v') {                  // corridor: the line x + y = at
      var t = pos.x - pos.y;
      return { x: (lane.at + t) / 2, y: (lane.at - t) / 2 };
    }
    return { x: pos.x, y: lane.at };          // room aisle: the line y = at
  }

  /* Out to the lane, along the spot's own waypoints, then onto the spot. */
  function walkTo(a, spot, then, opts) {
    var points = [laneAt(a.lane, a.pos)].concat(spot.via || [], [copy(spot)]);
    return new Walk(points, spot, then, opts);
  }

  /* Home is the way we came: retrace the waypoints already passed, then sit
     down. Works from a finished errand and from the middle of a walk. */
  function goHome(a, sim, then) {
    sim.release(a);
    var seat = a.places.seat;
    var back = (a.route || []).slice(0, a.wp).reverse();
    return new Walk(back.concat([copy(seat)]), seat, then, { home: true });
  }

  /* The way out from wherever the worker is (spec Sec 15.2): a session can
     end at the canteen, at another row's cooler or halfway along a walk,
     and the door's `via` starts at the worker's own aisle. So walk the
     route it is on, back or ahead, to the nearest point of its own way out
     (seat, aisle, the door's `via`, the door), and follow that to the door.
     Every leg is one the floor already keeps clear for the trips out and
     back; `straighten` then folds the out-and-back stretches, so nobody
     walks to the spine just to turn round in the corridor. */
  function walkOut(a, then) {
    var seat = a.places.seat, door = a.places.door;
    var way = [copy(seat), laneAt(seat.lane, seat)].concat(door.via || [], [copy(door)]);
    var best = [joinWay(a.route.slice(0, a.wp).reverse(), way, a.pos), joinWay(a.route.slice(a.wp), way, a.pos)]
      .filter(Boolean).sort(function (p, q) { return p.length - q.length; })[0];
    var points = best ? best.legs.concat(way.slice(best.at + 1)) : way;
    return new Walk(straighten([copy(a.pos)].concat(points)), door, then, { leaving: true });
  }

  /* Along `chain` from `pos` to its first point on `way`, or null. */
  function joinWay(chain, way, pos) {
    var length = 0, last = pos;
    for (var i = 0; i < chain.length; i++) {
      length += Math.hypot(chain[i].x - last.x, chain[i].y - last.y);
      last = chain[i];
      for (var j = 0; j < way.length; j++) {
        if (Math.hypot(way[j].x - last.x, way[j].y - last.y) < 1e-6) return { legs: chain.slice(0, i + 1), at: j, length: length };
      }
    }
    return null;
  }

  /* Drop every waypoint that lies on the line through its neighbours. The
     walk from the first to the last of three such points never leaves the
     two legs it replaces. */
  function straighten(points) {
    var out = [];
    points.forEach(function (q) {
      while (out.length >= 2) {
        var a = out[out.length - 2], b = out[out.length - 1];
        if (Math.abs((b.x - a.x) * (q.y - b.y) - (b.y - a.y) * (q.x - b.x)) > 1e-6) break;
        out.pop();
      }
      out.push(q);
    });
    return out;
  }

  // ------------------------------------------------------------ DECIDE
  /* Each state: enter(agent, sim), update(agent, sim, dt) -> next state or
     null, optional exit(agent, sim). `pose` is what office.js draws. */

  function leaveIfGone(a) { return a.server === 'gone' ? new Leaving() : null; }

  /* An errand is work time spent away from the desk, so it ends the moment
     the session stops working: a gone session walks out, a sleeper walks
     home and dozes. */
  function errand(a, spot, then) { return walkTo(a, spot, then, { errand: true }); }
  function atDesk(a) { return a.server === 'working' ? new Typing() : new Dozing(); }
  function backToDesk(a, sim) {
    return goHome(a, sim, function () { return atDesk(a); });
  }
  function errandOver(a, sim) {
    if (a.server === 'working') return null;
    return leaveIfGone(a) || backToDesk(a, sim);
  }

  function Typing() {}
  Typing.prototype.pose = 'typing';
  Typing.prototype.enter = function (a) { a.timer = between(a.rng, 9, 28); };
  Typing.prototype.update = function (a, sim, dt) {
    if (a.server !== 'working') return leaveIfGone(a) || new Dozing();
    a.timer -= dt;
    return a.timer <= 0 ? new Thinking() : null;
  };

  function Thinking() {}
  Thinking.prototype.pose = 'thinking';
  Thinking.prototype.enter = function (a) { a.timer = between(a.rng, 3, 6); };
  Thinking.prototype.update = function (a, sim, dt) {
    if (a.server !== 'working') return leaveIfGone(a) || new Dozing();
    a.timer -= dt;
    if (a.timer > 0) return null;
    return sim.wander && a.rng() < BREAK_CHANCE ? takeBreak(a, sim) : new Typing();
  };

  /* A sleeper stays at its desk until its session works again or ends. */
  function Dozing() {}
  Dozing.prototype.pose = 'dozing';
  Dozing.prototype.enter = function () {};
  Dozing.prototype.update = function (a) { return a.server === 'working' ? new Typing() : leaveIfGone(a); };

  /* Coffee is contagious: a worker is far likelier to head for the canteen
     when somebody is already standing there. Otherwise it is a toss-up
     between the canteen, a chat in the aisle and the water cooler. */
  function takeBreak(a, sim) {
    if (a.rng() < (sim.anyLounger(a) ? 0.85 : 0.5)) {
      var table = sim.freeSpot(a, a.places.canteen);
      if (table) return errand(a, table, function () { return new Lounging(); });
    }
    var partner = sim.partnerFor(a);
    if (partner && a.rng() < 0.6 && sim.freeSpot(a, [a.places.meet])) {
      if (sim.freeSpot(partner, [partner.places.meet2])) {
        sim.transition(partner, errand(partner, partner.places.meet2, function () { return new Chatting(a); }));
        return errand(a, a.places.meet, function () { return new Chatting(partner); });
      }
      sim.release(a);
    }
    var stand = sim.freeSpot(a, [a.places.stand]);
    return stand ? errand(a, stand, function () { return new Drinking(); }) : new Typing();
  }

  /* Walk the `points`, then become whatever `then()` returns. A gone
     session walks out; an errand ends early once the session stops working. */
  function Walk(points, target, then, opts) {
    this.points = points;
    this.target = target;
    this.then = then;
    this.opts = opts || {};
  }
  Walk.prototype.pose = 'walking';
  Walk.prototype.enter = function (a) {
    a.route = this.points;
    a.wp = 0;
    a.walked = 0;
    a.vel = { x: 0, y: 0 };
  };
  Walk.prototype.update = function (a, sim, dt) {
    if (!this.opts.leaving) {
      if (a.server === 'gone') return new Leaving();
      if (this.opts.errand) { var over = errandOver(a, sim); if (over) return over; }
    }
    a.walked += dt;
    if (a.walked > WALK_TIMEOUT) a.wp = a.route.length;         // stuck check: snap and move on
    if (a.wp < a.route.length && arrive(a, a.route[a.wp], dt)) a.wp += 1;
    if (a.wp < a.route.length) return null;
    a.pos = copy(this.target);
    a.lane = this.target.lane || a.lane;
    a.vel = { x: 0, y: 0 };
    return this.then();
  };

  function Drinking() {}
  Drinking.prototype.pose = 'drinking';
  Drinking.prototype.enter = function (a) { a.timer = between(a.rng, 3.5, 6); a.facing = 1; };   // the cooler is to the right
  Drinking.prototype.update = function (a, sim, dt) {
    a.timer -= dt;
    return errandOver(a, sim) || (a.timer <= 0 ? backToDesk(a, sim) : null);
  };

  /* The canteen out on the plaza: the one place where workers from
     different projects stand together, so they talk while they are there. */
  function Lounging() {}
  Lounging.prototype.pose = 'lounging';
  Lounging.prototype.enter = function (a) {
    a.timer = between(a.rng, 14, 26);
    a.bubbleAt = between(a.rng, 0, 4);
    a.extended = false;
  };
  Lounging.prototype.update = function (a, sim, dt) {
    var over = errandOver(a, sim);
    if (over) return over;
    var mate = sim.loungerNear(a);
    if (mate) {
      if (!a.extended) { a.extended = true; a.timer += 12; }   // nobody cuts a conversation short
      a.facing = (a.pos.x - a.pos.y) < (mate.pos.x - mate.pos.y) ? 1 : -1;
      a.bubbleAt = (a.bubbleAt + dt) % 4;       // take turns: ~2 s of talk in every 4
      a.bubble = a.bubbleAt < 2;
    } else {
      a.bubble = false;
    }
    a.timer -= dt;
    return a.timer <= 0 ? backToDesk(a, sim) : null;
  };
  Lounging.prototype.exit = function (a) { a.bubble = false; };

  function Chatting(partner) { this.partner = partner; }
  Chatting.prototype.pose = 'chatting';
  Chatting.prototype.enter = function (a) {
    a.timer = between(a.rng, 7, 12);
    a.bubbleAt = between(a.rng, 0, 2);
    a.facing = (a.pos.x - a.pos.y) < (this.partner.pos.x - this.partner.pos.y) ? 1 : -1;
  };
  Chatting.prototype.update = function (a, sim, dt) {
    var over = errandOver(a, sim);
    if (over) return over;
    var other = this.partner.state;
    var partnerHere = other && (other.pose === 'chatting' || (other.pose === 'walking' && !other.opts.home));
    a.timer -= dt;
    a.bubbleAt = (a.bubbleAt + dt) % 4;
    a.bubble = a.bubbleAt < 2;
    if (a.timer <= 0 || !partnerHere) { a.bubble = false; return backToDesk(a, sim); }
    return null;
  };
  Chatting.prototype.exit = function (a) { a.bubble = false; };

  function Leaving() {}
  Leaving.prototype.pose = 'walking';
  Leaving.prototype.enter = function (a, sim) {
    a.leaving = true;
    sim.release(a);
    sim.transition(a, walkOut(a, function () { return new Gone(); }));
  };
  Leaving.prototype.update = function () { return null; };

  function Gone() {}
  Gone.prototype.pose = 'gone';
  Gone.prototype.enter = function (a) { a.done = true; };
  Gone.prototype.update = function () { return null; };

  // ----------------------------------------------------------- interns
  /* A sub-agent is an intern (spec Sec 13.4). It walks in from the doorway
     to a standing slot by its lead's desk, works there while its agent
     runs, dozes there while its agent is asleep, walks to the lead's desk to
     hand back once the agent is done, and walks out. For an intern
     `a.server` is the agent's state -- running, asleep, done -- or gone once
     the payload stops listing it. */
  function internNext(a, sim, here) {
    var gone = leaveIfGone(a);
    if (gone) return gone;
    if (a.server === 'done') {
      if (!sim.wander) return new Gone();
      var desk = sim.freeSpot(a, [a.places.handoff]);     // one intern hands back at a time
      return desk ? walkTo(a, desk, function () { return new HandingBack(); }) : null;
    }
    var want = a.server === 'asleep' ? 'dozing' : 'standing';
    if (want === here) return null;
    return want === 'dozing' ? new Napping() : new Standing();
  }

  function Standing() {}
  Standing.prototype.pose = 'standing';
  Standing.prototype.enter = function () {};
  Standing.prototype.update = function (a, sim) { return internNext(a, sim, 'standing'); };

  function Napping() {}
  Napping.prototype.pose = 'dozing';
  Napping.prototype.enter = function () {};
  Napping.prototype.update = function (a, sim) { return internNext(a, sim, 'dozing'); };

  function HandingBack() {}
  HandingBack.prototype.pose = 'handing';
  HandingBack.prototype.enter = function (a, sim) {
    a.timer = 1.2;
    a.facing = -1;                                   // the desk is up-left of the hand-off spot
    var lead = sim.agents[a.lead];
    if (lead) lead.popped = true;
  };
  HandingBack.prototype.update = function (a, sim, dt) {
    a.timer -= dt;
    return a.timer <= 0 ? new Leaving() : null;
  };

  function internPlaces(places, slot) {
    return { room: places.room, row: places.row, seat: places.pod[slot], handoff: places.handoff, door: places.door };
  }

  // --------------------------------------------------------------- Sim
  /* A fresh agent standing at `pos`: habits and looks follow from its id. */
  function newAgent(id, pos, lane) {
    var h = hash(id);
    return { id: id, rng: rng(h), pos: copy(pos), vel: { x: 0, y: 0 }, facing: 1,
             bubble: false, popped: false, done: false, leaving: false, state: null,
             route: [], wp: 0, held: null, lane: lane, kind: 'lead',
             look: { hair: h % 5, glasses: ((h >>> 3) % 3) === 0, coat: ((h >>> 5) % 4) === 0 } };
  }

  /* Hand an agent its places; when the floor was re-laid out under it (a
     session joined or left), restart it at its new seat. Whatever it was
     doing -- sitting, on an errand, walking in, out or home -- its position,
     route and target belong to the old floor, and from a point of the old
     floor no walk on the new one is known to be clear. The new seat is,
     and every walk the agent then plans starts from there. */
  function relocate(sim, a, places) {
    var moved = !same(a.places, places);
    a.places = places;
    if (!moved) return;
    sim.release(a);
    a.pos = copy(places.seat);
    a.lane = places.seat.lane;
    a.vel = { x: 0, y: 0 };
    a.route = [];
    a.wp = 0;
    var popped = a.popped;                 // a re-layout is not a moment to squash for
    sim.transition(a, a.leaving ? new Leaving() : a.kind === 'intern' ? new Standing() : atDesk(a));
    a.popped = popped;
  }

  function Sim(opts) {
    this.agents = {};
    this.primed = false;                   // true once the first sync has finished
    this.floor = null;                     // the places of the last sync: a new object means a new floor
    this.spots = {};                       // spotKey -> id of the agent holding that spot
    this.wander = !(opts && opts.wander === false);
  }

  Sim.prototype.transition = function (a, next) {
    if (a.state && a.state.exit) a.state.exit(a, this);
    a.state = next;
    a.popped = true;                       // office.js plays a squash-and-stretch once
    next.enter(a, this);
  };

  /* Server payload in, agents updated: new sessions get an agent at their
     seat, missing ones are told they are gone, an agent whose places moved
     (rooms re-laid out) restarts at its new seat (relocate), and each
     sub-agent gets an intern (syncInterns). `placesById` is the floor
     plan's own `places`: the same object means the same floor, so places
     are compared only after a re-layout. */
  Sim.prototype.sync = function (workers, placesById) {
    var seen = {};
    var self = this;
    var relaid = placesById !== this.floor;   // office.js rebuilds the plan only when the floor changes
    this.floor = placesById;
    workers.forEach(function (w) {
      var places = placesById[w.session_id];
      if (!places) return;
      seen[w.session_id] = true;
      var tier = tierOf(w.model);
      var server = w.state;
      var a = self.agents[w.session_id];
      if (!a) {
        a = newAgent(w.session_id, places.seat, places.seat.lane);
        self.agents[w.session_id] = a;
        a.server = server;
        a.places = places;
        a.tier = tier.key;
        a.speed = tier.speed;
        self.transition(a, atDesk(a));
        a.popped = false;
      } else {
        a.server = server;
        a.tier = tier.key;
        a.speed = tier.speed;
        if (relaid) relocate(self, a, places);
      }
      self.syncInterns(w, places, seen, relaid);
    });
    Object.keys(this.agents).forEach(function (id) {
      if (!seen[id]) self.agents[id].server = 'gone';
    });
    this.primed = true;
  };

  /* One intern per listed sub-agent that is not done, while the pod has a
     free slot; one already on stage follows its agent's state. A sub-agent
     that finished before we ever saw it only shows in the rail. */
  Sim.prototype.syncInterns = function (w, places, seen, relaid) {
    var self = this;
    (w.agents || []).forEach(function (sub) {
      var id = w.session_id + '/' + sub.agent_id;
      var a = self.agents[id];
      if (!a) {
        if (sub.state === 'done') return;
        var slot = self.freeSlot(w.session_id, places);
        if (slot < 0) return;                       // the pod is full: the name plate counts it
        a = self.spawnIntern(id, w.session_id, sub, places, slot);
      }
      seen[id] = true;
      a.server = sub.state;
      if (relaid) relocate(self, a, internPlaces(places, a.slot));
      a.background = !!sub.background;
      a.parentId = sub.parent ? w.session_id + '/' + sub.parent : w.session_id;
    });
  };

  Sim.prototype.freeSlot = function (lead, places) {
    var taken = {};
    var self = this;
    Object.keys(this.agents).forEach(function (id) {
      var b = self.agents[id];
      if (b.lead === lead && b.server !== 'done' && b.server !== 'gone') taken[b.slot] = true;
    });
    for (var s = 0; s < places.pod.length; s++) if (!taken[s]) return s;
    return -1;
  };

  /* Interns on stage at the first sync start in their slot; later ones walk
     in from the doorway (or appear there under reduced motion). */
  Sim.prototype.spawnIntern = function (id, leadId, sub, places, slot) {
    var tier = window.OfficeCast.internTierOf(sub.model);
    var p = internPlaces(places, slot);
    var walkIn = this.primed && this.wander;
    var a = newAgent(id, walkIn ? places.door : p.seat, walkIn ? places.door.lane : p.seat.lane);
    a.kind = 'intern';
    a.lead = leadId;
    a.slot = slot;
    a.places = p;
    a.server = sub.state;
    a.tier = tier.key;
    a.speed = tier.speed;
    this.agents[id] = a;
    this.transition(a, walkIn ? walkTo(a, p.seat, function () { return new Standing(); }) : new Standing());
    a.popped = false;
    if (this.primed && this.agents[leadId]) this.agents[leadId].popped = true;
    return a;
  };

  /* The first lead other than `a` that passes `test`, or null. */
  Sim.prototype.leadWhere = function (a, test) {
    var ids = Object.keys(this.agents);
    for (var i = 0; i < ids.length; i++) {
      var b = this.agents[ids[i]];
      if (b.kind === 'lead' && b !== a && test(b)) return b;
    }
    return null;
  };

  /* Another worker at its desk in the same room, free to be talked to. */
  Sim.prototype.partnerFor = function (a) {
    return this.leadWhere(a, function (b) {
      return b.places.room === a.places.room && b.server === 'working' && (b.state.pose === 'typing' || b.state.pose === 'thinking');
    });
  };

  /* Anyone else standing around the canteen, whatever project they came from. */
  Sim.prototype.loungerNear = function (a) {
    return this.leadWhere(a, function (b) { return b.state.pose === 'lounging' && Math.hypot(b.pos.x - a.pos.x, b.pos.y - a.pos.y) < CHAT_RANGE; });
  };

  /* Is anyone already at the canteen, or on their way there? */
  Sim.prototype.anyLounger = function (a) {
    return this.leadWhere(a, function (b) {
      return b.state.pose === 'lounging' || (b.state.pose === 'walking' && b.places.canteen.indexOf(b.state.target) !== -1);
    });
  };

  /* Every shared spot (a canteen table, the cooler stand, the two meeting
     spots, a desk's hand-off spot) is first come, first served, so two
     agents never stand inside each other. The first spot of `list` that
     nobody else holds becomes `a`'s, or null. An agent holds one spot at a
     time and gives it back when it walks away (goHome, Leaving, relocate).
     Spots are told apart by where they are: each worker's places are its
     own objects, but the cooler stand of a room is one point for all. */
  Sim.prototype.freeSpot = function (a, list) {
    for (var i = 0; i < list.length; i++) {
      var key = spotKey(list[i]), holder = this.spots[key];
      if (!holder || holder === a.id) {
        if (a.held !== key) this.release(a);
        this.spots[key] = a.id;
        a.held = key;
        return list[i];
      }
    }
    return null;
  };

  Sim.prototype.release = function (a) {
    if (a.held) { delete this.spots[a.held]; a.held = null; }
  };

  /* Advance every agent by dt seconds. Returns true while anyone moves. */
  Sim.prototype.tick = function (dt) {
    var moving = false;
    var self = this;
    Object.keys(this.agents).forEach(function (id) {
      var a = self.agents[id];
      var next = a.state.update(a, self, dt);
      if (next) self.transition(a, next);
      if (a.done) { self.release(a); delete self.agents[id]; return; }
      if (a.state.pose === 'walking') moving = true;
    });
    return moving;
  };

  Sim.prototype.list = function () {
    var self = this;
    return Object.keys(this.agents).map(function (id) { return self.agents[id]; });
  };

  return { Sim: Sim };
})();
