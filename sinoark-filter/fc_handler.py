"""
Alibaba Cloud Function Compute handler for sinoark-filter.
Wraps filter.py with no changes to the original script.

filter.py enters a 60-min monitoring loop after its main classification pass.
The main pass completes in ~60-90s. We run it with a 120s subprocess timeout
so FC returns cleanly instead of waiting to be killed at the function timeout.
"""
import os
import subprocess
import sys


def handler(event, context):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            [sys.executable, 'filter.py'],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.stdout:
            print(result.stdout[-3000:])
        if result.returncode != 0:
            raise Exception(f'filter.py exited {result.returncode}: {result.stderr[-500:]}')
        return 'done'
    except subprocess.TimeoutExpired:
        # filter.py enters a monitoring loop after the main pass — this is expected.
        # 120s is enough for the classification pass to complete.
        print('filter.py reached 120s timeout (normal — monitoring loop truncated)')
        return 'done'
