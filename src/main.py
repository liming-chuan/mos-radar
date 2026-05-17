from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from emailer import send_email
from historical_replay import run_historical_replay
from price_update import update_prices_only
from report import generate_report
from valuation import analyze_ticker, results_to_dataframe


ROOT = Path(__file__).resolve().parents[1]

UNIVERSE_PATH = ROOT / "data" / "universe.csv"
HOLDINGS_PATH = ROOT / "data" / "holdings.csv"

RESULTS_PATH = ROOT / "data" / "results" / "mos_latest.csv"
SNAPSHOT_PATH = ROOT / "data" / "results" / "mos_snapshot_latest.csv"
REPORTS_DIR = ROOT / "reports"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def getenv_int(name: str, default: int) -> int:
    try:
        value = os.getenv(name)
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except Exception:
        return default


def getenv_float(name: str, default: float) -> float:
    try:
        value = os.getenv(name)
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def detect_mode() -> str:
    forced = os.getenv("RUN_MODE")
    if forced:
        return forced

    schedule = os.getenv("GITHUB_EVENT_SCHEDULE", "")

    if "37 18" in schedule:
        return "full_after_close"
    if "31 8" in schedule:
        return "morning_email"
    if "31 12" in schedule:
        return "noon_update"
    if "31 15" in schedule:
        return "afternoon_update"

    return "manual"


def _read_ticker_csv(path: Path) -> list[str]:
    if not path.exists():
        return []

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return []
    except Exception:
        return []

    if df.empty:
        return []

    if "ticker" in df.columns:
        raw = df["ticker"].dropna().tolist()
    else:
        raw = df.iloc[:, 0].dropna().tolist()

    out = []
    seen = set()

    for x in raw:
        ticker = str(x).strip().upper()
        if not ticker or ticker == "TICKER":
            continue

        ticker = ticker.replace(".", "-").replace("/", "-")

        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)

    return out


def _normalize_tickers(raw: list[str]) -> list[str]:
    out = []
    seen = set()

    for x in raw:
        ticker = str(x).strip().upper()
        if not ticker or ticker == "TICKER":
            continue

        ticker = ticker.replace(".", "-").replace("/", "-")

        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)

    return out


def load_holdings() -> list[str]:
    holdings = _read_ticker_csv(HOLDINGS_PATH)
    env_holdings = os.getenv("HOLDINGS_TICKERS", "")
    if env_holdings.strip():
        holdings.extend(env_holdings.replace("\n", ",").split(","))
    return _normalize_tickers(holdings)


def load_scan_tickers() -> list[str]:
    universe = _read_ticker_csv(UNIVERSE_PATH)
    holdings = load_holdings()

    max_tickers = getenv_int("MAX_TICKERS", 0)
    if max_tickers > 0:
        universe = universe[:max_tickers]

    seen = set()
    tickers = []

    for ticker in universe:
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)

    # 持仓池强制加入扫描：即使不在 universe.csv 里，也会扫描
    for ticker in holdings:
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)

    return tickers


def annotate_pools(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    holdings = set(load_holdings())

    if "ticker" not in df.columns:
        return df

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["is_holding"] = df["ticker"].isin(holdings)
    df["pool"] = df["is_holding"].map(lambda x: "holding" if x else "market")

    return df


def run_full_scan() -> pd.DataFrame:
    tickers = load_scan_tickers()
    sleep_seconds = getenv_float("REQUEST_SLEEP_SECONDS", 0.2)

    results = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, start=1):
        print(f"[{i}/{total}] analyzing {ticker}", flush=True)
        results.append(analyze_ticker(ticker, sleep_seconds=sleep_seconds))

    df = results_to_dataframe(results)
    df = annotate_pools(df)

    if "margin_of_safety" in df.columns:
        df["margin_of_safety_at_scan"] = df["margin_of_safety"]

    df["scan_time"] = datetime.now().isoformat(timespec="seconds")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULTS_PATH, index=False)
    df.to_csv(SNAPSHOT_PATH, index=False)

    return df


def load_latest_or_full_scan() -> pd.DataFrame:
    if RESULTS_PATH.exists():
        df = pd.read_csv(RESULTS_PATH)
        return annotate_pools(df)

    return run_full_scan()


def save_report_files(df: pd.DataFrame, mode: str, body: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backtest_date = ""
    if mode == "historical_replay" and "backtest_date" in df.columns:
        values = df["backtest_date"].dropna().astype(str)
        backtest_date = values.iloc[0] if not values.empty else ""

    report_name = f"{mode}_{backtest_date}_{timestamp}.md" if backtest_date else f"{mode}_{timestamp}.md"
    (REPORTS_DIR / report_name).write_text(body, encoding="utf-8")
    (REPORTS_DIR / "latest_report.md").write_text(body, encoding="utf-8")

    df = annotate_pools(df)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if mode == "historical_replay" and backtest_date:
        df.to_csv(RESULTS_PATH.parent / f"historical_replay_{backtest_date}.csv", index=False)
    else:
        df.to_csv(RESULTS_PATH, index=False)


def subject_for(mode: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    mapping = {
        "full_after_close": f"【MOS Radar】{today} 盘后安全边际报告",
        "morning_email": f"【MOS Radar】{today} 开盘前安全边际报告",
        "noon_update": f"【MOS Radar】{today} 午盘安全边际变化",
        "afternoon_update": f"【MOS Radar】{today} 下午安全边际变化",
        "manual": f"【MOS Radar】{today} 手动安全边际扫描",
        "historical_replay": f"【MOS Radar】{today} 历史价格回放",
    }

    return mapping.get(mode, f"【MOS Radar】{today} 安全边际报告")


def main() -> None:
    mode = detect_mode()
    print(f"Run mode: {mode}", flush=True)

    top_mos_count = getenv_int("TOP_MOS_COUNT", 50)
    trap_count = getenv_int("TRAP_COUNT", 30)
    thin_count = getenv_int("THIN_COUNT", 30)

    if mode in {"full_after_close", "manual"}:
        df = run_full_scan()

    elif mode == "historical_replay":
        backtest_date = os.getenv("BACKTEST_DATE", "2022-10-14").strip() or "2022-10-14"
        use_latest = env_bool("BACKTEST_USE_LATEST", default=False)
        base_df = load_latest_or_full_scan() if use_latest else run_full_scan()
        df = run_historical_replay(base_df, backtest_date)
        df = annotate_pools(df)
        df["scan_time"] = datetime.now().isoformat(timespec="seconds")

    elif mode == "morning_email":
        df = load_latest_or_full_scan()

    elif mode in {"noon_update", "afternoon_update"}:
        df = load_latest_or_full_scan()
        sleep_seconds = getenv_float("PRICE_SLEEP_SECONDS", 0.05)
        df = update_prices_only(df, sleep_seconds=sleep_seconds)
        df = annotate_pools(df)

    else:
        df = load_latest_or_full_scan()

    report_body = generate_report(
        df,
        mode=mode,
        top_mos_count=top_mos_count,
        trap_count=trap_count,
        thin_count=thin_count,
    )

    save_report_files(df, mode, report_body)

    send_after_close = env_bool("SEND_AFTER_CLOSE", default=False)
    dry_run = env_bool("DRY_RUN", default=False)

    should_send = (
        mode in {"morning_email", "noon_update", "afternoon_update", "manual", "historical_replay"}
        or (mode == "full_after_close" and send_after_close)
    )

    if dry_run:
        print("DRY_RUN=true, email not sent.", flush=True)
    elif should_send:
        send_email(subject_for(mode), report_body)
        print("Email sent.", flush=True)
    else:
        print("Email not sent for this mode.", flush=True)


if __name__ == "__main__":
    main()
