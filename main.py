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

# ── 딜 목록 ──────────────────────────────────────────────────────────────────

PIPELINE_STAGES = ['REVIEWING', 'REPLIED', 'NEGOTIATING', 'QUOTED', 'CONTRACTING', 'SIGNED', 'CLOSED_WON']
ACTIVE_STAGES   = {'REVIEWING', 'REPLIED', 'NEGOTIATING', 'QUOTED', 'CONTRACTING', 'SIGNED'}

def _base_ctx(request: Request) -> dict:
    """모든 라우트에 공통으로 넘기는 사이드바용 컨텍스트."""
    return {'request': request, 'stage_counts': db.get_stage_counts()}

@app.get('/', response_class=HTMLResponse)
async def dashboard(request: Request, stage: str = None):
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

