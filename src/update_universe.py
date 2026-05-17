from __future__ import annotations

import os
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "universe.csv"

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"

BAD_TICKERS = {"FI", "K"}
UNIVERSE_COLUMNS = ["ticker", "name", "source", "market_cap", "avg_volume", "last_price"]
QUOTE_COLUMNS = ["ticker", "last_price", "market_cap", "avg_volume"]


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


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def safe_float(value):
    try:
        if value is None:
            return None
        value = float(value)
        if value != value:
            return None
        return value
    except Exception:
        return None


def quote_value(row: dict, keys: list[str]):
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def fetch_quote_batch(batch: list[str], timeout: int = 30) -> list[dict]:
    if not batch:
        return []

    response = requests.get(
        YAHOO_QUOTE_URL,
        params={
            "symbols": ",".join(batch),
            "fields": "regularMarketPrice,marketCap,averageDailyVolume3Month,averageDailyVolume10Day",
        },
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("quoteResponse", {}).get("result", [])

    verified = []
    for row in rows:
        ticker = str(row.get("symbol", "")).strip().upper()
        price = safe_float(quote_value(row, ["regularMarketPrice", "regularMarketPreviousClose", "regularMarketOpen"]))
        market_cap = safe_float(row.get("marketCap"))
        avg_volume = safe_float(quote_value(row, ["averageDailyVolume3Month", "averageDailyVolume10Day", "regularMarketVolume"]))

        if not ticker or price is None or price <= 0:
            continue

        verified.append({
            "ticker": yahoo_symbol(ticker),
            "last_price": price,
            "market_cap": market_cap,
            "avg_volume": avg_volume,
        })

    return verified


def verify_tickers(tickers: list[str], sleep_seconds: float, batch_size: int) -> pd.DataFrame:
    verified = []
    ticker_batches = list(chunks(tickers, batch_size))
    total_batches = len(ticker_batches)

    for i, batch in enumerate(ticker_batches, 1):
        first = batch[0]
        last = batch[-1]
        print(f"[batch {i}/{total_batches}] verifying {len(batch)} tickers: {first}..{last}", flush=True)

        try:
            verified.extend(fetch_quote_batch(batch))
        except Exception as e:
            print(f"skip batch {first}..{last}: {type(e).__name__}: {e}", flush=True)

        if sleep_seconds:
            time.sleep(sleep_seconds)

    return pd.DataFrame(verified, columns=QUOTE_COLUMNS)


def verify_ticker(ticker: str, sleep_seconds: float) -> dict | None:
    try:
        rows = fetch_quote_batch([ticker])
        time.sleep(sleep_seconds)
        return rows[0] if rows else None
    except Exception as e:
        print(f"skip {ticker}: {e}")
        time.sleep(sleep_seconds)
        return None


def build_universe() -> pd.DataFrame:
    limit = getenv_int("UNIVERSE_LIMIT", 1000)
    min_market_cap = getenv_int("MIN_MARKET_CAP", 1_000_000_000)
    min_avg_volume = getenv_int("MIN_AVG_VOLUME", 100_000)
    sleep_seconds = getenv_float("UNIVERSE_SLEEP_SECONDS", 0.05)
    batch_size = getenv_int("UNIVERSE_BATCH_SIZE", 100)

    print(
        "Universe config:",
        f"limit={limit}",
        f"min_market_cap={min_market_cap}",
        f"min_avg_volume={min_avg_volume}",
        f"batch_size={batch_size}",
        f"sleep_seconds={sleep_seconds}",
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

    vdf = verify_tickers(tickers, sleep_seconds=sleep_seconds, batch_size=batch_size)
    print("verified quote rows:", len(vdf), flush=True)
    if vdf.empty:
        print(
            "WARNING: quote verification returned zero rows. "
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
    merged["avg_volume"] = pd.to_numeric(merged["avg_volume"], errors="coerce")

    merged = merged[
        (merged["market_cap"].fillna(0) >= min_market_cap)
        & (merged["avg_volume"].fillna(0) >= min_avg_volume)
    ]
    print("rows after market cap / volume filters:", len(merged), flush=True)

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
