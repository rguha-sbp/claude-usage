/* The Agents rail (spec Sec 13.5): every live session and its sub-agents as
   a tree of rows, the readable twin of the scene. Pure: `markup(data, now)`
   returns a string and office.js puts it in the page, links hover between
   rows and figures (shared `data-agent`) and ticks the running clocks. The
   payload is `data`, a room `room`, a worker `worker`, a sub-agent `sub`. */
window.OfficeRail = (function () {
  'use strict';

  var Cast = window.OfficeCast;
  var STATE_GLYPH = {
    running: '<svg viewBox="0 0 12 12" class="sg"><circle cx="6" cy="6" r="4.5"/><path d="M6 1.5 A4.5 4.5 0 0 1 6 10.5 Z"/></svg>',
    done: '<svg viewBox="0 0 12 12" class="sg"><path d="M2.5 6.5 L5 9 L9.5 3.5"/></svg>',
    asleep: '<svg viewBox="0 0 12 12" class="sg"><path d="M3 3.5 H8 L3 8.5 H8"/></svg>'
  };
  var BACKGROUND = '<svg viewBox="0 0 12 12" class="bg" aria-label="background"><circle cx="2.5" cy="6" r="1.2"/>' +
    '<circle cx="6" cy="6" r="1.2"/><circle cx="9.5" cy="6" r="1.2"/></svg>';

  var esc = window.UsageFormat.esc, fmtTokens = window.UsageFormat.fmtTokens;
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function modelName(m) { return String(m || '').replace(/^claude-/, ''); }
  function doingText(doing) {
    if (!doing) return '';
    return doing.target ? doing.tool + ': ' + doing.target : doing.tool;
  }

  function duration(fromIso, toIso, nowMs) {
    var end = toIso ? Date.parse(toIso) : nowMs;
    var s = Math.max(0, Math.round((end - Date.parse(fromIso)) / 1000));
    if (!isFinite(s)) return '';
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm' + pad(s % 60);
    return Math.floor(s / 3600) + 'h' + pad(Math.floor(s % 3600 / 60));
  }

  /* Depth-first by parent; a sub whose parent is not listed (finished long
     ago, or beyond the cap) stands as a root. */
  function tree(subs) {
    var listed = {}, kids = {};
    subs.forEach(function (sub) { listed[sub.agent_id] = true; });
    subs.forEach(function (sub) {
      var key = sub.parent && listed[sub.parent] ? sub.parent : '';
      (kids[key] = kids[key] || []).push(sub);
    });
    var out = [];
    (function walk(key, depth) {
      (kids[key] || []).forEach(function (sub) { out.push({ sub: sub, depth: depth }); walk(sub.agent_id, depth + 1); });
    })('', 1);
    return out;
  }

  function elapsed(from, to, nowMs) {
    return '<span class="elapsed" data-from="' + esc(from) + '" data-to="' + esc(to || '') + '">' +
      esc(duration(from, to, nowMs)) + '</span>';
  }

  /* "model · 1m05 · 38.2K tok · 1 tool[ · done]", shared by the rail rows and
     the hover card. */
  function metaLine(sub, nowMs) {
    var live = sub.state === 'running';
    return esc(modelName(sub.model)) + ' · ' + elapsed(sub.started, live ? '' : sub.last, nowMs) +
      ' · ' + fmtTokens(sub.tokens) + ' tok · ' + sub.tools + (sub.tools === 1 ? ' tool' : ' tools') +
      (live ? '' : ' · ' + esc(sub.state));
  }

  function row(worker, node, nowMs) {
    var sub = node.sub;
    var live = sub.state === 'running';
    var mix = Object.keys(sub.tool_mix || {}).map(function (t) { return t + ' ' + sub.tool_mix[t]; }).join(' · ');
    var fam = live ? Cast.familyOf(sub.doing) : null;
    return '<button type="button" class="rail-row state-' + esc(sub.state) + (fam ? ' tool-' + fam : '') +
      '" data-agent="' + esc(worker.session_id + '/' + sub.agent_id) + '" data-session="' + esc(worker.session_id) +
      '" style="--depth:' + node.depth + '">' +
      '<span class="line1">' + STATE_GLYPH[sub.state] + '<b>' + esc(sub.type) + '</b>' +
        '<span class="desc">' + esc(sub.description) + '</span>' + (sub.background ? BACKGROUND : '') + '</span>' +
      '<span class="meta">' + metaLine(sub, nowMs) + '</span>' +
      (live && sub.doing ? '<span class="doing">' + esc(doingText(sub.doing)) + '</span>' : '') +
      (mix ? '<span class="mix">' + esc(mix) + '</span>' : '') +
      '</button>';
  }

  function session(worker, nowMs) {
    var subs = worker.agents || [];
    var running = subs.filter(function (sub) { return sub.state === 'running'; }).length;
    var done = subs.filter(function (sub) { return sub.state === 'done'; }).length;
    var tokens = subs.reduce(function (n, sub) { return n + (sub.tokens || 0); }, 0);
    var team = subs.length
      ? '<span class="team">' + running + ' running · ' + done + ' done · ' + fmtTokens(tokens) + ' tok across sub-agents</span>'
      : '';
    return '<section class="rail-session">' +
      '<button type="button" class="rail-row rail-lead state-' + esc(worker.state) + '" data-agent="' +
        esc(worker.session_id) + '" data-session="' + esc(worker.session_id) + '" style="--depth:0">' +
        '<span class="line1"><b>' + esc(worker.title) + '</b></span>' +
        '<span class="meta">' + esc(modelName(worker.model)) + (worker.model ? ' · ' : '') +
          (worker.state === 'working' ? 'working ' : 'quiet ') + elapsed(worker.since, '', nowMs) + '</span>' +
        (worker.doing ? '<span class="doing">' + esc(doingText(worker.doing)) + '</span>' : '') + team +
      '</button>' +
      tree(subs).map(function (node) { return row(worker, node, nowMs); }).join('') +
      (worker.agents_hidden > 0 ? '<span class="rail-more">+' + worker.agents_hidden + ' earlier</span>' : '') +
      '</section>';
  }

  function workersOf(data) {
    var out = [];
    (data.rooms || []).forEach(function (room) { room.workers.forEach(function (worker) { out.push(worker); }); });
    return out;
  }

  function signature(data) {
    return workersOf(data).map(function (worker) {
      return worker.session_id + ':' + worker.state + ':' + doingText(worker.doing) + ':' + worker.agents_hidden + '[' +
        (worker.agents || []).map(function (sub) {
          return sub.agent_id + ':' + sub.state + ':' + doingText(sub.doing) + ':' + sub.tools;
        }).join(',') + ']';
    }).join('|');
  }

  function markup(data, nowMs) {
    var workers = workersOf(data);
    if (!workers.length) return '<p class="rail-empty">No live sessions.</p>';
    return workers.map(function (worker) { return session(worker, nowMs); }).join('');
  }

  return { tree: tree, duration: duration, doingText: doingText, metaLine: metaLine, signature: signature, markup: markup };
})();
