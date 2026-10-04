"""
File cấu hình cho hệ thống PM Bridge (Đồng bộ 2 chiều: Outlook + Excel <-> Server Hub)
"""
import os
from pathlib import Path

# Thư mục gốc của ứng dụng Bridge
BASE_DIR = Path(__file__).resolve().parent

# Đường dẫn database SQLite lưu trạng thái sync cục bộ (tránh gửi trùng dữ liệu)
STATE_DB_PATH = BASE_DIR / "bridge_state.db"

# ==================== CẤU HÌNH SERVER HUB ====================
SERVER_BASE_URL = os.getenv("SERVER_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("BRIDGE_API_KEY", "")
CLIENT_ID = os.getenv("BRIDGE_CLIENT_ID", "local_nvl_workstation")

# Endpoint API trên Server
API_SYNC_EMAILS_URL = f"{SERVER_BASE_URL}/api/v1/sync/emails"
API_SYNC_EXCELS_URL = f"{SERVER_BASE_URL}/api/v1/sync/excels"
API_CHECK_PENDING_REPORTS_URL = f"{SERVER_BASE_URL}/api/v1/reports/pending"
API_DOWNLOAD_REPORT_URL = f"{SERVER_BASE_URL}/api/v1/reports/download"
API_CONFIRM_REPORT_URL = f"{SERVER_BASE_URL}/api/v1/reports/confirm"

# ==================== CẤU HÌNH EXCEL / ONEDRIVE ====================
# Thư mục chứa các file Excel cần theo dõi và đồng bộ lên server (mặc định lấy thư mục cha hoặc chỉ định)
EXCEL_WATCH_FOLDER = Path.home() / "Documents" / "PMBridgeSources"

# Thư mục bỏ qua khi quét file
IGNORED_FOLDERS = {".git", "__pycache__", "venv", ".venv", "env", "node_modules", "vlc", "exe", "dist", "build", "scripts"}
EXCEL_EXTENSIONS = [".xlsx", ".xls", ".xlsm", ".csv"]


# ==================== CẤU HÌNH BÁO CÁO TẢI VỀ ====================
# Thư mục lưu các file báo cáo phân tích/tiến độ do Server tạo và tải về máy
REPORTS_DOWNLOAD_FOLDER = BASE_DIR / "BaoCao_TienDo_TuDong"

# ==================== CẤU HÌNH OUTLOOK ====================
# Số ngày quá khứ quét email lần đầu tiên (nếu chưa có lịch sử sync)
OUTLOOK_DEFAULT_LOOKBACK_DAYS = 7
# Lọc email theo từ khóa tiến độ dự án (nếu để trống [] thì đọc tất cả email mới)
OUTLOOK_PROJECT_KEYWORDS = []  # Ví dụ: ["tiến độ", "dự án", "task", "deadline", "báo cáo", "kế hoạch"]

# ==================== CẤU HÌNH CHẠY NGẦM (BACKGROUND INTERVAL) ====================
SYNC_INTERVAL_SECONDS = 300  # Quét và sync định kỳ mỗi 5 phút (300 giây)
