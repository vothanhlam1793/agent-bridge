"""One-way, resumable archive of every accessible Outlook store/folder."""
import hashlib
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
import requests


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class Archive:
    def __init__(self, settings):
        self.settings = settings
        self.url = settings['server_url'].rstrip('/')
        self.scope = identity(self.url, settings['client_id'])
        self.headers = {'Authorization': 'Bearer ' + settings['api_key'], 'X-Client-ID': settings['client_id']}
        from .paths import data_dir
        self.db_path = data_dir() / 'mail_archive.db'
        with closing(self.db()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS versions(scope TEXT,id TEXT,version TEXT,PRIMARY KEY(scope,id))')
            db.execute('CREATE TABLE IF NOT EXISTS cursors(scope TEXT,id TEXT,position INTEGER,PRIMARY KEY(scope,id))')

    def db(self):
        return sqlite3.connect(self.db_path, timeout=5)

    def post(self, route, payload):
        with requests.post(self.url + route, headers=self.headers, json=payload, timeout=(5, 45)) as response:
            response.raise_for_status()
            return response.json()

    def cursor(self, folder_id, position=None):
        with closing(self.db()) as db, db:
            if position is not None:
                db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?)', (self.scope, folder_id, position))
                return position
            row = db.execute('SELECT position FROM cursors WHERE scope=? AND id=?', (self.scope, folder_id)).fetchone()
            return row[0] if row else 1

    def unchanged(self, key, version):
        with closing(self.db()) as db:
            return db.execute('SELECT 1 FROM versions WHERE scope=? AND id=? AND version=?', (self.scope, key, version)).fetchone() is not None

    def mark(self, key, version):
        with closing(self.db()) as db, db:
            db.execute('INSERT OR REPLACE INTO versions VALUES(?,?,?)', (self.scope, key, version))

    def stats(self):
        with closing(self.db()) as db:
            return {'archived_emails': db.execute('SELECT COUNT(*) FROM versions WHERE scope=?', (self.scope,)).fetchone()[0],
                    'folders_with_progress': db.execute("SELECT COUNT(*) FROM cursors WHERE scope=? AND id!='__folder_rotation__'", (self.scope,)).fetchone()[0]}

    def blob(self, path):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with path.open('rb') as stream, requests.put(
                self.url + '/api/v1/sync/files', headers={**self.headers, 'Content-Type': 'application/octet-stream'},
                params={'source_id': 'mailarchive', 'relative_path': digest, 'sha256': digest},
                data=stream, timeout=(5, 60)) as response:
            response.raise_for_status()
            if response.json().get('sha256') != digest:
                raise ValueError('Archive blob checksum acknowledgement mismatch')
        return {'source_id': 'mailarchive', 'relative_path': digest, 'sha256': digest, 'size': path.stat().st_size}

    def export(self, item, folder):
        entry_id = str(item.EntryID)
        modified = item.LastModificationTime.isoformat()
        key = identity(folder['store_id'], entry_id)
        version = identity(modified, folder['id'])
        if self.unchanged(key, version):
            return False
        warnings = []
        def field(name, default=''):
            try:
                value = getattr(item, name)
                return value.isoformat() if hasattr(value, 'isoformat') else value
            except Exception as error:
                warnings.append(name + ': ' + str(error))
                return default
        data = {'id': key, 'entry_id': entry_id, 'store_id': folder['store_id'],
                'folder_id': folder['id'], 'folder_path': folder['path'], 'mailbox': folder['mailbox'],
                'modified_at': modified, 'version': version, 'warnings': warnings}
        for name, prop in {'subject': 'Subject', 'body_text': 'Body', 'body_html': 'HTMLBody',
                           'sender_name': 'SenderName', 'sender_email': 'SenderEmailAddress',
                           'to': 'To', 'cc': 'CC', 'bcc': 'BCC', 'received_time': 'ReceivedTime',
                           'sent_time': 'SentOn', 'conversation_id': 'ConversationID',
                           'conversation_topic': 'ConversationTopic', 'categories': 'Categories',
                           'unread': 'UnRead', 'importance': 'Importance', 'size': 'Size'}.items():
            data[name] = field(prop)
        data['recipients'] = []
        for index in range(1, item.Recipients.Count + 1):
            recipient = item.Recipients.Item(index)
            data['recipients'].append({'name': str(recipient.Name), 'address': str(recipient.Address), 'type': int(recipient.Type)})
        data['attachments'] = []
        with tempfile.TemporaryDirectory(prefix='pm-mail-') as temporary:
            root = Path(temporary)
            original = root / 'original.msg'
            item.SaveAs(str(original), 9)  # Unicode MSG preserves MAPI properties and attachments.
            data['original_msg'] = self.blob(original)
            for index in range(1, item.Attachments.Count + 1):
                attachment = item.Attachments.Item(index)
                target = root / str(index)
                attachment.SaveAsFile(str(target))
                data['attachments'].append({'index': index, 'filename': str(attachment.FileName),
                                             'blob': self.blob(target)})
        if item.LastModificationTime.isoformat() != modified:
            raise ValueError('Email changed while exporting; retry on next sweep')
        receipt = self.post('/api/v1/archive/emails', {'client_id': self.settings['client_id'], 'email': data})
        if receipt.get('id') != key or receipt.get('version') != version:
            raise ValueError('Archive receipt mismatch')
        self.mark(key, version)
        return True

    def run(self, namespace, budget_seconds=240, batch_size=500):
        started = time.monotonic()
        folders, errors, stack, seen = [], [], [], set()
        # Folder topology is sent even for non-mail folders so server retains classification.
        for index in range(1, namespace.Stores.Count + 1):
            try:
                store = namespace.Stores.Item(index)
                stack.append((store.GetRootFolder(), None, str(store.DisplayName)))
            except Exception as error:
                errors.append('Store: ' + str(error))
        objects = {}
        while stack:
            if time.monotonic() - started > budget_seconds:
                errors.append('Folder discovery deadline; will retry')
                break
            folder, parent, mailbox = stack.pop()
            try:
                key = identity(str(folder.StoreID), str(folder.EntryID))
                if key in seen:
                    continue
                seen.add(key)
                info = {'id': key, 'entry_id': str(folder.EntryID), 'store_id': str(folder.StoreID),
                        'parent_id': parent, 'name': str(folder.Name), 'path': str(folder.FolderPath),
                        'mailbox': mailbox, 'default_item_type': int(folder.DefaultItemType)}
                folders.append(info)
                objects[key] = folder
                for index in range(1, folder.Folders.Count + 1):
                    stack.append((folder.Folders.Item(index), key, mailbox))
            except Exception as error:
                errors.append('Folder: ' + str(error))
        self.post('/api/v1/archive/folders', {'client_id': self.settings['client_id'], 'folders': folders,
                                             'complete': not errors, 'errors': errors})
        count, scanned = 0, 0
        # Rotate starting folder across cycles so large early folders cannot starve later ones.
        offset = self.cursor('__folder_rotation__') - 1
        ordered = folders[offset % len(folders):] + folders[:offset % len(folders)] if folders else []
        for info in ordered:
            if scanned >= batch_size or time.monotonic() - started > budget_seconds:
                break
            self.cursor('__folder_rotation__', folders.index(info) + 2)
            try:
                items = objects[info['id']].Items
                position = self.cursor(info['id'])
                total = items.Count
                if position > total:
                    position = 1
                # Per-folder quota provides progress even on stores with huge folders.
                attempted = 0
                while position <= total and attempted < 100 and scanned < batch_size:
                    if time.monotonic() - started > budget_seconds:
                        break
                    try:
                        item = items.Item(position)
                        if item.Class == 43:
                            count += int(self.export(item, info))
                    except requests.RequestException:
                        raise  # Preserve current position when server/network fails.
                    except Exception as error:
                        errors.append(info['path'] + f' item {position}: ' + str(error))
                    position += 1
                    scanned += 1
                    attempted += 1
                    self.cursor(info['id'], position if position <= total else 1)
            except requests.RequestException:
                raise
            except Exception as error:
                errors.append(info['path'] + ': ' + str(error))
        return {'archived': count, 'scanned': scanned, 'folders': len(folders), 'errors': errors,
                'message': 'Rolling full sweep; no date/keyword filter; unacknowledged items retry on later sweeps'}
