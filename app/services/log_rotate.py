"""운영 로그 회전 (app/llama/ngrok).

세 로그 모두 장기 실행 프로세스가 `>>`로 fd를 잡고 있어서 mv/rm 회전은 새 파일이 빈 채로 남는다.
대신 copytruncate 패턴 — 스냅샷 복사 후 원본을 `open('w')`로 자른다. fd는 유지되고 다음 write부터 0바이트 시작.

매일 03:50 APScheduler가 rotate_all()을 호출한다.
"""

import os
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TARGETS = [
    PROJECT_ROOT / 'app.log',
    PROJECT_ROOT / 'llama-server.log',
    PROJECT_ROOT / 'ngrok.log',
]

MIN_BYTES      = int(os.getenv('LOG_ROTATE_MIN_BYTES', str(1 * 1024 * 1024)))  # 1MB
RETENTION_DAYS = int(os.getenv('LOG_ROTATE_RETENTION_DAYS', '14'))


def _ts() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def _rotate_one(path: Path, ts: str) -> dict:
    """copytruncate: 스냅샷 복사 → 원본을 0바이트로 자름."""
    if not path.exists():
        return {'path': str(path), 'status': 'missing'}

    size = path.stat().st_size
    if size < MIN_BYTES:
        return {'path': str(path), 'status': 'skipped', 'size': size}

    snapshot = path.with_name(f'{path.name}.{ts}')
    shutil.copy2(str(path), str(snapshot))
    # fd를 잡고 있는 프로세스의 다음 write가 0바이트 위치부터 이어쓰도록 truncate
    with open(path, 'w'):
        pass
    return {'path': str(path), 'status': 'rotated', 'size': size, 'snapshot': snapshot.name}


def purge_old() -> List[str]:
    """RETENTION_DAYS보다 오래된 회전 스냅샷 삭제."""
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).timestamp()
    removed = []
    for target in TARGETS:
        for p in target.parent.glob(f'{target.name}.*'):
            if p.is_file() and p.stat().st_mtime < cutoff:
                try:
                    p.unlink()
                    removed.append(p.name)
                except OSError as e:
                    logger.warning(f'스냅샷 삭제 실패 {p.name}: {e}')
    return removed


def list_snapshots() -> List[dict]:
    """회전된 스냅샷 파일 메타 — 세팅 페이지 표시용. 최신순."""
    items = []
    for target in TARGETS:
        for p in target.parent.glob(f'{target.name}.*'):
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


def rotate_all() -> dict:
    """전체 로그 회전 1회. 결과 dict 반환."""
    ts = _ts()
    result = {'ts': ts, 'rotated': [], 'skipped': [], 'errors': [], 'purged': []}

    for target in TARGETS:
        try:
            r = _rotate_one(target, ts)
            if r['status'] == 'rotated':
                result['rotated'].append(r)
            else:
                result['skipped'].append(r)
        except Exception as e:
            logger.exception(f'{target.name} 회전 실패')
            result['errors'].append(f'{target.name}: {e}')

    try:
        result['purged'] = purge_old()
    except Exception as e:
        logger.exception('스냅샷 정리 실패')
        result['errors'].append(f'purge: {e}')

    logger.info(
        f'로그 회전 ts={ts} rotated={len(result["rotated"])} '
        f'skipped={len(result["skipped"])} purged={len(result["purged"])} '
        f'errors={len(result["errors"])}'
    )
    return result
