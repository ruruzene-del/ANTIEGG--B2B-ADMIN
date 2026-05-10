import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

import os
import db
import email_client
import ai
import slack
import document

APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:8000').rstrip('/')

logger = logging.getLogger(__name__)

def poll_inbox():
    """15분마다: IMAP 폴링 → Ollama 파싱 + 회신초안 → DB 저장 → Slack 알림"""
    logger.info('[poll_inbox] 시작')
    try:
        emails = email_client.fetch_new_emails()
        if not emails:
            logger.info('[poll_inbox] 새 메일 없음')
            return

        for mail in emails:
            try:
                parsed = ai.parse_email(mail['body'])

                # sender에서 이메일 추출 (파싱 실패 보정)
                if parsed.get('email') == '미상':
                    parsed['email'] = mail.get('sender_email', '미상')

                reply_draft = ai.generate_reply_draft(
                    parsed.get('summary', ''),
                    parsed.get('contact_name', ''),
                    parsed.get('inquiry_type', ''),
                )
                parsed['reply_draft'] = reply_draft

                deal_id = db.insert_deal(parsed)
                slack.notify_new_inquiry(deal_id, parsed)
                logger.info(f'[poll_inbox] 저장 완료: {deal_id}')

            except Exception as e:
                logger.error(f'[poll_inbox] 메일 처리 실패: {e}')
                slack.notify_errors(
                    f'🔴 *poll_inbox 에러*\n'
                    f'메일: {mail.get("subject", "")}\n'
                    f'에러: {str(e)}'
                )
    except Exception as e:
        logger.error(f'[poll_inbox] IMAP 연결 실패: {e}')
        slack.notify_errors(f'🔴 *poll_inbox IMAP 실패*\n에러: {str(e)}')

def process_reply_send():
    """5분마다: trigger_reply_send=PENDING → SMTP 발송 → stage=REPLIED"""
    deals = db.get_deals_by_trigger('trigger_reply_send', 'PENDING')
    for deal in deals:
        deal_id = deal['deal_id']
        try:
            db.update_deal(deal_id, {'trigger_reply_send': 'PROCESSING'})
            email_client.send_email(
                to=deal['email'],
                subject=f'Re: {deal["company"]} 문의 답변드립니다',
                body=deal['reply_draft'] or '',
            )
            db.update_deal(deal_id, {
                'trigger_reply_send': 'DONE',
                'stage': 'REPLIED',
            })
            slack.notify_reply_sent(deal_id, deal['company'])
            logger.info(f'[reply_send] 완료: {deal_id}')
        except Exception as e:
            db.update_deal(deal_id, {'trigger_reply_send': 'ERROR'})
            slack.notify_errors(
                f'🔴 *reply_send 에러*\ndeal_id: {deal_id}\n에러: {str(e)}'
            )
            logger.error(f'[reply_send] 실패 {deal_id}: {e}')

def check_no_response():
    """매일 09:00: 7일 무응답 → Ollama 노크초안 생성 → trigger_knock_send=PENDING"""
    deals = db.get_deals_for_knock_check()
    for deal in deals:
        deal_id = deal['deal_id']
        try:
            knock_stage = (
                'KNOCK_REPLY' if deal['stage'] == 'REPLIED' else 'KNOCK_QUOTE'
            )
            knock_draft = ai.generate_knock_draft(
                deal['company'], deal['contact_name'], knock_stage
            )
            db.update_deal(deal_id, {
                'knock_draft': knock_draft,
                'stage': knock_stage,
                'trigger_knock_send': 'PENDING',
            })
            slack.notify_knock_needed(deal_id, knock_stage, deal['company'])
            logger.info(f'[knock_check] 노크 대기: {deal_id}')
        except Exception as e:
            slack.notify_errors(
                f'🔴 *knock_check 에러*\ndeal_id: {deal_id}\n에러: {str(e)}'
            )

def process_knock_send():
    """5분마다: trigger_knock_send=PENDING → SMTP 발송"""
    deals = db.get_deals_by_trigger('trigger_knock_send', 'PENDING')
    for deal in deals:
        deal_id = deal['deal_id']
        try:
            db.update_deal(deal_id, {'trigger_knock_send': 'PROCESSING'})
            email_client.send_email(
                to=deal['email'],
                subject=f'Re: {deal["company"]} 문의 후속 연락드립니다',
                body=deal['knock_draft'] or '',
            )
            db.update_deal(deal_id, {'trigger_knock_send': 'DONE'})
            logger.info(f'[knock_send] 완료: {deal_id}')
        except Exception as e:
            db.update_deal(deal_id, {'trigger_knock_send': 'ERROR'})
            slack.notify_errors(
                f'🔴 *knock_send 에러*\ndeal_id: {deal_id}\n에러: {str(e)}'
            )

def process_quote_gen():
    """5분마다: trigger_quote_gen=PENDING → 견적서 생성 → stage=QUOTED → Slack"""
    deals = db.get_deals_by_trigger('trigger_quote_gen', 'PENDING')
    for deal in deals:
        deal_id = deal['deal_id']
        try:
            db.update_deal(deal_id, {'trigger_quote_gen': 'PROCESSING'})
            col, path = document.generate_quote(deal)
            db.update_deal(deal_id, {
                col: path,
                'trigger_quote_gen': 'DONE',
                'stage': 'QUOTED',
            })
            slack.notify_quote_ready(deal_id, deal['company'], path)
            logger.info(f'[quote_gen] 완료: {deal_id} → {path}')
        except Exception as e:
            db.update_deal(deal_id, {'trigger_quote_gen': 'ERROR'})
            slack.notify_errors(
                f'🔴 *quote_gen 에러*\ndeal_id: {deal_id}\n에러: {str(e)}'
            )
            logger.error(f'[quote_gen] 실패 {deal_id}: {e}')

def process_contract_gen():
    """5분마다: trigger_contract_gen=PENDING → 계약서 생성 → stage=CONTRACTING → Slack"""
    deals = db.get_deals_by_trigger('trigger_contract_gen', 'PENDING')
    for deal in deals:
        deal_id = deal['deal_id']
        try:
            db.update_deal(deal_id, {'trigger_contract_gen': 'PROCESSING'})
            col, path = document.generate_contract(deal)
            db.update_deal(deal_id, {
                col: path,
                'trigger_contract_gen': 'DONE',
                'stage': 'CONTRACTING',
            })
            slack.notify_contract_ready(deal_id, deal['company'], path)
            logger.info(f'[contract_gen] 완료: {deal_id} → {path}')
        except Exception as e:
            db.update_deal(deal_id, {'trigger_contract_gen': 'ERROR'})
            slack.notify_errors(
                f'🔴 *contract_gen 에러*\ndeal_id: {deal_id}\n에러: {str(e)}'
            )
            logger.error(f'[contract_gen] 실패 {deal_id}: {e}')

def _contract_email_body(deal: dict, sign_url: str, download_url: str) -> str:
    from datetime import datetime
    unit_price = int(''.join(c for c in str(deal.get('cond_unit_price', '0')) if c.isdigit()) or '0')
    quantity   = int(''.join(c for c in str(deal.get('cond_quantity', '1')) if c.isdigit()) or '1') or 1
    total      = unit_price * quantity
    vat        = int(total * 0.1)

    return (
        f'{deal.get("contact_name", "담당자")} 님,\n\n'
        f'안녕하세요, ANTIEGG입니다.\n'
        f'아래 계약 내용을 확인하신 후 서명 버튼을 눌러주시기 바랍니다.\n\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'계약번호  : {deal["deal_id"]}\n'
        f'서비스명  : {deal.get("cond_service_name", "")}\n'
        f'계약 기간  : {deal.get("cond_contract_start", "")} ~ {deal.get("cond_contract_end", "")}\n'
        f'계약 금액  : {total + vat:,}원 (VAT 포함)\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
        f'▶ 계약서 확인 및 전자서명:\n{sign_url}\n\n'
        f'▶ 계약서 원본 다운로드:\n{download_url}\n\n'
        f'서명 링크는 1회 사용 후 만료됩니다.\n'
        f'문의사항은 이 메일에 회신 주시면 빠르게 안내드리겠습니다.\n\n'
        f'감사합니다.\nANTIEGG 드림'
    )

def process_contract_send():
    """5분마다: trigger_contract_send=PENDING → 서명 토큰 생성 → 이메일 발송"""
    deals = db.get_deals_by_trigger('trigger_contract_send', 'PENDING')
    for deal in deals:
        deal_id = deal['deal_id']
        try:
            db.update_deal(deal_id, {'trigger_contract_send': 'PROCESSING'})
            token        = db.set_sign_token(deal_id)
            sign_url     = f'{APP_BASE_URL}/sign/{token}'
            download_url = f'{APP_BASE_URL}/download/{token}'
            body = _contract_email_body(deal, sign_url, download_url)
            email_client.send_email(
                to=deal['email'],
                subject=f'[ANTIEGG] 용역 계약서 서명 요청 — {deal_id}',
                body=body,
            )
            db.update_deal(deal_id, {'trigger_contract_send': 'DONE'})
            slack.notify_contract_sent(deal_id, deal['company'])
            logger.info(f'[contract_send] 완료: {deal_id}')
        except Exception as e:
            db.update_deal(deal_id, {'trigger_contract_send': 'ERROR'})
            slack.notify_errors(
                f'🔴 *contract_send 에러*\ndeal_id: {deal_id}\n에러: {str(e)}'
            )
            logger.error(f'[contract_send] 실패 {deal_id}: {e}')

def check_closed_lost():
    """매일 09:00: KNOCK 후 7일 추가 무응답 → CLOSED_LOST"""
    deals = db.get_deals_for_closed_lost()
    for deal in deals:
        deal_id = deal['deal_id']
        db.update_deal(deal_id, {'stage': 'CLOSED_LOST'})
        slack.notify_closed_lost(deal_id, deal['company'])
        logger.info(f'[closed_lost] 전환: {deal_id}')

def daily_reminder():
    """매일 09:00 + 18:00: 미발송 건 Slack 재알림"""
    deals = db.get_deals_by_trigger('trigger_reply_send', 'PENDING')
    for deal in deals:
        slack.notify_deals(
            f'⏰ *회신 미발송 리마인더* | {deal["deal_id"]} | {deal["company"]}'
        )

def create_scheduler() -> BackgroundScheduler:
    db.init_db()
    scheduler = BackgroundScheduler(
        job_defaults={
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': 300,
        }
    )
    scheduler.add_job(poll_inbox,         IntervalTrigger(minutes=15), id='poll_inbox')
    scheduler.add_job(process_reply_send, IntervalTrigger(minutes=5),  id='process_reply_send')
    scheduler.add_job(process_quote_gen,     IntervalTrigger(minutes=5),  id='process_quote_gen')
    scheduler.add_job(process_contract_gen,  IntervalTrigger(minutes=5),  id='process_contract_gen')
    scheduler.add_job(process_contract_send, IntervalTrigger(minutes=5),  id='process_contract_send')
    scheduler.add_job(process_knock_send, IntervalTrigger(minutes=5),  id='process_knock_send')
    scheduler.add_job(check_no_response,  CronTrigger(hour=9, minute=0),  id='check_no_response')
    scheduler.add_job(check_closed_lost,  CronTrigger(hour=9, minute=0),  id='check_closed_lost')
    scheduler.add_job(daily_reminder,     CronTrigger(hour=9, minute=0),  id='reminder_morning')
    scheduler.add_job(daily_reminder,     CronTrigger(hour=18, minute=0), id='reminder_evening')
    return scheduler
