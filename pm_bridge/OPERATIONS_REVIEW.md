# Review logic, operations and interaction — 2026-10-04

Scope: desktop_app.py, web_app.py, file_sync.py, outlook_reader.py,
report_downloader.py, server_hub_demo/server.py, templates/index.html,
test_regression.py and previously inspected runtime log.
This is a source review, not a claim that every hang has been reproduced.
No runtime behavior is changed by this document.

## Priority 0: lifecycle and responsiveness

1. web_app.perform_sync_core does not gate collection on authenticated server readiness.
   test_server is separate and only probes reports/pending, not upload capabilities.
2. Desktop.tick performs synchronous SQLite reads on the Tk thread; DB timeout is 30s.
   An exception before the final root.after also stops the periodic UI update.
3. Outlook COM, hydration/file reads and requests lack a cycle-wide deadline/cancellation.
   Requests connect/read timeout is not a total operation deadline.
4. Desktop.quit waits without a deadline; new web sync requests remain accepted while stopping.
5. sync_lock is mutual exclusion, not a state model. No phase, active item, progress,
   heartbeat, cancellation or next scheduled run is exposed.
6. Packaged runtime log contains missing win32timezone errors for Outlook items.
   Per-item exceptions are swallowed, so an unsuccessful read can look like zero new mail.

## Priority 1: consistency and user-visible correctness

- Manual web sync holds a request for the entire cycle; browser fetch has no deadline.
- Desktop and web read settings independently. Web refresh never reloads settings;
  toggling automatic mode on desktop leaves the open web view stale.
- settings save tests lock state but does not hold the same lock across update/reset;
  scheduler/manual sync can start between the check and commit.
- Destination changes delete sync history before saving new settings, in separate transactions.
- Scheduler is a wake/sleep loop; saving any settings can trigger immediate automatic work.
  Manual completion does not reschedule the next automatic cycle. No backoff/jitter.
- Turning automatic mode off is disabled while busy. No cancel-current-run control.
- File upload catches network failures per file and continues reading later files.
- File scanner reads and snapshots unchanged files every cycle. OneDrive hydration and
  large directories can be expensive, with no progress or explicit policy.
- Source ownership depends on configured root order for overlapping folders.
  Deduplication state only uses absolute local path, not destination/source/relative path.
  Changing roots can therefore skip the initial upload under a new source identity.
- Missing root and genuinely empty root are not presented as separate UI conditions.
- Download failure on one report aborts remaining reports. Local completion and remote
  acknowledgement are distinct but not visible; partial successes can be absent from summary.
- Server report list hashes every report; download and confirm repeat that scan.
  Connection checks call this expensive endpoint instead of a lightweight handshake.
- Demo server shares one API key and trusts supplied client ID; not per-client authorization.
- Local web control lacks a session secret; Host/Origin checks alone do not authenticate clients.

## Required state contract

One coordinator owns commands, schedule, immutable status snapshots and worker lifecycle.
Keep dimensions separate:
- lifecycle: STARTING, RUNNING, STOPPING, STOPPED
- schedule: MANUAL, AUTO (next_run_at)
- connection: UNKNOWN, CHECKING, ONLINE, OFFLINE, AUTH_FAILED, INCOMPATIBLE
- job: IDLE, PREFLIGHT, SCANNING, UPLOADING, OUTLOOK, DOWNLOADING, CANCELLING
- last_result: SUCCESS, PARTIAL, FAILED, CANCELLED

Commands: request_sync, cancel_current, enable_auto, disable_auto, save_settings, shutdown.
All web/tray/window actions go through the same command interface.
request_sync returns accepted/job_id promptly; duplicate requests return the existing job.
Status includes phase, item, completed/failed/skipped counts, transferred bytes,
last_progress_at, last_success_at, last_result and next_run_at.
Use indeterminate progress while scanning; percentages only when the denominator is known.

## Execution rules

- Validate settings and authenticated protocol/capabilities before reading sources.
- Offline: defer work with bounded exponential backoff; auth/protocol failure: wait for correction.
- Local file errors may continue; connection loss/auth errors stop subsequent uploads.
- Pause automatic schedule remains available during a job and only affects future runs.
- Cancel current job is distinct; do not report cancellation complete while a worker still runs.
- Isolate potentially uninterruptible Outlook/I/O work in managed subprocesses where needed.
  Enforce operation deadlines and a bounded shutdown policy; preserve committed state.
- UI thread only consumes in-memory snapshots; database/network work is asynchronous.
- Settings and destination/source state transitions are atomic and coordinated with jobs.
- Download reports by immutable version; expose downloaded/pending_ack separately.

## Verification gates before release

Existing tests cover small-file happy path/retry, API rendering and basic validation.
They do not establish responsiveness under failures.

Add deterministic fault tests for offline/auth rejection (zero source reads), COM hang,
DB contention, file hydration/read stall, mid-stream disconnect, checksum mismatch,
ack failure after successful download, overlapping-root edits, concurrent web/tray commands,
cancel while busy, shutdown while busy, restart after interrupted upload and cross-UI updates.
Measure command response latency, UI heartbeat delay, cancellation/shutdown deadline
and duplicate worker count in the packaged executable, not only Python source.

## Delivery order

1. Coordinator/status contract + preflight + logging + packaged dependency fix.
2. Managed workers/deadlines/cancel/shutdown + move DB access off Tk thread.
3. Stable source IDs/state transactions + retry classification + report manifest.
4. Bind both UIs to the same status and command APIs; add progress, errors, pending changes.
5. Fault injection and EXE lifecycle tests; only then publish a new build.
