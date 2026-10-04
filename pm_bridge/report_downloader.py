"""Download atomically and retry acknowledgements on subsequent cycles."""
import hashlib
import uuid
from pathlib import Path
from urllib.parse import quote
import requests
from .state_db import BridgeStateDB

class ReportDownloader:
    def __init__(self, state_db=None, download_dir=None, settings=None):
        self.state_db = state_db or BridgeStateDB()
        self.settings = settings or self.state_db.get_all_settings()
        self.download_dir = Path(download_dir or self.settings['reports_download_folder']).resolve()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.url = self.settings['server_url'].rstrip('/') + '/api/v1/reports'
        self.client = self.settings['client_id']
        self.headers = {'Authorization': 'Bearer ' + self.settings['api_key'], 'X-Client-ID': self.client}

    def fetch_and_download_reports(self):
        downloaded = []
        with requests.get(self.url + '/pending', params={'client_id': self.client},
                          headers=self.headers, timeout=(3, 15)) as response:
            response.raise_for_status()
            reports = response.json()['reports']
        for report in reports:
            report_id = str(report['id'])
            name = report['filename']
            if not name or name in ('.', '..') or any(c in name for c in '/\\:<>"|?*') or name.endswith((' ', '.')):
                raise ValueError('Tên báo cáo không hợp lệ')
            if not self.state_db.is_report_downloaded(report_id):
                target = self.download_dir / name
                if target.exists():
                    target = target.with_name(f'{target.stem}_{uuid.uuid4().hex[:10]}{target.suffix}')
                partial = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
                digest = hashlib.sha256()
                try:
                    with requests.get(self.url + '/download/' + quote(report_id, safe=''),
                                      headers=self.headers, stream=True, timeout=(3, 60)) as response:
                        response.raise_for_status()
                        with partial.open('xb') as stream:
                            for chunk in response.iter_content(65536):
                                stream.write(chunk)
                                digest.update(chunk)
                    if report.get('sha256') and digest.hexdigest() != report['sha256']:
                        raise ValueError('Checksum báo cáo không khớp')
                    partial.rename(target)
                    self.state_db.mark_report_downloaded(report_id, target.name, str(target))
                    downloaded.append(target)
                finally:
                    partial.unlink(missing_ok=True)
            with requests.post(self.url + '/confirm', json={'report_id': report_id, 'client_id': self.client},
                               headers=self.headers, timeout=(3, 15)) as response:
                response.raise_for_status()
        return downloaded
