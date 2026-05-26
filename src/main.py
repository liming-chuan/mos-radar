from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bear_validation import build_config, generate_bear_validation_report, run_bear_validation
from emailer import send_email
from historical_replay import run_historical_replay
from price_update import update_prices_only
from report import generate_report
from valuation import MODEL_VERSION, analyze_ticker, results_to_dataframe


ROOT = Path(__file__).resolve().parents[1]

MARKET = os.getenv("MARKET", "us").strip().lower() or "us"
MARKET_LABELS = {"us": "美股", "hk": "港股"}
MARKET_PREFIX = "" if MARKET == "us" else f"{MARKET}_"

UNIVERSE_PATH = ROOT / "data" / f"{MARKET_PREFIX}universe.csv"
HOLDINGS_PATH = ROOT / "data" / f"{MARKET_PREFIX}holdings.csv"
HK_UNIVERSE_SEED_PATH = ROOT / "data" / "hk_universe_seed.csv"

RESULTS_PATH = ROOT / "data" / "results" / f"{MARKET_PREFIX}mos_latest.csv"
SNAPSHOT_PATH = ROOT / "data" / "results" / f"{MARKET_PREFIX}mos_snapshot_latest.csv"
DIAGNOSTICS_PATH = ROOT / "data" / "results" / f"{MARKET_PREFIX}data_quality_diagnostics.csv"
REPORTS_DIR = ROOT / "reports" / MARKET if MARKET != "us" else ROOT / "reports"

STATE_DIR = ROOT / "state"
STATE_MARKET_PATH = STATE_DIR / f"{MARKET_PREFIX}mos_market_latest.csv"
CACHE_DIR = ROOT / "cache" / "fundamentals" / MARKET

SCHEDULE_MODE_MAP = {
    "31 12 * * 1-5": "premarket_scan",
    "0 1 * * 1-5": "premarket_scan",
}
STATE_REQUIRED_MODES = {"morning_email", "noon_update", "afternoon_update"}


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
    if forced and forced.strip():
        return forced.strip()

    schedule = os.getenv("GITHUB_EVENT_SCHEDULE", "").strip()
    return SCHEDULE_MODE_MAP.get(schedule, "manual")


def normalize_ticker_for_market(value: str) -> str:
    ticker = str(value).strip().upper()
    if not ticker or ticker == "TICKER":
        return ""

    ticker = ticker.replace("/", "-")

    if MARKET == "hk":
        if ticker.endswith(".HK"):
            base = ticker[:-3]
            if base.isdigit():
                return f"{int(base):04d}.HK"
            return ticker
        raw = ticker.replace("HK:", "").replace("HK", "")
        raw = raw.replace(".", "")
        if raw.isdigit():
            return f"{int(raw):04d}.HK"
        return ticker

    return ticker.replace(".", "-")


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

    return _normalize_tickers(raw)


def _normalize_tickers(raw: list[str]) -> list[str]:
    out = []
    seen = set()

    for x in raw:
        ticker = normalize_ticker_for_market(str(x))
        if not ticker:
            continue

        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)

    return out


def load_holdings() -> list[str]:
    holdings = _read_ticker_csv(HOLDINGS_PATH)
    env_key = "HOLDINGS_TICKERS_HK" if MARKET == "hk" else "HOLDINGS_TICKERS"
    env_holdings = os.getenv(env_key, "")
    if env_holdings.strip():
        holdings.extend(env_holdings.replace("\n", ",").split(","))
    return _normalize_tickers(holdings)


def load_scan_tickers() -> list[str]:
    universe = _read_ticker_csv(UNIVERSE_PATH)
    if MARKET == "hk" and not universe:
        universe = _read_ticker_csv(HK_UNIVERSE_SEED_PATH)
        print(
            f"WARNING: {UNIVERSE_PATH} is empty; using HK seed universe {HK_UNIVERSE_SEED_PATH} rows={len(universe)}",
            flush=True,
        )
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

    # 持仓池强制加入扫描：即使不在 universe.csv 里，也会扫描。
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


def public_market_state(df: pd.DataFrame) -> pd.DataFrame:
    df = annotate_pools(df)
    if df.empty:
        return df
    if "is_holding" in df.columns:
        df = df[~df["is_holding"].map(lambda x: bool(x))].copy()
    private_cols = [c for c in ["is_holding", "pool"] if c in df.columns]
    if private_cols:
        df = df.drop(columns=private_cols)
    return df.reset_index(drop=True)


def save_public_market_state(df: pd.DataFrame) -> None:
    state = public_market_state(df)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state.to_csv(STATE_MARKET_PATH, index=False)
    print(f"Saved public market state: {STATE_MARKET_PATH} rows={len(state)}", flush=True)


def load_public_market_state() -> pd.DataFrame:
    if RESULTS_PATH.exists():
        df = pd.read_csv(RESULTS_PATH)
        print(f"Loaded latest local result: {RESULTS_PATH} rows={len(df)}", flush=True)
        return annotate_pools(df)

    if STATE_MARKET_PATH.exists():
        df = pd.read_csv(STATE_MARKET_PATH)
        print(f"Loaded persisted public market state: {STATE_MARKET_PATH} rows={len(df)}", flush=True)
        return annotate_pools(df)

    raise RuntimeError(
        "Missing latest scan state. Run full_after_close or manual full scan first. "
        f"Expected {STATE_MARKET_PATH}. Non-full modes will not silently run a full scan."
    )


def cache_enabled() -> bool:
    return env_bool("USE_FUNDAMENTALS_CACHE", default=True)


def cache_ttl_days() -> int:
    return getenv_int("FUNDAMENTALS_CACHE_DAYS", 7)


def cache_path(ticker: str) -> Path:
    safe = str(ticker).strip().upper().replace("/", "-").replace(".", "-")
    return CACHE_DIR / f"{safe}.json"


def load_cached_analysis(ticker: str) -> dict | None:
    if not cache_enabled():
        return None
    path = cache_path(ticker)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(str(payload.get("saved_at", "")))
        age_days = (datetime.now(timezone.utc) - saved_at).total_seconds() / 86400
        if age_days > cache_ttl_days():
            return None
        row = dict(payload.get("result") or {})
        row["cache_status"] = "HIT"
        row["cache_age_days"] = round(age_days, 2)
        return row
    except Exception:
        return None


def save_cached_analysis(ticker: str, result) -> None:
    if not cache_enabled():
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        row = asdict(result) if hasattr(result, "__dataclass_fields__") else dict(result)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "model_version": MODEL_VERSION,
            "ticker": ticker,
            "result": row,
        }
        cache_path(ticker).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"Cache write skipped for {ticker}: {type(e).__name__}: {e}", flush=True)


def analyze_one(ticker: str, sleep_seconds: float):
    cached = load_cached_analysis(ticker)
    if cached is not None:
        print(f"cache hit {ticker}", flush=True)
        return cached

    result = analyze_ticker(ticker, sleep_seconds=sleep_seconds)
    save_cached_analysis(ticker, result)
    row = asdict(result)
    row["cache_status"] = "MISS"
    row["cache_age_days"] = 0
    return row


def build_data_quality_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    wanted = [
        "ticker", "company_name", "sector", "industry", "rating", "reason",
        "model_version", "model_type", "industry_model_status", "financial_period_type",
        "data_quality_score", "confidence_score", "trap_flags", "rating_cap",
        "valuation_method", "valuation_candidates", "price_data_status", "cache_status",
        "historical_price_status",
    ]
    cols = [c for c in wanted if c in df.columns]
    return df[cols].copy() if cols else pd.DataFrame()


def save_outputs(df: pd.DataFrame, write_state: bool = True) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULTS_PATH, index=False)
    df.to_csv(SNAPSHOT_PATH, index=False)
    diagnostics = build_data_quality_diagnostics(df)
    if not diagnostics.empty:
        diagnostics.to_csv(DIAGNOSTICS_PATH, index=False)
    if write_state:
        save_public_market_state(df)


def run_full_scan() -> pd.DataFrame:
    tickers = load_scan_tickers()
    sleep_seconds = getenv_float("REQUEST_SLEEP_SECONDS", 0.2)
    price_sleep_seconds = getenv_float("PRICE_SLEEP_SECONDS", 0.02)

    rows = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, start=1):
        print(f"[{i}/{total}] analyzing {ticker}", flush=True)
        rows.append(analyze_one(ticker, sleep_seconds=sleep_seconds))

    df = pd.DataFrame(rows)
    df = annotate_pools(df)
    if "cache_status" in df.columns and (df["cache_status"].astype(str) == "HIT").any():
        df = update_prices_only(df, sleep_seconds=price_sleep_seconds)

    if "margin_of_safety" in df.columns:
        df["margin_of_safety_at_scan"] = df["margin_of_safety"]

    df["scan_time"] = datetime.now().isoformat(timespec="seconds")
    df["model_version"] = MODEL_VERSION
    save_outputs(df, write_state=True)
    return df


def analyze_holdings_for_state() -> pd.DataFrame:
    holdings = load_holdings()
    if not holdings:
        return pd.DataFrame()
    sleep_seconds = getenv_float("REQUEST_SLEEP_SECONDS", 0.2)
    rows = []
    total = len(holdings)
    for i, ticker in enumerate(holdings, start=1):
        print(f"[{i}/{total}] analyzing holding {ticker}", flush=True)
        rows.append(analyze_one(ticker, sleep_seconds=sleep_seconds))
    return annotate_pools(pd.DataFrame(rows))


def load_latest_state_required() -> pd.DataFrame:
    market_df = load_public_market_state()
    holdings_df = analyze_holdings_for_state()
    if holdings_df.empty:
        return annotate_pools(market_df)
    combined = pd.concat([market_df, holdings_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["ticker"], keep="last")
    return annotate_pools(combined)


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
        df.to_csv(RESULTS_PATH.parent / f"{MARKET_PREFIX}historical_replay_{backtest_date}.csv", index=False)
        diagnostics = build_data_quality_diagnostics(df)
        if not diagnostics.empty:
            diagnostics.to_csv(DIAGNOSTICS_PATH, index=False)
    else:
        df.to_csv(RESULTS_PATH, index=False)
        diagnostics = build_data_quality_diagnostics(df)
        if not diagnostics.empty:
            diagnostics.to_csv(DIAGNOSTICS_PATH, index=False)
        if mode in {"full_after_close", "manual", "premarket_scan", "noon_update", "afternoon_update"}:
            save_public_market_state(df)


def save_bear_validation_files(candidates: pd.DataFrame, summary: pd.DataFrame, body: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidates_path = RESULTS_PATH.parent / f"{MARKET_PREFIX}bear_validation_candidates.csv"
    summary_path = RESULTS_PATH.parent / f"{MARKET_PREFIX}bear_validation_summary.csv"

    candidates.to_csv(candidates_path, index=False)
    summary.to_csv(summary_path, index=False)

    (REPORTS_DIR / f"bear_validation_{timestamp}.md").write_text(body, encoding="utf-8")
    (REPORTS_DIR / "latest_bear_validation.md").write_text(body, encoding="utf-8")

    print(f"Saved bear validation candidates: {candidates_path} rows={len(candidates)}", flush=True)
    print(f"Saved bear validation summary: {summary_path} rows={len(summary)}", flush=True)


def subject_for(mode: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    label = MARKET_LABELS.get(MARKET, MARKET.upper())

    mapping = {
        "full_after_close": f"【MOS Radar {label}】{today} 盘后安全边际报告",
        "premarket_scan": f"【MOS Radar {label}】{today} 盘前安全边际扫描",
        "morning_email": f"【MOS Radar {label}】{today} 开盘前安全边际报告",
        "noon_update": f"【MOS Radar {label}】{today} 午盘安全边际变化",
        "afternoon_update": f"【MOS Radar {label}】{today} 下午安全边际变化",
        "manual": f"【MOS Radar {label}】{today} 手动安全边际扫描",
        "historical_replay": f"【MOS Radar {label}】{today} 历史价格压力测试",
        "bear_validation": f"【MOS Radar {label}】{today} 熊市候选方向性验证",
    }

    return mapping.get(mode, f"【MOS Radar {label}】{today} 安全边际报告")


def main() -> None:
    mode = detect_mode()
    print(f"Run mode: {mode}", flush=True)

    top_mos_count = getenv_int("TOP_MOS_COUNT", 50)
    trap_count = getenv_int("TRAP_COUNT", 30)
    thin_count = getenv_int("THIN_COUNT", 30)

    if mode in {"full_after_close", "manual", "premarket_scan"}:
        df = run_full_scan()

    elif mode == "historical_replay":
        backtest_date = os.getenv("BACKTEST_DATE", "2022-10-14").strip() or "2022-10-14"
        use_latest = env_bool("BACKTEST_USE_LATEST", default=False)
        base_df = load_latest_state_required() if use_latest else run_full_scan()
        df = run_historical_replay(base_df, backtest_date)
        df = annotate_pools(df)
        df["scan_time"] = datetime.now().isoformat(timespec="seconds")

    elif mode == "bear_validation":
        use_latest = env_bool("BACKTEST_USE_LATEST", default=False)
        base_df = load_latest_state_required() if use_latest else run_full_scan()
        config = build_config(
            market=MARKET,
            bear_dates=os.getenv("BEAR_DATES"),
            forward_windows=os.getenv("FORWARD_WINDOWS"),
            benchmark_ticker=os.getenv("BENCHMARK_TICKER"),
            cohort_ratings=os.getenv("COHORT_RATINGS"),
            include_holdings=env_bool("BEAR_INCLUDE_HOLDINGS", default=False),
        )
        candidates, summary = run_bear_validation(base_df, config)
        candidates = annotate_pools(candidates) if not candidates.empty else candidates
        report_body = generate_bear_validation_report(candidates, summary, config)
        save_bear_validation_files(candidates, summary, report_body)

        dry_run = env_bool("DRY_RUN", default=False)
        if dry_run:
            print("DRY_RUN=true, email not sent.", flush=True)
        else:
            send_email(subject_for(mode), report_body)
            print("Email sent.", flush=True)
        return

    elif mode == "morning_email":
        df = load_latest_state_required()

    elif mode in {"noon_update", "afternoon_update"}:
        df = load_latest_state_required()
        sleep_seconds = getenv_float("PRICE_SLEEP_SECONDS", 0.02)
        df = update_prices_only(df, sleep_seconds=sleep_seconds)
        df = annotate_pools(df)

    elif mode in STATE_REQUIRED_MODES:
        df = load_latest_state_required()

    else:
        df = run_full_scan()

    report_body = generate_report(
        df,
        mode=mode,
        top_mos_count=top_mos_count,
        trap_count=trap_count,
        thin_count=thin_count,
        market=MARKET,
        model_version=MODEL_VERSION,
    )

    save_report_files(df, mode, report_body)

    send_after_close = env_bool("SEND_AFTER_CLOSE", default=False)
    dry_run = env_bool("DRY_RUN", default=False)

    should_send = (
        mode in {"premarket_scan", "morning_email", "noon_update", "afternoon_update", "manual", "historical_replay", "bear_validation"}
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
