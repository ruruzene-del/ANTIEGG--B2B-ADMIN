import os
import json
import logging
from app.services import scheduler as sched
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import db
from app.services import scheduler as sched
from app.services import ai
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

def _format_activity(activity):
    """activity dict → 사람이 읽는 한 줄 문장."""
    t = activity.get('type', '')
    p = activity.get('payload') or {}
    if t == 'stage_changed':
        return f"Stage {p.get('from','?')} → {p.get('to','?')}"
    if t == 'trigger_fired':
        labels = {
            'reply_send':    '회신',
            'quote_gen':     '견적서',
            'contract_gen':  '계약서',
            'contract_send': '전자계약',
            'knock_send':    '노크',
        }
        label = labels.get(p.get('trigger', ''), p.get('trigger', '?'))
        return f"{label} 트리거 {p.get('from','?')} → {p.get('to','?')}"
    if t == 'signed':
        return "전자서명 완료"
    if t == 'note_added':
        return f"노트: {p.get('text', '')}"
    return t

templates.env.filters['format_activity'] = _format_activity

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

# ── v2: 파이프라인 Phase 그루핑 ────────────────────────────────────────
PHASE_MAP = {
    'REVIEWING':   '새 문의',
    'REPLIED':     '응답·협상',
    'NEGOTIATING': '응답·협상',
    'KNOCK_REPLY': '응답·협상',
    'QUOTED':      '견적·계약',
    'CONTRACTING': '견적·계약',
    'KNOCK_QUOTE': '견적·계약',
    'SIGNED':      '체결',
    'CLOSED_WON':  '체결',
    'CLOSED_LOST': '종료',
}
PHASE_ORDER = ['새 문의', '응답·협상', '견적·계약', '체결']
STAGE_ABBR = {
    'REVIEWING':   'REVIEW.',
    'REPLIED':     'REPLIED',
    'NEGOTIATING': 'NEGOT.',
    'KNOCK_REPLY': 'KNOCK_R.',
    'QUOTED':      'QUOTED',
    'CONTRACTING': 'CONTR.',
    'KNOCK_QUOTE': 'KNOCK_Q.',
    'SIGNED':      'SIGNED',
    'CLOSED_WON':  'CLOSED_W',
    'CLOSED_LOST': 'CLOSED_L',
}

# ── v2: Deal 패널 컨텍스트 ────────────────────────────────────────────
TRIGGER_META = {
    'reply_send':    ('mail',       '회신 초안 → Gmail'),
    'quote_gen':     ('file-text',  '견적서'),
    'contract_gen':  ('clipboard',  '계약서'),
    'contract_send': ('pen-line',   '전자계약'),
    'knock_send':    ('send',       '노크'),
}
TRIGGER_KEYS = ('reply_send', 'quote_gen', 'contract_gen', 'contract_send', 'knock_send')

STAGE_TO_PRIMARY_TRIGGER = {
    'REVIEWING':   'reply_send',
    'REPLIED':     'quote_gen',
    'NEGOTIATING': 'quote_gen',
    'QUOTED':      'contract_gen',
    'CONTRACTING': 'contract_send',
    'KNOCK_REPLY': 'knock_send',
    'KNOCK_QUOTE': 'knock_send',
}

def _trigger_btn_data(deal: dict, key: str, primary_key: str) -> dict:
    icon, base = TRIGGER_META[key]
    status = (deal.get(f'trigger_{key}') or 'IDLE')
    return {
        'key':        key,
        'status':     status,
        'icon':       icon,
        'label':      base,
        'is_primary': (key == primary_key),
    }

def _panel_context(deal: dict) -> dict:
    stage = deal.get('stage') or 'REVIEWING'
    primary_key = STAGE_TO_PRIMARY_TRIGGER.get(stage)

    triggers = {k: _trigger_btn_data(deal, k, primary_key) for k in TRIGGER_KEYS}

    company = deal.get('company') or '(회사명 미상)'
    history = db.get_deals_by_company(company, exclude_deal_id=deal['deal_id'])
    activities = db.get_activities(deal['deal_id'])

    has_draft = any(triggers[k]['status'] == 'DRAFT'
                    for k in ('reply_send', 'contract_send', 'knock_send'))
    has_error = any(t['status'] == 'ERROR' for t in triggers.values())
    show_docs = deal.get('trigger_quote_gen') == 'DONE' or deal.get('trigger_contract_gen') == 'DONE'

    return {
        'deal':         deal,
        'triggers':     triggers,
        'history':      history,
        'activities':   activities,
        'has_draft':    has_draft,
        'has_error':    has_error,
        'show_docs':    show_docs,
        'stage_options': STAGE_OPTIONS,
    }

def _pipeline_marker(deal: dict):
    """파이프라인 카드 dot. None이면 마커 없음."""
    triggers = [
        deal.get('trigger_reply_send'),
        deal.get('trigger_quote_gen'),
        deal.get('trigger_contract_gen'),
        deal.get('trigger_contract_send'),
        deal.get('trigger_knock_send'),
    ]
    if any(t == 'ERROR' for t in triggers):
        return 'critical'
    if any(t == 'DRAFT' for t in triggers):
        return 'attention'
    # 임박 (REPLIED/QUOTED + 6일 무응답)
    if deal.get('stage') in ('REPLIED', 'QUOTED'):
        try:
            updated = datetime.fromisoformat(deal.get('updated_at', ''))
            days = (datetime.now() - updated).days
            if 6 <= days < 7:
                return 'upcoming'
        except Exception:
            pass
    return None

# ── 딜 목록 ──────────────────────────────────────────────────────────────────

ACTIVE_STAGES = {'REVIEWING', 'REPLIED', 'NEGOTIATING', 'QUOTED', 'CONTRACTING', 'SIGNED'}

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
async def pipeline_page(request: Request, show_closed: bool = False):
    """파이프라인 — 4 Phase Kanban + 종료 토글."""
    all_deals = db.get_all_deals()

    phases = {p: [] for p in PHASE_ORDER}
    closed_lost = []

    for d in all_deals:
        stage = d.get('stage') or 'REVIEWING'
        phase = PHASE_MAP.get(stage, '새 문의')
        item = {
            'deal':      d,
            'sub_stage': STAGE_ABBR.get(stage, stage),
            'marker':    _pipeline_marker(d),
        }
        if phase == '종료':
            closed_lost.append(item)
        else:
            phases[phase].append(item)

    for p in PHASE_ORDER:
        phases[p].sort(key=lambda x: x['deal'].get('updated_at') or '', reverse=True)
    closed_lost.sort(key=lambda x: x['deal'].get('updated_at') or '', reverse=True)

    active_count = sum(len(phases[p]) for p in PHASE_ORDER)
    closed_count = len(closed_lost)

    return templates.TemplateResponse('pipeline.html', {
        'request':       request,
        'phase_order':   PHASE_ORDER,
        'phases':        phases,
        'closed_lost':   closed_lost,
        'active_count':  active_count,
        'closed_count':  closed_count,
        'show_closed':   show_closed,
    })

@app.get('/companies', response_class=HTMLResponse)
async def companies_page(request: Request):
    """회사 목록 — 회사명 그룹별 진행중/총/마지막 활동."""
    companies = db.get_companies_summary()
    return templates.TemplateResponse('companies.html', {
        'request':   request,
        'companies': companies,
        'total':     len(companies),
    })

@app.get('/companies/{company:path}', response_class=HTMLResponse)
async def company_detail_page(request: Request, company: str):
    """특정 회사의 모든 딜 (진행중/종료 분리)."""
    deals  = db.get_deals_by_company(company)
    active = [d for d in deals if d.get('stage') not in ('CLOSED_WON', 'CLOSED_LOST')]
    closed = [d for d in deals if d.get('stage') in ('CLOSED_WON', 'CLOSED_LOST')]
    first_at = min((d.get('created_at') for d in deals if d.get('created_at')), default=None)
    last_at  = max((d.get('updated_at') for d in deals if d.get('updated_at')), default=None)
    return templates.TemplateResponse('company_detail.html', {
        'request':    request,
        'company':    company,
        'deals':      deals,
        'active':     active,
        'closed':     closed,
        'first_at':   first_at,
        'last_at':    last_at,
        'stage_abbr': STAGE_ABBR,
    })

@app.get('/search', response_class=HTMLResponse)
async def search(request: Request, q: str = ''):
    """Cmd+K 글로벌 검색. HTMX 청크 반환."""
    q = (q or '').strip()
    if not q:
        return templates.TemplateResponse('search_results.html', {
            'request':    request,
            'q':          '',
            'exact':      None,
            'deals':      [],
            'stage_abbr': STAGE_ABBR,
        })
    result = db.search_deals(q, limit=20)
    exact  = result['exact_deal']
    deals  = result['deals']
    if exact:
        deals = [d for d in deals if d['deal_id'] != exact['deal_id']]
    return templates.TemplateResponse('search_results.html', {
        'request':    request,
        'q':          q,
        'exact':      exact,
        'deals':      deals,
        'stage_abbr': STAGE_ABBR,
    })

# ── 딜 상세 ──────────────────────────────────────────────────────────────────

@app.get('/deals/{deal_id}/panel', response_class=HTMLResponse)
async def deal_panel(request: Request, deal_id: str):
    """슬라이드 패널 HTML 청크 (HTMX swap 대상)."""
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse(
            '<div class="panel-body"><div class="empty">딜을 찾을 수 없습니다</div></div>',
            status_code=404,
        )
    ctx = _panel_context(deal)
    ctx['request'] = request
    return templates.TemplateResponse('deal_panel.html', ctx)

def _hx_or_redirect(request: Request, deal_id: str, toast: dict = None):
    """HTMX 요청이면 갱신된 패널 HTML + HX-Trigger 토스트, 아니면 레거시 redirect."""
    if request.headers.get('HX-Request'):
        deal = db.get_deal(deal_id)
        if not deal:
            return HTMLResponse(
                '<div class="panel-body"><div class="empty">딜을 찾을 수 없습니다</div></div>',
                status_code=404,
            )
        ctx = _panel_context(deal)
        ctx['request'] = request
        response = templates.TemplateResponse('deal_panel.html', ctx)
        if toast:
            response.headers['HX-Trigger'] = json.dumps({'toast': toast})
        return response
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)

def _hx_toast_only(request: Request, toast: dict):
    """패널 재렌더 없이 토스트만 (reply-draft, conditions 저장 등)."""
    if request.headers.get('HX-Request'):
        response = HTMLResponse('', status_code=200)
        response.headers['HX-Trigger'] = json.dumps({'toast': toast})
        return response
    return None

@app.get('/deals/{deal_id}')
async def deal_detail(deal_id: str):
    """직접 URL 접근 시 인박스로 보내며 슬라이드 패널 자동 오픈."""
    if not db.get_deal(deal_id):
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)
    return RedirectResponse(url=f'/?open={deal_id}', status_code=302)

# ── Stage 변경 ────────────────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/stage')
async def update_stage(request: Request, deal_id: str, stage: str = Form(...)):
    old = db.get_deal(deal_id)
    old_stage = (old or {}).get('stage') or 'REVIEWING'
    if old_stage == stage:
        return _hx_or_redirect(request, deal_id)
    db.update_deal(deal_id, {'stage': stage})
    db.log_activity(deal_id, 'stage_changed', {'from': old_stage, 'to': stage})
    return _hx_or_redirect(request, deal_id, toast={
        'message': f'Stage {old_stage} → {stage}',
        'type':    'info',
        'undo':    {'deal_id': deal_id, 'stage': old_stage},
    })

# ── Reply Draft 수정 저장 ─────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/reply-draft')
async def update_reply_draft(request: Request, deal_id: str, reply_draft: str = Form('')):
    db.update_deal(deal_id, {'reply_draft': reply_draft})
    toast_resp = _hx_toast_only(request, {
        'message': '회신 초안 저장됨', 'type': 'success',
    })
    if toast_resp is not None:
        return toast_resp
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)

# ── 딜 컨디션 저장 ────────────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/conditions')
async def update_conditions(
    request: Request,
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
    toast_resp = _hx_toast_only(request, {
        'message': '딜 조건 저장됨', 'type': 'success',
    })
    if toast_resp is not None:
        return toast_resp
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)

# ── 트리거 버튼 ───────────────────────────────────────────────────────────────

@app.post('/deals/{deal_id}/trigger/{trigger_name}')
async def set_trigger(request: Request, deal_id: str, trigger_name: str):
    col = TRIGGER_COL_MAP.get(trigger_name)
    if not col:
        return HTMLResponse('Invalid trigger', status_code=400)
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜 없음', status_code=404)
    old_status = deal.get(col) or 'IDLE'
    db.update_deal(deal_id, {col: 'PENDING'})
    db.log_activity(deal_id, 'trigger_fired', {
        'trigger': col.replace('trigger_', ''),
        'from':    old_status,
        'to':      'PENDING',
    })
    return _hx_or_redirect(request, deal_id)

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
    db.log_activity(deal['deal_id'], 'signed', {'signer': signer_name, 'ip': client_ip})
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

# ── Admin: few-shot 사례 수집 ─────────────────────────────────────────────────

@app.post('/admin/ingest-sent')
async def admin_ingest_sent(background_tasks: BackgroundTasks, limit: int = 10):
    """Gmail SENT few-shot 사례 수집을 백그라운드로 트리거. 즉시 202 반환."""
    def _run():
        try:
            r = ai.ingest_sent_examples(limit=limit)
            logging.info(f'[admin/ingest-sent] {r}')
        except Exception as e:
            logging.error(f'[admin/ingest-sent] 실패: {e}')

    background_tasks.add_task(_run)
    return JSONResponse({'status': 'started', 'limit': limit}, status_code=202)

