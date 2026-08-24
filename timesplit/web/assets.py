"""CSS and JavaScript, embedded as strings.

Everything is inline: no external requests, so the dashboard works offline and
cannot leak a page view to anyone. The JavaScript is deliberately tiny -- the
pages are rendered on the server, and the script only handles the one
interaction that matters, which is correcting a category.
"""

from __future__ import annotations

CSS = """
:root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #1f2328; --muted: #656d76;
  --line: #d8dde3; --muted-bg: #eaedf1; --accent: #3b7dd8;
  --warn-bg: #fff6e0; --warn-line: #e8c96a; --radius: 10px;
  --shadow: 0 1px 2px rgba(16,22,26,.06), 0 4px 12px rgba(16,22,26,.05);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --panel: #1c2024; --ink: #e6e9ec; --muted: #9aa4ad;
    --line: #2c3238; --muted-bg: #262b30; --warn-bg: #33290f; --warn-line: #6b5715;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--accent); }
header {
  background: var(--panel); border-bottom: 1px solid var(--line);
  padding: 12px 20px; display: flex; align-items: center; gap: 18px;
  flex-wrap: wrap; position: sticky; top: 0; z-index: 5;
}
header h1 { font-size: 16px; margin: 0; font-weight: 650; letter-spacing: -.01em; }
nav { display: flex; gap: 4px; flex-wrap: wrap; }
nav a {
  padding: 5px 11px; border-radius: 7px; text-decoration: none;
  color: var(--muted); font-size: 14px;
}
nav a:hover { background: var(--muted-bg); color: var(--ink); }
nav a.active { background: var(--accent); color: #fff; }
main { padding: 20px; max-width: 1100px; margin: 0 auto; }
.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 18px; margin-bottom: 18px; box-shadow: var(--shadow);
}
.panel h2 { margin: 0 0 14px; font-size: 14px; text-transform: uppercase;
  letter-spacing: .05em; color: var(--muted); font-weight: 600; }
.row { display: flex; gap: 18px; flex-wrap: wrap; align-items: flex-start; }
.grow { flex: 1 1 320px; min-width: 0; }
.totals { display: flex; gap: 14px; flex-wrap: wrap; }
.total-card {
  flex: 1 1 170px; padding: 14px 16px; border-radius: var(--radius);
  border: 1px solid var(--line); border-left-width: 4px; background: var(--panel);
}
.total-card .name { font-size: 13px; color: var(--muted); }
.total-card .value { font-size: 26px; font-weight: 650; letter-spacing: -.02em;
  margin-top: 2px; font-variant-numeric: tabular-nums; }
.total-card .sub { font-size: 12px; color: var(--muted); margin-top: 3px; }
.banner {
  background: var(--warn-bg); border: 1px solid var(--warn-line);
  border-radius: var(--radius); padding: 12px 16px; margin-bottom: 18px;
  display: flex; justify-content: space-between; gap: 12px; align-items: center;
  flex-wrap: wrap;
}
.timeline-wrap { overflow-x: auto; padding-bottom: 4px; }
.timeline { display: block; }
.timeline-bg { fill: var(--muted-bg); }
.hourline { stroke: var(--line); stroke-width: 1; }
.gridline { stroke: var(--line); stroke-width: 1; stroke-dasharray: 2 3; }
.axis { fill: var(--muted); font-size: 10px; }
.work-block { cursor: pointer; }
.work-block:hover { opacity: .82; }
.idle-block { fill: var(--line); }
.donut-total { font-size: 19px; font-weight: 650; fill: var(--ink); }
.donut-label, .donut-empty { font-size: 11px; fill: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: .04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.chip { display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 12px; color: #fff; white-space: nowrap; }
.chip.muted { background: var(--muted); }
.title-cell { max-width: 380px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.sub { color: var(--muted); font-size: 12px; }
button, .btn {
  font: inherit; font-size: 13px; padding: 5px 11px; border-radius: 7px;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink);
  cursor: pointer;
}
button:hover { background: var(--muted-bg); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.primary:hover { filter: brightness(1.07); }
button[disabled] { opacity: .5; cursor: default; }
.actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
select, input[type=text], input[type=date], input[type=number], input[type=password] {
  font: inherit; font-size: 13px; padding: 5px 8px; border-radius: 7px;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink);
}
.bar-row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.bar-label { flex: 0 0 190px; font-size: 13px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.bar-track { flex: 1 1 auto; height: 9px; background: var(--muted-bg);
  border-radius: 5px; overflow: hidden; }
.bar-fill { display: block; height: 100%; border-radius: 5px; }
.bar-value { flex: 0 0 66px; text-align: right; font-size: 12px;
  color: var(--muted); font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); font-size: 14px; margin: 6px 0; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 13px; }
.legend span { display: flex; align-items: center; gap: 6px; }
.swatch { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
.hatched { background-image: repeating-linear-gradient(45deg,
  transparent, transparent 3px, rgba(0,0,0,.28) 3px, rgba(0,0,0,.28) 6px); }
.flash { position: fixed; right: 18px; bottom: 18px; background: var(--ink);
  color: var(--bg); padding: 9px 15px; border-radius: 8px; font-size: 13px;
  opacity: 0; transition: opacity .18s; pointer-events: none; z-index: 20; }
.flash.show { opacity: 1; }
.note { font-size: 13px; color: var(--muted); line-height: 1.6; }
.note code { background: var(--muted-bg); padding: 1px 5px; border-radius: 4px;
  font-size: 12px; }
footer { color: var(--muted); font-size: 12px; text-align: center; padding: 8px 0 28px; }
@media (max-width: 640px) {
  main { padding: 12px; }
  .bar-label { flex-basis: 120px; }
  .title-cell { max-width: 170px; }
}
"""

JS = """
(function () {
  var token = document.body.dataset.token || '';

  function flash(message) {
    var el = document.getElementById('flash');
    if (!el) return;
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('show'); }, 2600);
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-TS-Token': token },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-action]');
    if (!button) return;
    var action = button.dataset.action;

    if (action === 'correct') {
      var row = button.closest('[data-session-id]');
      var scopeEl = row ? row.querySelector('[data-scope]') : null;
      button.disabled = true;
      post('/api/correct', {
        session_id: Number(button.dataset.sessionId),
        category: button.dataset.category,
        scope: scopeEl ? scopeEl.value : 'session'
      }).then(function (res) {
        if (!res.ok) { flash(res.error || 'Could not save that.'); button.disabled = false; return; }
        var extra = res.reclassified ? ' (' + res.reclassified + ' other sessions updated)' : '';
        flash('Saved' + extra);
        setTimeout(function () { location.reload(); }, 550);
      }).catch(function () { flash('Could not reach the tracker.'); button.disabled = false; });
    }

    if (action === 'confirm') {
      button.disabled = true;
      post('/api/confirm', { session_id: Number(button.dataset.sessionId) }).then(function () {
        flash('Confirmed');
        setTimeout(function () { location.reload(); }, 400);
      });
    }

    if (action === 'delete-rule') {
      if (!confirm('Delete this rule?')) return;
      post('/api/rule/delete', { rule_id: Number(button.dataset.ruleId) }).then(function (res) {
        flash('Rule deleted' + (res.reclassified ? ', ' + res.reclassified + ' sessions updated' : ''));
        setTimeout(function () { location.reload(); }, 500);
      });
    }
  });

  // Clicking a block in the timeline jumps to that session's row.
  document.addEventListener('click', function (event) {
    var block = event.target.closest('[data-session-id].work-block');
    if (!block) return;
    var target = document.getElementById('session-' + block.dataset.sessionId);
    if (target) {
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      target.style.transition = 'background .3s';
      target.style.background = 'var(--muted-bg)';
      setTimeout(function () { target.style.background = ''; }, 1400);
    }
  });

  // The today view refreshes itself so the tray and the page agree.
  if (document.body.dataset.autorefresh === '1') {
    setTimeout(function () { location.reload(); }, 60000);
  }
})();
"""
