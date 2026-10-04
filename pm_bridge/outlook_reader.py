"""
Module đọc dữ liệu email và file đính kèm từ Outlook Desktop qua MAPI / COM
"""
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
import win32com.client
import pythoncom
import logging
from .config import OUTLOOK_DEFAULT_LOOKBACK_DAYS, OUTLOOK_PROJECT_KEYWORDS
from .state_db import BridgeStateDB

class OutlookReader:
    def __init__(self, state_db: Optional[BridgeStateDB] = None, keywords=None):
        self.keywords = OUTLOOK_PROJECT_KEYWORDS if keywords is None else keywords
        self.state_db = state_db or BridgeStateDB()
        self.outlook = None
        self.namespace = None

    def _connect(self):
        if not self.outlook:
            try:
                self.outlook = win32com.client.Dispatch("Outlook.Application")
                self.namespace = self.outlook.GetNamespace("MAPI")
            except Exception as e:
                raise RuntimeError(f"Không thể kết nối với Outlook Desktop: {e}")

    def fetch_new_emails(self, max_items: int = 100, lookback_days: int = OUTLOOK_DEFAULT_LOOKBACK_DAYS) -> List[Dict[str, Any]]:
        pythoncom.CoInitialize()
        try:
            return self._fetch(max_items, lookback_days)
        finally:
            self.namespace = None
            self.outlook = None
            pythoncom.CoUninitialize()

    def _fetch(self, max_items, lookback_days):
        """
        Lấy các email mới từ Inbox chưa được đồng bộ
        """
        self._connect()
        inbox = self.namespace.GetDefaultFolder(6)  # 6 = olFolderInbox
        items = inbox.Items
        # Sắp xếp email mới nhất lên đầu
        items.Sort("[ReceivedTime]", True)

        cutoff_date = datetime.now() - timedelta(days=lookback_days)
        synced_ids = self.state_db.get_synced_email_ids()
        
        extracted_emails = []
        count = 0

        for item in items:
            if count >= max_items:
                break

            # Kiểm tra xem item có phải là MailItem (Class == 43) không
            try:
                if getattr(item, "Class", None) != 43:
                    continue

                entry_id = str(item.EntryID)
                if entry_id in synced_ids:
                    continue

                received_time = item.ReceivedTime
                # Chuyển đổi timestamp nếu cần
                received_dt = datetime.fromtimestamp(received_time.timestamp()) if hasattr(received_time, "timestamp") else datetime.now()
                
                if received_dt < cutoff_date:
                    # Vì đã sort theo ReceivedTime giảm dần, nếu gặp mail cũ hơn cutoff_date thì dừng
                    break

                subject = str(getattr(item, "Subject", "")) or "(Không có tiêu đề)"
                sender_name = str(getattr(item, "SenderName", ""))
                sender_email = str(getattr(item, "SenderEmailAddress", ""))
                body = str(getattr(item, "Body", ""))

                # Lọc theo từ khóa (nếu có cấu hình)
                if self.keywords:
                    text_to_check = f"{subject} {body}".lower()
                    if not any(kw.lower() in text_to_check for kw in self.keywords):
                        continue

                # Lấy danh sách đính kèm
                attachments_info = []
                attachments = getattr(item, "Attachments", None)
                if attachments and attachments.Count > 0:
                    for i in range(1, attachments.Count + 1):
                        att = attachments.Item(i)
                        attachments_info.append({
                            "index": i,
                            "filename": att.FileName,
                            "size": getattr(att, "Size", 0)
                        })

                email_data = {
                    "entry_id": entry_id,
                    "store_id": str(inbox.StoreID),
                    "subject": subject,
                    "sender_name": sender_name,
                    "sender_email": sender_email,
                    "received_time": received_dt.isoformat(),
                    "body_text": body,
                    "attachments": attachments_info,
                    "importance": int(getattr(item, "Importance", 1)),
                    "conversation_topic": str(getattr(item, "ConversationTopic", subject))
                }

                extracted_emails.append(email_data)
                count += 1

            except Exception as item_err:
                logging.getLogger(__name__).exception("Cannot read Outlook item")
                self.state_db.add_log("OUTLOOK", f"Không đọc được một email: {item_err}", "warning")
                continue

        return extracted_emails
