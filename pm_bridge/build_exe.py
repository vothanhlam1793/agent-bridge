"""
Script đóng gói toàn bộ PM Assistant Bridge thành 1 file .exe độc lập bằng PyInstaller
"""
import os
import sys
import subprocess
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent

def build():
    print("==================================================================")
    print(" 🛠️  BẮT ĐẦU ĐÓNG GÓI PM ASSISTANT BRIDGE THÀNH FILE .EXE")
    print("==================================================================")

    templates_dir = BASE_DIR / "templates"
    entry_point = BASE_DIR / "desktop_app.py"
    from datetime import datetime
    output_dir = BASE_DIR / 'releases' / datetime.now().strftime('%Y%m%d_%H%M%S')

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",  # Đóng gói dạng thư mục (khởi động cực nhanh)
        "--windowed", # Không hiện cửa sổ đen console
        "--name", "PMAssistant_Bridge",
        f"--add-data={templates_dir};pm_bridge/templates",
        f"--distpath={output_dir}",
        f"--workpath={BASE_DIR / 'build'}",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=win32com.client",
        "--hidden-import=win32timezone",
        "--hidden-import=jinja2",
        "--hidden-import=sqlite3",
        "--exclude-module=pandas",
        "--exclude-module=numpy",
        "--exclude-module=IPython",
        "--exclude-module=torch",
        "--exclude-module=tensorflow",
        "--exclude-module=cv2",
        "--exclude-module=keras",
        "--exclude-module=torchvision",
        "--exclude-module=matplotlib",
        "--exclude-module=scipy",
        str(entry_point)
    ]

    print("Đang chạy PyInstaller...")
    subprocess.run(cmd, check=True)
    print("\n==================================================================")
    print(f"🎉 ĐÓNG GÓI THÀNH CÔNG!")
    print(f"Thư mục ứng dụng: {output_dir / 'PMAssistant_Bridge'}")
    print("Bạn có thể copy thư mục này sang bất kỳ máy tính nào để sử dụng!")
    print("==================================================================")

if __name__ == "__main__":
    build()
