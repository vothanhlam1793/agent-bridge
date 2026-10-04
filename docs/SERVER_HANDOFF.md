# Server team handoff

## Goal

Receive original project files and email text from Windows agents. Backend owns extraction,
indexing, project mapping, progress analysis and report generation. Agent owns local collection,
upload, settings and report download. No server-to-PC inbound connection required.

## Implementation order

1. Implement auth-bound client identity and `/api/v1/handshake` exactly as protocol 1.0.
2. Implement streaming file ingestion with atomic commit/hash verification and client/source isolation.
3. Implement transactionally idempotent email batch ingestion.
4. Maintain report metadata (ID, client, filename, checksum, size, published status, receipt).
5. Serve pending/download/confirm with per-client authorization and idempotent receipts.
6. Feed committed versions into asynchronous processing; publish immutable report versions.

Suggested backend tables: clients, credentials, sources, file_versions, emails,
analysis_jobs, reports, report_receipts. Source display names are not supplied in v1;
present source_id or configure labels in backend until a later source registry endpoint.

## Acceptance scenarios

- Wrong key -> 401; valid key for another client -> 403; no data leakage.
- Handshake is fast and does not hash a report directory.
- Nested Unicode filenames, zero-byte files, large streamed files preserve exact bytes.
- Same upload twice -> one current logical version; changed hash -> new current content.
- Broken stream/checksum mismatch leaves previous committed version intact.
- Relative traversal and cross-client access are rejected.
- Identical email batch replay creates no duplicates; failed batch commits none.
- Empty pending queue returns 200 with reports: [].
- Download version bytes match advertised SHA-256; changed content gets a new report ID.
- Interrupted download leaves report pending; duplicate confirmation succeeds.
- Confirmation timeout after server commit is safe to replay.

## Known agent limitations to account for

The coordinator redesign is pending (see OPERATIONS_REVIEW.md). Current agent has a preflight
gate but not hard cancellation, robust backoff, transfer resume, remote delete or folder mirroring.
Outlook is optional; only default Inbox and attachment metadata are collected.
Read and respect the timestamp caveat in the protocol.
Client settings contain a plaintext API key in local SQLite for now.

## Running the example

Set BRIDGE_API_KEY and PM_HUB_DATA_DIR, then run the reference ASGI server.
Uploads appear under `<data>/<sha256(client_id)[:24]>/uploads/<source_id>/<relative_path>`.
For a local end-to-end report test, write a document into that client's `reports/` directory,
using a .part filename until writing finishes, then atomically rename.
The reference server's directory hashing is for demonstration; production should use metadata
stored at report publication time, not rescan and hash files on each poll.
The example also serves reports directly from that directory: keep each published file
unchanged and retained until confirmed. It does not enforce immutable version storage;
the production backend must enforce this to avoid a publish/download race.

## Handoff deliverables from server team

- HTTPS base URL, provisioned client ID and credential through an appropriate private channel.
- Protocol 1.0 handshake and all documented endpoints.
- Published upload limits, retention policy and error/request-ID logging.
- Staging environment and results for the acceptance scenarios above.

Canonical contract: [SERVER_PROTOCOL_V1.md](SERVER_PROTOCOL_V1.md).
