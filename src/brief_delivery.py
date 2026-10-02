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
from scan_health import usable


def deliver(market, state_dir, mode='premarket', now=None, sender=None, dry_run=False, persist_receipt=None):
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
    pending=old.get('pending',{})
    if not isinstance(sent,dict) or not isinstance(pending,dict):
        raise ValueError('invalid delivery sessions')
    for day,at in sent.items():
        date.fromisoformat(day)
        checked=timestamp(at)
        if pd.isna(checked) or checked>now:
            raise ValueError('invalid delivery timestamp')
    for day,claim in pending.items():
        date.fromisoformat(day)
        if not isinstance(claim,dict) or claim.get('status') not in {'CLAIMED','UNCERTAIN'}:
            raise ValueError('invalid delivery claim')
        checked=timestamp(claim['claimed_at'])
        if pd.isna(checked) or checked>now:
            raise ValueError('invalid delivery claim timestamp')
    if key in sent and not dry_run:
        return dict(status='ALREADY_SENT',session=key)
    if key in pending and not dry_run:
        return dict(status='DELIVERY_REVIEW',session=key,reason='已保留发送声明，需核对邮件后人工解决；不盲目重发')
    snapshot=root/f'{prefix}mos_short_term.json'
    data=json.loads(snapshot.read_text(encoding='utf-8')) if snapshot.exists() else {}
    fresh=(data.get('signal_date')==str(cal.previous_session(session).date()) and
           usable(data) and
           data.get('observed_at') and timestamp(data['observed_at'])<=now)
    # Do not burn the daily delivery on a broken or stale after-close scan.
    if not fresh and (mode=='after-close' or now<cal.session_open(session)) and not dry_run:
        return dict(status='DEFERRED_DATA',session=key)
    subject,body=prepare_brief(market,root,now,target_session=key,early=mode=='after-close')
    if not fresh:
        subject=subject.replace('短线机会简报','数据故障提示')
    output=root.parent/'reports'/market/'short_brief.html'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(body,encoding='utf-8')
    if dry_run:
        return dict(status='DRY_RUN',session=key)
    if sender is None:
        from emailer import send_email
        sender=send_email

    def save():
        marker.parent.mkdir(parents=True,exist_ok=True)
        temp=marker.with_suffix('.json.tmp')
        temp.write_text(json.dumps(dict(market=market,sessions=dict(sorted(sent.items())[-60:]),
                                       pending=pending),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temp.replace(marker)
        if persist_receipt:
            persist_receipt()

    # Persist intent BEFORE SMTP. A remote claim survives an ambiguous SMTP result
    # or a later Git failure; another job must not deliver the same session again.
    pending[key]=dict(status='CLAIMED',claimed_at=now.isoformat())
    save()
    try:
        sender(subject,body)
    except Exception:
        pending[key]['status']='UNCERTAIN'
        try:
            save()
        except Exception:
            pass  # the previously persisted claim already blocks a retry
        raise
    sent[key]=now.isoformat()
    del pending[key]
    save()
    return dict(status='SENT',session=key)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--market',choices=['hk','us'],required=True)
    parser.add_argument('--mode',choices=['after-close','premarket'],default='after-close')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--persist',action='store_true',help='Persist claim before SMTP and receipt after SMTP using authenticated Git')
    args=parser.parse_args()
    state=Path(__file__).resolve().parents[1]/'state'
    dry_run=args.dry_run or os.getenv('DRY_RUN','').strip().lower() in {'1','true','yes','y','on'}
    from persist_public_state import persist
    result=deliver(args.market,state,args.mode,dry_run=dry_run,
                   persist_receipt=(lambda:persist(args.market,'receipt')) if args.persist else None)
    print('Daily brief delivery: '+json.dumps(result,ensure_ascii=False))
    if result['status']=='DELIVERY_REVIEW':
        raise SystemExit('Delivery uncertain: inspect outbox claim and mailbox; do not automatically resend')
