"""Upload original files with stable source IDs and relative paths."""
import hashlib
import os
import tempfile
from pathlib import Path
import requests

class FileSync:
    def __init__(self, settings, state_db):
        self.settings, self.db = settings, state_db
        self.roots = list(dict.fromkeys(Path(line.strip()).resolve() for line in
                          settings['excel_watch_folders'].splitlines() if line.strip()))
        self.excluded_paths = [Path(settings['reports_download_folder']).resolve(),
                         Path(state_db.db_path).resolve().parent]

    def excluded(self, path):
        return any(path == p or p in path.parents for p in self.excluded_paths)

    def run(self):
        count, errors, seen = 0, [], set()
        headers = {'Authorization': 'Bearer ' + self.settings['api_key'],
                   'X-Client-ID': self.settings['client_id']}
        for root in self.roots:
            if not root.is_dir():
                errors.append(f'Thư mục không tồn tại: {root}')
                continue
            if self.excluded(root):
                errors.append(f'Thư mục trùng vùng dữ liệu ứng dụng/download: {root}')
                continue
            source_id = hashlib.sha256(str(root).casefold().encode()).hexdigest()[:24]
            def walk_error(error):
                errors.append(str(error))
            for current, dirs, names in os.walk(root, onerror=walk_error):
                dirs[:] = [d for d in dirs if not self.excluded((Path(current) / d).resolve())
                           and not (Path(current) / d).is_symlink()]
                for name in names:
                    path = Path(current) / name
                    if path.is_symlink() or name.startswith('~$') or name.endswith(('.part', '.tmp')):
                        continue
                    path = path.resolve()
                    if path in seen or self.excluded(path):
                        continue
                    seen.add(path)
                    try:
                        before = path.stat()
                        digest = hashlib.sha256()
                        # Snapshot on disk: avoids loading large files into RAM or marking a newer version synced.
                        with tempfile.TemporaryFile() as snapshot:
                            with path.open('rb') as source:
                                while chunk := source.read(1024 * 1024):
                                    digest.update(chunk)
                                    snapshot.write(chunk)
                            after = path.stat()
                            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                                raise OSError('File đang thay đổi, thử lại chu kỳ sau')
                            checksum = digest.hexdigest()
                            with self.db._get_connection() as conn:
                                row = conn.execute('SELECT file_hash FROM synced_files WHERE file_path=?', (str(path),)).fetchone()
                            if row and row[0] == checksum:
                                continue
                            snapshot.seek(0)
                            # Raw streaming PUT avoids multipart buffering for large files.
                            with requests.put(self.settings['server_url'].rstrip('/') + '/api/v1/sync/files',
                                              params={'source_id': source_id, 'relative_path': path.relative_to(root).as_posix(),
                                                      'sha256': checksum}, data=snapshot,
                                              headers={**headers, 'Content-Type': 'application/octet-stream',
                                                       'Content-Length': str(before.st_size)}, timeout=(5, 120)) as response:
                                response.raise_for_status()
                                if response.json().get('sha256') != checksum:
                                    raise ValueError('Server chưa xác nhận đúng checksum')
                            self.db.mark_file_synced(path, checksum)
                            count += 1
                            self.db.add_log('UPLOAD', f'Đã gửi: {path.name}', 'success')
                    except Exception as error:
                        errors.append(f'{path.name}: {error}')
        return count, errors
