"""
Alibaba Cloud Function Compute handler for translation-digest.
Wraps scripts/translate_batch.py with no changes to the original script.
Mirrors the cron command: python3 scripts/translate_batch.py --limit 50
"""
import os
import subprocess
import sys


def handler(event, context):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        [sys.executable, 'scripts/translate_batch.py', '--limit', '50'],
        cwd=script_dir,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout[-3000:])
    if result.returncode != 0:
        raise Exception(f'translate_batch.py exited {result.returncode}: {result.stderr[-500:]}')
    return 'done'
