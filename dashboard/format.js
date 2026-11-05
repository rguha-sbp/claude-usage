/* Formatters shared by the dashboard and the Office files; DOM-free. */
window.UsageFormat = (function () {
  'use strict';

  var UNITS = [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']];

  /* Mirrors usage/tips.py fmt_tokens: one decimal, unit chosen after
     rounding, plain integer below 1000. */
  function fmtTokens(n) {
    n = Number(n) || 0;
    var a = Math.abs(n);
    if (a < 1000) return String(Math.trunc(n));
    for (var i = 0; i < UNITS.length; i++) {
      if (a >= UNITS[i][0]) {
        var m = Math.round((n / UNITS[i][0]) * 10) / 10;
        if (Math.abs(m) >= 1000 && i > 0) {
          m = Math.round((n / UNITS[i - 1][0]) * 10) / 10;
          return m.toFixed(1) + UNITS[i - 1][1];
        }
        return m.toFixed(1) + UNITS[i][1];
      }
    }
    return String(Math.trunc(n));
  }

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  return { esc: esc, fmtTokens: fmtTokens };
})();
