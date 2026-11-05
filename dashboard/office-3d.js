/* The office in three.js (spec Sec 15.6): the camera, the light, the
   people on the floor and the frame loop. office.js owns the data and the
   simulation; this file only draws what it is told and reports what the
   pointer touches.

   Drawing is on demand. The loop runs only while the Office tab and the
   page are visible; it draws at full rate while anyone walks or the
   pointer drags, and at about 15 frames a second otherwise, for the
   typing hands and the breathing. Under reduced motion there is no loop:
   `still()` advances the simulation and draws one frame per update.

   `hooks`: advance(dt) runs the simulation and returns true while anyone
   walks; figures() lists what to draw (office.js `figureOf`); pick(id)
   and hover(id or null) report the pointer; drawn() follows every frame. */
import * as THREE from 'three';
import { buildScenery } from './office-scenery.js';
import { Person, SITTING } from './office-people.js';

const VIEW = new THREE.Vector3(1, 0.816, 1).normalize();     // toward the camera: the classic isometric angle
const SUN = new THREE.Vector3(-0.55, 1, -0.35).normalize();  // in through the back-left windows
const AMBIENT_MS = 66;
const ZOOM = [1, 4];
const CLICK_PX = 4;
const STEP = 0.05;            // the simulation's step, in seconds, when catching up
const CATCH_UP = 10;          // and the most it catches up in one go
const WAVE_RANGE = 2.5;       // a leaver waves goodbye this close to the door

export class OfficeView {
  constructor(host, hooks, still) {
    this.host = host;
    this.hooks = hooks;
    this.stillMode = still;
    const renderer = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });   // throws without WebGL
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.shadowMap.autoUpdate = false;          // the scenery never moves: draw the shadows once per build
    this.canvas = renderer.domElement;
    host.appendChild(this.canvas);

    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 200);
    this.target = new THREE.Vector3();
    this.hemi = new THREE.HemisphereLight();
    this.sun = new THREE.DirectionalLight();
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0004;
    this.sun.shadow.normalBias = 0.02;
    this.figures = new THREE.Group();
    this.tethers = {
      solid: new THREE.LineSegments(lineGeometry(0), new THREE.LineBasicMaterial({ color: '#5b6b7d', transparent: true, opacity: 0.7 })),
      dashed: new THREE.LineSegments(lineGeometry(0), new THREE.LineDashedMaterial({ color: '#5b6b7d', dashSize: 0.08, gapSize: 0.06, transparent: true, opacity: 0.7 }))
    };
    /* Tethers follow walkers anywhere on the floor and are rewritten every
       frame: never cull them on a stale bounding sphere. */
    this.tethers.solid.frustumCulled = this.tethers.dashed.frustumCulled = false;
    this.scene.add(this.hemi, this.sun, this.sun.target, this.figures, this.tethers.solid, this.tethers.dashed);

    this.people = new Map();
    this.plan = null;
    this.scenery = null;
    this.active = false;
    this.raf = null;
    this.last = 0;
    this.drawn = 0;
    this.clock = 0;
    this.busy = false;
    this.dirty = true;
    this.hovered = null;
    this.drag = null;
    this.ray = new THREE.Raycaster();

    this.loop = this.loop.bind(this);
    this.listen();
    new ResizeObserver(() => { if (this.plan) { this.fit(); this.request(); } }).observe(host);
  }

  /* A new floor: theme, layout, desks or light changed. */
  setFloor(plan, theme, dress, sky) {
    if (this.scenery) { this.scene.remove(this.scenery.root); this.scenery.dispose(); }
    /* A floor of the same size keeps the reader's pan and zoom: a new
       intern or a theme swap should not throw the view back out. */
    const reframe = !this.plan || this.plan.W !== plan.W || this.plan.D !== plan.D;
    this.plan = plan;
    this.scenery = buildScenery(plan, theme, dress);
    this.scene.add(this.scenery.root);
    const light = theme.light;
    this.hemi.color.set(light.sky);
    this.hemi.groundColor.set(light.ground);
    this.hemi.intensity = light.hemi * sky.light;
    this.sun.color.set(light.sun);
    this.sun.intensity = light.sunI * sky.light * (sky.lamps ? 0.55 : 1);

    const centre = new THREE.Vector3(plan.W / 2, 0, plan.D / 2);
    const reach = Math.hypot(plan.W, plan.D) / 2 + 1;
    this.sun.target.position.copy(centre);
    this.sun.position.copy(centre).addScaledVector(SUN, 30);
    Object.assign(this.sun.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 1, far: 60 });
    this.sun.shadow.camera.updateProjectionMatrix();
    this.renderer.shadowMap.needsUpdate = true;

    if (reframe) { this.target.copy(centre); this.camera.zoom = 1; }
    this.fit();
    this.request();
  }

  // ------------------------------------------------------------- camera
  /* Size the canvas to the floor's shape and fit the whole floor in it. */
  fit() {
    const { W, D, wallH } = this.plan;
    this.place();
    this.camera.updateMatrixWorld();
    const box = new THREE.Box3();
    const v = new THREE.Vector3();
    [0, W].forEach((x) => [0, D].forEach((z) => [0, wallH].forEach((y) => {
      box.expandByPoint(v.set(x, y, z).applyMatrix4(this.camera.matrixWorldInverse));
    })));
    const bw = box.max.x - box.min.x, bh = box.max.y - box.min.y;
    const width = Math.max(320, this.host.clientWidth);
    const height = Math.round(Math.min(Math.max(width * bh / bw, 380), window.innerHeight * 0.8));
    this.renderer.setSize(width, height);
    const aspect = width / height;
    const halfH = Math.max(bh, bw / aspect) / 2 * 1.03, halfW = halfH * aspect;
    const cx = (box.min.x + box.max.x) / 2, cy = (box.min.y + box.max.y) / 2;
    Object.assign(this.camera, { left: cx - halfW, right: cx + halfW, top: cy + halfH, bottom: cy - halfH });
    this.camera.updateProjectionMatrix();
  }

  place() {
    this.camera.position.copy(this.target).addScaledVector(VIEW, 60);
    this.camera.lookAt(this.target);
  }

  /* The floor point under a client position, or null off the floor plane. */
  groundAt(clientX, clientY) {
    const r = this.canvas.getBoundingClientRect();
    this.ray.setFromCamera(new THREE.Vector2((clientX - r.left) / r.width * 2 - 1, -(clientY - r.top) / r.height * 2 + 1), this.camera);
    return this.ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
  }

  /* Move the view so `from` (a floor point) ends up where `to` is now. */
  pan(from, to) {
    this.target.add(from.sub(to));
    this.target.x = Math.min(Math.max(this.target.x, 0), this.plan.W);
    this.target.z = Math.min(Math.max(this.target.z, 0), this.plan.D);
    this.place();
    this.camera.updateMatrixWorld();
    this.request();
  }

  /* The figure under a client position, or null. */
  pickAt(clientX, clientY) {
    const r = this.canvas.getBoundingClientRect();
    this.ray.setFromCamera(new THREE.Vector2((clientX - r.left) / r.width * 2 - 1, -(clientY - r.top) / r.height * 2 + 1), this.camera);
    const hit = this.ray.intersectObjects([...this.people.values()].map((p) => p.proxy), false)[0];
    return hit ? hit.object.userData.agent : null;
  }

  /* Where a figure's head is, in pixels from the canvas's top left. */
  screenOf(id) {
    const person = this.people.get(id);
    if (!person) return null;
    const p = person.headAt(new THREE.Vector3()).project(this.camera);
    return { x: (p.x + 1) / 2 * this.canvas.clientWidth, y: (1 - p.y) / 2 * this.canvas.clientHeight };
  }

  listen() {
    const c = this.canvas;
    c.addEventListener('pointerdown', (e) => {
      if (!this.plan) return;
      this.drag = { x: e.clientX, y: e.clientY, at: this.groundAt(e.clientX, e.clientY), moved: false };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener('pointermove', (e) => {
      if (this.drag) {
        const d = this.drag;
        d.moved = d.moved || Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_PX;
        const now = this.groundAt(e.clientX, e.clientY);
        if (d.moved && d.at && now) this.pan(d.at.clone(), now);
        return;
      }
      const id = this.pickAt(e.clientX, e.clientY);
      c.style.cursor = id ? 'pointer' : 'grab';
      if (id !== this.hovered) { this.hovered = id; this.hooks.hover(id); }
    });
    c.addEventListener('pointerup', (e) => {
      const d = this.drag;
      this.drag = null;
      if (d && !d.moved) {
        const id = this.pickAt(e.clientX, e.clientY);
        if (id) this.hooks.pick(id);
      }
    });
    c.addEventListener('pointerleave', () => {
      if (this.hovered) { this.hovered = null; this.hooks.hover(null); }
    });
    /* Zoom about the pointer; at either limit the page scrolls instead. */
    c.addEventListener('wheel', (e) => {
      if (!this.plan) return;
      const zoom = Math.min(ZOOM[1], Math.max(ZOOM[0], this.camera.zoom * Math.exp(-e.deltaY * 0.0015)));
      if (zoom === this.camera.zoom) return;
      e.preventDefault();
      const before = this.groundAt(e.clientX, e.clientY);
      this.camera.zoom = zoom;
      this.camera.updateProjectionMatrix();
      const after = this.groundAt(e.clientX, e.clientY);
      if (before && after) this.pan(before, after);
      else this.request();
    }, { passive: false });
    document.addEventListener('visibilitychange', () => this.kick());
  }

  // -------------------------------------------------------------- people
  /* Seat, heading and pose for one figure from office.js. */
  situate(f) {
    const seat = SITTING[f.pose] ? this.seatAt(f.x, f.y) : null;
    const speed = Math.hypot(f.vel.x, f.vel.y);
    const door = this.plan.door;
    const waving = f.leaving && Math.hypot(door.x - f.x, door.y - f.y) < WAVE_RANGE;
    let heading = null;
    if (seat) heading = seat.face;
    else if (f.pose === 'walking') heading = speed > 0.05 ? f.vel : null;
    else if (f.partner) heading = { x: f.partner.x - f.x, y: f.partner.y - f.y };
    else heading = { x: f.facing + 0.6, y: 0.6 - f.facing };          // three-quarters to the viewer, as the sim faces
    return { pose: waving ? 'waving' : f.pose, x: f.x, y: f.y, heading: heading, seat: seat, family: f.family,
             talking: f.talking, linked: f.linked, popped: f.popped, speed: speed / f.spec.scale };
  }

  seatAt(x, y) {
    let best = null, bestD = 0.15;
    this.plan.seats.forEach((s) => {
      const d = Math.hypot(s.x - x, s.y - y);
      if (d < bestD) { best = s; bestD = d; }
    });
    return best;
  }

  /* Bring the figures in line with the list: new ones built, gone ones
     dropped, a figure whose looks changed (its model moved tier) rebuilt. */
  cast(list, still) {
    const live = new Set();
    list.forEach((f) => {
      let person = this.people.get(f.id);
      if (person && person.key !== f.key) { this.figures.remove(person.root); person = null; }
      if (!person) {
        person = new Person(f.spec);
        person.key = f.key;
        this.people.set(f.id, person);
        this.figures.add(person.root);
      }
      live.add(f.id);
      person.pose(this.situate(f), still);
    });
    this.people.forEach((person, id) => {
      if (!live.has(id)) { this.figures.remove(person.root); this.people.delete(id); }
    });
  }

  /* A line from every intern to whoever spawned it; dashed for agents
     running in the background. */
  tether(list) {
    const lines = { solid: [], dashed: [] };
    list.forEach((f) => {
      const a = this.people.get(f.id), b = f.parent && this.people.get(f.parent);
      if (!a || !b) return;
      lines[f.background ? 'dashed' : 'solid'].push(chest(b), chest(a));
    });
    Object.keys(lines).forEach((k) => segments(this.tethers[k], lines[k]));
  }

  // ---------------------------------------------------------------- loop
  setActive(on) {
    this.active = on;
    this.hovered = null;          // a hidden canvas gets no pointerleave: the next move reports afresh
    if (on && this.stillMode) this.still();
    this.kick();
  }

  kick() {
    if (this.raf || !this.active || document.hidden || this.stillMode) return;
    this.last = performance.now();
    this.dirty = true;
    this.raf = requestAnimationFrame(this.loop);
  }

  /* Something changed that needs drawing: the next frame draws it, or,
     without a loop, draw now. */
  request() {
    this.dirty = true;
    if (this.stillMode) this.frame(0, true);
    else this.kick();
  }

  loop(now) {
    this.raf = null;
    if (!this.active || document.hidden) return;
    this.raf = requestAnimationFrame(this.loop);
    if (!this.busy && !this.dirty && !this.drag && now - this.drawn < AMBIENT_MS) return;
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    this.drawn = now;
    this.dirty = false;
    this.busy = this.hooks.advance(dt);
    this.clock += dt;
    this.frame(dt, false);
  }

  /* Reduced motion: catch the simulation up in small steps (a leaver
     reaches the door, an intern its slot) and draw where everyone is. */
  still() {
    const now = performance.now();
    let left = Math.min(CATCH_UP, this.last ? (now - this.last) / 1000 : 0);
    this.last = now;
    for (; left > 0; left -= STEP) this.hooks.advance(Math.min(STEP, left));
    this.frame(0, true);
  }

  frame(dt, still) {
    if (!this.plan) return;
    const list = this.hooks.figures();
    this.cast(list, still);
    this.people.forEach((person) => person.step(dt, this.clock));
    this.figures.updateMatrixWorld();
    this.tether(list);
    this.renderer.render(this.scene, this.camera);
    this.canvas.dataset.rendered = '1';
    this.hooks.drawn();
  }
}

/* Write `points` (pairs) into a LineSegments in place; new buffers only
   when the count changes. Each segment's own length goes into
   `lineDistance` for the dashes: `computeLineDistances()` would allocate a
   fresh attribute, and leak its GPU buffer, on every frame. */
function segments(line, points) {
  let g = line.geometry;
  if (g.attributes.position.count !== points.length) {
    g.dispose();
    g = line.geometry = lineGeometry(points.length);
  }
  g.setFromPoints(points);
  const d = g.attributes.lineDistance;
  for (let i = 0; i < points.length; i += 2) d.setX(i + 1, points[i].distanceTo(points[i + 1]));
  d.needsUpdate = true;
}

function lineGeometry(n) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(n * 3, 3));
  g.setAttribute('lineDistance', new THREE.Float32BufferAttribute(n, 1));
  return g;
}

const CHEST = 1.05;
function chest(person) {
  return person.root.position.clone().setY(CHEST * person.spec.scale);
}
