"""Outlook command execution in an isolated, deadline-bound process."""
import base64
import multiprocessing
from pathlib import Path


def execute_outlook(command, settings):
    import pythoncom
    import win32com.client
    import win32timezone  # Required by frozen COM datetime conversion.
    pythoncom.CoInitialize()
    try:
        app = win32com.client.Dispatch('Outlook.Application')
        namespace = app.GetNamespace('MAPI')
        kind, payload = command['type'], command['payload']
        if kind == 'outlook.calendar.read':
            from .outlook_calendar import collect
            return collect(namespace, payload)
        if kind == 'outlook.calendar.create':
            from .outlook_calendar import create
            return create(namespace, command)
        if kind == 'outlook.attachment':
            import tempfile
            message = namespace.GetItemFromID(payload['entry_id'], payload.get('store_id', ''))
            attachment = message.Attachments.Item(int(payload['index']))
            if attachment.Size > 10 * 1024 * 1024:
                raise ValueError('Attachment exceeds 10 MiB command limit')
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'attachment.bin'
                attachment.SaveAsFile(str(target))
                content = target.read_bytes()
            return {'filename': attachment.FileName, 'content_base64': base64.b64encode(content).decode(),
                    'size': len(content)}
        if kind == 'outlook.reply':
            original = namespace.GetItemFromID(payload['entry_id'], payload.get('store_id', ''))
            mail = original.Reply()
            mail.Body = payload['body'] + '\r\n\r\n' + mail.Body
        else:
            mail = app.CreateItem(0)
            mail.To = payload['to']
            mail.CC = payload.get('cc', '')
            mail.BCC = payload.get('bcc', '')
            mail.Subject = payload['subject']
            mail.Body = payload['body']
        account = payload.get('account')
        if account:
            selected = next((a for a in namespace.Accounts if a.SmtpAddress.lower() == account.lower()), None)
            if selected is None:
                raise ValueError('Requested Outlook account is unavailable')
            mail.SendUsingAccount = selected
        for name in payload.get('attachments', []):
            root = Path(settings['reports_download_folder']).resolve()
            target = (root / name).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError('Attachment must be an existing file in the report download folder')
            mail.Attachments.Add(str(target))
        if not mail.Recipients.ResolveAll():
            raise ValueError('Outlook could not resolve recipients')
        marker = mail.UserProperties.Add('PMBridgeCommandID', 1)
        marker.Value = command['id']
        mail.Save()
        entry_id = str(mail.EntryID)
        if kind == 'outlook.draft' or (kind == 'outlook.reply' and payload.get('draft', False)):
            return {'outcome': 'draft_saved', 'entry_id': entry_id}
        mail.Send()
        return {'outcome': 'submitted_to_outlook', 'draft_entry_id': entry_id}
    finally:
        pythoncom.CoUninitialize()


def _child(channel, command, settings):
    try:
        channel.send({'status': 'succeeded', 'result': execute_outlook(command, settings)})
    except Exception as error:
        # COM may fail after accepting Send. Do not assume an exception means not sent.
        channel.send({'status': 'uncertain', 'result': {'error': str(error)}})
    finally:
        channel.close()


def run_isolated(command, settings, timeout=60):
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(sender, command, settings), daemon=True)
    process.start()
    sender.close()
    try:
        if receiver.poll(timeout):
            try:
                return receiver.recv()
            except EOFError:
                pass
        return {'status': 'uncertain', 'result': {'error': 'Outlook worker timed out or exited; do not automatically resend'}}
    finally:
        receiver.close()
        process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(3)
