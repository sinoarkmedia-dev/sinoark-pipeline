#!/usr/bin/env python3
"""
One-off script: upload existing digest JSON files to Supabase digests table.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

load_dotenv('/root/workspace/sinoark_media_wechat_pipeline/.env')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
DIGESTS_DIR = Path('/root/workspace/sinoark-web/public/digests')

client = create_client(SUPABASE_URL, SUPABASE_KEY)

files = sorted(f for f in DIGESTS_DIR.glob('*.json') if f.name != 'latest.json')
print(f'Found {len(files)} digest files to upload\n')

for f in files:
    data = json.loads(f.read_text())
    row = {
        'digest_date':    data['date'],
        'window_start':   data['window_start'],
        'window_end':     data['window_end'],
        'html_content':   data['html_content'],
        'articles_json':  data.get('articles', []),
        'articles_count': data.get('articles_count', len(data.get('articles', []))),
        'generated_at':   data.get('generated_at'),
    }
    res = client.table('digests').upsert(row, on_conflict='digest_date').execute()
    if hasattr(res, 'error') and res.error:
        print(f'  ERROR {f.name}: {res.error}')
    else:
        print(f'  OK  {f.name} ({row["articles_count"]} articles)')

print('\nDone.')
