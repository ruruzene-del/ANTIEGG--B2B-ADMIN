"""SQLite DB + ai_context 파일 백업.

매일 03:30 APScheduler가 backup_now()를 호출하고, 어드민 수동 트리거로도 실행 가능.
SQLite는 sqlite3.Connection.backup()으로 동시 쓰기 안전 복사한다.
회전: BACKUP_RETENTION_DAYS(기본 30)보다 오래된 파일 삭제.
"""

import os
import shutil
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH      = PROJECT_ROOT / 'b2b.db'
JSON_PATH    = PROJECT_ROOT / 'ai_context' / 'reply_examples.json'
GUIDE_PATH   = PROJECT_ROOT / 'ai_context' / 'antiegg_style_guide.md'

DEFAULT_BACKUP_DIR = os.path.expanduser(
    '~/Library/Mobile Documents/com~apple~CloudDocs/antiegg-b2b-backups'
)
BACKUP_DIR     = Path(os.path.expanduser(os.getenv('BACKUP_DIR', DEFAULT_BACKUP_DIR)))
RETENTION_DAYS = int(os.getenv('BACKUP_RETENTION_DAYS', '30'))


def _ts() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def _ensure_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _backup_db(ts: str) -> Path:
    """sqlite3 .backup — 동시 쓰기 안전."""
    dst = BACKUP_DIR / f'b2b_{ts}.db'
    src_conn = sqlite3.connect(str(DB_PATH))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    return dst


def _backup_file(src: Path, prefix: str, ext: str, ts: str) -> Optional[Path]:
    if not src.exists():
        return None
    dst = BACKUP_DIR / f'{prefix}_{ts}.{ext}'
    shutil.copy2(str(src), str(dst))
    return dst


def purge_old() -> List[str]:
    """RETENTION_DAYS보다 오래된 백업 파일 삭제. 삭제된 이름 리스트 반환."""
    if not BACKUP_DIR.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).timestamp()
    removed = []
    for p in BACKUP_DIR.iterdir():
        if p.is_file() and p.stat().st_mtime < cutoff:
            try:
                p.unlink()
                removed.append(p.name)
            except OSError as e:
                logger.warning(f'백업 삭제 실패 {p.name}: {e}')
    return removed


def backup_now() -> dict:
    """전체 백업 1회 실행. 결과 dict 반환."""
    _ensure_dir()
    ts = _ts()
    result = {'ts': ts, 'dir': str(BACKUP_DIR), 'files': [], 'errors': [], 'purged': []}

    try:
        result['files'].append(_backup_db(ts).name)
    except Exception as e:
        logger.exception('DB 백업 실패')
        result['errors'].append(f'db: {e}')

    for src, prefix, ext in [
        (JSON_PATH,  'reply_examples',      'json'),
        (GUIDE_PATH, 'antiegg_style_guide', 'md'),
    ]:
        try:
            p = _backup_file(src, prefix, ext, ts)
            if p is not None:
                result['files'].append(p.name)
        except Exception as e:
            logger.exception(f'{prefix} 백업 실패')
            result['errors'].append(f'{prefix}: {e}')

    try:
        result['purged'] = purge_old()
    except Exception as e:
        logger.exception('회전 실패')
        result['errors'].append(f'purge: {e}')

    logger.info(
        f'백업 완료 ts={ts} files={len(result["files"])} '
        f'purged={len(result["purged"])} errors={len(result["errors"])}'
    )
    return result


def list_backups() -> List[dict]:
    if not BACKUP_DIR.exists():
        return []
    items = []
    for p in BACKUP_DIR.iterdir():
        if not p.is_file():
            continue
        st = p.stat()
        items.append({
            'name':  p.name,
            'size':  st.st_size,
            'mtime': datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    items.sort(key=lambda x: x['mtime'], reverse=True)
    return items
