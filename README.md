# Agent Bridge

Windows agent for original-file folder uploads, Outlook Classic email collection,
and downloading reports from a central server. Includes local web dashboard and native tray controller.

## Server team: start here

1. [Server integration contract v1](docs/SERVER_PROTOCOL_V1.md) — normative wire contract.
2. [Server handoff checklist](docs/SERVER_HANDOFF.md) — implementation and acceptance criteria.
   [Server-to-Bridge commands](docs/COMMANDS_V1.md) — Outlook draft/send/reply/attachment queue.
   [Calendar synchronization and creation](docs/CALENDAR_V1.md) — Outlook appointments and meetings.
   [Full email archive](docs/EMAIL_ARCHIVE_V1.md) — all accessible stores/folders, MSG and attachments; replaces Inbox-only sync.
3. `pm_bridge/server_hub_demo/server.py` — executable reference implementation, not a production server.
4. [Operations review](pm_bridge/OPERATIONS_REVIEW.md) — known lifecycle/UI limitations.

Protocol version: **1.0**. The client performs an authenticated handshake before reading sources.
The repo remains a prototype: lifecycle cancellation/deadlines and coordinator redesign from
the operations review are not yet implemented. Do not interpret successful packaging as production readiness.

## Run client (Windows, Python 3.11)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r pm_bridge/requirements.txt
.venv\Scripts\python pm_bridge/desktop_app.py
```

Dashboard: http://127.0.0.1:5555. Configure the server, credentials, source folders and report folder there.
Outlook requires Classic Outlook with a signed-in profile; file-only mode is supported.

## Run reference server

```sh
python -m pip install -r requirements-server.txt
# Set BRIDGE_API_KEY to a nonempty test secret; use the same secret in client settings.
python -m uvicorn pm_bridge.server_hub_demo.server:app --host 127.0.0.1 --port 8000
```

Environment: `BRIDGE_API_KEY`, `PM_HUB_DATA_DIR` (server); `PM_BRIDGE_DATA_DIR` (client).
Reference server refuses to start without a key. Real deployment needs HTTPS and credentials bound to clients.

## Tests / build

```powershell
.venv\Scripts\python -m pip install httpx pyinstaller
.venv\Scripts\python -m unittest pm_bridge.test_archive pm_bridge.test_calendar pm_bridge.test_commands pm_bridge.test_regression -v
.venv\Scripts\python pm_bridge/build_exe.py
```

Build outputs a versioned folder under `pm_bridge/releases`; distribute the entire application folder.
No user database, mail, credentials, reports or binaries are committed.

`excel_reader.py` and `server_client.py` are legacy helpers, not the active original-file pipeline.
