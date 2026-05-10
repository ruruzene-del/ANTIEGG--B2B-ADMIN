import imaplib
import smtplib
import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
import os
from dotenv import load_dotenv

load_dotenv()

def _creds() -> tuple[str, str, str]:
    load_dotenv(override=True)
    return (
        os.getenv('GMAIL_ADDRESS', ''),
        os.getenv('GMAIL_APP_PASSWORD', ''),
        os.getenv('B2B_LABEL', 'B2B_INQUIRY'),
    )

def _decode_header_str(value: str) -> str:
    parts = decode_header(value or '')
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or 'utf-8', errors='replace'))
        else:
            result.append(part)
    return ''.join(result)

def _extract_email_address(sender: str) -> str:
    if '<' in sender:
        return sender.split('<')[1].rstrip('>')
    return sender.strip()

def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            return payload.decode(charset, errors='replace')
    return ''

def fetch_new_emails() -> list:
    """B2B_INQUIRY 레이블의 UNSEEN 메일 목록 반환."""
    addr, pwd, label = _creds()
    if not addr or not pwd:
        print('[IMAP] GMAIL_ADDRESS / GMAIL_APP_PASSWORD 미설정 — 스킵')
        return []

    results = []
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(addr, pwd)

        status, _ = mail.select(f'"{label}"')
        if status != 'OK':
            print(f'[IMAP] 레이블 "{label}" 없음 — INBOX 사용')
            mail.select('INBOX')

        _, uid_data = mail.uid('search', None, 'UNSEEN')
        uids = uid_data[0].split()

        for uid in uids:
            _, data = mail.uid('fetch', uid, '(RFC822)')
            raw = data[0][1]
            msg = email.message_from_bytes(raw)

            sender = msg.get('From', '')
            results.append({
                'uid': uid.decode(),
                'subject': _decode_header_str(msg.get('Subject', '')),
                'sender': sender,
                'sender_email': _extract_email_address(sender),
                'body': _get_body(msg),
            })

            mail.uid('store', uid, '+FLAGS', '\\Seen')

        mail.logout()
    except Exception as e:
        raise RuntimeError(f'IMAP 오류: {e}')

    return results

def send_email(to: str, subject: str, body: str):
    addr, pwd, _ = _creds()
    if not addr or not pwd:
        raise RuntimeError('GMAIL_ADDRESS / GMAIL_APP_PASSWORD 미설정')

    msg = MIMEMultipart()
    msg['From'] = addr
    msg['To'] = to
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(addr, pwd)
        server.send_message(msg)
