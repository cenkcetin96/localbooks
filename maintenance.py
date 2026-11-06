#!/usr/bin/env python3
"""Offline backup/restore for large databases. Uses the same guarded restore."""
import argparse
import fcntl
from pathlib import Path
from app import Store,ROOT

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['backup','restore'])
parser.add_argument('file',type=Path)
parser.add_argument('--data',type=Path,default=ROOT/'data')
parser.add_argument('--confirm')
args=parser.parse_args()
args.data.mkdir(parents=True,exist_ok=True)
with open(args.data/'server.lock','a') as lock:
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:parser.error('Quit LocalBooks before offline maintenance.')
    store=Store(args.data)
    try:
        if args.action=='backup':
            with args.file.open('xb') as f:f.write(store.backup())
            print('Backup saved:',args.file)
        else:
            result=store.restore(args.file.read_bytes(),args.confirm)
            print('Backup restored. Previous data retained:',result['safety_copy'])
    finally:store.db.close()
