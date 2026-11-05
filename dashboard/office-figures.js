/* The figures (spec Sec 12.3, 13.4): a lead per session and an intern per
   live sub-agent, as SVG strings with their origin between the feet.
   Pure: no DOM, so tests/office_figures_check.js drives it under node.
   office.js places and poses them; the pose and tool family are classes
   on the root, and office.css decides what each class shows. */
window.OfficeFigures = (function () {
  'use strict';

  var Cast = window.OfficeCast;
  var SHIRTS = ['var(--cat-1)', 'var(--cat-2)', 'var(--cat-3)', 'var(--cat-4)'];
  var HAIR = ['hsl(28 40% 22%)', 'hsl(40 60% 45%)', 'hsl(18 70% 30%)', 'hsl(0 0% 15%)', 'hsl(0 0% 60%)'];
  var INTERN_SCALE = 0.72;

  /* One glyph per tool family, floating over the head; office.css shows the
     one matching the figure's `tool-<family>` class. */
  var GLYPHS = '<g class="glyph" transform="translate(0 -46)">' +
    '<g class="g-read"><rect x="-5" y="-6" width="10" height="12" rx="1"/><path d="M-3 -2 H3 M-3 1 H3 M-3 4 H1"/></g>' +
    '<g class="g-edit"><path d="M-5 5 L3 -3 L5 -1 L-3 7 Z M-5 5 L-6 8 L-3 7"/></g>' +
    '<g class="g-shell"><rect x="-7" y="-5" width="14" height="10" rx="1.5"/><path d="M-4 -2 L-1 0 L-4 2 M0 2.5 H4"/></g>' +
    '<g class="g-web"><circle r="6"/><path d="M-6 0 H6 M0 -6 Q4 0 0 6 Q-4 0 0 -6"/></g>' +
    '<g class="g-delegate"><circle cx="-2" cy="-1" r="3"/><circle cx="4" cy="3" r="2"/><path d="M0 1 L3 2.5"/></g>' +
    '<g class="g-mcp"><path d="M-3 -6 V-2 M3 -6 V-2 M-5 -2 H5 V1 Q5 5 0 5 Q-5 5 -5 1 Z M0 5 V8"/></g>' +
    '</g>';

  var esc = window.UsageFormat.esc;
  function shirt(model) { return SHIRTS[Cast.hash(String(model || '')) % SHIRTS.length]; }

  function glasses() {
    return '<g class="glasses"><circle cx="-3" cy="-20" r="2.6"/><circle cx="3" cy="-20" r="2.6"/><path d="M-0.4 -20 H0.4"/></g>';
  }

  /* What the model's tier wears: a crown, a monocle, headphones -- or the
     figure's own glasses for everybody else. */
  function headgear(tier, look) {
    if (tier.crown === 'crown') {
      return '<path class="crown" d="M-6.5 -26 L-5 -33 L-2 -29 L0 -34 L2 -29 L5 -33 L6.5 -26 Z"/>' +
        (look.glasses ? glasses() : '');
    }
    if (tier.crown === 'monocle') {
      return '<g class="monocle"><circle cx="3" cy="-20" r="3.4"/><path d="M5.8 -17.8 L7.2 -12"/></g>';
    }
    if (tier.crown === 'headphones') {
      return '<g class="cans"><path d="M-7.5 -21 A7.5 7.5 0 0 1 7.5 -21" fill="none"/>' +
        '<rect x="-9.5" y="-22" width="3.5" height="6" rx="1.4"/><rect x="6" y="-22" width="3.5" height="6" rx="1.4"/></g>' +
        (look.glasses ? glasses() : '');
    }
    return look.glasses ? glasses() : '';
  }

  /* The shared body. `o`: {cls, session, agent, model, tier, look, badge}. */
  function body(o) {
    var tier = o.tier;
    var coat = tier.key === 'muse' || o.look.coat;
    return '<g class="worker tier-' + tier.key + o.cls + '" data-session="' + esc(o.session) + '" data-agent="' +
      esc(o.agent) + '" tabindex="0" role="button" ' +
      'style="--shirt:' + shirt(o.model) + ';--hair:' + HAIR[o.look.hair] + (o.badge ? ';--badge:' + o.badge : '') + '">' +
      (tier.aura ? '<ellipse class="aura" cx="0" cy="-13" rx="16" ry="19"/>' : '') +
      '<circle class="ring" cx="0" cy="-14" r="24"/>' +
      '<g class="bubble"><rect x="6" y="-46" width="30" height="16" rx="8"/>' +
        '<circle cx="15" cy="-38" r="2"/><circle cx="21" cy="-38" r="2"/><circle cx="27" cy="-38" r="2"/>' +
        '<path d="M10 -31 L8 -25 L16 -31 Z"/></g>' +
      GLYPHS +
      '<g class="flip">' +
        '<g class="legs"><rect class="leg l" x="-6" y="-2" width="5" height="11" rx="2"/><rect class="leg r" x="1" y="-2" width="5" height="11" rx="2"/></g>' +
        '<g class="body">' +
          '<path class="torso" d="M-9 -2 Q-9 -14 0 -14 Q9 -14 9 -2 L9 4 Q9 7 6 7 L-6 7 Q-9 7 -9 4 Z"/>' +
          (coat ? '<path class="coat" d="M-9 -2 Q-9 -14 0 -14 L-3 -14 L-4 7 L-6 7 Q-9 7 -9 4 Z M9 -2 Q9 -14 0 -14 L3 -14 L4 7 L6 7 Q9 7 9 4 Z"/>' : '') +
          (o.badge ? '<path class="lanyard" d="M-4 -13 L0 -5 L4 -13" fill="none"/><rect class="badge" x="-2.5" y="-6" width="5" height="4" rx="1"/>' : '') +
          '<rect class="arm left" x="-14" y="-8" width="6" height="10" rx="3"/>' +
          '<rect class="arm right" x="8" y="-8" width="6" height="10" rx="3"/>' +
          '<rect class="cup" x="12" y="-19" width="5" height="6" rx="1"/>' +
          '<circle class="head" cx="0" cy="-20" r="7"/>' +
          '<path class="hair" d="M-7 -21 Q-6 -29 0 -28 Q6 -29 7 -21 Q4 -25 0 -24 Q-4 -25 -7 -21 Z"/>' +
          headgear(tier, o.look) +
        '</g>' +
      '</g>' +
      '<text class="z z1" x="8" y="-28">z</text><text class="z z2" x="8" y="-28">z</text><text class="z z3" x="8" y="-28">z</text>' +
      '</g>';
  }

  function lead(worker, look) {
    return body({ cls: '', session: worker.session_id, agent: worker.session_id, model: worker.model,
                  tier: Cast.tierOf(worker.model), look: look });
  }

  function intern(sub, look, sessionId) {
    return body({ cls: ' intern', session: sessionId, agent: sessionId + '/' + sub.agent_id, model: sub.model,
                  tier: Cast.internTierOf(sub.model), look: look, badge: Cast.badgeOf(sub.type) });
  }

  return { lead: lead, intern: intern, INTERN_SCALE: INTERN_SCALE };
})();
