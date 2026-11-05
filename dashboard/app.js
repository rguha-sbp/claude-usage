/* Claude Code usage dashboard.
   Renders the /api/summary payload into the approved Trends Report layout.
   Reads only keys that usage/summary.py:build_summary produces; the payload
   root is always bound to `data`, its summary block to `summary`, a session
   to `sess`, a chart point to `pt` and a project to `pr` (tests/
   test_dashboard_static.py checks those accesses against a real payload). */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var PANES = ['month', 'sessions', 'projects', 'office'];
  var BASELINE_KEY = 'usage-baseline';
  var THEME_KEY = 'usage-theme';

  var state = {
    data: null,          // last good payload
    period: null,        // requested period, a month or a day (null = server default)
    pane: 'month',
    selected: null,      // session_id shown in the detail panel
    baseline: {},        // session_id -> value when the user last looked
    chosen: false,       // true once the user picked a session themselves
    staleSince: null,
    booted: false
  };

  // ---------------------------------------------------------------- format
  var esc = window.UsageFormat.esc, fmtTokens = window.UsageFormat.fmtTokens;

  /* Mirrors usage/tips.py fmt_usd: $1,234 at or above $100, $12.40 below. */
  function fmtUsd(v) {
    var neg = Number(v) < 0;
    var c = Math.round(Math.abs(Number(v) || 0) * 100) / 100;
    var body = c >= 100 ? Math.round(c).toLocaleString('en-US') : c.toFixed(2);
    return (neg ? '-$' : '$') + body;
  }

  /* Truth over estimate: a missing dollar figure says so in words. */
  function money(v) {
    if (v === null || v === undefined) return '<span class="unavailable">unavailable</span>';
    return fmtUsd(v);
  }

  function magnitude(v, unit) { return unit === 'usd' ? money(v) : fmtTokens(v); }

  function pct(x) {
    if (x === null || x === undefined) return '\u2014';
    return Math.round(x * 100) + '%';
  }

  function ago(ts) {
    if (!ts) return 'just now';
    var s = (Date.now() - new Date(ts).getTime()) / 1000;
    if (s < 90) return 'just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' d ago';
  }

  function clockTime(ts) {
    var d = ts ? new Date(ts) : new Date();
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  }

  function monthLabel(m) {
    if (!m) return '';
    var d = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)) - 1, 1);
    return d.toLocaleString('en', { month: 'long' }) + ' ' + m.slice(0, 4);
  }

  function monthName(m) {
    if (!m) return 'last month';
    var d = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)) - 1, 1);
    return d.toLocaleString('en', { month: 'long' });
  }

  function dateOf(day) {
    return new Date(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)));
  }

  /* "September 2026" for a month, "Sunday 14 September 2026" for a day. */
  function periodLabel(data) {
    if (data.kind !== 'day') return monthLabel(data.month);
    return dateOf(data.period).toLocaleDateString('en-GB',
      { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  }

  /* Short day for the picker: "Sun 14 Sept". */
  function dayLabel(day) {
    return dateOf(day).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  }

  /* The previous period as the cards name it: "August", "yesterday" or
     "Saturday 13 September". */
  function prevLabel(data) {
    var prev = data.summary.previous_period;
    if (data.kind !== 'day') return monthName(prev);
    var d = dateOf(prev);
    var yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return 'yesterday';
    return d.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });
  }

  function capital(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  /* "the month" / "the day": the noun the headings use. */
  function periodNoun(data) { return data.kind === 'day' ? 'the day' : 'the month'; }

  function kOf(threshold) { return Math.round(Number(threshold || 0) / 1000) + 'K'; }

  /* Long working directories keep their tail, which is the part that
     identifies the worktree. The full path stays in the cell's title. */
  function shortPath(p, max) {
    p = String(p || '');
    if (!p) return '—';
    if (p.length <= max) return p;
    var parts = p.split('/');
    var out = parts.pop();
    while (parts.length && (out.length + parts[parts.length - 1].length + 3) <= max) {
      out = parts.pop() + '/' + out;
    }
    return '…/' + out;
  }

  function unitWord(data) { return data.cost_available ? 'spend' : 'tokens'; }

  // ------------------------------------------------- icons (authored SVG)
  // One stroke system: 20x20 box, stroke-width 2, round caps and joins.
  function icon(paths, size) {
    return '<svg width="' + (size || 20) + '" height="' + (size || 20) + '" viewBox="0 0 20 20" ' +
      'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true" focusable="false">' + paths + '</svg>';
  }
  var ICON = {
    up: icon('<path d="M4 13.5 L10 7.5 L16 13.5"/>'),
    down: icon('<path d="M4 6.5 L10 12.5 L16 6.5"/>'),
    flat: icon('<path d="M3.5 10 H15"/><path d="M11.5 6.5 L15 10 L11.5 13.5"/>'),
    none: icon('<path d="M5 10 H15"/>'),
    tip: icon('<path d="M10 2.6a5 5 0 0 0-3 9V13h6v-1.4a5 5 0 0 0-3-9Z"/><path d="M8 15.6h4"/>'),
    alert: icon('<path d="M10 3 L18 16.5 H2 Z"/><path d="M10 8.4v3.4"/><path d="M10 14.4h.01"/>', 16),
    over: icon('<path d="M10 4.5 L16.5 15.5 H3.5 Z"/>', 12),
    chevron: icon('<path d="M8 5l5 5-5 5"/>', 12),
    ok: icon('<circle cx="10" cy="10" r="6.5"/>', 12)
  };

  /* direction -> arrow glyph. 'none' gets its own flat dash so the muted
     "No comparison yet" state is legible without colour. */
  function arrowIcon(direction) { return ICON[direction] || ICON.flat; }

  /* Colour is a second carrier only: the glyph shape and the verdict
     sentence already say which way things are going. */
  function arrowTone(direction, upIsBad) {
    if (direction === 'none' || direction === 'flat') return 'flat';
    var bad = direction === 'up' ? upIsBad : !upIsBad;
    return bad ? 'bad' : 'good';
  }

  // ------------------------------------------------------------ trend strip
  function metricCard(opts) {
    var cue = opts.cue
      ? ' <button type="button" class="cue" ' + opts.cue.attr + '>' + esc(opts.cue.label) +
        ICON.chevron + '</button>'
      : '';
    return '<article class="glass metric' + (opts.direction === 'none' ? ' none' : '') + '">' +
      '<div class="arrow ' + arrowTone(opts.direction, opts.upIsBad) + '" role="img" aria-label="' +
      esc(opts.arrowLabel) + '">' + arrowIcon(opts.direction) + '</div>' +
      '<div class="body">' +
      '<h2>' + esc(opts.verdict) + '</h2>' +
      '<div class="mag num">' + opts.magnitude + '</div>' +
      '<div class="sub">' + opts.sub + cue + '</div>' +
      '</div></article>';
  }

  var ARROW_LABEL = {
    up: 'Trending up', down: 'Trending down', flat: 'Level', none: 'No comparison yet'
  };

  function renderTrendStrip(data) {
    var summary = data.summary;
    var unit = data.unit;
    var k = kOf(data.threshold);
    var prev = prevLabel(data);
    var isDay = data.kind === 'day';
    var cards = '';

    // 1. Spend to date, with projection (a month) or as a closed total (a day).
    var spendSub = (summary.previous_total === null || summary.previous_total === undefined)
      ? capital(prev) + ' has no comparable data.'
      : capital(prev) + ' closed at ' + magnitude(summary.previous_total, unit) + '.';
    if (!isDay) spendSub += ' Projection uses the last 7 days\u2019 pace.';
    var spendQualifier = isDay
      ? (data.period_phrase === 'today' ? 'so far today' : 'total for the day')
      : 'so far &middot; projected ' + magnitude(summary.projected, unit);
    cards += metricCard({
      direction: summary.spend_trend.direction,
      upIsBad: true,
      arrowLabel: ARROW_LABEL[summary.spend_trend.direction] || 'Level',
      verdict: summary.spend_trend.verdict,
      magnitude: magnitude(summary.to_date, unit) + '<span>' + spendQualifier + '</span>',
      sub: esc(spendSub),
      cue: {
        label: data.cost_available ? 'How to spend less' : 'How to use fewer tokens',
        attr: 'data-tip="month-compact"'
      }
    });

    // 2. Share of the month spent above the context threshold.
    // A "was 46%" qualifier under a "No comparison yet" heading contradicts it:
    // the previous month exists but is too short a run to compare against.
    var wasShare = (summary.previous_over_share === null || summary.previous_over_share === undefined
      || summary.over_trend.direction === 'none')
      ? '' : ' &middot; was ' + pct(summary.previous_over_share);
    cards += metricCard({
      direction: summary.over_trend.direction,
      upIsBad: true,
      arrowLabel: ARROW_LABEL[summary.over_trend.direction] || 'Level',
      verdict: summary.over_trend.verdict,
      magnitude: pct(summary.over_share) +
        '<span>of ' + unitWord(data) + ' &middot; ' + magnitude(summary.over_value, unit) + wasShare + '</span>',
      sub: 'Threshold ' + esc(k) + ' tokens. Tool calls made with a context larger than this re-read all of it every time.',
      cue: { label: 'How to fix this', attr: 'data-tip="month-compact"' }
    });

    // 3. The session that led the month.
    var top = summary.top_session;
    if (top) {
      var share = (top.share === null || top.share === undefined) ? null : top.share;
      cards += metricCard({
        direction: 'flat',
        upIsBad: true,
        arrowLabel: 'Largest single session',
        verdict: share ? 'One session is ' + pct(share) + ' of ' + periodNoun(data)
          : 'The largest session of ' + periodNoun(data),
        magnitude: magnitude(top.value, unit) + '<span>' + esc(top.title) + '</span>',
        sub: 'Named by what it was about, not by its id.',
        cue: { label: 'See why', attr: 'data-session="' + esc(top.session_id) + '"' }
      });
    } else {
      cards += metricCard({
        direction: 'none',
        upIsBad: true,
        arrowLabel: 'No sessions yet',
        verdict: 'No sessions yet ' + data.period_phrase,
        magnitude: '\u2014',
        sub: 'Nothing has been recorded for ' + esc(periodLabel(data)) + '.'
      });
    }
    $('trend-strip').innerHTML = cards;
  }

  // ----------------------------------------------------------------- chart
  var LEFT = 66, TOP = 26, BASE = 178, RIGHT_GUTTER = 60, MIN_WIDTH = 640;

  function axisValue(v, unit) {
    if (unit !== 'usd') return fmtTokens(v);
    if (v >= 10) return '$' + Math.round(v).toLocaleString('en-US');
    if (v <= 0) return '$0';
    return '$' + v.toFixed(1);
  }

  function renderChart(data) {
    var unit = data.unit;
    var k = kOf(data.threshold);
    var pts = data.series;
    var isDay = data.kind === 'day';
    $('chart-title').textContent = (isDay ? 'Hourly ' : 'Daily ') + (data.cost_available ? 'spend' : 'tokens');

    if (!pts.length) {
      $('chart').innerHTML = '';
      $('chart-legend').innerHTML = '';
      $('chart-hint').textContent = '';
      return;
    }

    // The drawing is as wide as the panel gives it, so text keeps its size
    // on a large monitor instead of the whole picture scaling up.
    var svg = $('chart');
    var width = Math.max(MIN_WIDTH, Math.round(svg.getBoundingClientRect().width) || MIN_WIDTH);
    var RIGHT = width - RIGHT_GUTTER;
    svg.setAttribute('viewBox', '0 0 ' + width + ' 220');

    var peak = 0;
    pts.forEach(function (pt) { if (pt.value > peak) peak = pt.value; });
    var max = peak || 1;
    var step = (RIGHT - LEFT) / pts.length;
    var bw = Math.max(3, step - 5);
    var y = function (v) { return BASE - (v / max) * (BASE - TOP); };
    var yShare = function (r) { return BASE - Math.min(1, Math.max(0, r)) * (BASE - TOP); };

    // value axis (left) + share axis (right, the threshold series)
    var axes = '<g class="axis" font-size="11" fill="var(--ink-3)">';
    (peak ? [0, 0.5, 1] : [0]).forEach(function (f) {
      var yy = y(max * f);
      axes += '<line x1="' + LEFT + '" y1="' + yy + '" x2="' + RIGHT + '" y2="' + yy + '" stroke="var(--hairline)"/>' +
        '<text x="' + (LEFT - 8) + '" y="' + (yy + 4) + '" text-anchor="end">' +
        axisValue(max * f, unit) + '</text>';
    });
    axes += '</g><g class="axis" font-size="11" fill="var(--threshold-text)">';
    [0, 0.5, 1].forEach(function (f) {
      axes += '<text x="' + (RIGHT + 8) + '" y="' + (yShare(f) + 4) + '" text-anchor="start">' +
        Math.round(f * 100) + '%</text>';
    });
    axes += '</g>';

    var bars = '', over = '', proj = '', labels = '', share = [];
    var todayX = null, hasActual = false, hasProjected = false;
    pts.forEach(function (pt, i) {
      var x = LEFT + i * step + 2;
      var top = y(pt.value);
      var h = Math.max(0, BASE - top);
      var when = isDay ? pt.label + ':00' : pt.label;
      var reading = when + ': ' + (unit === 'usd' ? fmtUsd(pt.value) : fmtTokens(pt.value) + ' tokens');
      // A month's bar is a button that opens its day.
      var link = isDay ? '' : ' data-day="' + esc(pt.label) + '" role="button" tabindex="0" aria-label="' +
        esc(reading + '. Open this day') + '"';
      if (pt.projected) {
        hasProjected = true;
        proj += '<rect x="' + x + '" y="' + top + '" width="' + bw + '" height="' + h + '" rx="3">' +
          '<title>' + esc(reading) + ' (projected)</title></rect>';
      } else {
        hasActual = true;
        // Bars are drawn from the baseline so an empty day is still a target.
        bars += '<rect x="' + x + '" y="' + top + '" width="' + bw + '" height="' + h + '" rx="3"' + link + '>' +
          '<title>' + esc(reading) + '</title></rect>';
        if (pt.over_value > 0) {
          over += '<rect x="' + x + '" y="' + top + '" width="' + bw + '" height="' +
            Math.max(2, BASE - y(pt.over_value)) + '" rx="3" pointer-events="none"><title>' +
            esc(magnitude(pt.over_value, unit) + ' above ' + k + (isDay ? ' at ' : ' on ') + when) + '</title></rect>';
        }
        if (pt.tokens > 0) share.push((x + bw / 2).toFixed(1) + ',' + yShare(pt.over_tokens / pt.tokens).toFixed(1));
        todayX = x + bw + 2;
      }
      var n = Number(isDay ? pt.label : pt.label.slice(8));
      var tick = isDay ? (n % 3 === 0) : (n === 1 || n % 5 === 0);
      if (tick) {
        labels += '<text x="' + (x + bw / 2) + '" y="' + (BASE + 18) + '" text-anchor="middle">' +
          (isDay ? pt.label + ':00' : n) + '</text>';
      }
    });

    var marker = '';
    // A fully past month has no boundary to mark and nothing projected.
    if (hasActual && hasProjected && todayX !== null && todayX < RIGHT) {
      marker = '<line x1="' + todayX + '" y1="' + TOP + '" x2="' + todayX + '" y2="' + BASE +
        '" stroke="var(--ink-3)" stroke-width="1" stroke-dasharray="2 3"/>' +
        '<text x="' + (todayX - 4) + '" y="' + (TOP - 8) + '" fill="var(--ink-2)" font-weight="600" ' +
        'text-anchor="end" font-size="11">today</text>';
    }

    $('chart').innerHTML = axes +
      '<g fill="var(--bar)">' + bars + '</g>' +
      '<g fill="var(--bar-over)">' + over + '</g>' +
      '<g fill="var(--bar-proj)">' + proj + '</g>' +
      (share.length > 1
        ? '<polyline points="' + share.join(' ') + '" fill="none" stroke="var(--threshold-mark)" ' +
          'stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round" stroke-linecap="round"/>'
        : '') +
      '<g class="axis" font-size="11" fill="var(--ink-3)">' + labels + marker + '</g>';

    // The legend below names the marks; this line says something the marks cannot.
    $('chart-hint').textContent = isDay
      ? (unit === 'usd' ? fmtUsd(data.summary.to_date) : fmtTokens(data.summary.to_date) + ' tokens') +
        (data.period_phrase === 'today' ? ' so far today. Select a month in the picker to go back.' : ' for the day.')
      : 'Last 7 days averaged ' +
        (unit === 'usd' ? fmtUsd(data.summary.pace) : fmtTokens(data.summary.pace) + ' tokens') +
        ' a day. Select a bar to open that day.';
    $('chart-legend').innerHTML =
      '<span><i style="background:var(--bar)"></i>' + (data.cost_available ? 'Spend' : 'Tokens') + '</span>' +
      '<span><i style="background:var(--bar-over)"></i>Of which above ' + esc(k) + '</span>' +
      (hasProjected ? '<span><i style="background:var(--bar-proj)"></i>Projected</span>' : '') +
      '<span><i class="dash"></i>Share above ' + esc(k) + '</span>';
  }

  // ---------------------------------------------------------------- splits
  function splitBlock(title, entries, unit, colors) {
    var total = 0;
    entries.forEach(function (e) { total += e[1]; });
    if (!total) {
      return '<div class="kv head"><span class="k">' + esc(title) + '</span><span class="v muted">no data</span></div>';
    }
    var bar = '<div class="split" aria-hidden="true">';
    var rows = '';
    entries.forEach(function (e, i) {
      if (e[1] <= 0) return;
      var c = colors[i % colors.length];
      var share = 100 * e[1] / total;
      bar += '<b style="width:' + share.toFixed(2) + '%;background:' + c + '"></b>';
      rows += '<div class="kv"><span class="k"><i style="background:' + c + '"></i>' +
        '<span class="name">' + esc(e[0]) + '</span></span><span class="v num">' +
        (unit === 'usd' ? money(e[1]) : fmtTokens(e[1])) + ' &middot; ' + Math.round(share) + '%</span></div>';
    });
    return '<div class="kv head"><span class="k">' + esc(title) + '</span><span class="v num">' +
      (unit === 'usd' ? money(total) : fmtTokens(total)) + '</span></div>' + bar + '</div>' + rows;
  }

  function renderSplits(data) {
    var models = Object.keys(data.model_split).map(function (name) {
      return [name, data.model_split[name]];
    }).sort(function (a, b) { return b[1] - a[1]; });
    $('split-model').innerHTML = splitBlock(
      data.cost_available ? 'By model' : 'By model (tokens)', models, data.unit,
      ['var(--cat-1)', 'var(--cat-2)', 'var(--cat-3)', 'var(--cat-4)']);

    var t = data.token_split;
    $('split-tokens').innerHTML = splitBlock('By token type', [
      ['Cache read', t.cache_read], ['Cache write', t.cache_write],
      ['Input', t.input], ['Output', t.output]
    ], 'tokens', ['var(--cat-1)', 'var(--cat-2)', 'var(--cat-3)', 'var(--cat-4)']);

    // The threshold is drawn under the chart too, as the split the whole
    // dashboard is arguing about.
    var k = kOf(data.threshold);
    var summary = data.summary;
    var under = Math.max(0, summary.to_date - summary.over_value);
    $('split-threshold').innerHTML = splitBlock('Either side of ' + k + ' of context', [
      ['Under ' + k, under], ['Above ' + k, summary.over_value]
    ], data.unit, ['var(--cat-1)', 'var(--threshold-mark)']);
  }

  // ------------------------------------------------------------ table rows
  function peakCell(sess, threshold) {
    var over = sess.peak_over;
    return '<span class="peak ' + (over ? 'over' : 'ok') + '" title="' +
      (over ? 'Above the ' + esc(kOf(threshold)) + ' threshold' : 'Under the ' + esc(kOf(threshold)) + ' threshold') +
      '">' + (over ? ICON.over : ICON.ok) + '<span class="num">' + fmtTokens(sess.peak_context) + '</span></span>';
  }

  function valueOf(sess, data) {
    return data.cost_available ? sess.cost : sess.total_tokens;
  }

  function valueCell(sess, data) {
    if (data.cost_available) return money(sess.cost);
    return fmtTokens(sess.total_tokens);
  }

  function rowMeta(sess) {
    var bits = [sess.turns + ' turns', sess.calls.toLocaleString('en-US') + ' calls'];
    if (sess.compactions) bits.push('compacted ' + sess.compactions + '\u00d7');
    return bits.join(' \u00b7 ');
  }

  /* "+$14 since you looked" — held until the row is clicked or focused. */
  function heldNote(sess, data) {
    var now = valueOf(sess, data);
    var was = state.baseline[sess.session_id];
    if (now === null || now === undefined || was === undefined) return '';
    var delta = now - was;
    var floor = data.cost_available ? 0.005 : 1000;
    if (delta < floor) return '';
    return '<span class="changed">+' +
      (data.cost_available ? fmtUsd(delta) : fmtTokens(delta)) + ' since you looked</span>';
  }

  function markSeen(sessionId) {
    var data = state.data;
    if (!data) return;
    data.sessions.forEach(function (sess) {
      if (sess.session_id !== sessionId) return;
      var v = valueOf(sess, data);
      if (v !== null && v !== undefined) state.baseline[sess.session_id] = v;
    });
    saveBaseline();
    ['movers-body', 'sessions-body'].forEach(function (id) {
      var tr = $(id).querySelector('tr[data-id="' + cssEscape(sessionId) + '"]');
      if (tr) {
        tr.classList.remove('held');
        var note = tr.querySelector('.changed');
        if (note) note.remove();
      }
    });
  }

  function cssEscape(s) { return String(s).replace(/["\\]/g, '\\$&'); }

  function saveBaseline() {
    try { localStorage.setItem(BASELINE_KEY, JSON.stringify(state.baseline)); } catch (e) { /* private mode */ }
  }

  function loadBaseline() {
    try { state.baseline = JSON.parse(localStorage.getItem(BASELINE_KEY) || '{}') || {}; }
    catch (e) { state.baseline = {}; }
  }

  var EMPTY_BODY = 'Claude Code writes a transcript under ~/.claude/projects for every session. ' +
    'Start a session, or pick another period from the selectors above.';

  function emptyRow(cols) {
    var phrase = state.data ? state.data.period_phrase : 'this month';
    return '<tr><td colspan="' + cols + '" class="empty"><b>No sessions ' + phrase + '</b><span>' +
      EMPTY_BODY + '</span></td></tr>';
  }

  /* Rows keep DOM identity across refreshes: matched by session_id, moved
     rather than recreated, and their innerHTML is only rewritten when the
     rendered cells actually changed (so focus and selection survive). */
  function upsertRows(tbody, list, cells, cols, showSelection) {
    if (!list.length) { tbody.innerHTML = emptyRow(cols); return; }
    var existing = {};
    Array.prototype.forEach.call(tbody.children, function (tr) {
      if (tr.dataset.id) existing[tr.dataset.id] = tr;
    });
    list.forEach(function (sess, i) {
      var tr = existing[sess.session_id];
      if (!tr) {
        tr = document.createElement('tr');
        tr.dataset.id = sess.session_id;
        tr.tabIndex = 0;
        tr.title = 'Open this session and its tips';
      } else {
        delete existing[sess.session_id];
      }
      var html = cells(sess);
      if (tr.__sig !== html) { tr.innerHTML = html; tr.__sig = html; }
      tr.classList.toggle('held', html.indexOf('class="changed"') !== -1);
      tr.setAttribute('aria-selected',
        (showSelection && state.selected === sess.session_id) ? 'true' : 'false');
      if (tbody.children[i] !== tr) tbody.insertBefore(tr, tbody.children[i] || null);
    });
    Object.keys(existing).forEach(function (id) { existing[id].remove(); });
    while (tbody.children.length > list.length) tbody.removeChild(tbody.lastChild);
  }

  function renderMovers(data) {
    $('movers-title').textContent = 'Sessions that moved ' + periodNoun(data);
    $('th-movers-value').textContent = data.cost_available ? 'Cost' : 'Tokens';
    var top = data.sessions.slice().sort(function (a, b) {
      return (valueOf(b, data) || 0) - (valueOf(a, data) || 0);
    }).slice(0, 5);
    upsertRows($('movers-body'), top, function (sess) {
      return '<td><span class="title">' + esc(sess.display_title) +
        '<small>' + esc(rowMeta(sess)) + '</small>' + heldNote(sess, data) + '</span></td>' +
        '<td>' + esc(sess.project || 'unknown') + '<br><small title="' + esc(sess.display_cwd) + '">' +
        esc(shortPath(sess.display_cwd, 38)) + '</small></td>' +
        '<td>' + esc(ago(sess.last_ts)) + '</td>' +
        '<td class="r num">' + valueCell(sess, data) + '</td>' +
        '<td class="r">' + peakCell(sess, data.threshold) + '</td>' +
        '<td class="r">' + (sess.tips.length
          ? '<span class="pill">' + sess.tips.length + '</span>'
          : '<span class="muted">none</span>') + '</td>';
    }, 6, state.chosen);
  }

  function renderSessions(data) {
    $('th-sessions-value').textContent = data.cost_available ? 'Cost' : 'Tokens';
    $('sessions-hint').textContent = data.sessions.length
      ? 'Newest first \u00b7 ' + data.sessions.length + ' ' + data.period_phrase
      : 'Nothing recorded for ' + periodLabel(data);
    upsertRows($('sessions-body'), data.sessions, function (sess) {
      return '<td><span class="title">' + esc(sess.display_title) +
        '<small>' + esc((sess.project || 'unknown') + ' \u00b7 ' + ago(sess.last_ts)) + '</small>' +
        heldNote(sess, data) + '</span></td>' +
        '<td><small title="' + esc(sess.display_cwd) + '">' + esc(shortPath(sess.display_cwd, 28)) + '</small></td>' +
        '<td class="r num">' + valueCell(sess, data) + '</td>' +
        '<td class="r">' + peakCell(sess, data.threshold) + '</td>' +
        '<td class="r">' + skillsCell(sess) + '</td>';
    }, 5, true);
    renderDetail(data);
  }

  // ---------------------------------------------------------------- skills
  var KIND_LABEL = { skill: 'skill', hook: 'hook', listing: 'menu' };

  /* Injected text is sized from the transcript; a load whose text was not
     recorded is estimated from context growth and says so. */
  function injectedCell(row) {
    if (row.injected_tokens === null || row.injected_tokens === undefined) {
      return '<span class="muted">not measured</span>';
    }
    return '<span class="num">' + fmtTokens(row.injected_tokens) + '</span>' +
      (row.exact ? '' : ' <small>est.</small>');
  }

  /* Shares under 10% keep one decimal: most skills sit between 0.1% and 1%,
     and a column of "0%" would say nothing. */
  function sharePct(x) {
    if (x === null || x === undefined) return '\u2014';
    var v = x * 100;
    return (v < 10 ? (Math.round(v * 10) / 10).toFixed(1) : String(Math.round(v))) + '%';
  }

  function skillsCell(sess) {
    if (!sess.skills.length) return '<span class="muted">none</span>';
    var names = sess.skills.map(function (row) { return row.name; }).join(', ');
    return '<span class="pill" title="' + esc(names) + '">' + sess.skills.length + '</span>' +
      '<br><small class="num">' + fmtTokens(sess.skills_carried_tokens) + ' carried</small>';
  }

  function renderSkills(data) {
    var rows = data.skills;
    $('skills-hint').textContent = rows.length
      ? 'Heaviest first \u00b7 what each load cost once it was sent again with every later call'
      : 'Nothing was loaded ' + data.period_phrase;
    $('skills-note').textContent = data.skills_note;
    if (!rows.length) {
      $('skills-body').innerHTML = '<tr><td colspan="6" class="empty"><b>No skills loaded ' + data.period_phrase + '</b>' +
        '<span>Skill loads, SessionStart hook context and the skill listing appear here once a session uses them.</span></td></tr>';
      return;
    }
    $('skills-body').innerHTML = rows.map(function (row) {
      return '<tr><td><span class="title">' + esc(row.name) +
        '<small>' + esc(KIND_LABEL[row.kind] || row.kind) + '</small></span></td>' +
        '<td class="r num">' + row.loads + '</td>' +
        '<td class="r num">' + row.sessions + '</td>' +
        '<td class="r">' + injectedCell(row) + '</td>' +
        '<td class="r num">' + fmtTokens(row.carried_tokens) + '</td>' +
        '<td class="r num">' + sharePct(row.share) + '</td></tr>';
    }).join('');
  }

  // ---------------------------------------------------------------- detail
  function tipItem(tip) {
    // The saving is usually already spelled out inside `detail`; only add the
    // chip when the sentence does not carry a figure of its own.
    var saving = (tip.saving_usd && tip.detail.indexOf('$') === -1)
      ? ' <em class="save">about ' + fmtUsd(tip.saving_usd) + ' saved</em>' : '';
    return '<li tabindex="-1" data-tip-id="' + esc(tip.id) + '">' + ICON.tip +
      '<div><b>' + esc(tip.title) + '</b><span>' + esc(tip.detail) + saving + '</span>' +
      '<a class="doc" href="' + esc(tip.doc) + '" target="_blank" rel="noopener noreferrer">Claude Code docs</a>' +
      '</div></li>';
  }

  function renderDetail(data) {
    var previous = state.selected;
    var sess = null;
    data.sessions.forEach(function (s) { if (s.session_id === state.selected) sess = s; });
    if (!sess) sess = data.sessions[0] || null;
    if (!sess) {
      state.selected = null;
      if (previous !== null) syncUrl();
      $('detail').innerHTML = '<div class="empty"><b>No sessions ' + data.period_phrase + '</b><span>' + EMPTY_BODY + '</span></div>';
      return;
    }
    state.selected = sess.session_id;
    // renderDetail may fall back to the newest session; the address bar must
    // not keep pointing at a session that is no longer shown.
    if (previous !== state.selected) syncUrl();

    var k = kOf(data.threshold);
    var models = Object.keys(sess.models).sort(function (a, b) {
      return sess.models[b] - sess.models[a];
    });
    var moreModels = models.length > 3 ? ' and ' + (models.length - 3) + ' more' : '';
    models = models.slice(0, 3);
    var ctx = sess.input_tokens + sess.cache_write_tokens + sess.cache_read_tokens;
    var cacheHit = ctx ? Math.round(100 * sess.cache_read_tokens / ctx) + '%' : 'not measured';
    var callShare = sess.calls ? ' \u00b7 ' + Math.round(100 * sess.calls_over_threshold / sess.calls) + '%' : '';

    function kv(label, value, tone) {
      return '<div class="kv"><span class="k">' + esc(label) + '</span><span class="v num"' +
        (tone ? ' style="color:' + tone + '"' : '') + '>' + value + '</span></div>';
    }

    var meta = [sess.project || 'unknown', sess.display_cwd || '\u2014'];
    if (models.length) meta.push(models.join(', ') + moreModels);
    if (!sess.source_present) meta.push('transcript since cleaned up');

    $('detail').innerHTML =
      '<h2 class="dt">' + esc(sess.display_title) + '</h2>' +
      '<div class="meta">' + esc(meta.join(' \u00b7 ')) + '</div>' +
      kv('Cost', money(sess.cost)) +
      kv('Tokens', fmtTokens(sess.total_tokens)) +
      kv('Calls \u00b7 turns', sess.calls.toLocaleString('en-US') + ' \u00b7 ' + sess.turns) +
      kv('Peak context', fmtTokens(sess.peak_context), sess.peak_over ? 'var(--threshold-text)' : '') +
      kv('Calls above ' + k, sess.calls_over_threshold.toLocaleString('en-US') + ' of ' +
        sess.calls.toLocaleString('en-US') + callShare) +
      kv('Tokens above ' + k, fmtTokens(sess.tokens_over_threshold)) +
      kv('Cache hit ratio', cacheHit) +
      kv('Subagent share', sess.total_tokens
        ? Math.round(100 * sess.subagent_tokens / sess.total_tokens) + '%' : 'not measured') +
      kv('Skills carried', sess.skills.length
        ? fmtTokens(sess.skills_carried_tokens) + (ctx ? ' \u00b7 ' + sharePct(sess.skills_carried_tokens / ctx) : '')
        : 'none loaded') +
      (sess.skills.length
        ? '<h3>Skills and injected context</h3>' + sess.skills.map(function (row) {
            return kv(row.name + ' \u00b7 ' + (KIND_LABEL[row.kind] || row.kind) +
              (row.loads > 1 ? ' \u00b7 ' + row.loads + ' loads' : ''),
              fmtTokens(row.carried_tokens) + ' <small>from ' +
              (row.injected_tokens === null ? 'unmeasured' : fmtTokens(row.injected_tokens) + ' per load') + '</small>');
          }).join('')
        : '') +
      '<h3>How to make this cheaper</h3>' +
      (sess.tips.length
        ? '<ul class="tips">' + sess.tips.map(tipItem).join('') + '</ul>'
        : '<p class="hint">No tips: this session stayed under ' + esc(k) + ' of context.</p>');

    ['movers-body', 'sessions-body'].forEach(function (id) {
      var show = state.chosen || id === 'sessions-body';
      Array.prototype.forEach.call($(id).querySelectorAll('tr[data-id]'), function (tr) {
        tr.setAttribute('aria-selected',
          (show && tr.dataset.id === sess.session_id) ? 'true' : 'false');
      });
    });
  }

  // -------------------------------------------------------------- projects
  function renderProjects(data) {
    var summary = data.summary;
    $('th-projects-value').textContent = data.cost_available ? 'Cost' : 'Tokens';
    $('th-projects-over').textContent = 'Above ' + kOf(data.threshold);
    $('projects-title').textContent = 'Projects ' + data.period_phrase;
    $('th-projects-prev').textContent = 'vs ' + prevLabel(data);
    $('projects-hint').textContent = data.cost_available
      ? 'Dollars are allocated across days by weighted tokens. Grouping sessions by project is deferred.'
      : 'Token totals per project. Dollar figures are unavailable this refresh.';

    if (!data.projects.length) {
      $('projects-body').innerHTML = '<tr><td colspan="6" class="empty"><b>No projects ' + data.period_phrase + '</b>' +
        '<span>' + EMPTY_BODY + '</span></td></tr>';
      return;
    }
    $('projects-body').innerHTML = data.projects.map(function (pr) {
      var vs = '<span class="muted">new</span>';
      if (pr.previous_value) {
        var r = (pr.value - pr.previous_value) / pr.previous_value;
        var tone = r > 0.05 ? 'var(--up-bad)' : r < -0.05 ? 'var(--down-good)' : 'var(--ink-2)';
        vs = '<span style="color:' + tone + '">' + (r >= 0 ? '+' : '') + Math.round(r * 100) + '%</span>';
      }
      var allocated = data.cost_available ? ' <small>allocated</small>' : '';
      return '<tr><td class="title">' + esc(pr.project) + '</td>' +
        '<td><small title="' + esc(pr.cwd) + '">' + esc(shortPath(pr.cwd, 44)) + '</small></td>' +
        '<td class="r num">' + pr.sessions + '</td>' +
        '<td class="r num">' + magnitude(pr.value, data.unit) + allocated + '</td>' +
        '<td class="r num">' + vs + '</td>' +
        '<td class="r num" style="color:var(--threshold-text)">' + pct(pr.over_share) + '</td></tr>';
    }).join('');
  }

  function renderMonthTips(data) {
    $('tips-title').textContent = data.kind === 'day'
      ? (data.period_phrase === 'today' ? 'Today\u2019s tips' : 'This day\u2019s tips')
      : 'This month\u2019s tips';
    $('month-tips').innerHTML = data.month_tips.length
      ? data.month_tips.map(tipItem).join('')
      : '<li tabindex="-1" data-tip-id="none">' + ICON.tip +
        '<div><b>Nothing to fix</b><span>No session crossed ' + esc(kOf(data.threshold)) +
        ' of context ' + esc(data.period_phrase) + '.</span></div></li>';
  }

  function fillSelect(select, options, value) {
    var sig = options.map(function (o) { return o[0]; }).join(',');
    if (select.__sig !== sig) {
      select.innerHTML = options.map(function (o) {
        return '<option value="' + esc(o[0]) + '">' + esc(o[1]) + '</option>';
      }).join('');
      select.__sig = sig;
    }
    select.value = value;
  }

  /* Month picker, then the days of that month with data ("Whole month" first). */
  function renderPickers(data) {
    var months = data.months.slice().reverse();
    if (!months.length) months = [data.month];
    fillSelect($('month-select'), months.map(function (m) { return [m, monthLabel(m)]; }), data.month);
    $('month-select').disabled = months.length < 2;

    var days = [[data.month, 'Whole month']].concat(data.days.slice().reverse().map(function (d) {
      return [d, dayLabel(d)];
    }));
    fillSelect($('day-select'), days, data.kind === 'day' ? data.period : data.month);
    $('day-select').disabled = days.length < 2;
  }

  function renderFooter(data) {
    $('foot-note').textContent = data.cost_note;
    var warn = $('warn-note');
    if (data.warnings.length) {
      warn.hidden = false;
      warn.textContent = data.warnings.length + ' warning' + (data.warnings.length === 1 ? '' : 's') +
        ' during the last refresh: ' + data.warnings.join(' \u00b7 ');
    } else {
      warn.hidden = true;
      warn.textContent = '';
    }
  }

  function render(data) {
    state.data = data;
    $('title-month').textContent = periodLabel(data);
    $('subtitle').textContent = 'Claude Code usage \u00b7 refreshed ' + ago(data.generated_at);
    $('th-skills-carried').textContent = 'Carried ' + data.period_phrase;
    renderPickers(data);
    renderTrendStrip(data);
    renderChart(data);
    renderSplits(data);
    renderMovers(data);
    renderSkills(data);
    renderSessions(data);
    renderProjects(data);
    renderMonthTips(data);
    renderFooter(data);
    // Sessions seen for the first time start level: only later changes light up.
    var dirty = false;
    data.sessions.forEach(function (sess) {
      var v = valueOf(sess, data);
      if (v === null || v === undefined) return;
      var was = state.baseline[sess.session_id];
      if (was === undefined || v < was) { state.baseline[sess.session_id] = v; dirty = true; }
    });
    if (dirty) saveBaseline();
    $('page').setAttribute('aria-busy', 'false');
    state.booted = true;
    clearStale();
  }

  // ------------------------------------------------------------------ data
  function showStale(reason) {
    state.staleSince = state.staleSince || new Date().toISOString();
    var el = $('stale');
    var since = 'Stale since ' + clockTime(state.staleSince) + '. ';
    var body = state.data
      ? 'Showing the last good data, from ' + ago(state.data.generated_at) + '. ' + reason + '.'
      : 'No data has loaded yet. ' + reason + '.';
    el.innerHTML = ICON.alert + '<span>' + esc(since + body) + '</span>';
    el.hidden = false;
  }

  function clearStale() {
    state.staleSince = null;
    $('stale').hidden = true;
    $('stale').innerHTML = '';
  }

  function load() {
    var url = '/api/summary' + (state.period ? '?period=' + encodeURIComponent(state.period) : '');
    fetch(url, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('the server answered HTTP ' + r.status);
      return r.json();
    }).then(render).catch(function (e) {
      showStale('Refresh failed: ' + (e && e.message ? e.message : 'no response'));
    });
  }

  // ------------------------------------------------- panes, theme, routing
  function moveThumb(seg) {
    var on = seg.querySelector('[aria-selected="true"]');
    var thumb = seg.querySelector('.thumb');
    if (!on || !thumb) return;
    thumb.style.width = on.offsetWidth + 'px';
    thumb.style.transform = 'translateX(' + (on.offsetLeft - 3) + 'px)';
  }

  function selectSeg(seg, btn) {
    Array.prototype.forEach.call(seg.querySelectorAll('button'), function (b) {
      var on = b === btn;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      if (b.hasAttribute('aria-checked')) b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    moveThumb(seg);
  }

  function syncUrl() {
    var hash = '#' + state.pane;
    if (state.pane === 'sessions' && state.selected) hash += '/' + encodeURIComponent(state.selected);
    // Keep the other panes' parameters (the office's ?office= and ?hour=).
    var params = new URLSearchParams(location.search);
    params.delete('month'); // the old alias, now held as period
    if (state.period) params.set('period', state.period); else params.delete('period');
    var query = params.toString();
    history.replaceState(null, '', location.pathname + (query ? '?' + query : '') + hash);
  }

  function showPane(name, opts) {
    if (PANES.indexOf(name) === -1) name = 'month';
    state.pane = name;
    Array.prototype.forEach.call(document.querySelectorAll('.pane'), function (p) {
      p.hidden = p.id !== 'pane-' + name;
    });
    selectSeg($('tabs'), $('tabs').querySelector('[data-pane="' + name + '"]'));
    if (opts && opts.focus) $('pane-' + name).focus();
    syncUrl();
    // office.js starts and stops its own polling on this.
    document.dispatchEvent(new CustomEvent('usage:pane', { detail: name }));
  }

  function selectSession(id, opts) {
    state.selected = id;
    state.chosen = true;
    markSeen(id);
    if (state.data) renderDetail(state.data);
    syncUrl();
    if (opts && opts.tip) focusTip($('detail'), null);
  }

  function focusTip(scope, tipId) {
    var el = tipId ? scope.querySelector('[data-tip-id="' + cssEscape(tipId) + '"]') : null;
    if (!el) el = scope.querySelector('[data-tip-id]');
    if (!el) return;
    el.scrollIntoView({
      block: 'nearest',
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    });
    el.focus({ preventScroll: true });
    el.classList.add('lit');
    setTimeout(function () { el.classList.remove('lit'); }, 1400);
  }

  function applyTheme(name, persist) {
    if (['auto', 'light', 'dark'].indexOf(name) === -1) name = 'auto';
    if (name === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', name);
    if (persist !== false) {
      try { localStorage.setItem(THEME_KEY, name); } catch (e) { /* private mode */ }
    }
    selectSeg($('theme'), $('theme').querySelector('[data-theme="' + name + '"]'));
  }

  function routeFromHash() {
    var raw = (location.hash || '#month').slice(1).split('/');
    var pane = raw[0] || 'month';
    var id = raw[1] ? decodeURIComponent(raw[1]) : null;
    if (id) { state.selected = id; state.chosen = true; }
    showPane(pane);
    if (id && state.data) renderDetail(state.data);
  }

  // ------------------------------------------------------------- listeners
  $('tabs').addEventListener('click', function (e) {
    var b = e.target.closest('button');
    if (b) showPane(b.dataset.pane);
  });
  $('theme').addEventListener('click', function (e) {
    var b = e.target.closest('button');
    if (b) applyTheme(b.dataset.theme);
  });
  function choosePeriod(period) {
    state.period = period;
    syncUrl();
    load();
  }
  $('month-select').addEventListener('change', function (e) { choosePeriod(e.target.value); });
  $('day-select').addEventListener('change', function (e) { choosePeriod(e.target.value); });
  $('chart').addEventListener('click', function (e) {
    var bar = e.target.closest('[data-day]');
    if (bar) choosePeriod(bar.dataset.day);
  });

  // Every figure opens its tip: a metric cue, a "See why", or a row.
  document.addEventListener('click', function (e) {
    var cue = e.target.closest('[data-tip],[data-session],[data-goto]');
    if (cue) {
      e.preventDefault();
      if (cue.dataset.session) {
        showPane('sessions', { focus: false });
        selectSession(cue.dataset.session, { tip: true });
      } else if (cue.dataset.tip) {
        showPane('month');
        focusTip($('month-tips'), cue.dataset.tip);
      } else {
        showPane(cue.dataset.goto, { focus: true });
      }
      return;
    }
    var row = e.target.closest('tr[data-id]');
    if (row) {
      showPane('sessions');
      selectSession(row.dataset.id, { tip: false });
      var live = document.querySelector('#sessions-body tr[data-id="' + cssEscape(row.dataset.id) + '"]');
      if (live) live.focus();
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var bar = e.target.closest && e.target.closest('[data-day]');
    if (bar) { e.preventDefault(); choosePeriod(bar.dataset.day); return; }
    var row = e.target.closest && e.target.closest('tr[data-id]');
    if (!row) return;
    e.preventDefault();
    showPane('sessions');
    selectSession(row.dataset.id, { tip: false });
  });

  // Focusing a held row counts as looking at it.
  document.addEventListener('focusin', function (e) {
    var row = e.target.closest && e.target.closest('tr[data-id]');
    if (row && row.classList.contains('held')) markSeen(row.dataset.id);
  });

  var redrawTimer = null;
  function remeasure() {
    moveThumb($('tabs')); moveThumb($('theme'));
    // The chart is drawn to its panel's width; redraw once the resize settles.
    clearTimeout(redrawTimer);
    redrawTimer = setTimeout(function () { if (state.data) renderChart(state.data); }, 120);
  }
  window.addEventListener('resize', remeasure);
  window.addEventListener('load', remeasure);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(remeasure);
  window.addEventListener('hashchange', routeFromHash);

  // The Office tab (office.js) opens a live session from a worker (a live
  // session has calls today, so it is in the current month's list) and
  // dresses its theme picker like the header's.
  window.usageDashboard = {
    selectSeg: selectSeg,
    openSession: function (id) {
      var thisMonth = new Date().toISOString().slice(0, 7);
      if (state.period && state.period.slice(0, 7) !== thisMonth) choosePeriod(null);
      showPane('sessions', { focus: false });
      selectSession(id, { tip: false });
    }
  };

  // ------------------------------------------------------------------ boot
  var params = new URLSearchParams(location.search);
  state.period = params.get('period') || params.get('month') || null;
  loadBaseline();
  if (params.get('theme')) applyTheme(params.get('theme'), false);
  else applyTheme(localStorage.getItem(THEME_KEY) || 'auto');
  routeFromHash();
  load();
  setInterval(load, 60000);
})();
