"""Durable command journal. Uncertain operations are never automatically replayed."""
import hashlib
import json
import sqlite3
from contextlib import closing
from urllib.parse import quote
import requests
from .outlook_commands import run_isolated

KINDS = {'outlook.draft', 'outlook.send', 'outlook.reply', 'outlook.attachment', 'outlook.calendar.create'}


def validate(command):
    if not isinstance(command, dict) or not isinstance(command.get('id'), str) or not 1 <= len(command['id']) <= 128:
        raise ValueError('Invalid command ID')
    kind, payload = command.get('type'), command.get('payload')
    if kind not in KINDS or not isinstance(payload, dict):
        raise ValueError('Unsupported command type or payload')
    if kind == 'outlook.calendar.create':
        from .outlook_calendar import validate_create
        validate_create(payload)
        return
    required = ('entry_id',) if kind == 'outlook.attachment' else ('entry_id', 'body') if kind == 'outlook.reply' else ('to', 'subject', 'body')
    if any(not isinstance(payload.get(key), str) for key in required):
        raise ValueError('Missing command fields')
    for key in ('cc', 'bcc', 'account', 'store_id'):
        if key in payload and not isinstance(payload[key], str):
            raise ValueError('Invalid ' + key)
    if 'draft' in payload and not isinstance(payload['draft'], bool):
        raise ValueError('draft must be boolean')
    if kind == 'outlook.attachment' and (type(payload.get('index')) is not int or payload['index'] < 1):
        raise ValueError('Attachment index must be a positive integer')
    attachments = payload.get('attachments', [])
    if not isinstance(attachments, list) or any(not isinstance(name, str) for name in attachments):
        raise ValueError('Invalid attachments')


class CommandQueue:
    def __init__(self, settings, db_path, executor=run_isolated):
        self.settings, self.db_path, self.executor = settings, str(db_path), executor
        self.scope = hashlib.sha256(json.dumps([settings['server_url'].rstrip('/'), settings['client_id']]).encode()).hexdigest()
        self.url = settings['server_url'].rstrip('/') + '/api/v1/commands'
        self.headers = {'Authorization': 'Bearer ' + settings['api_key'], 'X-Client-ID': settings['client_id']}
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS command_journal(scope TEXT,id TEXT,digest TEXT,response TEXT,PRIMARY KEY(scope,id))')

    def process(self, command):
        validate(command)
        digest = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
        # Commit uncertain BEFORE doing anything in Outlook, including Save or Send.
        response = {'status': 'uncertain', 'result': {'error': 'Execution interrupted; manual reconciliation required'}}
        with closing(sqlite3.connect(self.db_path)) as db, db:
            cursor = db.execute('INSERT OR IGNORE INTO command_journal VALUES(?,?,?,?)',
                                (self.scope, command['id'], digest, json.dumps(response)))
            if not cursor.rowcount:
                old_digest, old_response = db.execute('SELECT digest,response FROM command_journal WHERE scope=? AND id=?',
                                                     (self.scope, command['id'])).fetchone()
                if old_digest != digest:
                    raise ValueError('Server mutated an existing command ID')
                return json.loads(old_response)
        try:
            response = self.executor(command, self.settings)
        except Exception as error:
            response = {'status': 'uncertain', 'result': {'error': str(error)}}
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('UPDATE command_journal SET response=? WHERE scope=? AND id=?',
                       (json.dumps(response), self.scope, command['id']))
        return response

    def poll(self, stop=None):
        with requests.get(self.url + '/pending', headers=self.headers, timeout=(3, 15)) as response:
            response.raise_for_status()
            commands = response.json()['commands']
        completed = 0
        for command in commands[:10]:
            if stop and stop.is_set():
                break
            try:
                result = self.process(command)
            except ValueError as error:
                result = {'status': 'failed', 'result': {'error': str(error)}}
            with requests.post(self.url + '/' + quote(command['id'], safe='') + '/result',
                               headers=self.headers, json=result, timeout=(3, 30)) as response:
                response.raise_for_status()
            completed += 1
        return completed
