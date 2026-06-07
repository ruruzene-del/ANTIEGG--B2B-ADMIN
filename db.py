import os
import json
from datetime import datetime, timedelta
from contextlib import contextmanager
import psycopg2
import psycopg2.extras


@contextmanager
def get_conn():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
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
            notion_page_id      TEXT,
            created_at          TEXT,
            updated_at          TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS counter (
            year_month   TEXT PRIMARY KEY,
            last_number  INTEGER DEFAULT 0
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id           SERIAL PRIMARY KEY,
            deal_id      TEXT NOT NULL,
            type         TEXT NOT NULL,
            payload      TEXT,
            created_at   TEXT NOT NULL
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_deal_id ON activities(deal_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_created_at ON activities(created_at DESC)")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id           SERIAL PRIMARY KEY,
            level        TEXT NOT NULL,
            logger_name  TEXT,
            message      TEXT,
            traceback    TEXT,
            created_at   TEXT NOT NULL
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_errors_created_at ON errors(created_at DESC)")
        cur.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS notion_page_id TEXT")


def generate_deal_id() -> str:
    ym = datetime.now().strftime('%Y%m')
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'SELECT last_number FROM counter WHERE year_month = %s FOR UPDATE', (ym,)
        )
        row = cur.fetchone()
        if row:
            n = row['last_number'] + 1
            cur.execute(
                'UPDATE counter SET last_number = %s WHERE year_month = %s', (n, ym)
            )
        else:
            n = 1
            cur.execute(
                'INSERT INTO counter (year_month, last_number) VALUES (%s, %s)', (ym, n)
            )
    return f'AE-{ym}-{n:03d}'


def insert_deal(deal: dict) -> str:
    deal_id = generate_deal_id()
    now = datetime.now().isoformat()
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
        INSERT INTO deals (
            deal_id, company, contact_name, contact_title, contact_phone, email,
            inquiry_type, service_interest,
            summary, reply_draft, created_at, updated_at
        ) VALUES (
            %(deal_id)s, %(company)s, %(contact_name)s, %(contact_title)s, %(contact_phone)s, %(email)s,
            %(inquiry_type)s, %(service_interest)s,
            %(summary)s, %(reply_draft)s, %(created_at)s, %(updated_at)s
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
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals ORDER BY created_at DESC')
        return [dict(r) for r in cur.fetchall()]


def get_deal(deal_id: str) -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals WHERE deal_id = %s', (deal_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def update_deal(deal_id: str, fields: dict):
    fields = {**fields, 'updated_at': datetime.now().isoformat()}
    set_clause = ', '.join(f'{k} = %({k})s' for k in fields)
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            f'UPDATE deals SET {set_clause} WHERE deal_id = %(deal_id)s',
            {**fields, 'deal_id': deal_id}
        )


def delete_deal(deal_id: str) -> bool:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('DELETE FROM deals WHERE deal_id = %s', (deal_id,))
        rowcount = cur.rowcount
        cur.execute('DELETE FROM activities WHERE deal_id = %s', (deal_id,))
        return rowcount > 0


def get_deals_by_trigger(trigger_col: str, status: str = 'PENDING') -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(f'SELECT * FROM deals WHERE {trigger_col} = %s', (status,))
        return [dict(r) for r in cur.fetchall()]


def get_stage_counts() -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT stage, COUNT(*) as cnt FROM deals GROUP BY stage')
        return {r['stage']: r['cnt'] for r in cur.fetchall()}


def get_action_needed() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE stage = 'REVIEWING'
               OR (stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE') AND trigger_knock_send NOT IN ('DONE', 'PROCESSING'))
               OR trigger_reply_send   IN ('PENDING', 'ERROR')
               OR trigger_quote_gen    IN ('PENDING', 'ERROR')
               OR trigger_contract_gen IN ('PENDING', 'ERROR')
               OR trigger_contract_send IN ('PENDING', 'ERROR')
               OR trigger_knock_send   IN ('PENDING', 'ERROR')
            ORDER BY created_at DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def set_sign_token(deal_id: str) -> str:
    import uuid
    token = str(uuid.uuid4())
    update_deal(deal_id, {'sign_token': token})
    return token


def get_deal_by_sign_token(token: str) -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals WHERE sign_token = %s', (token,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_deal_by_modusign_id(doc_id: str) -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals WHERE modusign_doc_id = %s', (doc_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_deals_for_knock_check() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE stage IN ('REPLIED', 'QUOTED')
            AND trigger_knock_send = 'IDLE'
            AND NOW() - updated_at::timestamp >= INTERVAL '7 days'
        """)
        return [dict(r) for r in cur.fetchall()]


def get_deals_for_closed_lost() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE')
            AND NOW() - updated_at::timestamp >= INTERVAL '7 days'
        """)
        return [dict(r) for r in cur.fetchall()]


def get_inbox_now() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE (stage = 'REVIEWING' AND (reply_draft IS NULL OR reply_draft = ''))
               OR stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE')
               OR trigger_reply_send    IN ('DRAFT', 'ERROR')
               OR trigger_quote_gen     = 'ERROR'
               OR trigger_contract_gen  = 'ERROR'
               OR trigger_contract_send IN ('DRAFT', 'ERROR')
               OR trigger_knock_send    IN ('DRAFT', 'ERROR')
            ORDER BY updated_at DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_inbox_upcoming() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE stage IN ('REPLIED', 'QUOTED')
            AND trigger_knock_send = 'IDLE'
            AND NOW() - updated_at::timestamp >= INTERVAL '6 days'
            AND NOW() - updated_at::timestamp < INTERVAL '7 days'
            ORDER BY updated_at ASC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_companies_summary() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT
                COALESCE(NULLIF(company, ''), '(회사명 미상)') AS company,
                COUNT(*) AS total,
                SUM(CASE WHEN stage NOT IN ('CLOSED_WON','CLOSED_LOST') THEN 1 ELSE 0 END) AS active,
                MAX(updated_at) AS last_activity
            FROM deals
            GROUP BY COALESCE(NULLIF(company, ''), '(회사명 미상)')
            ORDER BY last_activity DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_deals_by_company(company: str, exclude_deal_id: str = None) -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        if exclude_deal_id:
            cur.execute("""
                SELECT * FROM deals
                WHERE COALESCE(NULLIF(company, ''), '(회사명 미상)') = %s
                  AND deal_id != %s
                ORDER BY created_at DESC
            """, (company, exclude_deal_id))
        else:
            cur.execute("""
                SELECT * FROM deals
                WHERE COALESCE(NULLIF(company, ''), '(회사명 미상)') = %s
                ORDER BY created_at DESC
            """, (company,))
        return [dict(r) for r in cur.fetchall()]


def search_deals(query: str, limit: int = 20) -> dict:
    q = f'%{query}%'
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'SELECT * FROM deals WHERE deal_id ILIKE %s LIMIT 1', (query,)
        )
        exact = cur.fetchone()
        cur.execute("""
            SELECT * FROM deals
            WHERE deal_id ILIKE %s
               OR company ILIKE %s
               OR contact_name ILIKE %s
               OR email ILIKE %s
               OR summary ILIKE %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (q, q, q, q, q, limit))
        deal_rows = cur.fetchall()
    return {
        'exact_deal': dict(exact) if exact else None,
        'deals': [dict(r) for r in deal_rows],
    }


def log_activity(deal_id: str, type_: str, payload: dict = None):
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            INSERT INTO activities (deal_id, type, payload, created_at)
            VALUES (%s, %s, %s, %s)
        """, (
            deal_id,
            type_,
            json.dumps(payload, ensure_ascii=False) if payload else None,
            datetime.now().isoformat(),
        ))


def get_activities(deal_id: str, limit: int = 50) -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM activities
            WHERE deal_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (deal_id, limit))
        out = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get('payload'):
                try:
                    d['payload'] = json.loads(d['payload'])
                except Exception:
                    pass
            out.append(d)
    return out


def insert_error(level: str, logger_name: str, message: str, tb: str = None):
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'INSERT INTO errors (level, logger_name, message, traceback, created_at) '
            'VALUES (%s, %s, %s, %s, %s)',
            (level, logger_name, message, tb, datetime.now().isoformat()),
        )


def get_recent_errors(limit: int = 50) -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'SELECT * FROM errors ORDER BY created_at DESC LIMIT %s', (limit,)
        )
        return [dict(r) for r in cur.fetchall()]


def count_errors_since(iso_ts: str) -> int:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'SELECT COUNT(*) AS cnt FROM errors WHERE created_at >= %s', (iso_ts,)
        )
        row = cur.fetchone()
        return row['cnt'] if row else 0


def purge_old_errors(days: int = 30) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('DELETE FROM errors WHERE created_at < %s', (cutoff,))
        return cur.rowcount
