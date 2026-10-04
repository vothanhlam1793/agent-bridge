"""Server protocol 1.0: authenticated, lightweight readiness handshake."""
import requests

VERSION = '1.0'

def handshake(settings):
    with requests.get(settings['server_url'].rstrip('/') + '/api/v1/handshake',
                      headers={'Authorization': 'Bearer ' + settings['api_key'],
                               'X-Client-ID': settings['client_id']}, timeout=(3, 5)) as response:
        response.raise_for_status()
        payload = response.json()
    required = {'files.upload', 'reports.download'}
    if settings.get('outlook_enabled') == 'true':
        required.add('emails.upsert')
    if (payload.get('protocol_version') != VERSION or payload.get('status') != 'ready'
            or payload.get('client_id') != settings['client_id']
            or not required.issubset(set(payload.get('capabilities', [])))):
        raise ValueError('Server chưa sẵn sàng hoặc không hỗ trợ giao thức Bridge 1.0')
    return payload
