from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import yfinance as yf


def _safe_float(x):
    try:
        v = float(x)
        if v != v:
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


def get_current_price(ticker: str) -> Optional[float]:
    try:
        t = yf.Ticker(ticker)
        try:
            p = _retry(lambda: t.fast_info.get("last_price"))
            p = _safe_float(p)
            if p:
                return p
        except Exception:
            pass
        info = _retry(lambda: t.info or {})
        for key in ["currentPrice", "regularMarketPrice", "previousClose"]:
            p = _safe_float(info.get(key))
            if p:
                return p
    except Exception:
        return None
    return None


def update_prices_only(df: pd.DataFrame, sleep_seconds: float = 0.0) -> pd.DataFrame:
    df = df.copy()
    if "price" not in df.columns:
        raise ValueError("latest result missing price column")
    if "intrinsic_value_per_share" not in df.columns:
        raise ValueError("latest result missing intrinsic_value_per_share column")

    old_price = pd.to_numeric(df["price"], errors="coerce")
    current_prices = []
    for ticker in df["ticker"].astype(str):
        p = get_current_price(ticker)
        current_prices.append(p)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    df["previous_scan_price"] = old_price
    df["price"] = pd.Series(current_prices).fillna(old_price)
    intrinsic = pd.to_numeric(df["intrinsic_value_per_share"], errors="coerce")
    df["margin_of_safety"] = (intrinsic - df["price"]) / df["price"]
    df["price_change_since_scan"] = (df["price"] - df["previous_scan_price"]) / df["previous_scan_price"]
    df["mos_change_since_scan"] = df["margin_of_safety"] - pd.to_numeric(df.get("margin_of_safety_at_scan", df["margin_of_safety"]), errors="coerce")

    return df
