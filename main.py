import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import db
import scheduler as sched
import slack

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
):
    db.update_deal(deal_id, {
        'cond_service_name': cond_service_name,
        'cond_service_desc': cond_service_desc,
        'cond_unit_price': cond_unit_price,
        'cond_quantity': cond_quantity,
        'cond_payment_terms': cond_payment_terms,
        'cond_delivery_scope': cond_delivery_scope,
        'cond_notes': cond_notes,
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

@app.post('/webhook/modusign')
async def modusign_webhook(request: Request):
    payload = await request.json()
    if payload.get('event_type') == 'DOCUMENT_COMPLETED':
        doc_id = payload.get('document', {}).get('id', '')
        # Phase 4: deal 조회 → stage=SIGNED → CLOSED_WON
    return {'ok': True}
