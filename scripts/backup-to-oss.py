#!/usr/bin/env python3
"""
SinoArk OSS Backup — uses Python oss2 SDK (no ossutil binary needed).
Backs up SQLite databases and .env files to Alibaba Cloud OSS.
"""
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import oss2

ACCESS_ID     = os.environ['ALIYUN_ACCESS_ID']
ACCESS_SECRET = os.environ['ALIYUN_ACCESS_ID_SECRET']
ENDPOINT      = os.environ.get('ALIYUN_OSS_ENDPOINT', 'oss-ap-southeast-1.aliyuncs.com')
BUCKET_NAME   = os.environ['BACKUP_BUCKET_NAME']
PREFIX        = 'sinoark'

auth   = oss2.Auth(ACCESS_ID, ACCESS_SECRET)
bucket = oss2.Bucket(auth, f'https://{ENDPOINT}', BUCKET_NAME)

def ensure_bucket():
    try:
        bucket.get_bucket_info()
        print(f'  Bucket {BUCKET_NAME} exists')
    except oss2.exceptions.NoSuchBucket:
        bucket.create_bucket(oss2.BUCKET_ACL_PRIVATE)
        print(f'  Created bucket {BUCKET_NAME}')

def upload(local_path: str, oss_key: str):
    size = os.path.getsize(local_path)
    bucket.put_object_from_file(oss_key, local_path)
    print(f'  {local_path} → oss://{BUCKET_NAME}/{oss_key}  ({size/1024/1024:.1f} MB)')

def ts():
    return datetime.now(timezone.utc).strftime('%H:%M:%S')

print(f'[{ts()}] SinoArk OSS backup → oss://{BUCKET_NAME}/{PREFIX}/')

ensure_bucket()

# ── SQLite databases ──────────────────────────────────────────────────────────
db_files = [
    ('/root/data/db.db',  f'{PREFIX}/data/db.db'),
    ('/root/data2/db.db', f'{PREFIX}/data2/db.db'),
    ('/root/data3/db.db', f'{PREFIX}/data3/db.db'),
]
for local, key in db_files:
    if os.path.exists(local):
        upload(local, key)
    else:
        print(f'  SKIP {local} (not found)')

# ── .env files (bundled as tar.gz) ───────────────────────────────────────────
env_files = list(Path('/root/workspace').glob('**/.env'))
if env_files:
    with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
        tmp_path = tmp.name
    with tarfile.open(tmp_path, 'w:gz') as tar:
        for f in env_files:
            tar.add(str(f), arcname=str(f))
    upload(tmp_path, f'{PREFIX}/secrets/env-files.tar.gz')
    os.unlink(tmp_path)

# ── Crontab ───────────────────────────────────────────────────────────────────
import subprocess
crontab_out = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
if crontab_out.returncode == 0:
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
        tmp.write(crontab_out.stdout)
        tmp_path = tmp.name
    upload(tmp_path, f'{PREFIX}/crontab.txt')
    os.unlink(tmp_path)

print(f'[{ts()}] Backup complete')
