# Full one-way Outlook email archive

This supersedes the legacy Inbox/500-new-mails/lookback/keyword pipeline. With Outlook
enabled, the new client REQUIRES `emails.archive` in handshake. It reports an actionable
error on older hubs rather than quietly falling back to incomplete Inbox collection.

## Scope and scheduling

Enumerates all Stores exposed to the signed-in Outlook profile, recursively enumerates
their folders, and exports every MailItem (Class=43) encountered. No date, keyword or
folder-name exclusions: Inbox, Sent, Archive, Deleted, Junk, Drafts, custom folders,
mounted shared mailboxes/PST stores are included when accessible. Calendar and other
non-mail items are handled by their separate features, not exported as email.

Each cycle scans up to 500 items, at most 100 per folder, rotating the starting folder.
These are work quotas, NOT a history limit. SQLite persists per-folder positions and
acknowledged item versions. Repeated full sweeps catch items moved/reordered while
scanning; Outlook collections are live, not point-in-time snapshots. An item deleted
before capture cannot be recovered. No claim of 100% capture of every intermediate edit.
Offline/not-downloaded/protected/inaccessible items produce errors and retry on later
sweeps, never get marked archived. Non-network item errors do not block other items.
Network failure stops the cycle without advancing the failed item. Local worker budget
240s, process deadline 300s. A killed worker retries unacknowledged work. The original
Outlook process is never killed. Periodic auto sync must remain enabled for catch-up.

## Folder inventory

`POST /api/v1/archive/folders` standard Bearer/X-Client-ID headers:
```json
{"client_id":"pc1","complete":true,"errors":[],"folders":[
 {"id":"opaque-hash","entry_id":"outlook-folder-entry","store_id":"outlook-store",
  "parent_id":null,"name":"Mailbox","path":"\\\\Mailbox","mailbox":"Mailbox name","default_item_type":0}
]}
```
Upsert by (client,id), retain old folders, including those missing in later inventories.
parent_id provides a full tree, including empty and non-mail folders. complete=false
means discovery failed or reached its budget; never infer deletions from this inventory.
200: `{"status":"success","received":1}`.

## Binary data then metadata commit

Upload each original Unicode `.msg` and each extracted attachment using existing raw PUT
`/api/v1/sync/files`, source_id=`mailarchive`, relative_path=SHA256 hex, sha256=same hex.
Server must reserve that namespace for content-addressed immutable blobs. Exact replay is
safe. No application-level attachment size cutoff or base64 expansion; streams to hub.
Temporary disk space must accommodate one original message plus extracted attachments.
MSG includes embedded objects/MAPI properties; if separate attachment extraction fails,
the item is not committed and is retried. Hub size limits still apply and are surfaced.

Then `POST /api/v1/archive/emails`:
```json
{"client_id":"pc1","email":{
 "id":"opaque-message-hash","version":"opaque-version-hash",
 "entry_id":"outlook-id","store_id":"outlook-store","folder_id":"opaque-folder-hash",
 "folder_path":"\\\\Mailbox\\Inbox\\Project A","mailbox":"Mailbox name",
 "modified_at":"2026-10-04T10:00:00+00:00","subject":"Subject",
 "body_text":"Plain text","body_html":"<p>HTML</p>",
 "sender_name":"Name","sender_email":"Exchange or SMTP address",
 "to":"Display recipients","cc":"","bcc":"",
 "received_time":"2026-10-04T09:00:00+00:00","sent_time":"2026-10-04T08:59:00+00:00",
 "conversation_id":"outlook-conversation","conversation_topic":"Subject",
 "categories":"Project A","unread":false,"importance":1,"size":1024,
 "recipients":[{"name":"Person","address":"Exchange or SMTP address","type":1}],
 "original_msg":{"source_id":"mailarchive","relative_path":"sha256","sha256":"sha256","size":1024},
 "attachments":[{"index":1,"filename":"file.pdf","blob":{"source_id":"mailarchive","relative_path":"sha256","sha256":"sha256","size":512}}],
 "warnings":[]
}}
```
Metadata properties unavailable via COM are empty with warnings; original MSG remains the
archive source. Recipients may be Exchange internal addresses. BCC is only available if
present in the local message (typically the sender's copy). Datetimes preserve COM offsets.
Identity hashes store_id + entry_id; version hashes last-modification + folder_id. A move
may change EntryID: retain both records, use conversation/MSG identifiers to correlate,
not Internet Message-ID alone as a uniqueness constraint.

Server checks all blobs exist before atomically retaining the immutable version.
200 receipt: `{"status":"success","id":"opaque-message-hash","version":"opaque-version-hash"}`.
Client marks archived ONLY after matching receipt. Lost receipt -> safe replay. Server
must retain historical versions and never delete archived emails when Outlook deletes them.
No reverse email restore, delete, move or read-state mutations are performed by archive sync.

## Reference server query APIs (same client-bound auth)

- GET `/api/v1/archive/folders` -> folder tree records.
- GET `/api/v1/archive/emails?offset=0&limit=100&folder_id=...` -> emails (all retained
  versions), next_offset or null; limit 1..500. folder_id optional.
- GET `/api/v1/archive/blobs/{sha256}` -> raw original MSG or attachment bytes.

Backend can classify by mailbox, folder tree/path, categories, conversations, participant
metadata and attachment content, then use existing draft/send/reply/calendar commands.
Results include entry_id/store_id needed for Outlook commands; old moved items may no
longer resolve. New directory/GAL discovery is not included in this email archive extension.

## Acceptance

Test nested/empty folders and multiple stores; old/non-keyword mail; more than one batch;
restart during upload and after metadata commit; lost receipts; changed/moved/deleted
items; one inaccessible folder/item; large attachments and embedded objects; cross-client
isolation; byte-exact MSG/attachment retrieval; no Outlook-side deletion/read-state change.
Keep the local `mail_archive.db`; deleting it causes a rescan, with server idempotency.
