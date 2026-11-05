// Drives dashboard/office-rail.js without a browser. Run by tests/test_office_sim.py.
'use strict';
global.window = {};
require('../dashboard/format.js');
require('../dashboard/office-cast.js');
require('../dashboard/office-rail.js');
var R = window.OfficeRail;

function expect(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exitCode = 1; } }

var T0 = Date.parse('2026-10-14T10:00:00Z');
function sub(id, parent, state, extra) {
  var s = { agent_id: id, type: 'Explore', description: 'look at ' + id, background: false, depth: parent ? 2 : 1,
            parent: parent, model: 'claude-sonnet-5', tokens: 38200, tools: 14, tool_mix: { Read: 6, Grep: 5, Glob: 3 },
            doing: { tool: 'Grep', target: 'subagent' }, started: '2026-10-14T09:58:20Z',
            last: '2026-10-14T09:59:50Z', state: state };
  Object.keys(extra || {}).forEach(function (k) { s[k] = extra[k]; });
  return s;
}
var data = { generated_at: '2026-10-14T10:00:00Z', rooms: [{ project: 'usage', cwd: '/x', workers: [
  { session_id: 's1', title: 'office <v4>', model: 'Opus 5', state: 'working', since: '2026-10-14T09:48:00Z',
    doing: { tool: 'Bash', target: 'uv run pytest -q' }, agents_hidden: 3, agents: [
      sub('a', null, 'running', { type: 'T<b>"&', description: 'D<b>"&', doing: { tool: 'Grep', target: 'X<b>"&' }, tool_mix: { 'M<b>"&': 2 } }), sub('b', 'a', 'running', { background: true }), sub('c', null, 'done'),
      sub('orphan', 'gone-parent', 'asleep')] },
  { session_id: 's2', title: 'quiet one', model: '', state: 'asleep', since: '2026-10-14T09:00:00Z',
    doing: null, agents_hidden: 0, agents: [] }] }] };

var order = R.tree(data.rooms[0].workers[0].agents).map(function (n) { return n.sub.agent_id + ':' + n.depth; });
expect(order.join(',') === 'a:1,b:2,c:1,orphan:1', 'tree is depth-first and an orphan is a root (' + order + ')');
expect(R.duration('2026-10-14T09:59:15Z', null, T0) === '45s', 'seconds');
expect(R.duration('2026-10-14T09:56:55Z', '2026-10-14T10:00:00Z', 0) === '3m05', 'minutes and seconds');
expect(R.duration('2026-10-14T08:48:00Z', null, T0) === '1h12', 'hours and minutes');

var html = R.markup(data, T0);
expect(html.indexOf('office &lt;v4&gt;') !== -1 && html.indexOf('<v4>') === -1, 'titles are escaped');
['T<b>', 'D<b>', 'X<b>', 'M<b>'].forEach(function (raw) { expect(html.indexOf(raw) === -1, 'sub text is escaped: ' + raw); });
expect(html.indexOf('T&lt;b&gt;&quot;&amp;') !== -1 && html.indexOf('X&lt;b&gt;&quot;&amp;') !== -1, 'escaped forms are present');
expect((html.match(/class="rail-session"/g) || []).length === 2, 'one block per live session');
expect(html.indexOf('data-agent="s1/b"') !== -1 && html.indexOf('--depth:2') !== -1, 'nested rows indent');
expect(html.indexOf('state-done') !== -1 && html.indexOf('state-asleep') !== -1, 'done and asleep rows are marked');
expect(html.indexOf('Bash: uv run pytest -q') !== -1 && html.indexOf('Grep: subagent') !== -1, 'leads and interns show what they do');
expect(html.indexOf('38.2K tok') !== -1 && html.indexOf('14 tools') !== -1, 'tokens and tool counts');
var one = R.metaLine(sub('solo', null, 'running', { tools: 1 }), T0);
expect(one.indexOf('1 tool') !== -1 && one.indexOf('1 tools') === -1, 'one tool is singular (' + one + ')');
expect(html.indexOf('Read 6 · Grep 5 · Glob 3') !== -1, 'the tool mix');
expect(html.indexOf('+3 earlier') !== -1, 'hidden agents are counted');
expect(html.indexOf('2 running') !== -1 && html.indexOf('1 done') !== -1, 'the session header sums its team');
expect(html.indexOf('data-to=""') !== -1, 'running rows tick');
expect(html.indexOf('NaN') === -1 && html.indexOf('undefined') === -1, 'no NaN or undefined');
var sig = R.signature(data);
data.rooms[0].workers[0].agents[0].doing = { tool: 'Read', target: '/a' };
expect(R.signature(data) !== sig, 'a new doing changes the signature');
console.log(JSON.stringify({ rows: (html.match(/class="rail-row state-/g) || []).length, order: order }));
