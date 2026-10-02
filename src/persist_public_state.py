"""Persist only the selected public state files, including a pre-send outbox claim."""
import argparse
import os
from pathlib import Path
import subprocess
import time


def persist(market,kind,root=None,command=None,pause=time.sleep):
    root=Path(root or Path(__file__).resolve().parents[1])
    prefix='hk_' if market=='hk' else ''
    suffixes={'short':['mos_short_term.json','mos_short_last_good.json'],
              'receipt':['mos_brief_delivery.json'],
              'value':['mos_market_latest.csv','mos_watch_history.csv','mos_entry_history.csv']}
    paths=['state/'+prefix+s for s in suffixes[kind] if (root/'state'/(prefix+s)).exists()]
    if not paths:
        return
    def git(*args,check=True):
        if command:
            return command(list(args),check)
        return subprocess.run(['git',*args],cwd=root,check=check,capture_output=True,text=True)
    git('config','user.name','mos-radar-bot')
    git('config','user.email','bot@example.com')
    git('add','--',*paths)
    diff=git('diff','--cached','--quiet','--',*paths,check=False)
    if diff.returncode not in {0,1}:
        raise RuntimeError('Cannot inspect public state changes')
    if diff.returncode==0:
        return
    git('commit','-m',f'update {market} public {kind} state','--only','--',*paths)
    branch=os.getenv('GITHUB_REF_NAME','main')
    for attempt in range(3):
        # Different markets/value jobs may advance main concurrently.
        git('pull','--rebase','origin',branch)
        pushed=git('push','origin',f'HEAD:{branch}',check=False)
        if pushed.returncode==0:
            return
        if attempt<2:
            pause(2*(attempt+1))
    raise RuntimeError('Public state push failed after three attempts; no mail may be sent without a persisted claim')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--market',choices=['hk','us'],required=True)
    parser.add_argument('--kind',choices=['short','receipt','value'],required=True)
    args=parser.parse_args()
    persist(args.market,args.kind)
