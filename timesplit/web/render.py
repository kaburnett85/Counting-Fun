"""HTML for the dashboard pages, built with f-strings.

No template engine: the pages are simple, and Jinja would be another
dependency loaded into a process that is meant to sit quietly in the tray.
"""

from __future__ import annotations

from datetime import datetime

from ..core.aggregate import DayTotals, TimelineBlock
from ..core.clock import add_days, fmt_duration, fmt_hours, local_dt
from .assets import CSS, JS
from .charts import Slice, bar_row, donut, esc, stacked_days, timeline_band

NAV = [
    ("/", "Today"),
    ("/week", "Week"),
    ("/review", "Review"),
    ("/rules", "Rules"),
    ("/export", "Export"),
    ("/settings", "Settings"),
]


def page(title: str, body: str, *, active: str = "/", token: str = "",
         autorefresh: bool = False) -> str:
    nav = "".join(
        f'<a href="{esc(href)}" class="{"active" if href == active else ""}">{esc(label)}</a>'
        for href, label in NAV
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{esc(title)} · TimeSplit</title>
<style>{CSS}</style>
</head>
<body data-token="{esc(token)}" data-autorefresh="{'1' if autorefresh else '0'}">
<header>
  <h1>TimeSplit</h1>
  <nav>{nav}</nav>
</header>
<main>{body}</main>
<footer>Everything here is stored on this computer only.</footer>
<div class="flash" id="flash"></div>
<script>{JS}</script>
</body></html>"""


# ---- shared fragments ----------------------------------------------------


def totals_cards(totals: DayTotals, *, show_idle: bool = True) -> str:
    cards = []
    for cat in totals.categories:
        if cat.key == "excluded" and cat.seconds <= 0:
            continue
        sub = f"{cat.sessions} session{'s' if cat.sessions != 1 else ''}"
        if cat.review_seconds > 0:
            sub += f" · {fmt_duration(cat.review_seconds)} unconfirmed"
        cards.append(
            f'<div class="total-card" style="border-left-color:{esc(cat.color)}">'
            f'<div class="name">{esc(cat.name)}</div>'
            f'<div class="value">{esc(fmt_duration(cat.seconds))}</div>'
            f'<div class="sub">{esc(sub)} · {fmt_hours(cat.seconds)} h</div></div>'
        )
    if show_idle and totals.idle_seconds > 0:
        cards.append(
            '<div class="total-card" style="border-left-color:var(--line)">'
            '<div class="name">Away / idle</div>'
            f'<div class="value">{esc(fmt_duration(totals.idle_seconds))}</div>'
            '<div class="sub">not billed to either job</div></div>'
        )
    if not cards:
        return '<p class="empty">Nothing tracked yet. Leave it running and check back.</p>'
    return f'<div class="totals">{"".join(cards)}</div>'


def review_banner(count: int, seconds: float) -> str:
    if count <= 0:
        return ""
    return (
        f'<div class="banner"><div><strong>{count} '
        f'session{"s" if count != 1 else ""} need a decision</strong> '
        f'<span class="sub">({esc(fmt_duration(seconds))} unassigned or uncertain)</span></div>'
        f'<a class="btn" href="/review">Review them</a></div>'
    )


def category_buttons(session_id: int, categories: list[dict], *, scope: bool = True) -> str:
    buttons = "".join(
        f'<button data-action="correct" data-session-id="{session_id}"'
        f' data-category="{esc(c["key"])}" style="border-left:4px solid {esc(c["color"])}">'
        f"{esc(c['display_name'])}</button>"
        for c in categories
    )
    scope_select = ""
    if scope:
        scope_select = (
            '<select data-scope title="How widely should this apply?">'
            '<option value="session">just this</option>'
            '<option value="same_title">same window title</option>'
            '<option value="same_domain">same website</option>'
            '<option value="same_exe">same program</option>'
            "</select>"
        )
    return f'<div class="actions">{buttons}{scope_select}</div>'


def _time_range(row: dict, tz) -> str:
    start = local_dt(float(row["start_ts"]), tz).strftime("%H:%M")
    end = local_dt(float(row["end_ts"]), tz).strftime("%H:%M")
    return f"{start}–{end}"


def sessions_table(rows: list[dict], categories: list[dict], tz, *, show_actions: bool = True) -> str:
    if not rows:
        return '<p class="empty">No sessions recorded.</p>'
    body = []
    for row in rows:
        color = row.get("category_color") or "#9aa0a6"
        name = row.get("category_name") or "Uncategorised"
        flag = ' <span class="sub">· unconfirmed</span>' if row["needs_review"] else ""
        lock = ' <span class="sub">· you set this</span>' if row["is_locked"] else ""
        site = f'<div class="sub">{esc(row["domain"])}</div>' if row.get("domain") else ""
        actions = (
            category_buttons(int(row["id"]), categories) if show_actions else ""
        )
        body.append(
            f'<tr id="session-{row["id"]}" data-session-id="{row["id"]}">'
            f'<td class="sub">{esc(_time_range(row, tz))}</td>'
            f'<td class="title-cell" title="{esc(row["title"])}">{esc(row["title"] or "(no title)")}'
            f'{site}</td>'
            f'<td class="sub">{esc(row.get("exe_name") or "")}</td>'
            f'<td class="num">{esc(fmt_duration(float(row["duration_s"])))}</td>'
            f'<td><span class="chip" style="background:{esc(color)}">{esc(name)}</span>'
            f"{flag}{lock}</td>"
            f"<td>{actions}</td></tr>"
        )
    return f"""<table>
<thead><tr><th>When</th><th>Window</th><th>App</th><th class="num">Time</th>
<th>Job</th><th>Change to</th></tr></thead>
<tbody>{"".join(body)}</tbody></table>"""


def legend(categories: list[dict]) -> str:
    items = "".join(
        f'<span><i class="swatch" style="background:{esc(c["color"])}"></i>'
        f"{esc(c['display_name'])}</span>"
        for c in categories
    )
    return (
        f'<div class="legend">{items}'
        '<span><i class="swatch" style="background:var(--line)"></i>away / idle</span>'
        '<span><i class="swatch hatched" style="background:var(--muted)"></i>'
        "unconfirmed</span></div>"
    )


# ---- pages ---------------------------------------------------------------


def day_page(
    *, day: str, totals: DayTotals, blocks: list[TimelineBlock], rows: list[dict],
    categories: list[dict], tz, day_bounds: tuple[float, float],
    review: tuple[int, float], token: str, is_today: bool,
) -> str:
    slices = [
        Slice(c.name, c.seconds, c.color)
        for c in totals.categories
        if c.key != "excluded" and c.seconds > 0
    ]
    heading = "Today" if is_today else datetime.fromisoformat(day).strftime("%A, %d %B %Y")
    nav = (
        f'<div class="actions"><a class="btn" href="/day?d={esc(add_days(day, -1))}">'
        f"&larr; previous day</a>"
        + (
            f'<a class="btn" href="/day?d={esc(add_days(day, 1))}">next day &rarr;</a>'
            if not is_today
            else ""
        )
        + "</div>"
    )
    body = f"""
{review_banner(*review)}
<div class="panel">
  <h2>{esc(heading)}</h2>
  <div class="row">
    <div class="grow">{totals_cards(totals)}</div>
    <div>{donut(slices)}</div>
  </div>
</div>
<div class="panel">
  <h2>Timeline</h2>
  <div class="timeline-wrap">{timeline_band(blocks, day_bounds[0], day_bounds[1])}</div>
  {legend(categories)}
  <p class="note">Click a block to jump to it below. Hatched blocks are guesses
  the tracker is not sure about.</p>
</div>
<div class="panel">
  <h2>Sessions</h2>
  {sessions_table(rows, categories, tz)}
  {nav}
</div>"""
    return page(heading, body, active="/" if is_today else "/day", token=token,
                autorefresh=is_today)


def week_page(
    *, days: list[str], per_day: list[DayTotals], categories: list[dict],
    apps: list[dict], domains: list[dict], review: tuple[int, float],
    token: str, week_label: str, prev_week: str, next_week: str,
) -> str:
    series = []
    for cat in categories:
        values = [d.seconds_for(cat["key"]) for d in per_day]
        if any(values):
            series.append((cat["display_name"], cat["color"], values))

    combined = {}
    for day_total in per_day:
        for cat in day_total.categories:
            entry = combined.setdefault(cat.key, [cat.name, cat.color, 0.0, 0])
            entry[2] += cat.seconds
            entry[3] += cat.sessions

    cards = "".join(
        f'<div class="total-card" style="border-left-color:{esc(color)}">'
        f'<div class="name">{esc(name)}</div>'
        f'<div class="value">{esc(fmt_duration(seconds))}</div>'
        f'<div class="sub">{fmt_hours(seconds)} h · {count} sessions</div></div>'
        for name, color, seconds, count in sorted(
            combined.values(), key=lambda v: v[2], reverse=True
        )
    ) or '<p class="empty">Nothing tracked this week.</p>'

    app_peak = max([float(a["seconds"]) for a in apps], default=1.0)
    app_rows = "".join(
        bar_row(a["exe_name"], float(a["seconds"]), app_peak,
                a.get("category_color") or "#9aa0a6")
        for a in apps
    ) or '<p class="empty">No apps yet.</p>'

    domain_peak = max([float(d["seconds"]) for d in domains], default=1.0)
    domain_rows = "".join(
        bar_row(d["domain"], float(d["seconds"]), domain_peak,
                d.get("category_color") or "#9aa0a6")
        for d in domains
    ) or '<p class="empty">No websites yet.</p>'

    day_links = "".join(
        f'<a class="btn" href="/day?d={esc(d)}">{esc(d[5:])}</a>' for d in days
    )

    body = f"""
{review_banner(*review)}
<div class="panel">
  <h2>{esc(week_label)}</h2>
  <div class="totals">{cards}</div>
  {stacked_days(days, series)}
  {legend(categories)}
  <div class="actions" style="margin-top:12px">
    <a class="btn" href="/week?d={esc(prev_week)}">&larr; previous week</a>
    <a class="btn" href="/week?d={esc(next_week)}">next week &rarr;</a>
    {day_links}
  </div>
</div>
<div class="row">
  <div class="panel grow"><h2>Where the time went — apps</h2>{app_rows}</div>
  <div class="panel grow"><h2>Where the time went — websites</h2>{domain_rows}</div>
</div>"""
    return page("Week", body, active="/week", token=token)


def review_page(*, rows: list[dict], categories: list[dict], tz,
                review: tuple[int, float], token: str) -> str:
    count, seconds = review
    if not rows:
        body = """
<div class="panel"><h2>Review</h2>
<p class="empty">Nothing to review — every session has a job assigned.</p></div>"""
        return page("Review", body, active="/review", token=token)

    items = []
    for row in rows:
        color = row.get("category_color") or "#9aa0a6"
        name = row.get("category_name") or "Uncategorised"
        guess = (
            f'<span class="chip" style="background:{esc(color)}">{esc(name)}</span>'
            f' <span class="sub">guessed at {float(row["confidence"]):.0%}</span>'
            if row["source"] == "model"
            else '<span class="chip muted">not recognised</span>'
        )
        confirm = (
            f'<button data-action="confirm" data-session-id="{row["id"]}">that is right</button>'
            if row["source"] == "model"
            else ""
        )
        site = f'<div class="sub">{esc(row["domain"])}</div>' if row.get("domain") else ""
        items.append(
            f'<tr id="session-{row["id"]}" data-session-id="{row["id"]}">'
            f'<td class="num">{esc(fmt_duration(float(row["duration_s"])))}</td>'
            f'<td class="title-cell" title="{esc(row["title"])}">'
            f'{esc(row["title"] or "(no title)")}{site}</td>'
            f'<td class="sub">{esc(row.get("exe_name") or "")}</td>'
            f"<td>{guess}</td>"
            f'<td>{category_buttons(int(row["id"]), categories)}{confirm}</td></tr>'
        )

    body = f"""
<div class="panel">
  <h2>Review</h2>
  <p class="note"><strong>{count} session{"s" if count != 1 else ""}</strong>
  totalling {esc(fmt_duration(seconds))} need a decision. They are listed
  longest first, so the first few clicks account for most of the time.
  Choosing “same website” or “same program” creates a rule, and everything
  matching it is updated at once.</p>
  <table>
    <thead><tr><th class="num">Time</th><th>Window</th><th>App</th>
    <th>Current guess</th><th>Assign to</th></tr></thead>
    <tbody>{"".join(items)}</tbody>
  </table>
</div>"""
    return page("Review", body, active="/review", token=token)


def rules_page(*, rules: list[dict], categories: list[dict], model: dict,
               token: str, message: str = "") -> str:
    by_source: dict[str, list[dict]] = {}
    for rule in rules:
        by_source.setdefault(rule["source"], []).append(rule)

    labels = {
        "user": "Yours",
        "wizard": "From setup",
        "llm": "Suggested by Claude",
        "seed": "Shipped defaults",
    }
    sections = []
    for source in ("user", "wizard", "llm", "seed"):
        group = by_source.get(source)
        if not group:
            continue
        body_rows = "".join(
            f"<tr><td>{esc(r['kind'].replace('_', ' '))}</td>"
            f"<td><code>{esc(r['pattern'])}</code></td>"
            f'<td><span class="chip" style="background:{esc(r["category_color"])}">'
            f"{esc(r['category_name'])}</span></td>"
            f'<td class="num sub">{r["hit_count"]}</td>'
            f'<td><button data-action="delete-rule" data-rule-id="{r["id"]}">delete</button>'
            f"</td></tr>"
            for r in group
        )
        sections.append(
            f'<div class="panel"><h2>{esc(labels.get(source, source))} '
            f"({len(group)})</h2><table><thead><tr><th>Match on</th><th>Pattern</th>"
            f'<th>Job</th><th class="num">Used</th><th></th></tr></thead>'
            f"<tbody>{body_rows}</tbody></table></div>"
        )

    options = "".join(
        f'<option value="{esc(c["key"])}">{esc(c["display_name"])}</option>' for c in categories
    )
    warm = (
        f"Learning from {int(model['corrections'])} correction"
        f"{'s' if model['corrections'] != 1 else ''}"
        f" · {model['vocabulary']} terms"
        if model["warm"]
        else f"Still warming up — {int(model['corrections'])} of {model['min_docs']}"
        " corrections before it starts predicting on its own"
    )

    body = f"""
{f'<div class="banner">{esc(message)}</div>' if message else ""}
<div class="panel">
  <h2>Add a rule</h2>
  <form method="post" action="/rules/add" class="actions">
    <input type="hidden" name="token" value="{esc(token)}">
    <select name="kind">
      <option value="domain">website is</option>
      <option value="domain_suffix">website ends with</option>
      <option value="url_prefix">web address starts with</option>
      <option value="exe">program is</option>
      <option value="title_contains">title contains</option>
      <option value="title_regex">title matches (regex)</option>
    </select>
    <input type="text" name="pattern" placeholder="zillow.com" size="28" required>
    <select name="category">{options}</select>
    <button class="primary" type="submit">Add rule</button>
  </form>
  <p class="note">Rules you add here beat everything else, and are applied to
  the last 30 days plus anything still uncategorised.</p>
</div>
<div class="panel">
  <h2>Learned model</h2>
  <p class="note">{esc(warm)}. It only ever learns from corrections you make —
  never from its own guesses.</p>
</div>
{"".join(sections)}"""
    return page("Rules", body, active="/rules", token=token)


def export_page(*, days: list[str], token: str, default_from: str, default_to: str,
                message: str = "", xlsx_available: bool = True) -> str:
    xlsx_option = (
        '<option value="xlsx">Excel workbook (.xlsx)</option>'
        if xlsx_available
        else '<option value="xlsx" disabled>Excel (install openpyxl to enable)</option>'
    )
    body = f"""
{f'<div class="banner">{esc(message)}</div>' if message else ""}
<div class="panel">
  <h2>Export a timesheet</h2>
  <form method="post" action="/export/run" class="actions">
    <input type="hidden" name="token" value="{esc(token)}">
    <label class="sub">from <input type="date" name="from" value="{esc(default_from)}"></label>
    <label class="sub">to <input type="date" name="to" value="{esc(default_to)}"></label>
    <select name="format"><option value="csv">CSV files</option>{xlsx_option}</select>
    <select name="rounding">
      <option value="0">exact minutes</option>
      <option value="6">round to 6 min (0.1 h)</option>
      <option value="15">round to 15 min</option>
      <option value="30">round to 30 min</option>
    </select>
    <label class="sub"><input type="checkbox" name="exclude_unreviewed" value="1">
      leave out unconfirmed time</label>
    <button class="primary" type="submit">Write files</button>
  </form>
  <p class="note">Files are written to your TimeSplit data folder and the path
  is shown here — nothing is uploaded anywhere. You get a session-by-session
  file, a daily totals file, and an invoice-shaped file with decimal hours.</p>
</div>
<div class="panel">
  <h2>Days with data</h2>
  <p class="note">{esc(f"{len(days)} days recorded" if days else "Nothing recorded yet.")}
  {esc(f"({days[0]} to {days[-1]})" if days else "")}</p>
</div>"""
    return page("Export", body, active="/export", token=token)


def settings_page(*, cfg, model: dict, secrets_desc: str, llm_stats: dict,
                  db_size: int, data_dir: str, autostart: tuple[bool, str],
                  token: str, message: str = "", preview: str = "") -> str:
    jobs = "".join(
        f'<tr><td><span class="chip" style="background:{esc(c.color)}">'
        f"{esc(c.display_name)}</span></td><td>{esc(c.description or '—')}</td></tr>"
        for c in cfg.categories
    )
    installed, autostart_note = autostart
    llm = cfg.llm_assist
    preview_block = (
        f'<div class="panel"><h2>Exactly what would be sent</h2>'
        f'<pre class="note" style="white-space:pre-wrap;overflow-x:auto">{esc(preview)}</pre>'
        f"</div>"
        if preview
        else ""
    )
    body = f"""
{f'<div class="banner">{esc(message)}</div>' if message else ""}
<div class="panel">
  <h2>Your jobs</h2>
  <table><thead><tr><th>Job</th><th>Description</th></tr></thead>
  <tbody>{jobs}</tbody></table>
  <p class="note">Run <code>python -m timesplit wizard</code> to rename these or
  change which sites and programs they start with.</p>
</div>

<div class="panel">
  <h2>How it is tracking</h2>
  <table>
    <tr><td>Samples the active window every</td><td>{cfg.sampling.interval_s} seconds</td></tr>
    <tr><td>Counts you as away after</td><td>{cfg.idle.threshold_s // 60} minutes
      with no keyboard or mouse</td></tr>
    <tr><td>Locked screen and screensaver</td>
      <td>{"stop the clock" if cfg.idle.treat_lock_as_idle else "keep counting"}</td></tr>
    <tr><td>Reads web addresses from</td>
      <td>{esc(", ".join(cfg.browser.browser_exes)) if cfg.browser.url_extraction
            else "disabled — window titles only"}</td></tr>
    <tr><td>Starts when you log in</td>
      <td>{"yes" if installed else "no"} <span class="sub">{esc(autostart_note)}</span></td></tr>
  </table>
</div>

<div class="panel">
  <h2>What stays on this computer</h2>
  <p class="note">
    Everything. Your sessions, window titles, web addresses, rules and the
    learned model live in <code>{esc(data_dir)}</code>
    ({db_size / 1_048_576:.1f} MB) and are never uploaded. The dashboard listens
    only on 127.0.0.1 and shuts itself down
    {cfg.dashboard.auto_shutdown_min} minutes after you close it.
    No screenshots are ever taken.
  </p>
  <p class="note">
    Windows from these programs are not recorded at all:
    <code>{esc(", ".join(cfg.privacy.exclude_exes) or "none")}</code>.
    Neither are windows whose title contains:
    <code>{esc(", ".join(cfg.privacy.exclude_title_patterns) or "none")}</code>.
    Their time still counts toward the day so the totals add up — only the
    content is dropped.
  </p>
</div>

<div class="panel">
  <h2>Learned model</h2>
  <p class="note">
    {int(model["corrections"])} correction{"s" if model["corrections"] != 1 else ""}
    learned · {model["vocabulary"]} terms ·
    {"actively predicting" if model["warm"] else
     f"warming up ({model['min_docs']} corrections needed)"}.
    It learns only from you.
  </p>
  <form method="post" action="/settings/rebuild" class="actions">
    <input type="hidden" name="token" value="{esc(token)}">
    <button type="submit">Rebuild from my corrections</button>
  </form>
</div>

<div class="panel">
  <h2>Claude assist <span class="sub">— {"on" if llm.enabled else "off"}</span></h2>
  <p class="note">
    Optional. When a window matches none of your rules and the learned model is
    unsure, TimeSplit can ask Claude which job it belongs to. This is the only
    feature that sends anything off this computer.
  </p>
  <p class="note">
    <strong>What it would send:</strong> the program name (for example
    <code>chrome.exe</code>), the website domain (<code>dotloop.com</code>) and
    the window title with email addresses, long digit strings and file paths
    removed. {"Full web addresses are also sent." if llm.send_urls
    else "Full web addresses are <strong>not</strong> sent."}
    Never screenshots, file contents, or keystrokes. Repeated windows are sent
    once, not every time.
  </p>
  <p class="note">
    <strong>Limits:</strong> at most {llm.daily_request_cap} requests a day,
    one hour apart, and it stops at ${llm.monthly_cost_cap_usd:.2f} a month.
    Used so far this month: ${llm_stats.get("month_cost", 0.0):.2f}
    across {llm_stats.get("month_requests", 0)} requests.
    API key: {esc(secrets_desc)}.
  </p>
  <form method="post" action="/settings/llm-preview" class="actions">
    <input type="hidden" name="token" value="{esc(token)}">
    <button type="submit">Show me exactly what would be sent</button>
  </form>
  <p class="note">Turn it on or off in <code>config.json</code> under
  <code>llm_assist.enabled</code>, and set the key with
  <code>python -m timesplit wizard</code>.</p>
</div>
{preview_block}"""
    return page("Settings", body, active="/settings", token=token)
