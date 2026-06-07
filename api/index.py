import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from main import app
except Exception as _e:
    import traceback
    print(f"[STARTUP ERROR] {_e}", flush=True)
    print(traceback.format_exc(), flush=True)
    raise
