"""Validate provider-labelled trailing statements; never annualize half-year data."""
from __future__ import annotations

import pandas as pd
import math


def aligned_trailing_values(income, cashflow, not_before=None, now=None):
    """Inputs MUST come from the provider's explicit freq='trailing' endpoint.

    Only accept the newest column of both statements. Do not backfill missing
    latest items from older periods. Optional profit metrics stay unknown.
    """
    if income is None or cashflow is None or income.empty or cashflow.empty:
        return None, "TRAILING_UNAVAILABLE"
    frames = []
    for frame in (income, cashflow):
        frame = frame.copy()
        dates = pd.to_datetime(frame.columns, utc=True, errors="coerce")
        if dates.isna().any() or dates.has_duplicates:
            return None, "TRAILING_INVALID_DATES"
        frame.columns = dates
        frames.append(frame.sort_index(axis=1, ascending=False))
    income, cashflow = frames
    date = income.columns[0]
    if date != cashflow.columns[0]:
        return None, "TRAILING_PERIOD_MISMATCH"
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.to_datetime(now, utc=True)
    if date > now or (now-date).days > 200:
        return None, "TRAILING_STALE_OR_FUTURE"
    baseline = pd.to_datetime(not_before, utc=True, errors="coerce")
    if pd.notna(baseline) and date <= baseline:
        return None, "TRAILING_NOT_NEWER_THAN_ANNUAL"

    def value(frame, names):
        for name in names:
            if name in frame.index:
                try:
                    n = float(frame.loc[name, date])
                    if math.isfinite(n):
                        return n
                except (TypeError, ValueError):
                    pass
        return None

    result = {key: value(income, names) for key, names in {
        "revenue": ["Total Revenue", "Operating Revenue"],
        "net_income": ["Net Income", "Net Income Common Stockholders"],
        "gross_profit": ["Gross Profit"],
        "operating_income": ["Operating Income", "Operating Income or Loss"],
        "ebitda": ["EBITDA"],
        "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    }.items()}
    ocf = value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = value(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    sbc = value(cashflow, ["Stock Based Compensation"])
    if any(n is None for n in (ocf, capex, sbc, result["revenue"], result["net_income"], result["operating_income"])):
        return None, "TRAILING_REQUIRED_ITEMS_MISSING"
    result.update(reported_fcf=ocf-abs(capex), owner_fcf=ocf-abs(capex)-abs(sbc),
                  sbc=abs(sbc), asof=date.date().isoformat())
    return result, "PROVIDER_TRAILING"
