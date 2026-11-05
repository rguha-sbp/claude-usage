/* Office tab (spec Sec 15): every live Claude Code session is a worker at a
   desk on an open-plan office floor, one neighbourhood per project, drawn
   in three.js. Reads /api/office; the payload is bound to `data`, a room to
   `room`, a worker to `worker` and a sub-agent to `sub`
   (tests/test_dashboard_static.py checks those accesses).

   Who does what:
   - office-cast.js: who a worker is (tier, tool family) and the daylight.
   - office-themes.js: the three looks of the floor.
   - office-floor.js: the layout, from the payload and the theme.
   - office-sim.js: what each worker does and where it stands.
   - office-3d.js and the modules it imports: the drawing.
   - office-rail.js: the Agents rail.
   This file is the controller: it fetches, feeds the simulation, tells the
   view what to draw, and owns the hover card, the theme picker, the
   screen-reader roster and the rail's links. app.js tells it when the
   Office pane shows. */
import { OfficeView } from './office-3d.js';

const { esc, fmtTokens } = window.UsageFormat;
const Cast = window.OfficeCast, Rail = window.OfficeRail, Floor = window.OfficeFloor, Themes = window.OfficeThemes;
const $ = (id) => document.getElementById(id);
const POLL_MS = 5000;
const INTERN_SCALE = 0.82;
const THEME_KEY = 'usage.office.theme';
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const params = new URLSearchParams(location.search);

/* `use(localStorage)`, or null when storage is blocked: then reading the
   `localStorage` property itself throws SecurityError, and the Office runs
   on without a remembered theme. */
function stored(use) {
  try { return use(localStorage); } catch (e) { return null; }
}

const sim = new window.OfficeSim.Sim({ wander: !reduceMotion });
const state = { data: null, timer: null, ticker: null, visible: false, workers: {}, subs: {}, cast: {}, sig: '',
                plan: null, railSig: null, rosterSig: '', linked: '', card: null, view: null,
                theme: Themes.themeOf(params.get('office') || stored((s) => s.getItem(THEME_KEY))) };

// -------------------------------------------------------------- render
function counts(data) {
  let total = 0, working = 0;
  data.rooms.forEach((room) => room.workers.forEach((worker) => {
    total += 1;
    if (worker.state === 'working') working += 1;
  }));
  return { total, working };
}

function clock(ts) {
  return ts ? new Date(ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '';
}

/* `?hour=NN` previews another time of day. */
function sky() {
  return Cast.skyOf(params.has('hour') ? params.get('hour') : new Date().getHours());
}

function money(v) { return '$' + Number(v || 0).toFixed(2); }

/* The notice board in the kitchen: the day's real numbers (spec Sec 12.7). */
function boardLines(data, n) {
  const board = data.board || {};
  return [
    { text: 'TODAY', size: 0.8, bold: true, color: '#f2cc8f' },
    { text: board.unit === 'usd' ? money(board.value) : fmtTokens(board.value) + ' tokens', size: 1.5, bold: true },
    { text: 'busiest: ' + (board.top_project || 'nobody yet'), size: 0.9 },
    { text: n.working + ' working · ' + (n.total - n.working) + ' asleep', size: 0.9 }
  ];
}

/* What the floor is built from: the theme, the light, and every room's
   project and sessions with their tiers. The plan (office-floor.js) and
   the view's scenery are rebuilt only when it changes, not on every poll. */
function floorSig(data, light) {
  return state.theme.key + '@' + light.key + '@' + data.rooms.map((room) => room.project + ':' +
    room.workers.map((worker) => worker.session_id + '/' + Cast.tierOf(worker.model).key).join(',')).join('|');
}

/* Interns past the pod's slots: counted on the desk instead. */
function extraOf(worker) {
  return (worker.agents || []).filter((sub) => sub.state !== 'done').length - Floor.POD;
}

function render(data) {
  state.data = data;
  renderRail(data);
  const stage = $('office-stage');
  const n = counts(data);
  $('office-hint').textContent = n.total
    ? n.working + ' working · ' + (n.total - n.working) + ' asleep · updated ' + clock(data.generated_at)
    : 'No session has reported in yet';
  state.workers = {};
  state.subs = {};
  const flat = [];
  data.rooms.forEach((room) => room.workers.forEach((worker) => {
    state.workers[worker.session_id] = worker;
    flat.push(worker);
    (worker.agents || []).forEach((sub) => { state.subs[worker.session_id + '/' + sub.agent_id] = { worker, sub }; });
  }));
  const light = sky();
  const sig = n.total ? floorSig(data, light) : '';
  const fresh = sig !== state.sig;
  if (fresh) { state.sig = sig; state.plan = sig ? Floor.build(data, state.theme) : null; }
  const plan = state.plan;
  sim.sync(flat, plan ? plan.places : {});
  remember();
  markVacant();
  if (!plan) { renderRoster(); return; }
  stage.style.setProperty('--sky-top', light.top);
  stage.style.setProperty('--sky-bottom', light.bottom);
  stage.classList.toggle('night', light.stars);

  const view = state.view;
  if (view) {
    if (fresh) {
      const desks = {};
      flat.forEach((worker) => { desks[worker.session_id] = { tier: Cast.tierOf(worker.model) }; });
      view.setFloor(plan, state.theme, { night: light.lamps, desks }, light);
    }
    flat.forEach((worker) => view.scenery.setDesk(worker.session_id,
      { working: worker.state === 'working', title: worker.title, extra: extraOf(worker) }));
    view.scenery.setBoard(boardLines(data, n));
    if (reduceMotion && state.visible) view.still();
  }
  renderRoster();
}

/* The empty-office note replaces the canvas only once the last leaver is
   out of the door: a session that ends leaves the payload at once, but
   its worker still walks out. Checked on every poll and every frame. */
function markVacant() {
  $('office-stage').classList.toggle('vacant', !sim.list().length);
}

/* Who each agent on the floor is, kept until it has walked out: a session
   that ends leaves the payload at once, but its worker still waves goodbye
   on the way to the door. */
function remember() {
  Object.keys(state.workers).forEach((id) => { state.cast[id] = { model: state.workers[id].model }; });
  Object.keys(state.subs).forEach((id) => { state.cast[id] = { model: state.subs[id].sub.model, type: state.subs[id].sub.type }; });
  Object.keys(state.cast).forEach((id) => { if (!sim.agents[id]) delete state.cast[id]; });
}

// ---------------------------------------------------------------- rail
function renderRail(data) {
  const sig = Rail.signature(data);
  if (sig === state.railSig) return;
  state.railSig = sig;
  const list = $('office-rail-list');
  const focused = list.contains(document.activeElement) ? document.activeElement.dataset.agent : null;
  list.innerHTML = Rail.markup(data, Date.now());
  list.querySelectorAll('.rail-row').forEach((row) => {
    if (row.dataset.agent === state.linked) row.classList.add('linked');
    if (row.dataset.agent === focused) row.focus();
  });
}

/* Running clocks tick from the viewer's clock; everything else waits for a poll. */
function tick() {
  $('office-rail-list').querySelectorAll('.elapsed[data-to=""]').forEach((el) => {
    el.textContent = Rail.duration(el.dataset.from, null, Date.now());
  });
}

/* One agent is linked at a time: its figure wears a ring and its rail row lights up. */
function link(id, on) {
  if (!on && state.linked !== id) return;       // a later link already replaced this one
  state.linked = on ? id : '';
  $('office-rail-list').querySelectorAll('.rail-row[data-agent="' + CSS.escape(id) + '"]').forEach((el) => {
    el.classList.toggle('linked', on);
  });
  if (state.view) state.view.request();
}

// -------------------------------------------------------------- figures
/* What the view draws for one sim agent, lead or intern. */
function figureOf(agent) {
  const who = state.cast[agent.id];
  if (!who) return null;
  const intern = agent.kind === 'intern';
  const info = state.subs[agent.id], worker = state.workers[agent.id];
  const tier = intern ? Cast.internTierOf(who.model) : Cast.tierOf(who.model);
  const live = intern ? info && info.sub.state === 'running' && info.sub : worker;
  const doing = live ? live.doing : null;
  const badge = intern ? Cast.badgeOf(who.type) : null;
  const partner = agent.state.partner;
  const popped = agent.popped;
  agent.popped = false;
  return {
    id: agent.id, key: tier.key + '|' + badge,
    spec: { id: agent.id, seed: Cast.hash(agent.id), look: agent.look, tier, intern, badge,
            scale: tier.scale * (intern ? INTERN_SCALE : 1) },
    pose: agent.state.pose, x: agent.pos.x, y: agent.pos.y, vel: agent.vel, facing: agent.facing,
    partner: partner ? partner.pos : null, family: Cast.familyOf(doing), talking: agent.bubble,
    linked: state.linked === agent.id, popped, leaving: agent.leaving,
    parent: intern ? (sim.agents[agent.parentId] ? agent.parentId : agent.lead) : null, background: agent.background
  };
}

function figures() { return sim.list().map(figureOf).filter(Boolean); }

// ---------------------------------------------------------------- card
function since(ts) {
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 90) return 'a moment';
  if (s < 3600) return Math.round(s / 60) + ' min';
  if (s < 86400) return Math.round(s / 3600) + ' h';
  return Math.round(s / 86400) + ' d';
}

const DOING = { typing: 'typing away', thinking: 'thinking', dozing: 'dozing at the desk', walking: 'on the move',
                drinking: 'at the water cooler', chatting: 'chatting in the aisle', lounging: 'on a break',
                standing: 'at work beside the desk', handing: 'handing back' };

function internCard(info) {
  const sub = info.sub;
  const running = sub.state === 'running';
  return '<b>' + esc(sub.type) + '</b><span class="meta">' + esc(sub.description) + '</span>' +
    '<span class="meta">' + Rail.metaLine(sub, Date.now()) + '</span>' +
    (running && sub.doing ? '<div class="msg"><small>Doing</small>' + esc(Rail.doingText(sub.doing)) + '</div>' : '');
}

function leadCard(worker) {
  const agent = sim.agents[worker.session_id];
  const head = (worker.state === 'working' ? 'Working for ' : 'Quiet for ') + since(worker.since);
  const doing = agent ? DOING[agent.state.pose] || '' : '';
  const tool = worker.doing ? worker.doing.tool : '';
  const running = (worker.agents || []).filter((sub) => sub.state === 'running').length;
  return '<b>' + esc(worker.title) + '</b><span class="meta">' + esc(head) +
    (doing ? ' · ' + esc(doing) : '') + (tool ? ' · ' + esc(tool) : '') +
    (worker.model ? ' · ' + esc(worker.model) : '') + '</span>' +
    (running ? '<span class="meta">' + running + (running === 1 ? ' intern' : ' interns') + ' running</span>' : '') +
    (worker.last_message ? '<div class="msg"><small>Waiting on you</small>' + esc(worker.last_message) + '</div>' : '');
}

function showCard(id) {
  const info = state.subs[id], worker = state.workers[id];
  if (!id || (!info && !worker)) return hideCard();
  const card = $('office-card');
  card.innerHTML = info ? internCard(info) : leadCard(worker);
  card.hidden = false;
  state.card = id;
  placeCard(id);
}

/* Above the figure's head, kept inside the stage; below it near the top.
   All in the stage's own space, where the card is positioned:
   `screenOf` is in the canvas's, which sits inside the stage. */
function placeCard(id) {
  const at = state.view && state.view.screenOf(id);
  if (!at) return;
  const card = $('office-card');
  const stage = $('office-stage').getBoundingClientRect(), canvas = state.view.canvas.getBoundingClientRect();
  const x = canvas.left - stage.left + at.x, y = canvas.top - stage.top + at.y;
  const left = Math.max(0, Math.min(x - card.offsetWidth / 2, stage.width - card.offsetWidth));
  const top = y - card.offsetHeight - 14 < 0 ? y + 40 : y - card.offsetHeight - 14;
  card.style.left = left + 'px';
  card.style.top = top + 'px';
}

function hideCard() {
  $('office-card').hidden = true;
  state.card = null;
}

/* The pointer or the roster focus is on `id`, or on nobody. The link
   follows it whether or not `id` has a card (a leaver no longer does). */
function hover(id) {
  if (state.linked && state.linked !== id) link(state.linked, false);
  if (id) { showCard(id); link(id, true); } else hideCard();
}

function open(id) {
  hover(null);
  const intern = state.subs[id];
  window.usageDashboard.openSession(intern ? intern.worker.session_id : id);
}

// -------------------------------------------------------------- roster
/* The canvas is one picture; screen readers and keyboards get a list of
   every worker instead, in the order of the rooms. */
function renderRoster() {
  const rows = [];
  sim.list().forEach((agent) => {
    const info = state.subs[agent.id], worker = state.workers[agent.id];
    if (worker) rows.push([agent.id, worker.title + ', ' + (worker.state === 'working' ? 'working' : 'waiting') +
      (worker.model ? ', ' + worker.model : '')]);
    else if (info) rows.push([agent.id, 'Intern ' + info.sub.type + ' for ' + info.worker.title + ': ' + info.sub.description]);
  });
  const sig = JSON.stringify(rows);
  if (sig === state.rosterSig) return;
  state.rosterSig = sig;
  $('office-roster').innerHTML = rows.map((r) =>
    '<li><button type="button" data-agent="' + esc(r[0]) + '">' + esc(r[1]) + '</button></li>').join('');
}

// --------------------------------------------------------------- theme
function renderThemes() {
  const seg = $('office-theme');
  seg.insertAdjacentHTML('beforeend', Themes.THEMES.map((t) =>
    '<button type="button" role="radio" aria-checked="false" aria-selected="false" data-office="' + t.key + '">' +
    esc(t.label) + '</button>').join(''));
  markTheme();
}

function markTheme() {
  const seg = $('office-theme');
  window.usageDashboard.selectSeg(seg, seg.querySelector('[data-office="' + state.theme.key + '"]'));
}

/* A pick beats a `?office=` preview: the parameter goes, or a reload
   (app.js syncUrl keeps the query) would bring the preview back. */
function chooseTheme(key) {
  state.theme = Themes.themeOf(key);
  stored((s) => s.setItem(THEME_KEY, state.theme.key));
  const url = new URL(location.href);
  if (url.searchParams.has('office')) { url.searchParams.delete('office'); history.replaceState(null, '', url); }
  markTheme();
  if (state.data) render(state.data);
}

// ------------------------------------------------------------- polling
function poll() {
  fetch('/api/office', { cache: 'no-store' }).then((r) => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(render).catch(() => {
    $('office-hint').textContent = 'Could not reach the server; showing the last office';
  });
}

function setVisible(on) {
  state.visible = on;
  clearInterval(state.timer);
  clearInterval(state.ticker);
  state.timer = on ? setInterval(poll, POLL_MS) : null;
  state.ticker = on ? setInterval(tick, 1000) : null;
  if (on) { poll(); markTheme(); }
  if (state.view) state.view.setActive(on);
}

// ---------------------------------------------------------------- boot
try {
  state.view = new OfficeView($('office-view'), {
    advance: (dt) => sim.tick(dt), figures, pick: open, hover,
    drawn: () => { if (state.card) placeCard(state.card); markVacant(); }
  }, reduceMotion);
  state.view.canvas.setAttribute('role', 'img');
  state.view.canvas.setAttribute('aria-label', 'The office floor: live Claude Code sessions as workers at their desks, ' +
    'one neighbourhood per project, with their sub-agents as interns beside them. The list below names every worker.');
} catch (e) {
  $('office-view').innerHTML = '<div class="empty"><b>The office needs WebGL</b>' +
    '<span>This browser could not start WebGL, so the floor cannot be drawn. The Agents rail still lists every session.</span></div>';
}

renderThemes();
$('office-theme').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b) chooseTheme(b.dataset.office);
});
const roster = $('office-roster');
roster.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) open(b.dataset.agent); });
roster.addEventListener('focusin', (e) => { const b = e.target.closest('button'); if (b) hover(b.dataset.agent); });
roster.addEventListener('focusout', () => hover(null));
const rail = $('office-rail-list');
rail.addEventListener('mouseover', (e) => { const r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, true); });
rail.addEventListener('mouseout', (e) => { const r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, false); });
rail.addEventListener('focusin', (e) => { const r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, true); });
rail.addEventListener('focusout', (e) => { const r = e.target.closest('.rail-row'); if (r) link(r.dataset.agent, false); });
rail.addEventListener('click', (e) => {
  const r = e.target.closest('.rail-row');
  if (r) window.usageDashboard.openSession(r.dataset.session);
});
$('office-rail-toggle').addEventListener('click', function () {
  const collapsed = $('office-rail').classList.toggle('collapsed');
  this.setAttribute('aria-expanded', String(!collapsed));
  this.textContent = collapsed ? 'Show' : 'Hide';
});
document.addEventListener('usage:pane', (e) => setVisible(e.detail === 'office'));
setVisible(!$('pane-office').hidden);
