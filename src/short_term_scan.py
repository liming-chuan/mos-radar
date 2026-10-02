"""Bounded public-universe price scan. Does not import email or private holdings."""
import argparse
import json
from pathlib import Path
import pandas as pd
from price_feed import fetch_bars
from market_sessions import latest_completed, timestamp
from market_sessions import calendar
from entry_guards import GATE_VERSION, regime_gate, fetch_earnings, apply_guards, earnings_gate
from short_term import POLICY, STRATEGIES, make_plan, update_journal, report_html, annotate_existing_positions
from scan_health import assess, snapshot_health


def select_universe(frame, now):
    frame = frame.copy()
    if 'is_holding' in frame:
        frame = frame[~frame.is_holding.astype(str).str.lower().isin(['true', '1', '1.0'])]
    required = {'ticker', 'quote_type', 'scan_time', 'liquidity_value', 'equity', 'statement_evidence_status'}
    if not required.issubset(frame):
        return []
    age = (timestamp(now)-pd.to_datetime(frame.scan_time, utc=True, errors='coerce')).dt.total_seconds()/86400
    keep = (age.between(0, 8) & frame.quote_type.eq('EQUITY') &
            pd.to_numeric(frame.equity, errors='coerce').gt(0) &
            ~frame.statement_evidence_status.eq('REJECTED'))
    if 'is_historical_replay' in frame:
        keep &= ~frame.is_historical_replay.astype(str).str.lower().isin(['true', '1', '1.0'])
    frame = frame.loc[keep].assign(turnover=pd.to_numeric(frame.liquidity_value, errors='coerce'))
    frame = frame[frame.turnover.ge(20_000_000)].sort_values(['turnover', 'ticker'], ascending=[False, True])
    # Round-robin sectors before rotation: a large sector cannot occupy every first batch.
    frame = frame.drop_duplicates('ticker')
    sectors = frame.get('sector', pd.Series('UNKNOWN', index=frame.index)).fillna('UNKNOWN')
    frame['sector_rank'] = frame.groupby(sectors, sort=False).cumcount()
    return frame.sort_values(['sector_rank', 'turnover', 'ticker'], ascending=[True, False, True]).ticker.tolist()


def choose_batch(universe, old, budget=300):
    if not universe:
        return [], 0
    # A broken cohort must be retried, not silently skipped by rotation.
    if old.get('plans') and snapshot_health(old)['status']=='FAILED':
        retry=list(dict.fromkeys(p['ticker'] for p in old['plans'] if p['ticker'] in universe))[:budget]
        if retry:
            coverage=old.get('coverage',{})
            return retry,int(coverage.get('retry_next_cursor',coverage.get('next_cursor',0)))%len(universe)
    cursor = int(old.get('coverage', {}).get('next_cursor', 0)) % len(universe)
    rotated = universe[cursor:]+universe[:cursor]
    # Previous near setups are rechecked; reserve most slots for systematic rotation.
    priority = list(dict.fromkeys(p['ticker'] for p in old.get('plans', []) if p['status'] in {'NEAR', 'PENDING'} and p['ticker'] in universe))[:budget//3]
    selected = list(dict.fromkeys(priority))
    walked = 0
    for ticker in rotated:
        if len(selected) >= budget:
            break
        walked += 1
        if ticker not in selected:
            selected.append(ticker)
    return selected, (cursor+walked) % len(universe)


def run(market, state_dir, now=None, fetch=fetch_bars, event_fetch=fetch_earnings):
    now = timestamp(now)
    prefix = 'hk_' if market == 'hk' else ''
    state_dir = Path(state_dir)
    source = state_dir/f'{prefix}mos_market_latest.csv'
    path = state_dir/f'{prefix}mos_short_term.json'
    # A corrupt ledger must fail the job instead of silently deleting its history.
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if old and (old.get('policy') != POLICY or old.get('market') != market):
        raise ValueError('short-term state requires explicit migration')
    journal = old.get('trades', [])
    source_frame = pd.read_csv(source)
    universe = select_universe(source_frame, now)
    selected, next_cursor = choose_batch(universe, old)
    old_coverage=old.get('coverage',{})
    retrying=bool(old.get('plans') and snapshot_health(old)['status']=='FAILED')
    cursor_start=old_coverage.get('cursor_start',old_coverage.get('next_cursor',0)) if retrying else old_coverage.get('next_cursor',0)
    active = [t['ticker'] for t in journal if t['state'] in {'PENDING', 'OPEN', 'DATA_REVIEW'}]
    benchmark = '^HSI' if market == 'hk' else '^GSPC'
    requested=list(dict.fromkeys([benchmark]+active+selected))
    frames = fetch(requested, market, now, priority=active) if fetch is fetch_bars else fetch(requested,market,now)
    # Record observation after fetching, not before a request that may cross the opening bell.
    observed = max(now, timestamp()) if fetch is fetch_bars else now
    plans = [make_plan(t, frames.get(t), frames.get(benchmark), market, observed, strategy)
             for t in selected for strategy in STRATEGIES]
    names = source_frame.set_index('ticker').get('company_name', pd.Series(dtype=str)).to_dict()
    for p in plans:
        name = names.get(p['ticker'], '')
        p['company_name'] = str(name) if pd.notna(name) else ''
    regime=regime_gate(frames.get(benchmark),market,observed)
    failed=len({p['ticker'] for p in plans if p['status']=='DATA'})
    health=assess(len(selected),failed,regime)
    candidates=list(dict.fromkeys(p['ticker'] for p in plans if p['status'] in {'PENDING','NEAR'}))
    # Prioritize actual plans before the watchlist within the bounded event budget.
    candidates=list(dict.fromkeys([t['ticker'] for t in journal if t['state'] in {'PENDING','DATA_REVIEW'} and 'fill' not in t]+[p['ticker'] for p in plans if p['status']=='PENDING']+candidates))
    events=event_fetch(candidates,observed) if health['status']!='FAILED' else {}
    observed=max(observed,timestamp()) if fetch is fetch_bars else observed
    for p in plans:
        p['observed_at']=observed.isoformat()
        if p['status']=='PENDING' and observed>=calendar(market).session_open(p['entry_session']):
            p.update(status='LATE',reason='排雷完成时已开盘，禁止追认早盘成交')
    source_rows={str(r['ticker']):r.to_dict() for _,r in source_frame.iterrows()}
    plans=apply_guards(plans,regime,events,source_rows,market,observed)
    for p in plans:
        sector=source_rows.get(p['ticker'],{}).get('sector')
        p['sector']=str(sector) if pd.notna(sector) else ''
    for t in journal:
        if t['state'] in {'PENDING','DATA_REVIEW'} and 'fill' not in t and observed<calendar(market).session_open(t['entry_session']):
            event=earnings_gate(events.get(t['ticker']),market,t['entry_session'],observed)
            if t.get('gate_version')!=GATE_VERSION or regime['status']=='BLOCK' or event['status']=='BLOCK':
                t.update(state='CANCELLED_GUARD',exit_reason='新版排雷未通过，撤销尚未入场的模拟计划')
                t.pop('data_reason',None)
            elif regime['status']=='UNKNOWN' or event['status']=='UNKNOWN':
                t.update(state='DATA_REVIEW',data_reason='入场前排雷证据待核验；暂不授权模拟成交',earnings_gate=event,market_gate=regime)
            else:
                t.update(state='PENDING',earnings_gate=event,market_gate=regime)
                t.pop('data_reason',None)
    journal=update_journal(journal,[],frames,market,observed)
    tracking_gaps=[t['ticker'] for t in journal if t['state']=='DATA_REVIEW' and
                   not t.get('data_reason','').startswith(('公司行动或停牌','入场前排雷'))]
    # Event fetching may cross a session boundary: count the final plans again.
    failed=len({p['ticker'] for p in plans if p['status']=='DATA'})
    health=assess(len(selected),failed,regime,tracking_gaps)
    if health['status']=='FAILED':
        for p in plans:
            if p['status'] in {'PENDING','NEAR','LATE','GUARD_BLOCKED'}:
                p.update(status='GUARD_BLOCKED',reason='本轮扫描故障：'+health['reason'])
    experiments=[]
    for p in plans:
        if p['status']=='PENDING':
            experiments.extend([dict(p,exit_policy='FIXED'),dict(p,id=p['id']+':be-1',source_id=p['id'],exit_policy='BE_1R')])
    journal = update_journal(journal, experiments, frames, market, observed, advance=False)
    plans = annotate_existing_positions(plans, journal)
    unhealthy=health['status']=='FAILED'
    retry_next_cursor=next_cursor
    if unhealthy:
        next_cursor=cursor_start
    result = dict(policy=POLICY, market=market, gate_version=GATE_VERSION,market_gate=regime, observed_at=observed.isoformat(),
                  health=health, diagnostics=getattr(frames,'diagnostics',{}),
                  signal_date=str(latest_completed(market, observed).date()),
                  coverage=dict(source_count=len(source_frame), eligible=len(universe), selected=len(selected),
                                unselected=len(universe)-len(selected), received=sum(t in frames for t in selected),
                                data_failed=failed,next_cursor=next_cursor,cursor_start=cursor_start,
                                retry_next_cursor=retry_next_cursor,retrying_failed_cohort=retrying),
                  plans=plans, trades=journal)
    # Atomic replacement: failed writes must not truncate the forward ledger.
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')
    tmp.replace(path)
    if not unhealthy:
        good=state_dir/f'{prefix}mos_short_last_good.json'
        good_tmp=good.with_suffix('.json.tmp')
        good_tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        good_tmp.replace(good)
    pd.DataFrame([dict(ticker=t,**d) for t,d in getattr(frames,'diagnostics',{}).items()]).to_csv(state_dir/f'{prefix}mos_short_diagnostics.csv',index=False)
    # Keep bounded raw public evidence for future diagnosis, rather than guessing
    # which field the vendor returned after it has changed on a later request.
    samples=[]
    cutoff=calendar(market).sessions_in_range(latest_completed(market,observed)-pd.Timedelta(days=130),latest_completed(market,observed))[-60]
    for ticker,d in getattr(frames,'diagnostics',{}).items():
        raw=frames.get(ticker)
        if d['reason'] and raw is not None and not raw.empty and len(samples)<10:
            days=pd.to_datetime(raw.index).date
            raw=raw.loc[(days>=cutoff.date()) & (days<=latest_completed(market,observed).date())].copy()
            raw=raw.rename_axis('date').reset_index()
            raw.insert(0,'ticker',ticker)
            samples.append(raw)
    (pd.concat(samples,ignore_index=True) if samples else pd.DataFrame(columns=['ticker','date'])).to_csv(state_dir/f'{prefix}mos_short_bad_bars.csv',index=False)
    pd.DataFrame(plans).to_csv(state_dir/f'{prefix}mos_short_plans.csv', index=False)
    pd.DataFrame(journal).to_csv(state_dir/f'{prefix}mos_short_trades.csv', index=False)
    body = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{max-width:760px;margin:24px auto;padding:16px;font:16px/1.7 sans-serif}.stock{border:1px solid #ddd;padding:16px;margin:16px 0;break-inside:avoid}.sub{color:#526271}</style>'+report_html(market, state_dir, observed)
    (state_dir/f'{prefix}mos_short_report.html').write_text(body, encoding='utf-8')
    print(f'{market}: health={health["status"]} checked={len(selected)} valid={len(selected)-failed} pending={sum(p["status"] == "PENDING" for p in plans)} ledger={len(journal)}')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['hk', 'us'], required=True)
    parser.add_argument('--state-dir', type=Path, default=Path(__file__).resolve().parents[1]/'state')
    args = parser.parse_args()
    result=run(args.market, args.state_dir)
    if result['health']['status']=='FAILED':
        raise SystemExit('Short-term scan failed: diagnostics and ledger retained; no valid market snapshot')
