"""SQLite b2b.db → Supabase PostgreSQL 1회성 마이그레이션.

실행: python scripts/migrate_to_pg.py
  --dry-run  : 연결 테스트 + 카운트만, 실제 INSERT 없음
  --apply    : 실제 마이그레이션 실행
"""
import argparse
import json
import os
import sqlite3
import sys

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import psycopg2
import psycopg2.extras

SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'b2b.db')


def get_pg():
    url = os.environ.get('DATABASE_URL')
    if not url:
        print('❌ DATABASE_URL 환경변수가 없습니다. .env 확인.')
        sys.exit(1)
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def get_sqlite():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def test_connections():
    print('SQLite 연결 확인...', end=' ')
    sq = get_sqlite()
    row = sq.execute('SELECT COUNT(*) AS cnt FROM deals').fetchone()
    print(f'✅ deals {row["cnt"]}건')
    sq.close()

    print('Supabase 연결 확인...', end=' ')
    pg = get_pg()
    cur = pg.cursor()
    cur.execute('SELECT version()')
    ver = cur.fetchone()
    print(f'✅ {ver["version"][:40]}...')
    pg.close()


def migrate(dry_run: bool):
    import db
    print('\n▶ 스키마 생성 (CREATE TABLE IF NOT EXISTS)...')
    db.init_db()
    print('  ✅ 스키마 준비 완료')

    sq = get_sqlite()
    pg = get_pg()
    pg.autocommit = False
    cur = pg.cursor()

    tables = [
        ('deals',      'deal_id'),
        ('counter',    'year_month'),
        ('activities', 'id'),
        ('settings',   'key'),
        ('errors',     'id'),
    ]

    for table, pk in tables:
        rows = sq.execute(f'SELECT * FROM {table}').fetchall()
        rows = [dict(r) for r in rows]
        if not rows:
            print(f'  {table}: 0건 — 스킵')
            continue

        cols = list(rows[0].keys())
        # PostgreSQL 스키마에 없는 컬럼 필터링 (notion_page_id는 이미 포함)
        placeholders = ', '.join(f'%({c})s' for c in cols)
        col_list = ', '.join(cols)
        sql = (
            f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ({pk}) DO NOTHING'
        )

        if dry_run:
            print(f'  {table}: {len(rows)}건 (dry-run, INSERT 생략)')
            continue

        success = 0
        for row in rows:
            # None이 아닌 값만 넣고, 나머지는 None 유지
            try:
                cur.execute(sql, row)
                success += 1
            except Exception as e:
                print(f'  ⚠️  {table} {row.get(pk)}: {e}')
                pg.rollback()
                # 행 단위로 롤백 후 계속
                cur = pg.cursor()

        pg.commit()
        print(f'  {table}: {success}/{len(rows)}건 ✅')

    sq.close()
    pg.close()

    if not dry_run:
        print('\n✅ 마이그레이션 완료')
        print('  다음: Vercel 환경변수에 DATABASE_URL 등록 후 배포')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='연결 테스트만')
    parser.add_argument('--apply', action='store_true', help='실제 마이그레이션')
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.print_help()
        sys.exit(0)

    test_connections()
    migrate(dry_run=args.dry_run)
