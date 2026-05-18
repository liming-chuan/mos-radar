from __future__ import annotations

import contextlib
import io
import logging
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf

from valuation import (
    MODEL_VERSION,
    AnalysisResult,
    cap_rating,
    quality_rating_cap,
    safe_float,
    score_balance,
    score_cashflow,
    score_margin_of_safety,
    update_buy_prices,
)


def parse_backtest_date(value: str) -> datetime:
    return datetime.strptime(str(value).strip(), "%Y-%m-%d")


def _clean_ticker(ticker: Any) -> str:
    return str(ticker or "").strip().upper()


def _quiet_yfinance_download(tickers: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    logger = logging.getLogger("yfinance")
    old_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return yf.download(
                tickers,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                auto_adjust=False,
                progress=False,
                threads=True,
            )
    except Exception:
        return pd.DataFrame()
    finally:
        logger.setLevel(old_level)


def fetch_historical_prices(tickers: list[str], backtest_date: str, lookahead_days: int = 7) -> dict[str, float]:
    start = parse_backtest_date(backtest_date)
    end = start + timedelta(days=lookahead_days)
    clean = [_clean_ticker(t) for t in tickers if _clean_ticker(t)]
    if not clean:
        return {}

    data = _quiet_yfinance_download(clean, start, end)

    prices: dict[str, float] = {}
    if data is None or data.empty:
        return prices

    if isinstance(data.columns, pd.MultiIndex):
        for ticker in clean:
            close = None
            for field in ["Adj Close", "Close"]:
                key = (field, ticker)
                if key in data.columns:
                    close = pd.to_numeric(data[key], errors="coerce").dropna()
                    break
            if close is not None and not close.empty:
                price = safe_float(close.iloc[0])
                if price is not None and price > 0:
                    prices[ticker] = price
    else:
        close = None
        for field in ["Adj Close", "Close"]:
            if field in data.columns:
                close = pd.to_numeric(data[field], errors="coerce").dropna()
                break
        if close is not None and not close.empty and len(clean) == 1:
            price = safe_float(close.iloc[0])
            if price is not None and price > 0:
                prices[clean[0]] = price

    missing = len(clean) - len(prices)
    print(
        f"Historical prices {backtest_date}: found {len(prices)}/{len(clean)}, missing {missing}. "
        "Missing usually means the stock was not listed yet or Yahoo has no historical price for that date.",
        flush=True,
    )

    return prices


def _result_from_row(row: pd.Series) -> AnalysisResult:
    result = AnalysisResult(ticker=_clean_ticker(row.get("ticker")))
    valid_fields = {f.name for f in fields(AnalysisResult)}

    for name in valid_fields:
        if name not in row.index:
            continue
        value = row.get(name)
        if pd.isna(value):
            continue
        setattr(result, name, value)

    numeric_fields = {
        "price", "market_cap", "enterprise_value", "financial_to_quote_fx", "revenue_ttm", "revenue_5y_cagr",
        "gross_margin", "operating_margin", "net_margin", "net_income_ttm",
        "reported_fcf_ttm", "sbc_ttm", "fcf_ttm", "fcf_3y_avg", "fcf_5y_avg",
        "fcf_volatility", "fcf_yield", "fcf_conversion", "cash", "total_debt",
        "net_cash", "total_assets", "total_liabilities", "ncav", "tangible_equity",
        "ebitda", "debt_to_ebitda", "interest_coverage", "equity", "roe",
        "share_dilution_3y", "intrinsic_value_total", "intrinsic_value_per_share",
        "buy_price_20mos", "buy_price_35mos", "buy_price_50mos",
        "margin_of_safety", "risk_free_rate", "discount_rate_used", "accrual_ratio",
        "mos_score", "cashflow_score", "balance_sheet_score", "quality_score",
        "trend_score", "data_quality_score", "confidence_score", "final_score",
        "current_price", "current_market_cap", "return_since_backtest",
    }
    for name in numeric_fields:
        setattr(result, name, safe_float(getattr(result, name, None)))

    trap_count = safe_float(getattr(result, "trap_count", 0))
    result.trap_count = int(trap_count or 0)

    return result


def _reprice_result(result: AnalysisResult, historical_price: float, backtest_date: str) -> AnalysisResult:
    current_price = safe_float(result.price)
    current_market_cap = safe_float(result.market_cap)
    historical_price = safe_float(historical_price)

    result.is_historical_replay = True
    result.backtest_date = backtest_date
    result.current_price = current_price
    result.current_market_cap = current_market_cap

    if historical_price is None or historical_price <= 0:
        result.historical_price_status = "NO_HISTORICAL_PRICE"
        result.rating = "SKIP"
        result.reason = f"历史价格压力测试跳过：{backtest_date} 附近无历史价格，通常是当时未上市、改名、分拆或 Yahoo 无数据"
        return result

    result.historical_price_status = "OK"
    share_count = None
    if current_price is not None and current_price > 0 and current_market_cap is not None and current_market_cap > 0:
        share_count = current_market_cap / current_price

    result.price = historical_price
    if share_count is not None and share_count > 0:
        result.market_cap = historical_price * share_count
        if result.intrinsic_value_total is not None and result.intrinsic_value_total > 0:
            result.intrinsic_value_per_share = result.intrinsic_value_total / share_count

    if current_price is not None and current_price > 0:
        result.return_since_backtest = current_price / historical_price - 1

    if result.market_cap is not None:
        result.enterprise_value = (result.market_cap or 0) + (result.total_debt or 0) - (result.cash or 0)

    if result.intrinsic_value_per_share is None or result.intrinsic_value_per_share <= 0:
        result.margin_of_safety = None
        result.rating = "NO_DATA"
        result.reason = "历史回放缺少保守价值/股，不能判断"
        return result

    result.margin_of_safety = (result.intrinsic_value_per_share - result.price) / result.price
    update_buy_prices(result)
    result.mos_score = score_margin_of_safety(result.margin_of_safety)

    if result.model_type == "financial_pb_roe":
        result.fcf_yield = None
        result.debt_to_ebitda = None
        result.final_score = result.mos_score + result.quality_score + result.data_quality_score
    else:
        if result.market_cap is not None and result.market_cap > 0 and result.fcf_ttm is not None:
            result.fcf_yield = result.fcf_ttm / result.market_cap
        result.cashflow_score = score_cashflow(
            result.fcf_ttm,
            result.fcf_5y_avg,
            result.fcf_yield,
            result.fcf_conversion,
        )
        result.balance_sheet_score = score_balance(
            result.cash,
            result.total_debt,
            result.debt_to_ebitda,
            result.net_cash,
            result.market_cap,
        )
        result.final_score = (
            result.mos_score
            + result.cashflow_score
            + result.balance_sheet_score
            + result.quality_score
            + result.trend_score
            + result.data_quality_score
        )

    if result.model_type == "financial_pb_roe":
        if result.trap_count >= 2:
            result.rating = "D_TRAP"
            result.reason = f"历史价格压力测试：金融股风险信号过多：{result.trap_flags}"
        elif result.margin_of_safety >= 0.35 and result.final_score >= 55 and (result.roe is not None and result.roe >= 0.10):
            result.rating = "B"
            result.reason = "历史价格压力测试：金融股 PB/ROE 口径有折价，仍需人工复核当时资产质量"
        elif result.margin_of_safety >= 0:
            result.rating = "C_THIN"
            result.reason = "历史价格压力测试：金融股有折价但未进入 B 级"
        else:
            result.rating = "PASS"
            result.reason = "历史价格压力测试：金融股当日价格高于保守 PB/ROE 价值"
    elif result.trap_count >= 3:
        result.rating = "D_TRAP"
        result.reason = f"历史价格压力测试：疑似价值陷阱：{result.trap_flags}"
    elif result.margin_of_safety >= 0.50 and result.final_score >= 75:
        result.rating = "S"
        result.reason = f"历史价格压力测试：安全边际很厚，{MODEL_VERSION.replace('MOS_Radar_', '')}模型={result.model_type}"
    elif result.margin_of_safety >= 0.35 and result.final_score >= 65:
        result.rating = "A"
        result.reason = f"历史价格压力测试：安全边际较厚，{MODEL_VERSION.replace('MOS_Radar_', '')}模型={result.model_type}"
    elif result.margin_of_safety >= 0.20 and result.final_score >= 55:
        result.rating = "B"
        result.reason = f"历史价格压力测试：有一定安全边际，{MODEL_VERSION.replace('MOS_Radar_', '')}模型={result.model_type}"
    elif result.margin_of_safety >= 0:
        result.rating = "C_THIN"
        result.reason = "历史价格压力测试：安全边际偏薄"
    else:
        result.rating = "PASS"
        result.reason = "历史价格压力测试：当日价格高于保守内在价值"

    cap, cap_reasons = quality_rating_cap(result)
    if cap is not None:
        original_rating = result.rating
        result.rating = cap_rating(result.rating, cap)
        if result.rating != original_rating:
            result.rating_cap = cap
            result.reason += f"；质量/风险封顶：{original_rating}->{result.rating}({','.join(cap_reasons)})"

    return result


def apply_historical_replay(df: pd.DataFrame, prices: dict[str, float], backtest_date: str) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        result = _result_from_row(row)
        ticker = _clean_ticker(result.ticker)
        historical_price = prices.get(ticker)
        rows.append(asdict(_reprice_result(result, historical_price, backtest_date)))
    return pd.DataFrame(rows)


def run_historical_replay(df: pd.DataFrame, backtest_date: str) -> pd.DataFrame:
    tickers = [_clean_ticker(t) for t in df.get("ticker", pd.Series(dtype=object)).tolist()]
    prices = fetch_historical_prices(tickers, backtest_date)
    return apply_historical_replay(df, prices, backtest_date)
