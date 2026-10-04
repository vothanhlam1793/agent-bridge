"""Archive fault tests: fake Outlook, real local hub, no user mailbox accessed."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
import unittest
from pm_bridge import test_regression as regression
from pm_bridge.mail_archive import Archive


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        regression.RegressionTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        regression.RegressionTests.tearDownClass.__func__(cls)

    def test_archive_receipt_retry_binary_and_versions(self):
        archive = Archive(self.settings)
        folder = {'id': 'test-folder', 'store_id': 'store', 'path': r'\\Mailbox\Sent\Project', 'mailbox': 'Mailbox'}
        attachment = Mock(FileName='report.txt')
        attachment.SaveAsFile.side_effect = lambda path: Path(path).write_bytes(b'attachment content')
        item = SimpleNamespace(EntryID='archive-mail', LastModificationTime=datetime(2020, 1, 1, tzinfo=timezone.utc),
                               Recipients=SimpleNamespace(Count=0), Attachments=Mock(Count=1),
                               SaveAs=lambda path, kind: Path(path).write_bytes(b'original MSG bytes'))
        item.Attachments.Item.return_value = attachment
        with patch.object(archive, 'post', side_effect=requests.ConnectionError('receipt lost')):
            with self.assertRaises(requests.ConnectionError):
                archive.export(item, folder)
        self.assertEqual(archive.stats()['archived_emails'], 0)
        self.assertTrue(archive.export(item, folder))
        self.assertFalse(archive.export(item, folder))
        response = requests.get(self.url + '/api/v1/archive/emails', headers=archive.headers).json()
        mail = next(m for m in response['emails'] if m['entry_id'] == item.EntryID)
        self.assertEqual(mail['folder_path'], folder['path'])
        digest = mail['original_msg']['sha256']
        self.assertEqual(requests.get(self.url + '/api/v1/archive/blobs/' + digest, headers=archive.headers).content, b'original MSG bytes')
        other = {**archive.headers, 'X-Client-ID': 'unrelated-client'}
        self.assertEqual(requests.get(self.url + '/api/v1/archive/blobs/' + digest, headers=other).status_code, 404)
        item.LastModificationTime = datetime(2020, 1, 2, tzinfo=timezone.utc)
        self.assertTrue(archive.export(item, folder))
        response = requests.get(self.url + '/api/v1/archive/emails', headers=archive.headers).json()
        self.assertEqual(len([m for m in response['emails'] if m['entry_id'] == item.EntryID]), 2)

    def test_tree_resumes_without_date_keyword_filters(self):
        archive = Archive(self.settings)
        items = [SimpleNamespace(Class=43, EntryID=str(i)) for i in range(120)]
        def folder(name, children):
            value = Mock(StoreID='fake-store', EntryID=name, Name=name, FolderPath=name, DefaultItemType=0)
            value.Folders.Count = len(children)
            value.Folders.Item.side_effect = lambda i: children[i - 1]
            value.Items.Count = len(items) if name == 'Sent' else 0
            value.Items.Item.side_effect = lambda i: items[i - 1]
            return value
        sent = folder('Sent', [])
        root = folder('Root', [sent])
        namespace = Mock()
        namespace.Stores.Count = 1
        namespace.Stores.Item.return_value = Mock(DisplayName='Mailbox')
        namespace.Stores.Item.return_value.GetRootFolder.return_value = root
        seen = []
        with patch.object(archive, 'post', return_value={}) as post, patch.object(archive, 'export', side_effect=lambda item, info: seen.append(item.EntryID) or True):
            archive.run(namespace)
            restarted = Archive(self.settings)
            with patch.object(restarted, 'post', return_value={}), patch.object(restarted, 'export', side_effect=lambda item, info: seen.append(item.EntryID) or True):
                restarted.run(namespace)
            self.assertEqual(set(seen), {str(i) for i in range(120)})
            self.assertEqual(len(post.call_args.args[1]['folders']), 2)
