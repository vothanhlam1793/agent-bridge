# Outlook calendar extension

Two directions: local Outlook calendar -> server, and server command -> local calendar.
No Microsoft credentials are required on the server. Uses the signed-in default Outlook
Classic Calendar. Additional calendars/shared calendars are not included in this version.

## Collection

Server advertises `calendar.upsert` in handshake. Bridge then collects on each sync cycle
when Outlook is enabled: 30 days past through 90 days future. Outlook process has a 60s
deadline. Recurrences are expanded into occurrences inside this bounded window, capped
at 2000 scanned occurrences. Outlook must already have accepted/imported invitations into
Calendar; invitation emails alone are not calendar appointments.

`POST /api/v1/sync/calendar`, standard Authorization and X-Client-ID headers:
```json
{
  "client_id": "workstation-123",
  "window_start": "2026-09-04T00:00:00+00:00",
  "window_end": "2027-01-02T00:00:00+00:00",
  "calendar_store_id": "outlook-store-id",
  "calendar_entry_id": "outlook-folder-id",
  "complete": true,
  "errors": [],
  "events": [{
    "id": "sha256-occurrence-identity",
    "entry_id": "outlook-entry-id",
    "store_id": "outlook-store-id",
    "global_appointment_id": "outlook-global-id",
    "subject": "Project meeting",
    "body": "Agenda",
    "location": "Meeting room",
    "start": "2026-10-05T02:00:00+00:00",
    "end": "2026-10-05T03:00:00+00:00",
    "all_day": false,
    "is_recurring": false,
    "organizer": "Organizer name",
    "meeting_status": 1,
    "response_status": 3,
    "last_modified": "2026-10-04T09:00:00+00:00",
    "attendees": [{"name":"Participant", "address":"person@example.com", "type":1, "response_status":3}]
  }]
}
```
200: `{"status":"success","received":1}` after atomic batch upsert.
Identity = SHA256(JSON serialization of [store_id, global_appointment_id or entry_id, UTC start]).
Server treats IDs as opaque. Times start/end use UTC offsets; all-day end is exclusive.
Status/type values are Outlook enums, not strings; addresses can be Exchange addresses.
Replays are idempotent by (authenticated client, event id). Event body and attendees are
sent in full. Partial collection is uploaded with complete=false and surfaced as a warning.

This is upsert, NOT deletion mirroring. Moving an occurrence's start changes its ID.
Server must not silently interpret missing/partial/out-of-window events as cancellations.
The reference stores latest records by ID; old/rescheduled IDs remain. Full tombstones,
recurrence exception reconciliation and bidirectional editing are future extensions.

## Create appointment or meeting

Use existing `commands.queue` endpoints with type `outlook.calendar.create`:
```json
{
  "id": "unique-command-uuid",
  "type": "outlook.calendar.create",
  "payload": {
    "subject": "Project review",
    "body": "Review weekly progress",
    "location": "Meeting room A",
    "start": "2026-10-05T09:00:00+07:00",
    "end": "2026-10-05T10:00:00+07:00",
    "all_day": false,
    "reminder_minutes": 15,
    "attendees": ["person@example.com"],
    "send_invitations": false
  }
}
```
subject/start/end required; body/location default empty. Times MUST include UTC offset;
end must be after start. all_day defaults false; for all-day use local workstation midnight
boundaries and exclusive end. reminder_minutes defaults 15 (0..40320).
No attendees -> personal appointment. Attendees -> meeting saved locally.
Only explicit send_invitations=true sends invitations through Outlook. It requires attendees.
Creates in default Calendar; organizer/account is determined by that Calendar.
Creates single events only; recurrence creation, edits, deletes, meeting accept/decline and
Teams link generation are not implemented. Supply existing join links in body/location.

Results: calendar_saved or meeting_submitted_to_outlook, with entry_id/store_id/global_appointment_id.
Submitted does not mean attendee delivery/acceptance. Journal/replay/uncertain semantics
are identical to email commands; do not blindly retry uncertain creation with a new ID.
Next calendar collection uploads newly created entries.
