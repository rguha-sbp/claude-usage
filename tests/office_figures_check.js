// Drives dashboard/office-figures.js without a browser: figures are pure
// strings, so node can check them. Run by tests/test_office_sim.py.
'use strict';
global.window = {};
require('../dashboard/format.js');
require('../dashboard/office-cast.js');
require('../dashboard/office-figures.js');
var F = window.OfficeFigures;

function expect(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exitCode = 1; } }
function clean(s) { return s.indexOf('NaN') === -1 && s.indexOf('undefined') === -1 && s.indexOf('null') === -1; }

var look = { hair: 2, glasses: true, coat: false };
var lead = F.lead({ session_id: 's1', model: 'Opus 5' }, look);
var plain = F.lead({ session_id: 's2', model: '' }, { hair: 0, glasses: false, coat: true });
var sub = { agent_id: 'a1', type: 'Explore', model: 'claude-haiku-4-5', background: true };
var intern = F.intern(sub, look, 's1');

expect(clean(lead) && clean(plain) && clean(intern), 'no NaN, undefined or null in any figure');
expect(lead.indexOf('data-agent="s1"') !== -1 && lead.indexOf('data-session="s1"') !== -1, 'a lead is addressable by session and agent');
expect(lead.indexOf('class="crown"') !== -1, 'an Opus lead still wears the crown');
expect(intern.indexOf('data-agent="s1/a1"') !== -1 && intern.indexOf('data-session="s1"') !== -1, 'an intern points at its session');
expect(intern.indexOf(' intern') !== -1 && intern.indexOf('class="badge"') !== -1, 'an intern wears a lanyard badge');
expect(intern.indexOf(window.OfficeCast.badgeOf('Explore')) !== -1, 'the badge is coloured by agent type');
expect(intern.indexOf('class="cans"') !== -1, 'a Haiku intern wears headphones');
['g-read', 'g-edit', 'g-shell', 'g-web', 'g-delegate', 'g-mcp', 'g-stalled'].forEach(function (g) {
  expect(lead.indexOf(g) !== -1 && intern.indexOf(g) !== -1, 'both figures carry glyph ' + g);
});
expect(F.INTERN_SCALE > 0.5 && F.INTERN_SCALE < 0.9, 'interns are smaller');
var evil = F.intern({ agent_id: 'a"1', type: 'E<b>', model: 'x', background: false }, look, 's"1');
expect(evil.indexOf('data-agent="s&quot;1/a&quot;1"') !== -1 && evil.indexOf('a"1') === -1, 'an agent id with a quote is escaped in data-agent');
console.log(JSON.stringify({ lead: lead.length, intern: intern.length }));
