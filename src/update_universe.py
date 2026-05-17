from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "universe.csv"

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"

BAD_TICKERS = {"FI", "K"}
UNIVERSE_COLUMNS = ["ticker", "name", "source", "market_cap", "liquidity_volume", "volume_source", "avg_volume", "last_price"]
QUOTE_COLUMNS = ["ticker", "last_price", "market_cap", "liquidity_volume", "volume_source", "avg_volume"]


def getenv_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or str(value).strip() == "" else int(value)


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


def safe_float(value):
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
            if value.upper() in {"N/A", "NA", ""}:
                return None
        value = float(value)
        if value != value:
            return None
        return value
    except Exception:
        return None


def first_numeric(row: dict, keys: list[str]) -> tuple[float | None, str]:
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None:
            return value, key
    return None, ""


def fetch_nasdaq_screener_quotes(timeout: int = 30) -> pd.DataFrame:
    response = requests.get(
        NASDAQ_SCREENER_URL,
        params={
            "tableonly": "true",
            "limit": "25000",
            "offset": "0",
            "download": "true",
        },
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
        },
    )
    response.raise_for_status()
    rows = response.json().get("data", {}).get("rows", [])

    out = []
    for row in rows:
        ticker = yahoo_symbol(row.get("symbol", ""))
        price = safe_float(row.get("lastsale"))
        market_cap = safe_float(row.get("marketCap"))
        avg_volume, avg_source = first_numeric(row, ["avgVolume", "averageVolume", "averageVolume10Day", "averageDailyVolume3Month"])
        last_volume = safe_float(row.get("volume"))
        liquidity_volume = avg_volume if avg_volume is not None else last_volume
        volume_source = avg_source if avg_volume is not None else "last_volume"

        if not ticker or price is None or price <= 0:
            continue

        out.append({
            "ticker": ticker,
            "last_price": price,
            "market_cap": market_cap,
            "liquidity_volume": liquidity_volume,
            "volume_source": volume_source,
            "avg_volume": avg_volume,
        })

    return pd.DataFrame(out, columns=QUOTE_COLUMNS)


def build_universe() -> pd.DataFrame:
    limit = getenv_int("UNIVERSE_LIMIT", 1000)
    min_market_cap = getenv_int("MIN_MARKET_CAP", 1_000_000_000)
    min_liquidity_volume = getenv_int("MIN_LIQUIDITY_VOLUME", getenv_int("MIN_AVG_VOLUME", 100_000))
    print(
        "Universe config:",
        f"limit={limit}",
        f"min_market_cap={min_market_cap}",
        f"min_liquidity_volume={min_liquidity_volume}",
        flush=True,
    )
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

    print("Fetching Nasdaq screener quote data...", flush=True)
    try:
        vdf = fetch_nasdaq_screener_quotes()
        print("Nasdaq screener quote rows:", len(vdf), flush=True)
    except Exception as e:
        print(f"Nasdaq screener quote fetch failed: {type(e).__name__}: {e}", flush=True)
        vdf = pd.DataFrame(columns=QUOTE_COLUMNS)

    if vdf.empty:
        print(
            "WARNING: Nasdaq screener returned zero rows. "
            "Keeping existing data/universe.csv if available instead of writing an empty universe.",
            flush=True,
        )
        if OUT_PATH.exists():
            existing = pd.read_csv(OUT_PATH)
            for column in UNIVERSE_COLUMNS:
                if column not in existing.columns:
                    existing[column] = pd.NA
            return existing[UNIVERSE_COLUMNS].reset_index(drop=True)
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    merged = raw.merge(vdf, on="ticker", how="inner")
    print("merged quote rows:", len(merged), flush=True)

    merged["market_cap"] = pd.to_numeric(merged["market_cap"], errors="coerce")
    merged["liquidity_volume"] = pd.to_numeric(merged["liquidity_volume"], errors="coerce")
    if "avg_volume" in merged.columns:
        merged["avg_volume"] = pd.to_numeric(merged["avg_volume"], errors="coerce")

    merged = merged[
        (merged["market_cap"].fillna(0) >= min_market_cap)
        & (merged["liquidity_volume"].fillna(0) >= min_liquidity_volume)
    ]
    source_counts = merged["volume_source"].fillna("unknown").value_counts().to_dict() if "volume_source" in merged.columns else {}
    print("rows after market cap / liquidity filters:", len(merged), flush=True)
    print("liquidity volume source distribution:", source_counts, flush=True)

    merged = merged.sort_values("market_cap", ascending=False)

    if limit > 0:
        merged = merged.head(limit)

    return merged[UNIVERSE_COLUMNS].reset_index(drop=True)


def main():
    df = build_universe()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)

    print("saved:", OUT_PATH)
    print("final universe size:", len(df))
    print(df.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
