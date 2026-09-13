"""Point-in-time research gates. Unknown evidence cannot authorize a new plan."""
import time
import pandas as pd
import yfinance as yf
from market_sessions import calendar, latest_completed, timestamp

GATE_VERSION = 'guards-1'


def regime_gate(benchmark, market, now=None):
    from short_term import clean_bars
    try:
        last = latest_completed(market, now)
        expected = calendar(market).sessions_in_range(last-pd.Timedelta(days=100),last)[-50:]
        bars = clean_bars(benchmark,market,now,expected[0])
        if len(expected)!=50 or not bars.index.equals(expected):
            raise ValueError('基准最近50个完整交易日不齐')
        close = bars['Adj Close']
        allowed = close.iloc[-1] > close.mean()
        return dict(status='ALLOW' if allowed else 'BLOCK',asof=str(last.date()),
                    close=float(close.iloc[-1]),ma50=float(close.mean()),
                    reason='基准收盘在MA50上方' if allowed else '基准收盘未超过MA50，暂停新计划')
    except (ValueError,TypeError,KeyError,IndexError) as exc:
        return dict(status='UNKNOWN',reason=str(exc))


def fetch_earnings(tickers, now=None):
    from valuation import quiet_yfinance_call
    results = {}
    deadline = time.monotonic()+180
    for ticker in list(dict.fromkeys(tickers))[:40]:
        if time.monotonic() >= deadline:
            break
        try:
            value = quiet_yfinance_call(lambda: yf.Ticker(ticker).get_calendar())
            dates = (value or {}).get('Earnings Date', [])
            results[ticker] = dict(dates=[str(pd.Timestamp(x).date()) for x in dates],
                                   observed_at=timestamp().isoformat(), source='Yahoo calendar (estimated)')
        except Exception:
            results[ticker] = dict(dates=[], observed_at=timestamp().isoformat(),source='Yahoo calendar failed')
    return results


def earnings_gate(evidence, market, entry_session, now=None):
    result = dict(status='UNKNOWN',reason='缺少有效的未来财报日期，不能视为没有财报')
    try:
        now = timestamp(now)
        evidence = evidence or {}
        if not evidence.get('observed_at'):
            return result
        age = (now-timestamp(evidence['observed_at'])).total_seconds()
        if not 0 <= age <= 4*86400 or not evidence.get('source'):
            return result
        entry = pd.Timestamp(entry_session)
        end = entry
        for _ in range(9):
            end = calendar(market).next_session(end)
        local_today = now.tz_convert('Asia/Hong_Kong' if market=='hk' else 'America/New_York').date()
        dates = [pd.Timestamp(x).date() for x in evidence.get('dates', [])]
        if not dates or any(pd.isna(x) or x < local_today for x in dates):
            return result
        first = min(dates)
        result.update(source=evidence['source'], observed_at=evidence['observed_at'],
                      earliest_date=str(first), window_end=str(end.date()))
        # Include an upcoming announcement before entry as well: its reaction is not yet observed.
        if first <= end.date():
            return dict(result,status='BLOCK',reason='预计财报日期落在入场前或最长10交易日持有窗口内')
        return dict(result,status='ALLOW',reason='供应商预计财报日在持有窗口之后；仍可能改期，非公告保证')
    except (KeyError,ValueError,TypeError,IndexError):
        return result


def value_label(row, plan, now=None):
    from opportunity import evaluate_entry, ENTRY_STATES
    from quality_watch import evaluate_quality
    row = dict(row)
    try:
        observed = pd.to_datetime(row.get('scan_time'),utc=True,errors='coerce')
        age = (timestamp(now)-observed).total_seconds()
        if pd.isna(observed) or not 0 <= age <= 8*86400 or row.get('entry_status')=='REVIEW_REQUIRED':
            return '价值证据待复核'
        if pd.notna(row.get('entry_risk_issues')) and str(row.get('entry_risk_issues','')).strip():
            return '基本面风险待复核'
        # Recheck entry price against the latest daily close, not a stale quote's status/rating.
        price = float(plan['close'])
        old_price = float(row['price'])
        row.update(price=price, market_cap=float(row['market_cap'])*price/old_price,
                   price_asof=timestamp(now).isoformat())
        if evaluate_entry(row,now)['entry_status'] in ENTRY_STATES:
            return '严格价值条件同时通过（按最新日线收盘复核）'
        if evaluate_quality(row,now)['qv_status']=='QUALITY_WATCH':
            return '质量与合理估值条件同时通过（按日线复核）'
        return '未同时通过价值条件'
    except (ValueError,TypeError,KeyError,ZeroDivisionError):
        return '价值证据待补齐'


def apply_guards(plans, regime, events, source_rows, market, now=None):
    for p in plans:
        p.update(gate_version=GATE_VERSION,market_gate=regime,
                 value_label=value_label(source_rows.get(p['ticker'],{}),p,now))
        if p['status'] not in {'PENDING','NEAR'}:
            continue
        session = p.get('entry_session') or str(calendar(market).next_session(latest_completed(market,now)).date())
        event = earnings_gate(events.get(p['ticker']),market,session,now)
        p.update(earnings_gate=event,technical_status=p['status'])
        if regime['status']!='ALLOW' or event['status']!='ALLOW':
            p.update(status='GUARD_BLOCKED',reason='；'.join(x['reason'] for x in [regime,event] if x['status']!='ALLOW'))
        elif p['status']=='PENDING':
            p['id'] += ':'+GATE_VERSION
    return plans


def entry_snapshot_valid(trade, market):
    """Only evidence observed before intended entry, never later events for an earlier fill."""
    if trade.get('gate_version')!=GATE_VERSION:
        return True  # retained legacy control, never a newly gated record
    cal=calendar(market)
    opened=cal.session_open(trade['entry_session'])
    evidence=trade.get('earnings_gate',{})
    if not evidence.get('observed_at') or timestamp(evidence['observed_at'])>=opened:
        return False
    checked=earnings_gate(dict(dates=[evidence['earliest_date']] if evidence.get('earliest_date') else [],
                              observed_at=evidence.get('observed_at'),source=evidence.get('source')),market,trade['entry_session'],opened)
    regime=trade.get('market_gate',{})
    return (checked['status']=='ALLOW' and regime.get('status')=='ALLOW' and
            regime.get('asof')==str(cal.previous_session(trade['entry_session']).date()))
