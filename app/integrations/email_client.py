import imaplib
import smtplib
import email
import re
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


def _find_drafts_folder(mail: imaplib.IMAP4_SSL) -> str:
    """\\Drafts 속성을 가진 Gmail 폴더명 반환."""
    return _find_folder_by_flag(mail, '\\Drafts', default='[Gmail]/Drafts')


def _find_folder_by_flag(mail: imaplib.IMAP4_SSL, flag: str, default: str) -> str:
    """주어진 IMAP 플래그(\\Sent, \\Drafts 등)를 가진 폴더명 반환."""
    _, folders = mail.list()
    for f in folders:
        decoded = f.decode('utf-8', errors='replace')
        if flag in decoded:
            parts = decoded.split('"')
            if len(parts) >= 2:
                return parts[-2]
    return default


# 인용/시그니처 제거 패턴
_QUOTE_HEADER_RE = re.compile(
    r'^(\s*-+\s*Original Message\s*-+|'
    r'\s*From:\s|'
    r'\s*보낸 사람:|'
    r'\s*\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}.*작성|'
    r'\s*On\s.+wrote:\s*$|'
    r'\s*\d{4}년.*작성[:：]?\s*$)',
    re.MULTILINE,
)


def _clean_reply_body(body: str) -> str:
    """본문에서 인용된 원문/시그니처를 제거하고 사용자가 작성한 부분만 반환."""
    if not body:
        return ''

    # 인용 헤더 이후 전부 잘라냄
    m = _QUOTE_HEADER_RE.search(body)
    if m:
        body = body[:m.start()]

    # 라인 단위 후처리: `>` 시작 인용, 시그니처 구분자(--) 이후 제거
    lines = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('>'):
            continue
        if stripped == '--' or stripped == '---':
            break
        lines.append(line)

    # 끝부분의 빈 줄 정리
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines).strip()


_BRAND_KEYWORDS = ('ANTIEGG', 'antiegg', '앤티에그')


def fetch_sent_emails(limit: int = 30, since_uid: str = None) -> list:
    """보낸편지함에서 ANTIEGG 브랜드 회신만 정제해 반환.

    필터:
      - To 가 본인 주소면 스킵 (self-sent 테스트 메일 제외)
      - 본문에 ANTIEGG/앤티에그 키워드 필수 (브랜드 회신만 학습 대상)
      - 정제 후 본문 30자 미만은 스킵

    Args:
        limit: 최근 메일 최대 개수
        since_uid: 이 UID 이후 메일만 (증분 수집용)
    """
    addr, pwd, _ = _creds()
    if not addr or not pwd:
        print('[IMAP] GMAIL_ADDRESS / GMAIL_APP_PASSWORD 미설정 — 스킵')
        return []

    results = []
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(addr, pwd)

        sent_folder = _find_folder_by_flag(mail, '\\Sent', default='[Gmail]/Sent Mail')
        status, _ = mail.select(f'"{sent_folder}"')
        if status != 'OK':
            mail.logout()
            raise RuntimeError(f'보낸편지함 폴더 선택 실패: {sent_folder}')

        # Gmail X-GM-RAW로 서버측 본문 검색 — 첨부 큰 메일 다운로드 회피
        # in:sent + 브랜드 키워드 + 본인 주소로 보낸 self-test 제외
        # imaplib는 ASCII만 받아 한글 검색어 불가 — ANTIEGG(영문)만으로 충분
        gm_query = f'in:sent ANTIEGG -to:{addr}'
        _, uid_data = mail.uid('search', None, 'X-GM-RAW', f'"{gm_query}"')
        uids = uid_data[0].split() if uid_data and uid_data[0] else []

        if since_uid:
            cutoff = int(since_uid)
            uids = [u for u in uids if int(u) > cutoff]
        # 최신 우선 (UID 큰 순) + limit
        uids = list(reversed(uids))[:limit]

        for uid in uids:
            _, data = mail.uid('fetch', uid, '(RFC822)')
            if not data or not data[0]:
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw)

            to_header = _decode_header_str(msg.get('To', ''))
            body_raw = _get_body(msg)
            body_clean = _clean_reply_body(body_raw)
            if len(body_clean) < 30:
                continue

            results.append({
                'uid':         uid.decode(),
                'message_id':  msg.get('Message-ID', '').strip(),
                'in_reply_to': msg.get('In-Reply-To', '').strip(),
                'subject':     _decode_header_str(msg.get('Subject', '')),
                'to':          to_header,
                'date':        msg.get('Date', ''),
                'body':        body_clean,
            })

        mail.logout()
    except Exception as e:
        raise RuntimeError(f'IMAP SENT 오류: {e}')

    return results


def create_draft(to: str, subject: str, body: str) -> None:
    """Gmail 임시보관함에 초안 저장. 발송하지 않음."""
    import time
    import email.utils as eutils

    addr, pwd, _ = _creds()
    if not addr or not pwd:
        raise RuntimeError('GMAIL_ADDRESS / GMAIL_APP_PASSWORD 미설정')

    msg = MIMEMultipart()
    msg['From'] = addr
    msg['To'] = to
    msg['Subject'] = subject
    msg['Date'] = eutils.formatdate(localtime=True)
    msg['Message-ID'] = eutils.make_msgid()
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(addr, pwd)
    drafts = _find_drafts_folder(mail)
    mail.append(
        drafts,
        '\\Draft',
        imaplib.Time2Internaldate(time.time()),
        msg.as_bytes(),
    )
    mail.logout()
