"""Calendar tests use fake COM objects and never create real appointments."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from pm_bridge.command_queue import validate
from pm_bridge.outlook_calendar import create, collect


class CalendarTests(unittest.TestCase):
    def command(self, **changes):
        return {'id': 'calendar-test', 'type': 'outlook.calendar.create', 'payload': {
            'subject': 'Review', 'start': '2026-10-05T09:00:00+07:00',
            'end': '2026-10-05T10:00:00+07:00', **changes}}

    def namespace(self):
        item = Mock(EntryID='entry', GlobalAppointmentID='global')
        folder = Mock(StoreID='store')
        folder.Items.Add.return_value = item
        namespace = Mock()
        namespace.GetDefaultFolder.return_value = folder
        return namespace, item

    def test_personal_calendar_never_sends(self):
        namespace, item = self.namespace()
        command = self.command()
        validate(command)
        result = create(namespace, command)
        item.Save.assert_called_once()
        item.Send.assert_not_called()
        self.assertEqual(item.StartUTC, datetime(2026, 10, 5, 2, tzinfo=timezone.utc))
        self.assertEqual(result['outcome'], 'calendar_saved')

    def test_meeting_requires_explicit_send(self):
        namespace, item = self.namespace()
        create(namespace, self.command(attendees=['test@example.com']))
        item.Send.assert_not_called()
        result = create(namespace, self.command(attendees=['test@example.com'], send_invitations=True))
        item.Send.assert_called_once()
        self.assertEqual(result['outcome'], 'meeting_submitted_to_outlook')

    def test_invalid_times_and_send_flags(self):
        for changes in ({'start': '2026-10-05T09:00:00'},
                        {'end': '2026-10-04T10:00:00+07:00'},
                        {'send_invitations': 'false'}, {'send_invitations': True},
                        {'attendees': 'test@example.com'}, {'recurrence': 'weekly'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(self.command(**changes))

    def test_bounded_collection_partial_errors(self):
        namespace, _ = self.namespace()
        folder = namespace.GetDefaultFolder.return_value
        folder.EntryID = 'calendar'
        good = SimpleNamespace(Class=26, StartUTC=datetime(2026, 10, 5, 2), EndUTC=datetime(2026, 10, 5, 3),
                               GlobalAppointmentID='global', EntryID='entry', Subject='Meeting', Body='Agenda',
                               Location='Room', AllDayEvent=False, IsRecurring=True, Organizer='Owner',
                               MeetingStatus=1, ResponseStatus=3, LastModificationTime=datetime(2026, 10, 4),
                               Recipients=SimpleNamespace(Count=0))
        broken = SimpleNamespace(Class=26)
        restricted = folder.Items.Restrict.return_value
        restricted.GetFirst.return_value = good
        restricted.GetNext.side_effect = [broken, None]
        result = collect(namespace, {'window_start': '2026-10-01T00:00:00Z', 'window_end': '2026-11-01T00:00:00Z'})
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertFalse(result['complete'])
        self.assertTrue(folder.Items.IncludeRecurrences)
        self.assertIn('[End] >', folder.Items.Restrict.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
