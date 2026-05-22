from __future__ import annotations

import contextlib
import io
import logging
import math
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_PATH = ROOT / "data" / "feedback.csv"

MODEL_VERSION = "MOS_Radar_V6.5.8"
RISK_FREE_RATE_CACHE: float | None = None
FX_RATE_CACHE: dict[tuple[str, str], float] = {}


@dataclass
class AnalysisResult:
    ticker: str
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    quote_currency: str = ""
    financial_currency: str = ""
    quote_type: str = ""
    financial_to_quote_fx: float | None = None
    liquidity_volume: float | None = None
    liquidity_value: float | None = None
    price: float | None = None
    market_cap: float | None = None
    enterprise_value: float | None = None

    revenue_ttm: float | None = None
    financial_period_type: str = ""
    data_quality_notes: str = ""
    revenue_5y_cagr: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None

    net_income_ttm: float | None = None
    reported_fcf_ttm: float | None = None
    sbc_ttm: float | None = None
    fcf_ttm: float | None = None
    fcf_3y_avg: float | None = None
    fcf_5y_avg: float | None = None
    fcf_volatility: float | None = None
    fcf_yield: float | None = None
    fcf_conversion: float | None = None

    cash: float | None = None
    total_debt: float | None = None
    net_cash: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    ncav: float | None = None
    tangible_equity: float | None = None
    ebitda: float | None = None
    debt_to_ebitda: float | None = None
    interest_coverage: float | None = None
    equity: float | None = None
    roe: float | None = None

    share_dilution_3y: float | None = None

    intrinsic_value_total: float | None = None
    intrinsic_value_per_share: float | None = None
    buy_price_20mos: float | None = None
    buy_price_35mos: float | None = None
    buy_price_50mos: float | None = None
    margin_of_safety: float | None = None

    valuation_method: str = ""
    valuation_candidates: str = ""
    industry_model_status: str = ""
    model_type: str = ""
    model_version: str = MODEL_VERSION
    risk_free_rate: float | None = None
    discount_rate_used: float | None = None
    accrual_ratio: float | None = None

    mos_score: float = 0
    cashflow_score: float = 0
    balance_sheet_score: float = 0
    quality_score: float = 0
    trend_score: float = 0
    data_quality_score: float = 0
    confidence_score: float = 0
    final_score: float = 0

    trap_flags: str = ""
    trap_count: int = 0
    rating_cap: str = ""
    feedback_label: str = ""

    is_historical_replay: bool = False
    backtest_date: str = ""
    current_price: float | None = None
    current_market_cap: float | None = None
    return_since_backtest: float | None = None
    historical_price_status: str = ""

    rating: str = "NO_DATA"
    price_data_status: str = ""
    cache_status: str = ""
    cache_age_days: float | None = None
    reason: str = ""


def safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        if isinstance(x, str) and x.strip() == "":
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def coalesce_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def clamp(x: float | None, lo: float, hi: float, default: float = 0) -> float:
    if x is None:
        return default
    return max(lo, min(hi, float(x)))


def series_latest(s: pd.Series | None) -> float | None:
    if s is None or len(s) == 0:
        return None
    vals = pd.to_numeric(s, errors="coerce").dropna()
    if vals.empty:
        return None
    return safe_float(vals.iloc[0])


def series_sum_latest(s: pd.Series | None, n: int = 4, min_periods: int = 4) -> float | None:
    if s is None or len(s) == 0:
        return None
    vals = pd.to_numeric(s, errors="coerce").dropna().head(n)
    if len(vals) < min_periods:
        return None
    return safe_float(vals.sum())


def build_owner_fcf_series(ocf_s: pd.Series | None, capex_s: pd.Series | None, sbc_s: pd.Series | None) -> tuple[pd.Series | None, pd.Series | None]:
    if ocf_s is None or capex_s is None:
        return None, None
    ocf_vals = pd.to_numeric(ocf_s, errors="coerce")
    capex_vals = pd.to_numeric(capex_s, errors="coerce")
    common_cols = ocf_vals.index.intersection(capex_vals.index)
    if len(common_cols) == 0:
        return None, None
    reported_fcf_s = ocf_vals.loc[common_cols] + capex_vals.loc[common_cols]
    owner_fcf_s = reported_fcf_s.copy()
    if sbc_s is not None:
        sbc_vals = pd.to_numeric(sbc_s, errors="coerce")
        sbc_cols = common_cols.intersection(sbc_vals.index)
        owner_fcf_s.loc[sbc_cols] = owner_fcf_s.loc[sbc_cols] - sbc_vals.loc[sbc_cols].abs()
    return reported_fcf_s, owner_fcf_s


def industry_model_status(sector: str, industry: str, model_type: str) -> str:
    s = str(sector or "").lower()
    i = str(industry or "").lower()
    if model_type == "financial_pb_roe":
        if "insurance" in i:
            return "INSURANCE_LIMITED_PB_ROE_NEEDS_COMBINED_RATIO_FLOAT_RESERVES"
        if "bank" in i or "banks" in i:
            return "BANK_LIMITED_PB_ROE_NEEDS_CET1_NIM_NPL_DEPOSIT_COST"
        return "FINANCIAL_LIMITED_PB_ROE_NEEDS_INDUSTRY_SPECIFIC_REVIEW"
    if model_type == "reit_needs_affo":
        return "REIT_SKIPPED_NEEDS_AFFO_NOI_CAP_RATE"
    if model_type == "cyclical_semiconductor":
        return "SEMICONDUCTOR_CYCLE_LIMITED_NEEDS_INVENTORY_MARGIN_CAPEX_REVIEW"
    if model_type in {"energy_cyclical", "materials_cyclical", "precious_metals_miner"}:
        return "COMMODITY_CYCLE_LIMITED_NEEDS_MID_CYCLE_PRICE_COST_RESERVE_REVIEW"
    if model_type == "agri_cyclical":
        return "AGRI_CYCLE_LIMITED_NEEDS_NORMALIZED_MARGIN_VOLUME_REVIEW"
    if model_type == "software_tech":
        return "SOFTWARE_OWNER_FCF_SBC_ADJUSTED_NEEDS_NRR_RPO_RULE_OF_40_REVIEW"
    return "GENERAL_OWNER_FCF_MODEL"


def compact_candidates(candidates: list[tuple[str, float]]) -> str:
    clean = [(name, value) for name, value in candidates if value is not None and value > 0]
    clean = sorted(clean, key=lambda x: x[1])[:8]
    return "; ".join(f"{name}={value:.0f}" for name, value in clean)


def series_avg(s: pd.Series | None, n: int = 5) -> float | None:
    if s is None or len(s) == 0:
        return None
    vals = pd.to_numeric(s, errors="coerce").dropna().head(n)
    if vals.empty:
        return None
    return safe_float(vals.mean())


def series_volatility_ratio(s: pd.Series | None, n: int = 5) -> float | None:
    if s is None or len(s) == 0:
        return None
    vals = pd.to_numeric(s, errors="coerce").dropna().head(n)
    if len(vals) < 3:
        return None
    avg = vals.mean()
    if avg <= 0:
        return None
    return safe_float(vals.std(ddof=0) / avg)


def has_consecutive_decline(s: pd.Series | None, periods: int = 3) -> bool:
    if s is None or len(s) < periods:
        return False
    vals = pd.to_numeric(s, errors="coerce").dropna().head(periods)
    if len(vals) < periods:
        return False
    # yfinance columns are usually newest first.
    return all(vals.iloc[i] < vals.iloc[i + 1] for i in range(len(vals) - 1))


def margin_declining(numerator: pd.Series | None, denominator: pd.Series | None, periods: int = 3) -> bool:
    if numerator is None or denominator is None:
        return False
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    common_cols = num.index.intersection(den.index)
    if len(common_cols) < periods:
        return False
    margins = (num.loc[common_cols] / den.loc[common_cols]).replace([np.inf, -np.inf], np.nan).dropna().head(periods)
    if len(margins) < periods:
        return False
    return all(margins.iloc[i] < margins.iloc[i + 1] for i in range(len(margins) - 1))


def update_buy_prices(result: AnalysisResult) -> None:
    iv = result.intrinsic_value_per_share
    if iv is None or iv <= 0:
        return
    result.buy_price_20mos = iv / 1.20
    result.buy_price_35mos = iv / 1.35
    result.buy_price_50mos = iv / 1.50


def row(df: pd.DataFrame | None, names: list[str]) -> pd.Series | None:
    if df is None or df.empty:
        return None
    idx_lower = {str(i).lower(): i for i in df.index}
    for name in names:
        key = name.lower()
        if key in idx_lower:
            return pd.to_numeric(df.loc[idx_lower[key]], errors="coerce")
    return None


def fast_info_value(fast_info, key: str):
    try:
        return fast_info[key]
    except Exception:
        try:
            return getattr(fast_info, key)
        except Exception:
            return None


def retry_call(label: str, fn, attempts: int = 3, base_sleep: float = 0.5):
    last_error = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if i < attempts - 1:
                time.sleep(base_sleep * (i + 1))
    raise last_error


def quiet_yfinance_call(fn):
    logger = logging.getLogger("yfinance")
    old_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn()
    finally:
        logger.setLevel(old_level)


def normalize_tnx_quote(raw: float | None) -> float | None:
    value = safe_float(raw)
    if value is None or value <= 0:
        return None
    # ^TNX is usually quoted as yield * 10, e.g. 43.5 means 4.35%.
    if value > 20:
        return value / 1000
    if value > 1:
        return value / 100
    return value


def current_market() -> str:
    return os.getenv("MARKET", "us").strip().lower() or "us"


def forex_symbol(from_currency: str, to_currency: str) -> str:
    return f"{from_currency.upper()}{to_currency.upper()}=X"


def get_fx_rate(from_currency: str, to_currency: str, default: float | None = None) -> float | None:
    src = str(from_currency or "").strip().upper()
    dst = str(to_currency or "").strip().upper()
    if not src or not dst or src == dst:
        return 1.0
    key = (src, dst)
    if key in FX_RATE_CACHE:
        return FX_RATE_CACHE[key]
    try:
        t = yf.Ticker(forex_symbol(src, dst))
        raw = None
        try:
            raw = fast_info_value(t.fast_info, "last_price") or fast_info_value(t.fast_info, "lastPrice")
        except Exception:
            raw = None
        if raw is None:
            info = retry_call(f"{src}{dst}.info", lambda: t.info or {}, attempts=2, base_sleep=0.3)
            raw = info.get("regularMarketPrice") or info.get("previousClose")
        rate = safe_float(raw)
        if rate is not None and rate > 0:
            FX_RATE_CACHE[key] = rate
            return rate
    except Exception:
        pass
    return default


def can_convert_currency(quote_currency: str, financial_currency: str) -> bool:
    quote = str(quote_currency or "").strip().upper()
    financial = str(financial_currency or "").strip().upper()
    if not quote or not financial or quote == financial:
        return True
    if current_market() == "hk" and quote == "HKD" and financial in {"CNY", "CNH", "USD"}:
        return True
    return False


def scale_financial_df(df: pd.DataFrame | None, factor: float | None) -> pd.DataFrame | None:
    if df is None or df.empty or factor is None or factor == 1:
        return df
    out = df.copy()
    return out.apply(pd.to_numeric, errors="coerce") * factor


def get_risk_free_rate(default: float = 0.045) -> float:
    global RISK_FREE_RATE_CACHE
    if RISK_FREE_RATE_CACHE is not None:
        return RISK_FREE_RATE_CACHE

    try:
        t = yf.Ticker("^TNX")
        raw = None
        try:
            raw = fast_info_value(t.fast_info, "last_price") or fast_info_value(t.fast_info, "lastPrice")
        except Exception:
            raw = None
        if raw is None:
            info = retry_call("^TNX.info", lambda: t.info or {}, attempts=2, base_sleep=0.3)
            raw = info.get("regularMarketPrice") or info.get("previousClose")
        rate = normalize_tnx_quote(raw)
        RISK_FREE_RATE_CACHE = clamp(rate, 0.02, 0.08, default=default)
    except Exception:
        RISK_FREE_RATE_CACHE = default

    return RISK_FREE_RATE_CACHE


def effective_discount_rate(cfg: dict, risk_free_rate: float | None) -> float:
    base = float(cfg.get("discount_rate", 0.11))
    premium = float(cfg.get("risk_premium", 0.06))
    if risk_free_rate is None:
        return base
    return max(base, risk_free_rate + premium)


def latest_download_price(symbol: str) -> float | None:
    try:
        data = quiet_yfinance_call(lambda: yf.download(symbol, period="5d", interval="1d", auto_adjust=False, progress=False, threads=False))
        if data is None or data.empty:
            return None
        series = None
        if isinstance(data.columns, pd.MultiIndex):
            for field in ["Adj Close", "Close"]:
                if field in data.columns.get_level_values(0):
                    frame = data[field]
                    if symbol in frame.columns:
                        series = frame[symbol]
                    else:
                        series = frame.iloc[:, 0]
                    break
        else:
            for field in ["Adj Close", "Close"]:
                if field in data.columns:
                    series = data[field]
                    break
        if series is None:
            return None
        values = pd.to_numeric(series, errors="coerce").dropna()
        if values.empty:
            return None
        return safe_float(values.iloc[-1])
    except Exception:
        return None


def latest_download_price_volume(symbol: str, period: str = "10d") -> tuple[float | None, float | None]:
    try:
        data = quiet_yfinance_call(lambda: yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False, threads=False))
        if data is None or data.empty:
            return None, None

        close_series = None
        volume_series = None
        if isinstance(data.columns, pd.MultiIndex):
            for field in ["Adj Close", "Close"]:
                if field in data.columns.get_level_values(0):
                    frame = data[field]
                    close_series = frame[symbol] if symbol in frame.columns else frame.iloc[:, 0]
                    break
            if "Volume" in data.columns.get_level_values(0):
                frame = data["Volume"]
                volume_series = frame[symbol] if symbol in frame.columns else frame.iloc[:, 0]
        else:
            for field in ["Adj Close", "Close"]:
                if field in data.columns:
                    close_series = data[field]
                    break
            if "Volume" in data.columns:
                volume_series = data["Volume"]

        price = None
        avg_volume = None
        if close_series is not None:
            close_vals = pd.to_numeric(close_series, errors="coerce").dropna()
            if not close_vals.empty:
                price = safe_float(close_vals.iloc[-1])
        if volume_series is not None:
            volume_vals = pd.to_numeric(volume_series, errors="coerce").dropna()
            if not volume_vals.empty:
                avg_volume = safe_float(volume_vals.tail(10).mean())
        return price, avg_volume
    except Exception:
        return None, None


def get_price_and_cap(t: yf.Ticker, info: dict, fallback_price: float | None = None) -> tuple[float | None, float | None]:
    price = None
    market_cap = None

    try:
        fi = quiet_yfinance_call(lambda: t.fast_info)
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
    except Exception:
        pass

    price = coalesce_none(safe_float(price), safe_float(info.get("currentPrice")), safe_float(info.get("regularMarketPrice")), safe_float(fallback_price))
    if price is None:
        symbol = str(getattr(t, "ticker", "") or info.get("symbol") or "").upper()
        if symbol:
            price = latest_download_price(symbol)

    market_cap = coalesce_none(safe_float(market_cap), safe_float(info.get("marketCap")))
    shares = coalesce_none(safe_float(info.get("sharesOutstanding")), safe_float(info.get("impliedSharesOutstanding")))
    if market_cap is None and price is not None and shares is not None and shares > 0:
        market_cap = price * shares

    return price, market_cap


def get_feedback_map() -> dict[str, dict[str, str]]:
    if not FEEDBACK_PATH.exists():
        return {}
    try:
        df = pd.read_csv(FEEDBACK_PATH)
        if df.empty or "ticker" not in df.columns:
            return {}
        out = {}
        for _, r in df.iterrows():
            ticker = str(r.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            out[ticker] = {k: str(v) for k, v in r.items() if pd.notna(v)}
        return out
    except Exception:
        return {}


def sector_config(sector: str, industry: str) -> dict[str, float | str]:
    s = (sector or "").lower()
    i = (industry or "").lower()

    cfg = {
        "model": "normal_fcf",
        "fcf_multiple": 10.0,
        "pe_multiple": 10.0,
        "terminal_multiple": 9.0,
        "discount_rate": 0.11,
        "risk_premium": 0.06,
        "growth_cap": 0.08,
    }

    if any(x in i for x in ["gold", "silver", "copper", "other precious metals", "industrial metals", "mining"]):
        cfg.update({
            "model": "precious_metals_miner",
            "fcf_multiple": 4.0,
            "pe_multiple": 6.0,
            "terminal_multiple": 4.0,
            "discount_rate": 0.15,
            "risk_premium": 0.10,
            "growth_cap": 0.00,
        })
    elif any(x in i for x in ["farm products", "agricultural inputs", "agriculture"]):
        cfg.update({
            "model": "agri_cyclical",
            "fcf_multiple": 5.0,
            "pe_multiple": 8.0,
            "terminal_multiple": 5.0,
            "discount_rate": 0.13,
            "risk_premium": 0.09,
            "growth_cap": 0.00,
        })
    elif "semiconductor" in i:
        cfg.update({
            "model": "cyclical_semiconductor",
            "fcf_multiple": 10.0,
            "pe_multiple": 11.0,
            "terminal_multiple": 8.0,
            "discount_rate": 0.12,
            "risk_premium": 0.08,
            "growth_cap": 0.08,
        })
    elif "technology" in s:
        cfg.update({
            "model": "software_tech",
            "fcf_multiple": 13.0,
            "pe_multiple": 14.0,
            "terminal_multiple": 12.0,
            "discount_rate": 0.11,
            "risk_premium": 0.07,
            "growth_cap": 0.12,
        })
    elif "communication" in s:
        cfg.update({
            "model": "communication_services",
            "fcf_multiple": 11.0,
            "pe_multiple": 12.0,
            "terminal_multiple": 10.0,
            "discount_rate": 0.11,
            "risk_premium": 0.06,
            "growth_cap": 0.09,
        })
    elif "consumer defensive" in s:
        cfg.update({
            "model": "defensive_consumer",
            "fcf_multiple": 12.0,
            "pe_multiple": 13.0,
            "terminal_multiple": 11.0,
            "discount_rate": 0.10,
            "risk_premium": 0.05,
            "growth_cap": 0.07,
        })
    elif "consumer cyclical" in s:
        cfg.update({
            "model": "consumer_cyclical",
            "fcf_multiple": 10.0,
            "pe_multiple": 11.0,
            "terminal_multiple": 9.0,
            "discount_rate": 0.12,
            "risk_premium": 0.08,
            "growth_cap": 0.08,
        })
    elif "industrial" in s:
        cfg.update({
            "model": "industrial_normalized",
            "fcf_multiple": 10.0,
            "pe_multiple": 11.0,
            "terminal_multiple": 9.0,
            "discount_rate": 0.12,
            "risk_premium": 0.08,
            "growth_cap": 0.07,
        })
    elif "energy" in s:
        cfg.update({
            "model": "energy_cyclical",
            "fcf_multiple": 7.0,
            "pe_multiple": 8.0,
            "terminal_multiple": 6.0,
            "discount_rate": 0.13,
            "risk_premium": 0.09,
            "growth_cap": 0.03,
        })
    elif "basic materials" in s:
        cfg.update({
            "model": "materials_cyclical",
            "fcf_multiple": 8.0,
            "pe_multiple": 9.0,
            "terminal_multiple": 7.0,
            "discount_rate": 0.13,
            "risk_premium": 0.09,
            "growth_cap": 0.04,
        })
    elif "utilities" in s:
        cfg.update({
            "model": "utility_debt_sensitive",
            "fcf_multiple": 8.0,
            "pe_multiple": 10.0,
            "terminal_multiple": 7.0,
            "discount_rate": 0.10,
            "risk_premium": 0.05,
            "growth_cap": 0.04,
        })
    elif "healthcare" in s:
        if "biotechnology" in i and ("drug" in i or "biotechnology" in i):
            cfg.update({
                "model": "biotech_special_case",
                "fcf_multiple": 8.0,
                "pe_multiple": 8.0,
                "terminal_multiple": 6.0,
                "discount_rate": 0.14,
                "risk_premium": 0.10,
                "growth_cap": 0.06,
            })
        else:
            cfg.update({
                "model": "healthcare",
                "fcf_multiple": 11.0,
                "pe_multiple": 12.0,
                "terminal_multiple": 10.0,
                "discount_rate": 0.11,
                "risk_premium": 0.06,
                "growth_cap": 0.08,
            })
    elif "financial" in s:
        cfg.update({
            "model": "financial_pb_roe",
            "fcf_multiple": 0.0,
            "pe_multiple": 9.0,
            "terminal_multiple": 0.0,
            "discount_rate": 0.12,
            "risk_premium": 0.08,
            "growth_cap": 0.05,
        })
    elif "real estate" in s or "reit" in i:
        cfg.update({
            "model": "reit_needs_affo",
            "fcf_multiple": 0.0,
            "pe_multiple": 0.0,
            "terminal_multiple": 0.0,
            "discount_rate": 0.11,
            "risk_premium": 0.07,
            "growth_cap": 0.03,
        })

    return cfg


def has_currency_mismatch(quote_currency: str, financial_currency: str) -> bool:
    quote = str(quote_currency or "").strip().upper()
    financial = str(financial_currency or "").strip().upper()
    if not quote or not financial:
        return False
    return quote != financial


def needs_nav_or_special_model(company_name: str, sector: str, industry: str) -> bool:
    name = str(company_name or "").lower()
    s = str(sector or "").lower()
    i = str(industry or "").lower()

    if "financial" not in s:
        return False

    hard_keywords = [
        "fund",
        "income fund",
        "closed-end",
        "closed end",
        "business development",
        "bdc",
        "trust",
        "preferred",
        "preference",
        "debenture",
        "debentures",
        "note",
        "notes",
        "subordinated",
        "%",
    ]
    if any(k in name or k in i for k in hard_keywords):
        return True

    asset_management_like = "asset management" in i and any(
        k in name for k in ["capital", "investment", "income", "credit"]
    )
    return asset_management_like


def is_non_common_security_name(company_name: str, industry: str = "") -> bool:
    text = f"{company_name or ''} {industry or ''}".lower()
    if "%" in text:
        return True
    patterns = [
        r"\bpreferred\b",
        r"\bpreference\b",
        r"\bdepositary( share| shares)?\b",
        r"\bdebentures?\b",
        r"\bnotes?\b",
        r"\bsubordinated\b",
        r"\bbonds?\b",
        r"\bbaby bond\b",
        r"\bcapital securities\b",
        r"\bterm trust\b",
        r"\bclosed[- ]end\b",
        r"\bincome fund\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def is_hk_holding_company(company_name: str, sector: str, industry: str) -> bool:
    if current_market() != "hk":
        return False
    text = f"{company_name or ''} {sector or ''} {industry or ''}".lower()
    strong_terms = [
        "conglomerate", "conglomerates", "investment holding", "investment holdings",
        "holding company", "diversified", "multi-sector",
    ]
    known_hk_holding_names = ["citic", "ck hutchison", "fosun", "swire pacific", "china resources"]
    return any(k in text for k in strong_terms) or any(k in text for k in known_hk_holding_names)


def calc_cagr(latest: float | None, oldest: float | None, years: int) -> float | None:
    if latest is None or oldest is None or years <= 0:
        return None
    if latest <= 0 or oldest <= 0:
        return None
    try:
        return (latest / oldest) ** (1 / years) - 1
    except Exception:
        return None


def dcf_value(
    fcf_base: float,
    growth: float,
    terminal_multiple: float,
    discount_rate: float,
    cash: float,
    debt: float,
) -> float | None:
    if fcf_base <= 0:
        return None

    value = 0.0
    fcf = fcf_base

    for year in range(1, 6):
        fcf = fcf * (1 + growth)
        value += fcf / ((1 + discount_rate) ** year)

    terminal_value = fcf * terminal_multiple
    value += terminal_value / ((1 + discount_rate) ** 5)

    equity_value = value + cash - debt
    return equity_value if equity_value > 0 else None


def financial_pb_value(
    equity: float | None,
    roe: float | None,
    market_cap: float | None,
    tangible_equity: float | None = None,
) -> tuple[float | None, str]:
    capital_base = tangible_equity if tangible_equity is not None and tangible_equity > 0 else equity

    if capital_base is None or capital_base <= 0:
        return None, "financial_pb_no_equity"

    r = roe if roe is not None else 0

    if r >= 0.18:
        pb = 1.45
    elif r >= 0.13:
        pb = 1.20
    elif r >= 0.09:
        pb = 1.00
    elif r >= 0.06:
        pb = 0.80
    else:
        pb = 0.60

    base_name = "tangible_book" if tangible_equity is not None and tangible_equity > 0 else "book"
    return capital_base * pb, f"financial_pb_roe_{base_name}_{pb:.2f}x"


def estimate_intrinsic_value(
    cfg: dict,
    latest_fcf: float | None,
    fcf_3y_avg: float | None,
    fcf_5y_avg: float | None,
    latest_net_income: float | None,
    net_income_5y_avg: float | None,
    revenue_cagr: float | None,
    cash: float | None,
    debt: float | None,
    equity: float | None,
    roe: float | None,
    market_cap: float | None,
    ncav: float | None,
    tangible_equity: float | None,
    risk_free_rate: float | None,
) -> tuple[float | None, str, str]:
    cash = 0.0 if cash is None else cash
    debt = 0.0 if debt is None else debt
    model = str(cfg.get("model", "normal_fcf"))

    if model == "reit_needs_affo":
        return None, "SKIP_REIT_AFFO_REQUIRED", ""

    if model == "financial_pb_roe":
        value, method = financial_pb_value(equity, roe, market_cap, tangible_equity)
        return value, method, compact_candidates([(method, value or 0)])

    fcf_candidates = [x for x in [latest_fcf, fcf_3y_avg, fcf_5y_avg] if x is not None and x > 0]
    ni_candidates = [x for x in [latest_net_income, net_income_5y_avg] if x is not None and x > 0]

    valuations: list[tuple[str, float]] = []

    fcf_multiple = float(cfg.get("fcf_multiple", 10))
    pe_multiple = float(cfg.get("pe_multiple", 10))
    terminal_multiple = float(cfg.get("terminal_multiple", 9))
    discount_rate = effective_discount_rate(cfg, risk_free_rate)
    growth_cap = float(cfg.get("growth_cap", 0.08))

    if fcf_candidates:
        cyclical_models = {"energy_cyclical", "materials_cyclical", "cyclical_semiconductor", "industrial_normalized", "precious_metals_miner", "agri_cyclical"}
        if model in cyclical_models:
            cycle_candidates = [x for x in [fcf_3y_avg, fcf_5y_avg] if x is not None and x > 0]
            if fcf_5y_avg is not None and fcf_5y_avg > 0:
                cycle_candidates.append(fcf_5y_avg * 0.85)
            if latest_fcf is not None and latest_fcf > 0:
                cycle_candidates.append(latest_fcf * 0.40)
            fcf_base = min(cycle_candidates) if cycle_candidates else min(fcf_candidates)
        else:
            # 保守 owner FCF 基数：取最近、3年、5年中较低的正值，防止高点误判
            fcf_base = min(fcf_candidates)

        valuations.append(("normalized_fcf_multiple", fcf_base * fcf_multiple + cash - debt))

        if model not in {"energy_cyclical", "materials_cyclical", "cyclical_semiconductor", "industrial_normalized", "precious_metals_miner", "agri_cyclical"} and latest_fcf is not None and latest_fcf > 0:
            valuations.append(("latest_fcf_capped_10x", latest_fcf * min(10.0, fcf_multiple) + cash - debt))

        growth = clamp(revenue_cagr, -0.05, growth_cap, default=0.02)
        dcf = dcf_value(
            fcf_base=fcf_base,
            growth=growth,
            terminal_multiple=terminal_multiple,
            discount_rate=discount_rate,
            cash=cash,
            debt=debt,
        )
        if dcf is not None:
            valuations.append(("conservative_5y_dcf", dcf))

        valuations.append(("asset_plus_fcf_8x", cash - debt + fcf_base * 8.0))

    if ni_candidates:
        ni_base = min(ni_candidates)
        valuations.append(("normalized_net_income_pe", ni_base * pe_multiple + cash - debt))

    asset_heavy_models = {"energy_cyclical", "materials_cyclical", "industrial_normalized", "precious_metals_miner", "consumer_cyclical", "agri_cyclical"}
    if ncav is not None and ncav > 0:
        if model in asset_heavy_models or (market_cap is not None and ncav >= market_cap):
            valuations.append(("ncav_2_3", ncav * 0.67))

    if tangible_equity is not None and tangible_equity > 0 and model in asset_heavy_models:
        valuations.append(("tangible_book_0_8x", tangible_equity * 0.80))

    clean = [(name, v) for name, v in valuations if v is not None and v > 0]

    if not clean:
        return None, "NO_VALID_VALUATION", compact_candidates(valuations)

    # 保守原则：取最低的有效估值
    method, value = min(clean, key=lambda x: x[1])

    return value, method, compact_candidates(clean)


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


def score_cashflow(latest_fcf, fcf_5y_avg, fcf_yield, fcf_conversion) -> float:
    score = 0.0

    if latest_fcf is not None and latest_fcf > 0:
        score += 5
    if fcf_5y_avg is not None and fcf_5y_avg > 0:
        score += 5

    if fcf_yield is not None:
        if fcf_yield >= 0.10:
            score += 5
        elif fcf_yield >= 0.07:
            score += 4
        elif fcf_yield >= 0.05:
            score += 3
        elif fcf_yield >= 0.03:
            score += 1.5

    if fcf_conversion is not None:
        if fcf_conversion >= 0.90:
            score += 5
        elif fcf_conversion >= 0.70:
            score += 3
        elif fcf_conversion >= 0.50:
            score += 1

    return min(score, 20)


def score_balance(cash, debt, debt_to_ebitda, net_cash, market_cap) -> float:
    score = 0.0

    if cash is not None and debt is not None:
        if cash >= debt:
            score += 6
        elif debt > 0 and cash / debt >= 0.5:
            score += 3

    if debt_to_ebitda is not None:
        if debt_to_ebitda <= 1:
            score += 5
        elif debt_to_ebitda <= 2.5:
            score += 4
        elif debt_to_ebitda <= 4:
            score += 2
    elif debt is not None and debt <= 0:
        score += 3

    if net_cash is not None and market_cap is not None and market_cap > 0:
        if net_cash / market_cap >= 0.10:
            score += 4
        elif net_cash > 0:
            score += 2

    return min(score, 15)


def score_quality(roe, gross_margin, operating_margin, net_margin) -> float:
    score = 0.0

    if roe is not None:
        if roe >= 0.25:
            score += 5
        elif roe >= 0.15:
            score += 4
        elif roe >= 0.10:
            score += 2

    if gross_margin is not None:
        if gross_margin >= 0.60:
            score += 4
        elif gross_margin >= 0.40:
            score += 3
        elif gross_margin >= 0.25:
            score += 1

    if operating_margin is not None:
        if operating_margin >= 0.25:
            score += 4
        elif operating_margin >= 0.15:
            score += 3
        elif operating_margin >= 0.08:
            score += 1

    if net_margin is not None:
        if net_margin >= 0.20:
            score += 2
        elif net_margin >= 0.10:
            score += 1

    return min(score, 15)


def score_financial_quality(roe) -> float:
    if roe is None:
        return 0
    if roe >= 0.18:
        return 25
    if roe >= 0.13:
        return 21
    if roe >= 0.10:
        return 16
    if roe >= 0.07:
        return 10
    if roe >= 0.04:
        return 5
    return 0


def score_trend(revenue_cagr, share_dilution, fcf_latest, fcf_5y_avg) -> float:
    score = 0.0

    if revenue_cagr is not None:
        if revenue_cagr >= 0.10:
            score += 5
        elif revenue_cagr >= 0.05:
            score += 4
        elif revenue_cagr >= 0:
            score += 2
        elif revenue_cagr >= -0.03:
            score += 1

    if share_dilution is not None:
        if share_dilution <= 0:
            score += 3
        elif share_dilution <= 0.03:
            score += 2
        elif share_dilution <= 0.08:
            score += 1

    if fcf_latest is not None and fcf_5y_avg is not None and fcf_5y_avg > 0:
        ratio = fcf_latest / fcf_5y_avg
        if ratio >= 1.1:
            score += 2
        elif ratio >= 0.8:
            score += 1

    return min(score, 10)


def score_data_quality(*values) -> float:
    total = len(values)
    present = sum(1 for v in values if safe_float(v) is not None)
    if total == 0:
        return 0
    return round(10 * present / total, 2)


def detect_traps(
    latest_fcf,
    fcf_5y_avg,
    fcf_volatility,
    revenue_cagr,
    revenue_decline_streak,
    gross_margin_decline,
    operating_margin_decline,
    debt_to_ebitda,
    interest_coverage,
    debt,
    market_cap,
    operating_margin,
    net_margin,
    share_dilution,
    fcf_conversion,
    accrual_ratio,
    model_type,
    latest_net_income,
    fcf_yield=None,
) -> list[str]:
    flags = []

    if latest_fcf is not None and latest_fcf < 0:
        flags.append("latest_fcf_negative")

    if fcf_5y_avg is not None and fcf_5y_avg <= 0:
        flags.append("avg_fcf_not_positive")

    if revenue_cagr is not None and revenue_cagr < -0.03:
        flags.append("revenue_decline")

    if revenue_decline_streak:
        flags.append("revenue_decline_streak")

    if gross_margin_decline:
        flags.append("gross_margin_decline")

    if operating_margin_decline:
        flags.append("operating_margin_decline")

    if fcf_volatility is not None and fcf_volatility > 0.80:
        flags.append("high_fcf_volatility")

    if debt_to_ebitda is not None and debt_to_ebitda > 4:
        flags.append("high_debt_to_ebitda")

    if interest_coverage is not None and interest_coverage < 3:
        flags.append("weak_interest_coverage")

    if debt is not None and market_cap is not None and market_cap > 0 and debt > market_cap:
        flags.append("debt_exceeds_market_cap")

    if debt is not None and fcf_5y_avg is not None and fcf_5y_avg > 0 and debt > 5 * fcf_5y_avg:
        flags.append("debt_over_5x_avg_fcf")

    if operating_margin is not None and operating_margin < 0:
        flags.append("negative_operating_margin")

    if net_margin is not None and net_margin < 0:
        flags.append("negative_net_margin")

    if share_dilution is not None and share_dilution > 0.10:
        flags.append("heavy_dilution")

    if fcf_conversion is not None and fcf_conversion < 0.30 and latest_net_income is not None and latest_net_income > 0:
        flags.append("weak_cash_conversion")

    if accrual_ratio is not None and accrual_ratio > 0.20:
        flags.append("very_high_accrual_ratio")
    elif accrual_ratio is not None and accrual_ratio > 0.10:
        flags.append("high_accrual_ratio")

    cyclical_models = {"energy_cyclical", "materials_cyclical", "cyclical_semiconductor", "industrial_normalized", "precious_metals_miner", "agri_cyclical"}
    if model_type in cyclical_models:
        if latest_fcf is not None and fcf_5y_avg is not None and fcf_5y_avg > 0 and latest_fcf > 2.0 * fcf_5y_avg:
            flags.append("possible_cycle_peak_fcf")
        if fcf_yield is not None and fcf_yield > 0.12 and (revenue_decline_streak or gross_margin_decline or (revenue_cagr is not None and revenue_cagr < -0.03)):
            flags.append("cyclical_profit_reversal_risk")

    return flags


def detect_financial_traps(latest_net_income, equity, roe) -> list[str]:
    flags = []

    if latest_net_income is not None and latest_net_income < 0:
        flags.append("financial_net_income_negative")

    if equity is not None and equity <= 0:
        flags.append("financial_equity_not_positive")

    if roe is not None and roe < 0.04:
        flags.append("financial_low_roe")

    return flags


def rating_rank(rating: str) -> int:
    order = {"S": 0, "A": 1, "B": 2, "C_THIN": 3, "PASS": 4, "D_TRAP": 5, "NO_DATA": 6, "SKIP": 7, "ERROR": 8}
    return order.get(str(rating), 9)


def cap_rating(rating: str, cap: str) -> str:
    return cap if rating_rank(rating) < rating_rank(cap) else rating


def quality_rating_cap(result: AnalysisResult) -> tuple[str | None, list[str]]:
    reasons = []
    cap = None

    if result.trap_count >= 3:
        return "D_TRAP", ["trap_count_ge_3"]

    if result.trap_count >= 2:
        cap = "C_THIN"
        reasons.append("trap_count_ge_2")

    if str(result.ticker).upper().endswith(".HK"):
        hk_penny = result.price is not None and result.price < 1.0
        hk_low_turnover = result.liquidity_value is not None and result.liquidity_value < 5_000_000
        if hk_penny and hk_low_turnover:
            cap = cap_rating(cap or "S", "D_TRAP")
            reasons.append("hk_penny_low_turnover_trap")
        elif hk_penny:
            cap = cap_rating(cap or "S", "C_THIN")
            reasons.append("hk_price_below_1")
        elif hk_low_turnover:
            cap = cap_rating(cap or "S", "C_THIN")
            reasons.append("hk_low_turnover")

    if result.model_type == "financial_pb_roe":
        cap = cap_rating(cap or "S", "B")
        reasons.append("financial_limited_pb_roe_model")
        if result.data_quality_score < 5:
            cap = cap_rating(cap, "B")
            reasons.append("low_data_quality")
        return cap, reasons

    if result.cashflow_score < 8:
        cap = cap_rating(cap or "S", "B")
        reasons.append("weak_cashflow_score")

    if result.quality_score < 6:
        cap = cap_rating(cap or "S", "B")
        reasons.append("weak_quality_score")

    if result.debt_to_ebitda is not None and result.debt_to_ebitda > 5:
        cap = cap_rating(cap or "S", "C_THIN")
        reasons.append("debt_to_ebitda_over_5")

    if result.interest_coverage is not None and result.interest_coverage < 2:
        cap = cap_rating(cap or "S", "C_THIN")
        reasons.append("interest_coverage_under_2")

    if result.data_quality_score < 5:
        cap = cap_rating(cap or "S", "B")
        reasons.append("low_data_quality")

    if result.model_type == "precious_metals_miner":
        cap = cap_rating(cap or "S", "B")
        reasons.append("precious_metals_cycle_model")

    if result.fcf_yield is not None and result.fcf_yield > 0.25:
        cap = cap_rating(cap or "S", "C_THIN")
        reasons.append("abnormal_fcf_yield")

    return cap, reasons


def apply_feedback(ticker: str, intrinsic_value: float | None, final_score: float, flags: list[str]) -> tuple[float | None, float, str, list[str]]:
    feedback = get_feedback_map().get(ticker.upper(), {})
    label = feedback.get("label", "").strip().lower()

    if not label:
        return intrinsic_value, final_score, "", flags

    if label == "true_opportunity":
        final_score += 5
    elif label == "value_trap":
        flags.append("manual_value_trap")
        final_score -= 10
    elif label == "too_strict":
        if intrinsic_value is not None:
            intrinsic_value *= 1.10
    elif label == "too_loose":
        if intrinsic_value is not None:
            intrinsic_value *= 0.90

    return intrinsic_value, final_score, label, flags


def normalize_analysis_ticker(ticker: str) -> str:
    raw = str(ticker or "").strip().upper().replace("/", "-")
    if current_market() == "hk":
        if raw.endswith(".HK"):
            base = raw[:-3]
            return f"{int(base):04d}.HK" if base.isdigit() else raw
        compact = raw.replace("HK:", "").replace("HK", "").replace(".", "")
        if compact.isdigit():
            return f"{int(compact):04d}.HK"
        return raw
    return raw.replace(".", "-")


def analyze_ticker(ticker: str, sleep_seconds: float = 0.2) -> AnalysisResult:
    ticker = normalize_analysis_ticker(ticker)
    result = AnalysisResult(ticker=ticker)

    try:
        prechecked_price = None
        prechecked_avg_volume = None
        if current_market() == "hk":
            prechecked_price, prechecked_avg_volume = latest_download_price_volume(ticker)
            if prechecked_price is None or prechecked_price <= 0:
                result.rating = "NO_DATA"
                result.price_data_status = "NO_RECENT_YAHOO_PRICE"
                result.reason = "Yahoo 近期价格缺失，跳过该港股，避免无效 ticker 反复重试"
                return result

        t = yf.Ticker(ticker)

        info = {}
        try:
            info = retry_call(f"{ticker}.info", lambda: quiet_yfinance_call(lambda: t.info or {}))
        except Exception:
            info = {}

        price, market_cap = get_price_and_cap(t, info, fallback_price=prechecked_price)

        result.price = price
        result.market_cap = market_cap
        result.company_name = info.get("longName") or info.get("shortName") or ""
        result.sector = info.get("sector") or ""
        result.industry = info.get("industry") or ""
        result.quote_currency = str(info.get("currency") or "").upper()
        result.financial_currency = str(info.get("financialCurrency") or "").upper()
        result.quote_type = str(info.get("quoteType") or "").upper()
        result.liquidity_volume = prechecked_avg_volume
        if price is not None and prechecked_avg_volume is not None:
            result.liquidity_value = price * prechecked_avg_volume

        if result.quote_type and result.quote_type != "EQUITY":
            result.rating = "SKIP"
            result.price_data_status = "SKIP_NON_EQUITY"
            result.reason = f"非普通股或非股票资产类型，跳过估值：quoteType={result.quote_type}"
            return result

        if is_non_common_security_name(result.company_name, result.industry):
            result.rating = "SKIP"
            result.price_data_status = "SKIP_NON_COMMON_STOCK"
            result.reason = "非普通股/债券/优先股/封闭基金等证券，跳过估值，避免财务分子分母错配"
            return result

        if price is None or price <= 0:
            result.rating = "NO_DATA"
            result.reason = "无法获取有效股价"
            return result

        fx_factor = 1.0
        if has_currency_mismatch(result.quote_currency, result.financial_currency):
            if not can_convert_currency(result.quote_currency, result.financial_currency):
                result.rating = "SKIP"
                result.reason = (
                    "报价币种与财报币种不一致，疑似 ADR/海外股票；"
                    f"quote_currency={result.quote_currency}, financial_currency={result.financial_currency}。"
                    "当前模型暂不自动估值，避免币种/ADR比例导致安全边际失真"
                )
                return result
            fx_factor = get_fx_rate(result.financial_currency, result.quote_currency)
            if fx_factor is None or fx_factor <= 0:
                result.rating = "SKIP"
                result.reason = (
                    "报价币种与财报币种不一致，且无法获取换汇汇率；"
                    f"quote_currency={result.quote_currency}, financial_currency={result.financial_currency}"
                )
                return result
            result.financial_to_quote_fx = fx_factor
            result.data_quality_notes = f"financials_converted_{result.financial_currency}_to_{result.quote_currency}_fx={fx_factor:.4f}"
        else:
            result.financial_to_quote_fx = 1.0

        sector = result.sector
        industry = result.industry
        cfg = sector_config(sector, industry)
        result.model_type = str(cfg.get("model", "normal_fcf"))
        result.industry_model_status = industry_model_status(sector, industry, result.model_type)

        if needs_nav_or_special_model(result.company_name, sector, industry):
            result.rating = "SKIP"
            result.reason = "基金/BDC/特殊金融资产需要 NAV/NII/分红覆盖专门模型，当前模型暂不自动估值"
            return result

        if result.model_type == "reit_needs_affo":
            result.rating = "SKIP"
            result.reason = "REIT/地产类公司需要 AFFO/NOI 专门模型，当前模型暂不自动估值"
            return result

        annual_financials = None
        annual_balance = None
        annual_cashflow = None
        quarterly_financials = None
        quarterly_balance = None
        quarterly_cashflow = None

        try:
            annual_financials = retry_call(f"{ticker}.financials", lambda: quiet_yfinance_call(lambda: t.financials))
        except Exception:
            annual_financials = None

        try:
            annual_balance = retry_call(f"{ticker}.balance_sheet", lambda: quiet_yfinance_call(lambda: t.balance_sheet))
        except Exception:
            annual_balance = None

        try:
            annual_cashflow = retry_call(f"{ticker}.cashflow", lambda: quiet_yfinance_call(lambda: t.cashflow))
        except Exception:
            annual_cashflow = None

        try:
            quarterly_financials = retry_call(f"{ticker}.quarterly_financials", lambda: quiet_yfinance_call(lambda: t.quarterly_financials), attempts=2, base_sleep=0.3)
        except Exception:
            quarterly_financials = None

        try:
            quarterly_cashflow = retry_call(f"{ticker}.quarterly_cashflow", lambda: quiet_yfinance_call(lambda: t.quarterly_cashflow), attempts=2, base_sleep=0.3)
        except Exception:
            quarterly_cashflow = None

        try:
            quarterly_balance = retry_call(f"{ticker}.quarterly_balance_sheet", lambda: quiet_yfinance_call(lambda: t.quarterly_balance_sheet), attempts=2, base_sleep=0.3)
        except Exception:
            quarterly_balance = None

        annual_financials = scale_financial_df(annual_financials, fx_factor)
        annual_balance = scale_financial_df(annual_balance, fx_factor)
        annual_cashflow = scale_financial_df(annual_cashflow, fx_factor)
        quarterly_financials = scale_financial_df(quarterly_financials, fx_factor)
        quarterly_balance = scale_financial_df(quarterly_balance, fx_factor)
        quarterly_cashflow = scale_financial_df(quarterly_cashflow, fx_factor)

        financials = annual_financials
        balance = quarterly_balance if quarterly_balance is not None and not quarterly_balance.empty else annual_balance
        cashflow = annual_cashflow

        revenue_s = row(financials, ["Total Revenue", "Operating Revenue"])
        gross_profit_s = row(financials, ["Gross Profit"])
        operating_income_s = row(financials, ["Operating Income", "Operating Income or Loss"])
        net_income_s = row(financials, ["Net Income", "Net Income Common Stockholders"])
        ebitda_s = row(financials, ["EBITDA"])
        interest_expense_s = row(financials, ["Interest Expense", "Interest Expense Non Operating"])

        q_revenue_s = row(quarterly_financials, ["Total Revenue", "Operating Revenue"])
        q_gross_profit_s = row(quarterly_financials, ["Gross Profit"])
        q_operating_income_s = row(quarterly_financials, ["Operating Income", "Operating Income or Loss"])
        q_net_income_s = row(quarterly_financials, ["Net Income", "Net Income Common Stockholders"])
        q_ebitda_s = row(quarterly_financials, ["EBITDA"])
        q_interest_expense_s = row(quarterly_financials, ["Interest Expense", "Interest Expense Non Operating"])

        ocf_s = row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex_s = row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
        sbc_s = row(cashflow, ["Stock Based Compensation"])

        q_ocf_s = row(quarterly_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        q_capex_s = row(quarterly_cashflow, ["Capital Expenditure", "Capital Expenditures"])
        q_sbc_s = row(quarterly_cashflow, ["Stock Based Compensation"])

        shares_s = row(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"])
        cash_s = row(balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])
        debt_s = row(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
        equity_s = row(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest", "Total Stockholder Equity"])
        current_assets_s = row(balance, ["Current Assets", "Total Current Assets"])
        total_liabilities_s = row(balance, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
        total_assets_s = row(balance, ["Total Assets"])
        goodwill_s = row(balance, ["Goodwill"])
        goodwill_and_intangibles_s = row(balance, ["Goodwill And Other Intangible Assets"])
        other_intangible_s = row(balance, ["Other Intangible Assets", "Intangible Assets"])

        reported_fcf_s, owner_fcf_s = build_owner_fcf_series(ocf_s, capex_s, sbc_s)

        q_reported_fcf_s, q_owner_fcf_s = build_owner_fcf_series(q_ocf_s, q_capex_s, q_sbc_s)
        q_reported_fcf = series_sum_latest(q_reported_fcf_s, 4, 4)
        q_owner_fcf = series_sum_latest(q_owner_fcf_s, 4, 4)
        q_sbc = series_sum_latest(q_sbc_s, 4, 1)

        annual_reported_fcf = series_latest(reported_fcf_s)
        annual_sbc = series_latest(sbc_s)
        annual_owner_fcf = series_latest(owner_fcf_s)

        revenue_ttm = series_sum_latest(q_revenue_s, 4, 4)
        net_income_ttm = series_sum_latest(q_net_income_s, 4, 4)
        gross_profit_ttm = series_sum_latest(q_gross_profit_s, 4, 4)
        operating_income_ttm = series_sum_latest(q_operating_income_s, 4, 4)
        ebitda_ttm = series_sum_latest(q_ebitda_s, 4, 4)
        interest_expense_ttm = series_sum_latest(q_interest_expense_s, 4, 4)

        revenue_annual_latest = series_latest(revenue_s)
        latest_net_income_annual = series_latest(net_income_s)

        revenue_latest = coalesce_none(revenue_ttm, revenue_annual_latest, safe_float(info.get("totalRevenue")))
        latest_net_income = coalesce_none(net_income_ttm, latest_net_income_annual, safe_float(info.get("netIncomeToCommon")))
        reported_fcf = coalesce_none(q_reported_fcf, annual_reported_fcf)
        sbc = coalesce_none(q_sbc, annual_sbc)
        latest_fcf = coalesce_none(q_owner_fcf, annual_owner_fcf)
        result.financial_period_type = "TTM" if q_owner_fcf is not None and q_reported_fcf is not None else "ANNUAL_FALLBACK"

        revenue_oldest = None
        if revenue_s is not None and len(pd.to_numeric(revenue_s, errors="coerce").dropna()) >= 4:
            vals = pd.to_numeric(revenue_s, errors="coerce").dropna().head(5)
            revenue_oldest = safe_float(vals.iloc[-1]) if len(vals) >= 2 else None

        revenue_cagr = calc_cagr(revenue_annual_latest, revenue_oldest, max(1, min(4, len(pd.to_numeric(revenue_s, errors="coerce").dropna()) - 1)) if revenue_s is not None else 4)

        gross_profit = coalesce_none(gross_profit_ttm, series_latest(gross_profit_s))
        operating_income = coalesce_none(operating_income_ttm, series_latest(operating_income_s))
        net_income_5y_avg = series_avg(net_income_s, 5)

        fcf_3y_avg = series_avg(owner_fcf_s, 3)
        fcf_5y_avg = series_avg(owner_fcf_s, 5)
        fcf_volatility = series_volatility_ratio(owner_fcf_s, 5)

        cash = coalesce_none(series_latest(cash_s), safe_float(info.get("totalCash")))
        debt = coalesce_none(series_latest(debt_s), safe_float(info.get("totalDebt")))
        equity = series_latest(equity_s)
        current_assets = series_latest(current_assets_s)
        total_liabilities = series_latest(total_liabilities_s)
        total_assets = series_latest(total_assets_s)
        goodwill = series_latest(goodwill_s)
        other_intangible = series_latest(other_intangible_s)
        goodwill_and_intangibles = series_latest(goodwill_and_intangibles_s)
        if goodwill_and_intangibles is None:
            intangible_components = [v for v in [goodwill, other_intangible] if v is not None]
            goodwill_and_intangibles = sum(intangible_components) if intangible_components else None

        ebitda = coalesce_none(ebitda_ttm, series_latest(ebitda_s), safe_float(info.get("ebitda")))

        result.revenue_ttm = revenue_latest
        result.revenue_5y_cagr = revenue_cagr
        result.net_income_ttm = latest_net_income
        result.reported_fcf_ttm = reported_fcf
        result.sbc_ttm = sbc
        result.fcf_ttm = latest_fcf
        result.fcf_3y_avg = fcf_3y_avg
        result.fcf_5y_avg = fcf_5y_avg
        result.fcf_volatility = fcf_volatility
        result.cash = cash
        result.total_debt = debt
        result.net_cash = (0 if cash is None else cash) - (0 if debt is None else debt)
        result.total_assets = total_assets
        result.total_liabilities = total_liabilities
        result.ncav = current_assets - total_liabilities if current_assets is not None and total_liabilities is not None else None
        result.tangible_equity = equity - goodwill_and_intangibles if equity is not None and goodwill_and_intangibles is not None else None
        result.ebitda = ebitda
        result.equity = equity
        result.risk_free_rate = get_risk_free_rate()
        result.discount_rate_used = effective_discount_rate(cfg, result.risk_free_rate)
        result.industry_model_status = industry_model_status(sector, industry, result.model_type)
        if result.financial_period_type != "TTM":
            note = "quarterly_ttm_unavailable_used_annual_fallback"
            result.data_quality_notes = f"{result.data_quality_notes};{note}" if result.data_quality_notes else note
        if latest_net_income is not None and latest_fcf is not None and total_assets is not None and total_assets > 0:
            result.accrual_ratio = (latest_net_income - latest_fcf) / total_assets

        result.enterprise_value = (market_cap if market_cap is not None else 0) + (debt if debt is not None else 0) - (cash if cash is not None else 0) if market_cap is not None else None

        if market_cap is not None and market_cap > 0 and latest_fcf is not None:
            result.fcf_yield = latest_fcf / market_cap

        if latest_net_income is not None and latest_net_income != 0 and latest_fcf is not None:
            result.fcf_conversion = latest_fcf / latest_net_income

        if revenue_latest is not None and revenue_latest > 0:
            result.gross_margin = gross_profit / revenue_latest if gross_profit is not None else safe_float(info.get("grossMargins"))
            result.operating_margin = operating_income / revenue_latest if operating_income is not None else safe_float(info.get("operatingMargins"))
            result.net_margin = latest_net_income / revenue_latest if latest_net_income is not None else safe_float(info.get("profitMargins"))
        else:
            result.gross_margin = safe_float(info.get("grossMargins"))
            result.operating_margin = safe_float(info.get("operatingMargins"))
            result.net_margin = safe_float(info.get("profitMargins"))

        if equity is not None and equity > 0 and latest_net_income is not None:
            result.roe = latest_net_income / equity
        else:
            result.roe = safe_float(info.get("returnOnEquity"))

        if debt is not None and ebitda is not None and ebitda > 0:
            result.debt_to_ebitda = debt / ebitda

        interest_expense = coalesce_none(interest_expense_ttm, series_latest(interest_expense_s))
        if operating_income is not None and interest_expense is not None and interest_expense != 0:
            result.interest_coverage = operating_income / abs(interest_expense)

        if shares_s is not None:
            vals = pd.to_numeric(shares_s, errors="coerce").dropna().head(4)
            if len(vals) >= 2 and vals.iloc[-1] > 0:
                result.share_dilution_3y = vals.iloc[0] / vals.iloc[-1] - 1

        intrinsic, method, valuation_candidates = estimate_intrinsic_value(
            cfg=cfg,
            latest_fcf=latest_fcf,
            fcf_3y_avg=fcf_3y_avg,
            fcf_5y_avg=fcf_5y_avg,
            latest_net_income=latest_net_income,
            net_income_5y_avg=net_income_5y_avg,
            revenue_cagr=revenue_cagr,
            cash=cash,
            debt=debt,
            equity=equity,
            roe=result.roe,
            market_cap=market_cap,
            ncav=result.ncav,
            tangible_equity=result.tangible_equity,
            risk_free_rate=result.risk_free_rate,
        )

        if intrinsic is not None and is_hk_holding_company(result.company_name, sector, industry):
            intrinsic *= 0.65
            method = f"{method}_hk_holding_0_65x"
            result.data_quality_notes = f"{result.data_quality_notes};hk_holding_company_discount_0_65x" if result.data_quality_notes else "hk_holding_company_discount_0_65x"

        result.valuation_method = method
        result.valuation_candidates = valuation_candidates

        if result.model_type == "financial_pb_roe":
            result.fcf_yield = None
            result.fcf_conversion = None
            result.debt_to_ebitda = None
            result.interest_coverage = None
            flags = detect_financial_traps(
                latest_net_income=latest_net_income,
                equity=equity,
                roe=result.roe,
            )
        else:
            flags = detect_traps(
                latest_fcf=latest_fcf,
                fcf_5y_avg=fcf_5y_avg,
                fcf_volatility=fcf_volatility,
                revenue_cagr=revenue_cagr,
                revenue_decline_streak=has_consecutive_decline(revenue_s, 3),
                gross_margin_decline=margin_declining(gross_profit_s, revenue_s, 3),
                operating_margin_decline=margin_declining(operating_income_s, revenue_s, 3),
                debt_to_ebitda=result.debt_to_ebitda,
                interest_coverage=result.interest_coverage,
                debt=debt,
                market_cap=market_cap,
                operating_margin=result.operating_margin,
                net_margin=result.net_margin,
                share_dilution=result.share_dilution_3y,
                fcf_conversion=result.fcf_conversion,
                accrual_ratio=result.accrual_ratio,
                model_type=result.model_type,
                latest_net_income=latest_net_income,
                fcf_yield=result.fcf_yield,
            )

        if intrinsic is not None:
            result.intrinsic_value_total = intrinsic
            if market_cap is not None and market_cap > 0:
                result.intrinsic_value_per_share = price * intrinsic / market_cap
                result.margin_of_safety = (result.intrinsic_value_per_share - price) / price
                update_buy_prices(result)

        result.mos_score = score_margin_of_safety(result.margin_of_safety)

        if result.model_type == "financial_pb_roe":
            result.cashflow_score = 0
            result.balance_sheet_score = 0
            result.quality_score = score_financial_quality(result.roe)
            result.trend_score = 0
            result.data_quality_score = score_data_quality(price, market_cap, latest_net_income, equity, result.roe)
            result.final_score = (
                result.mos_score
                + result.quality_score
                + result.data_quality_score
            )
        else:
            result.cashflow_score = score_cashflow(latest_fcf, fcf_5y_avg, result.fcf_yield, result.fcf_conversion)
            result.balance_sheet_score = score_balance(cash, debt, result.debt_to_ebitda, result.net_cash, market_cap)
            result.quality_score = score_quality(result.roe, result.gross_margin, result.operating_margin, result.net_margin)
            result.trend_score = score_trend(revenue_cagr, result.share_dilution_3y, latest_fcf, fcf_5y_avg)
            result.data_quality_score = score_data_quality(
                price,
                market_cap,
                revenue_latest,
                latest_fcf,
                fcf_5y_avg,
                latest_net_income,
                cash,
                debt,
                equity,
                total_assets,
                result.roe,
                result.gross_margin,
                result.operating_margin,
            )

            result.final_score = (
                result.mos_score
                + result.cashflow_score
                + result.balance_sheet_score
                + result.quality_score
                + result.trend_score
                + result.data_quality_score
            )

        intrinsic_adjusted, final_adjusted, feedback_label, flags = apply_feedback(
            ticker=ticker,
            intrinsic_value=result.intrinsic_value_total,
            final_score=result.final_score,
            flags=flags,
        )

        if intrinsic_adjusted != result.intrinsic_value_total and intrinsic_adjusted is not None and market_cap:
            result.intrinsic_value_total = intrinsic_adjusted
            result.intrinsic_value_per_share = price * intrinsic_adjusted / market_cap
            result.margin_of_safety = (result.intrinsic_value_per_share - price) / price
            update_buy_prices(result)
            result.mos_score = score_margin_of_safety(result.margin_of_safety)

        result.final_score = final_adjusted
        result.feedback_label = feedback_label

        result.trap_count = len(flags)
        result.trap_flags = ",".join(flags)

        # confidence = 数据质量 + 现金流稳定 + 非陷阱
        result.confidence_score = clamp(
            result.data_quality_score * 6
            + min(result.cashflow_score, 15) * 1.5
            + max(0, 20 - result.trap_count * 5),
            0,
            100,
        )

        if result.intrinsic_value_total is None or result.margin_of_safety is None:
            result.rating = "NO_DATA"
            result.reason = f"估值数据不足：{method}"
        elif result.model_type == "financial_pb_roe":
            if result.trap_count >= 2:
                result.rating = "D_TRAP"
                result.reason = f"金融股风险信号过多：{result.trap_flags}"
            elif result.margin_of_safety >= 0.35 and result.final_score >= 55 and (result.roe is not None and result.roe >= 0.10):
                result.rating = "B"
                result.reason = f"金融股 PB/ROE 口径有折价，仍需人工复核资产质量，估值法={method}"
            elif result.margin_of_safety >= 0:
                result.rating = "C_THIN"
                result.reason = "金融股 PB/ROE 口径有一定折价，但安全边际/ROE/数据质量不足，不进入主候选"
            else:
                result.rating = "PASS"
                result.reason = "金融股当前价格高于保守 PB/ROE 价值，没有安全边际"
        elif result.trap_count >= 3:
            result.rating = "D_TRAP"
            result.reason = f"疑似价值陷阱：{result.trap_flags}"
        elif result.margin_of_safety >= 0.50 and result.final_score >= 75:
            result.rating = "S"
            result.reason = f"安全边际很厚，{MODEL_VERSION.replace('MOS_Radar_', '')}模型={result.model_type}，估值法={method}"
        elif result.margin_of_safety >= 0.35 and result.final_score >= 65:
            result.rating = "A"
            result.reason = f"安全边际较厚，{MODEL_VERSION.replace('MOS_Radar_', '')}模型={result.model_type}，估值法={method}"
        elif result.margin_of_safety >= 0.20 and result.final_score >= 55:
            result.rating = "B"
            result.reason = f"有一定安全边际，{MODEL_VERSION.replace('MOS_Radar_', '')}模型={result.model_type}，估值法={method}"
        elif result.margin_of_safety >= 0.20:
            result.rating = "C_THIN"
            result.reason = "安全边际达到观察区，但综合分或质量门槛不足，未进入 B 级"
        elif result.margin_of_safety >= 0:
            result.rating = "C_THIN"
            result.reason = "安全边际低于 20%，偏薄，不优先"
        else:
            result.rating = "PASS"
            result.reason = "当前价格高于保守内在价值，没有安全边际"

        cap, cap_reasons = quality_rating_cap(result)
        if cap is not None:
            original_rating = result.rating
            result.rating = cap_rating(result.rating, cap)
            if result.rating != original_rating:
                result.rating_cap = cap
                result.reason += f"；质量/风险封顶：{original_rating}->{result.rating}({','.join(cap_reasons)})"

        if feedback_label:
            result.reason += f"；人工反馈标签={feedback_label}"

        return result

    except Exception as e:
        result.rating = "ERROR"
        result.reason = f"分析失败：{type(e).__name__}: {e}"
        return result

    finally:
        time.sleep(sleep_seconds)


def results_to_dataframe(results: list[AnalysisResult]) -> pd.DataFrame:
    rows = [asdict(r) if isinstance(r, AnalysisResult) else r for r in results]
    return pd.DataFrame(rows)
