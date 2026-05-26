from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Iterable

import pandas as pd

from historical_replay import fetch_historical_prices, run_historical_replay
from report import money, pct, rating_badge
from valuation import MODEL_VERSION, safe_float


US_DEFAULT_BEAR_DATES = "2009-03-09,2020-03-23,2022-10-14"
HK_DEFAULT_BEAR_DATES = "2016-02-12,2020-03-23,2022-10-31"
DEFAULT_FORWARD_WINDOWS = "365,730,1095"
DEFAULT_COHORT_RATINGS = "S,A,B"


@dataclass(frozen=True)
class BearValidationConfig:
    market: str
    bear_dates: list[str]
    forward_windows: list[int]
    benchmark_ticker: str
    cohort_ratings: set[str]
    include_holdings: bool = False


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
