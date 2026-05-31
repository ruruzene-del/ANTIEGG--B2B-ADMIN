"""few-shot 사례 CRUD — reply_examples.json 직접 조작.

ai.ingest_sent_examples()가 자동 추가하고, 어드민에서 수동 추가/삭제. 모든 사례에
id 필드를 부여(없으면 마이그레이션) — 인덱스가 아니라 안정적인 키로 식별.

저장 시 ai._examples_cache를 무효화해서 다음 generate_reply_draft 호출이 새 사례를 본다.
"""

import json
import time
import uuid
from pathlib import Path
from typing import List, Optional

_CONTEXT_DIR = Path(__file__).resolve().parents[2] / 'ai_context'
PATH = _CONTEXT_DIR / 'reply_examples.json'

INQUIRY_TYPES = ['도입문의', '가격문의', '파트너십', '기술문의', '기타']


def _load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text(encoding='utf-8'))
    return {'examples': []}


def _save(data: dict):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    _invalidate_cache()


def _invalidate_cache():
    try:
        from app.services import ai
        ai._examples_cache = None
    except Exception:
        pass


def _ensure_ids(data: dict) -> bool:
    """모든 사례에 id 부여. 변경 있으면 True."""
    changed = False
    for e in data.get('examples', []):
        if e.get('id'):
            continue
        if e.get('source_uid'):
            e['id'] = f'auto_{e["source_uid"]}'
        else:
            e['id'] = f'manual_{uuid.uuid4().hex[:12]}'
        changed = True
    return changed


def list_all() -> List[dict]:
    data = _load()
    if _ensure_ids(data):
        _save(data)
    return list(data.get('examples', []))


def delete_by_id(ex_id: str) -> Optional[dict]:
    data = _load()
    _ensure_ids(data)
    before = data.get('examples', [])
    target = next((e for e in before if e.get('id') == ex_id), None)
    if target is None:
        return None
    data['examples'] = [e for e in before if e.get('id') != ex_id]
    _save(data)
    return target


def update_reply(ex_id: str, reply: str) -> Optional[dict]:
    """사례의 reply 본문만 갱신. 자동 분류 메타는 보존."""
    data = _load()
    _ensure_ids(data)
    target = next((e for e in data.get('examples', []) if e.get('id') == ex_id), None)
    if target is None:
        return None
    target['reply'] = reply.strip()
    target['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    _save(data)
    return target


def get_by_id(ex_id: str) -> Optional[dict]:
    data = _load()
    _ensure_ids(data)
    return next((e for e in data.get('examples', []) if e.get('id') == ex_id), None)


def add_manual(inquiry_type: str, summary: str, contact_name: str, reply: str) -> dict:
    data = _load()
    _ensure_ids(data)
    new_ex = {
        'id':            f'manual_{uuid.uuid4().hex[:12]}',
        'inquiry_type':  inquiry_type.strip() or '기타',
        'summary':       summary.strip(),
        'contact_name':  contact_name.strip() or '미상',
        'reply':         reply.strip(),
        'created_at':    time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    data.setdefault('examples', []).append(new_ex)
    _save(data)
    return new_ex
