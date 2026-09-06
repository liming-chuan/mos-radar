"""Strict, explainable price-zone screening. Thresholds are policy, not fitted returns."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math

import pandas as pd

from valuation import MODEL_VERSION


@dataclass(frozen=True)
class EntryPolicy:
    min_discount: float = 0.35
    cyclical_discount: float = 0.50
    annual_discount: float = 0.40
    min_stress_discount: float = 0.10
    min_owner_yield: float = 0.08
    max_net_debt_ebitda: float = 3.0
    min_interest_coverage: float = 4.0
    min_cash_conversion: float = 0.70
    max_dilution: float = 0.05
    max_fundamentals_days: int = 8
    max_quote_days: int = 5
    max_quarter_days: int = 200
    max_annual_days: int = 550
    near_entry_distance: float = 0.10
    max_value_decline: float = 0.15


POLICY = EntryPolicy()
CYCLICAL = {"energy_cyclical", "materials_cyclical", "cyclical_semiconductor",
            "industrial_normalized", "precious_metals_miner", "agri_cyclical", "consumer_cyclical"}
ENTRY_STATES = {"ENTRY_REVIEW", "DEEP_VALUE_REVIEW"}
STATUS_ZH = {"ENTRY_REVIEW": "价格达标·入场复核", "DEEP_VALUE_REVIEW": "深折价·入场复核",
             "NEAR_ENTRY": "接近触发价", "WAIT_PRICE": "等待价格", "RISK_BLOCKED": "风险未通过",
             "DATA_REQUIRED": "数据待补齐", "REVIEW_REQUIRED": "估值下修·重新复核",
             "SPECIAL_REVIEW": "行业专门复核", "HISTORICAL_ONLY": "仅历史压力测试"}
EVENT_ZH = {"FIRST_OBSERVATION": "首次记录", "UNCHANGED": "状态未变",
            "PRICE_ENTERED": "价格进入区间", "VALUE_REVISION_ENTERED": "估值变化后达标",
            "QUALITY_RECOVERED": "质量复核恢复", "EXITED": "已退出入场区", "STATE_CHANGED": "状态变化"}


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def truth(value):
    return str(value).strip().lower() in {"true", "1", "1.0", "yes"}


def age_days(value, now):
    date = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(date) else (now - date).total_seconds() / 86400


def evaluate_entry(row, now=None, policy=POLICY):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.to_datetime(now, utc=True)
    get = lambda key: number(row.get(key))
    price, value, stress = get("price"), get("intrinsic_value_per_share"), get("stress_value_per_share")
    required = policy.cyclical_discount if row.get("model_type") in CYCLICAL else policy.min_discount
    if row.get("financial_period_type") != "TTM":
        required = max(required, policy.annual_discount)
    result = {"discount_to_value": None, "upside_to_value": None, "stress_discount": None,
              "required_discount": required, "entry_price": None, "deep_entry_price": None,
              "distance_to_entry": None, "entry_status": "DATA_REQUIRED", "entry_reason": "",
              "entry_policy_version": "3", "entry_data_issues": "", "entry_risk_issues": "",
              "entry_value_ceiling": None, "entry_stress_ceiling": None, "entry_yield_ceiling": None,
              "entry_binding_constraint": ""}
    if price and price > 0 and value and value > 0:
        result.update(discount_to_value=1-price/value, upside_to_value=value/price-1)
    if price and price > 0 and stress and stress > 0:
        result["stress_discount"] = 1-price/stress
    fcf, cap = get("fcf_ttm"), get("market_cap")
    if all(x is not None and x > 0 for x in (price, value, stress, fcf, cap)):
        ceilings = {"保守价值折价": value*(1-required), "压力情景折价": stress*(1-policy.min_stress_discount),
                    "现金流收益率": price*fcf/cap/policy.min_owner_yield}
        ceiling = min(ceilings.values())
        result.update(entry_value_ceiling=ceilings["保守价值折价"], entry_stress_ceiling=ceilings["压力情景折价"],
                      entry_yield_ceiling=ceilings["现金流收益率"], entry_binding_constraint="、".join(
                          k for k, v in ceilings.items() if math.isclose(v, ceiling, rel_tol=1e-9)))
        result.update(entry_price=ceiling, deep_entry_price=min(ceiling, value*0.50, stress*0.75),
                      distance_to_entry=ceiling/price-1)

    def finish(status, reasons):
        result.update(entry_status=status, entry_reason="；".join(dict.fromkeys(reasons)))
        # An unauditable or vetoed price must not be presented as an actionable trigger.
        if status in {"DATA_REQUIRED", "RISK_BLOCKED", "SPECIAL_REVIEW", "HISTORICAL_ONLY"}:
            result.update(entry_price=None, deep_entry_price=None, distance_to_entry=None)
            result.update(entry_value_ceiling=None, entry_stress_ceiling=None, entry_yield_ceiling=None,
                          entry_binding_constraint="")
        return result

    if truth(row.get("is_historical_replay")):
        return finish("HISTORICAL_ONLY", ["当前财报配历史价，禁止生成实时入场信号"])
    if row.get("model_type") in {"financial_pb_roe", "reit_needs_affo"} or row.get("rating") == "SKIP":
        return finish("SPECIAL_REVIEW", ["需要行业资产质量或专门模型"])
    if str(row.get("valuation_method", "")).split("|")[0].split("_holding_")[0] in {"ncav_2_3", "tangible_book_0_8x"}:
        return finish("SPECIAL_REVIEW", ["仅资产折价参考，需核实资产可变现性，不能直接生成经营型入场价"])
    if row.get("rating") in {"ERROR", "NO_DATA"}:
        result["entry_data_issues"] = "本次估值未完成"
        return finish("DATA_REQUIRED", ["本次估值未完成", str(row.get("reason") or "请重试数据抓取")])

    missing, risks = [], []
    if str(row.get('ticker', '')).endswith('.HK'):
        from statement_evidence import evidence_fingerprint
        if row.get('statement_evidence_fingerprint') != evidence_fingerprint():
            missing.append("公告补录版本变化或未核验，需完整重扫")
    if row.get("statement_evidence_status") == "REJECTED":
        missing.append("公告补录校验失败或与数据源冲突，须复核诊断CSV")
    if row.get("model_version") != MODEL_VERSION:
        missing.append("旧模型结果需要重新扫描")
    if row.get("rating") in {"ERROR", "NO_DATA"}:
        missing.append("本次估值未完成")
    for key, label in (("price", "股价"), ("intrinsic_value_per_share", "保守价值"),
                       ("stress_value_per_share", "压力估值"),
                       ("market_cap", "市值"), ("cash", "现金"), ("total_debt", "总债务"),
                       ("shares_outstanding", "股本")):
        if get(key) is None:
            missing.append(label+"缺失")
    if not row.get("quote_currency") or not row.get("financial_currency"):
        missing.append("币种未确认")
    annual = row.get("financial_period_type") != "TTM"
    statement_limit = policy.max_annual_days if annual else policy.max_quarter_days
    for key, limit, label in (("fundamentals_asof", policy.max_fundamentals_days, "基本面抓取"),
                              ("financial_asof", statement_limit, "现金流财报"),
                              ("balance_asof", statement_limit, "资产负债表"),
                              ("price_asof", policy.max_quote_days, "行情")):
        age = age_days(row.get(key), now)
        if age is None or age < -0.01 or age > limit:
            missing.append(label+"日期缺失或过期")
    if row.get("price_data_status") != "OK":
        missing.append("价格更新失败或正在沿用旧价")
    if not truth(row.get("sbc_history_complete")):
        missing.append("SBC扣除证据不完整")
    if not truth(row.get("financial_period_aligned")):
        missing.append("现金流与收入利润报告期未对齐")
    years, positive = get("fcf_history_years"), get("fcf_positive_years")
    if years is None or years < 3:
        missing.append("不足三年可用Owner FCF")
    elif positive is None or positive < 3 or positive/years < 0.8:
        risks.append("现金流为正的年份不足")
    if truth(row.get("share_count_mismatch")):
        risks.append("市值、报价与股本不一致，须核对股类/ADR")
    if (get("trap_count") or 0) > 0 or str(row.get("trap_flags", "")).strip() not in {"", "nan", "None"}:
        risks.append("仍有价值陷阱标记")
    if row.get("rating") == "D_TRAP":
        risks.append("疑似价值陷阱")
    if row.get("rating_cap") in {"C_THIN", "D_TRAP"}:
        risks.append("估值引擎的质量/风险封顶尚未解除")
    if fcf is not None and cap is not None and cap > 0 and fcf/cap > 0.25:
        risks.append("Owner FCF收益率异常高，需排查周期高点或数据错配")
    for key, minimum, label in (("fcf_ttm", 0, "近期现金流"), ("fcf_3y_avg", 0, "三年现金流"),
                                ("fcf_5y_avg", 0, "长期现金流"), ("net_income_ttm", 0, "净利润"),
                                ("operating_margin", 0, "营业利润率")):
        if get(key) is None:
            missing.append(label+"缺失")
        elif get(key) <= minimum:
            risks.append(label+"非正")
    if value is not None and value <= 0 or stress is not None and stress <= 0:
        risks.append("保守或压力情景权益价值非正")
    for key, threshold, label, higher_bad in (
        ("fcf_conversion", policy.min_cash_conversion, "现金转换率不足70%", False),
        ("share_dilution_3y", policy.max_dilution, "三年稀释超过5%", True),
        ("quality_score", 6, "经营质量评分不足", False),
    ):
        n = get(key)
        if n is None:
            missing.append({"fcf_conversion": "现金转换率缺失", "share_dilution_3y": "三年股本变化缺失",
                            "quality_score": "经营质量评分缺失"}[key])
        elif (n > threshold if higher_bad else n < threshold):
            risks.append(label)
    if row.get("fcf_volatility_status") == "NONPOSITIVE_MEAN":
        risks.append("历史平均现金流非正，波动率不适用")
    elif get("fcf_volatility") is None:
        missing.append("现金流波动所需历史数据不足")
    elif get("fcf_volatility") > 0.60:
        risks.append("现金流波动过高")
    cash, debt = get("cash"), get("total_debt")
    if cash is not None and debt is not None and debt > cash:
        ebitda, coverage = get("ebitda"), get("interest_coverage")
        if ebitda is None or coverage is None:
            missing.append("净负债公司的偿债证据不足")
        else:
            if ebitda <= 0 or (debt-cash)/ebitda > policy.max_net_debt_ebitda:
                risks.append("净债务/EBITDA超过3倍或EBITDA非正")
            if coverage < policy.min_interest_coverage:
                risks.append("利息覆盖不足4倍")
    turnover = get("liquidity_value")
    minimum_turnover = 5_000_000 if str(row.get("ticker", "")).endswith(".HK") else 1_000_000
    if turnover is None:
        missing.append("成交金额缺失")
    elif turnover < minimum_turnover:
        risks.append("成交金额不足")
    if price is not None and price < 1:
        risks.append("低价股需专门复核")
    result.update(entry_data_issues="；".join(dict.fromkeys(missing)), entry_risk_issues="；".join(dict.fromkeys(risks)))
    if risks:
        return finish("RISK_BLOCKED", risks+missing)
    if missing or result["entry_price"] is None:
        return finish("DATA_REQUIRED", missing or ["入场估值数据不足"])
    if price <= result["entry_price"]:
        status = "DEEP_VALUE_REVIEW" if price <= result["deep_entry_price"] else "ENTRY_REVIEW"
        return finish(status, ["保守折价、压力折价及现金流门槛同时达标", "复核最新公告、价值实现条件后再决定"])
    status = "NEAR_ENTRY" if price <= result["entry_price"]*(1+policy.near_entry_distance) else "WAIT_PRICE"
    return finish(status, ["质量门槛通过，等待价格降至触发上限", "当前限制："+result["entry_binding_constraint"]])


def annotate_opportunities(df, previous=None, now=None, policy=POLICY):
    if df.empty:
        return df.copy()
    prior = {}
    if previous is not None and not previous.empty and "ticker" in previous:
        prior = {str(r["ticker"]): r for _, r in previous.iterrows()}
    output = []
    observed_at = pd.Timestamp.now(tz="UTC") if now is None else pd.to_datetime(now, utc=True)
    for _, row in df.iterrows():
        result = evaluate_entry(row, now=observed_at, policy=policy)
        old = prior.get(str(row.get("ticker")))
        event, decline = "FIRST_OBSERVATION", None
        review_anchor = None
        if old is not None and number(old.get("entry_policy_version")) == number(result["entry_policy_version"]) and old.get("model_version") == row.get("model_version"):
            old_iv, iv = number(old.get("intrinsic_value_per_share")), number(row.get("intrinsic_value_per_share"))
            if old_iv and iv is not None:
                decline = iv/old_iv-1
                review_anchor = number(old.get("value_review_anchor"))
                if decline < -policy.max_value_decline:
                    review_anchor = max(review_anchor or 0, old_iv)
                if review_anchor and iv >= review_anchor*(1-policy.max_value_decline):
                    review_anchor = None
                if review_anchor and result["entry_status"] in ENTRY_STATES | {"WAIT_PRICE", "NEAR_ENTRY"}:
                    result.update(entry_status="REVIEW_REQUIRED", entry_reason="保守价值较上次下修超过15%，先重做投资论点",
                                  entry_price=None, deep_entry_price=None, distance_to_entry=None,
                                  entry_value_ceiling=None, entry_stress_ceiling=None, entry_yield_ceiling=None,
                                  entry_binding_constraint="")
            current_in = result["entry_status"] in ENTRY_STATES
            old_in = old.get("entry_status") in ENTRY_STATES
            event = "UNCHANGED" if old.get("entry_status") == result["entry_status"] else "STATE_CHANGED"
            if current_in and not old_in:
                old_price, old_limit = number(old.get("price")), number(old.get("entry_price"))
                new_price = number(row.get("price"))
                if old_price and old_limit and new_price and old_price > old_limit >= new_price:
                    event = "PRICE_ENTERED"
                elif old.get("entry_status") in {"NEAR_ENTRY", "WAIT_PRICE"}:
                    event = "VALUE_REVISION_ENTERED"
                else:
                    event = "QUALITY_RECOVERED"
            elif old_in and not current_in:
                event = "EXITED"
        merged = row.to_dict()
        merged.update(result, entry_event=event, value_change_since_previous=decline,
                      value_review_anchor=review_anchor, signal_observed_at=observed_at.isoformat())
        output.append(merged)
    return pd.DataFrame(output)


def policy_metadata():
    return asdict(POLICY)
