import os
import requests
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_DEALS = os.getenv('SLACK_WEBHOOK_DEALS', '')
WEBHOOK_ERRORS = os.getenv('SLACK_WEBHOOK_ERRORS', '')

def _post(webhook_url: str, text: str):
    if not webhook_url:
        print(f'[Slack] Webhook URL 미설정 — {text[:60]}')
        return
    try:
        requests.post(webhook_url, json={'text': text}, timeout=5)
    except Exception as e:
        print(f'[Slack Error] {e}')

def notify_deals(text: str):
    _post(WEBHOOK_DEALS, text)

def notify_errors(text: str):
    _post(WEBHOOK_ERRORS, text)

def notify_new_inquiry(deal_id: str, deal: dict):
    msg = (
        f"🔔 *새 B2B 문의 접수* | {deal_id}\n"
        f"*회사:* {deal.get('company')} | "
        f"*담당자:* {deal.get('contact_name')} ({deal.get('email')})\n"
        f"*유형:* {deal.get('inquiry_type')} | "
        f"*규모:* {deal.get('scale')} | "
        f"*긴급도:* {deal.get('urgency')}\n"
        f"*요약:* {deal.get('summary')}\n"
        f"→ 어드민에서 reply_draft 확인 후 [1차 회신 발송] 버튼"
    )
    notify_deals(msg)

def notify_reply_sent(deal_id: str, company: str):
    notify_deals(f'✉️ *1차 회신 발송 완료* | {deal_id} | {company}')

def notify_knock_needed(deal_id: str, stage: str, company: str):
    msg = (
        f"📨 *노크 메일 대기* | {deal_id} | {stage}\n"
        f"*회사:* {company} | 마지막 연락 후 7일 경과\n"
        f"→ 어드민에서 knock_draft 확인 후 [노크 메일 발송] 버튼"
    )
    notify_deals(msg)

def notify_closed_lost(deal_id: str, company: str):
    notify_deals(f'🔒 *CLOSED_LOST 전환* | {deal_id} | {company}')
