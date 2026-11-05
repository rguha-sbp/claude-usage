/* Who a worker is and what the light is like (spec Sec 12.6, 12.8). Pure
   data: no DOM, no state, so tests/office_cast_check.js can drive it under
   node. office.js reads a tier to dress the figure and its desk;
   office-sim.js reads `speed` so a small model really does move quicker. */
window.OfficeCast = (function () {
  'use strict';

  /* Tiers, richest first. `match` is tried against the lower-cased model
     name, so both the display name ("Opus 5 (1M context)") and the id
     ("claude-opus-5") land on the same tier. `core` is the fallback: an
     unknown model is an ordinary worker, never a decorated one. */
  var TIERS = [
    { key: 'lead', match: 'opus', label: 'Opus', crown: 'crown', aura: true, chair: 'gold',
      ornament: 'trophy', scale: 1.08, speed: 0.95 },
    { key: 'muse', match: 'fable', label: 'Fable', crown: 'monocle', aura: true, chair: 'gold',
      ornament: 'orchid', scale: 1.0, speed: 1.05 },
    { key: 'swift', match: 'haiku', label: 'Haiku', crown: 'headphones', aura: false, chair: 'plain',
      ornament: null, scale: 0.86, speed: 1.35 },
    { key: 'core', match: null, label: '', crown: null, aura: false, chair: 'plain',
      ornament: null, scale: 1.0, speed: 1.0 }
  ];
  var CORE = TIERS[TIERS.length - 1];

  function tierOf(model) {
    var name = String(model === null || model === undefined ? '' : model).toLowerCase();
    for (var i = 0; i < TIERS.length; i++) {
      if (TIERS[i].match && name.indexOf(TIERS[i].match) !== -1) return TIERS[i];
    }
    return CORE;
  }

  /* An intern dresses by its model too, but the crown and the aura belong
     to leads: whatever runs a sub-agent, it wears its lanyard instead. */
  function internTierOf(model) {
    var tier = tierOf(model);
    return Object.assign({}, tier, { crown: tier.crown === 'crown' ? null : tier.crown, aura: false });
  }

  /* The scene is lit by the viewer's own clock. `light` scales the room
     lightness, `lamps` turns the desk lamps on, `stars` fills the sky. */
  var SKIES = [
    { key: 'dawn', from: 5, to: 8, top: 'hsl(25 72% 70%)', bottom: 'hsl(42 86% 86%)', light: 0.94, lamps: false, stars: false },
    { key: 'day', from: 8, to: 17, top: 'hsl(205 68% 80%)', bottom: 'hsl(200 50% 94%)', light: 1, lamps: false, stars: false },
    { key: 'dusk', from: 17, to: 20, top: 'hsl(278 42% 48%)', bottom: 'hsl(22 82% 70%)', light: 0.84, lamps: true, stars: false },
    { key: 'night', from: 20, to: 29, top: 'hsl(232 46% 14%)', bottom: 'hsl(226 34% 30%)', light: 0.62, lamps: true, stars: true }
  ];

  function skyOf(hour) {
    var h = Number(hour);
    if (!isFinite(h)) h = 12;
    h = ((Math.floor(h) % 24) + 24) % 24;
    if (h < 5) h += 24;                       // the small hours belong to the night before
    for (var i = 0; i < SKIES.length; i++) {
      if (h >= SKIES[i].from && h < SKIES[i].to) return SKIES[i];
    }
    return SKIES[SKIES.length - 1];
  }

  /* FNV-1a: the one string hash the office uses (shirts, habits, badges). */
  function hash(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    return h >>> 0;
  }

  /* Tool families (spec Sec 13.4): one table for leads and interns. The
     family picks the pose class and the glyph over the figure's head. */
  var FAMILIES = {
    read: ['Read', 'Grep', 'Glob', 'LS'],
    edit: ['Edit', 'Write', 'MultiEdit', 'NotebookEdit'],
    shell: ['Bash', 'BashOutput'],
    web: ['WebFetch', 'WebSearch'],
    delegate: ['Agent', 'Task']
  };
  var FAMILY_OF = Object.create(null);     // no inherited keys: 'constructor' is not a tool
  Object.keys(FAMILIES).forEach(function (f) { FAMILIES[f].forEach(function (t) { FAMILY_OF[t] = f; }); });

  function familyOf(doing) {
    if (!doing || !doing.tool) return null;
    if (FAMILY_OF[doing.tool]) return FAMILY_OF[doing.tool];
    return doing.tool.indexOf('mcp__') === 0 ? 'mcp' : 'think';
  }

  /* An intern's lanyard: the same colour for the same agent type, always. */
  var BADGES = ['hsl(200, 70%, 48%)', 'hsl(150, 55%, 40%)', 'hsl(32, 85%, 50%)',      // commas: three.js
                'hsl(280, 50%, 56%)', 'hsl(350, 65%, 52%)', 'hsl(48, 85%, 45%)'];      // reads them too
  function badgeOf(type) { return BADGES[hash(String(type || '')) % BADGES.length]; }

  return { TIERS: TIERS, tierOf: tierOf, internTierOf: internTierOf, skyOf: skyOf, SKIES: SKIES,
           hash: hash, FAMILIES: FAMILIES, familyOf: familyOf, badgeOf: badgeOf };
})();
