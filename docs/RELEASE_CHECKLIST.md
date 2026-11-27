# WorkSense AI — Production Release Checklist

Release owner: ____________________  
Version / build date: ____________________  
Git commit: ____________________  
Artifact SHA-256: ____________________

Companion audit report: `docs\PRODUCTION_READINESS_AUDIT.md`.

This checklist separates **automated tests executed in this repository** from
**manual validation that still requires a physical Windows desktop, installer
media, or code-signing key**. A release is not GO while any required manual item
or any blocking defect remains open.

## Local execution record — 2026-11-27 (CI release + Linux startup fix)

Artifacts: rolling `latest` release built by GitHub Actions from commit
`ad359a2` (workflow `desktop.yml`).

| Asset | Size | SHA-256 |
|---|---|---|
| `WorkSense-Windows.zip` | 60,311,532 B | `f90664848d3007e58fbea0c3013d02bfc061951283cac11f226a52b142913bb0` |
| `WorkSense-Linux.zip` | 81,757,729 B | `7fac6ef61d59709637c3a9e5605c0ee5516054c9d70e7abe9280f402142f6364` |
| `WorkSense-macOS.zip` | 57,747,575 B | `a1cbaf6853f3addbe96f22d6082cc4f525da5bff36c6c0cd5e184debf02c66e4` |

| Gate | Result | Evidence |
|---|---|---|
| Full pytest suite | **PASS** — 93 passed, 155 warnings, 63.82 s | terminal output |
| Windows artifact hash | **PASS** — downloaded asset matches the release digest | `Get-FileHash` |
| Windows executable | 60,818,744 B, FileVersion `3.0.0.0`, `NotSigned` | `Get-AuthenticodeSignature` |
| Windows headless smoke | **PASS** — dashboard HTTP 200 in 3 s, v3.0 + combined view, sqlite-vec loaded, `rules_version` 3 | packaged run, isolated APPDATA |
| Linux glibc floor | **PASS** — max bundled requirement **2.35** (was 2.42) | `objdump -T` on extracted archive |
| Linux headless run (Ubuntu 24.04, glibc 2.39, no `DISPLAY`) | **PASS** — dashboard HTTP 200 in 3 s, sqlite-vec loaded, tray skipped with a warning | WSL2, isolated APPDATA |
| Linux X11 run (WSLg, consent preset) | **PASS** — status widget launched, tray started, input listeners started, process stayed alive | WSL2 + WSLg |
| Release notes checksums | **PASS** — `latest` body lists SHA-256 for all three zips | `gh release view latest` |

The 2026-11-26 Linux report (exit code 1 before the dashboard, `_tkinter`
needing glibc 2.42, tray crash on U+2014) was reproduced on Ubuntu 24.04 and is
fixed in `92411ed`, `bc05ddb` and `ad359a2`; details and residual Linux
limitations are in `docs/PRODUCTION_READINESS_AUDIT.md`.

## Local execution record — 2026-11-25 (AI-tool classification + full E2E)

Artifact: `dist\WorkSense.exe`, 78,598,028 bytes, SHA-256
`B694FAD2261D341FA7D51930A7B1C1F91A8D04B48B7BAECF55BAD8DB0F5BBB9D`,
built 2026-11-25 00:40 with Python 3.14.2 / PyInstaller 6.21.0. The build
includes the AI-tool classification rules (`rules_version` 3: OpenCode,
Antigravity, VS Code, Cursor, Windsurf, Trae, Kiro → development; AI Studio,
ChatGPT, Gemini, Claude, Copilot, Perplexity, DeepSeek, Grok, Mistral, Poe,
Upwork → company).

| Gate | Result | Evidence |
|---|---|---|
| Full pytest suite | **PASS** — 88 passed, 155 warnings, 61.62 s | terminal output |
| `pip check` | **PASS** — no broken requirements | terminal output |
| Real Windows watcher validation | **PASS** — 21/21 checks, `SUCCESS=True` | `test-artifacts/watcher-validation-20261125-002658.json` |
| 60-second soak | **PASS** — 121 rows, max RSS 63.15 MB, avg CPU 3.66%, 11 threads, 0 duplicate groups, 0 watcher errors | `test-artifacts/soak-20261125-002804.json` |
| Packaged clean install (new artifact) | **PASS** — 18/18 checks, `SUCCESS=True` | `test-artifacts/packaged-20261125-004126.json` |
| Source E2E, isolated APPDATA | **PASS** — 61/61 checks | `test-artifacts/e2e-20261125-004234.json` |
| Packaged rules bootstrap | **PASS** — `rules_version=3`, OpenCode/Antigravity/AI Studio/Upwork present, API served | packaged smoke run |
| Excel workbook opened in Excel | **PASS** — Microsoft Excel 16.0 opened the workbook, 8 sheets, `A1 = WorkSense AI - Activity Report` | `test-artifacts/e2e-excel/company.xlsx` |
| Windows Defender custom scan | **PASS** — completed, no threat detection | local Defender |
| Code signing | **BLOCKED — `NotSigned`; no certificate installed** | `Get-AuthenticodeSignature` |

The 61-check source E2E covered: every workspace scope and the combined scope
(combined = company + development, all = sum of scopes); feed isolation;
Markdown/PDF/DOCX reports for company, development, personal and unclassified
with no cross-scope category leakage; structured report event isolation; Excel
export restricted to company rows; briefing, websites, timeline, screenshots,
meetings, research, search, memory search, insights, session context and
settings endpoints; notes round-trip; invalid-scope normalisation; loopback-only
listener; `/docs` disabled; foreign Host and Origin rejected; SQLite integrity
`ok`; no `combined`/`all` values stored in `events`; clean shutdown.

Live classification was also verified against real windows on 2026-11-24:
Antigravity and OpenCode → **development / Coding**; Google AI Studio, Gemini and
Upwork → **company / AI Tools or Productivity**; YouTube → **personal**.

Real SMTP delivery was exercised during the E2E run through the operator's
`.env` configuration (one report email was delivered). The automated
"not configured" path returns HTTP 400 with `status=not_configured` instead of a
500, which is the expected behaviour for clients without mail settings.

Still blocking unrestricted distribution: code signing, Windows 10/11 clean-VM
matrix without Python, 24–72 hour soak, network/disk reconnect, kernel
event-queue overflow, real editor attribution, SmartScreen/AV reputation, and
confirmation of the startup retention policy for installations with more than
30 days of history.

## Local execution record — 2026-11-07

These checks were executed locally on Windows 11 `10.0.26340` with the packaged
`dist/WorkSense.exe` (SHA-256
`8B138E00479146B08EB6AF114569CDA617C689AF8EF13EEBB39FB83EA9298765`). They are
evidence for the release owner, not a substitute for sign-off.

| Gate | Result | Artifact |
|---|---|---|
| Full pytest suite | **71 passed**, 155 warnings, 75.41s | terminal output |
| Packaged clean install / restart | **18/18 checks passed** | `test-artifacts/packaged-20261107-204327.json` |
| Real Windows watcher validation | **20/20 checks passed** | `test-artifacts/watcher-validation-20261107-204417.json` |
| 60-second soak | Passed; max RSS 63.37 MB, avg CPU 4.91%, 0 duplicate groups | `test-artifacts/soak-20261107-204458.json` |
| Live dashboard UI QA | **17/17 checks passed**, 0 console errors, 0 failed requests | `test-artifacts/live-dashboard/live-dashboard-check.json` |
| First-launch consent, widget, pause/resume, Kill | Passed on the packaged GUI | `test-artifacts/manual-gui/` |
| Tray-menu Exit | **PASS** — native menu opened and Exit clicked; process tree stopped, port closed, log recorded a clean exit, events persisted across relaunch | `test-artifacts/tray-exit/tray-exit-evidence.json` |
| Retention backup/restore | **PASS** — synthetic legacy DB, verified preflight and schema-upgrade backups, old rows/files removed, recent rows/files kept, restore matched counts | `test-artifacts/retention-safety/retention-safety-evidence.json` |
| Loopback-only API and docs default | Passed; `/docs` 404, foreign Host 403 | `test-artifacts/manual-gui/gui-check.json` |
| Startup on a production DB copy | Passed; production DB untouched; designed 30-day retention removed old rows from the copy | `test-artifacts/manual-production-copy/production-copy-check.json` |
| Realistic legacy-schema upgrade | Passed; backup + checksum, row preservation, unclassified backfill | `test-artifacts/manual-legacy-upgrade/legacy-upgrade-check.json` |
| Windows Defender custom scan | Completed; no threat result | local Defender |
| Code signing | **BLOCKED — executable is NotSigned and no signing certificate is installed** | `Get-AuthenticodeSignature` |

Retention note: the production-copy startup deleted 3,767 rows older than the
30-day cutoff from the copy. Confirm the retention policy and require a verified
backup (or a higher first-run retention) before upgrading an installation with
older history. The production database itself was not modified.

Still blocking general distribution: code signing, Windows 10/11 clean-VM
matrix, kernel event-overflow testing, 24-72 hour soak, real SMTP/OCR/AI failure
modes, and SmartScreen/antivirus reputation review. Tray Exit, retention
backup/restore, report inspection in Word/PDF (including the PDF Unicode fix), and
sleep/resume watcher recovery are complete. The live dashboard review is
complete. See `docs\PRODUCTION_READINESS_AUDIT.md`.

### Live dashboard QA details

The packaged app was launched headless against an isolated temporary database
with synthetic company and development records, then driven in Chromium:

- 17/17 UI checks passed with **0 console errors** and **0 failed requests**.
- The consent banner now shows a readable privacy message with Enable/Dismiss
  actions; the previous empty purple bar was fixed.
- Company scope showed company activity, the supplier-reconciliation research
  session, and the Weekly Operations Sync meeting.
- Development scope showed the editor activity, observed `backend/app.py` and
  `reports/generator.py` filesystem events, and the SQLite research session.
- Company and development reports generated through the UI; Markdown, PDF, and
  DOCX download controls appeared, and the report text did not cross scopes.
- Settings/privacy sections and the email/AI settings modal opened and closed.
- A direct request against the packaged app after writing malformed legacy
  `research_sessions.sources` text returned HTTP 200 and an empty `sources` list.
- Evidence: `test-artifacts/live-dashboard/live-dashboard-check.json` and
  `test-artifacts/live-dashboard/*.png`.

Two non-blocking observations remain: the handoff card is intentionally a
combined session summary, so it can show development files while the Company
tab is selected; and the Sites Visited KPI counts distinct browser-history
domains while the Websites & Apps table also includes application events.

### Dashboard UI restoration — 2026-11-17

The dashboard panels for **Timeline (Recall)**, **Browser History**, **Notes**,
and **Memory Search** existed in the template but their navigation buttons were
missing after the workspace/report redesign. They are now restored, and the
Timeline panel no longer leaves a stale screenshot visible when the selected
workspace has no frames.

| Check | Result | Evidence |
|---|---|---|
| All 12 navigation buttons visible and each panel opens | **PASS** — 12/12 | `test-artifacts/ui-restore/check_ui_focus.mjs` |
| Timeline with Company + All time | **PASS** — 3 frames, image 1280 px, 3 activity thumbnails, slider 1/3 | `test-artifacts/ui-restore/timeline-company-all.png` |
| Timeline with Development (no frames) | **PASS** — viewer hidden, clear empty state, no stale image | `test-artifacts/ui-restore/timeline-empty-state.png` |
| Browser History | **PASS** — 2 history rows and 2 top sites | `test-artifacts/ui-restore/browser-history.png` |
| Notes | **PASS** — saved note rendered | `test-artifacts/ui-restore/notes.png` |
| Memory Search | **PASS** — query returned 2 matching frames | `test-artifacts/ui-restore/memory-search.png` |
| File Changes and Projects | **PASS** — observed filesystem event and charts | `test-artifacts/ui-restore/file-changes.png` |
| Reports / Settings controls | **PASS** — generate, PDF, DOCX, privacy and retention controls present | browser check output |
| Console errors / failed requests | **PASS** - 0 / 0 | `test-artifacts/ui-restore/ui-restore-browser-output.txt` |
| Full pytest suite | **PASS** - 84 passed, 155 warnings, 57.89 s (latest run) | terminal output |
| Source app and packaged app both checked | **PASS** | `test-artifacts/ui-restore/` |
| Latest packaged artifact | `dist\WorkSense.exe`, 62,803,622 bytes, SHA-256 `7D74537B16FE1444F9C907F692EAA1E74FA66496D15A73C44ACB58556483A891` | `Get-FileHash` |
| Code signing | **BLOCKED — NotSigned** | `Get-AuthenticodeSignature` |

The dashboard now loads Chart.js, Inter, JetBrains Mono, and the generic site
icon from local `backend/static/` assets; it no longer calls Google Fonts,
jsDelivr, or Google favicon services. This keeps the local dashboard usable
offline and removes third-party requests from the browsing-history view.

### Live capture and feed visibility — 2026-11-17

Real captures were being recorded but were not visible on the default Overview.
The Overview filter started on **Company**, while browser and terminal frames
were classified as **unclassified** or **personal**. The screenshot grid also
refreshed only on load/consent and used lazy image loading. The capture worker
committed a frame only after embedding generation, so the first model load could
delay a visible frame by more than a minute.

The dashboard now starts the Overview on **All workspaces**; Reports still
defaults to **Company** unless the filter is explicitly changed. Empty states
name the scope and offer **Show all workspaces**. The Overview refreshes the feed
and screenshots every 10 seconds, images load eagerly by immutable ID, and the
grid only re-renders when the frame set changes. Captured frames are committed
before embedding; the model loads in a background thread with a packaged
`SimpleEmbedding` fallback. Each worker start has its own stop event so a quick
Disable/Enable does not leave a stale or missing worker.

| Check | Result | Evidence |
|---|---|---|
| Source: frame saved before embedding | **PASS** — 1 s after grant; model-load log follows | `test-artifacts/ui-restore/capture-refresh-evidence.json` |
| Source: controlled test frame | **PASS** — OCR marker `WorkSense Capture Test CAPTURE-2026-11-17` | same evidence file |
| Source: Overview / Reports / Timeline | **PASS** — 4 screenshots, 6 feed events; Reports Company default; Show all workspaces works; Timeline 4 frames | same evidence file |
| Packaged: Enable/Disable via UI | **PASS** — `screen_capture_enabled` true, then false | `test-artifacts/ui-restore/packaged-capture-refresh-evidence.json` |
| Packaged: frame saved before embedding | **PASS** — 1.5 s after grant; `SimpleEmbedding` fallback logged | same evidence file |
| Packaged: controlled test frame | **PASS** — OCR marker matched; embedding stored; workspace `unclassified` | same evidence file |
| Packaged: Overview visibility/refresh | **PASS** — 2 screenshots, 4 feed events, 1920x1080 images, second frame appeared without reload | `packaged-capture-browser-output.txt` |
| Packaged: Reports default / Show all | **PASS** — Company default, then All with 2 screenshots | same output |
| Packaged: Timeline | **PASS** — 2 frames, slider 1/2, empty state hidden | same output |
| Packaged: all tabs | **PASS** — 12/12 panels visible | same output |
| Packaged: browser health | **PASS** — 0 console errors, 0 failed requests | same output |
| Full pytest after capture changes | **PASS** — 84 passed, 155 warnings, 57.89 s | terminal output |

The isolated sessions were deleted after verification; only sanitized counts,
log ordering, and the non-sensitive test marker were retained. This fix does not
close the remaining release blockers.

The restoration check does **not** close the remaining release blockers. Tray-menu
Exit, retention backup/restore, report generation/inspection (including the PDF
Unicode fix), and sleep/resume watcher recovery have since been validated (see the
evidence sections below). The 24–72 hour soak, the Windows 10/11 clean-VM matrix,
and code signing are still open. No commit or push was performed.

## 1. Source and secret hygiene

- [ ] `git status --short` reviewed; no unexpected files are staged or committed.
- [ ] No `.env`, `config.json`, database, export, screenshot, backup, or generated
      executable is tracked. Run `git ls-files` and confirm.
- [ ] `.env` is **not** bundled in the PyInstaller archive. `scripts/test_packaged.py`
      checks this automatically (`no_env_bundled`).
- [ ] `WS_EMAIL_PASS`, SMTP passwords, AI API keys, and session secrets are supplied
      only through environment variables or the local AppData config file.
- [ ] Old release binaries are removed from Git tracking and, before a public
      release, purged from Git history. Removing a file from the current index does
      not remove it from existing commits.
- [ ] `WorkSense-Windows.exe` (legacy root binary) is not distributed. It remains in
      historical commits until history is rewritten.
- [ ] `test-artifacts/`, `dist/`, `build/`, `backups/`, `exports/`, screenshots and
      databases are not committed.

## 2. Reproducible test gate (must pass)

Use the project virtual environment on Windows:

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
.\venv\Scripts\python.exe scripts\validate_watcher.py
.\venv\Scripts\python.exe scripts\soak_test.py --duration 60 --interval 2
```

- [ ] Full pytest suite passes (last verified: **93 passed**, 2026-11-27).
- [ ] `pip check` reports no broken requirements.
- [ ] `watchdog==7.1.0` (or a newer verified release) is installed. Do **not**
      ship `watchdog==3.0.0` on CPython 3.14/Windows: its observer shutdown can
      raise `_PySemaphore_Wakeup: parking_lot: ReleaseSemaphore failed` and abort
      the process during a root restart. The test suite and soak exercise this path.
- [ ] Build, test, and package with the same Python minor version. Record
      `python -VV` and `pip freeze` with the release artifact.
- [ ] Tests use an isolated temporary database. `tests/conftest.py` redirects
      `APPDATA`, `WS_DB_PATH`, `WS_BACKUP_DIR`, and `WS_EXPORT`; `tests/test_isolation.py`
      fails if a non-temporary path is used.
- [ ] Real Windows watcher validation passes all checks: editor writes, terminal
      writes, background process, create/modify/delete/rename, multiple roots,
      restart, deleted root, burst, duplicate suppression, read-only file, and no
      write errors.
- [ ] Soak test completes with no exact duplicate rows and no watcher write errors;
      memory, thread count, database growth and dedupe-cache bounds are recorded in
      `test-artifacts/soak-*.json`.
- [ ] The short soak is a controlled local test, **not** a 24-hour production soak.
      A longer soak on representative hardware is recommended before high-volume
      deployment.

## 3. Security and privacy review

- [ ] Server binds only to `127.0.0.1`; no `0.0.0.0` or LAN listener. Verify with
      `Get-NetTCPConnection` while the packaged app is running.
- [ ] Host/Origin/Referer/Sec-Fetch-Site guard rejects foreign hosts and cross-site
      requests (`tests/test_local_security.py`).
- [ ] CORS is localhost-only; no `10.0.2.2`, wildcard, or null origin.
- [ ] API errors return generic messages; detailed diagnostics remain in local logs.
      No traceback HTML is returned.
- [ ] `/docs`, `/redoc`, and `/openapi.json` are disabled unless
      `WS_ENABLE_DOCS=1` is explicitly set for development.
- [ ] Screenshot serving and deletion are restricted to approved roots; path
      traversal is rejected.
- [ ] Sensitive processes/titles (password managers, banking, RDP/credential tools,
      login/private-browsing titles) are excluded from screen capture.
- [ ] Screen capture requires explicit consent; OCR is off by default unless enabled.
- [ ] The filesystem watcher records metadata only. No default file-content or diff
      collection is performed.
- [ ] Config file containing mail settings is written with owner-only permissions
      where the platform supports it.
- [ ] Local database/export/backup directories are inside AppData or another
      user-chosen location and are not served as arbitrary files.

## 4. Database, migration, backup and restore

- [ ] SQLite uses WAL, foreign keys, and a busy timeout.
- [ ] Concurrent writer test passes; `PRAGMA integrity_check` returns `ok`.
- [ ] Schema migrations are idempotent.
- [x] A pre-upgrade backup is created before a workspace schema upgrade, and a
      failed migration restores that backup. Verified on a synthetic legacy
      database copy on 2026-11-17.
- [x] Backup sidecar/checksum is verified; restore checks integrity before replacing
      the target.
- [x] Restore test is performed against a backup copy, never directly on the only
      production database.
- [ ] Production data is backed up before installing this release.
- [ ] Stale `-wal` / `-shm` files are handled by the documented restore procedure.

## 5. Reports and exports

- [x] Markdown, PDF, DOCX, and XLSX render successfully with company/development/
      personal/unclassified scopes. Markdown/PDF/DOCX were generated through the
      Reports tab on 2026-11-18 for company, development, and personal scopes;
      XLSX was covered by the automated suite, not re-opened in Excel.
- [ ] `combined`/`all` remain query scopes only, not stored workspace values.
- [x] Unicode text renders in PDF; fallback is used when an embedded TrueType font
      is unavailable. **PASS (2026-11-19):** CJK, Greek, Cyrillic, and shaped Arabic
      render correctly; emoji is rendered as a readable label such as `[rocket]`
      because ReportLab cannot embed non-BMP color glyphs. Evidence:
      `test-artifacts/report-validation/pdf-unicode-fixed.png`.
- [ ] Long/multi-page content, empty reports, and midnight-crossing periods are
      covered by tests. Empty personal report was generated without error; the
      other two cases are covered by the automated suite.
- [x] Excel export contains filters, frozen header row, and escaped formula-leading
      text (`=`, `+`, `-`, `@`). The generated workbook was opened in Microsoft
      Excel 16.0 on 2026-11-25 (8 sheets, company-only rows).
- [x] Report filenames are collision-safe (generation timestamp + content hash).
- [x] No workspace leakage: a company report does not include development/personal
      records. The live UI check generated company and development reports and
      confirmed no cross-scope text in MD/PDF/DOCX; Word and the rendered PDF were
      inspected on 2026-11-18.
- [ ] Exports are written only to the configured export directory.

## 6. Retention, deletion and storage

- [x] Expired activity, OCR/search/research/insight/note/report/snapshot rows are
      removed according to the retention policy. Verified on a synthetic legacy
      database on 2026-11-17: old rows were deleted and recent rows remained.
- [ ] Screenshot files are deleted only inside approved roots. The retention run
      removed old frames while recent frames remained; outside-root deletion was
      not separately exercised.
- [ ] Storage cap cleanup does not delete the database or unrelated user files.
- [ ] Privacy deletion returns a truthful count and reports skipped/unavailable
      files without leaking paths.
- [ ] Run retention on a test copy and verify the production database still opens.

## 7. Packaged executable and clean install

Build:

```powershell
.\venv\Scripts\python.exe -m PyInstaller worksense_onefile.spec --noconfirm --clean
```

Automated clean-install smoke test:

```powershell
.\scripts\clean_install_test.ps1 -SkipBuild
```

The script uses a temporary APPDATA, database, export directory and project root.
It verifies first launch with no database, schema creation, watcher event capture,
PDF/DOCX/Markdown/Excel generation, settings persistence, process shutdown,
restart with the existing database/config, and no `.env` in the archive.

- [ ] `scripts/test_packaged.py` passes all checks on the release artifact.
- [ ] On a clean Windows VM with no Python/venv, copy only `WorkSense.exe` and run it.
- [ ] First-launch consent dialog appears and is actionable (manual, non-headless).
- [x] Dashboard renders all 12 tabs (including Timeline/Recall, Browser History,
      Notes, and Memory Search), scopes, reports, and settings (live QA passed;
      restored and re-verified 2026-11-17 in `test-artifacts/ui-restore/`).
- [x] Tray icon and tray-menu Exit work (manual). **PASS** on the packaged build:
      the native menu opened and Exit was clicked.
- [x] Pause/resume and Kill work (manual).
- [x] Exit from the tray stops the app without leaving an orphan process. **PASS**:
      process tree terminated, API port closed, clean-exit log line, and events
      persisted across relaunch (`test-artifacts/tray-exit/`).
- [ ] Restart with an existing AppData database preserves settings and events.
- [ ] Missing optional OCR/AI components do not prevent startup; log explains the
      degraded feature.
- [ ] Windows Defender/SmartScreen result recorded. Code signing is expected for
      broad distribution; no certificate is configured in this repository.
- [ ] Uninstall/delete instructions preserve user data only when the user chooses so.

## 8. Manual environment validation still required

These cannot be proven by the automated suite in this environment:

- [x] Suspend/resume (sleep) and watcher recovery after resume. **PASS** on
      2026-11-19 with the packaged app on AC: the watcher stayed running, recorded
      a post-resume event exactly once, and produced no duplicates
      (`test-artifacts/sleep-resume/`). The system resumed before the 120 s wake
      timer (another wake source), so timer-driven wake was not isolated.
- [ ] Network/disk reconnect after resume (not separately tested).
- [ ] Kernel filesystem event-queue overflow / mass-change recovery.
- [ ] Real Cursor, VS Code, Antigravity and background-agent attribution. Only the
      filesystem change is claimed; the editing application is recorded only when a
      focused editor title contains the exact filename.
- [x] Real corporate email/SMTP delivery and attachment rendering. **PASS
      (2026-11-25):** one report email with the PDF attachment was delivered
      through the configured Gmail SMTP during the E2E run. The unconfigured
      path returns HTTP 400 `not_configured`.
- [ ] Real OCR/AI service credentials and failure modes (offline, quota, timeout).
      The no-credential path (`/api/insights`, briefing) returns HTTP 200 without
      a crash; quota/timeout behaviour is not exercised.
- [ ] Long-duration soak (24–72 hours) on a representative workstation.
- [ ] Windows 10 and Windows 11 clean-install matrix.
- [ ] Antivirus false-positive review and code-signing validation.

## 9. Release decision

Blocking (must be fixed before release):

- [ ] No failing automated test.
- [ ] No known data-loss, cross-workspace leakage, path-traversal, secret embedding,
      LAN exposure, or formula-injection defect.
- [ ] No unhandled migration/backup failure on the legacy database copy.

GO only when all blocking items are checked and the manual items required for the
target deployment have an owner and a recorded result. Otherwise record **NO-GO**
with the exact blocker and artifact hash.

Signed off by: ____________________  Date: __________  
Decision: **GO / NO-GO**  
Notes:

