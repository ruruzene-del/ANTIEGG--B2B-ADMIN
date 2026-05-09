import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), 'b2b.db')

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
            scale               TEXT,
            urgency             TEXT,
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
        """)

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
            inquiry_type, service_interest, scale, urgency,
            summary, reply_draft, created_at, updated_at
        ) VALUES (
            :deal_id, :company, :contact_name, :contact_title, :contact_phone, :email,
            :inquiry_type, :service_interest, :scale, :urgency,
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

def get_deals_by_trigger(trigger_col: str, status: str = 'PENDING') -> list:
    with get_conn() as conn:
        rows = conn.execute(
            f'SELECT * FROM deals WHERE {trigger_col} = ?', (status,)
        ).fetchall()
    return [dict(r) for r in rows]

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
