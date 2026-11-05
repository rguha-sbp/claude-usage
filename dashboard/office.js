/* Office tab (spec Sec 12): every live Claude Code session is a worker at a
   desk in an isometric office, one room per project, with a plaza, a canteen
   and a server room out front. Reads /api/office; the payload is bound to
   `data`, a room to `room`, a worker to `worker` (tests/test_dashboard_static.py
   checks those accesses).

   The work is split five ways: office-cast.js says who a worker is and what
   the light is like, office-world.js lays out the campus and draws it,
   office-sim.js decides what each worker does and where it stands,
   office-figures.js draws the figures and office-rail.js the Agents rail.
   This file is the controller: it fetches, drives the frame loop, places the
   figures, draws the interns' tethers and owns the hover card and the rail's
   links. A sub-agent is `sub`, and stands beside its lead as an intern.
   app.js tells it when the Office pane shows. */
(function () {
  'use strict';

  var esc = window.UsageFormat.esc;
  var $ = function (id) { return document.getElementById(id); };
  var POLL_MS = 5000;
  var reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  var World = window.OfficeWorld, Cast = window.OfficeCast, Figures = window.OfficeFigures, Rail = window.OfficeRail;
  var sim = new window.OfficeSim.Sim({ wander: !reduceMotion });
  var state = { data: null, timer: null, visible: false, workers: {}, subs: {}, sig: '', railSig: null, els: {}, frame: null, last: 0, ticker: null, linked: '' };


  // -------------------------------------------------------------- render
  function counts(data) {
    var total = 0, working = 0;
    data.rooms.forEach(function (room) {
      room.workers.forEach(function (worker) { total += 1; if (worker.state === 'working') working += 1; });
    });
    return { total: total, working: working };
  }

  function clock(ts) {
    if (!ts) return '';
    return new Date(ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  }

  function empty(stage, svg) {
    svg.innerHTML = ''; svg.setAttribute('height', '1'); state.sig = ''; state.els = {};
    stage.insertAdjacentHTML('beforeend', '<div class="empty"><b>The office is empty</b>' +
      '<span>Workers appear as soon as a Claude Code session reports in through the statusline.</span></div>');
    sim.sync([], {});
  }

  /* Campus, desks, furniture and name plates are static: redrawn only when
     the set of workers, the light or the board's numbers change. Workers are
     live elements the frame loop moves; a seated worker lives in its desk
     row's layer so a nearer desk covers it, a walking one in the layer above
     every desk. */
  function render(data) {
    state.data = data;
    renderRail(data);
    var svg = $('office-scene');
    var stage = $('office-stage');
    var n = counts(data);
    $('office-hint').textContent = n.total
      ? n.working + ' working · ' + (n.total - n.working) + ' asleep · updated ' + clock(data.generated_at)
      : 'No session has reported in yet';
    stage.querySelectorAll('.empty').forEach(function (e) { e.remove(); });
    if (!n.total) return empty(stage, svg);

    var maxWidth = Math.max(640, stage.getBoundingClientRect().width || 640);
    var world = World.build(data, maxWidth);
    var flat = [];
    state.workers = {};
    state.subs = {};
    data.rooms.forEach(function (room) {
      room.workers.forEach(function (worker) {
        state.workers[worker.session_id] = worker;
        flat.push(worker);
        (worker.agents || []).forEach(function (sub) {
          state.subs[worker.session_id + '/' + sub.agent_id] = { worker: worker, sub: sub };
        });
      });
    });
    sim.sync(flat, world.places);

    var sky = Cast.skyOf(new Date().getHours());
    var board = data.board || {};
    var sig = flat.map(function (worker) {
      var live = (worker.agents || []).filter(function (sub) { return sub.state !== 'done'; }).length;
      return worker.session_id + ':' + worker.state + ':' + live;
    }).join('|') + '@' + Math.round(maxWidth) + '@' + sky.key + '@' + board.value + ':' + board.top_project;
    if (sig === state.sig) return;
    state.sig = sig;
    state.els = {};

    var scene = World.draw(world, sky, board, n);
    svg.setAttribute('viewBox', scene.viewBox);
    svg.style.width = Math.round(scene.width) + 'px';
    svg.setAttribute('height', String(Math.round(scene.height)));
    svg.innerHTML = scene.svg;
    frame(performance.now(), true);
  }

  // ---------------------------------------------------------------- rail
  function renderRail(data) {
    var sig = Rail.signature(data);
    if (sig === state.railSig) return;
    state.railSig = sig;
    var list = $('office-rail-list');
    var focused = list.contains(document.activeElement) ? document.activeElement.dataset.agent : null;
    list.innerHTML = Rail.markup(data, Date.now());
    list.querySelectorAll('.rail-row').forEach(function (row) {
      if (row.dataset.agent === state.linked) row.classList.add('linked');
      if (row.dataset.agent === focused) row.focus();
    });
  }

  /* Running clocks tick from the viewer's clock; everything else waits for a poll. */
  function tick() {
    var now = Date.now();
    $('office-rail-list').querySelectorAll('.elapsed[data-to=""]').forEach(function (el) {
      el.textContent = Rail.duration(el.dataset.from, null, now);
    });
  }

  /* The linked agent is remembered: figures and tethers are redrawn every
     frame and take their `linked` class from here. */
  function link(id, on) {
    if (!on && state.linked !== id) return;       // a later link already replaced this one
    state.linked = on ? id : '';
    $('office-rail-list').querySelectorAll('.rail-row[data-agent="' + CSS.escape(id) + '"]').forEach(function (el) {
      el.classList.toggle('linked', on);
    });
  }

  // ---------------------------------------------------------- frame loop
  var AT_DESK = { typing: 1, thinking: 1, dozing: 1, standing: 1, stalled: 1 };

  /* What to draw and say for a sim agent, a lead or an intern. */
  function figureFor(agent) {
    if (agent.kind === 'intern') {
      var info = state.subs[agent.id];
      var doing = info && info.sub.state === 'running' ? info.sub.doing : null;
      return info ? { html: Figures.intern(info.sub, agent.look, info.worker.session_id), model: info.sub.model,
                      scale: Figures.INTERN_SCALE, doing: doing, family: Cast.familyOf(doing),
                      label: info.sub.type + ': ' + info.sub.description } : null;
    }
    var worker = state.workers[agent.id];
    return worker ? { html: Figures.lead(worker, agent.look), model: worker.model, scale: 1,
                      doing: worker.doing, family: Cast.familyOf(worker.doing),
                      label: worker.title + (worker.state === 'asleep' ? ' (session waiting)' : '') } : null;
  }

  function place(agent, el) {
    var pose = agent.state.pose;
    var svg = $('office-scene');
    var layer = AT_DESK[pose]
      ? svg.querySelector('.seated[data-layer="' + agent.places.room + ':' + agent.places.row + '"]')
      : svg.querySelector('.mobile');
    if (layer && el.parentNode !== layer) layer.appendChild(el);
  }

  function frame(now, force) {
    var dt = Math.min(0.05, (now - (state.last || now)) / 1000);
    state.last = now;
    if (!force) sim.tick(dt);
    var svg = $('office-scene');
    if (!svg.querySelector('.mobile')) return;
    var live = {};
    sim.list().forEach(function (agent) {
      var fig = figureFor(agent);
      if (!fig) return;
      live[agent.id] = true;
      var el = state.els[agent.id];
      if (!el) {
        var tpl = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        tpl.innerHTML = fig.html;
        el = tpl.firstChild;
        state.els[agent.id] = el;
      }
      place(agent, el);
      var p = World.iso(agent.pos.x, agent.pos.y);
      var pose = agent.state.pose;
      var fam = fig.family;
      if ((el.dataset.family || '') !== (fam || '')) { if (el.dataset.family) agent.popped = true; el.dataset.family = fam || ''; }
      el.setAttribute('transform', 'translate(' + p.x.toFixed(1) + ',' + p.y.toFixed(1) + ') scale(' +
        (Cast.tierOf(fig.model).scale * fig.scale) + ')');
      el.setAttribute('class', 'worker tier-' + agent.tier + ' ' + pose + (agent.kind === 'intern' ? ' intern' : '') +
        (fam ? ' tool-' + fam : '') + (agent.leaving ? ' leaving' : '') + (agent.bubble ? ' talking' : '') +
        (state.linked === agent.id ? ' linked' : ''));
      el.setAttribute('aria-label', fig.label + ', ' + pose);
      el.querySelector('.flip').setAttribute('transform', 'scale(' + agent.facing + ',1)');
      if (agent.popped) {
        agent.popped = false;
        el.classList.remove('pop'); void el.getBoundingClientRect(); el.classList.add('pop');
      }
    });
    Object.keys(state.els).forEach(function (id) {
      if (!live[id]) { state.els[id].remove(); delete state.els[id]; }
    });
    drawTethers(svg);
  }

  /* A thin line from every intern to whoever spawned it, lead or intern;
     dashed when the agent runs in the background. Redrawn every frame, there
     are only ever a handful. */
  function drawTethers(svg) {
    var layer = svg.querySelector('.tethers');
    if (!layer) return;
    var out = '';
    sim.list().forEach(function (agent) {
      if (agent.kind !== 'intern') return;
      var parent = sim.agents[agent.parentId] || sim.agents[agent.lead];
      if (!parent) return;
      var a = World.iso(agent.pos.x, agent.pos.y), b = World.iso(parent.pos.x, parent.pos.y);
      var lift = parent.kind === 'intern' ? Figures.INTERN_SCALE : 1;     // chest height of the parent
      out += '<line class="tether' + (agent.background ? ' bg' : '') + (state.linked === agent.id ? ' linked' : '') +
        '" data-agent="' + esc(agent.id) + '" x1="' + b.x.toFixed(1) + '" y1="' + (b.y - 16 * lift).toFixed(1) +
        '" x2="' + a.x.toFixed(1) + '" y2="' + (a.y - 12 * Figures.INTERN_SCALE).toFixed(1) + '"/>';
    });
    layer.innerHTML = out;
  }

  function loop(now) {
    if (!state.visible) { state.frame = null; return; }
    frame(now, false);
    state.frame = requestAnimationFrame(loop);
  }

  // ---------------------------------------------------------------- card
  function since(ts) {
    var s = (Date.now() - new Date(ts).getTime()) / 1000;
    if (s < 90) return 'a moment';
    if (s < 3600) return Math.round(s / 60) + ' min';
    if (s < 86400) return Math.round(s / 3600) + ' h';
    return Math.round(s / 86400) + ' d';
  }

  var DOING = { typing: 'typing away', thinking: 'thinking', dozing: 'dozing at the desk', walking: 'on the move',
                drinking: 'at the water cooler', chatting: 'chatting in the aisle', lounging: 'out at the canteen',
                standing: 'at work beside the desk', stalled: 'stuck', handing: 'handing back' };

  function internCard(info) {
    var sub = info.sub;
    var running = sub.state === 'running';
    return '<b>' + esc(sub.type) + '</b><span class="meta">' + esc(sub.description) + '</span>' +
      '<span class="meta">' + Rail.metaLine(sub, Date.now()) + '</span>' +
      (running && sub.doing ? '<div class="msg"><small>Doing</small>' + esc(Rail.doingText(sub.doing)) + '</div>' : '');
  }

  function leadCard(worker) {
    var agent = sim.agents[worker.session_id];
    var head = (worker.state === 'working' ? 'Working for ' : 'Quiet for ') + since(worker.since);
    var doing = agent ? DOING[agent.state.pose] || '' : '';
    var tool = worker.doing ? worker.doing.tool : '';
    var running = (worker.agents || []).filter(function (sub) { return sub.state === 'running'; }).length;
    return '<b>' + esc(worker.title) + '</b><span class="meta">' + esc(head) +
      (doing ? ' · ' + esc(doing) : '') + (tool ? ' · ' + esc(tool) : '') +
      (worker.model ? ' · ' + esc(worker.model) : '') + '</span>' +
      (running ? '<span class="meta">' + running + (running === 1 ? ' intern' : ' interns') + ' running</span>' : '') +
      (worker.last_message ? '<div class="msg"><small>Waiting on you</small>' + esc(worker.last_message) + '</div>' : '');
  }

  function showCard(el) {
    var info = state.subs[el.dataset.agent];
    var worker = state.workers[el.dataset.agent];
    if (!info && !worker) return;
    var card = $('office-card');
    card.innerHTML = info ? internCard(info) : leadCard(worker);
    var stage = $('office-stage').getBoundingClientRect();
    var box = el.getBoundingClientRect();
    card.hidden = false;
    var left = box.left - stage.left + box.width / 2 - card.offsetWidth / 2;
    left = Math.max(0, Math.min(left, stage.width - card.offsetWidth));
    var top = box.top - stage.top - card.offsetHeight - 8;
    if (top < 0) top = box.bottom - stage.top + 8;
    card.style.left = left + 'px';
    card.style.top = top + 'px';
  }

  function hideCard() { $('office-card').hidden = true; }

  // ------------------------------------------------------------- polling
  function poll() {
    fetch('/api/office', { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(render).catch(function () {
      $('office-hint').textContent = 'Could not reach the server; showing the last office';
    });
  }

  function setVisible(on) {
    state.visible = on;
    clearInterval(state.timer);
    state.timer = null;
    clearInterval(state.ticker);
    state.ticker = on ? setInterval(tick, 1000) : null;
    if (on) {
      poll();
      state.timer = setInterval(poll, POLL_MS);
      if (!state.frame) { state.last = 0; state.frame = requestAnimationFrame(loop); }
    }
  }

  // ----------------------------------------------------------- listeners
  var scene = $('office-scene');
  function open(el) { hideCard(); if (window.usageDashboard) window.usageDashboard.openSession(el.dataset.session); }
  scene.addEventListener('click', function (e) { var w = e.target.closest('.worker'); if (w) open(w); });
  scene.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var w = e.target.closest('.worker');
    if (w) { e.preventDefault(); open(w); }
  });
  scene.addEventListener('mouseover', function (e) {
    var w = e.target.closest('.worker');
    if (w) { showCard(w); link(w.dataset.agent, true); }
  });
  scene.addEventListener('mouseout', function (e) {
    var w = e.target.closest('.worker');
    if (w) { hideCard(); link(w.dataset.agent, false); }
  });
  var rail = $('office-rail-list');
  rail.addEventListener('mouseover', function (e) { var r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, true); });
  rail.addEventListener('mouseout', function (e) { var r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, false); });
  rail.addEventListener('focusin', function (e) { var r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, true); });
  rail.addEventListener('focusout', function (e) { var r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, false); });
  rail.addEventListener('click', function (e) {
    var r = e.target.closest('.rail-row');
    if (r && window.usageDashboard) window.usageDashboard.openSession(r.dataset.session);
  });
  $('office-rail-toggle').addEventListener('click', function () {
    var aside = $('office-rail');
    var collapsed = aside.classList.toggle('collapsed');
    this.setAttribute('aria-expanded', String(!collapsed));
    this.textContent = collapsed ? 'Show' : 'Hide';
    if (state.data) { state.sig = ''; render(state.data); }      // the stage got wider or narrower
  });
  scene.addEventListener('focusin', function (e) { var w = e.target.closest('.worker'); if (w) { showCard(w); link(w.dataset.agent, true); } });
  scene.addEventListener('focusout', function (e) {
    var w = e.target.closest('.worker');
    hideCard();
    if (w) link(w.dataset.agent, false);
  });
  document.addEventListener('usage:pane', function (e) { setVisible(e.detail === 'office'); });
  var resizeTimer = null;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (state.visible && state.data) { state.sig = ''; render(state.data); } }, 120);
  });

  setVisible(!$('pane-office').hidden);
})();
