from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from emailer import send_email, env_bool
from price_update import update_prices_only
from report import generate_report
from valuation import analyze_ticker, results_to_dataframe

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "data" / "universe.csv"
RESULTS_PATH = ROOT / "data" / "results" / "mos_latest.csv"
SNAPSHOT_PATH = ROOT / "data" / "results" / "mos_snapshot_latest.csv"
REPORTS_DIR = ROOT / "reports"


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


def load_universe() -> list[str]:
    if not UNIVERSE_PATH.exists():
        raise FileNotFoundError(f"Missing {UNIVERSE_PATH}")
    df = pd.read_csv(UNIVERSE_PATH)
    if "ticker" not in df.columns:
        raise ValueError("universe.csv must contain ticker column")
    tickers = [str(x).strip().upper() for x in df["ticker"].dropna().tolist()]
    seen = set()
    out = []
    for t in tickers:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    max_tickers = getenv_int("MAX_TICKERS", 0)
    if max_tickers > 0:
        return out[:max_tickers]
    return out


def run_full_scan() -> pd.DataFrame:
    tickers = load_universe()
    sleep_seconds = getenv_float("REQUEST_SLEEP_SECONDS", 0.2)
    results = []
    total = len(tickers)
    for i, ticker in enumerate(tickers, start=1):
        print(f"[{i}/{total}] analyzing {ticker}", flush=True)
        results.append(analyze_ticker(ticker, sleep_seconds=sleep_seconds))
    df = results_to_dataframe(results)
    if "margin_of_safety" in df.columns:
        df["margin_of_safety_at_scan"] = df["margin_of_safety"]
    df["scan_time"] = datetime.now().isoformat(timespec="seconds")
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULTS_PATH, index=False)
    df.to_csv(SNAPSHOT_PATH, index=False)
    return df


def load_latest_or_full_scan() -> pd.DataFrame:
    if RESULTS_PATH.exists():
        return pd.read_csv(RESULTS_PATH)
    return run_full_scan()


def save_report_files(df: pd.DataFrame, mode: str, body: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (REPORTS_DIR / f"{mode}_{timestamp}.md").write_text(body, encoding="utf-8")
    (REPORTS_DIR / "latest_report.md").write_text(body, encoding="utf-8")
    df.to_csv(RESULTS_PATH, index=False)


def subject_for(mode: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    mapping = {
        "full_after_close": f"【MOS Radar】{today} 盘后完整安全边际扫描完成",
        "morning_email": f"【MOS Radar】{today} 开盘前安全边际报告",
        "noon_update": f"【MOS Radar】{today} 午盘安全边际变化提醒",
        "afternoon_update": f"【MOS Radar】{today} 下午安全边际变化提醒",
        "manual": f"【MOS Radar】{today} 手动安全边际扫描",
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
    elif mode in {"morning_email"}:
        df = load_latest_or_full_scan()
    elif mode in {"noon_update", "afternoon_update"}:
        df = load_latest_or_full_scan()
        sleep_seconds = getenv_float("PRICE_SLEEP_SECONDS", 0.05)
        df = update_prices_only(df, sleep_seconds=sleep_seconds)
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

    # Default: send emails for morning/noon/afternoon/manual. After-close email is optional.
    send_after_close = env_bool("SEND_AFTER_CLOSE", default=False)
    should_send = mode in {"morning_email", "noon_update", "afternoon_update", "manual"} or (mode == "full_after_close" and send_after_close)
    if should_send:
        send_email(subject_for(mode), report_body)
        print("Email sent.", flush=True)
    else:
        print("Email not sent for this mode.", flush=True)


if __name__ == "__main__":
    main()
