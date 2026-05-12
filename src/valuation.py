from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf


EXCLUDED_SECTOR_KEYWORDS = {
    # V1 deliberately avoids sectors that need very different valuation models.
    "financial services",
    "real estate",
}

EXCLUDED_INDUSTRY_KEYWORDS = {
    "biotechnology",
    "reit",
    "shell companies",
}

SECTOR_FCF_MULTIPLE = {
    "Technology": 12,
    "Communication Services": 11,
    "Consumer Cyclical": 10,
    "Consumer Defensive": 12,
    "Healthcare": 11,
    "Industrials": 10,
    "Energy": 7,
    "Basic Materials": 8,
    "Utilities": 9,
}

SECTOR_EARNINGS_MULTIPLE = {
    "Technology": 13,
    "Communication Services": 12,
    "Consumer Cyclical": 11,
    "Consumer Defensive": 13,
    "Healthcare": 12,
    "Industrials": 11,
    "Energy": 8,
    "Basic Materials": 9,
    "Utilities": 10,
}


@dataclass
class StockResult:
    ticker: str
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    price: Optional[float] = None
    market_cap: Optional[float] = None
    shares_outstanding: Optional[float] = None
    enterprise_value: Optional[float] = None
    revenue_latest: Optional[float] = None
    revenue_5y_avg: Optional[float] = None
    revenue_5y_cagr: Optional[float] = None
    net_income_latest: Optional[float] = None
    normalized_net_income: Optional[float] = None
    fcf_latest: Optional[float] = None
    fcf_5y_avg: Optional[float] = None
    operating_cashflow_latest: Optional[float] = None
    capex_latest: Optional[float] = None
    total_cash: Optional[float] = None
    total_debt: Optional[float] = None
    ebitda: Optional[float] = None
    net_debt: Optional[float] = None
    roe: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    profit_margin: Optional[float] = None
    fcf_yield: Optional[float] = None
    pe: Optional[float] = None
    forward_pe: Optional[float] = None
    ev_ebitda: Optional[float] = None
    debt_to_ebitda: Optional[float] = None
    intrinsic_equity_value: Optional[float] = None
    intrinsic_value_per_share: Optional[float] = None
    margin_of_safety: Optional[float] = None
    mos_score: float = 0
    cashflow_score: float = 0
    balance_sheet_score: float = 0
    quality_score: float = 0
    data_score: float = 0
    final_score: float = 0
    rating: str = "NO_DATA"
    trap_flags: str = ""
    reason: str = ""


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, str) and not x.strip():
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _first_not_none(*values: Any) -> Optional[float]:
    for v in values:
        fv = _safe_float(v)
        if fv is not None:
            return fv
    return None


def _row_values(df: Optional[pd.DataFrame], names: List[str]) -> List[float]:
    if df is None or df.empty:
        return []
    idx_lower = {str(i).strip().lower(): i for i in df.index}
    for name in names:
        key = name.strip().lower()
        if key in idx_lower:
            s = df.loc[idx_lower[key]].dropna()
            vals = []
            for v in s.values:
                fv = _safe_float(v)
                if fv is not None:
                    vals.append(fv)
            return vals
    return []


def _latest(values: List[float]) -> Optional[float]:
    return values[0] if values else None


def _avg_positive_or_all(values: List[float]) -> Optional[float]:
    vals = [_safe_float(v) for v in values if _safe_float(v) is not None]
    if not vals:
        return None
    positives = [v for v in vals if v > 0]
    if len(positives) >= 2:
        return float(np.mean(positives))
    return float(np.mean(vals))


def _cagr(values: List[float]) -> Optional[float]:
    vals = [_safe_float(v) for v in values if _safe_float(v) is not None]
    if len(vals) < 3:
        return None
    latest = vals[0]
    oldest = vals[-1]
    years = len(vals) - 1
    if latest is None or oldest is None or latest <= 0 or oldest <= 0 or years <= 0:
        return None
    return (latest / oldest) ** (1 / years) - 1


def _get_fast_info(ticker_obj: yf.Ticker, key: str) -> Optional[float]:
    try:
        return _safe_float(ticker_obj.fast_info.get(key))
    except Exception:
        return None


def _get_df(ticker_obj: yf.Ticker, attr_name: str) -> pd.DataFrame:
    try:
        df = getattr(ticker_obj, attr_name)
        if isinstance(df, pd.DataFrame):
            # yfinance returns latest year first for most statements.
            return df
    except Exception:
        pass
    return pd.DataFrame()


def _sector_multiple(sector: str, table: Dict[str, int], default: int) -> int:
    return table.get(sector or "", default)


def _score_mos(mos: Optional[float]) -> float:
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


def _score_cashflow(fcf_latest: Optional[float], fcf_5y_avg: Optional[float], fcf_yield: Optional[float], net_income_latest: Optional[float]) -> float:
    score = 0.0
    if fcf_latest is not None and fcf_latest > 0:
        score += 5
    if fcf_5y_avg is not None and fcf_5y_avg > 0:
        score += 5
    if fcf_yield is not None:
        if fcf_yield >= 0.10:
            score += 6
        elif fcf_yield >= 0.07:
            score += 5
        elif fcf_yield >= 0.05:
            score += 3
        elif fcf_yield >= 0.03:
            score += 1
    if fcf_latest is not None and net_income_latest is not None and net_income_latest > 0:
        conversion = fcf_latest / net_income_latest
        if conversion >= 0.9:
            score += 4
        elif conversion >= 0.6:
            score += 2
    return min(score, 20)


def _score_balance(total_cash: Optional[float], total_debt: Optional[float], debt_to_ebitda: Optional[float], market_cap: Optional[float]) -> float:
    score = 0.0
    cash = total_cash or 0
    debt = total_debt or 0
    if debt <= cash:
        score += 6
    elif market_cap and debt / market_cap < 0.25:
        score += 3
    if debt_to_ebitda is not None:
        if debt_to_ebitda <= 1:
            score += 6
        elif debt_to_ebitda <= 2.5:
            score += 4
        elif debt_to_ebitda <= 4:
            score += 2
    elif debt == 0:
        score += 4
    if market_cap and cash / market_cap >= 0.10:
        score += 3
    elif market_cap and cash / market_cap >= 0.05:
        score += 1
    return min(score, 15)


def _score_quality(roe: Optional[float], gross_margin: Optional[float], operating_margin: Optional[float], revenue_cagr: Optional[float], profit_margin: Optional[float]) -> float:
    score = 0.0
    if roe is not None:
        if roe >= 0.20:
            score += 4
        elif roe >= 0.10:
            score += 2
    if gross_margin is not None:
        if gross_margin >= 0.55:
            score += 4
        elif gross_margin >= 0.35:
            score += 2
    if operating_margin is not None:
        if operating_margin >= 0.20:
            score += 3
        elif operating_margin >= 0.10:
            score += 2
    if profit_margin is not None:
        if profit_margin >= 0.15:
            score += 2
        elif profit_margin >= 0.08:
            score += 1
    if revenue_cagr is not None:
        if revenue_cagr >= 0.08:
            score += 2
        elif revenue_cagr >= 0.02:
            score += 1
    return min(score, 15)


def _data_score(*fields: Any) -> float:
    present = sum(1 for f in fields if _safe_float(f) is not None)
    return min(5, 5 * present / max(1, len(fields)))


def _format_reason(result: StockResult) -> str:
    parts = []
    if result.margin_of_safety is not None:
        parts.append(f"安全边际约 {result.margin_of_safety:.1%}")
    if result.fcf_yield is not None:
        parts.append(f"FCF Yield {result.fcf_yield:.1%}")
    if result.debt_to_ebitda is not None:
        parts.append(f"债务/EBITDA {result.debt_to_ebitda:.1f}")
    if result.roe is not None:
        parts.append(f"ROE {result.roe:.1%}")
    if result.trap_flags:
        parts.append(f"警告：{result.trap_flags}")
    return "；".join(parts) if parts else "数据不足，需人工复核"


def analyze_ticker(ticker: str, sleep_seconds: float = 0.0) -> StockResult:
    ticker = ticker.strip().upper()
    r = StockResult(ticker=ticker)
    if not ticker:
        r.reason = "Empty ticker"
        return r

    try:
        t = yf.Ticker(ticker)
        try:
            info = t.info or {}
        except Exception:
            info = {}

        r.company_name = str(info.get("shortName") or info.get("longName") or "")
        r.sector = str(info.get("sector") or "")
        r.industry = str(info.get("industry") or "")

        sector_l = r.sector.lower()
        industry_l = r.industry.lower()
        if any(k in sector_l for k in EXCLUDED_SECTOR_KEYWORDS) or any(k in industry_l for k in EXCLUDED_INDUSTRY_KEYWORDS):
            r.rating = "SKIP"
            r.trap_flags = "V1暂不覆盖该行业估值模型"
            r.reason = _format_reason(r)
            return r

        r.price = _first_not_none(
            _get_fast_info(t, "last_price"),
            info.get("currentPrice"),
            info.get("regularMarketPrice"),
            info.get("previousClose"),
        )
        r.market_cap = _first_not_none(
            _get_fast_info(t, "market_cap"),
            info.get("marketCap"),
        )
        r.shares_outstanding = _first_not_none(
            info.get("sharesOutstanding"),
            (r.market_cap / r.price) if r.market_cap and r.price else None,
        )
        r.enterprise_value = _safe_float(info.get("enterpriseValue"))
        r.total_cash = _safe_float(info.get("totalCash"))
        r.total_debt = _safe_float(info.get("totalDebt"))
        r.ebitda = _safe_float(info.get("ebitda"))
        r.roe = _safe_float(info.get("returnOnEquity"))
        r.gross_margin = _safe_float(info.get("grossMargins"))
        r.operating_margin = _safe_float(info.get("operatingMargins"))
        r.profit_margin = _safe_float(info.get("profitMargins"))
        r.pe = _safe_float(info.get("trailingPE"))
        r.forward_pe = _safe_float(info.get("forwardPE"))

        cashflow = _get_df(t, "cashflow")
        financials = _get_df(t, "financials")

        fcf_values = _row_values(cashflow, ["Free Cash Flow", "FreeCashFlow"])
        ocf_values = _row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex_values = _row_values(cashflow, ["Capital Expenditure", "Capital Expenditures"])
        revenue_values = _row_values(financials, ["Total Revenue", "Revenue"])
        net_income_values = _row_values(financials, ["Net Income", "Net Income Common Stockholders"])

        r.fcf_latest = _first_not_none(info.get("freeCashflow"), _latest(fcf_values))
        r.fcf_5y_avg = _avg_positive_or_all(fcf_values[:5])
        r.operating_cashflow_latest = _first_not_none(info.get("operatingCashflow"), _latest(ocf_values))
        r.capex_latest = _latest(capex_values)
        r.revenue_latest = _first_not_none(info.get("totalRevenue"), _latest(revenue_values))
        r.revenue_5y_avg = _avg_positive_or_all(revenue_values[:5])
        r.revenue_5y_cagr = _cagr(revenue_values[:5])
        r.net_income_latest = _latest(net_income_values)
        r.normalized_net_income = _avg_positive_or_all(net_income_values[:5])

        if r.total_cash is not None or r.total_debt is not None:
            r.net_debt = (r.total_debt or 0) - (r.total_cash or 0)
        if r.market_cap and r.fcf_latest is not None:
            r.fcf_yield = r.fcf_latest / r.market_cap
        if r.enterprise_value and r.ebitda and r.ebitda > 0:
            r.ev_ebitda = r.enterprise_value / r.ebitda
        if r.total_debt is not None and r.ebitda and r.ebitda > 0:
            r.debt_to_ebitda = r.total_debt / r.ebitda

        fcf_mult = _sector_multiple(r.sector, SECTOR_FCF_MULTIPLE, default=10)
        earn_mult = _sector_multiple(r.sector, SECTOR_EARNINGS_MULTIPLE, default=10)
        valuation_candidates: List[float] = []
        if r.fcf_5y_avg is not None and r.fcf_5y_avg > 0:
            valuation_candidates.append(r.fcf_5y_avg * fcf_mult)
        if r.fcf_latest is not None and r.fcf_latest > 0:
            valuation_candidates.append(r.fcf_latest * min(fcf_mult, 10))
        if r.normalized_net_income is not None and r.normalized_net_income > 0:
            valuation_candidates.append(r.normalized_net_income * earn_mult)
        if r.total_cash is not None and r.total_debt is not None and r.fcf_5y_avg is not None and r.fcf_5y_avg > 0:
            valuation_candidates.append((r.total_cash - r.total_debt) + r.fcf_5y_avg * 8)

        valuation_candidates = [v for v in valuation_candidates if v and v > 0]
        if valuation_candidates:
            # Conservative: use the lowest candidate, not the optimistic one.
            r.intrinsic_equity_value = float(min(valuation_candidates))
            if r.shares_outstanding and r.shares_outstanding > 0:
                r.intrinsic_value_per_share = r.intrinsic_equity_value / r.shares_outstanding
        if r.price and r.intrinsic_value_per_share:
            r.margin_of_safety = (r.intrinsic_value_per_share - r.price) / r.price

        flags = []
        if r.fcf_latest is not None and r.fcf_latest <= 0:
            flags.append("FCF为负")
        if r.fcf_5y_avg is not None and r.fcf_5y_avg <= 0:
            flags.append("5年FCF均值不佳")
        if r.revenue_5y_cagr is not None and r.revenue_5y_cagr < -0.03:
            flags.append("收入趋势下滑")
        if r.debt_to_ebitda is not None and r.debt_to_ebitda > 4:
            flags.append("债务/EBITDA偏高")
        if r.profit_margin is not None and r.profit_margin < 0:
            flags.append("利润率为负")
        if r.market_cap is None or r.price is None:
            flags.append("价格/市值数据缺失")
        if r.intrinsic_value_per_share is None:
            flags.append("保守估值数据不足")

        r.trap_flags = "、".join(flags)
        r.mos_score = _score_mos(r.margin_of_safety)
        r.cashflow_score = _score_cashflow(r.fcf_latest, r.fcf_5y_avg, r.fcf_yield, r.net_income_latest)
        r.balance_sheet_score = _score_balance(r.total_cash, r.total_debt, r.debt_to_ebitda, r.market_cap)
        r.quality_score = _score_quality(r.roe, r.gross_margin, r.operating_margin, r.revenue_5y_cagr, r.profit_margin)
        r.data_score = _data_score(r.price, r.market_cap, r.fcf_latest, r.fcf_5y_avg, r.normalized_net_income, r.total_debt, r.total_cash, r.revenue_latest)
        r.final_score = r.mos_score + r.cashflow_score + r.balance_sheet_score + r.quality_score + r.data_score

        hard_trap_count = sum(1 for f in flags if f in {"FCF为负", "5年FCF均值不佳", "收入趋势下滑", "债务/EBITDA偏高", "利润率为负"})
        if r.rating != "SKIP":
            if r.margin_of_safety is None:
                r.rating = "NO_DATA"
            elif hard_trap_count >= 2:
                r.rating = "D_TRAP"
            elif r.margin_of_safety >= 0.50 and r.final_score >= 75:
                r.rating = "S"
            elif r.margin_of_safety >= 0.35 and r.final_score >= 65:
                r.rating = "A"
            elif r.margin_of_safety >= 0.20 and r.final_score >= 55:
                r.rating = "B"
            elif r.margin_of_safety >= 0:
                r.rating = "C_THIN"
            else:
                r.rating = "PASS"
        r.reason = _format_reason(r)
        return r

    except Exception as exc:
        r.rating = "ERROR"
        r.trap_flags = f"抓取/计算失败：{type(exc).__name__}"
        r.reason = str(exc)[:180]
        return r
    finally:
        if sleep_seconds:
            time.sleep(sleep_seconds)


def results_to_dataframe(results: List[StockResult]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(x) for x in results])
    numeric_cols = [c for c in df.columns if c not in {"ticker", "company_name", "sector", "industry", "rating", "trap_flags", "reason"}]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
