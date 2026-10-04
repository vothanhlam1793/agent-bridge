"""
Quản lý cấu hình động và trạng thái đồng bộ bằng SQLite
"""
import sqlite3
import hashlib
import socket
import uuid
from contextlib import contextmanager
from .paths import data_dir
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Dict, Any, List

class BridgeStateDB:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            # Mặc định lưu file database cùng thư mục với state_db.py
            db_path = data_dir() / "bridge_state.db"
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Bảng Settings (Cấu hình hệ thống lưu vào SQLite)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)

            # 2. Bảng email đã sync
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS synced_emails (
                    entry_id TEXT PRIMARY KEY,
                    subject TEXT,
                    sender TEXT,
                    received_time TEXT,
                    synced_at TEXT
                )
            """)

            # 3. Bảng file excel đã sync
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS synced_files (
                    file_path TEXT PRIMARY KEY,
                    file_hash TEXT,
                    last_modified REAL,
                    synced_at TEXT
                )
            """)

            # 4. Bảng báo cáo đã tải về từ server
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS downloaded_reports (
                    report_id TEXT PRIMARY KEY,
                    file_name TEXT,
                    local_path TEXT,
                    downloaded_at TEXT
                )
            """)

            # 5. Bảng Nhật ký hoạt động (Sync Logs)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    log_type TEXT,
                    message TEXT,
                    status TEXT
                )
            """)
            conn.commit()
            
        # Khởi tạo settings mặc định nếu chưa có
        self._init_default_settings()

    def _init_default_settings(self):
        default_settings = {
            "server_url": "http://localhost:8000",
            "api_key": "",
            "client_id": socket.gethostname() + "-" + uuid.uuid4().hex[:8],
            "excel_watch_folders": self.get_setting("excel_watch_folder", ""),
            "reports_download_folder": str(Path.home() / "Documents" / "PMBridgeReports"),
            "auto_sync_enabled": "false",
            "sync_interval_seconds": "300",
            "outlook_lookback_days": "7",
            "outlook_keywords": ""
            ,"outlook_enabled": "true"
        }
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for k, v in default_settings.items():
                cursor.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (k, v, datetime.now().isoformat()))
            conn.commit()

    # --- SETTINGS METHODS ---
    def get_setting(self, key: str, default: str = "") -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default

    def get_all_settings(self) -> Dict[str, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_settings")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def set_setting(self, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO system_settings (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, str(value), datetime.now().isoformat()))
            conn.commit()

    def update_settings(self, settings_dict: Dict[str, Any]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for k, v in settings_dict.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO system_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (k, str(v), datetime.now().isoformat()))
            conn.commit()

    # --- LOGS METHODS ---
    def add_log(self, log_type: str, message: str, status: str = "info"):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sync_logs (timestamp, log_type, message, status)
                VALUES (?, ?, ?, ?)
            """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_type, message, status))
            # Giữ tối đa 500 logs gần nhất
            cursor.execute("DELETE FROM sync_logs WHERE id NOT IN (SELECT id FROM sync_logs ORDER BY id DESC LIMIT 500)")
            conn.commit()

    def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, timestamp, log_type, message, status FROM sync_logs ORDER BY id DESC LIMIT ?", (limit,))
            return [{"id": r[0], "timestamp": r[1], "type": r[2], "message": r[3], "status": r[4]} for r in cursor.fetchall()]

    def get_stats(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM synced_emails")
            emails_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM synced_files")
            files_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM downloaded_reports")
            reports_count = cursor.fetchone()[0]
            return {
                "synced_emails": emails_count,
                "synced_files": files_count,
                "downloaded_reports": reports_count
            }

    # --- EMAIL METHODS ---
    def is_email_synced(self, entry_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM synced_emails WHERE entry_id = ?", (entry_id,))
            return cursor.fetchone() is not None

    def get_synced_email_ids(self) -> Set[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT entry_id FROM synced_emails")
            return {row[0] for row in cursor.fetchall()}

    def mark_email_synced(self, entry_id: str, subject: str, sender: str, received_time: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO synced_emails (entry_id, subject, sender, received_time, synced_at)
                VALUES (?, ?, ?, ?, ?)
            """, (entry_id, subject, sender, received_time, datetime.now().isoformat()))
            conn.commit()

    # --- EXCEL / FILE METHODS ---
    @staticmethod
    def calculate_file_hash(file_path: Path) -> str:
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception:
            return ""

    def should_sync_file(self, file_path: Path) -> bool:
        if not file_path.exists():
            return False
        
        mtime = file_path.stat().st_mtime
        curr_hash = self.calculate_file_hash(file_path)
        if not curr_hash:
            return False

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_hash, last_modified FROM synced_files WHERE file_path = ?", (str(file_path),))
            row = cursor.fetchone()
            if not row:
                return True
            prev_hash, _ = row
            return prev_hash != curr_hash

    def mark_file_synced(self, file_path: Path, file_hash=None):
        mtime = file_path.stat().st_mtime
        curr_hash = file_hash or self.calculate_file_hash(file_path)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO synced_files (file_path, file_hash, last_modified, synced_at)
                VALUES (?, ?, ?, ?)
            """, (str(file_path), curr_hash, mtime, datetime.now().isoformat()))
            conn.commit()

    # --- REPORT METHODS ---
    def is_report_downloaded(self, report_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT local_path FROM downloaded_reports WHERE report_id = ?", (report_id,))
            row = cursor.fetchone()
            return bool(row and Path(row[0]).is_file())

    def mark_report_downloaded(self, report_id: str, file_name: str, local_path: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO downloaded_reports (report_id, file_name, local_path, downloaded_at)
                VALUES (?, ?, ?, ?)
            """, (report_id, file_name, local_path, datetime.now().isoformat()))
            conn.commit()

    def get_downloaded_reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT report_id, file_name, local_path, downloaded_at FROM downloaded_reports ORDER BY downloaded_at DESC LIMIT ?", (limit,))
            return [{"id": r[0], "filename": r[1], "local_path": r[2], "downloaded_at": r[3]} for r in cursor.fetchall()]
