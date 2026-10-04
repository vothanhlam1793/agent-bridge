"""Isolated regression tests: no real mailbox or user documents are read."""
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import requests
import uvicorn
from fastapi.testclient import TestClient

TEMP_BASE = Path(os.getenv('LOCALAPPDATA', tempfile.gettempdir())) / 'Temp' / 'opencode'
TEMP_BASE.mkdir(parents=True, exist_ok=True)
TEMP = tempfile.TemporaryDirectory(dir=TEMP_BASE)
ROOT = Path(TEMP.name)
os.environ['PM_BRIDGE_DATA_DIR'] = str(ROOT / 'client')
os.environ['PM_HUB_DATA_DIR'] = str(ROOT / 'hub')
os.environ['BRIDGE_API_KEY'] = 'isolated-test-key'
from pm_bridge import web_app as web
from pm_bridge.server_hub_demo import server as hub

class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket = socket.socket()
        cls.socket.bind(('127.0.0.1', 0))
        cls.url = f'http://127.0.0.1:{cls.socket.getsockname()[1]}'
        cls.server = uvicorn.Server(uvicorn.Config(hub.app, log_config=None))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={'sockets': [cls.socket]}, daemon=True)
        cls.thread.start()
        import time
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(.05)
        assert cls.server.started
        cls.ui = TestClient(web.app)
        cls.upload = ROOT / 'source'
        cls.upload.mkdir(exist_ok=True)
        cls.download = cls.upload / 'downloads'
        cls.settings = {'server_url': cls.url, 'api_key': hub.KEY, 'client_id': 'test-client',
                        'excel_watch_folders': str(cls.upload), 'reports_download_folder': str(cls.download),
                        'auto_sync_enabled': 'false', 'outlook_enabled': 'false'}
        web.state_db.update_settings(cls.settings)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10)
        cls.socket.close()
        web.handler.close()

    def test_home_and_endpoints(self):
        for route in ('/', '/api/settings', '/api/stats', '/api/reports', '/api/logs'):
            self.assertEqual(self.ui.get(route).status_code, 200, route)
        self.assertEqual(self.ui.get('/api/test-server').json()['status'], 'ok')

    def test_failed_handshake_does_not_read_sources(self):
        with patch('pm_bridge.web_app.handshake', side_effect=requests.ConnectionError('offline')), \
             patch('pm_bridge.web_app.OutlookReader') as outlook, \
             patch('pm_bridge.web_app.FileSync') as files, \
             patch('pm_bridge.web_app.ReportDownloader') as reports:
            self.assertEqual(self.ui.post('/api/sync-now').json()['status'], 'error')
            outlook.assert_not_called()
            files.assert_not_called()
            reports.assert_not_called()

    def test_validation_and_local_origin(self):
        self.assertEqual(self.ui.post('/api/settings', json={'sync_interval_seconds': 'abc'}).status_code, 422)
        self.assertEqual(self.ui.post('/api/settings', json={'reports_download_folder': ''}).status_code, 422)
        self.assertEqual(self.ui.post('/api/settings', json=[], headers={'Origin': 'https://other.site'}).status_code, 403)
        self.assertEqual(self.ui.post('/api/open-file', params={'path': 'C:/Windows/notepad.exe'}).status_code, 403)

    def test_raw_upload_download_retry_and_no_loop(self):
        child = self.upload / 'nested'
        child.mkdir(exist_ok=True)
        original = child / 'sample.bin'
        original.write_bytes(bytes(range(256)) * 1000)
        web.state_db.set_setting('excel_watch_folders', str(self.upload) + '\n' + str(child))
        folder = hub.client_dir('test-client') / 'reports'
        folder.mkdir(exist_ok=True)
        (folder / 'report.txt').write_text('Report content', encoding='utf-8')
        result = self.ui.post('/api/sync-now').json()
        self.assertEqual(result['status'], 'success', repr(web.state_db.get_recent_logs()))
        uploaded = list(hub.client_dir('test-client').rglob('sample.bin'))
        self.assertEqual(len(uploaded), 1)
        self.assertEqual(uploaded[0].read_bytes(), original.read_bytes())
        self.assertEqual((self.download / 'report.txt').read_text(), 'Report content')
        result = self.ui.post('/api/sync-now').json()
        self.assertIn('0 file', result['message'])
        self.assertEqual(len(list(hub.client_dir('test-client').rglob('report.txt'))), 1)
        original.write_bytes(b'Changed version')
        result = self.ui.post('/api/sync-now').json()
        self.assertEqual(result['status'], 'success')
        self.assertEqual(uploaded[0].read_bytes(), b'Changed version')
        with patch('pm_bridge.file_sync.requests.put', side_effect=requests.ConnectionError('offline')):
            original.write_bytes(b'Offline version')
            self.assertEqual(self.ui.post('/api/sync-now').json()['status'], 'warning')
        self.assertEqual(self.ui.post('/api/sync-now').json()['status'], 'success')
        self.assertEqual(uploaded[0].read_bytes(), b'Offline version')

    def test_hub_auth_and_path_traversal(self):
        response = requests.put(self.url + '/api/v1/sync/files', params={'source_id': 'abc', 'relative_path': '../escape', 'sha256': 'x'},
                                headers={'Authorization': 'Bearer ' + hub.KEY, 'X-Client-ID': 'test-client'}, data=b'bad')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(requests.get(self.url + '/api/v1/reports/pending', params={'client_id': 'x'}).status_code, 401)

    def test_command_queue_receipt_retry_and_isolation(self):
        from pm_bridge.command_queue import CommandQueue
        from unittest.mock import Mock
        command = {'id': 'integration-job', 'type': 'outlook.draft', 'payload': {'to': 'test@example.com', 'subject': 'test', 'body': 'text'}}
        hub.enqueue_command('test-client', command)
        executor = Mock(return_value={'status': 'succeeded', 'result': {'outcome': 'draft_saved'}})
        queue = CommandQueue(self.settings, ROOT / 'commands.db', executor)
        with patch('pm_bridge.command_queue.requests.post', side_effect=requests.ConnectionError('ack lost')):
            with self.assertRaises(requests.ConnectionError):
                queue.poll()
        self.assertEqual(queue.poll(), 1)
        self.assertEqual(queue.poll(), 0)
        executor.assert_called_once()
        headers = {'Authorization': 'Bearer ' + hub.KEY, 'X-Client-ID': 'other-client'}
        self.assertEqual(requests.get(self.url + '/api/v1/commands/pending', headers=headers).json(), {'commands': []})
        result_url = self.url + '/api/v1/commands/integration-job/result'
        self.assertEqual(requests.post(result_url, headers=headers, json=executor.return_value).status_code, 404)
        self.assertEqual(requests.post(result_url, headers=queue.headers, json=executor.return_value).status_code, 200)

    def test_calendar_batch_replay_and_client_identity(self):
        payload = {'client_id': 'test-client', 'events': [{'id': 'calendar-event', 'subject': 'Review'}]}
        headers = {'Authorization': 'Bearer ' + hub.KEY, 'X-Client-ID': 'test-client'}
        for _ in range(2):
            response = requests.post(self.url + '/api/v1/sync/calendar', headers=headers, json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['received'], 1)
        db = hub.connect()
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM calendar_events WHERE client=?', ('test-client',)).fetchone()[0], 1)
        finally:
            db.close()
        payload['client_id'] = 'other-client'
        self.assertEqual(requests.post(self.url + '/api/v1/sync/calendar', headers=headers, json=payload).status_code, 422)

if __name__ == '__main__':
    unittest.main()
