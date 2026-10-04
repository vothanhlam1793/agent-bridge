# Server-to-Bridge commands (optional protocol 1.0 extension)

Server advertises `commands.queue` in the existing handshake. Older servers without it
remain compatible. Bridge polls after handshake, before normal collection, when Outlook
is enabled. Automatic polling follows the existing sync interval; in manual mode click
Sync Now. Closing/stopping automatic sync prevents future scheduled polling.

## Endpoints

Both require the standard Bearer and X-Client-ID headers. Production credentials MUST
be bound to client identity. Use one active Bridge installation per client ID; this
version does not lease commands across multiple machines sharing an identity.

`GET /api/v1/commands/pending` -> 200:
```json
{"commands":[{"id":"unique-uuid","type":"outlook.draft","payload":{"to":"recipient@example.com","subject":"Progress report","body":"Report text"}}]}
```

Return at most 10 commands in creation order. IDs are immutable, unique per client
and server, at most 128 characters; use UUIDs. The entire returned command object
must remain unchanged on redelivery (no volatile attempt/time fields).

`POST /api/v1/commands/{id}/result` body:
```json
{"status":"succeeded","result":{"outcome":"submitted_to_outlook","draft_entry_id":"..."}}
```
200: `{"status":"acknowledged"}`. Result statuses: succeeded, failed, uncertain.
Record results durably and remove all three statuses from pending. Duplicate identical
results succeed; different results for the same ID -> 409. Unknown/unassigned ID -> 404.

## Commands

| Type | Required payload | Result |
|---|---|---|
| outlook.draft | to, subject, body (strings) | draft_saved + entry_id |
| outlook.send | to, subject, body (strings) | submitted_to_outlook + draft_entry_id |
| outlook.reply | entry_id, body (strings) | submitted_to_outlook, or draft_saved if draft=true |
| outlook.attachment | entry_id (string), index (integer >=1) | filename, size, content_base64 |

Optional fields for draft/send: cc, bcc (Outlook recipient strings, semicolon-separated),
account (SMTP address matching a configured Outlook account), attachments (list of paths
relative to the configured report download directory). Default account is Outlook's default.
Reply supports account, attachments, draft boolean (default false), store_id; recipients
come from Outlook Reply, not ReplyAll. Bodies are plain text. Reply prepends body to the
quoted original. Attachment extraction supports store_id; new collected email records
include store_id for reliable lookup. Entry IDs may stop working if a message is moved.

Download required reports in an earlier cycle before issuing send with attachments;
commands run before the current cycle's report downloads. No arbitrary local file paths,
shell commands, or executable jobs are part of this extension.

Attachment extraction returns binary data encoded as base64 in the result (10 MiB maximum
binary size). Server result endpoint must accept at least 15 MiB JSON. This first version
does not stream attachment results. Outlook reading/upserting continues via existing API.

## Delivery and recovery semantics

Bridge journals each command in SQLite before starting an isolated Outlook process.
Execution deadline is 60 seconds per command. Worker is terminated at timeout; Outlook
itself is not killed. Exceptions, process crashes and deadline expiry are conservatively
`uncertain`, since Outlook may have accepted a side effect already.

After execution, result is saved locally before POST. Network/ack failure replays the
saved result, NEVER Send. A crash with a journal entry but no result returns uncertain
on redelivery, also without re-execution. Reusing an ID with changed content is rejected.
Journal namespace includes server URL and client ID. Keep those stable and retain the
local database: wiping it or moving identity to another installation loses dedup history.

`submitted_to_outlook` means Outlook accepted Send, NOT delivered/read by recipient.
Outlook may queue mail offline. Outbound items are stamped with the custom property
PMBridgeCommandID before Save/Send. For uncertain jobs, inspect Drafts, Outbox and Sent
Items before deciding what to do. Automated reconciliation is not implemented. Do not
mint a new command ID to blindly retry an uncertain send. Exactly-once SMTP delivery
cannot be promised by this design.

## Run the reference producer on the server

Set BRIDGE_API_KEY and PM_HUB_DATA_DIR to match the running reference hub, then:
```sh
python -m pm_bridge.server_hub_demo.enqueue --client workstation-123 --file command.json
```
command.json contains one command object as above. The backend producer uses the hub
database directly; there is no agent-facing command-creation API. Backend jobs can call
`enqueue_command(client_id, command)` instead. Results are in hub.db commands.result.

Client history: `GET http://127.0.0.1:5555/api/commands`; also visible in web Logs.
History hides base64 attachment bytes. This is durable command execution support, not
the full scheduler/UI state-machine redesign described in OPERATIONS_REVIEW.md.
