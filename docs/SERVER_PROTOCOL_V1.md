# Agent Bridge — Server protocol 1.0

Status: agreed implementation contract for this repository. Base prefix `/api/v1`.
JSON is UTF-8; file bodies are raw bytes. Client initiates every connection; no inbound
port on the Windows workstation is required. Reports use polling, not server push.

## 1. Authentication and identity

Every endpoint below requires:

```http
Authorization: Bearer <opaque API key>
X-Client-ID: <persistent workstation ID>
```

Production server MUST bind credentials to allowed client IDs and reject other IDs with 403.
Never trust a supplied client ID merely because a shared token is valid.
The reference server only demonstrates a single shared test key; implement binding in production.
Use HTTPS outside localhost. Do not redirect authenticated API requests to another host.
Provision credentials outside this API; user enters them in client settings.

## 2. Handshake — required before each sync cycle

`GET /api/v1/handshake`

200:
```json
{
  "protocol_version": "1.0",
  "status": "ready",
  "client_id": "workstation-123",
  "capabilities": ["files.upload", "emails.upsert", "reports.download"]
}
```

`client_id` MUST match the authenticated requested client. `ready` means the client may
use advertised capabilities, including storage/database availability. This endpoint must be
lightweight: do not enumerate or hash reports. Client requires files.upload/reports.download;
emails.upsert is required only if Outlook is enabled. Client 1.0 requires exact version `1.0`.
Unavailable service: 503. Invalid/expired key: 401. Disallowed client: 403.
Client in this repo performs this gate before reading Outlook or source folders.
Mid-cycle connection loss still needs improved fail-fast handling (see operations review).

## 3. Original file upload

`PUT /api/v1/sync/files?source_id=<id>&relative_path=<url-encoded-path>&sha256=<hex>`

Headers: auth headers, `Content-Type: application/octet-stream`, `Content-Length`.
Body: original file bytes, streamed. Not multipart and not parsed spreadsheet JSON.

Example:
```http
PUT /api/v1/sync/files?source_id=abc123&relative_path=weekly%2Freport.xlsx&sha256=<64-lowercase-hex>
```

200 after durable commit:
```json
{"status":"success","sha256":"<64-lowercase-hex>"}
```

- Logical identity: `(authenticated client_id, source_id, relative_path)`.
- Current client source_id = first 24 hexadecimal characters of SHA256 of
  resolved local root path casefolded and UTF-8 encoded. Server treats it as opaque.
- relative_path uses `/`, is relative to that root, and preserves Unicode names.
- Reject absolute/traversal paths, unsafe platform names and paths escaping the client source.
  Never use a client-provided absolute filesystem path as a server path.
- Write to staging, compute SHA-256 while receiving, verify, then atomically publish.
- Checksum mismatch: 422. Failed/disconnected upload must not replace a committed version.
- Retrying identical path/hash MUST be idempotent. New hash replaces current content;
  backend may additionally retain version history.
- Zero-byte files are valid (SHA256 of empty bytes).
- No deletion, rename propagation, empty-directory sync, multipart chunk resume or compression
  protocol in v1. A rename currently appears as a new file; old server file remains.
- A server may enforce a documented upload size limit (413); current client cannot negotiate it.
- Preserve source namespaces; identical relative paths from different sources must not collide.

Known client limitation: local dedup state is keyed by local absolute path, not full remote identity.
Editing/reordering overlapping roots needs the planned source-state migration. Do not assume
full filesystem mirroring or that a local delete means the server should delete.

## 4. Emails: idempotent batch upsert

`POST /api/v1/sync/emails` with JSON:

```json
{
  "client_id": "workstation-123",
  "count": 1,
  "emails": [{
    "entry_id": "outlook-entry-id",
    "subject": "Weekly progress",
    "sender_name": "Project Team",
    "sender_email": "team@example.com",
    "received_time": "2026-10-04T09:30:00",
    "body_text": "Full plain-text message",
    "attachments": [{"index":1,"filename":"report.xlsx","size":1234}],
    "importance": 1,
    "conversation_topic": "Weekly progress"
  }]
}
```

200:
```json
{"status":"success","received":1}
```

- Body client_id MUST match authenticated identity; count must equal batch length.
- Upsert key `(client_id, entry_id)`. Commit entire batch before 2xx; client marks the
  entire batch synced on success. Do not return partial success as 200.
- Attachments are metadata only; no binary attachment upload is implemented.
- Current client emits local naive ISO timestamps: DO NOT interpret as UTC. Preserve raw value;
  timezone-aware timestamps require a later client update.
- sender_email can be an Exchange internal address, not always SMTP.
- Current source is default Inbox, newest first, at most 500 new records per cycle within
  configured lookback. EntryID is not a global immutable email identifier.

## 5. Pending reports

`GET /api/v1/reports/pending?client_id=<client_id>`

200 (including empty queue):
```json
{"reports":[{"id":"opaque-immutable-report-version","filename":"Progress.xlsx","sha256":"<64-hex>"}]}
```

- Only return reports assigned to this authenticated client and not acknowledged by it.
- Reports MUST be fully published before appearing. Never expose work-in-progress files.
- IDs are globally unique immutable version IDs. Changed bytes require a new ID.
- Filename is a basename (no directories); use Windows-compatible document names.
- sha256 is required by this contract; client verifies it when supplied.
- There is no pagination support in current client: return all pending records.
- Do not expose server-local filesystem paths.

## 6. Download

`GET /api/v1/reports/download/{report_id}`

200: raw bytes, suitable Content-Type, Content-Length if known;
Content-Disposition with a safe filename is recommended. Client uses pending-list filename.
404 for unknown/unassigned reports. Server MUST enforce ownership at download time.
Repeated downloads return identical bytes for an ID. Keep content available until receipt is
confirmed; a retention policy after acknowledgement is a backend choice.
No Range/resume support is required by current client.

## 7. Receipt acknowledgement

`POST /api/v1/reports/confirm`
```json
{"report_id":"opaque-immutable-report-version","client_id":"workstation-123"}
```
200:
```json
{"status":"confirmed"}
```

Idempotent per `(client_id, report_id)`. Repeat confirmation returns success.
Acknowledgement means bytes were saved locally, not that a human read the document.
Client saves file and local state before confirming. If confirmation fails, report remains pending;
next poll retries confirmation without downloading again when local file still exists.
Reports already confirmed are not automatically redownloaded after local user deletion.

## 8. Errors and retries

JSON errors:
```json
{"detail":"Readable explanation"}
```
Optional `code` and `request_id` may be added without breaking client.

| HTTP | Meaning | Intended client policy |
|---|---|---|
| 400 / 422 | Invalid fields/path/checksum | Fail item, surface reason |
| 401 | Missing/invalid key | Stop; user fixes credential |
| 403 | Client or resource not allowed | Stop; user fixes permission |
| 404 | Unknown report/resource | Surface item error; reconcile next poll |
| 409 | Temporary version/conflict | Retry after reconciliation |
| 413 | File too large | Fail item; do not retry unchanged forever |
| 429 | Rate limited | Respect Retry-After |
| 500 / 502 / 503 / 504 | Server unavailable | Bounded backoff |

These retry policies are TARGET BEHAVIOR: current client records errors and retries in later cycles;
it does not yet implement Retry-After, exponential backoff or full error classification.
Do not rely on request timeouts for exactly-once delivery. Every mutation above must be idempotent.

## 9. Compatibility rules

Server may add optional response fields; existing names/types and semantics must remain stable.
Breaking changes need a new protocol version and coordinated client support. Do not remove existing
endpoints or replace raw uploads with multipart without a version change.

Health/preflight and receipt acknowledgements must not run document analysis synchronously.
Schedule parsing/AI jobs only after durable upload commit. Analysis failure does not invalidate
receipt of file bytes. Report production is a separate backend pipeline.
