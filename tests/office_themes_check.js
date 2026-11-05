// Checks dashboard/office-themes.js without a browser: every theme defines
// every palette role as a colour, asks only for decor that office-floor.js
// can place, and carries a complete light. Prints a JSON report for
// tests/test_office_sim.py, which also checks the names the scenery draws.
'use strict';
global.window = {};
require('../dashboard/office-cast.js');
require('../dashboard/office-themes.js');
require('../dashboard/office-floor.js');
var T = window.OfficeThemes, F = window.OfficeFloor;

var failures = [];
function expect(cond, msg) { if (!cond) failures.push(msg); }

T.THEMES.forEach(function (theme) {
  var tag = theme.key;
  expect(theme.label && T.themeOf(theme.key) === theme, tag + ': has a label and is found by key');
  expect(JSON.stringify(Object.keys(theme.palette).sort()) === JSON.stringify(T.PALETTE_KEYS.slice().sort()),
         tag + ': palette defines exactly the reference roles');
  Object.keys(theme.palette).forEach(function (role) {
    expect(/^#[0-9a-f]{6}$/.test(theme.palette[role]), tag + ': ' + role + ' is #rrggbb');
  });
  Object.keys(theme.decor.weights).forEach(function (kind) {
    expect(F.FILL_KINDS.indexOf(kind) !== -1 && theme.decor.weights[kind] > 0, tag + ': ' + kind + ' is a weighted fill kind');
  });
  expect(F.KINDS[theme.decor.centrepiece] !== undefined, tag + ': centrepiece is a floor kind');
  expect(theme.decor.density > 0 && theme.decor.density <= 1, tag + ': density in (0, 1]');
  ['hemi', 'sunI', 'warm'].forEach(function (k) { expect(isFinite(theme.light[k]), tag + ': light.' + k); });
  ['sky', 'ground', 'sun'].forEach(function (k) { expect(/^#[0-9a-f]{6}$/.test(theme.light[k]), tag + ': light.' + k); });
});
expect(T.themeOf('nonsense') === T.THEMES[0], 'an unknown key falls back to the first theme');

if (failures.length) { console.error(failures.join('\n')); process.exitCode = 1; }
console.log(JSON.stringify({
  themes: T.THEMES.map(function (t) { return t.key; }),
  patterns: T.THEMES.map(function (t) { return t.floor.pattern; }),
  partitions: T.THEMES.map(function (t) { return t.partition; }),
  kinds: F.FILL_KINDS.concat(T.THEMES.map(function (t) { return t.decor.centrepiece; }))
}));
