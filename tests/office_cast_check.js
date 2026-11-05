// Drives dashboard/office-cast.js without a browser: the model tiers and the
// daylight palette are pure data, so node can check them. Exits non-zero on a
// broken expectation. Run by tests/test_office_sim.py.
'use strict';
global.window = {};
require('../dashboard/office-cast.js');
var cast = window.OfficeCast;

function expect(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exitCode = 1; } }

var lead = cast.tierOf('Opus 5 (1M context)');
var muse = cast.tierOf('Fable 5.1');
var core = cast.tierOf('Sonnet 5');
var swift = cast.tierOf('Haiku 4.5');

expect(lead.key === 'lead', 'Opus is the lead tier');
expect(muse.key === 'muse', 'Fable is the muse tier');
expect(core.key === 'core', 'Sonnet is a normal worker');
expect(swift.key === 'swift', 'Haiku is the swift tier');
expect(cast.tierOf('claude-opus-5').key === 'lead', 'model ids work as well as display names');
expect(cast.tierOf('').key === 'core' && !cast.tierOf('').ornament, 'an unknown model is a plain worker');
expect(cast.tierOf(undefined).key === 'core', 'a missing model never throws');

expect(lead.ornament && muse.ornament && !core.ornament && !swift.ornament, 'only the top tiers get a desk ornament');
expect(lead.chair === 'gold' && muse.chair === 'gold' && core.chair === 'plain', 'the top tiers sit on gold');
expect(lead.aura && muse.aura && !core.aura, 'only the top tiers glow');
expect(lead.crown && muse.crown && !core.crown, 'only the top tiers wear something');
expect(swift.speed > core.speed && swift.scale < core.scale, 'Haiku is smaller and quicker');
expect(cast.tierOf('Opus 5').key === cast.tierOf('OPUS 5').key, 'matching ignores case');

var keys = {};
for (var h = 0; h < 24; h++) {
  var sky = cast.skyOf(h);
  keys[sky.key] = (keys[sky.key] || 0) + 1;
  expect(typeof sky.top === 'string' && typeof sky.bottom === 'string' && typeof sky.light === 'number',
    'hour ' + h + ' has a full palette');
}
expect(cast.skyOf(3).key === 'night' && cast.skyOf(3).stars, 'the small hours are dark and starry');
expect(cast.skyOf(6).key === 'dawn', 'early morning is dawn');
expect(cast.skyOf(12).key === 'day' && !cast.skyOf(12).lamps, 'midday needs no desk lamps');
expect(cast.skyOf(19).key === 'dusk' && cast.skyOf(19).lamps, 'the evening lights the lamps');
expect(Object.keys(keys).length === 4, 'the day passes through all four palettes');

var fam = function (tool) { return cast.familyOf(tool === null ? null : { tool: tool, target: '' }); };
expect(fam('Read') === 'read' && fam('Grep') === 'read' && fam('Glob') === 'read', 'reading tools are the read family');
expect(fam('Edit') === 'edit' && fam('Write') === 'edit', 'writing tools are the edit family');
expect(fam('Bash') === 'shell' && fam('WebSearch') === 'web' && fam('Agent') === 'delegate' && fam('Task') === 'delegate',
  'shell, web and delegate families');
expect(fam('mcp__slack__post') === 'mcp', 'any MCP tool is the mcp family');
expect(fam('replying') === 'think' && fam('Whatever') === 'think', 'replies and unknown tools are thinking');
expect(fam('constructor') === 'think' && fam('toString') === 'think', 'inherited object keys are not tool families');
expect(fam(null) === null && cast.familyOf(undefined) === null, 'no doing, no family');
expect(cast.badgeOf('Explore') === cast.badgeOf('Explore') && /^hsl\(/.test(cast.badgeOf('Plan')), 'a badge is a stable colour');
expect(cast.badgeOf('Explore') !== cast.badgeOf('general-purpose') || cast.badgeOf('Plan') !== cast.badgeOf('Explore'),
  'different agent types get different badges');
expect(cast.hash('abc') === cast.hash('abc') && cast.hash('abc') !== cast.hash('abd'), 'hash is stable');

console.log(JSON.stringify({ tiers: [lead.key, muse.key, core.key, swift.key], hours: keys,
                             families: ['Read', 'Edit', 'Bash', 'WebFetch', 'Agent', 'mcp__a', 'replying'].map(fam) }));
