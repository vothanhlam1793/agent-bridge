"""Reference hub: durable storage, original-file upload and report downloads."""
import os
import json
import sqlite3
import hashlib
import secrets
import uuid
from pathlib import Path, PurePosixPath
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse

ROOT = Path(os.getenv('PM_HUB_DATA_DIR', str(Path(__file__).resolve().parent / 'data')))
ROOT.mkdir(parents=True, exist_ok=True)
KEY = os.getenv('BRIDGE_API_KEY', '')
if not KEY:
    raise RuntimeError('Set BRIDGE_API_KEY before starting the reference server')

def connect():
    return sqlite3.connect(ROOT / 'hub.db', timeout=30)

with connect() as db:
    db.execute('CREATE TABLE IF NOT EXISTS emails(client TEXT, id TEXT, payload TEXT, PRIMARY KEY(client,id))')
    db.execute('CREATE TABLE IF NOT EXISTS confirmations(client TEXT, id TEXT, PRIMARY KEY(client,id))')
    db.execute('CREATE TABLE IF NOT EXISTS commands(client TEXT,id TEXT,command TEXT,result TEXT,PRIMARY KEY(client,id))')
    db.execute('CREATE TABLE IF NOT EXISTS calendar_events(client TEXT,id TEXT,payload TEXT,PRIMARY KEY(client,id))')
    db.execute('CREATE TABLE IF NOT EXISTS archive_folders(client TEXT,id TEXT,payload TEXT,PRIMARY KEY(client,id))')
    db.execute('CREATE TABLE IF NOT EXISTS archive_versions(client TEXT,id TEXT,version TEXT,payload TEXT,PRIMARY KEY(client,id,version))')
db.close()

def auth(request: Request):
    if not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + KEY):
        raise HTTPException(401, 'Invalid API key')
    client = request.headers.get('X-Client-ID', '')
    if not client:
        raise HTTPException(400, 'Missing client ID')
    return client

def client_dir(client):
    path = ROOT / hashlib.sha256(client.encode()).hexdigest()[:24]
    path.mkdir(parents=True, exist_ok=True)
    return path

app = FastAPI(title='PM Bridge Reference Hub')

@app.get('/')
def home():
    return {'status': 'running'}

@app.get('/api/v1/handshake')
def handshake(client=Depends(auth)):
    return {'protocol_version': '1.0', 'status': 'ready', 'client_id': client,
            'capabilities': ['files.upload', 'emails.upsert', 'reports.download', 'commands.queue', 'calendar.upsert', 'emails.archive']}

@app.post('/api/v1/archive/folders')
async def archive_folders(request: Request, client=Depends(auth)):
    payload = await request.json()
    folders = payload.get('folders')
    if payload.get('client_id') != client or not isinstance(folders, list) or any(
            not isinstance(folder, dict) or not isinstance(folder.get('id'), str) for folder in folders):
        raise HTTPException(422, 'Invalid folder inventory')
    db = connect()
    try:
        with db:
            for folder in folders:
                db.execute('INSERT OR REPLACE INTO archive_folders VALUES(?,?,?)', (client, folder['id'], json.dumps(folder)))
    finally:
        db.close()
    return {'status': 'success', 'received': len(folders)}

@app.post('/api/v1/archive/emails')
async def archive_email(request: Request, client=Depends(auth)):
    payload = await request.json()
    mail = payload.get('email')
    if payload.get('client_id') != client or not isinstance(mail, dict) or any(
            not isinstance(mail.get(key), str) or not mail[key] for key in ('id', 'version', 'folder_id', 'entry_id', 'store_id')):
        raise HTTPException(422, 'Invalid archived email')
    try:
        blobs = [mail['original_msg']] + [a['blob'] for a in mail['attachments']]
        for blob in blobs:
            digest = blob['sha256']
            if (len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
                    or blob['source_id'] != 'mailarchive' or blob['relative_path'] != digest):
                raise ValueError()
            path = client_dir(client) / 'uploads' / 'mailarchive' / digest
            if not path.is_file() or path.stat().st_size != blob['size']:
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, 'Upload all archive blobs before committing metadata')
    db = connect()
    try:
        with db:
            # Immutable receipt: replay preserves previously committed version.
            db.execute('INSERT OR IGNORE INTO archive_versions VALUES(?,?,?,?)',
                       (client, mail['id'], mail['version'], json.dumps(mail, ensure_ascii=False)))
    finally:
        db.close()
    return {'status': 'success', 'id': mail['id'], 'version': mail['version']}

@app.get('/api/v1/archive/folders')
def list_archive_folders(client=Depends(auth)):
    db = connect()
    try:
        return {'folders': [json.loads(row[0]) for row in db.execute('SELECT payload FROM archive_folders WHERE client=?', (client,))]}
    finally:
        db.close()

@app.get('/api/v1/archive/emails')
def list_archive_emails(offset: int = 0, limit: int = 100, folder_id: str = '', client=Depends(auth)):
    if offset < 0 or not 1 <= limit <= 500:
        raise HTTPException(422, 'Invalid pagination')
    db = connect()
    try:
        # Version pagination intentionally retains moved/deleted mail history.
        query = 'SELECT payload FROM archive_versions WHERE client=?'
        args = [client]
        if folder_id:
            query += " AND json_extract(payload, '$.folder_id')=?"
            args.append(folder_id)
        rows = db.execute(query + ' ORDER BY rowid LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
        return {'emails': [json.loads(row[0]) for row in rows], 'next_offset': offset + len(rows) if len(rows) == limit else None}
    finally:
        db.close()

@app.get('/api/v1/archive/blobs/{digest}')
def archive_blob(digest: str, client=Depends(auth)):
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise HTTPException(422, 'Invalid digest')
    path = client_dir(client) / 'uploads' / 'mailarchive' / digest
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='application/octet-stream')

@app.post('/api/v1/sync/calendar')
async def calendar_upsert(request: Request, client=Depends(auth)):
    payload = await request.json()
    if not isinstance(payload, dict) or payload.get('client_id') != client:
        raise HTTPException(422, 'Invalid calendar client')
    events = payload.get('events')
    if not isinstance(events, list) or len(events) > 2000 or any(
            not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id'] for event in events):
        raise HTTPException(422, 'Invalid calendar events')
    db = connect()
    try:
        with db:
            for event in events:
                db.execute('INSERT OR REPLACE INTO calendar_events VALUES(?,?,?)',
                           (client, event['id'], json.dumps(event, ensure_ascii=False)))
    finally:
        db.close()
    return {'status': 'success', 'received': len(events)}

def enqueue_command(client, command):
    """Backend-only API: no public endpoint allowing agents to create server jobs."""
    from pm_bridge.command_queue import validate
    validate(command)
    db = connect()
    try:
        with db:
            existing = db.execute('SELECT command FROM commands WHERE client=? AND id=?', (client, command['id'])).fetchone()
            encoded = json.dumps(command, sort_keys=True)
            if existing and existing[0] != encoded:
                raise ValueError('Command ID already exists with different payload')
            db.execute('INSERT OR IGNORE INTO commands VALUES(?,?,?,NULL)', (client, command['id'], encoded))
    finally:
        db.close()

@app.get('/api/v1/commands/pending')
def pending_commands(client=Depends(auth)):
    db = connect()
    try:
        rows = db.execute('SELECT command FROM commands WHERE client=? AND result IS NULL ORDER BY rowid LIMIT 10', (client,)).fetchall()
        return {'commands': [json.loads(row[0]) for row in rows]}
    finally:
        db.close()

@app.post('/api/v1/commands/{command_id}/result')
async def command_result(command_id: str, request: Request, client=Depends(auth)):
    payload = await request.json()
    if payload.get('status') not in ('succeeded', 'failed', 'uncertain') or not isinstance(payload.get('result'), dict):
        raise HTTPException(422, 'Invalid command result')
    db = connect()
    try:
        with db:
            row = db.execute('SELECT result FROM commands WHERE client=? AND id=?', (client, command_id)).fetchone()
            if row is None:
                raise HTTPException(404)
            encoded = json.dumps(payload, sort_keys=True)
            if row[0] is not None and row[0] != encoded:
                raise HTTPException(409, 'Result already committed')
            db.execute('UPDATE commands SET result=? WHERE client=? AND id=?', (encoded, client, command_id))
    finally:
        db.close()
    return {'status': 'acknowledged'}

@app.put('/api/v1/sync/files')
async def upload(request: Request, source_id: str, relative_path: str, sha256: str, client=Depends(auth)):
    if source_id == 'mailarchive' and (relative_path != sha256 or len(sha256) != 64
                                     or any(c not in '0123456789abcdef' for c in sha256)):
        raise HTTPException(422, 'Archive blobs must be content-addressed')
    parts = PurePosixPath(relative_path).parts
    if (not source_id.isalnum() or len(source_id) > 64 or not parts or
        PurePosixPath(relative_path).is_absolute() or any(p in ('.', '..') for p in parts) or
        any(c in relative_path for c in '\\:<>"|?*')):
        raise HTTPException(422, 'Invalid relative path')
    base = (client_dir(client) / 'uploads' / source_id).resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(422, 'Invalid target')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
    digest = hashlib.sha256()
    try:
        with temporary.open('xb') as output:
            async for chunk in request.stream():
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise HTTPException(422, 'Checksum mismatch')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {'status': 'success', 'sha256': sha256}

@app.post('/api/v1/sync/emails')
async def emails(request: Request, client=Depends(auth)):
    payload = await request.json()
    if payload.get('client_id') != client or not isinstance(payload.get('emails'), list):
        raise HTTPException(422, 'Invalid payload')
    if payload.get('count') != len(payload['emails']) or any(
            not isinstance(item, dict) or not isinstance(item.get('entry_id'), str)
            or not item['entry_id'] for item in payload['emails']):
        raise HTTPException(422, 'Invalid email batch')
    db = connect()
    try:
        with db:
            for item in payload['emails']:
                db.execute('INSERT OR REPLACE INTO emails VALUES(?,?,?)',
                           (client, item['entry_id'], json.dumps(item, ensure_ascii=False)))
    finally:
        db.close()
    return {'status': 'success', 'received': len(payload['emails'])}

def report_list(client):
    folder = client_dir(client) / 'reports'
    folder.mkdir(exist_ok=True)
    reports = []
    for path in folder.iterdir():
        if path.is_file() and not path.name.endswith('.part'):
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            report_id = hashlib.sha256(json.dumps([client, path.name, digest]).encode()).hexdigest()
            reports.append({'id': report_id, 'filename': path.name, 'sha256': digest, '_path': path})
    return reports

@app.get('/api/v1/reports/pending')
def pending(client_id: str, client=Depends(auth)):
    if client_id != client:
        raise HTTPException(403)
    db = connect()
    try:
        confirmed = {r[0] for r in db.execute('SELECT id FROM confirmations WHERE client=?', (client,))}
    finally:
        db.close()
    return {'reports': [{k: v for k, v in r.items() if k != '_path'}
                        for r in report_list(client) if r['id'] not in confirmed]}

@app.get('/api/v1/reports/download/{report_id}')
def download(report_id: str, client=Depends(auth)):
    report = next((r for r in report_list(client) if r['id'] == report_id), None)
    if report is None:
        raise HTTPException(404)
    return FileResponse(report['_path'], filename=report['filename'])

@app.post('/api/v1/reports/confirm')
async def confirm(request: Request, client=Depends(auth)):
    payload = await request.json()
    if payload.get('client_id') != client:
        raise HTTPException(403)
    if payload.get('report_id') not in {r['id'] for r in report_list(client)}:
        raise HTTPException(404)
    db = connect()
    try:
        with db:
            db.execute('INSERT OR IGNORE INTO confirmations VALUES(?,?)', (client, payload['report_id']))
    finally:
        db.close()
    return {'status': 'confirmed'}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)
