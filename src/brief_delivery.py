"""One daily brief per market/entry session, with an after-close first attempt."""
import argparse
import json
import os
from datetime import date
from pathlib import Path
import pandas as pd
from market_sessions import calendar, latest_completed, timestamp
from report_timing import market_time
from short_brief import prepare_brief


def deliver(market, state_dir, mode='premarket', now=None, sender=None, dry_run=False):
    now=timestamp(now)
    root=Path(state_dir)
    prefix='hk_' if market=='hk' else ''
    cal=calendar(market)
    if mode=='after-close':
        session=cal.next_session(latest_completed(market,now))
        if now>=cal.session_open(session):
            return dict(status='DEFERRED_LATE',session=str(session.date()))
    else:
        day=str(market_time(market,now).date())
        if not cal.is_session(day):
            return dict(status='SKIPPED_HOLIDAY')
        session=pd.Timestamp(day)
        if now>=cal.session_close(session):
            return dict(status='SKIPPED_ENDED',session=day)
    key=str(session.date())
    marker=root/f'{prefix}mos_brief_delivery.json'
    old=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    if not isinstance(old,dict):
        raise ValueError('invalid delivery receipt')
    if old.get('market')!=market and old:
        raise ValueError('delivery marker market mismatch')
    sent=old.get('sessions',{})
    if not isinstance(sent,dict):
        raise ValueError('invalid delivery sessions')
    for day,at in sent.items():
        date.fromisoformat(day)
        checked=timestamp(at)
        if pd.isna(checked) or checked>now:
            raise ValueError('invalid delivery timestamp')
    if key in sent:
        return dict(status='ALREADY_SENT',session=key)
    snapshot=root/f'{prefix}mos_short_term.json'
    data=json.loads(snapshot.read_text(encoding='utf-8')) if snapshot.exists() else {}
    fresh=(data.get('signal_date')==str(cal.previous_session(session).date()) and
           data.get('health',{}).get('status') in {'OK','PARTIAL'} and
           data.get('observed_at') and timestamp(data['observed_at'])<=now)
    # Do not burn the daily delivery on a broken or stale after-close scan.
    if mode=='after-close' and not fresh:
        return dict(status='DEFERRED_DATA',session=key)
    subject,body=prepare_brief(market,root,now,target_session=key,early=mode=='after-close')
    output=root.parent/'reports'/market/'short_brief.html'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(body,encoding='utf-8')
    if dry_run:
        return dict(status='DRY_RUN',session=key)
    if sender is None:
        from emailer import send_email
        sender=send_email
    sender(subject,body)
    sent[key]=now.isoformat()
    # Keep enough days for delayed attempts, with no addresses or private data.
    sessions=dict(sorted(sent.items())[-60:])
    marker.parent.mkdir(parents=True,exist_ok=True)
    temp=marker.with_suffix('.json.tmp')
    temp.write_text(json.dumps(dict(market=market,sessions=sessions),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(marker)
    return dict(status='SENT',session=key)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--market',choices=['hk','us'],required=True)
    parser.add_argument('--mode',choices=['after-close','premarket'],default='after-close')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    state=Path(__file__).resolve().parents[1]/'state'
    dry_run=args.dry_run or os.getenv('DRY_RUN','').strip().lower() in {'1','true','yes','y','on'}
    result=deliver(args.market,state,args.mode,dry_run=dry_run)
    print('Daily brief delivery: '+json.dumps(result,ensure_ascii=False))
