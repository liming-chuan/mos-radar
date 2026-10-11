"""Bounded downloads with isolated retries and public field-level diagnostics."""
import io
import logging
import json
import time
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import pandas as pd
import yfinance as yf
from market_sessions import calendar, latest_completed
from short_term import clean_bars, FIELDS


class PriceFrames(dict):
    def __init__(self):
        super().__init__()
        self.diagnostics = {}


def issue(frame, market, now):
    last = latest_completed(market, now)
    expected = calendar(market).sessions_in_range(last-pd.Timedelta(days=130), last)[-60:]
    if frame is None or frame.empty:
        return '未返回日线'
    missing = sorted(set(FIELDS)-set(frame.columns))
    if missing:
        return '缺少字段：'+','.join(missing)
    try:
        days = pd.to_datetime(frame.index).date
        window = frame.loc[(days >= expected[0].date()) & (days <= last.date())]
        bad = window[FIELDS].apply(pd.to_numeric, errors='coerce').isna()
        if bad.any().any():
            return '无效字段：'+','.join(bad.columns[bad.any()])+'；日期：'+','.join(str(x.date()) for x in pd.to_datetime(window.index[bad.any(axis=1)])[:3])
        checked = clean_bars(frame, market, now, expected[0])
        if not checked.tail(60).index.equals(expected):
            absent=expected.difference(checked.index)
            return '最近60交易日日线不齐；缺失日期：'+','.join(str(d.date()) for d in absent[:3])
        return ''
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return str(exc)


def pending_daily_bar(frame, market, now):
    """Latest daily close is absent; a later scheduled request is required."""
    try:
        last=latest_completed(market,now)
        expected=calendar(market).sessions_in_range(last-pd.Timedelta(days=130),last)[-60:]
        window=frame[FIELDS].copy()
        window.index=pd.DatetimeIndex(pd.to_datetime(window.index).date)
        window=window.reindex(expected).apply(pd.to_numeric,errors='coerce')
        if not window.iloc[:-1].notna().all().all():
            return False
        missing=set(window.columns[window.iloc[-1].isna()])
        if not missing or not missing.issubset({'Close','Adj Close'}):
            return False
        # This diagnosis never authorizes use of the unfinished bar.
        previous_now=calendar(market).session_open(last)-pd.Timedelta(seconds=1)
        checked=clean_bars(frame,market,previous_now,expected[0])
        return checked.index.equals(expected[:-1])
    except (ValueError,TypeError,KeyError,AttributeError,IndexError):
        return False


def fetch_bars(tickers, market, now, priority=(), cache_root=None, fallback=False):
    last = latest_completed(market, now)
    frames = PriceFrames()
    deadline = time.monotonic()+18*60
    retry_budget = 40

    def cache_path(ticker):
        safe = ''.join(c if c.isalnum() or c in '.-_' else '_' for c in ticker)
        return Path(cache_root)/market/(safe+'.json')

    def cached(ticker):
        if cache_root is None:
            return None
        try:
            payload = json.loads(cache_path(ticker).read_text(encoding='utf-8'))
            if payload['ticker'] != ticker or payload['market'] != market or pd.Timestamp(payload['observed_at']) > pd.Timestamp(now):
                return None
            frame = pd.DataFrame(payload['rows']).set_index('date')
            frame.index = pd.to_datetime(frame.index)
            return frame if not issue(frame, market, now) else None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save_valid(ticker):
        if cache_root is None or frames.diagnostics[ticker]['reason']:
            return
        try:
            frame = clean_bars(frames[ticker], market, now, last-pd.Timedelta(days=130)).tail(60)
            rows = frame.rename_axis('date').reset_index()
            rows['date'] = rows.date.dt.strftime('%Y-%m-%d')
            path = cache_path(ticker)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(dict(ticker=ticker, market=market, observed_at=pd.Timestamp(now).isoformat(),
                                           rows=rows.to_dict('records')), allow_nan=False), encoding='utf-8')
            tmp.replace(path)
        except (OSError, ValueError, TypeError):
            pass  # Cache failure cannot authorize a trade or discard valid live evidence.

    def recover(ticker):
        """Same-provider alternate request, not independent price confirmation."""
        if not fallback or time.monotonic() >= deadline or not frames.diagnostics[ticker]['reason']:
            return
        before = frames.diagnostics[ticker]
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                frame = yf.Ticker(ticker).history(start=str((last-pd.Timedelta(days=180)).date()),
                    end=str((last+pd.Timedelta(days=1)).date()), auto_adjust=False,
                    actions=True, repair=False, timeout=10, raise_errors=True)
            reason = issue(frame, market, now)
            before['fallback_reason'] = reason
            if not reason:
                frames[ticker] = frame
                before.update(reason='', readiness='READY', source='YAHOO_HISTORY')
        except Exception as exc:
            before['fallback_reason'] = type(exc).__name__+': '+str(exc)[:400]
        before['fallback_attempts'] = before.get('fallback_attempts', 0)+1

    def download(batch):
        # Capture bounded public diagnostics instead of discarding vendor errors.
        output = io.StringIO()
        logger = logging.getLogger('yfinance')
        handler = logging.StreamHandler(output)
        logger.addHandler(handler)
        try:
            with redirect_stdout(output), redirect_stderr(output):
                result = yf.download(batch, start=str((last-pd.Timedelta(days=180)).date()),
                                     end=str((last+pd.Timedelta(days=1)).date()), auto_adjust=False,
                                     actions=True, threads=False, progress=False, timeout=10)
            error = output.getvalue()[-1200:]
        except Exception as exc:
            result, error = None, type(exc).__name__+': '+str(exc)[:400]
        finally:
            logger.removeHandler(handler)
        for ticker in batch:
            frame = None
            if result is not None and not result.empty:
                if isinstance(result.columns, pd.MultiIndex) and ticker in result.columns.get_level_values(-1):
                    frame = result.xs(ticker, axis=1, level=-1).dropna(how='all')
                elif len(batch)==1:
                    frame = result.dropna(how='all')
            reason = issue(frame, market, now)
            before = frames.diagnostics.get(ticker, {})
            frames.diagnostics[ticker] = dict(reason=reason, attempts=before.get('attempts',0)+1,
                                             first_issue=before.get('first_issue',reason), vendor_error=error,
                                             source='YAHOO_DOWNLOAD',
                                             readiness='DAILY_PENDING' if pending_daily_bar(frame,market,now) else ('INVALID' if reason else 'READY'))
            if frame is not None:
                frames[ticker] = frame

    # Benchmark and all active ledger names precede the rotation in the caller.
    # The benchmark always has an independent index, avoiding shared batch padding.
    isolated=list(dict.fromkeys(tickers[:1]+list(priority)))
    # Reuse only a fully validated snapshot for the exact completed session.
    for ticker in tickers:
        frame = cached(ticker)
        if frame is not None:
            frames[ticker] = frame
            frames.diagnostics[ticker] = dict(reason='', attempts=0, first_issue='', vendor_error='',
                                             readiness='READY', source='VALIDATED_CACHE')
    for ticker in isolated:
        if ticker in frames:
            continue
        if time.monotonic() >= deadline:
            break
        download([ticker])
        if frames.diagnostics[ticker]['reason'] and frames.diagnostics[ticker]['readiness']!='DAILY_PENDING' and time.monotonic()<deadline:
            download([ticker])
        recover(ticker)
        save_valid(ticker)
    remaining=[t for t in tickers if t not in isolated and t not in frames]
    for offset in range(0, len(remaining), 20):
        if time.monotonic() >= deadline:
            break
        batch = remaining[offset:offset+20]
        download(batch)
        for ticker in batch:
            if frames.diagnostics[ticker]['reason'] and frames.diagnostics[ticker]['readiness']!='DAILY_PENDING' and retry_budget>0 and time.monotonic()<deadline:
                download([ticker])
                retry_budget -= 1
            save_valid(ticker)
        print(f'{market}: price requests checked={len(frames.diagnostics)}/{len(tickers)} remaining retries={retry_budget}',flush=True)
    for ticker in tickers:
        if ticker not in frames.diagnostics:
            frames.diagnostics[ticker] = dict(reason='下载预算用尽，未请求', attempts=0, readiness='NOT_REQUESTED')
    return frames
