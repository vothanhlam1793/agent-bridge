"""Default Outlook calendar: bounded occurrence collection and explicit creation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('Calendar times must include a UTC offset')
    return parsed


def validate_create(payload):
    allowed = {'subject', 'body', 'location', 'start', 'end', 'all_day', 'attendees', 'send_invitations', 'reminder_minutes'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('Unsupported calendar field')
    for key in ('subject', 'start', 'end'):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError('Missing calendar ' + key)
    for key in ('body', 'location'):
        if key in payload and not isinstance(payload[key], str):
            raise ValueError('Invalid calendar ' + key)
    start, end = parse_time(payload['start']), parse_time(payload['end'])
    if end <= start:
        raise ValueError('Calendar end must be after start')
    for key in ('all_day', 'send_invitations'):
        if key in payload and type(payload[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    attendees = payload.get('attendees', [])
    if not isinstance(attendees, list) or any(not isinstance(a, str) or not a.strip() for a in attendees):
        raise ValueError('attendees must be a list of recipient strings')
    if payload.get('send_invitations') and not attendees:
        raise ValueError('Sending invitations requires attendees')
    if payload.get('all_day'):
        local_start, local_end = start.astimezone(), end.astimezone()
        if any((d.hour, d.minute, d.second, d.microsecond) != (0, 0, 0, 0) for d in (local_start, local_end)):
            raise ValueError('All-day boundaries must be midnight in the workstation timezone')
    reminder = payload.get('reminder_minutes', 15)
    if type(reminder) is not int or not 0 <= reminder <= 40320:
        raise ValueError('reminder_minutes must be 0..40320')


def utc_text(value):
    # Outlook StartUTC/EndUTC are UTC even when pywin32 returns naive values.
    return value.replace(tzinfo=timezone.utc).isoformat()


def collect(namespace, payload):
    begin = parse_time(payload['window_start']).astimezone(timezone.utc)
    end = parse_time(payload['window_end']).astimezone(timezone.utc)
    if not 0 < (end - begin).total_seconds() <= 180 * 86400:
        raise ValueError('Calendar window must be between 0 and 180 days')
    folder = namespace.GetDefaultFolder(9)
    items = folder.Items
    items.Sort('[Start]')
    items.IncludeRecurrences = True
    # Outlook Jet date filters use local wall time. Never enumerate unrestricted recurrences.
    lower = begin.astimezone().strftime('%m/%d/%Y %I:%M %p')
    upper = end.astimezone().strftime('%m/%d/%Y %I:%M %p')
    restricted = items.Restrict(f"[Start] < '{upper}' AND [End] > '{lower}'")
    events, errors = [], []
    item = restricted.GetFirst()
    scanned = 0
    while item is not None and scanned < 2000:
        scanned += 1
        try:
            if item.Class == 26:
                start, finish = utc_text(item.StartUTC), utc_text(item.EndUTC)
                if parse_time(start) < end and parse_time(finish) > begin:
                    global_id = str(item.GlobalAppointmentID)
                    identity = [str(folder.StoreID), global_id or str(item.EntryID), start]
                    recipients = []
                    for index in range(1, item.Recipients.Count + 1):
                        recipient = item.Recipients.Item(index)
                        recipients.append({'name': str(recipient.Name), 'address': str(recipient.Address),
                                           'type': int(recipient.Type), 'response_status': int(recipient.MeetingResponseStatus)})
                    events.append({'id': hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                                   'entry_id': str(item.EntryID), 'store_id': str(folder.StoreID),
                                   'global_appointment_id': global_id, 'subject': str(item.Subject or ''),
                                   'body': str(item.Body or ''), 'location': str(item.Location or ''),
                                   'start': start, 'end': finish, 'all_day': bool(item.AllDayEvent),
                                   'is_recurring': bool(item.IsRecurring), 'organizer': str(item.Organizer or ''),
                                   'meeting_status': int(item.MeetingStatus), 'response_status': int(item.ResponseStatus),
                                   'last_modified': item.LastModificationTime.isoformat(), 'attendees': recipients})
        except Exception as error:
            errors.append(str(error))
        item = restricted.GetNext()
    return {'window_start': begin.isoformat(), 'window_end': end.isoformat(),
            'events': events, 'complete': item is None and not errors, 'errors': errors,
            'calendar_store_id': str(folder.StoreID), 'calendar_entry_id': str(folder.EntryID)}


def create(namespace, command):
    payload = command['payload']
    validate_create(payload)
    folder = namespace.GetDefaultFolder(9)
    appointment = folder.Items.Add(1)
    appointment.Subject = payload['subject']
    appointment.Body = payload.get('body', '')
    appointment.Location = payload.get('location', '')
    appointment.AllDayEvent = payload.get('all_day', False)
    # Use UTC properties: no ambiguous local wall-clock conversion around DST.
    appointment.StartUTC = parse_time(payload['start']).astimezone(timezone.utc)
    appointment.EndUTC = parse_time(payload['end']).astimezone(timezone.utc)
    appointment.ReminderSet = True
    appointment.ReminderMinutesBeforeStart = payload.get('reminder_minutes', 15)
    attendees = payload.get('attendees', [])
    if attendees:
        appointment.MeetingStatus = 1
        for address in attendees:
            appointment.Recipients.Add(address).Type = 1
        if not appointment.Recipients.ResolveAll():
            raise ValueError('Outlook could not resolve meeting attendees')
    appointment.UserProperties.Add('PMBridgeCommandID', 1).Value = command['id']
    appointment.Save()
    result = {'outcome': 'calendar_saved', 'entry_id': str(appointment.EntryID),
              'store_id': str(folder.StoreID), 'global_appointment_id': str(appointment.GlobalAppointmentID)}
    if payload.get('send_invitations', False):
        appointment.Send()
        result['outcome'] = 'meeting_submitted_to_outlook'
    return result


def sync_calendar(settings, executor=None):
    import requests
    from .outlook_commands import run_isolated
    now = datetime.now(timezone.utc)
    command = {'id': 'calendar-read', 'type': 'outlook.calendar.read', 'payload': {
        'window_start': (now - timedelta(days=30)).isoformat(),
        'window_end': (now + timedelta(days=90)).isoformat()}}
    response = (executor or run_isolated)(command, settings)
    if response['status'] != 'succeeded':
        raise RuntimeError(response['result'].get('error', 'Calendar read failed'))
    batch = response['result']
    with requests.post(settings['server_url'].rstrip('/') + '/api/v1/sync/calendar',
                       headers={'Authorization': 'Bearer ' + settings['api_key'], 'X-Client-ID': settings['client_id']},
                       json={'client_id': settings['client_id'], **batch}, timeout=(3, 60)) as result:
        result.raise_for_status()
    if not batch['complete']:
        raise RuntimeError(f"Calendar upload partial: {len(batch['events'])} events; {len(batch['errors'])} errors (limit 2000)")
    return len(batch['events'])
