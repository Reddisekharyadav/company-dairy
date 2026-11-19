# WorkSense AI — Production Readiness Audit

Date: **2026-11-06/07; extended 2026-11-17 through 2026-11-19**  
Repository: `D:\project\companydairy`  
Audit scope: database safety, local security, filesystem watcher, stability,
reporting, privacy/retention, and Windows packaging. No commit or push was made.

## Release decision

**Controlled local pilot: GO. Unrestricted production distribution: NO-GO until the
manual validation items in section 6 are completed.**

The automated gates and the live dashboard review pass. Tray-menu Exit, retention
backup/restore, report inspection in Word/PDF, and sleep/resume watcher recovery
have since been executed and recorded below. The release is still not ready for
broad distribution because kernel event-buffer overflow, real editor attribution,
a clean Windows VM matrix, code signing, real SMTP/OCR/AI failure modes,
SmartScreen reputation, and a longer soak remain open.

## Artifact and environment

| Item | Value |
|---|---|
| Packaged artifact | `dist\WorkSense.exe` |
| Size | 62,803,622 bytes |
| SHA-256 | `7D74537B16FE1444F9C907F692EAA1E74FA66496D15A73C44ACB58556483A891` |
| Build time | 2026-11-17 20:19 +05:30 |
| Python used to build | 3.14.2 |
| PyInstaller | 6.21.0 |
| Test environment | Windows 11 10.0.26340, project `venv` |
| Database default | `%APPDATA%\WorkSense\events.db` |

`pip check` now reports **No broken requirements found** after aligning the
optional ML stack (`sentence-transformers 6.1.0`, `transformers 5.22.2`,
`huggingface-hub 1.33.0`, `tokenizers 0.23.3`). The packaged executable
intentionally excludes `torch`/`transformers`; packaged semantic search therefore
uses the documented fallback rather than a neural model.

## Automated tests executed

### Full pytest suite

Command:

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Result: **71 passed, 155 warnings, 75.41s**.

`pytest.ini` restricts collection to `tests/` so operational scripts such as
`scripts/soak_test.py` and `scripts/test_packaged.py` are not imported during a
plain `pytest` run. This prevents collection-time changes to `WS_DB_PATH` from
breaking test isolation.

The suite covers:

- temporary-database isolation for every test and an assertion that tests did not
  touch the production database (`tests/conftest.py`, `tests/test_isolation.py`);
- local-only API host/origin/referer/fetch-site protection, localhost CORS, disabled
  docs by default, generic error responses, and screenshot-root restrictions
  (`tests/test_local_security.py`);
- workspace migration idempotency, legacy-schema upgrade, pre-upgrade backup
  contents, failed-migration restore, and non-destructive backfill
  (`tests/test_migrations.py`);
- WAL concurrency with six writers/150 rows, integrity check, backup during writes,
  and restore (`tests/test_database_concurrency.py`);
- watcher root handling, ignored-parent edge case, duplicate suppression, restart,
  deleted root, and read-only files (`tests/test_fs_watcher.py`);
- sensitive-app exclusion and text redaction (`tests/test_privacy.py`);
- retention cleanup and approved-root screenshot deletion (`tests/test_retention.py`);
- PDF/DOCX/Markdown/XLSX rendering, Unicode PDFs, empty reports, midnight-crossing
  ranges, workspace isolation, unique filenames, Excel filtering/freeze panes, and
  formula-injection escaping (`tests/test_report_renderers.py`);
- privacy/search/FTS5/model integration tests, rewritten to use real assertions and
  pytest temporary directories (`tests/test_v4_features.py`);
- research-session endpoints tolerate malformed legacy JSON in text columns
  (`tests/test_all_functionalities.py`).

### Real Windows watcher validation

Command:

```powershell
.\venv\Scripts\python.exe scripts\validate_watcher.py
```

Result: `SUCCESS=True`. Latest artifact:
`test-artifacts\watcher-validation-20261107-204417.json`.

All 20 checks passed, including editor create/modify, terminal write, background
process write, rename, delete, duplicate suppression, a 45-file burst with at least
30 rows observed, restart recovery, offline changes not claimed, ten repeated
restarts, deleted-root reporting, safe failed restart, read-only survival, clean
shutdown, zero write errors, and zero exact duplicate rows.

A separate minimal watchdog stress test performed **40 native observer
start/stop cycles with the watched root deleted each cycle** and exited 0 in 44
seconds on the final dependency set.

### Soak test

Command:

```powershell
.\venv\Scripts\python.exe scripts\soak_test.py --duration 60 --interval 2
```

Result: `success=true`, artifact `test-artifacts\soak-20261107-204458.json`.

| Measurement | Result |
|---|---|
| Duration | 60.13 s |
| Rows recorded | 117 |
| Max RSS | 63.37 MB |
| Average CPU | 4.91% |
| Maximum threads | 11 |
| Database growth | +73,728 bytes |
| Exact duplicate groups | 0 |
| Watcher write errors | 0 |
| Thread-leak check | Passed |
| Dedupe-cache bound | Passed |
| Restart recovery | Passed |
| Offline changes not claimed | Passed |

This is a controlled 60-second soak, not a 24–72 hour production soak.

### Packaged clean-install test

Command:

```powershell
.\scripts\clean_install_test.ps1 -SkipBuild
```

Result: **18/18 checks passed**, `success=true`, artifact
`test-artifacts\packaged-20261107-204327.json`.

The test uses a temporary `APPDATA`, database, export directory, and project root.
It verifies:

- no `.env` is bundled in the executable;
- first launch with no database starts, serves the API, creates settings, creates
  the database, and applies migrations;
- the packaged watcher records a real event;
- Markdown, PDF, and DOCX reports and an Excel export are produced;
- settings persist across shutdown/restart;
- the existing database and config are preserved on the second launch;
- the second launch reports no pending migration;
- shutdown leaves no running packaged process.

### Live dashboard UI QA

The packaged app was launched headless with an isolated temporary database and
synthetic company/development records, then driven in Chromium at
`http://127.0.0.1:8765/`. Result: **17/17 checks passed**, 0 console errors, 0
failed requests. Evidence: `test-artifacts/live-dashboard/live-dashboard-check.json`
and the screenshots in `test-artifacts/live-dashboard/`.

The run verified:

- the consent banner now contains a readable privacy message and Enable/Dismiss
  actions (the previous empty purple bar was fixed);
- company scope rendered company activity, the supplier-reconciliation research
  session, and the Weekly Operations Sync meeting;
- development scope rendered editor activity, observed filesystem changes, and
  the SQLite research session;
- company and development reports generated through the UI, with Markdown/PDF/DOCX
  controls visible and no cross-scope report text;
- settings/privacy sections and the email/AI settings modal opened and closed;
- a direct request against the packaged app after writing malformed legacy
  `research_sessions.sources` text returned HTTP 200 and an empty `sources` list,
  confirming the defensive JSON parsing in the shipped executable.

Two non-blocking observations were recorded: the handoff card is intentionally a
combined session summary (it can show development files while Company is
selected), and the Sites Visited KPI counts distinct browser-history domains while
the Websites & Apps table also includes application events.

### Dashboard UI restoration (2026-11-17)

A later UI regression removed the navigation buttons for panels that still
existed in the template: **Timeline (Recall)**, **Browser History**, **Notes**,
and **Memory Search**. The panels were unreachable even though their HTML and
JavaScript were present. The buttons were restored, the navigation now wraps to
show all 12 tabs, and the Timeline panel clears its viewer when the selected
workspace/period has no frames instead of leaving a stale screenshot visible.

Verification was run against an isolated database with synthetic company and
development data, first from source and then from the rebuilt packaged artifact:

| Check | Result | Evidence |
|---|---|---|
| All 12 tabs open the matching panel | **PASS** — 12/12 | `test-artifacts/ui-restore/check_ui_focus.mjs` |
| Timeline (Company + All time) | **PASS** — 3 frames, 1280 px image, 3 thumbnails, slider 1/3 | `test-artifacts/ui-restore/timeline-company-all.png` |
| Timeline (Development, no frames) | **PASS** — clear empty state, viewer hidden, no stale image | `test-artifacts/ui-restore/timeline-empty-state.png` |
| Browser History / Notes / Memory Search | **PASS** — 2 history rows, 2 top sites, 1 note, 2 memory matches | `browser-history.png`, `notes.png`, `memory-search.png` |
| File Changes / Reports / Settings | **PASS** — observed event, charts, report controls, privacy controls | `file-changes.png`, browser check output |
| Console errors / failed requests | **PASS** - 0 / 0 | `test-artifacts/ui-restore/ui-restore-browser-output.txt` |
| Full pytest suite after the change | **PASS** - 84 passed, 155 warnings, 57.89 s (latest run) | terminal output |
| Packaged artifact | `dist\WorkSense.exe`, 62,803,622 bytes, SHA-256 `7D74537B16FE1444F9C907F692EAA1E74FA66496D15A73C44ACB58556483A891` | `Get-FileHash` |
| Code signing | **BLOCKED — NotSigned** | `Get-AuthenticodeSignature` |

The dashboard now serves Chart.js, Inter, JetBrains Mono, and a generic site icon
from local `backend/static/` assets instead of Google Fonts, jsDelivr, and Google
favicon services. The packaged build was rebuilt and re-checked after that
change. This closes the UI regression only; it does not close the manual
validation blockers or the code-signing blocker. No commit or push was made.

### Live capture and activity feed visibility (2026-11-17)

A real capture test found that screenshots and feed events were being recorded
but were invisible on the default Overview. The Overview filter started on
**Company**, while conservative classification placed browser and terminal
frames in **unclassified** or **personal**. The screenshot grid also refreshed
only on load/consent and used lazy image loading, so a newly captured frame could
remain absent until a reload or a scroll. Separately, the capture worker committed
the frame only after generating an embedding; the first sentence-transformers
load could therefore delay a visible frame by more than a minute.

The fixes are deliberately local to the dashboard and capture path:

- Overview starts on **All workspaces** and labels the scope explicitly. Reports
  still default to **Company** unless the user explicitly changes the filter;
  opening Reports from a fresh Overview does not create a cross-workspace report.
- Empty screenshot/feed states name the current scope and offer **Show all
  workspaces**. The Reports tab also offers that button and updates its scope
  note before generating.
- The Overview refreshes the feed and screenshot grid every 10 seconds. The grid
  only re-renders when the frame signature changes, and images use their immutable
  IDs without a cache-busting timestamp or lazy-loading gate.
- A captured frame is committed before embedding generation. The embedding model
  now loads in a background thread; until it is ready, search falls back to FTS5.
  The packaged build has no `sentence-transformers` and uses `SimpleEmbedding`.
- Each capture worker start gets its own stop event, so a quick Disable/Enable
  cannot revive an old worker or leave no worker running.

| Check | Result | Evidence |
|---|---|---|
| Source run: first frame saved before embedding | **PASS** — frame saved 1 s after grant; model-load log follows | `test-artifacts/ui-restore/capture-refresh-evidence.json` |
| Source run: non-sensitive test frame | **PASS** — OCR contained `WorkSense Capture Test CAPTURE-2026-11-17 Non-sensitive test pattern` | same evidence file |
| Source run: Overview visibility/refresh | **PASS** — 4 screenshots, 6 feed events, grid updated from 2 to 3 without reload | same evidence file |
| Source run: Reports default | **PASS** — Company default; Show all workspaces switched to All | same evidence file |
| Packaged run: consent Enable/Disable via UI | **PASS** — `true` then `false` | `packaged-capture-refresh-evidence.json` |
| Packaged run: first frame before embedding | **PASS** — 1.5 s after grant; `SimpleEmbedding` fallback logged | same evidence file |
| Packaged run: test frame | **PASS** — OCR marker matched; frame had an embedding; workspace `unclassified` | same evidence file |
| Packaged run: Overview visibility/refresh | **PASS** — 2 screenshots, 4 feed events, images 1920x1080, second frame appeared without reload | `packaged-capture-browser-output.txt` |
| Packaged run: Reports default / Show all | **PASS** — Company default, then All with 2 screenshots | same output |
| Packaged run: Timeline | **PASS** — 2 frames, slider 1/2, empty state hidden | same output |
| Packaged run: all tabs | **PASS** — 12/12 panels visible | same output |
| Packaged run: browser health | **PASS** — 0 console errors, 0 failed requests | same output |
| Full pytest after capture changes | **PASS** — 84 passed, 155 warnings, 57.89 s | terminal output |

The isolated capture sessions contained a mix of a controlled test pattern and
other desktop activity. Their databases and screenshots were deleted after the
run; only sanitized counts, log ordering, and the non-sensitive test marker were
retained. The capture/feed fix is independent of the tray Exit, retention, report,
and sleep/resume validations recorded in later sections.

### Report export and Office/PDF inspection (2026-11-18)

Environment: packaged `dist/WorkSense.exe` (SHA-256
`7D74537B16FE1444F9C907F692EAA1E74FA66496D15A73C44ACB58556483A891`), isolated
APPDATA/database/export directory, synthetic company, development, and personal
records, generated through actual Reports-tab clicks. Exports were deleted after
inspection; the sanitized findings are in
`test-artifacts/report-validation/report-inspection.json`.

| Check | Result | Evidence |
|---|---|---|
| Company report via Reports tab | **PASS** — Markdown, PDF, and DOCX written; download controls appeared | `report-inspection.json` |
| Personal report (empty scope) | **PASS** — zero metrics rendered without an error; MD/PDF/DOCX generated | same file |
| Development isolation | **PASS** — no company markers in any development MD/PDF/DOCX | same file |
| Company DOCX Unicode in Microsoft Word | **PASS** — `Résumé`, `日本語`, `Ελληνικά`, `العربية`, `🚀` all preserved; Word reports 2 pages / 138 words and renders the meetings row correctly | `word-company-unicode-table.png`, `word-company-unicode-page2.png` |
| Company PDF Unicode | **PASS (fixed)** — `Résumé`, `日本語`, `Ελληνικά`, and shaped Arabic render correctly; emoji is rendered as a readable `[rocket]` label; no NUL bytes or boxes | `pdf-unicode-fixed.png`, `pdf-unicode-fixed.json` |
| Report filename collisions | **PASS** — timestamp + content hash suffix observed on every export | export names in `report-inspection.json` |

The PDF renderer now selects per-script TrueType fonts: Segoe UI/Arial for Latin,
Greek, Cyrillic, and Arabic; Microsoft YaHei/SimSun for Han and kana; Malgun Gothic
for Hangul. Arabic is reshaped and bidi-ordered with `arabic-reshaper` and
`python-bidi`. ReportLab's TrueType subsetter cannot embed non-BMP emoji, so emoji
is rendered as a readable label such as `[rocket]` instead of a box. The fix is
covered by `tests/test_report_renderers.py`; evidence is
`test-artifacts/report-validation/pdf-unicode-fixed.png`.

### Sleep/resume watcher recovery (2026-11-19)

Environment: packaged `dist/WorkSense.exe`, isolated APPDATA/database/watch root,
`WS_WATCHER=1`, AC power. The harness wrote a file, suspended the machine, wrote a
second file after resume, and compared the recorded rows with the watcher status.
Evidence: `test-artifacts/sleep-resume/sleep-resume-evidence.json`.

| Check | Result | Evidence |
|---|---|---|
| Real suspend/resume | **PASS** — Windows logged Kernel-Power 42/107 and Power-Troubleshooter; the harness was suspended for 45.8 s | `sleep-resume-evidence.json` |
| Pre-sleep event | **PASS** — `pre_sleep.md` recorded exactly once | same file |
| Post-resume event | **PASS** — `post_resume.md` recorded exactly once; `running: true`, no watcher errors | same file |
| Duplicate suppression | **PASS** — one row per file; `events_seen: 4`, `events_recorded: 2`, `events_deduplicated: 0` | same file |
| Wake timer isolation | **PARTIAL** — the system resumed after 45.8 s, before the 120 s timer; another wake source resumed it, so this proves resume recovery but not timer-driven wake | Windows event log in the same file |

The watcher recovered automatically after resume; no manual restart was needed.
The test ran on AC because Windows disables wake timers on battery. Network and
disk reconnect after resume were not separately exercised.

### Dependency and packaging hygiene

- `watchdog` is now pinned to `7.1.0` in `requirements.txt`.
- PyInstaller archive check found no bundled `.env`.
- `git ls-files` found no tracked executable, database, WAL/SHM file, `.env`,
  backup, export, or test artifact.
- `WorkSense-Windows.exe` was removed from the Git index with `git rm --cached`
  and is now ignored by `*.exe`. It still exists in historical commits until the
  history is rewritten.

## High-risk findings fixed

1. **Watcher process crash on restart/deleted root.** `watchdog==3.0.0` on
   CPython 3.14/Windows intermittently aborted the interpreter with
   `_PySemaphore_Wakeup: parking_lot: ReleaseSemaphore failed` when stopping the
   observer, especially after a watched root disappeared. This was reproduced with
   a minimal watchdog script on both 3.14.2 and 3.14.10. Upgrading to
   `watchdog 7.1.0` eliminated the crash; the Windows emitter drain timeout is set
   to 0.1 s so continuous write streams do not delay events indefinitely.
2. **Unsafe schema upgrades.** Migrations now create a verified pre-upgrade backup,
   detect pending work, add columns idempotently, preserve non-empty workspaces, and
   restore the backup if an upgrade fails. Restore validates integrity and cleans
   stale WAL/SHM files.
3. **LAN/cross-site exposure of the local API.** The server binds only
   `127.0.0.1`; a host/origin/referer/fetch-site guard rejects foreign hosts;
   CORS is localhost-only; API docs are opt-in; public errors are generic;
   screenshot access/deletion is limited to approved roots.
4. **Watcher root/ignore and cache issues.** Ignored directory names now apply only
   below a configured root, so a project under a parent named `temp` is watched.
   Dedupe state is bounded by a debounce-relative expiry, and deleted roots fail
   safely without claiming offline work.
5. **Report/export risks.** Excel formula-injection is escaped; filters and frozen
   headers are present; report filenames are collision-safe; PDF Unicode handling
   now uses per-script embedded TrueType fonts, Arabic reshaping/bidi, and a
   readable emoji label instead of boxes.
6. **Privacy and retention.** Sensitive applications/titles are excluded from
   capture; file-content/diff collection is not a default; retention removes
   expired rows and only screenshot files inside approved roots; privacy deletion
   reports truthful counts without leaking paths.
7. **Release hygiene.** Generated executables/databases/backups/exports are
   ignored, and the release checklist explicitly covers secrets, clean install,
   migration backup/restore, manual environment validation, and GO/NO-GO criteria.
8. **Dashboard resilience/UI defects.** Malformed legacy JSON in the
   research-session sources or screen-note context columns is now parsed
   defensively instead of returning HTTP 500 for the whole tab. The empty consent
   banner that rendered as a blank purple bar was replaced with a readable privacy
   message and Enable/Dismiss actions.

## Manual validation executed locally (2026-11-07)

Environment: Windows 11 `10.0.26340`, Python 3.14.2, packaged
`dist/WorkSense.exe` (SHA-256
`8B138E00479146B08EB6AF114569CDA617C689AF8EF13EEBB39FB83EA9298765`).
The production database was copied through SQLite's backup API and never
modified. Artifacts are under `test-artifacts/manual-gui/`,
`test-artifacts/manual-production-copy/`, and
`test-artifacts/manual-legacy-upgrade/`.

| Check | Result | Evidence |
|---|---|---|
| First-launch consent dialog | PASS | Dialog shown; `Yes` accepted; `screen_consent.txt` contains `granted`. |
| Floating status widget and menu | PASS | Recording widget and Pause/Kill/report menu rendered on the desktop. |
| Pause / resume tracking | PASS | Log recorded `Tracking paused by user` and `Tracking resumed by user`; UI changed to Paused/Resume. |
| Kill / graceful shutdown | PASS | Clicking Kill exited the packaged process tree without an orphan. Tray-menu Exit was exercised separately on 2026-11-17 and passed (`test-artifacts/tray-exit/`). |
| Live dashboard UI QA | PASS | 17/17 checks passed, 0 console errors, 0 failed requests; company/development scopes, reports, research, meetings, files, settings, and consent banner verified. |
| Loopback-only API and docs default | PASS | `/docs` returned 404; foreign `Host` returned 403; packaged server listened on `127.0.0.1`. |
| Startup against a production DB copy | PASS with policy note | Copy integrity `ok`; production DB size and mtime unchanged. The designed 30-day retention cleanup removed 3,767 old rows from the copy. |
| Realistic legacy-schema upgrade | PASS | Pre-upgrade backup and SHA-256 sidecar created; row counts preserved; workspace columns restored; legacy rows backfilled to `unclassified`; production DB untouched. |
| Windows Defender custom scan | PASS, no detection | `Start-MpScan` completed without a threat result. |
| Code signing | **BLOCKED** | `Get-AuthenticodeSignature` reports `NotSigned`; no code-signing certificate is installed. |

### Retention policy observation

Starting the packaged application against a copy of the real database ran the
configured retention cleanup immediately. On this machine it deleted 3,767
rows older than the 30-day cutoff (2026-10-08), including old OCR, screen-frame,
search, event, insight, and research rows. This is expected behavior for the
current policy, but it is destructive for an existing installation that has
more than 30 days of history. Before upgrading a real installation, the release
owner must either back up the database, raise `retention_days` for the first
run, or explicitly confirm that immediate 30-day retention is intended. The
production database itself was not changed.

## Manual validation still required

Completed since this list was first written: tray-menu Exit, retention
backup/restore, report generation and Word/PDF inspection, and sleep/resume
watcher recovery. The items below were **not** executed and must not be
represented as passing:

- Windows network/disk reconnect (sleep/resume watcher recovery passed on
  2026-11-19 with the packaged app and an isolated root; reconnect itself was not
  separately tested);
- Windows kernel filesystem event-queue overflow and mass-change recovery;
- real Cursor, VS Code, Antigravity, and background-agent attribution. Only
  filesystem activity is claimed; an editor is attributed only when a focused
  window title contains the exact filename;
- real SMTP/corporate email delivery and attachment rendering;
- real OCR/AI service credentials, offline behavior, quota, timeout, and failure
  modes;
- 24–72 hour soak on representative workstation hardware;
- clean-install matrix on Windows 10 and Windows 11 without Python;
- missing-DLL/optional-component repair and degraded-feature messaging;
- antivirus false-positive reputation review, SmartScreen result, and
  code-signing validation (a local Defender custom scan passed, but this is not
  a reputation or SmartScreen result);
- visual inspection of one company/development/personal/unclassified report per
  release, plus an Excel workbook opened in Excel (company/development/personal
  MD/PDF/DOCX were inspected on 2026-11-18; the PDF Unicode fix was re-inspected
  on 2026-11-19; the Excel workbook has not been opened in Excel).


## Residual risks and conditions

- The packaged application applies the retention policy at startup. On a copy of
  the real database this deleted 3,767 rows older than the 30-day cutoff. Confirm
  the policy with the release owner and require a verified backup (or a higher
  first-run retention) before upgrading installations with older history.
- The 60-second soak is not a substitute for a multi-day soak. Continuous,
  high-volume builds can still overflow the OS event buffer; configure watcher
  exclusions for build/output directories and perform OS-level overflow testing.
- The one-file executable excludes `torch`/`transformers`; neural semantic search
  is not available in the packaged build. Users get the fallback. If neural search
  is a release requirement, bundle and test the ML stack separately.
- `watchdog 7.1.0` is a hard minimum for this Python 3.14/Windows build. A clean
  install must not resolve back to `3.0.0`.
- The legacy `WorkSense-Windows.exe` remains in Git history. Purge history before a
  public distribution if the old binary is considered a secret or policy risk.
- The handoff card is intentionally a combined session summary; it can show
  development files while the Company tab is selected. The Sites Visited KPI counts
  distinct browser-history domains, while the Websites & Apps table also includes
  application events. Both are non-blocking UX observations.
- All audit changes remain uncommitted/unpushed. The release tag should only be
  created after review and after the manual checklist is signed off.

## GO/NO-GO

| Scope | Decision |
|---|---|
| Controlled local pilot with automated gates | **GO** |
| General production distribution | **NO-GO** until the manual checklist and code signing are complete |

PDF export now renders CJK, Greek, Cyrillic, and shaped Arabic correctly; emoji is
rendered as a readable text label because ReportLab cannot embed non-BMP color
glyphs.

Validated since the previous audit: tray-menu Exit on the packaged build,
retention backup/restore on a synthetic legacy database, report generation through
the Reports tab with Word and PDF inspection, the PDF Unicode fix, and sleep/resume
watcher recovery. Still open for unrestricted distribution: code signing,
Windows 10/11 clean-VM installs, the 24–72 hour soak, real SMTP/OCR/AI failure
modes, SmartScreen/antivirus reputation, and confirmation of the startup retention
policy before upgrading installations with older history.

Release checklist: `docs\RELEASE_CHECKLIST.md`.

