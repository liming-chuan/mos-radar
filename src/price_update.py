from __future__ import annotations

import contextlib
import io
import logging
import math
import time
from dataclasses import fields
from typing import Optional

import pandas as pd
import yfinance as yf
from valuation import AnalysisResult, quality_rating_cap, score_cashflow, score_balance
from opportunity import annotate_opportunities


RATING_ORDER = {"S": 0, "A": 1, "B": 2, "C_THIN": 3, "PASS": 4, "D_TRAP": 5, "NO_DATA": 6, "SKIP": 7, "ERROR": 8}


def _safe_float(x):
    try:
        v = float(x)
        if not math.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _retry(fn, attempts: int = 3, base_sleep: float = 0.5):
    last_error = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if i < attempts - 1:
                time.sleep(base_sleep * (i + 1))
    raise last_error


def score_margin_of_safety(mos: float | None) -> float:
    if mos is None:
        return 0
    if mos >= 0.70:
        return 40
    if mos >= 0.50:
        return 36
    if mos >= 0.35:
        return 30
    if mos >= 0.20:
        return 22
    if mos >= 0:
        return 10
    return 0


def rating_rank(rating: str) -> int:
    return RATING_ORDER.get(str(rating), 9)


def cap_rating(rating: str, cap: str) -> str:
    return cap if rating_rank(rating) < rating_rank(cap) else rating


def get_current_price(ticker: str) -> Optional[float]:
    try:
        t = yf.Ticker(ticker)
        try:
            p = _retry(lambda: t.fast_info.get("last_price"))
            p = _safe_float(p)
            if p is not None and p > 0:
                return p
        except Exception:
            pass
        info = _retry(lambda: t.info or {})
        for key in ["currentPrice", "regularMarketPrice", "previousClose"]:
            p = _safe_float(info.get(key))
            if p is not None and p > 0:
                return p
    except Exception:
        return None
    return None


def _quiet_download(tickers: list[str]) -> pd.DataFrame:
    logger = logging.getLogger("yfinance")
    old_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return yf.download(
                tickers,
                period="5d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
    except Exception:
        return pd.DataFrame()
    finally:
        logger.setLevel(old_level)


def batch_current_quotes(tickers: list[str]) -> dict[str, tuple[float, str]]:
    clean = []
    seen = set()
    for ticker in tickers:
        t = str(ticker or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            clean.append(t)
    if not clean:
        return {}

    data = _quiet_download(clean)
    prices: dict[str, tuple[float, str]] = {}
    if data is None or data.empty:
        return prices

    if isinstance(data.columns, pd.MultiIndex):
        for ticker in clean:
            close = None
            for field in ["Close"]:
                key = (field, ticker)
                if key in data.columns:
                    close = pd.to_numeric(data[key], errors="coerce").dropna()
                    break
            if close is not None and not close.empty:
                price = _safe_float(close.iloc[-1])
                if price is not None and price > 0:
                    prices[ticker] = (price, pd.to_datetime(close.index[-1], utc=True).isoformat())
    else:
        close = None
        for field in ["Close"]:
            if field in data.columns:
                close = pd.to_numeric(data[field], errors="coerce").dropna()
                break
        if close is not None and not close.empty and len(clean) == 1:
            price = _safe_float(close.iloc[-1])
            if price is not None and price > 0:
                prices[clean[0]] = (price, pd.to_datetime(close.index[-1], utc=True).isoformat())

    return prices


def batch_current_prices(tickers: list[str]) -> dict[str, float]:
    return {ticker: quote[0] for ticker, quote in batch_current_quotes(tickers).items()}


def _base_rating(row: pd.Series) -> tuple[str, str]:
    rating = str(row.get("rating", "NO_DATA") or "NO_DATA")
    if rating in {"SKIP", "ERROR"}:
        return rating, str(row.get("reason", ""))

    mos = _safe_float(row.get("margin_of_safety"))
    final_score = _safe_float(row.get("final_score")) or 0
    trap_count = int(_safe_float(row.get("trap_count")) or 0)
    roe = _safe_float(row.get("roe"))
    model_type = str(row.get("model_type", "") or "")
    method = str(row.get("valuation_method", "") or "")
    model_version = str(row.get("model_version", "") or "")
    version_label = model_version.replace("MOS_Radar_", "") if model_version else "current"

    if mos is None:
        return "NO_DATA", "价格更新后缺少安全边际，不能判断"

    if model_type == "financial_pb_roe":
        if trap_count >= 2:
            return "D_TRAP", f"价格更新后：金融股风险信号过多：{row.get('trap_flags', '')}"
        if mos >= 0.35 and final_score >= 55 and (roe is not None and roe >= 0.10):
            return "B", f"价格更新后：金融股 PB/ROE 口径有折价，仍需人工复核资产质量，估值法={method}"
        if mos >= 0:
            return "C_THIN", "价格更新后：金融股有折价但未进入 B 级"
        return "PASS", "价格更新后：当前价格高于保守 PB/ROE 价值"

    if trap_count >= 3:
        return "D_TRAP", f"价格更新后：疑似价值陷阱：{row.get('trap_flags', '')}"
    if mos >= 0.50 and final_score >= 75:
        return "S", f"价格更新后：安全边际很厚，{version_label}模型={model_type}，估值法={method}"
    if mos >= 0.35 and final_score >= 65:
        return "A", f"价格更新后：安全边际较厚，{version_label}模型={model_type}，估值法={method}"
    if mos >= 0.20 and final_score >= 55:
        return "B", f"价格更新后：有一定安全边际，{version_label}模型={model_type}，估值法={method}"
    if mos >= 0.20:
        return "C_THIN", "价格更新后：安全边际达到观察区，但综合分或质量门槛不足，未进入 B 级"
    if mos >= 0:
        return "C_THIN", "价格更新后：安全边际低于 20%，偏薄，不优先"
    return "PASS", "价格更新后：当前价格高于保守内在价值，没有安全边际"


def rerate_row(row: pd.Series) -> tuple[str, str]:
    rating, reason = _base_rating(row)
    result = AnalysisResult(ticker=str(row.get("ticker", "")))
    for field in fields(result):
        value = row.get(field.name)
        if value is not None and not pd.isna(value):
            setattr(result, field.name, value)
    cap, reasons = quality_rating_cap(result)
    saved_cap = str(row.get("rating_cap", ""))
    if saved_cap in RATING_ORDER:
        cap = cap_rating(cap or "S", saved_cap)
    if row.get("price_data_status") == "FALLBACK_PREVIOUS_PRICE":
        cap = cap_rating(cap or "S", "C_THIN")
        reasons.append("旧价格仅供参考")
    if cap:
        final = cap_rating(rating, cap)
        if final != rating:
            reason += f"；质量/风险封顶：{rating}->{final}({','.join(reasons)})"
        rating = final
    return rating, reason


def update_prices_only(df: pd.DataFrame, sleep_seconds: float = 0.0) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df
    if "price" not in df.columns:
        raise ValueError("latest result missing price column")
    if "intrinsic_value_per_share" not in df.columns:
        raise ValueError("latest result missing intrinsic_value_per_share column")

    tickers = df["ticker"].astype(str).str.strip().str.upper().tolist()
    old_price = pd.to_numeric(df["price"], errors="coerce")
    old_market_cap = pd.to_numeric(df.get("market_cap", pd.Series(index=df.index)), errors="coerce")
    prices = batch_current_quotes(tickers)

    current_prices = []
    statuses = []
    quote_dates = []
    previous_dates = df.get("price_asof", pd.Series("", index=df.index)).tolist()
    previous = df.copy()
    for ticker, fallback, old_date in zip(tickers, old_price, previous_dates):
        quote = prices.get(ticker)
        p, quote_date = quote if quote is not None else (None, "")
        if p is None:
            p = get_current_price(ticker)
            if sleep_seconds:
                time.sleep(sleep_seconds)
        if p is None or p <= 0:
            current_prices.append(fallback)
            statuses.append("FALLBACK_PREVIOUS_PRICE")
            quote_dates.append(old_date)
        else:
            current_prices.append(p)
            statuses.append("OK")
            # Timestamp unknown on the legacy single-ticker fallback: no fresh-entry claim.
            quote_dates.append(quote_date)

    df["previous_scan_price"] = old_price
    df["price"] = pd.to_numeric(pd.Series(current_prices, index=df.index), errors="coerce").fillna(old_price)
    df["price_data_status"] = statuses
    df["price_asof"] = quote_dates
    if "liquidity_volume" in df:
        df["liquidity_value"] = pd.to_numeric(df["liquidity_volume"], errors="coerce") * df["price"]

    share_count = old_market_cap / old_price.replace(0, pd.NA)
    if "market_cap" in df.columns:
        df["market_cap"] = (df["price"] * share_count).where(share_count.notna(), old_market_cap)

    if "total_debt" in df.columns or "cash" in df.columns:
        debt = pd.to_numeric(df.get("total_debt", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        cash = pd.to_numeric(df.get("cash", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        df["enterprise_value"] = pd.to_numeric(df.get("market_cap", pd.Series(index=df.index)), errors="coerce") + debt - cash

    intrinsic = pd.to_numeric(df["intrinsic_value_per_share"], errors="coerce")
    df["margin_of_safety"] = (intrinsic - df["price"]) / df["price"]
    df["price_change_since_scan"] = (df["price"] - df["previous_scan_price"]) / df["previous_scan_price"]
    df["mos_change_since_scan"] = df["margin_of_safety"] - pd.to_numeric(previous.get("margin_of_safety_at_scan", previous.get("margin_of_safety")), errors="coerce")

    if "fcf_ttm" in df.columns and "market_cap" in df.columns:
        fcf = pd.to_numeric(df["fcf_ttm"], errors="coerce")
        market_cap = pd.to_numeric(df["market_cap"], errors="coerce")
        df["fcf_yield"] = fcf / market_cap
        if "model_type" in df.columns:
            financial_mask = df["model_type"].astype(str).eq("financial_pb_roe")
            df.loc[financial_mask, "fcf_yield"] = pd.NA

    old_mos_score = pd.to_numeric(df.get("mos_score", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    new_mos_score = df["margin_of_safety"].map(score_margin_of_safety)
    df["mos_score"] = new_mos_score
    if "final_score" in df.columns:
        df["final_score"] = pd.to_numeric(df["final_score"], errors="coerce").fillna(0) - old_mos_score + new_mos_score

    # Yield and debt/market-cap scores also move with price.
    for index, r in df.iterrows():
        if r.get("model_type") == "financial_pb_roe":
            continue
        cashflow = score_cashflow(*[_safe_float(r.get(k)) for k in ("fcf_ttm", "fcf_5y_avg", "fcf_yield", "fcf_conversion")])
        balance = score_balance(*[_safe_float(r.get(k)) for k in ("cash", "total_debt", "debt_to_ebitda", "net_cash", "market_cap")])
        df.at[index, "final_score"] = (_safe_float(r.get("final_score")) or 0) - (_safe_float(r.get("cashflow_score")) or 0) - (_safe_float(r.get("balance_sheet_score")) or 0) + cashflow + balance
        df.at[index, "cashflow_score"] = cashflow
        df.at[index, "balance_sheet_score"] = balance

    rerated = df.apply(rerate_row, axis=1)
    df["rating"] = [x[0] for x in rerated]
    df["reason"] = [x[1] for x in rerated]
    return annotate_opportunities(df, previous=previous)
