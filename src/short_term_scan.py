"""Bounded public-universe price scan. Does not import email or private holdings."""
import argparse
import json
from pathlib import Path
import pandas as pd
import yfinance as yf
from market_sessions import latest_completed, timestamp
from short_term import POLICY, make_plan, update_journal, report_html


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
    return frame.sort_values(['turnover', 'ticker'], ascending=[False, True]).ticker.drop_duplicates().head(100).tolist()


def fetch_bars(tickers, market, now):
    from valuation import quiet_yfinance_call
    last = latest_completed(market, now)
    frames = {}
    for offset in range(0, len(tickers), 20):
        batch = tickers[offset:offset+20]
        try:
            result = quiet_yfinance_call(lambda: yf.download(
                batch, start=str((last-pd.Timedelta(days=180)).date()),
                end=str((last+pd.Timedelta(days=1)).date()), auto_adjust=False,
                actions=True, threads=False, progress=False, timeout=10))
            if result is None or result.empty:
                break
            for ticker in batch:
                if isinstance(result.columns, pd.MultiIndex):
                    if ticker in result.columns.get_level_values(-1):
                        frames[ticker] = result.xs(ticker, axis=1, level=-1).dropna(how='all')
                elif len(batch) == 1:
                    frames[ticker] = result.dropna(how='all')
        except Exception:
            break
    return frames


def run(market, state_dir, now=None, fetch=fetch_bars):
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
    universe = select_universe(pd.read_csv(source), now)
    active = [t['ticker'] for t in journal if t['state'] in {'PENDING', 'OPEN', 'DATA_REVIEW'}]
    benchmark = '^HSI' if market == 'hk' else '^GSPC'
    frames = fetch(list(dict.fromkeys([benchmark]+active+universe)), market, now)
    # Record observation after fetching, not before a request that may cross the opening bell.
    observed = max(now, timestamp()) if fetch is fetch_bars else now
    plans = [make_plan(t, frames.get(t), frames.get(benchmark), market, observed) for t in universe]
    journal = update_journal(journal, plans, frames, market, observed)
    result = dict(policy=POLICY, market=market, observed_at=observed.isoformat(),
                  signal_date=str(latest_completed(market, observed).date()),
                  plans=plans, trades=journal)
    # Atomic replacement: failed writes must not truncate the forward ledger.
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')
    tmp.replace(path)
    pd.DataFrame(plans).to_csv(state_dir/f'{prefix}mos_short_plans.csv', index=False)
    pd.DataFrame(journal).to_csv(state_dir/f'{prefix}mos_short_trades.csv', index=False)
    body = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{max-width:760px;margin:24px auto;padding:16px;font:16px/1.7 sans-serif}.stock{border:1px solid #ddd;padding:16px;margin:16px 0;break-inside:avoid}.sub{color:#526271}</style>'+report_html(market, state_dir, observed)
    (state_dir/f'{prefix}mos_short_report.html').write_text(body, encoding='utf-8')
    print(f'{market}: checked={len(plans)} pending={sum(p["status"] == "PENDING" for p in plans)} ledger={len(journal)}')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['hk', 'us'], required=True)
    parser.add_argument('--state-dir', type=Path, default=Path(__file__).resolve().parents[1]/'state')
    args = parser.parse_args()
    run(args.market, args.state_dir)
