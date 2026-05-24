"""DB 기반 에러 로그 — logging.Handler 후킹.

ERROR 이상 로그를 errors 테이블에 자동 적재한다. 코드 변경 없이 기존 logger.error /
logger.exception 호출이 다 잡힌다. emit()은 핸들러 자체 예외로 죽지 않도록 try/except.
"""

import logging
import traceback

import db


class DBErrorHandler(logging.Handler):
    def emit(self, record):
        try:
            tb = None
            if record.exc_info:
                tb = ''.join(traceback.format_exception(*record.exc_info))
            db.insert_error(
                level=record.levelname,
                logger_name=record.name,
                message=self.format(record),
                tb=tb,
            )
        except Exception:
            # 로깅 핸들러는 절대 raise해서는 안 됨 (무한 루프 방지)
            pass


def install_db_handler(level=logging.ERROR):
    """루트 로거에 DBErrorHandler 1회 부착. 중복 부착 방지."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, DBErrorHandler):
            return
    h = DBErrorHandler(level=level)
    h.setFormatter(logging.Formatter('%(message)s'))
    root.addHandler(h)
