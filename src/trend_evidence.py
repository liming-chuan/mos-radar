"""Bounded, adjusted daily-price evidence; excludes incomplete sessions."""
import pandas as pd
import numpy as np
import yfinance as yf
from market_sessions import latest_completed


def compute_trend(stock, benchmark, market='us', now=None):
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.to_datetime(now, utc=True)
    last = latest_completed(market, now).date()
    def clean(series):
        s = pd.to_numeric(series, errors='coerce').copy()
        s.index = pd.to_datetime([pd.Timestamp(x).date() for x in s.index])
        if s.index.has_duplicates:
            raise ValueError('价格历史包含重复日期')
        s = s[s.index.date <= last].sort_index()
        return s.where(np.isfinite(s) & (s > 0)).dropna()
    try:
        stock, benchmark = clean(stock), clean(benchmark)
        if stock.empty or benchmark.empty or stock.index[-1] != benchmark.index[-1]:
            raise ValueError('股票与基准最近完整交易日不一致或缺失')
        data = pd.concat([stock.rename('s'), benchmark.rename('b')], axis=1).dropna()
        if len(data) < 200 or (now.date()-data.index[-1].date()).days > 5:
            raise ValueError('不足200个共同交易日或历史价格过期')
        if data.tail(200).index.to_series().diff().dt.days.max() > 14:
            raise ValueError('历史价格存在超过14日的缺口')
        s, b = data.s, data.b
        momentum, base = s.iloc[-22]/s.iloc[-148]-1, b.iloc[-22]/b.iloc[-148]-1
        return dict(status='OK', trend_asof=data.index[-1].tz_localize('UTC').isoformat(),
                    trend_adjusted_close=float(s.iloc[-1]), trend_ma50=float(s.tail(50).mean()),
                    trend_ma200=float(s.tail(200).mean()), trend_momentum_6m=float(momentum),
                    trend_benchmark_momentum_6m=float(base), trend_relative_6m=float(momentum-base),
                    trend_benchmark='^HSI' if market == 'hk' else '^GSPC',
                    trend_benchmark_close=float(b.iloc[-1]), trend_observations=len(data))
    except (ValueError, TypeError, IndexError) as exc:
        return dict(status='MISSING', reason=str(exc))


def close_series(frame, ticker):
    if isinstance(frame.columns, pd.MultiIndex):
        for key in [('Close', ticker), (ticker, 'Close')]:
            if key in frame.columns:
                return frame[key]
        return pd.Series(dtype=float)
    return frame['Close'] if 'Close' in frame else pd.Series(dtype=float)


def fetch_trend_evidence(tickers, market='us', now=None):
    from valuation import quiet_yfinance_call
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.to_datetime(now, utc=True)
    today = latest_completed(market, now).date()+pd.Timedelta(days=1)
    benchmark = '^HSI' if market == 'hk' else '^GSPC'
    tickers = list(dict.fromkeys(tickers))
    result = {t: dict(status='LIMIT', reason='本次趋势抓取达到100只上限') for t in tickers[100:]}
    for offset in range(0, min(len(tickers), 100), 20):
        batch = tickers[offset:offset+20]
        try:
            frame = quiet_yfinance_call(lambda: yf.download(
                batch+[benchmark], start=str(today-pd.Timedelta(days=550)), end=str(today),
                auto_adjust=True, threads=False, progress=False, timeout=10))
            if frame is None or frame.empty:
                raise ValueError('行情服务未返回历史价格；停止本次追加请求')
            base = close_series(frame, benchmark)
            for ticker in batch:
                result[ticker] = compute_trend(close_series(frame, ticker), base, market, now)
        except Exception:
            for ticker in tickers[offset:100]:
                result[ticker] = dict(status='MISSING', reason='历史行情请求失败；本次不生成趋势信号')
            break
    return result
