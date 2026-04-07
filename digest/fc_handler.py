"""
Alibaba Cloud Function Compute handler for sinoark-web digest generation.
Wraps generate_digest.py with no changes to the original script.
Mirrors the cron command: python3 generate_digest.py
Note: generate_digest.py calls load_dotenv('/root/workspace/.../.env') which
will silently fail in FC — env vars are injected via FC environment config instead.
"""
import os
import subprocess
import sys


def handler(event, context):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        [sys.executable, 'generate_digest.py'],
        cwd=script_dir,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout[-3000:])
    if result.returncode != 0:
        raise Exception(f'generate_digest.py exited {result.returncode}: {result.stderr[-500:]}')
    return 'done'
