"""
Module giao tiếp HTTP gửi dữ liệu lên Server Hub
"""
import requests
from typing import List, Dict, Any
from .config import API_SYNC_EMAILS_URL, API_SYNC_EXCELS_URL, API_KEY, CLIENT_ID

class ServerClient:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {API_KEY}",
            "X-Client-ID": CLIENT_ID,
            "Content-Type": "application/json"
        }

    def send_emails(self, emails: List[Dict[str, Any]]) -> bool:
        if not emails:
            return True
        try:
            payload = {
                "client_id": CLIENT_ID,
                "count": len(emails),
                "emails": emails
            }
            resp = requests.post(API_SYNC_EMAILS_URL, json=payload, headers=self.headers, timeout=(2.0, 10.0))
            return resp.status_code in [200, 201]
        except Exception as e:
            print(f"[ServerClient] Không thể kết nối Server Hub để gửi email: {e}")
            return False

    def send_excel_data(self, excel_data: Dict[str, Any]) -> bool:
        if not excel_data:
            return True
        try:
            payload = {
                "client_id": CLIENT_ID,
                "data": excel_data
            }
            resp = requests.post(API_SYNC_EXCELS_URL, json=payload, headers=self.headers, timeout=(2.0, 15.0))
            return resp.status_code in [200, 201]
        except Exception as e:
            print(f"[ServerClient] Không thể kết nối Server Hub để gửi Excel: {e}")
            return False
