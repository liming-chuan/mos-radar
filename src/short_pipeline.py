"""Refresh evidence before delivery; repeated healthy sessions do not rotate again."""
import argparse
import json
import os
from pathlib import Path
from market_sessions import calendar, latest_completed, timestamp
from report_timing import market_time
from scan_health import usable, snapshot_health
from short_term_scan import run


def refresh(market,state_dir,phase='recovery',now=None,force=False,scanner=run):
    now=timestamp(now);cal=calendar(market)
    if phase=='premarket':
        day=str(market_time(market,now).date())
        if not cal.is_session(day):
            return dict(status='SKIPPED_HOLIDAY',run_value=False)
        if now>=cal.session_close(day):
            return dict(status='SKIPPED_ENDED',run_value=True)
    path=Path(state_dir)/('hk_mos_short_term.json' if market=='hk' else 'mos_short_term.json')
    old=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    expected=str(latest_completed(market,now).date())
    if not force and usable(old) and old.get('signal_date')==expected and old.get('observed_at') and timestamp(old['observed_at'])<=now:
        return dict(status='CURRENT',run_value=True,signal_date=expected,health=snapshot_health(old))
    result=scanner(market,Path(state_dir),now)
    health=snapshot_health(result)
    return dict(status='FAILED' if health['status']=='FAILED' else 'REFRESHED',run_value=True,
                signal_date=result.get('signal_date'),health=health,coverage=result.get('coverage',{}))


def publish_result(result):
    output=os.getenv('GITHUB_OUTPUT')
    if output:
        with open(output,'a',encoding='utf-8') as stream:
            stream.write('run_value='+str(result.get('run_value',True)).lower()+'\n')
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as stream:
            stream.write('### Short-term evidence\n\n```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```\n')
    print('Evidence refresh: '+json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--market',choices=['hk','us'],required=True)
    parser.add_argument('--phase',choices=['after-close','recovery','premarket'],default='recovery')
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--state-dir',type=Path,default=Path(__file__).resolve().parents[1]/'state')
    args=parser.parse_args()
    result=refresh(args.market,args.state_dir,args.phase,force=args.force)
    publish_result(result)
    if result['status']=='FAILED':
        raise SystemExit('Evidence unavailable: state retained; delayed recovery required')
