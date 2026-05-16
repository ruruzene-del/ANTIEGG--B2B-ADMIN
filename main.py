import os
import logging
from app.services import scheduler as sched
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import db
from app.services import scheduler as sched
from app.integrations import slack

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

TRIGGER_COL_MAP = {
    'reply-send':    'trigger_reply_send',
    'quote-gen':     'trigger_quote_gen',
    'contract-gen':  'trigger_contract_gen',
    'contract-send': 'trigger_contract_send',
    'knock-send':    'trigger_knock_send',
}

STAGE_OPTIONS = [
    'INQUIRY', 'REVIEWING', 'REPLIED', 'NEGOTIATING',
    'QUOTED', 'CONTRACTING', 'SIGNED',
    'CLOSED_WON', 'KNOCK_REPLY', 'KNOCK_QUOTE', 'CLOSED_LOST',
]

_scheduler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    db.init_db()
    _scheduler = sched.create_scheduler()
    _scheduler.start()
    yield
    if _scheduler:
        _scheduler.shutdown()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory='templates')

# ── v2: Jinja 필터 ──────────────────────────────────────────────────────
def _relative_time(value):
    """ISO 시간 문자열을 상대 시간으로. 예: '2시간 전', '어제', '5/14'"""
    if not value:
        return '—'
    try:
        dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    except Exception:
        return str(value)[:10]
    now = datetime.now()
    diff = now - dt
    secs = diff.total_seconds()
    if secs < 0:
        return dt.strftime('%m/%d')
    if secs < 60:
        return '방금 전'
    if secs < 3600:
        return f'{int(secs // 60)}분 전'
    if dt.date() == now.date():
        return f'오늘 {dt.strftime("%H:%M")}'
    if (now.date() - dt.date()).days == 1:
        return '어제'
    if diff.days < 7:
        return f'{diff.days}일 전'
    return dt.strftime('%m/%d')

templates.env.filters['relative_time'] = _relative_time

# ── v2: 인박스 분류 로직 ──────────────────────────────────────────────
def _classify_inbox_now(deal: dict) -> dict:
    """현재 손대야 할 가장 시급한 이슈로 분류. (dot 색 + 라벨)"""
    triggers = [
        ('reply_send',    '회신',     deal.get('trigger_reply_send')),
        ('quote_gen',     '견적서',   deal.get('trigger_quote_gen')),
        ('contract_gen',  '계약서',   deal.get('trigger_contract_gen')),
        ('contract_send', '전자계약', deal.get('trigger_contract_send')),
        ('knock_send',    '노크 메일', deal.get('trigger_knock_send')),
    ]
    # 1) ERROR
    for _, label, status in triggers:
        if status == 'ERROR':
            return {'dot': 'critical', 'label': f'트리거 오류 — {label} 실패'}
    # 2) DRAFT 검토
    for key, label, status in triggers:
        if status == 'DRAFT' and key in ('reply_send', 'contract_send', 'knock_send'):
            return {'dot': 'attention', 'label': f'{label} 초안 Gmail에 저장됨 — 검토·발송 필요'}
    # 3) 새 문의
    if deal.get('stage') == 'REVIEWING' and not (deal.get('reply_draft') or '').strip():
        return {'dot': 'action', 'label': '새 문의 — 회신 초안 만들기'}
    # 4) 노크 발송 필요
    if deal.get('stage') in ('KNOCK_REPLY', 'KNOCK_QUOTE'):
        return {'dot': 'action', 'label': '노크 메일 발송 필요'}
    return {'dot': 'action', 'label': '확인 필요'}

def _classify_inbox_upcoming(deal: dict) -> dict:
    """6일 무응답 — 노크 임박."""
    stage = deal.get('stage', '')
    return {'dot': 'upcoming', 'label': f'{stage} 후 6일 — 내일 노크 자동 전환 예정'}

# ── 딜 목록 ──────────────────────────────────────────────────────────────────

PIPELINE_STAGES = ['REVIEWING', 'REPLIED', 'NEGOTIATING', 'QUOTED', 'CONTRACTING', 'SIGNED', 'CLOSED_WON']
ACTIVE_STAGES   = {'REVIEWING', 'REPLIED', 'NEGOTIATING', 'QUOTED', 'CONTRACTING', 'SIGNED'}

def _base_ctx(request: Request) -> dict:
    """모든 라우트에 공통으로 넘기는 사이드바용 컨텍스트."""
    return {'request': request, 'stage_counts': db.get_stage_counts()}

@app.get('/', response_class=HTMLResponse)
async def inbox(request: Request):
    """인박스 — '오늘 손댈 것' 중심."""
    stage_counts = db.get_stage_counts()
    active_count = sum(stage_counts.get(s, 0) for s in ACTIVE_STAGES)

    now_deals      = db.get_inbox_now()
    upcoming_deals = db.get_inbox_upcoming()

    now_items = [
        {'deal': d, 'classify': _classify_inbox_now(d)}
        for d in now_deals
    ]
    upcoming_items = [
        {'deal': d, 'classify': _classify_inbox_upcoming(d)}
        for d in upcoming_deals
    ]

    WEEKDAYS_KO = ['월', '화', '수', '목', '금', '토', '일']
    today_dt = datetime.now()
    today_str = today_dt.strftime('%-m월 %-d일') + f' {WEEKDAYS_KO[today_dt.weekday()]}요일'

    return templates.TemplateResponse('inbox.html', {
        'request':        request,
        'today':          today_str,
        'active_count':   active_count,
        'now_items':      now_items,
        'upcoming_items': upcoming_items,
    })

@app.get('/pipeline', response_class=HTMLResponse)
async def pipeline_page(request: Request):
    """v2 파이프라인 stub — I-3에서 풀 구현."""
    stage_counts = db.get_stage_counts()
    active_count = sum(stage_counts.get(s, 0) for s in ACTIVE_STAGES)
    return templates.TemplateResponse('pipeline.html', {
        'request':      request,
        'active_count': active_count,
    })

@app.get('/legacy', response_class=HTMLResponse)
async def dashboard_legacy(request: Request, stage: str = None):
    """레거시 대시보드 (참고용 — I-7에서 제거 예정)."""
    stage_counts = db.get_stage_counts()
    all_deals    = db.get_all_deals()
    action_deals = db.get_action_needed()

    deals = [d for d in all_deals if d['stage'] == stage] if stage else all_deals

    pipeline = [{'stage': s, 'count': stage_counts.get(s, 0)} for s in PIPELINE_STAGES]

    return templates.TemplateResponse('dashboard.html', {
        **_base_ctx(request),
        'deals':        deals,
        'action_deals': action_deals,
        'pipeline':     pipeline,
        'active_stage': stage,
        'stats': {
            'total':        len(all_deals),
            'active':       sum(stage_counts.get(s, 0) for s in ACTIVE_STAGES),
            'closed_won':   stage_counts.get('CLOSED_WON', 0),
            'needs_action': len(action_deals),
        },
    })

# ── 딜 상세 ──────────────────────────────────────────────────────────────────

@app.get('/deals/{deal_id}', response_class=HTMLResponse)
async def deal_detail(request: Request, deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)
    return templates.TemplateResponse(
        'deal_detail.html',
        {**_base_ctx(request), 'deal': deal, 'stage_options': STAGE_OPTIONS},
    )

# ── Stage 변경 ────────────────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/stage')
async def update_stage(deal_id: str, stage: str = Form(...)):
    db.update_deal(deal_id, {'stage': stage})
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)

# ── Reply Draft 수정 저장 ─────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/reply-draft')
async def update_reply_draft(deal_id: str, reply_draft: str = Form(...)):
    db.update_deal(deal_id, {'reply_draft': reply_draft})
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)

# ── 딜 컨디션 저장 ────────────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/conditions')
async def update_conditions(
    deal_id: str,
    cond_service_name: str = Form(''),
    cond_service_desc: str = Form(''),
    cond_unit_price: str = Form(''),
    cond_quantity: str = Form(''),
    cond_payment_terms: str = Form(''),
    cond_delivery_scope: str = Form(''),
    cond_notes: str = Form(''),
    cond_company_addr: str = Form(''),
    cond_company_ceo: str = Form(''),
    cond_company_biz_no: str = Form(''),
    cond_contract_start: str = Form(''),
    cond_contract_end: str = Form(''),
):
    db.update_deal(deal_id, {
        'cond_service_name': cond_service_name,
        'cond_service_desc': cond_service_desc,
        'cond_unit_price': cond_unit_price,
        'cond_quantity': cond_quantity,
        'cond_payment_terms': cond_payment_terms,
        'cond_delivery_scope': cond_delivery_scope,
        'cond_notes': cond_notes,
        'cond_company_addr': cond_company_addr,
        'cond_company_ceo': cond_company_ceo,
        'cond_company_biz_no': cond_company_biz_no,
        'cond_contract_start': cond_contract_start,
        'cond_contract_end': cond_contract_end,
    })
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)

# ── 트리거 버튼 ───────────────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/trigger/{trigger_name}')
async def set_trigger(deal_id: str, trigger_name: str):
    col = TRIGGER_COL_MAP.get(trigger_name)
    if not col:
        return HTMLResponse('Invalid trigger', status_code=400)
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜 없음', status_code=404)
    db.update_deal(deal_id, {col: 'PENDING'})
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)

# ── 모두싸인 Webhook (Phase 4에서 구현) ──────────────────────────────────────

# ── 전자계약 서명 페이지 ──────────────────────────────────────────────────────

@app.get('/sign/{token}', response_class=HTMLResponse)
async def sign_page(request: Request, token: str):
    deal = db.get_deal_by_sign_token(token)
    if not deal:
        return HTMLResponse('유효하지 않은 서명 링크입니다.', status_code=404)
    return templates.TemplateResponse('sign.html', {'request': request, 'deal': deal})

@app.post('/sign/{token}', response_class=HTMLResponse)
async def sign_submit(request: Request, token: str, signer_name: str = Form(...)):
    deal = db.get_deal_by_sign_token(token)
    if not deal:
        return HTMLResponse('유효하지 않은 서명 링크입니다.', status_code=404)
    if deal.get('signed_at'):
        return HTMLResponse('이미 서명이 완료된 계약서입니다.', status_code=410)

    now = datetime.now().isoformat()
    client_ip = request.client.host if request.client else ''
    db.update_deal(deal['deal_id'], {
        'signed_at': now,
        'signed_ip': client_ip,
        'stage': 'SIGNED',
    })
    slack.notify_contract_signed(deal['deal_id'], deal['company'])

    return templates.TemplateResponse('sign.html', {
        'request': request,
        'deal': db.get_deal(deal['deal_id']),
        'signer_name': signer_name,
        'just_signed': True,
    })

@app.get('/preview/quote/{deal_id}', response_class=HTMLResponse)
async def preview_quote(request: Request, deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)

    def _parse(v):
        return int(''.join(c for c in str(v or '0') if c.isdigit()) or '0')

    from datetime import datetime, timedelta
    unit_price = _parse(deal.get('cond_unit_price'))
    quantity   = _parse(deal.get('cond_quantity')) or 1
    subtotal   = unit_price * quantity
    vat        = int(subtotal * 0.1)
    total      = subtotal + vat
    today      = datetime.now()

    return templates.TemplateResponse('quote_preview.html', {
        'request':     request,
        'deal':        deal,
        'unit_price':  f'{unit_price:,}',
        'quantity':    quantity,
        'subtotal':    f'{subtotal:,}',
        'vat':         f'{vat:,}',
        'total':       f'{total:,}',
        'quote_date':  today.strftime('%Y년 %m월 %d일'),
        'valid_until': (today + timedelta(days=30)).strftime('%Y년 %m월 %d일'),
        'ceo':         os.getenv('ANTIEGG_CEO', ''),
        'biz_no':      os.getenv('ANTIEGG_BIZ_NO', ''),
        'phone':       os.getenv('ANTIEGG_PHONE', ''),
        'email':       os.getenv('ANTIEGG_EMAIL', ''),
        'addr':        os.getenv('ANTIEGG_ADDR', ''),
    })


@app.get('/preview/contract/{deal_id}', response_class=HTMLResponse)
async def preview_contract(request: Request, deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)

    def _parse(v):
        return int(''.join(c for c in str(v or '0') if c.isdigit()) or '0')

    from datetime import datetime
    unit_price = _parse(deal.get('cond_unit_price'))
    quantity   = _parse(deal.get('cond_quantity')) or 1
    subtotal   = unit_price * quantity
    vat        = int(subtotal * 0.1)
    total      = subtotal + vat

    return templates.TemplateResponse('contract_preview.html', {
        'request':  request,
        'deal':     deal,
        'subtotal': f'{subtotal:,}',
        'vat':      f'{vat:,}',
        'total':    f'{total:,}',
        'today':    datetime.now().strftime('%Y년 %m월 %d일'),
        'ceo':      os.getenv('ANTIEGG_CEO', ''),
        'biz_no':   os.getenv('ANTIEGG_BIZ_NO', ''),
        'phone':    os.getenv('ANTIEGG_PHONE', ''),
        'email':    os.getenv('ANTIEGG_EMAIL', ''),
        'addr':     os.getenv('ANTIEGG_ADDR', ''),
    })


@app.get('/download/quote/{deal_id}')
async def download_quote(deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜을 찾을 수 없습니다.', status_code=404)
    for v in (3, 2, 1):
        path = deal.get(f'quote_path_v{v}')
        if path and os.path.exists(path):
            return FileResponse(
                path,
                media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                filename=f'{deal_id}_quote.docx',
            )
    return HTMLResponse('견적서 파일을 찾을 수 없습니다.', status_code=404)

@app.get('/download/{token}')
async def download_contract(token: str):
    deal = db.get_deal_by_sign_token(token)
    if not deal:
        return HTMLResponse('유효하지 않은 링크입니다.', status_code=404)
    for v in (3, 2, 1):
        path = deal.get(f'contract_path_v{v}')
        if path and os.path.exists(path):
            return FileResponse(
                path,
                media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                filename=f'{deal["deal_id"]}_contract.docx',
            )
    return HTMLResponse('계약서 파일을 찾을 수 없습니다. 관리자에게 문의하세요.', status_code=404)

