"""Native controller and tray, sharing the local web synchronization engine."""
import sys
import socket
import threading
import queue
import time
import webbrowser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tkinter as tk
from tkinter import ttk, messagebox
import requests
import pystray
from PIL import Image, ImageDraw
import uvicorn
from pm_bridge import web_app as web

URL = 'http://127.0.0.1:5555'

class Desktop:
    def __init__(self, sock):
        self.sock = sock
        self.events = queue.Queue()
        self.closing = False
        self.checking = False
        self.next_check = 0
        self.tray_ready = False
        self.root = tk.Tk()
        self.root.title('PM Bridge')
        self.root.geometry('460x430')
        self.root.minsize(440, 410)
        self.root.configure(bg='#f5f7fb')
        self.root.protocol('WM_DELETE_WINDOW', self.hide)
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TButton', font=('Segoe UI', 10), padding=9)
        self.state = tk.StringVar(value='Đang khởi động…')
        self.mode = tk.StringVar()
        self.connection = tk.StringVar(value='Đang kiểm tra…')
        self.recent = tk.StringVar(value='Chưa có lịch sử đồng bộ')
        self.result = tk.StringVar()
        frame = tk.Frame(self.root, bg='#f5f7fb', padx=24, pady=22)
        frame.pack(fill='both', expand=True)
        def label(text=None, variable=None, size=10, color='#76839a'):
            item = tk.Label(frame, text=text, textvariable=variable, font=('Segoe UI', size),
                            bg='#f5f7fb', fg=color, anchor='w', justify='left', wraplength=398)
            item.pack(fill='x', pady=3)
            return item
        label('PM Bridge', size=21, color='#4263eb')
        label(variable=self.state, size=13, color='#18243b')
        label(variable=self.mode)
        ttk.Separator(frame).pack(fill='x', pady=12)
        label(variable=self.connection)
        label(variable=self.recent)
        label(variable=self.result, color='#18243b')
        buttons = tk.Frame(frame, bg='#f5f7fb')
        buttons.pack(fill='x', pady=(16, 8))
        self.sync_button = ttk.Button(buttons, text='Đồng bộ ngay', command=self.sync)
        self.sync_button.pack(side='left', fill='x', expand=True, padx=(0, 6))
        self.auto_button = ttk.Button(buttons, text='Bật tự động', command=self.toggle)
        self.auto_button.pack(side='left', fill='x', expand=True)
        ttk.Button(frame, text='Mở bảng điều khiển Web ↗', command=lambda: webbrowser.open(URL)).pack(fill='x')
        label('Đóng cửa sổ để thu xuống khay. Chọn Thoát để dừng.', size=9)
        image = Image.new('RGB', (64, 64), '#4263eb')
        draw = ImageDraw.Draw(image)
        draw.arc((12, 12, 52, 52), 30, 300, fill='white', width=5)
        draw.polygon([(46, 8), (55, 22), (39, 22)], fill='white')
        self.tray = pystray.Icon('PMBridge', image, 'PM Bridge', pystray.Menu(
            pystray.MenuItem('Mở cửa sổ', lambda *_: self.events.put(('show', None)), default=True),
            pystray.MenuItem('Đồng bộ ngay', lambda *_: self.events.put(('sync', None))),
            pystray.MenuItem('Bật / tạm dừng tự động', lambda *_: self.events.put(('toggle', None))),
            pystray.MenuItem('Mở website', lambda *_: webbrowser.open(URL)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Thoát ứng dụng', lambda *_: self.events.put(('quit', None)))))
        web.desktop_show = lambda: self.events.put(('show', None))
        self.server = uvicorn.Server(uvicorn.Config(web.app, log_config=None, access_log=False))
        threading.Thread(target=self.server.run, kwargs={'sockets': [sock]}, daemon=True).start()
        self.worker = threading.Thread(target=web.background_sync_worker, daemon=True)
        self.worker.start()
        threading.Thread(target=self.run_tray, daemon=True).start()
        self.root.after(200, self.tick)

    def run_tray(self):
        try:
            def setup(icon):
                icon.visible = True
                self.events.put(('tray_ready', None))
            self.tray.run(setup=setup)
        except Exception:
            web.logging.exception('Tray failed')
            self.events.put(('show', None))

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after(150, lambda: self.root.attributes('-topmost', False))

    def hide(self):
        if self.tray_ready:
            self.root.withdraw()
        else:
            self.root.iconify()

    def sync(self):
        if self.closing or web.sync_lock.locked():
            return
        threading.Thread(target=lambda: web.perform_sync_core(True), daemon=True).start()

    def toggle(self):
        if self.closing or web.sync_lock.locked():
            return
        enabled = web.state_db.get_setting('auto_sync_enabled') == 'true'
        web.state_db.set_setting('auto_sync_enabled', 'false' if enabled else 'true')
        web.wake_worker.set()

    def check_server(self):
        try:
            result = web.test_server()
            self.events.put(('connection', 'Server: Đã kết nối · ' + str(result['latency_ms']) + ' ms'
                             if result['status'] == 'ok' else 'Server: Chưa kết nối — xem cài đặt trên Web'))
        finally:
            self.checking = False

    def quit(self):
        if self.closing:
            return
        self.closing = True
        web.stop_worker.set()
        web.wake_worker.set()
        self.show()
        self.state.set('Đang thoát · Chờ đồng bộ hiện tại hoàn tất…')

    def tick(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'show': self.show()
                elif kind == 'sync': self.sync()
                elif kind == 'toggle': self.toggle()
                elif kind == 'quit': self.quit()
                elif kind == 'connection': self.connection.set(data)
                elif kind == 'tray_ready': self.tray_ready = True
        except queue.Empty:
            pass
        busy = web.sync_lock.locked()
        if self.closing and not busy and not self.worker.is_alive():
            self.server.should_exit = True
            self.tray.stop()
            self.root.destroy()
            return
        settings = web.state_db.get_all_settings()
        auto = settings['auto_sync_enabled'] == 'true'
        if not self.closing:
            self.state.set('● Đang đồng bộ dữ liệu…' if busy else '● Đang chạy · Chờ lượt đồng bộ')
        self.mode.set('Tự động mỗi ' + settings['sync_interval_seconds'] + ' giây' if auto else 'Thủ công · Tự động đang tắt')
        self.auto_button.configure(text='Tạm dừng tự động' if auto else 'Bật tự động')
        for button in (self.sync_button, self.auto_button):
            button.configure(state='disabled' if busy or self.closing else 'normal')
        logs = web.state_db.get_recent_logs(50)
        completed = next((item for item in logs if item['type'] == 'SYNC' and item['status'] in ('success', 'warning', 'error')), None)
        if completed:
            self.recent.set('Kết quả gần nhất: ' + completed['timestamp'])
            self.result.set(completed['message'])
        if self.tray_ready:
            self.tray.title = 'PM Bridge · ' + ('Đang thoát' if self.closing else 'Đang đồng bộ' if busy else 'Tự động' if auto else 'Thủ công')
        if not self.closing and not self.checking and time.monotonic() >= self.next_check:
            self.checking = True
            self.next_check = time.monotonic() + 30
            threading.Thread(target=self.check_server, daemon=True).start()
        self.root.after(750, self.tick)

def main():
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', 5555))
    except OSError:
        sock.close()
        try:
            response = requests.post(URL + '/api/desktop/show', timeout=3)
            if response.ok and response.json().get('application') == 'pm-bridge':
                return
        except Exception:
            pass
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo('PM Bridge', 'Cổng 5555 đang được sử dụng. Nếu bản Web cũ đang chạy, hãy thoát bản đó rồi mở lại.')
        root.destroy()
        return
    try:
        desktop = Desktop(sock)
        desktop.root.mainloop()
    finally:
        sock.close()

if __name__ == '__main__':
    main()
