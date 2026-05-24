"""DB-backed runtime settings with .env fallback.

UI에서 편집 가능한 항목만 EDITABLE에 선언. get(key)는 DB → os.getenv → default 순으로
조회하므로 기존 .env가 그대로 폴백으로 살아있다. set_many()는 EDITABLE에 선언된 키만
받아 settings 테이블을 upsert한다.
"""

import os
from datetime import datetime
from dotenv import load_dotenv

import db

load_dotenv()

# (key, label, group)
EDITABLE = [
    ('ANTIEGG_CEO',    '대표자명',       'company'),
    ('ANTIEGG_BIZ_NO', '사업자등록번호', 'company'),
    ('ANTIEGG_PHONE',  '대표 전화',      'company'),
    ('ANTIEGG_EMAIL',  '대표 이메일',    'company'),
    ('ANTIEGG_ADDR',   '주소',           'company'),
    ('DIRECTOR_NAME',  '디렉터명',       'director'),
    ('DIRECTOR_EMAIL', '디렉터 이메일',  'director'),
]
EDITABLE_KEYS = {k for k, *_ in EDITABLE}

GROUP_LABELS = {
    'company':  '회사 정보',
    'director': '디렉터',
}


def get(key: str, default: str = '') -> str:
    with db.get_conn() as conn:
        row = conn.execute(
            'SELECT value FROM settings WHERE key = ?', (key,)
        ).fetchone()
    if row and row['value'] is not None and row['value'] != '':
        return row['value']
    return os.getenv(key, default)


def set_many(items: dict):
    now = datetime.now().isoformat()
    with db.get_conn() as conn:
        for k, v in items.items():
            if k not in EDITABLE_KEYS:
                continue
            conn.execute(
                'INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
                (k, (v or '').strip(), now),
            )


def get_all_editable() -> list:
    """UI 폼 렌더용 — 그룹·라벨·현재 값."""
    return [
        {
            'key':   k,
            'label': label,
            'group': group,
            'value': get(k),
        }
        for k, label, group in EDITABLE
    ]


def grouped_editable() -> list:
    """그룹별로 묶어서 반환. [{'group': 'company', 'label': '회사 정보', 'items': [...]}, ...]"""
    out = []
    seen = []
    for item in get_all_editable():
        g = item['group']
        if g not in seen:
            seen.append(g)
            out.append({'group': g, 'label': GROUP_LABELS.get(g, g), 'items': []})
        out[-1]['items'].append(item)
    return out
