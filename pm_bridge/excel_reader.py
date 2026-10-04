"""Scan configured folders and serialize complete workbook contents."""
import io
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
import pandas as pd
from .config import EXCEL_WATCH_FOLDER, EXCEL_EXTENSIONS, IGNORED_FOLDERS
from .state_db import BridgeStateDB

class ExcelReader:
    def __init__(self, watch_folders=None, state_db=None, exclude_folders=()):
        folders = [EXCEL_WATCH_FOLDER] if watch_folders is None else watch_folders
        if isinstance(folders, (str, Path)):
            folders = [folders]
        self.watch_folders = [Path(str(f).strip()).resolve() for f in folders if str(f).strip()]
        self.excluded = [Path(p).resolve() for p in exclude_folders]
        self.state_db = state_db or BridgeStateDB()

    def _excluded(self, path):
        return any(path == p or p in path.parents for p in self.excluded)

    def scan_changed_files(self):
        changed, seen = [], set()
        for folder in self.watch_folders:
            if not folder.is_dir():
                raise FileNotFoundError(f"Thư mục không tồn tại: {folder}")
            if self._excluded(folder):
                continue
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d.lower() not in IGNORED_FOLDERS
                           and not d.startswith('.') and not self._excluded((Path(root) / d).resolve())
                           and not (Path(root) / d).is_symlink()]
                for name in files:
                    path = (Path(root) / name).resolve()
                    if name.startswith(('~$', '.')) or path in seen or self._excluded(path):
                        continue
                    seen.add(path)
                    if path.suffix.lower() in EXCEL_EXTENSIONS and self.state_db.should_sync_file(path):
                        changed.append(path)
        return changed

    def parse_excel_file(self, file_path, max_rows_per_sheet=None):
        path = Path(file_path)
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise OSError(f"File đang thay đổi, thử lại ở chu kỳ sau: {path.name}")
        result = {"file_path": str(path), "file_name": path.name, "file_size": len(content),
                  "file_hash": hashlib.sha256(content).hexdigest(),
                  "last_modified": datetime.fromtimestamp(after.st_mtime).isoformat(), "sheets": {}}
        if path.suffix.lower() == '.csv':
            frames = {"CSV_Data": pd.read_csv(io.BytesIO(content), nrows=max_rows_per_sheet)}
        else:
            with pd.ExcelFile(io.BytesIO(content)) as workbook:
                frames = {name: pd.read_excel(workbook, sheet_name=name, nrows=max_rows_per_sheet)
                          for name in workbook.sheet_names}
        for name, frame in frames.items():
            frame.columns = [str(c) for c in frame.columns]
            result['sheets'][name] = {"columns": list(frame.columns), "total_rows": len(frame),
                "rows": json.loads(frame.to_json(orient='records', date_format='iso'))}
        return result
