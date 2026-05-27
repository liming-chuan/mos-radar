from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Iterable

import pandas as pd

from historical_replay import apply_historical_replay, fetch_historical_prices, run_historical_replay, _quiet_yfinance_download
from report import money, pct, rating_badge
from valuation import MODEL_VERSION, safe_float


US_DEFAULT_BEAR_DATES = "2009-03-09,2020-03-23,2022-10-14"
HK_DEFAULT_BEAR_DATES = "2016-02-12,2020-03-23,2022-10-31"
DEFAULT_FORWARD_WINDOWS = "365,730,1095"
DEFAULT_COHORT_RATINGS = "S,A,B"
DEFAULT_SAMPLE_EVERY_N_DAYS = 5
DEFAULT_MAX_SAMPLE_DATES = 20
DEFAULT_PRICE_BATCH_SIZE = 150


@dataclass(frozen=True)
class BearValidationConfig:
    market: str
    bear_dates: list[str]
    forward_windows: list[int]
    benchmark_ticker: str
    cohort_ratings: set[str]
    include_holdings: bool = False


@dataclass(frozen=True)
class BearRangeValidationConfig:
    market: str
    bear_start: str
    bear_end: str
    sample_every_n_days: int
    max_sample_dates: int
    forward_windows: list[int]
    benchmark_ticker: str
    cohort_ratings: set[str]
    include_holdings: bool = False
    price_batch_size: int = DEFAULT_PRICE_BATCH_SIZE


def default_bear_dates(market: str) -> str:
    return HK_DEFAULT_BEAR_DATES if str(market).lower() == "hk" else US_DEFAULT_BEAR_DATES


def default_benchmark(market: str) -> str:
    return "2800.HK" if str(market).lower() == "hk" else "SPY"


def parse_csv_list(value: str | None, default: str) -> list[str]:
    raw = value if value is not None and str(value).strip() else default
    out: list[str] = []
    seen: set[str] = set()
    for item in str(raw).replace("\n", ",").split(","):
        x = item.strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def parse_bear_dates(value: str | None, market: str) -> list[str]:
    dates = parse_csv_list(value, default_bear_dates(market))
    parsed: list[str] = []
    for item in dates:
        parsed.append(datetime.strptime(item, "%Y-%m-%d").strftime("%Y-%m-%d"))
    return parsed


def parse_forward_windows(value: str | None) -> list[int]:
    raw = parse_csv_list(value, DEFAULT_FORWARD_WINDOWS)
    windows: list[int] = []
    for item in raw:
        days = int(float(item))
        if days > 0 and days not in windows:
            windows.append(days)
    return windows


def parse_ratings(value: str | None) -> set[str]:
    ratings = parse_csv_list(value, DEFAULT_COHORT_RATINGS)
    return {x.strip().upper() for x in ratings if x.strip()}


def build_config(
    market: str,
    bear_dates: str | None,
    forward_windows: str | None,
    benchmark_ticker: str | None,
    cohort_ratings: str | None,
    include_holdings: bool = False,
) -> BearValidationConfig:
    market = str(market or "us").strip().lower() or "us"
    return BearValidationConfig(
        market=market,
        bear_dates=parse_bear_dates(bear_dates, market),
        forward_windows=parse_forward_windows(forward_windows),
        benchmark_ticker=(benchmark_ticker or default_benchmark(market)).strip().upper(),
        cohort_ratings=parse_ratings(cohort_ratings),
        include_holdings=include_holdings,
    )


def build_range_config(
    market: str,
    bear_start: str | None,
    bear_end: str | None,
    sample_every_n_days: str | int | None,
    max_sample_dates: str | int | None,
    forward_windows: str | None,
    benchmark_ticker: str | None,
    cohort_ratings: str | None,
    include_holdings: bool = False,
    price_batch_size: str | int | None = None,
) -> BearRangeValidationConfig:
    market = str(market or "us").strip().lower() or "us"
    default_dates = parse_bear_dates(None, market)
    start = bear_start.strip() if bear_start and str(bear_start).strip() else default_dates[-1]
    end = bear_end.strip() if bear_end and str(bear_end).strip() else default_dates[-1]
    start = datetime.strptime(start, "%Y-%m-%d").strftime("%Y-%m-%d")
    end = datetime.strptime(end, "%Y-%m-%d").strftime("%Y-%m-%d")
    if _parse_date(start) > _parse_date(end):
        raise ValueError(f"bear_start must be <= bear_end: {start} > {end}")

    every = int(float(sample_every_n_days or DEFAULT_SAMPLE_EVERY_N_DAYS))
    max_dates = int(float(max_sample_dates or DEFAULT_MAX_SAMPLE_DATES))
    batch_size = int(float(price_batch_size or DEFAULT_PRICE_BATCH_SIZE))

    return BearRangeValidationConfig(
        market=market,
        bear_start=start,
        bear_end=end,
        sample_every_n_days=max(1, every),
        max_sample_dates=max(1, max_dates),
        forward_windows=parse_forward_windows(forward_windows),
        benchmark_ticker=(benchmark_ticker or default_benchmark(market)).strip().upper(),
        cohort_ratings=parse_ratings(cohort_ratings),
        include_holdings=include_holdings,
        price_batch_size=max(10, batch_size),
    )


def _parse_date(value: str) -> datetime:
    return datetime.strptime(str(value), "%Y-%m-%d")


def _target_date(start_date: str, days: int) -> str:
    return (_parse_date(start_date) + timedelta(days=int(days))).strftime("%Y-%m-%d")


def _is_true(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _clean_tickers(values: Iterable) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        ticker = str(value or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
    return out


def _chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def generate_sample_dates(start_date: str, end_date: str, every_n_business_days: int, max_dates: int) -> list[str]:
    dates = pd.bdate_range(start=start_date, end=end_date)
    if dates.empty:
        return []
    sampled = dates[:: max(1, every_n_business_days)]
    if len(sampled) > max_dates:
        sampled = sampled[:max_dates]
    return [d.strftime("%Y-%m-%d") for d in sampled]


def _extract_close_matrix(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if data is None or data.empty:
        return pd.DataFrame()

    close = None
    if isinstance(data.columns, pd.MultiIndex):
        for field in ["Adj Close", "Close"]:
            if field in data.columns.get_level_values(0):
                close = data[field].copy()
                break
    else:
        for field in ["Adj Close", "Close"]:
            if field in data.columns and len(tickers) == 1:
                close = pd.DataFrame({tickers[0]: data[field]})
                break

    if close is None or close.empty:
        return pd.DataFrame()

    close.columns = [str(c).strip().upper() for c in close.columns]
    close = close.apply(pd.to_numeric, errors="coerce")
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close.loc[:, ~close.columns.duplicated()].sort_index()


def fetch_close_matrix(
    tickers: list[str],
    start_date: str,
    end_date: str,
    batch_size: int = DEFAULT_PRICE_BATCH_SIZE,
) -> pd.DataFrame:
    clean = _clean_tickers(tickers)
    if not clean:
        return pd.DataFrame()

    start = _parse_date(start_date)
    # yfinance end date is exclusive; add one extra day after our own lookahead buffer.
    end = _parse_date(end_date) + timedelta(days=1)
    frames: list[pd.DataFrame] = []

    for i, batch in enumerate(_chunked(clean, max(10, batch_size)), start=1):
        print(f"[price matrix batch {i}] {batch[0]}..{batch[-1]} rows target={len(batch)}", flush=True)
        data = _quiet_yfinance_download(batch, start, end)
        close = _extract_close_matrix(data, batch)
        if not close.empty:
            frames.append(close)

    if not frames:
        return pd.DataFrame()

    matrix = pd.concat(frames, axis=1)
    matrix = matrix.loc[:, ~matrix.columns.duplicated()].sort_index()
    print(f"Historical close matrix: rows={len(matrix)} cols={len(matrix.columns)}", flush=True)
    return matrix


def _prices_on_or_after(
    matrix: pd.DataFrame,
    tickers: list[str],
    date: str,
    lookahead_days: int = 10,
) -> dict[str, float]:
    if matrix.empty:
        return {}

    start = pd.Timestamp(_parse_date(date))
    end = start + pd.Timedelta(days=lookahead_days)
    window = matrix.loc[(matrix.index >= start) & (matrix.index <= end)]
    prices: dict[str, float] = {}

    if window.empty:
        return prices

    for ticker in _clean_tickers(tickers):
        if ticker not in window.columns:
            continue
        series = pd.to_numeric(window[ticker], errors="coerce").dropna()
        if series.empty:
            continue
        price = safe_float(series.iloc[0])
        if price is not None and price > 0:
            prices[ticker] = price

    return prices


def _select_cohort(df: pd.DataFrame, config: BearValidationConfig) -> pd.DataFrame:
    if df.empty or "rating" not in df.columns:
        return pd.DataFrame()
    out = df[df["rating"].astype(str).str.upper().isin(config.cohort_ratings)].copy()
    if not config.include_holdings and "is_holding" in out.columns:
        out = out[~out["is_holding"].map(_is_true)].copy()
    if "historical_price_status" in out.columns:
        out = out[out["historical_price_status"].fillna("").astype(str).isin(["OK", ""])]
    return out.reset_index(drop=True)


def _benchmark_return(benchmark_ticker: str, start_date: str, end_date: str) -> float | None:
    prices = fetch_historical_prices([benchmark_ticker], start_date, lookahead_days=10)
    start_price = safe_float(prices.get(benchmark_ticker))
    prices = fetch_historical_prices([benchmark_ticker], end_date, lookahead_days=10)
    end_price = safe_float(prices.get(benchmark_ticker))
    if start_price is None or start_price <= 0 or end_price is None or end_price <= 0:
        return None
    return end_price / start_price - 1


def _add_forward_returns(cohort: pd.DataFrame, config: BearValidationConfig, bear_date: str) -> pd.DataFrame:
    cohort = cohort.copy()
    tickers = _clean_tickers(cohort.get("ticker", pd.Series(dtype=object)).tolist())
    if not tickers:
        return cohort

    start_price = pd.to_numeric(cohort.get("price", pd.Series(index=cohort.index)), errors="coerce")

    for days in config.forward_windows:
        target = _target_date(bear_date, days)
        prices = fetch_historical_prices(tickers, target, lookahead_days=10)
        benchmark_return = _benchmark_return(config.benchmark_ticker, bear_date, target)

        future_col = f"price_after_{days}d"
        return_col = f"return_after_{days}d"
        benchmark_col = f"benchmark_return_after_{days}d"
        alpha_col = f"alpha_after_{days}d"
        beat_col = f"beat_benchmark_after_{days}d"

        cohort[future_col] = cohort["ticker"].astype(str).str.upper().map(prices)
        future_price = pd.to_numeric(cohort[future_col], errors="coerce")
        returns = future_price / start_price - 1
        cohort[return_col] = returns
        cohort[benchmark_col] = benchmark_return
        cohort[alpha_col] = returns - benchmark_return if benchmark_return is not None else pd.NA
        cohort[beat_col] = cohort[alpha_col].map(lambda x: bool(pd.notna(x) and float(x) > 0))

    return cohort


def _add_forward_returns_from_matrix(
    cohort: pd.DataFrame,
    config: BearRangeValidationConfig,
    signal_date: str,
    matrix: pd.DataFrame,
) -> pd.DataFrame:
    cohort = cohort.copy()
    tickers = _clean_tickers(cohort.get("ticker", pd.Series(dtype=object)).tolist())
    if not tickers:
        return cohort

    start_price = pd.to_numeric(cohort.get("price", pd.Series(index=cohort.index)), errors="coerce")
    benchmark_start = _prices_on_or_after(matrix, [config.benchmark_ticker], signal_date).get(config.benchmark_ticker)

    for days in config.forward_windows:
        target = _target_date(signal_date, days)
        prices = _prices_on_or_after(matrix, tickers, target)
        benchmark_end = _prices_on_or_after(matrix, [config.benchmark_ticker], target).get(config.benchmark_ticker)
        benchmark_return = None
        if benchmark_start is not None and benchmark_start > 0 and benchmark_end is not None and benchmark_end > 0:
            benchmark_return = benchmark_end / benchmark_start - 1

        future_col = f"price_after_{days}d"
        return_col = f"return_after_{days}d"
        benchmark_col = f"benchmark_return_after_{days}d"
        alpha_col = f"alpha_after_{days}d"
        beat_col = f"beat_benchmark_after_{days}d"

        cohort[future_col] = cohort["ticker"].astype(str).str.upper().map(prices)
        future_price = pd.to_numeric(cohort[future_col], errors="coerce")
        returns = future_price / start_price - 1
        cohort[return_col] = returns
        cohort[benchmark_col] = benchmark_return
        cohort[alpha_col] = returns - benchmark_return if benchmark_return is not None else pd.NA
        cohort[beat_col] = cohort[alpha_col].map(lambda x: bool(pd.notna(x) and float(x) > 0))

    return cohort


def _summary_for_date(cohort: pd.DataFrame, config: BearValidationConfig, bear_date: str) -> pd.DataFrame:
    rows: list[dict] = []
    for days in config.forward_windows:
        return_col = f"return_after_{days}d"
        benchmark_col = f"benchmark_return_after_{days}d"
        alpha_col = f"alpha_after_{days}d"
        beat_col = f"beat_benchmark_after_{days}d"

        returns = pd.to_numeric(cohort.get(return_col, pd.Series(dtype=float)), errors="coerce")
        alphas = pd.to_numeric(cohort.get(alpha_col, pd.Series(dtype=float)), errors="coerce")
        valid = returns.dropna()
        benchmark_return = None
        if benchmark_col in cohort.columns:
            bench_values = pd.to_numeric(cohort[benchmark_col], errors="coerce").dropna()
            benchmark_return = safe_float(bench_values.iloc[0]) if not bench_values.empty else None

        beat_rate = None
        if beat_col in cohort.columns and not alphas.dropna().empty:
            beat_rate = float(cohort.loc[alphas.notna(), beat_col].mean())

        rows.append(
            {
                "market": config.market,
                "bear_date": bear_date,
                "benchmark_ticker": config.benchmark_ticker,
                "forward_days": days,
                "cohort_ratings": ",".join(sorted(config.cohort_ratings)),
                "candidate_count": int(len(cohort)),
                "evaluated_count": int(len(valid)),
                "average_return": safe_float(valid.mean()) if not valid.empty else None,
                "median_return": safe_float(valid.median()) if not valid.empty else None,
                "benchmark_return": benchmark_return,
                "average_alpha": safe_float(alphas.dropna().mean()) if not alphas.dropna().empty else None,
                "median_alpha": safe_float(alphas.dropna().median()) if not alphas.dropna().empty else None,
                "beat_rate": beat_rate,
            }
        )
    return pd.DataFrame(rows)


def run_bear_validation(base_df: pd.DataFrame, config: BearValidationConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidate_frames: list[pd.DataFrame] = []
    summary_frames: list[pd.DataFrame] = []

    for bear_date in config.bear_dates:
        print(f"Bear validation replay date: {bear_date}", flush=True)
        replay_df = run_historical_replay(base_df, bear_date)
        cohort = _select_cohort(replay_df, config)
        cohort["bear_date"] = bear_date
        cohort["benchmark_ticker"] = config.benchmark_ticker
        cohort["cohort_ratings"] = ",".join(sorted(config.cohort_ratings))

        if not cohort.empty:
            cohort = _add_forward_returns(cohort, config, bear_date)

        candidate_frames.append(cohort)
        summary_frames.append(_summary_for_date(cohort, config, bear_date))

    candidates = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    summary = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    return candidates, summary


def _summary_for_range(signals: pd.DataFrame, ticker_rank: pd.DataFrame, config: BearRangeValidationConfig, sample_dates: list[str]) -> pd.DataFrame:
    rows: list[dict] = []
    for days in config.forward_windows:
        return_col = f"return_after_{days}d"
        alpha_col = f"alpha_after_{days}d"
        beat_col = f"beat_benchmark_after_{days}d"
        ticker_alpha_col = f"median_alpha_after_{days}d"

        signal_returns = pd.to_numeric(signals.get(return_col, pd.Series(dtype=float)), errors="coerce")
        signal_alphas = pd.to_numeric(signals.get(alpha_col, pd.Series(dtype=float)), errors="coerce")
        valid_signal_returns = signal_returns.dropna()
        valid_signal_alphas = signal_alphas.dropna()

        ticker_alphas = pd.to_numeric(ticker_rank.get(ticker_alpha_col, pd.Series(dtype=float)), errors="coerce").dropna()
        benchmark_values = pd.to_numeric(signals.get(f"benchmark_return_after_{days}d", pd.Series(dtype=float)), errors="coerce").dropna()

        signal_beat_rate = None
        if beat_col in signals.columns and not valid_signal_alphas.empty:
            signal_beat_rate = float(signals.loc[signal_alphas.notna(), beat_col].mean())

        ticker_beat_rate = None
        if not ticker_alphas.empty:
            ticker_beat_rate = float((ticker_alphas > 0).mean())

        rows.append(
            {
                "market": config.market,
                "bear_start": config.bear_start,
                "bear_end": config.bear_end,
                "benchmark_ticker": config.benchmark_ticker,
                "forward_days": days,
                "sample_every_n_days": config.sample_every_n_days,
                "sample_date_count": len(sample_dates),
                "total_signals": int(len(signals)),
                "unique_candidates": int(signals["ticker"].nunique()) if "ticker" in signals.columns and not signals.empty else 0,
                "evaluated_signals": int(len(valid_signal_returns)),
                "evaluated_tickers": int(len(ticker_alphas)),
                "median_signal_return": safe_float(valid_signal_returns.median()) if not valid_signal_returns.empty else None,
                "median_signal_alpha": safe_float(valid_signal_alphas.median()) if not valid_signal_alphas.empty else None,
                "signal_beat_rate": signal_beat_rate,
                "median_ticker_alpha": safe_float(ticker_alphas.median()) if not ticker_alphas.empty else None,
                "ticker_beat_rate": ticker_beat_rate,
                "median_benchmark_return": safe_float(benchmark_values.median()) if not benchmark_values.empty else None,
            }
        )
    return pd.DataFrame(rows)


def build_ticker_rank(signals: pd.DataFrame, config: BearRangeValidationConfig, sample_dates: list[str]) -> pd.DataFrame:
    if signals.empty or "ticker" not in signals.columns:
        return pd.DataFrame()

    rows: list[dict] = []
    for ticker, group in signals.groupby("ticker", dropna=False):
        row = {
            "market": config.market,
            "bear_start": config.bear_start,
            "bear_end": config.bear_end,
            "ticker": ticker,
            "company_name": group.get("company_name", pd.Series(dtype=object)).dropna().astype(str).iloc[0] if "company_name" in group.columns and not group.get("company_name", pd.Series(dtype=object)).dropna().empty else "",
            "sector": group.get("sector", pd.Series(dtype=object)).dropna().astype(str).iloc[0] if "sector" in group.columns and not group.get("sector", pd.Series(dtype=object)).dropna().empty else "",
            "signal_count": int(len(group)),
            "sample_date_count": len(sample_dates),
            "signal_frequency": len(group) / len(sample_dates) if sample_dates else None,
            "first_signal_date": str(group["signal_date"].min()) if "signal_date" in group.columns else "",
            "last_signal_date": str(group["signal_date"].max()) if "signal_date" in group.columns else "",
            "ratings_seen": ",".join(sorted(set(group.get("rating", pd.Series(dtype=object)).dropna().astype(str)))),
            "median_margin_of_safety": safe_float(pd.to_numeric(group.get("margin_of_safety", pd.Series(dtype=float)), errors="coerce").median()),
            "max_margin_of_safety": safe_float(pd.to_numeric(group.get("margin_of_safety", pd.Series(dtype=float)), errors="coerce").max()),
            "median_score": safe_float(pd.to_numeric(group.get("final_score", pd.Series(dtype=float)), errors="coerce").median()),
            "max_score": safe_float(pd.to_numeric(group.get("final_score", pd.Series(dtype=float)), errors="coerce").max()),
        }
        for days in config.forward_windows:
            return_col = f"return_after_{days}d"
            alpha_col = f"alpha_after_{days}d"
            beat_col = f"beat_benchmark_after_{days}d"
            returns = pd.to_numeric(group.get(return_col, pd.Series(dtype=float)), errors="coerce").dropna()
            alphas = pd.to_numeric(group.get(alpha_col, pd.Series(dtype=float)), errors="coerce").dropna()
            row[f"median_return_after_{days}d"] = safe_float(returns.median()) if not returns.empty else None
            row[f"median_alpha_after_{days}d"] = safe_float(alphas.median()) if not alphas.empty else None
            row[f"beat_rate_after_{days}d"] = float(group.loc[pd.to_numeric(group.get(alpha_col, pd.Series(dtype=float)), errors="coerce").notna(), beat_col].mean()) if beat_col in group.columns and not alphas.empty else None
        rows.append(row)

    rank = pd.DataFrame(rows)
    sort_cols = ["signal_count", "median_margin_of_safety", "median_score"]
    sort_cols = [c for c in sort_cols if c in rank.columns]
    return rank.sort_values(sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True) if sort_cols else rank


def run_bear_range_validation(
    base_df: pd.DataFrame,
    config: BearRangeValidationConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sample_dates = generate_sample_dates(
        config.bear_start,
        config.bear_end,
        every_n_business_days=config.sample_every_n_days,
        max_dates=config.max_sample_dates,
    )
    print(f"Bear range sample dates: {len(sample_dates)} {sample_dates}", flush=True)

    base_tickers = _clean_tickers(base_df.get("ticker", pd.Series(dtype=object)).tolist())
    matrix_tickers = _clean_tickers(base_tickers + [config.benchmark_ticker])
    max_forward = max(config.forward_windows) if config.forward_windows else 0
    matrix_end = (_parse_date(config.bear_end) + timedelta(days=max_forward + 14)).strftime("%Y-%m-%d")
    matrix = fetch_close_matrix(matrix_tickers, config.bear_start, matrix_end, batch_size=config.price_batch_size)

    signal_frames: list[pd.DataFrame] = []
    point_config = BearValidationConfig(
        market=config.market,
        bear_dates=[],
        forward_windows=config.forward_windows,
        benchmark_ticker=config.benchmark_ticker,
        cohort_ratings=config.cohort_ratings,
        include_holdings=config.include_holdings,
    )

    for sample_date in sample_dates:
        print(f"Bear range sample date: {sample_date}", flush=True)
        prices = _prices_on_or_after(matrix, base_tickers, sample_date)
        replay_df = apply_historical_replay(base_df, prices, sample_date)
        cohort = _select_cohort(replay_df, point_config)
        if cohort.empty:
            continue
        cohort["signal_date"] = sample_date
        cohort["bear_start"] = config.bear_start
        cohort["bear_end"] = config.bear_end
        cohort["benchmark_ticker"] = config.benchmark_ticker
        cohort["cohort_ratings"] = ",".join(sorted(config.cohort_ratings))
        cohort = _add_forward_returns_from_matrix(cohort, config, sample_date, matrix)
        signal_frames.append(cohort)

    signals = pd.concat(signal_frames, ignore_index=True) if signal_frames else pd.DataFrame()
    ticker_rank = build_ticker_rank(signals, config, sample_dates)
    summary = _summary_for_range(signals, ticker_rank, config, sample_dates)
    return signals, ticker_rank, summary


def _pct_cell(value) -> str:
    return pct(value)


def _summary_table(summary: pd.DataFrame) -> str:
    if summary.empty:
        return '<div class="empty">暂无可统计结果。</div>'

    rows = []
    for _, row in summary.iterrows():
        rows.append(
            f"""
            <tr>
                <td>{escape(str(row.get("bear_date", "")))}</td>
                <td class="num">{int(row.get("forward_days", 0) or 0)}</td>
                <td>{escape(str(row.get("benchmark_ticker", "")))}</td>
                <td class="num">{int(row.get("candidate_count", 0) or 0)}</td>
                <td class="num">{int(row.get("evaluated_count", 0) or 0)}</td>
                <td class="num">{_pct_cell(row.get("median_return"))}</td>
                <td class="num">{_pct_cell(row.get("benchmark_return"))}</td>
                <td class="num">{_pct_cell(row.get("median_alpha"))}</td>
                <td class="num">{_pct_cell(row.get("beat_rate"))}</td>
            </tr>
            """
        )

    return f"""
    <table>
        <thead>
            <tr>
                <th>熊市日期</th>
                <th>观察天数</th>
                <th>基准</th>
                <th>候选数</th>
                <th>有价格数</th>
                <th>候选中位收益</th>
                <th>大盘收益</th>
                <th>中位超额收益</th>
                <th>跑赢比例</th>
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _top_alpha_table(candidates: pd.DataFrame, days: int, currency_symbol: str) -> str:
    alpha_col = f"alpha_after_{days}d"
    return_col = f"return_after_{days}d"
    benchmark_col = f"benchmark_return_after_{days}d"
    if candidates.empty or alpha_col not in candidates.columns:
        return '<div class="empty">暂无候选明细。</div>'

    sample = candidates.copy()
    sample[alpha_col] = pd.to_numeric(sample[alpha_col], errors="coerce")
    sample = sample.dropna(subset=[alpha_col]).sort_values(alpha_col, ascending=False).head(30)
    if sample.empty:
        return '<div class="empty">暂无候选明细。</div>'

    rows = []
    for _, row in sample.iterrows():
        rows.append(
            f"""
            <tr>
                <td>{escape(str(row.get("bear_date", "")))}</td>
                <td class="ticker">{escape(str(row.get("ticker", "")))}</td>
                <td>{escape(str(row.get("company_name", "") or ""))}</td>
                <td>{rating_badge(str(row.get("rating", "N/A")))}</td>
                <td class="num">{_pct_cell(row.get("margin_of_safety"))}</td>
                <td class="num">{money(row.get("price"), currency_symbol)}</td>
                <td class="num">{_pct_cell(row.get(return_col))}</td>
                <td class="num">{_pct_cell(row.get(benchmark_col))}</td>
                <td class="num">{_pct_cell(row.get(alpha_col))}</td>
                <td class="num">{_pct_cell(row.get("return_since_backtest"))}</td>
            </tr>
            """
        )

    return f"""
    <table>
        <thead>
            <tr>
                <th>熊市日期</th>
                <th>代码</th>
                <th>公司</th>
                <th>评级</th>
                <th>当日安全边际</th>
                <th>当日价格</th>
                <th>{days}天收益</th>
                <th>大盘收益</th>
                <th>超额收益</th>
                <th>回放日至今</th>
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    """


def generate_bear_validation_report(
    candidates: pd.DataFrame,
    summary: pd.DataFrame,
    config: BearValidationConfig,
) -> str:
    market_label = "港股" if config.market == "hk" else "美股"
    currency_symbol = "HK$" if config.market == "hk" else "$"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    primary_window = max(config.forward_windows) if config.forward_windows else 0

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{ margin:0; padding:0; background:#f3f4f6; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,"Microsoft YaHei",sans-serif; color:#111827; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:22px; }}
    .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:20px; margin-bottom:18px; }}
    h1 {{ margin:0 0 8px 0; font-size:24px; }}
    h2 {{ margin:24px 0 12px 0; font-size:18px; }}
    .meta {{ color:#6b7280; font-size:13px; margin-bottom:12px; }}
    .warning {{ margin-top:14px; padding:12px 14px; border:1px solid #fca5a5; background:#fff1f2; color:#991b1b; border-radius:8px; line-height:1.8; font-weight:700; }}
    .note {{ background:#f9fafb; border-left:4px solid #2563eb; padding:10px 12px; color:#374151; font-size:13px; line-height:1.8; }}
    .empty {{ background:#f9fafb; border:1px dashed #d1d5db; padding:14px; color:#6b7280; border-radius:10px; }}
    table {{ border-collapse:collapse; width:100%; background:#fff; font-size:13px; }}
    th {{ text-align:left; background:#f3f4f6; color:#374151; padding:9px 8px; border:1px solid #e5e7eb; white-space:nowrap; }}
    td {{ padding:8px; border:1px solid #e5e7eb; vertical-align:top; }}
    tr:nth-child(even) td {{ background:#fafafa; }}
    .num {{ text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }}
    .ticker {{ font-weight:800; white-space:nowrap; }}
</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>【MOS Radar {escape(market_label)}】熊市候选方向性验证</h1>
        <div class="meta">生成时间：{escape(ts)} | 模式：bear_validation | 模型：{escape(MODEL_VERSION)} | 基准：{escape(config.benchmark_ticker)}</div>
        <div class="note">
            本报告把多个熊市日期作为买点压力测试，先用 MOS Radar 在该日期价格下筛出 {escape(','.join(sorted(config.cohort_ratings)))} 候选，
            再统计这些候选在后续观察窗口相对大盘的收益、超额收益和跑赢比例。它用于验证方向性，不是自动买卖建议。
        </div>
        <div class="warning">
            警告：本验证仍存在未来函数和幸存者偏差。系统使用当前可获得的财务数据、当前股票池和历史价格进行压力测试，
            不能等同于严格 point-in-time 回测。结果只能回答“这套筛选逻辑在熊市价格下是否倾向于选出未来相对强势股票”，不能证明历史当时一定可买。
        </div>
    </div>
    <div class="card">
        <h2>熊市日期汇总</h2>
        {_summary_table(summary)}
    </div>
    <div class="card">
        <h2>按 {primary_window} 天超额收益排序 Top 30</h2>
        {_top_alpha_table(candidates, primary_window, currency_symbol) if primary_window else '<div class="empty">未设置观察窗口。</div>'}
    </div>
</div>
</body>
</html>
"""


def _range_summary_table(summary: pd.DataFrame) -> str:
    if summary.empty:
        return '<div class="empty">暂无可统计结果。</div>'

    rows = []
    for _, row in summary.iterrows():
        rows.append(
            f"""
            <tr>
                <td class="num">{int(row.get("forward_days", 0) or 0)}</td>
                <td class="num">{int(row.get("sample_date_count", 0) or 0)}</td>
                <td class="num">{int(row.get("total_signals", 0) or 0)}</td>
                <td class="num">{int(row.get("unique_candidates", 0) or 0)}</td>
                <td class="num">{_pct_cell(row.get("median_signal_return"))}</td>
                <td class="num">{_pct_cell(row.get("median_benchmark_return"))}</td>
                <td class="num">{_pct_cell(row.get("median_signal_alpha"))}</td>
                <td class="num">{_pct_cell(row.get("signal_beat_rate"))}</td>
                <td class="num">{_pct_cell(row.get("median_ticker_alpha"))}</td>
                <td class="num">{_pct_cell(row.get("ticker_beat_rate"))}</td>
            </tr>
            """
        )

    return f"""
    <table>
        <thead>
            <tr>
                <th>观察天数</th>
                <th>采样日</th>
                <th>信号数</th>
                <th>股票数</th>
                <th>信号中位收益</th>
                <th>大盘中位收益</th>
                <th>信号中位Alpha</th>
                <th>信号跑赢比例</th>
                <th>股票中位Alpha</th>
                <th>股票跑赢比例</th>
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _ticker_rank_table(ticker_rank: pd.DataFrame, days: int, currency_symbol: str, title_metric: str = "frequency") -> str:
    if ticker_rank.empty:
        return '<div class="empty">暂无候选明细。</div>'

    alpha_col = f"median_alpha_after_{days}d"
    return_col = f"median_return_after_{days}d"
    beat_col = f"beat_rate_after_{days}d"
    sample = ticker_rank.copy()

    if title_metric == "alpha" and alpha_col in sample.columns:
        sample[alpha_col] = pd.to_numeric(sample[alpha_col], errors="coerce")
        sample = sample.dropna(subset=[alpha_col]).sort_values(alpha_col, ascending=False)
    else:
        sort_cols = [c for c in ["signal_count", "median_margin_of_safety", alpha_col] if c in sample.columns]
        if sort_cols:
            sample = sample.sort_values(sort_cols, ascending=[False] * len(sort_cols))

    sample = sample.head(30)
    if sample.empty:
        return '<div class="empty">暂无候选明细。</div>'

    rows = []
    for _, row in sample.iterrows():
        rows.append(
            f"""
            <tr>
                <td class="ticker">{escape(str(row.get("ticker", "")))}</td>
                <td>{escape(str(row.get("company_name", "") or ""))}</td>
                <td>{escape(str(row.get("sector", "") or ""))}</td>
                <td class="num">{int(row.get("signal_count", 0) or 0)}</td>
                <td class="num">{_pct_cell(row.get("signal_frequency"))}</td>
                <td>{escape(str(row.get("first_signal_date", "") or ""))}</td>
                <td>{escape(str(row.get("last_signal_date", "") or ""))}</td>
                <td class="num">{_pct_cell(row.get("median_margin_of_safety"))}</td>
                <td class="num">{_pct_cell(row.get("max_margin_of_safety"))}</td>
                <td class="num">{row.get("median_score", "N/A") if pd.notna(row.get("median_score", None)) else "N/A"}</td>
                <td class="num">{_pct_cell(row.get(return_col))}</td>
                <td class="num">{_pct_cell(row.get(alpha_col))}</td>
                <td class="num">{_pct_cell(row.get(beat_col))}</td>
            </tr>
            """
        )

    return f"""
    <table>
        <thead>
            <tr>
                <th>代码</th>
                <th>公司</th>
                <th>行业</th>
                <th>出现次数</th>
                <th>出现频率</th>
                <th>首次信号</th>
                <th>最后信号</th>
                <th>中位安全边际</th>
                <th>最高安全边际</th>
                <th>中位分数</th>
                <th>{days}天中位收益</th>
                <th>{days}天中位Alpha</th>
                <th>{days}天跑赢比例</th>
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    """


def generate_bear_range_validation_report(
    signals: pd.DataFrame,
    ticker_rank: pd.DataFrame,
    summary: pd.DataFrame,
    config: BearRangeValidationConfig,
) -> str:
    market_label = "港股" if config.market == "hk" else "美股"
    currency_symbol = "HK$" if config.market == "hk" else "$"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    primary_window = max(config.forward_windows) if config.forward_windows else 0
    total_signals = len(signals)
    unique_candidates = int(signals["ticker"].nunique()) if not signals.empty and "ticker" in signals.columns else 0

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{ margin:0; padding:0; background:#f3f4f6; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,"Microsoft YaHei",sans-serif; color:#111827; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:22px; }}
    .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:20px; margin-bottom:18px; }}
    h1 {{ margin:0 0 8px 0; font-size:24px; }}
    h2 {{ margin:24px 0 12px 0; font-size:18px; }}
    .meta {{ color:#6b7280; font-size:13px; margin-bottom:12px; }}
    .note {{ background:#f9fafb; border-left:4px solid #2563eb; padding:10px 12px; color:#374151; font-size:13px; line-height:1.8; }}
    .warning {{ margin-top:14px; padding:12px 14px; border:1px solid #fca5a5; background:#fff1f2; color:#991b1b; border-radius:8px; line-height:1.8; font-weight:700; }}
    .summary {{ display:table; width:100%; border-spacing:10px; margin:10px -10px 0 -10px; }}
    .box {{ display:table-cell; background:#f9fafb; border:1px solid #e5e7eb; border-radius:12px; padding:12px; }}
    .box .label {{ font-size:12px; color:#6b7280; }}
    .box .value {{ font-size:22px; font-weight:800; margin-top:4px; }}
    .empty {{ background:#f9fafb; border:1px dashed #d1d5db; padding:14px; color:#6b7280; border-radius:10px; }}
    table {{ border-collapse:collapse; width:100%; background:#fff; font-size:13px; }}
    th {{ text-align:left; background:#f3f4f6; color:#374151; padding:9px 8px; border:1px solid #e5e7eb; white-space:nowrap; }}
    td {{ padding:8px; border:1px solid #e5e7eb; vertical-align:top; }}
    tr:nth-child(even) td {{ background:#fafafa; }}
    .num {{ text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }}
    .ticker {{ font-weight:800; white-space:nowrap; }}
</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>【MOS Radar {escape(market_label)}】熊市区间安全边际方向性验证</h1>
        <div class="meta">生成时间：{escape(ts)} | 模式：bear_range_validation | 模型：{escape(MODEL_VERSION)} | 基准：{escape(config.benchmark_ticker)}</div>
        <div class="summary">
            <div class="box"><div class="label">熊市区间</div><div class="value">{escape(config.bear_start)} 至 {escape(config.bear_end)}</div></div>
            <div class="box"><div class="label">采样间隔</div><div class="value">{config.sample_every_n_days} 个交易日</div></div>
            <div class="box"><div class="label">信号总数</div><div class="value">{total_signals}</div></div>
            <div class="box"><div class="label">唯一股票数</div><div class="value">{unique_candidates}</div></div>
        </div>
        <div class="note">
            本报告在熊市区间内隔固定交易日采样，用历史价格重算安全边际，记录反复出现的 {escape(','.join(sorted(config.cohort_ratings)))} 候选，
            再统计这些候选在后续观察窗口相对大盘的表现。出现次数越多，说明该股票在熊市中不是偶然一天满足条件。
        </div>
        <div class="warning">
            警告：本验证仍存在未来函数和幸存者偏差。系统使用当前可获得财务数据和当前股票池匹配历史价格。
            结果只能验证模型方向性，不能等同于严格 point-in-time 回测，也不能替代人工复核财报、行业周期和估值实现路径。
        </div>
    </div>
    <div class="card">
        <h2>区间总体统计</h2>
        {_range_summary_table(summary)}
    </div>
    <div class="card">
        <h2>反复出现的安全边际候选 Top 30</h2>
        {_ticker_rank_table(ticker_rank, primary_window, currency_symbol, title_metric="frequency") if primary_window else '<div class="empty">未设置观察窗口。</div>'}
    </div>
    <div class="card">
        <h2>按 {primary_window} 天中位Alpha排序 Top 30</h2>
        {_ticker_rank_table(ticker_rank, primary_window, currency_symbol, title_metric="alpha") if primary_window else '<div class="empty">未设置观察窗口。</div>'}
    </div>
</div>
</body>
</html>
"""
