import sqlite3
import os
import json
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), 'b2b.db')

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # busy_timeout은 per-connection이라 매번 설정 (journal_mode·synchronous는 init_db에서 DB 파일에 영구 저장)
    conn.execute('PRAGMA busy_timeout = 5000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        # WAL 모드 — 읽기·쓰기 동시성 향상, scheduler 5분 쓰기 × 웹 요청 동시 접근 시 락 충돌 완화
        # synchronous=NORMAL — WAL과 함께 쓰면 안전(크래시 시 최근 커밋 일부 손실 가능, 손상 없음)
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = NORMAL')
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS deals (
            deal_id             TEXT PRIMARY KEY,
            company             TEXT,
            contact_name        TEXT,
            contact_title       TEXT,
            contact_phone       TEXT,
            email               TEXT,
            inquiry_type        TEXT,
            service_interest    TEXT,
            stage               TEXT DEFAULT 'REVIEWING',
            summary             TEXT,
            reply_draft         TEXT,
            knock_draft         TEXT,
            cond_service_name   TEXT,
            cond_service_desc   TEXT,
            cond_unit_price     TEXT,
            cond_quantity       TEXT,
            cond_payment_terms  TEXT,
            cond_delivery_scope TEXT,
            cond_notes          TEXT,
            quote_path_v1       TEXT,
            quote_path_v2       TEXT,
            quote_path_v3       TEXT,
            contract_path_v1    TEXT,
            contract_path_v2    TEXT,
            contract_path_v3    TEXT,
            modusign_doc_id     TEXT,
            cond_company_addr   TEXT,
            cond_company_ceo    TEXT,
            cond_company_biz_no TEXT,
            cond_contract_start TEXT,
            cond_contract_end   TEXT,
            sign_token          TEXT,
            signed_at           TEXT,
            signed_ip           TEXT,
            trigger_reply_send      TEXT DEFAULT 'IDLE',
            trigger_quote_gen       TEXT DEFAULT 'IDLE',
            trigger_contract_gen    TEXT DEFAULT 'IDLE',
            trigger_contract_send   TEXT DEFAULT 'IDLE',
            trigger_knock_send      TEXT DEFAULT 'IDLE',
            created_at          TEXT,
            updated_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS counter (
            year_month   TEXT PRIMARY KEY,
            last_number  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS activities (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id      TEXT NOT NULL,
            type         TEXT NOT NULL,
            payload      TEXT,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activities_deal_id ON activities(deal_id);
        CREATE INDEX IF NOT EXISTS idx_activities_created_at ON activities(created_at DESC);

        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS errors (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            level        TEXT NOT NULL,
            logger_name  TEXT,
            message      TEXT,
            traceback    TEXT,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_errors_created_at ON errors(created_at DESC);
        """)
        for col in [
            'cond_company_addr', 'cond_company_ceo', 'cond_company_biz_no',
            'cond_contract_start', 'cond_contract_end',
            'sign_token', 'signed_at', 'signed_ip',
        ]:
            try:
                conn.execute(f'ALTER TABLE deals ADD COLUMN {col} TEXT')
            except Exception:
                pass

def generate_deal_id() -> str:
    ym = datetime.now().strftime('%Y%m')
    with get_conn() as conn:
        row = conn.execute(
            'SELECT last_number FROM counter WHERE year_month = ?', (ym,)
        ).fetchone()
        if row:
            n = row['last_number'] + 1
            conn.execute(
                'UPDATE counter SET last_number = ? WHERE year_month = ?', (n, ym)
            )
        else:
            n = 1
            conn.execute(
                'INSERT INTO counter (year_month, last_number) VALUES (?, ?)', (ym, n)
            )
    return f'AE-{ym}-{n:03d}'

def insert_deal(deal: dict) -> str:
    deal_id = generate_deal_id()
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
        INSERT INTO deals (
            deal_id, company, contact_name, contact_title, contact_phone, email,
            inquiry_type, service_interest,
            summary, reply_draft, created_at, updated_at
        ) VALUES (
            :deal_id, :company, :contact_name, :contact_title, :contact_phone, :email,
            :inquiry_type, :service_interest,
            :summary, :reply_draft, :created_at, :updated_at
        )
        """, {
            **deal,
            'deal_id': deal_id,
            'created_at': now,
            'updated_at': now,
        })
    return deal_id

def get_all_deals() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM deals ORDER BY created_at DESC'
        ).fetchall()
    return [dict(r) for r in rows]

def get_deal(deal_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM deals WHERE deal_id = ?', (deal_id,)
        ).fetchone()
    return dict(row) if row else None

def update_deal(deal_id: str, fields: dict):
    fields = {**fields, 'updated_at': datetime.now().isoformat()}
    set_clause = ', '.join(f'{k} = :{k}' for k in fields)
    with get_conn() as conn:
        conn.execute(
            f'UPDATE deals SET {set_clause} WHERE deal_id = :deal_id',
            {**fields, 'deal_id': deal_id}
        )

def delete_deal(deal_id: str) -> bool:
    """딜 + 연결된 activities 영구 삭제. 존재했으면 True."""
    with get_conn() as conn:
        cur = conn.execute('DELETE FROM deals WHERE deal_id = ?', (deal_id,))
        conn.execute('DELETE FROM activities WHERE deal_id = ?', (deal_id,))
        return cur.rowcount > 0

def get_deals_by_trigger(trigger_col: str, status: str = 'PENDING') -> list:
    with get_conn() as conn:
        rows = conn.execute(
            f'SELECT * FROM deals WHERE {trigger_col} = ?', (status,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_stage_counts() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT stage, COUNT(*) as cnt FROM deals GROUP BY stage'
        ).fetchall()
    return {r['stage']: r['cnt'] for r in rows}

def get_action_needed() -> list:
    """액션이 필요한 딜: REVIEWING, 노크 미발송, trigger ERROR/PENDING"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM deals
            WHERE stage = 'REVIEWING'
               OR (stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE') AND trigger_knock_send NOT IN ('DONE', 'PROCESSING'))
               OR trigger_reply_send   IN ('PENDING', 'ERROR')
               OR trigger_quote_gen    IN ('PENDING', 'ERROR')
               OR trigger_contract_gen IN ('PENDING', 'ERROR')
               OR trigger_contract_send IN ('PENDING', 'ERROR')
               OR trigger_knock_send   IN ('PENDING', 'ERROR')
            ORDER BY created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]

def set_sign_token(deal_id: str) -> str:
    import uuid
    token = str(uuid.uuid4())
    update_deal(deal_id, {'sign_token': token})
    return token

def get_deal_by_sign_token(token: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM deals WHERE sign_token = ?', (token,)
        ).fetchone()
    return dict(row) if row else None

def get_deal_by_modusign_id(doc_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM deals WHERE modusign_doc_id = ?', (doc_id,)
        ).fetchone()
    return dict(row) if row else None

def get_deals_for_knock_check() -> list:
    """stage가 REPLIED 또는 QUOTED이고 7일 이상 updated_at이 없는 딜"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM deals
            WHERE stage IN ('REPLIED', 'QUOTED')
            AND trigger_knock_send = 'IDLE'
            AND julianday('now') - julianday(updated_at) >= 7
        """).fetchall()
    return [dict(r) for r in rows]

def get_deals_for_closed_lost() -> list:
    """stage가 KNOCK_REPLY 또는 KNOCK_QUOTE이고 7일 이상 updated_at이 없는 딜"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM deals
            WHERE stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE')
            AND julianday('now') - julianday(updated_at) >= 7
        """).fetchall()
    return [dict(r) for r in rows]

# ── v2: 인박스 액션 정렬 ────────────────────────────────────────────────
def get_inbox_now() -> list:
    """지금 해야 함: REVIEWING(초안 미작성), DRAFT 검토, 노크 발송, 트리거 ERROR"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM deals
            WHERE (stage = 'REVIEWING' AND (reply_draft IS NULL OR reply_draft = ''))
               OR stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE')
               OR trigger_reply_send    IN ('DRAFT', 'ERROR')
               OR trigger_quote_gen     = 'ERROR'
               OR trigger_contract_gen  = 'ERROR'
               OR trigger_contract_send IN ('DRAFT', 'ERROR')
               OR trigger_knock_send    IN ('DRAFT', 'ERROR')
            ORDER BY updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]

def get_inbox_upcoming() -> list:
    """임박(D-1): REPLIED/QUOTED + 6일 무응답 + 노크 미발송"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM deals
            WHERE stage IN ('REPLIED', 'QUOTED')
            AND trigger_knock_send = 'IDLE'
            AND julianday('now') - julianday(updated_at) >= 6
            AND julianday('now') - julianday(updated_at) < 7
            ORDER BY updated_at ASC
        """).fetchall()
    return [dict(r) for r in rows]

# ── v2: 회사 lookup ─────────────────────────────────────────────────────
def get_companies_summary() -> list:
    """회사명 그룹별 집계 (이력 조회용, 객체화 없음)"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(NULLIF(company, ''), '(회사명 미상)') AS company,
                COUNT(*) AS total,
                SUM(CASE WHEN stage NOT IN ('CLOSED_WON','CLOSED_LOST') THEN 1 ELSE 0 END) AS active,
                MAX(updated_at) AS last_activity
            FROM deals
            GROUP BY COALESCE(NULLIF(company, ''), '(회사명 미상)')
            ORDER BY last_activity DESC
        """).fetchall()
    return [dict(r) for r in rows]

def get_deals_by_company(company: str, exclude_deal_id: str = None) -> list:
    """특정 회사명의 딜 리스트. exclude_deal_id 주면 그 딜은 제외 (같은 회사 이력 표시용)"""
    with get_conn() as conn:
        if exclude_deal_id:
            rows = conn.execute("""
                SELECT * FROM deals
                WHERE COALESCE(NULLIF(company, ''), '(회사명 미상)') = ?
                  AND deal_id != ?
                ORDER BY created_at DESC
            """, (company, exclude_deal_id)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM deals
                WHERE COALESCE(NULLIF(company, ''), '(회사명 미상)') = ?
                ORDER BY created_at DESC
            """, (company,)).fetchall()
    return [dict(r) for r in rows]

# ── v2: Cmd+K 검색 ──────────────────────────────────────────────────────
def search_deals(query: str, limit: int = 20) -> dict:
    """회사/담당자/이메일/deal_id/summary 부분일치 검색. 타입별로 분리해 반환."""
    q = f'%{query.lower()}%'
    with get_conn() as conn:
        # 정확 deal_id 일치 먼저
        exact = conn.execute(
            "SELECT * FROM deals WHERE LOWER(deal_id) = LOWER(?) LIMIT 1",
            (query,)
        ).fetchone()
        deal_rows = conn.execute("""
            SELECT * FROM deals
            WHERE LOWER(deal_id) LIKE ?
               OR LOWER(company) LIKE ?
               OR LOWER(contact_name) LIKE ?
               OR LOWER(email) LIKE ?
               OR LOWER(summary) LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (q, q, q, q, q, limit)).fetchall()
    return {
        'exact_deal': dict(exact) if exact else None,
        'deals': [dict(r) for r in deal_rows],
    }

# ── v2: Activity 로그 ───────────────────────────────────────────────────
def log_activity(deal_id: str, type_: str, payload: dict = None):
    """활동 1건 기록. type ∈ {stage_changed, trigger_fired, signed, note_added}"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO activities (deal_id, type, payload, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            deal_id,
            type_,
            json.dumps(payload, ensure_ascii=False) if payload else None,
            datetime.now().isoformat(),
        ))

def get_activities(deal_id: str, limit: int = 50) -> list:
    """딜의 활동 로그 (최신순)"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM activities
            WHERE deal_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (deal_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get('payload'):
            try:
                d['payload'] = json.loads(d['payload'])
            except Exception:
                pass
        out.append(d)
    return out

# ── 에러 로그 ───────────────────────────────────────────────────────────────
def insert_error(level: str, logger_name: str, message: str, tb: str = None):
    """logging.Handler에서 호출. 절대 raise하면 안 됨."""
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO errors (level, logger_name, message, traceback, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (level, logger_name, message, tb, datetime.now().isoformat()),
        )

def get_recent_errors(limit: int = 50) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM errors ORDER BY created_at DESC LIMIT ?', (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

def count_errors_since(iso_ts: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            'SELECT COUNT(*) FROM errors WHERE created_at >= ?', (iso_ts,)
        ).fetchone()[0]

def purge_old_errors(days: int = 30) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = conn.execute('DELETE FROM errors WHERE created_at < ?', (cutoff,))
        return cur.rowcount
