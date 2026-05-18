from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "hk_universe_seed.csv"
OUT_PATH = ROOT / "data" / "hk_universe.csv"
UNIVERSE_COLUMNS = ["ticker", "name", "source", "market_cap", "liquidity_volume", "volume_source", "avg_volume", "last_price"]


def getenv_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or str(value).strip() == "" else int(value)


def normalize_hk_ticker(value: str) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    if raw.endswith(".HK"):
        base = raw[:-3]
        return f"{int(base):04d}.HK" if base.isdigit() else raw
    raw = raw.replace("HK:", "").replace("HK", "").replace(".", "")
    if raw.isdigit():
        return f"{int(raw):04d}.HK"
    return ""


def safe_float(value):
    try:
        if value is None:
            return None
        value = float(str(value).replace(",", ""))
        if value != value:
            return None
        return value
    except Exception:
        return None


def load_seed() -> pd.DataFrame:
    if not SEED_PATH.exists():
        raise FileNotFoundError(f"Missing {SEED_PATH}")
    df = pd.read_csv(SEED_PATH)
    if "ticker" not in df.columns:
        raise ValueError("hk_universe_seed.csv must contain ticker column")
    df["ticker"] = df["ticker"].map(normalize_hk_ticker)
    df = df[df["ticker"] != ""].drop_duplicates(subset=["ticker"])
    if "name" not in df.columns:
        df["name"] = ""
    return df[["ticker", "name"]]


def batch_prices(tickers: list[str]) -> dict[str, dict[str, float | None]]:
    if not tickers:
        return {}
    try:
        data = yf.download(tickers, period="10d", interval="1d", auto_adjust=False, progress=False, threads=True)
    except Exception:
        data = pd.DataFrame()
    out: dict[str, dict[str, float | None]] = {}
    if data is None or data.empty:
        return out
    for ticker in tickers:
        close = None
        volume = None
        if isinstance(data.columns, pd.MultiIndex):
            for field in ["Adj Close", "Close"]:
                key = (field, ticker)
                if key in data.columns:
                    close = pd.to_numeric(data[key], errors="coerce").dropna()
                    break
            key = ("Volume", ticker)
            if key in data.columns:
                volume = pd.to_numeric(data[key], errors="coerce").dropna()
        else:
            for field in ["Adj Close", "Close"]:
                if field in data.columns:
                    close = pd.to_numeric(data[field], errors="coerce").dropna()
                    break
            if "Volume" in data.columns:
                volume = pd.to_numeric(data["Volume"], errors="coerce").dropna()
        last_price = safe_float(close.iloc[-1]) if close is not None and not close.empty else None
        avg_volume = safe_float(volume.tail(10).mean()) if volume is not None and not volume.empty else None
        out[ticker] = {"last_price": last_price, "avg_volume": avg_volume}
    return out


def fast_info_rows(seed: pd.DataFrame, sleep_seconds: float = 0.03) -> pd.DataFrame:
    tickers = seed["ticker"].tolist()
    prices = batch_prices(tickers)
    rows = []
    total = len(tickers)
    name_map = dict(zip(seed["ticker"], seed["name"]))
    for i, ticker in enumerate(tickers, 1):
        print(f"[{i}/{total}] verifying {ticker}", flush=True)
        price = prices.get(ticker, {}).get("last_price")
        avg_volume = prices.get(ticker, {}).get("avg_volume")
        market_cap = None
        try:
            t = yf.Ticker(ticker)
            fi = t.fast_info
            try:
                market_cap = safe_float(fi.get("market_cap"))
            except Exception:
                market_cap = safe_float(getattr(fi, "market_cap", None))
            if price is None:
                try:
                    price = safe_float(fi.get("last_price"))
                except Exception:
                    price = safe_float(getattr(fi, "last_price", None))
        except Exception as e:
            print(f"skip fast_info {ticker}: {type(e).__name__}: {e}", flush=True)
        if sleep_seconds:
            time.sleep(sleep_seconds)
        if price is None or price <= 0:
            continue
        rows.append({
            "ticker": ticker,
            "name": name_map.get(ticker, ""),
            "source": "hk_seed_yfinance",
            "market_cap": market_cap,
            "liquidity_volume": avg_volume,
            "volume_source": "10d_avg_volume",
            "avg_volume": avg_volume,
            "last_price": price,
        })
    return pd.DataFrame(rows, columns=UNIVERSE_COLUMNS)


def build_universe() -> pd.DataFrame:
    limit = getenv_int("HK_UNIVERSE_LIMIT", getenv_int("UNIVERSE_LIMIT", 300))
    min_market_cap = getenv_int("HK_MIN_MARKET_CAP", getenv_int("MIN_MARKET_CAP", 5_000_000_000))
    min_liquidity_volume = getenv_int("HK_MIN_LIQUIDITY_VOLUME", getenv_int("MIN_LIQUIDITY_VOLUME", 500_000))
    print(
        "HK universe config:",
        f"limit={limit}",
        f"min_market_cap={min_market_cap}",
        f"min_liquidity_volume={min_liquidity_volume}",
        flush=True,
    )
    seed = load_seed()
    print("seed tickers:", len(seed), flush=True)
    df = fast_info_rows(seed)
    if df.empty:
        print("WARNING: HK verification returned zero rows; keeping existing hk_universe.csv if available.", flush=True)
        if OUT_PATH.exists():
            existing = pd.read_csv(OUT_PATH)
            for column in UNIVERSE_COLUMNS:
                if column not in existing.columns:
                    existing[column] = pd.NA
            return existing[UNIVERSE_COLUMNS].reset_index(drop=True)
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
    df["liquidity_volume"] = pd.to_numeric(df["liquidity_volume"], errors="coerce")
    df = df[
        (df["market_cap"].fillna(0) >= min_market_cap)
        & (df["liquidity_volume"].fillna(0) >= min_liquidity_volume)
    ]
    print("rows after HK filters:", len(df), flush=True)
    df = df.sort_values("market_cap", ascending=False, na_position="last")
    if limit > 0:
        df = df.head(limit)
    return df[UNIVERSE_COLUMNS].reset_index(drop=True)


def main():
    df = build_universe()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print("saved:", OUT_PATH)
    print("final HK universe size:", len(df))
    print(df.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
