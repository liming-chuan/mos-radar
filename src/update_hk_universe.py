from __future__ import annotations

import os
import time
import contextlib
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "hk_universe_seed.csv"
OUT_PATH = ROOT / "data" / "hk_universe.csv"
UNIVERSE_COLUMNS = ["ticker", "name", "source", "market_cap", "liquidity_volume", "volume_source", "avg_volume", "last_price"]
HKEX_SECURITIES_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"


def getenv_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or str(value).strip() == "" else int(value)


def getenv_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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


def quiet_yfinance_call(fn):
    logger = logging.getLogger("yfinance")
    old_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn()
    finally:
        logger.setLevel(old_level)


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


def _xlsx_column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    value = 0
    for ch in letters:
        value = value * 26 + ord(ch.upper()) - 64
    return value - 1


def _xlsx_rows(content: bytes) -> list[list[str]]:
    workbook = zipfile.ZipFile(io.BytesIO(content))
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared_strings: list[str] = []
    if "xl/sharedStrings.xml" in workbook.namelist():
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        for item in root.findall("a:si", ns):
            shared_strings.append("".join(t.text or "" for t in item.findall(".//a:t", ns)))

    root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))

    def cell_value(cell) -> str:
        value = cell.find("a:v", ns)
        if value is None:
            return ""
        raw = value.text or ""
        if cell.attrib.get("t") == "s":
            try:
                return shared_strings[int(raw)]
            except Exception:
                return ""
        return raw

    rows: list[list[str]] = []
    for row in root.findall(".//a:row", ns):
        values: dict[int, str] = {}
        for cell in row.findall("a:c", ns):
            values[_xlsx_column_index(cell.attrib.get("r", ""))] = cell_value(cell).strip()
        width = max(values.keys(), default=-1) + 1
        rows.append([values.get(i, "") for i in range(width)])
    return rows


def load_hkex_candidates() -> pd.DataFrame:
    response = requests.get(
        HKEX_SECURITIES_URL,
        timeout=30,
        headers={"User-Agent": "mos-radar/1.0"},
    )
    response.raise_for_status()
    rows = _xlsx_rows(response.content)
    if len(rows) < 4:
        raise ValueError("HKEX securities list is empty")

    header = rows[2]
    index = {name: i for i, name in enumerate(header)}
    required = ["Stock Code", "Name of Securities", "Category", "Sub-Category"]
    missing = [name for name in required if name not in index]
    if missing:
        raise ValueError(f"HKEX securities list missing columns: {missing}")

    records = []
    for row in rows[3:]:
        code = row[index["Stock Code"]] if len(row) > index["Stock Code"] else ""
        name = row[index["Name of Securities"]] if len(row) > index["Name of Securities"] else ""
        category = row[index["Category"]] if len(row) > index["Category"] else ""
        subcategory = row[index["Sub-Category"]] if len(row) > index["Sub-Category"] else ""
        if not re.fullmatch(r"\d{5}", str(code)):
            continue
        # Exclude RMB duplicate counters such as 80700 / 89988.
        if int(code) >= 80000:
            continue
        if category != "Equity" or "Equity Securities" not in subcategory:
            continue
        ticker = normalize_hk_ticker(code)
        if ticker:
            records.append({"ticker": ticker, "name": name.title()})

    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("HKEX securities list produced zero equity candidates")
    return df.drop_duplicates(subset=["ticker"])[["ticker", "name"]].reset_index(drop=True)


def load_candidates(source: str) -> pd.DataFrame:
    seed = load_seed()
    if source == "seed":
        print("HK candidate source: seed", flush=True)
        return seed
    try:
        hkex = load_hkex_candidates()
        print("HK candidate source: hkex", flush=True)
        print("HKEX equity candidates:", len(hkex), flush=True)
        return hkex
    except Exception as e:
        print(f"WARNING: HKEX candidate download failed: {type(e).__name__}: {e}", flush=True)
        print("HK candidate source: seed fallback", flush=True)
        return seed


def with_universe_columns(df: pd.DataFrame, source: str) -> pd.DataFrame:
    out = df.copy()
    for column in UNIVERSE_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    if "source" in out.columns:
        out["source"] = out["source"].fillna(source)
    return out[UNIVERSE_COLUMNS].reset_index(drop=True)


def existing_or_seed_universe(seed: pd.DataFrame, reason: str) -> pd.DataFrame:
    if OUT_PATH.exists():
        existing = pd.read_csv(OUT_PATH)
        if "ticker" in existing.columns and existing["ticker"].dropna().astype(str).str.strip().ne("").any():
            print(f"WARNING: {reason}; keeping existing non-empty hk_universe.csv rows={len(existing)}", flush=True)
            return with_universe_columns(existing, "hk_existing")
    print(f"WARNING: {reason}; falling back to hk_universe_seed.csv rows={len(seed)}", flush=True)
    return with_universe_columns(seed, "hk_seed_fallback")


def _batch_prices_once(tickers: list[str]) -> dict[str, dict[str, float | None]]:
    if not tickers:
        return {}
    try:
        data = quiet_yfinance_call(
            lambda: yf.download(tickers, period="10d", interval="1d", auto_adjust=False, progress=False, threads=False)
        )
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


def batch_prices(tickers: list[str], batch_size: int = 100, sleep_seconds: float = 0.05) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict[str, float | None]] = {}
    total_batches = (len(tickers) + batch_size - 1) // batch_size
    for start in range(0, len(tickers), batch_size):
        batch = tickers[start:start + batch_size]
        batch_no = start // batch_size + 1
        print(f"[price batch {batch_no}/{total_batches}] {batch[0]}..{batch[-1]}", flush=True)
        out.update(_batch_prices_once(batch))
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return out


def fast_info_rows(candidates: pd.DataFrame, fetch_market_cap: bool = False, sleep_seconds: float = 0.03) -> pd.DataFrame:
    tickers = candidates["ticker"].tolist()
    prices = batch_prices(tickers)
    rows = []
    total = len(tickers)
    name_map = dict(zip(candidates["ticker"], candidates["name"]))
    for i, ticker in enumerate(tickers, 1):
        if i == 1 or i % 100 == 0 or i == total:
            print(f"[{i}/{total}] building HK universe rows", flush=True)
        price = prices.get(ticker, {}).get("last_price")
        avg_volume = prices.get(ticker, {}).get("avg_volume")
        market_cap = None
        if fetch_market_cap or price is None:
            try:
                t = yf.Ticker(ticker)
                fi = quiet_yfinance_call(lambda: t.fast_info)
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
            "source": "hkex_yfinance" if total > 200 else "hk_seed_yfinance",
            "market_cap": market_cap,
            "liquidity_volume": avg_volume,
            "volume_source": "10d_avg_volume",
            "avg_volume": avg_volume,
            "last_price": price,
        })
    return pd.DataFrame(rows, columns=UNIVERSE_COLUMNS)


def build_universe() -> pd.DataFrame:
    limit = getenv_int("HK_UNIVERSE_LIMIT", getenv_int("UNIVERSE_LIMIT", 300))
    min_market_cap = getenv_int("HK_MIN_MARKET_CAP", getenv_int("MIN_MARKET_CAP", 500_000_000))
    min_liquidity_volume = getenv_int("HK_MIN_LIQUIDITY_VOLUME", getenv_int("MIN_LIQUIDITY_VOLUME", 500_000))
    source = os.getenv("HK_UNIVERSE_SOURCE", "hkex").strip().lower() or "hkex"
    fetch_market_cap = getenv_bool("HK_FETCH_MARKET_CAP", False)
    print(
        "HK universe config:",
        f"limit={limit}",
        f"min_market_cap={min_market_cap}",
        f"min_liquidity_volume={min_liquidity_volume}",
        f"source={source}",
        f"fetch_market_cap={fetch_market_cap}",
        flush=True,
    )
    seed = load_seed()
    candidates = load_candidates(source)
    print("seed tickers:", len(seed), flush=True)
    print("candidate tickers:", len(candidates), flush=True)
    df = fast_info_rows(candidates, fetch_market_cap=fetch_market_cap)
    if df.empty:
        return existing_or_seed_universe(seed, "HK verification returned zero rows")
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
    df["liquidity_volume"] = pd.to_numeric(df["liquidity_volume"], errors="coerce")
    before_filters = len(df)
    missing_cap = int(df["market_cap"].isna().sum())
    missing_liquidity = int(df["liquidity_volume"].isna().sum())
    print(
        "HK verification coverage:",
        f"rows={before_filters}",
        f"missing_market_cap={missing_cap}",
        f"missing_liquidity_volume={missing_liquidity}",
        flush=True,
    )
    cap_ok = df["market_cap"].isna() | (df["market_cap"] >= min_market_cap)
    liquidity_ok = df["liquidity_volume"].fillna(0) >= min_liquidity_volume
    df = df[
        cap_ok
        & liquidity_ok
    ]
    print("rows after HK filters:", len(df), flush=True)
    if df.empty:
        return existing_or_seed_universe(seed, "HK filters produced zero rows")
    df["has_market_cap"] = df["market_cap"].notna()
    df = df.sort_values(
        ["has_market_cap", "market_cap", "liquidity_volume"],
        ascending=[False, False, False],
        na_position="last",
    )
    df = df.drop(columns=["has_market_cap"])
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
