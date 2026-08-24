# TimeSplit

A background time tracker for Windows that splits your day between two jobs —
in this case the school and the real estate business — without you having to
start or stop anything.

It watches which window you are actually working in, decides which job it
belongs to, and adds the time up. When it gets one wrong you tell it once, and
it remembers. When you walk away from the computer it stops the clock.

Everything stays on your machine. No screenshots are ever taken, and nothing is
uploaded unless you explicitly switch on the optional Claude assist.

---

## Install

You need Python 3.11 or newer. If you do not have it:
[python.org/downloads](https://www.python.org/downloads/) — tick **“Add
python.exe to PATH”** during the install.

Then, from the folder containing this file, double-click:

```
scripts\setup.bat
```

That installs the three things it needs, then walks you through setup: naming
your two jobs, listing the sites and programs you already know belong to each,
and offering to start automatically when you log in. It takes about a minute
and a half.

To do it by hand instead:

```
pip install -r requirements-win.txt
pip install -e .
python -m timesplit wizard
```

## Run it

```
scripts\run_timesplit.bat
```

A small clock icon appears in your system tray. Right-click it for:

- **Open dashboard** — your day, your week, and anything needing a decision
- **Pause tracking** / **Pause for 1 hour** — paused time is recorded as paused,
  so the day still adds up
- **Open data folder** — where everything is stored
- **Quit**

If you said yes to autostart, it will already be running next time you log in
(30 seconds after, so it does not fight with everything else at boot).

---

## How it decides which job something is

In order — the first thing that matches wins:

1. **Sessions you set yourself.** Never overruled by anything.
2. **Private windows.** Password managers and incognito windows are not
   recorded at all. The *time* still counts so your day adds up; only the
   content is dropped.
3. **Your rules.** Anything you add on the Rules page, or that comes from
   choosing “same website” when correcting something.
4. **Claude's suggestions**, if you have turned that on.
5. **The shipped list** — about 130 sites and phrases: Canvas, Blackboard,
   PowerSchool, anything ending in `.edu` for school; Zillow, the MLS systems,
   dotloop, DocuSign, SkySlope, title companies for real estate.
6. **What it has learned from your corrections.** Confident answers are
   assigned; uncertain ones are assigned *and* flagged so you can check them.
7. **Nothing matched** — it says so and puts it in the review queue rather than
   guessing.

### Correcting it

Open the dashboard and click one of your two job names next to anything wrong.
The dropdown next to the buttons decides how far the correction reaches:

| Choice | What happens |
|---|---|
| **just this** | Only this one session changes. |
| **same window title** | Everything with that exact title, past and future. |
| **same website** | Everything on that site. Creates a rule. |
| **same program** | Everything in that program. Creates a rule. |

Anything wider than “just this” becomes a rule and is applied to the last 30
days plus anything still uncategorised — so one click can fix weeks at once.

The **Review** page lists everything undecided, **longest first**. Five clicks
there usually accounts for most of the unassigned time.

### Expect a review queue in the first week

The shipped rules cover a lot, and your setup answers cover more, but the
learned part deliberately says *“I don't know”* rather than guessing until it
has seen about 20 corrections. A confident wrong answer on a timesheet is worse
than an honest gap. It settles down quickly.

---

## When it stops counting

Time is never billed to a job when you are not there:

- **Away from the keyboard** for 3 minutes (adjustable). Importantly, when it
  notices at 3:05 that you have been idle 5 minutes, it ends the session at
  **3:00** — the moment you actually stopped — not when it noticed. Coming back
  works the same way in reverse.
- **Screen locked** or **screensaver running**.
- **Machine asleep.** An overnight suspend becomes one “asleep” entry, never an
  eight-hour work session.
- **Paused** from the tray.

All of it shows on the dashboard as grey, separate from either job.

---

## Getting your hours out

**Export** page, or:

```
python -m timesplit export --from 2026-08-01 --to 2026-08-31
```

You get three files:

- `sessions_*.csv` — every session, line by line
- `daily_totals_*.csv` — hours per job per day
- `invoice_*.csv` — decimal hours, optionally rounded up to 6, 15 or 30
  minutes, with totals — the shape you paste into a timesheet

Add `--format xlsx` for a single Excel workbook with a chart (needs
`pip install openpyxl`).

---

## What it can see, and what it does with it

**Reads:** the name of the program in the foreground, its window title, and —
for Chrome and Perplexity Comet — the address in the address bar.

**Never reads:** screen contents, keystrokes, file contents, other windows,
anything in the background.

**Stores:** all of the above, in a SQLite file in
`%LOCALAPPDATA%\TimeSplit\`. Sessions are kept for two years, idle records for
90 days. A year of normal use is 15–40 MB.

**Sends:** nothing, unless you turn on the Claude assist below.

The dashboard listens only on `127.0.0.1`, is not running most of the time
(it starts when you open it and shuts down ten minutes after you close it),
and requires a token that changes every launch — so a web page you happen to
be visiting cannot read your work history.

To delete everything: quit TimeSplit and delete the `%LOCALAPPDATA%\TimeSplit`
folder.

### The optional Claude assist

**Off by default.** When on, windows that match none of your rules and that the
learned model is unsure about can be sent to the Claude API for a suggestion.

It sends the program name, the website domain, and the window title with email
addresses, long digit strings and file paths stripped out. Not full web
addresses (unless you opt in), not screenshots, not file contents. Repeated
windows are sent once, not every time.

Before it ever sends anything, **Settings → “Show me exactly what would be
sent”** prints the literal payload.

Limits, enforced before any request is made: at most 12 requests a day, an hour
apart, stopping at $2.00 a month. Its suggestions become rules but are flagged
so you can veto them, and **they never train the learned model** — only your own
corrections do that. A model trained on its own upstream guesses would quietly
harden a mistake into a belief.

To enable: set `llm_assist.enabled` to `true` in `config.json` and provide a
key (stored encrypted to your Windows account, never in the config file or
logs).

---

## If something looks wrong

```
python -m timesplit doctor
```

It prints the version, where everything lives, what it has recorded, whether
the logon task is registered, and then samples your foreground window for ten
seconds so you can see exactly what it sees — including whether it can read
each browser's address bar.

| Symptom | Likely cause |
|---|---|
| No tray icon | `pip install pystray Pillow` |
| Web addresses never appear | `pip install uiautomation`, then run `doctor` with a browser focused |
| Stopped working on a laptop | Check the task exists: `schtasks /Query /TN TimeSplit` |
| Didn't start at logon | `python -m timesplit install-autostart`, or put a shortcut to `pythonw -m timesplit run` in `shell:startup` |
| Everything says "Uncategorised" | Normal in week one — work through the Review page |
| Long idle stretches while you were working | Known Windows limitation: it cannot see input to programs running as administrator |

Logs are in `%LOCALAPPDATA%\TimeSplit\timesplit.log`. They record events, never
window titles, unless you turn on `debug.verbose`.

---

## Notes on Perplexity Comet

Comet is Chromium-based, so it uses the same address-bar reading as Chrome, and
the resolver falls back through three strategies if a fork has changed things.
This could not be verified from the machine this was built on — run
`doctor` with Comet focused and it will tell you plainly whether the address
bar is readable. If Comet's executable turns out to be named something other
than `comet.exe`, `doctor` prints the real name and you can correct
`browser.browser_exes` in `config.json`.

Either way, tracking still works: without the address bar it falls back to
window titles, which are still informative. You lose per-site precision, not
the tracking itself.

---

## Settings worth knowing about

`%LOCALAPPDATA%\TimeSplit\config.json`:

| Setting | Default | What it does |
|---|---|---|
| `idle.threshold_s` | 180 | Seconds before you count as away |
| `sampling.interval_s` | 3 | How often it checks the foreground window |
| `browser.browser_exes` | chrome, comet | Which browsers to read addresses from |
| `classifier.min_docs` | 20 | Corrections before the learned model starts predicting |
| `privacy.exclude_exes` | password managers | Programs never recorded |
| `export.rounding_min` | 0 | Round invoice hours to 6/15/30 minutes |
| `retention.sessions_days` | 730 | How long to keep history |

Every value is checked on load, so a typo cannot stop it from starting.

---

## For developers

```bash
pip install -r requirements-dev.txt
pytest                          # the full suite runs on Linux and macOS too
python -m timesplit run --demo  # replay a synthetic workday, then serve the dashboard
```

The codebase splits deliberately: `core/`, `store/`, `web/`, `export/` and
`llm/` are pure Python with no Windows imports and are fully tested on any
platform against a scripted window feed and a fake clock. `backends/win/`
holds thin ctypes adapters that make no decisions. A test reads the source of
the pure packages and fails if a Windows import appears in one.
