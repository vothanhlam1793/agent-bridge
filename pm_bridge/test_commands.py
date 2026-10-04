"""Command replay and failure tests; never send real email."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from pm_bridge.command_queue import CommandQueue


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'state.db'
        self.settings = {'server_url': 'https://example.com', 'client_id': 'pc1', 'api_key': 'test'}
        self.command = {'id': 'job1', 'type': 'outlook.send', 'payload': {'to': 'test@example.com', 'subject': 'test', 'body': 'text'}}
        self.executor = Mock(return_value={'status': 'succeeded', 'result': {'outcome': 'submitted_to_outlook'}})
        self.queue = CommandQueue(self.settings, self.db, self.executor)

    def test_replay_after_restart_does_not_send_again(self):
        first = self.queue.process(self.command)
        restarted = CommandQueue(self.settings, self.db, self.executor)
        self.assertEqual(restarted.process(self.command), first)
        self.executor.assert_called_once()

    def test_exception_is_uncertain_and_not_retried(self):
        self.executor.side_effect = RuntimeError('COM disconnected after send')
        self.assertEqual(self.queue.process(self.command)['status'], 'uncertain')
        self.assertEqual(self.queue.process(self.command)['status'], 'uncertain')
        self.executor.assert_called_once()

    def test_crash_after_journal_before_result(self):
        self.executor.side_effect = SystemExit('simulated process crash')
        with self.assertRaises(SystemExit):
            self.queue.process(self.command)
        restarted = CommandQueue(self.settings, self.db, self.executor)
        self.assertEqual(restarted.process(self.command)['status'], 'uncertain')
        self.executor.assert_called_once()

    def test_mutated_id_rejected(self):
        self.queue.process(self.command)
        self.command['payload']['body'] = 'different'
        with self.assertRaises(ValueError):
            self.queue.process(self.command)
        self.executor.assert_called_once()

    def test_invalid_job_never_touches_outlook(self):
        self.command['type'] = 'shell.execute'
        with self.assertRaises(ValueError):
            self.queue.process(self.command)
        self.executor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
