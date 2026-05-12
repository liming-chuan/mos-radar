from __future__ import annotations

import os
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "universe.csv"

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

BAD_TICKERS = {"FI", "K"}


def getenv_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or str(value).strip() == "" else int(value)


def getenv_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None or str(value).strip() == "" else float(value)


def yahoo_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace(".", "-").replace("/", "-")


def is_bad_symbol(symbol: str) -> bool:
    s = str(symbol).strip().upper()
    if not s:
        return True
    if s in BAD_TICKERS:
        return True
    if "$" in s or "^" in s or " " in s:
        return True
    if len(s) > 6:
        return True
    return False


def is_bad_name(name: str) -> bool:
    n = str(name).lower()
    bad_keywords = [
        "warrant", "rights", "right", "unit", "units",
        "preferred", "preference", "depositary",
        "acquisition corp", "acquisition corporation",
        "blank check", "spac",
    ]
    return any(k in n for k in bad_keywords)


def fetch_nasdaq_listed() -> pd.DataFrame:
    text = requests.get(NASDAQ_LISTED_URL, timeout=30).text
    df = pd.read_csv(StringIO(text), sep="|")
    df = df[df["Symbol"].astype(str) != "File Creation Time"]

    out = pd.DataFrame()
    out["ticker"] = df["Symbol"].map(yahoo_symbol)
    out["name"] = df["Security Name"].astype(str)
    out["is_etf"] = df["ETF"].astype(str).str.upper()
    out["is_test"] = df["Test Issue"].astype(str).str.upper()
    out["source"] = "nasdaq"
    return out


def fetch_other_listed() -> pd.DataFrame:
    text = requests.get(OTHER_LISTED_URL, timeout=30).text
    df = pd.read_csv(StringIO(text), sep="|")
    df = df[df["ACT Symbol"].astype(str) != "File Creation Time"]

    out = pd.DataFrame()
    out["ticker"] = df["ACT Symbol"].map(yahoo_symbol)
    out["name"] = df["Security Name"].astype(str)
    out["is_etf"] = df["ETF"].astype(str).str.upper()
    out["is_test"] = df["Test Issue"].astype(str).str.upper()
    out["source"] = "other"
    return out


def fast_info_value(fast_info, key: str):
    try:
        return fast_info[key]
    except Exception:
        try:
            return getattr(fast_info, key)
        except Exception:
            return None


def verify_ticker(ticker: str, sleep_seconds: float) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info

        price = (
            fast_info_value(fi, "last_price")
            or fast_info_value(fi, "lastPrice")
            or fast_info_value(fi, "regular_market_price")
            or fast_info_value(fi, "regularMarketPrice")
        )

        market_cap = (
            fast_info_value(fi, "market_cap")
            or fast_info_value(fi, "marketCap")
        )

        avg_volume = (
            fast_info_value(fi, "three_month_average_volume")
            or fast_info_value(fi, "ten_day_average_volume")
            or fast_info_value(fi, "average_volume")
            or fast_info_value(fi, "averageVolume")
        )

        time.sleep(sleep_seconds)

        if price is None or float(price) <= 0:
            return None

        return {
            "ticker": ticker,
            "last_price": float(price),
            "market_cap": float(market_cap) if market_cap else None,
            "avg_volume": float(avg_volume) if avg_volume else None,
        }

    except Exception as e:
        print(f"skip {ticker}: {e}")
        time.sleep(sleep_seconds)
        return None


def build_universe() -> pd.DataFrame:
    limit = getenv_int("UNIVERSE_LIMIT", 500)
    min_market_cap = getenv_int("MIN_MARKET_CAP", 1_000_000_000)
    min_avg_volume = getenv_int("MIN_AVG_VOLUME", 100_000)
    sleep_seconds = getenv_float("UNIVERSE_SLEEP_SECONDS", 0.05)

    print("Fetching official symbol lists...")

    raw = pd.concat(
        [fetch_nasdaq_listed(), fetch_other_listed()],
        ignore_index=True,
    )

    raw = raw.drop_duplicates(subset=["ticker"])
    raw = raw[raw["is_etf"] == "N"]
    raw = raw[raw["is_test"] == "N"]
    raw = raw[~raw["ticker"].map(is_bad_symbol)]
    raw = raw[~raw["name"].map(is_bad_name)]

    tickers = raw["ticker"].tolist()
    print("raw filtered candidates:", len(tickers))

    verified = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        print(f"[{i}/{total}] verifying {ticker}", flush=True)
        item = verify_ticker(ticker, sleep_seconds)
        if item:
            verified.append(item)

    vdf = pd.DataFrame(verified)
    merged = raw.merge(vdf, on="ticker", how="inner")

    merged["market_cap"] = pd.to_numeric(merged["market_cap"], errors="coerce")
    merged["avg_volume"] = pd.to_numeric(merged["avg_volume"], errors="coerce")

    merged = merged[
        (merged["market_cap"].fillna(0) >= min_market_cap)
        & (merged["avg_volume"].fillna(0) >= min_avg_volume)
    ]

    merged = merged.sort_values("market_cap", ascending=False)

    if limit > 0:
        merged = merged.head(limit)

    return merged[
        ["ticker", "name", "source", "market_cap", "avg_volume", "last_price"]
    ].reset_index(drop=True)


def main():
    df = build_universe()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)

    print("saved:", OUT_PATH)
    print("final universe size:", len(df))
    print(df.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
