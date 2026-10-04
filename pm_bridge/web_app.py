"""Local dashboard and shared synchronization engine."""
import os
import sys
import time
import threading
import webbrowser
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR.parent))
from pm_bridge.paths import data_dir
from pm_bridge.state_db import BridgeStateDB
from pm_bridge.outlook_reader import OutlookReader
from pm_bridge.file_sync import FileSync
from pm_bridge.report_downloader import ReportDownloader
from pm_bridge.protocol import handshake
from pm_bridge.command_queue import CommandQueue
import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

DATA_DIR = data_dir()
handler = RotatingFileHandler(DATA_DIR / 'bridge.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
TEMPLATES_DIR = (Path(sys._MEIPASS) / 'pm_bridge' / 'templates'
                 if getattr(sys, 'frozen', False) else APP_DIR / 'templates')
app = FastAPI(title='PM Assistant Bridge')
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
state_db = BridgeStateDB(DATA_DIR / 'bridge_state.db')
sync_lock = threading.Lock()
wake_worker = threading.Event()
stop_worker = threading.Event()
desktop_show = None

@app.post('/api/desktop/show')
def show_desktop():
    if desktop_show is None:
        raise HTTPException(409, 'Desktop window is not available')
    desktop_show()
    return {'application': 'pm-bridge', 'status': 'ok'}

@app.middleware('http')
async def local_requests(request, call_next):
    if request.headers.get('host', '').split(':')[0] not in ('127.0.0.1', 'localhost', 'testserver'):
        return JSONResponse({'detail': 'Invalid host'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail': 'Invalid origin'}, status_code=403)
    try:
        return await call_next(request)
    except Exception:
        logging.exception('Request failed: %s', request.url.path)
        return JSONResponse({'detail': 'Lỗi ứng dụng; xem bridge.log trong thư mục dữ liệu.'}, status_code=500)

def perform_sync_core(is_manual=False):
    if not sync_lock.acquire(False):
        return {'status': 'busy', 'message': 'Một chu kỳ đồng bộ đang chạy.'}
    errors, emails_count, files_count, reports_count = [], 0, 0, 0
    try:
        settings = state_db.get_all_settings()
        # No mailbox/file collection before successful authenticated preflight.
        capabilities = handshake(settings)['capabilities']
        if 'commands.queue' in capabilities and settings.get('outlook_enabled', 'true') == 'true':
            count = CommandQueue(settings, state_db.db_path).poll(stop_worker)
            if count:
                state_db.add_log('COMMAND', f'Đã trả kết quả {count} lệnh Outlook; xem bảng lệnh để biết thành công/lỗi/chưa rõ', 'info')
        headers = {'Authorization': 'Bearer ' + settings['api_key'], 'X-Client-ID': settings['client_id']}
        state_db.add_log('SYNC', 'Bắt đầu: ' + ('Thủ công' if is_manual else 'Tự động'))
        if settings.get('outlook_enabled', 'true') == 'true':
            try:
                reader = OutlookReader(state_db, keywords=[k.strip() for k in settings['outlook_keywords'].split(',') if k.strip()])
                emails = reader.fetch_new_emails(max_items=500, lookback_days=int(settings['outlook_lookback_days']))
                if emails:
                    with requests.post(settings['server_url'].rstrip('/') + '/api/v1/sync/emails',
                                       json={'client_id': settings['client_id'], 'count': len(emails), 'emails': emails},
                                       headers=headers, timeout=(5, 60)) as response:
                        response.raise_for_status()
                    for email in emails:
                        state_db.mark_email_synced(email['entry_id'], email['subject'], email['sender_email'], email['received_time'])
                    emails_count = len(emails)
            except Exception as error:
                logging.exception('Outlook sync failed')
                errors.append(f'Outlook: {error}')
        try:
            files_count, file_errors = FileSync(settings, state_db).run()
            errors.extend(file_errors)
        except Exception as error:
            logging.exception('File sync failed')
            errors.append(f'Upload: {error}')
        try:
            reports_count = len(ReportDownloader(state_db=state_db, settings=settings).fetch_and_download_reports())
        except Exception as error:
            logging.exception('Report download failed')
            errors.append(f'Download: {error}')
        for error in errors:
            state_db.add_log('SYNC', error, 'error')
        message = f'{emails_count} email, {files_count} file, {reports_count} báo cáo; {len(errors)} lỗi'
        state_db.add_log('SYNC', message, 'warning' if errors else 'success')
        return {'status': 'warning' if errors else 'success', 'message': message}
    except Exception as error:
        logging.exception('Sync failed')
        state_db.add_log('SYNC', str(error), 'error')
        return {'status': 'error', 'message': str(error)}
    finally:
        sync_lock.release()

def background_sync_worker():
    while not stop_worker.is_set():
        wake_worker.clear()
        try:
            settings = state_db.get_all_settings()
            if settings['auto_sync_enabled'] == 'true':
                perform_sync_core()
            wake_worker.wait(max(30, int(settings['sync_interval_seconds'])))
        except Exception:
            logging.exception('Scheduler failed')
            wake_worker.wait(30)

@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name='index.html', context={})

@app.get('/api/settings')
def get_settings():
    return state_db.get_all_settings()

@app.get('/api/commands')
def command_history():
    import sqlite3
    from contextlib import closing
    queue = CommandQueue(state_db.get_all_settings(), state_db.db_path)
    with closing(sqlite3.connect(state_db.db_path)) as db:
        rows = db.execute('SELECT id,response FROM command_journal WHERE scope=? ORDER BY rowid DESC LIMIT 50', (queue.scope,)).fetchall()
    import json
    # Attachment bytes are kept for receipt retries, not included in dashboard history.
    return [{'id': key, 'status': json.loads(value)['status'],
             'result': {k: v for k, v in json.loads(value)['result'].items() if k != 'content_base64'}} for key, value in rows]

@app.post('/api/settings')
async def save_settings(request: Request):
    try:
        data = await request.json()
        current = state_db.get_all_settings()
        if not isinstance(data, dict) or any(k not in current or not isinstance(v, str) for k, v in data.items()):
            raise ValueError('Cấu hình phải là các trường chuỗi hợp lệ')
        merged = {**current, **data}
        url = urlsplit(merged['server_url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError('Server URL không hợp lệ')
        if not 30 <= int(merged['sync_interval_seconds']) <= 86400:
            raise ValueError('Chu kỳ phải từ 30 đến 86400 giây')
        if not 1 <= int(merged['outlook_lookback_days']) <= 3650:
            raise ValueError('Số ngày Outlook phải từ 1 đến 3650')
        for key in ('auto_sync_enabled', 'outlook_enabled'):
            if merged[key] not in ('true', 'false'):
                raise ValueError('Giá trị bật/tắt không hợp lệ')
        if not merged['client_id'].strip():
            raise ValueError('Client ID không được trống')
        download = Path(merged['reports_download_folder'])
        if not download.is_absolute():
            raise ValueError('Thư mục download phải là đường dẫn tuyệt đối')
        for line in merged['excel_watch_folders'].splitlines():
            if line.strip() and (not Path(line.strip()).is_absolute() or not Path(line.strip()).is_dir()):
                raise ValueError(f'Thư mục sync không tồn tại: {line}')
        if sync_lock.locked():
            raise HTTPException(409, 'Chờ chu kỳ sync hoàn tất trước khi đổi cấu hình')
        # Different destination must receive its own initial upload.
        if any(merged[k] != current[k] for k in ('server_url', 'client_id')):
            with state_db._get_connection() as conn:
                for table in ('synced_emails', 'synced_files', 'downloaded_reports'):
                    conn.execute(f'DELETE FROM {table}')
        state_db.update_settings(data)
        wake_worker.set()
        return {'status': 'ok'}
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error))

@app.get('/api/stats')
def stats():
    return {**state_db.get_stats(), 'syncing': sync_lock.locked()}

@app.get('/api/logs')
def logs():
    return state_db.get_recent_logs()

@app.get('/api/reports')
def reports():
    return state_db.get_downloaded_reports(100)

@app.post('/api/sync-now')
def sync_now():
    return perform_sync_core(True)

@app.get('/api/test-server')
def test_server():
    settings = state_db.get_all_settings()
    start = time.monotonic()
    try:
        handshake(settings)
        return {'status': 'ok', 'latency_ms': int((time.monotonic() - start) * 1000)}
    except Exception as error:
        return {'status': 'error', 'message': str(error)}

@app.post('/api/open-folder')
def open_folder():
    folder = Path(state_db.get_setting('reports_download_folder'))
    folder.mkdir(parents=True, exist_ok=True)
    os.startfile(str(folder))
    return {'status': 'ok'}

@app.post('/api/open-file')
def open_file(path: str):
    target = Path(path).resolve()
    with state_db._get_connection() as conn:
        allowed = [Path(r[0]).resolve() for r in conn.execute('SELECT local_path FROM downloaded_reports')]
    if target not in allowed or not target.is_file() or target.suffix.lower() not in ('.pdf', '.xlsx', '.xls', '.csv', '.docx', '.txt', '.png', '.jpg'):
        raise HTTPException(403, 'Chỉ mở tài liệu báo cáo đã tải')
    os.startfile(str(target))
    return {'status': 'ok'}

def run_app():
    import socket
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', 5555))
    except OSError:
        sock.close()
        webbrowser.open('http://127.0.0.1:5555')
        return
    threading.Thread(target=background_sync_worker, daemon=True).start()
    if '--no-browser' not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open('http://127.0.0.1:5555')).start()
    try:
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
        server.run(sockets=[sock])
    finally:
        sock.close()

if __name__ == '__main__':
    run_app()
