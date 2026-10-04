"""CLI uses the same settings and engine as the dashboard."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pm_bridge.web_app import perform_sync_core, background_sync_worker

if __name__ == '__main__':
    if '--daemon' in sys.argv:
        background_sync_worker()
    else:
        result = perform_sync_core(True)
        if sys.stdout:
            sys.stdout.reconfigure(encoding='utf-8')
            print(result)
        sys.exit(0 if result['status'] == 'success' else 1)
