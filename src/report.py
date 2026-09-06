from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional
from html import escape

import pandas as pd


def pct(x) -> str:
    try:
        if pd.isna(x):
            return "N/A"
        return f"{float(x):.1%}"
    except Exception:
        return "N/A"


def money(x, symbol: str = "$") -> str:
    try:
        if pd.isna(x):
            return "N/A"
        x = float(x)
        if abs(x) >= 1e12:
            return f"{symbol}{x/1e12:.2f}T"
        if abs(x) >= 1e9:
            return f"{symbol}{x/1e9:.2f}B"
        if abs(x) >= 1e6:
            return f"{symbol}{x/1e6:.2f}M"
        return f"{symbol}{x:.2f}"
    except Exception:
        return "N/A"


SECTOR_ZH = {
    "Technology": "科技",
    "Financial Services": "金融服务",
    "Consumer Cyclical": "可选消费",
    "Consumer Defensive": "防御消费",
    "Communication Services": "通信服务",
    "Healthcare": "医疗健康",
    "Industrials": "工业",
    "Basic Materials": "基础材料",
    "Energy": "能源",
    "Utilities": "公用事业",
    "Real Estate": "房地产",
    "Unknown": "未知",
}

MODEL_STATUS_ZH = {
    "GENERAL_OWNER_FCF_MODEL": "通用所有者自由现金流模型",
    "SOFTWARE_OWNER_FCF_SBC_ADJUSTED_NEEDS_NRR_RPO_RULE_OF_40_REVIEW": "软件/科技所有者自由现金流模型，需人工复核股权激励、收入留存、订单和40法则",
    "COMMODITY_CYCLE_LIMITED_NEEDS_MID_CYCLE_PRICE_COST_RESERVE_REVIEW": "资源周期模型，需人工复核中周期价格、成本和储量",
    "AGRI_CYCLE_LIMITED_NEEDS_NORMALIZED_MARGIN_VOLUME_REVIEW": "农业/农产品周期模型，需人工复核正常化利润率、产量和商品价格",
    "SEMICONDUCTOR_CYCLE_LIMITED_NEEDS_INVENTORY_MARGIN_CAPEX_REVIEW": "半导体周期模型，需人工复核库存、毛利率和资本开支",
    "BANK_LIMITED_PB_ROE_NEEDS_CET1_NIM_NPL_DEPOSIT_COST": "银行市净率/净资产收益率初筛，需人工复核资本充足率、息差、不良率和存款成本",
    "INSURANCE_LIMITED_PB_ROE_NEEDS_COMBINED_RATIO_FLOAT_RESERVES": "保险市净率/净资产收益率初筛，需人工复核综合成本率、浮存金和准备金",
    "FINANCIAL_LIMITED_PB_ROE_NEEDS_INDUSTRY_SPECIFIC_REVIEW": "金融股市净率/净资产收益率初筛，需行业专门复核",
    "REIT_SKIPPED_NEEDS_AFFO_NOI_CAP_RATE": "房地产信托需调整后运营现金流、净运营收入、资本化率模型，当前跳过",
}

METHOD_ZH = {
    "normalized_net_income_pe": "正常化净利润倍数",
    "asset_plus_fcf_8x": "净资产现金 + 自由现金流8倍",
    "normalized_fcf_multiple": "正常化所有者自由现金流倍数",
    "latest_fcf_capped_10x": "最近所有者自由现金流封顶10倍",
    "conservative_5y_dcf": "保守5年现金流折现",
    "tangible_book_0_8x": "有形账面价值0.8倍",
    "ncav_2_3": "净流动资产2/3",
}

PERIOD_ZH = {
    "TTM": "最近四季滚动",
    "ANNUAL_FALLBACK": "年报口径",
    "MISSING": "缺失",
    "N/A": "N/A",
}

REASON_TOKEN_ZH = {
    "trap_count_ge_2": "风险信号不少于2个",
    "trap_count_ge_3": "风险信号不少于3个",
    "financial_limited_pb_roe_model": "金融股市净率/净资产收益率模型有限",
    "low_data_quality": "数据质量偏低",
    "weak_cashflow_score": "现金流评分偏弱",
    "weak_quality_score": "质量评分偏弱",
    "debt_to_ebitda_over_5": "债务/经营利润超过5倍",
    "interest_coverage_under_2": "利息覆盖低于2倍",
    "precious_metals_cycle_model": "贵金属周期模型",
    "abnormal_fcf_yield": "自由现金流收益率异常偏高",
    "cyclical_profit_reversal_risk": "周期股利润反转风险",
    "hk_penny_low_turnover_trap": "港股低价且成交金额不足",
    "hk_price_below_1": "港股股价低于1港元",
    "hk_low_turnover": "港股成交金额不足",
    "latest_fcf_negative": "最近自由现金流为负",
    "avg_fcf_not_positive": "平均自由现金流不为正",
    "revenue_decline": "收入下滑",
    "revenue_decline_streak": "收入连续下滑",
    "gross_margin_decline": "毛利率下滑",
    "operating_margin_decline": "经营利润率下滑",
    "high_fcf_volatility": "自由现金流波动过大",
    "high_debt_to_ebitda": "债务/经营利润偏高",
    "weak_interest_coverage": "利息覆盖偏弱",
    "debt_exceeds_market_cap": "债务超过市值",
    "debt_over_5x_avg_fcf": "债务超过5倍平均FCF",
    "negative_operating_margin": "经营利润率为负",
    "NO_VALID_VALUATION": "没有有效估值",
}


def display_sector(value: str) -> str:
    s = str(value or "")
    return SECTOR_ZH.get(s, s or "未知")


def display_model_status(value: str) -> str:
    s = str(value or "")
    return MODEL_STATUS_ZH.get(s, s)


def display_method(value: str) -> str:
    s = str(value or "")
    if s in METHOD_ZH:
        return METHOD_ZH[s]
    if s.startswith("financial_pb_roe_tangible_book_"):
        return "金融股市净率/净资产收益率：有形账面价值 " + s.rsplit("_", 1)[-1].replace("x", "倍")
    if s.startswith("financial_pb_roe_book_"):
        return "金融股市净率/净资产收益率：账面价值 " + s.rsplit("_", 1)[-1].replace("x", "倍")
    if "_holding_" in s and s.endswith("x"):
        base, discount = s.rsplit("_holding_", 1)
        return display_method(base) + "，控股/投资资产折价 " + discount.replace("x", "倍")
    return s


def display_period(value: str) -> str:
    s = str(value or "")
    return PERIOD_ZH.get(s, s or "N/A")


def translate_tokens(text: str) -> str:
    out = str(text or "")
    tokens = {**METHOD_ZH, **MODEL_STATUS_ZH, **REASON_TOKEN_ZH}
    for key in sorted(tokens, key=len, reverse=True):
        value = tokens[key]
        out = out.replace(key, value)
    return out


def display_candidates(value: str, symbol: str = "$") -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    parts = []
    for raw in s.split(";"):
        item = raw.strip()
        if not item:
            continue
        if "=" not in item:
            parts.append(translate_tokens(item))
            continue
        key, val = item.split("=", 1)
        label = display_method(key.strip())
        if key.strip() in {"ncav_2_3", "tangible_book_0_8x"}:
            label = "资产参考：" + label
        try:
            amount = money(float(val), symbol=symbol)
        except Exception:
            amount = translate_tokens(val.strip())
        parts.append(f"{label}={amount}")
    return "; ".join(parts)


def num(x) -> str:
    try:
        if pd.isna(x):
            return "N/A"
        return f"{float(x):.1f}"
    except Exception:
        return "N/A"


def is_true(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y"}
    return bool(v)


def normalize_numeric(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in [
        "margin_of_safety",
        "final_score",
        "fcf_yield",
        "reported_fcf_ttm",
        "sbc_ttm",
        "accrual_ratio",
        "ncav",
        "tangible_equity",
        "risk_free_rate",
        "discount_rate_used",
        "price",
        "intrinsic_value_per_share",
        "buy_price_20mos",
        "buy_price_35mos",
        "buy_price_50mos",
        "debt_to_ebitda",
        "interest_coverage",
        "roe",
        "price_change_since_scan",
        "mos_change_since_scan",
        "current_price",
        "current_market_cap",
        "return_since_backtest",
        "cache_age_days",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def sort_for_report(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    rating_order = {
        "S": 0,
        "A": 1,
        "B": 2,
        "C_THIN": 3,
        "PASS": 4,
        "D_TRAP": 5,
        "NO_DATA": 6,
        "SKIP": 7,
        "ERROR": 8,
    }

    df["_rating_order"] = df.get("rating", pd.Series(index=df.index, dtype=object)).map(rating_order).fillna(9)

    sort_cols = ["_rating_order"]
    ascending = [True]

    if "margin_of_safety" in df.columns:
        sort_cols.append("margin_of_safety")
        ascending.append(False)

    if "final_score" in df.columns:
        sort_cols.append("final_score")
        ascending.append(False)

    return df.sort_values(sort_cols, ascending=ascending)


def high_margin_candidates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "rating" not in df.columns:
        return pd.DataFrame()
    out = df[df["rating"].isin(["S", "A", "B"])].copy()
    return sort_for_report(out)


def holdings_all(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "is_holding" not in df.columns:
        return pd.DataFrame()
    mask = df["is_holding"].map(is_true)
    return sort_for_report(df[mask].copy())


def rating_badge(rating: str) -> str:
    rating = str(rating or "N/A")
    colors = {
        "S": ("#064e3b", "#d1fae5"),
        "A": ("#065f46", "#ecfdf5"),
        "B": ("#1e40af", "#dbeafe"),
        "C_THIN": ("#92400e", "#fef3c7"),
        "PASS": ("#7f1d1d", "#fee2e2"),
        "D_TRAP": ("#7f1d1d", "#fecaca"),
        "NO_DATA": ("#374151", "#f3f4f6"),
        "SKIP": ("#374151", "#f3f4f6"),
        "ERROR": ("#7f1d1d", "#fee2e2"),
    }
    fg, bg = colors.get(rating, ("#374151", "#f3f4f6"))
    return (
        f'<span style="display:inline-block;padding:3px 8px;border-radius:999px;'
        f'font-weight:700;color:{fg};background:{bg};font-size:12px;">{escape(rating)}</span>'
    )


def short_reason(x, limit: int = 80) -> str:
    s = translate_tokens(str(x or ""))
    s = s.replace("\n", " ").strip()
    if len(s) > limit:
        return escape(s[:limit] + "...")
    return escape(s)


def sort_by_mos_score(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()
    sort_cols = []
    ascending = []

    if "margin_of_safety" in df.columns:
        sort_cols.append("margin_of_safety")
        ascending.append(False)

    if "final_score" in df.columns:
        sort_cols.append("final_score")
        ascending.append(False)

    if not sort_cols:
        return df

    return df.sort_values(sort_cols, ascending=ascending)


def is_financial_row(row) -> bool:
    sector = str(row.get("sector", "") or "").lower()
    model_type = str(row.get("model_type", "") or "").lower()
    return "financial" in sector or model_type == "financial_pb_roe"


def split_financials(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return df.copy(), df.copy()
    mask = df.apply(is_financial_row, axis=1)
    return df[~mask].copy(), df[mask].copy()


def diversified_sample(df: pd.DataFrame, limit: int = 30, per_sector: int = 5) -> pd.DataFrame:
    if df.empty or "sector" not in df.columns:
        return df.head(limit)

    selected = []
    counts: dict[str, int] = {}

    for idx, row in df.iterrows():
        sector = str(row.get("sector", "") or "Unknown")
        if counts.get(sector, 0) >= per_sector:
            continue
        selected.append(idx)
        counts[sector] = counts.get(sector, 0) + 1
        if len(selected) >= limit:
            break

    return df.loc[selected].copy()


def text_value(value) -> str:
    return "" if value is None or pd.isna(value) else str(value)


def discount_label(value) -> str:
    from opportunity import number
    n = number(value)
    if n is None:
        return "待核实"
    return f"折价 {n:.1%}" if n >= 0 else f"溢价 {-n:.1%}"


def distance_label(value) -> str:
    from opportunity import number
    n = number(value)
    if n is None:
        return "未生成触发价"
    if n >= 0:
        return "价格已达标"
    return f"仍需下跌 {-n:.1%}" + ("（距离较远）" if n < -0.50 else "")


def stock_cards(df, title, limit=None, currency_symbol="$", entry=False):
    from opportunity import STATUS_ZH, EVENT_ZH
    parts = [f"<h2>{escape(title)}</h2>"]
    if df.empty:
        return parts[0] + '<div class="empty">暂无符合条件的公司。</div>'
    sample = df if limit is None else df.head(limit)
    for _, r in sample.iterrows():
        status = STATUS_ZH.get(r.get("entry_status"), "未评估严格入场条件")
        metrics = [("现价", money(r.get("price"), currency_symbol)),
                   ("保守价值/股", money(r.get("intrinsic_value_per_share"), currency_symbol))]
        if entry:
            metrics += [("触发上限", money(r.get("entry_price"), currency_symbol)),
                        ("距触发上限", distance_label(r.get("distance_to_entry"))),
                        ("保守折价", discount_label(r.get("discount_to_value"))),
                        ("压力折价", discount_label(r.get("stress_discount"))),
                        ("深折价观察价", money(r.get("deep_entry_price"), currency_symbol)),
                        ("状态变化", EVENT_ZH.get(r.get("entry_event"), "首次记录"))]
        else:
            metrics += [("潜在上涨空间（旧MoS）", pct(r.get("margin_of_safety"))),
                        ("Owner FCF收益率", pct(r.get("fcf_yield")))]
        if is_true(r.get("is_historical_replay")) and pd.notna(r.get("return_since_backtest")):
            metrics.append(("回放日至今", pct(r.get("return_since_backtest"))))
        # Two columns also work in email clients without CSS grid support.
        items = [f'<td><span class="sub">{escape(k)}</span><br><b>{escape(v)}</b></td>' for k, v in metrics]
        rows = ''.join('<tr>'+''.join(items[i:i+2])+'</tr>' for i in range(0, len(items), 2))
        reason = text_value(r.get("entry_reason")) or "严格入场条件尚未评估，请重新完整扫描。"
        parts.append(f'<div class="stock"><h3>{escape(text_value(r.get("ticker")))} · '
                     f'{escape(text_value(r.get("company_name")))}</h3>'
                     f'<p class="stock-status">{escape(status)}</p><table class="metrics">{rows}</table>'
                     f'<p class="stock-reason">{escape(reason)}</p>'
                     f'<div class="sub">{escape(display_sector(text_value(r.get("sector"))))} · '
                     f'{escape(display_period(text_value(r.get("financial_period_type"))))} · '
                     f'{escape(display_method(text_value(r.get("valuation_method"))))}</div>')
        if not entry:
            parts.append(f'<p class="sub">旧研究评级：{escape(text_value(r.get("rating")))}；'
                         f'{short_reason(r.get("reason"), limit=240)}</p>')
        parts.append('</div>')
    if len(df) > len(sample):
        parts.append(f'<p class="sub">本节显示 {len(sample)} / {len(df)} 只，完整数据见扫描 CSV。</p>')
    return ''.join(parts)


def html_table(df: pd.DataFrame, title: str, limit: Optional[int] = None, show_pool: bool = False, currency_symbol: str = "$") -> str:
    return stock_cards(df, title, limit, currency_symbol)


def compact_table(df: pd.DataFrame, title: str, limit: Optional[int] = None, currency_symbol: str = "$") -> str:
    return stock_cards(df, title, limit, currency_symbol)


def count_rating(df: pd.DataFrame, rating: str) -> int:
    if df.empty or "rating" not in df.columns:
        return 0
    return int((df["rating"] == rating).sum())


def rating_distribution_html(df: pd.DataFrame) -> str:
    ratings = ["S", "A", "B", "C_THIN", "PASS", "D_TRAP", "NO_DATA", "SKIP", "ERROR"]
    rows = []
    total = len(df)

    for rating in ratings:
        count = count_rating(df, rating)
        rows.append(
            f"""
            <tr>
                <td>{rating_badge(rating)}</td>
                <td class="num">{count}</td>
                <td class="num">{pct(count / total if total else None)}</td>
            </tr>
            """
        )

    return f"""
    <h2>扫描诊断：评级分布</h2>
    <table>
        <thead>
            <tr>
                <th>评级</th>
                <th>数量</th>
                <th>占比</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """


def sector_distribution_html(df: pd.DataFrame) -> str:
    if df.empty or "sector" not in df.columns:
        return ""

    rows = []
    total = len(df)
    grouped = (
        df.assign(sector=df["sector"].fillna("").replace("", "Unknown"))
        .groupby("sector", dropna=False)
        .agg(
            count=("ticker", "count"),
            sab=("rating", lambda s: int(s.isin(["S", "A", "B"]).sum())),
            c_thin=("rating", lambda s: int((s == "C_THIN").sum())),
            pass_count=("rating", lambda s: int((s == "PASS").sum())),
        )
        .sort_values("count", ascending=False)
        .head(12)
    )

    for sector, row in grouped.iterrows():
        rows.append(
            f"""
            <tr>
                <td>{escape(display_sector(str(sector)))}</td>
                <td class="num">{int(row["count"])}</td>
                <td class="num">{pct(row["count"] / total if total else None)}</td>
                <td class="num">{int(row["sab"])}</td>
                <td class="num">{int(row["c_thin"])}</td>
                <td class="num">{int(row["pass_count"])}</td>
            </tr>
            """
        )

    return f"""
    <h2>扫描诊断：市场行业分布</h2>
    <table>
        <thead>
            <tr>
                <th>行业</th>
                <th>数量</th>
                <th>占比</th>
                <th>S/A/B</th>
                <th>C_THIN</th>
                <th>PASS</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """


def historical_price_status_html(df: pd.DataFrame) -> str:
    if df.empty or "historical_price_status" not in df.columns:
        return ""

    status = df["historical_price_status"].fillna("").replace("", "UNKNOWN")
    total = len(df)
    rows = []
    for label, count in status.value_counts(dropna=False).items():
        rows.append(
            f"""
            <tr>
                <td>{escape(str(label))}</td>
                <td class="num">{int(count)}</td>
                <td class="num">{pct(count / total if total else None)}</td>
            </tr>
            """
        )

    return f"""
    <h2>历史回放诊断：历史价格覆盖</h2>
    <table>
        <thead>
            <tr>
                <th>状态</th>
                <th>数量</th>
                <th>占比</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """



def valuation_detail_html(df: pd.DataFrame, title: str = "估值拆解样本（最多5只）", currency_symbol: str = "$") -> str:
    if df.empty or "valuation_candidates" not in df.columns:
        return ""
    parts = [f'<h2>{escape(title)}</h2><p class="sub">经营估值与资产折价参考分列；'
             '资产账面折价不代表可实现的清算价值。以下保留原扫描模型的计算结果。</p>']
    for _, r in sort_for_report(df.copy()).head(5).iterrows():
        parts.append(f'<div class="stock"><h3>{escape(text_value(r.get("ticker")))}</h3>'
                     f'<p>采用：{escape(display_method(text_value(r.get("valuation_method"))))}</p>'
                     f'<p class="stock-reason">{escape(display_candidates(text_value(r.get("valuation_candidates")), currency_symbol))}</p></div>')
    return ''.join(parts)


def holdings_risk_html(df: pd.DataFrame, currency_symbol: str = "$") -> str:
    if df.empty or "rating" not in df.columns:
        return ""
    risk = df[df["rating"].isin(["C_THIN", "PASS", "D_TRAP", "NO_DATA", "SKIP", "ERROR"])].copy()
    if risk.empty:
        return ""
    return compact_table(sort_for_report(risk), "持仓风险复核：偏薄、无边际或数据不足", limit=None, currency_symbol=currency_symbol)

def diagnostic_sample_html(df: pd.DataFrame, title: str = "扫描诊断：未进入 S/A/B 的样本 Top 30", currency_symbol: str = "$") -> str:
    if df.empty or "rating" not in df.columns:
        return ""

    watch = df[df["rating"].isin(["C_THIN", "PASS", "D_TRAP", "NO_DATA", "SKIP", "ERROR"])].copy()
    if watch.empty:
        return ""

    watch = diversified_sample(sort_for_report(watch), limit=5, per_sector=1)
    return compact_table(watch, title, limit=5, currency_symbol=currency_symbol)


def near_miss_html(df: pd.DataFrame, title: str, limit: int = 30, currency_symbol: str = "$") -> str:
    if df.empty or "rating" not in df.columns or "margin_of_safety" not in df.columns:
        return ""

    mos = pd.to_numeric(df["margin_of_safety"], errors="coerce")
    score = pd.to_numeric(df.get("final_score", pd.Series(index=df.index)), errors="coerce")
    near = df[
        (df["rating"] == "C_THIN")
        & (
            (mos >= 0.15)
            | ((mos >= 0.10) & (score >= 50))
        )
    ].copy()

    if near.empty:
        return ""

    near = sort_by_mos_score(near)
    return compact_table(near, title, limit=limit, currency_symbol=currency_symbol)


def opportunity_html(df, currency_symbol="$", limit=20):
    from opportunity import STATUS_ZH, EVENT_ZH, ENTRY_STATES

    if "entry_status" not in df:
        return '<h2>严格入场观察区</h2><div class="empty">旧结果没有压力情景和数据日期，请重新完整扫描。</div>'
    parts = ['<h2>严格入场观察区</h2><div class="note">折价率=(价值−价格)/价值；潜在上涨空间=(价值−价格)/价格。'
             '触发上限同时受保守折价、压力折价和Owner FCF收益率约束。压力估值是敏感性情景，不是价格底部。'
             '“入场复核”表示价格与质量条件达标，仍需核对最新公告及价值实现条件。</div>']
    if "scan_status" in df and df["scan_status"].eq("PARTIAL_SOURCE_FAILURE").any():
        parts.append('<p><b>本次扫描因数据源连续限流提前停止，下方只包含已尝试的股票；已保留此前公开状态。</b></p>')
    counts = df["entry_status"].value_counts()
    parts.append('<p>'+" · ".join(f'{escape(STATUS_ZH.get(k, k))}：{v}' for k, v in counts.items())+'</p>')
    groups = [("价格已达标：优先复核", ENTRY_STATES),
              ("质量通过：等待更好的价格", {"NEAR_ENTRY", "WAIT_PRICE"}),
              ("已退出或估值明显下修", {"REVIEW_REQUIRED"})]
    for title, statuses in groups:
        subset = df[df["entry_status"].isin(statuses)].copy()
        if title == "已退出或估值明显下修" and "entry_event" in df:
            subset = df[df["entry_status"].isin(statuses) | df["entry_event"].eq("EXITED")].copy()
        if subset.empty:
            if statuses == ENTRY_STATES:
                parts.append('<p><b>目前没有通过严格门槛的入场候选，可以继续等待。</b></p>')
            continue
        subset = subset.sort_values("distance_to_entry", ascending=False, na_position="last")
        parts.append(stock_cards(subset, title, limit, currency_symbol, entry=True))
    blocked = df[df["entry_status"].isin({"RISK_BLOCKED", "DATA_REQUIRED"})]
    if not blocked.empty:
        columns = [("entry_data_issues", "需补齐的证据"), ("entry_risk_issues", "已识别的风险")]
        if not all(key in blocked for key, _ in columns):
            columns = [("entry_reason", "主要未通过原因（旧扫描合并记录）")]
        for key, label in columns:
            reasons = blocked[key].fillna("").str.split("；").explode()
            reasons = reasons[reasons.ne("")].value_counts().head(8)
            if not reasons.empty:
                parts.append(f'<h3>{escape(label)}</h3><p>'+"；".join(f'{escape(str(k))}：{v}只' for k, v in reasons.items())+'</p>')
    return ''.join(parts)


def generate_report(
    df: pd.DataFrame,
    mode: str,
    output_path: Optional[str] = None,
    top_mos_count: int = 50,
    trap_count: int = 30,
    thin_count: int = 30,
    market: str = "us",
    model_version: str | None = None,
) -> str:
    df = normalize_numeric(df)

    title_map = {
        "full_after_close": "盘后安全边际报告",
        "premarket_scan": "盘前安全边际扫描",
        "morning_email": "开盘前安全边际报告",
        "noon_update": "午盘安全边际变化",
        "afternoon_update": "下午安全边际变化",
        "manual": "手动安全边际扫描",
        "historical_replay": "历史价格回放压力测试",
    }

    title = title_map.get(mode, "安全边际报告")
    market_label = {"us": "美股", "hk": "港股"}.get(str(market).lower(), str(market).upper())
    currency_symbol = "HK$" if str(market).lower() == "hk" else "$"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    backtest_date = ""
    if "backtest_date" in df.columns:
        values = df["backtest_date"].dropna().astype(str)
        backtest_date = values.iloc[0] if not values.empty else ""

    model_version = model_version or ""
    if not model_version and "model_version" in df.columns:
        mv = df["model_version"].dropna().astype(str)
        model_version = mv.iloc[0] if not mv.empty else ""

    holdings_df = holdings_all(df)

    if "is_holding" in df.columns:
        market_df = df[~df["is_holding"].map(is_true)].copy()
    else:
        market_df = df.copy()

    operating_market_df, financial_market_df = split_financials(market_df)
    statuses = operating_market_df.get("entry_status", pd.Series(dtype=str))
    strict_count = int(statuses.isin({"ENTRY_REVIEW", "DEEP_VALUE_REVIEW"}).sum())
    wait_count = int(statuses.isin({"WAIT_PRICE", "NEAR_ENTRY"}).sum())
    data_count = int(statuses.eq("DATA_REQUIRED").sum())
    risk_count = int(statuses.eq("RISK_BLOCKED").sum())
    special_count = int(statuses.eq("SPECIAL_REVIEW").sum())
    coverage_text = f"本报告包含 {len(df)} 条结果；金融股另列。"
    if "scan_time" in df and not df["scan_time"].dropna().empty:
        coverage_text += " 扫描时间：" + str(df["scan_time"].dropna().iloc[0])
    if "scan_attempted_count" in df and "scan_expected_count" in df and not df.empty:
        coverage_text += f"；已尝试 {df.iloc[0]['scan_attempted_count']} / 计划 {df.iloc[0]['scan_expected_count']}。"
    if "report_context" in df and not df["report_context"].dropna().empty:
        coverage_text += " " + str(df["report_context"].dropna().iloc[0])
    entry_html = opportunity_html(operating_market_df, currency_symbol, top_mos_count) if mode != "historical_replay" else ""

    market_high = high_margin_candidates(operating_market_df)
    financial_high = high_margin_candidates(financial_market_df)
    rating_diag_html = rating_distribution_html(df)
    sector_diag_html = sector_distribution_html(market_df)
    historical_status_html = historical_price_status_html(df) if mode == "historical_replay" else ""
    diagnostic_html = diagnostic_sample_html(
        operating_market_df,
        "扫描诊断样本（最多5只，完整结果见 CSV）",
        currency_symbol=currency_symbol,
    )
    valuation_detail = valuation_detail_html(market_high if not market_high.empty else operating_market_df, currency_symbol=currency_symbol)
    holdings_risk = holdings_risk_html(holdings_df, currency_symbol=currency_symbol)

    holdings_html = html_table(
        holdings_df,
        "我的持仓池：全部持仓安全边际",
        limit=None,
        currency_symbol=currency_symbol,
    )

    market_html = html_table(
        market_high,
        f"旧研究评级 S/A/B：须以严格入场状态为准 Top {top_mos_count}",
        limit=top_mos_count,
        currency_symbol=currency_symbol,
    )

    financial_html = compact_table(
        sort_for_report(financial_high),
        "金融股观察池：S/A/B 候选",
        limit=20,
        currency_symbol=currency_symbol,
    )

    thicker_html = ""
    if "price_change_since_scan" in market_high.columns:
        thicker = market_high[
            pd.to_numeric(market_high["price_change_since_scan"], errors="coerce") < -0.01
        ].copy()

        if not thicker.empty:
            thicker = thicker.sort_values(["price_change_since_scan", "margin_of_safety"], ascending=[True, False])
            thicker_html = html_table(thicker, "盘中下跌后安全边际继续变厚的市场候选", limit=20, currency_symbol=currency_symbol)

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        margin: 0;
        padding: 0;
        background: #f3f4f6;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, "Microsoft YaHei", sans-serif;
        color: #111827;
    }}
    .wrap {{
        max-width: 920px;
        margin: 0 auto;
        padding: 22px;
    }}
    .card {{
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 14px;
        padding: 20px;
        margin-bottom: 18px;
    }}
    h1 {{
        margin: 0 0 8px 0;
        font-size: 24px;
        color: #111827;
    }}
    h2 {{
        margin: 26px 0 12px 0;
        font-size: 18px;
        color: #111827;
    }}
    .meta {{
        color: #6b7280;
        font-size: 13px;
        margin-bottom: 12px;
    }}
    .note {{
        background: #f9fafb;
        border-left: 4px solid #2563eb;
        padding: 10px 12px;
        font-size: 13px;
        color: #374151;
        margin-top: 10px;
    }}
    .warning {{
        margin-top: 14px;
        padding: 12px 14px;
        border: 1px solid #fca5a5;
        background: #fff1f2;
        color: #991b1b;
        border-radius: 8px;
        line-height: 1.8;
        font-weight: 700;
    }}
    .summary {{
        display: block;
        width: 100%;
        margin-top: 10px;
    }}
    .box {{
        display: inline-block;
        vertical-align: top;
        box-sizing: border-box;
        margin: 4px 0;
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 12px;
        width: 32%;
    }}
    .box .label {{
        font-size: 12px;
        color: #6b7280;
    }}
    .box .value {{
        font-size: 22px;
        font-weight: 800;
        margin-top: 4px;
        color: #111827;
    }}
    table {{
        border-collapse: collapse;
        width: 100%;
        background: #fff;
        font-size: 13px;
    }}
    th {{
        text-align: left;
        background: #f3f4f6;
        color: #374151;
        padding: 9px 8px;
        border: 1px solid #e5e7eb;
        font-weight: 700;
        white-space: normal;
    }}
    td {{
        padding: 8px;
        border: 1px solid #e5e7eb;
        vertical-align: top;
    }}
    tr:nth-child(even) td {{
        background: #fafafa;
    }}
    .ticker {{
        font-weight: 800;
        color: #111827;
        white-space: nowrap;
    }}
    .company {{
        min-width: 180px;
    }}
    .num {{
        text-align: right;
        white-space: nowrap;
        font-variant-numeric: tabular-nums;
    }}
    .reason {{
        min-width: 180px;
        max-width: 320px;
        line-height: 1.45;
        white-space: normal;
        word-break: normal;
        overflow-wrap: break-word;
    }}
    .compact th,
    .compact td {{
        padding: 8px 7px;
    }}
    .compact .reason {{
        min-width: 220px;
    }}
    .sub {{
        color: #6b7280;
        font-size: 12px;
    }}
    .empty {{
        background: #f9fafb;
        border: 1px dashed #d1d5db;
        padding: 14px;
        color: #6b7280;
        border-radius: 10px;
        font-size: 14px;
    }}
    .legend {{
        font-size: 13px;
        line-height: 1.8;
        color: #374151;
    }}

    * {{ box-sizing: border-box; }}
    .stock {{ border: 1px solid #dbe3ec; border-radius: 8px; padding: 14px; margin: 12px 0; break-inside: avoid; overflow-wrap: anywhere; }}
    .stock h3 {{ margin: 0 0 8px; font-size: 16px; }}
    .stock-status {{ color: #1d4ed8; font-weight: 700; margin: 6px 0 12px; }}
    .stock-reason {{ font-size: 13px; line-height: 1.7; margin: 12px 0; }}
    .metrics {{ table-layout: fixed; }}
    .metrics td {{ width: 50%; line-height: 1.6; }}
    .metrics b {{ font-variant-numeric: tabular-nums; }}
    @media (max-width: 600px) {{
        .wrap {{ padding: 8px; }} .card {{ padding: 12px; }}
        .box {{ width: 48%; padding: 10px; }}
        h1 {{ font-size: 21px; }} .stock {{ padding: 10px; }}
        th, td {{ overflow-wrap: anywhere; }}
    }}
    @media print {{
        @page {{ size: A4; margin: 12mm; }}
        body {{ background: white; }} .wrap {{ max-width: none; padding: 0; }}
        .card {{ padding: 12px; border-radius: 0; }}
        h2, h3 {{ break-after: avoid; }}
    }}
</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>【MOS Radar {escape(market_label)}】{escape(title)}</h1>
        <div class="meta">生成时间：{escape(ts)} | 市场：{escape(market_label)} | 模式：{escape(mode)} | 模型：{escape(model_version or "N/A")}</div>

        <div class="summary">
            <div class="box"><div class="label">经营型扫描股票</div><div class="value">{len(operating_market_df)}</div></div>
            <div class="box"><div class="label">严格入场复核</div><div class="value">{strict_count}</div></div>
            <div class="box"><div class="label">仅等待价格</div><div class="value">{wait_count}</div></div>
            <div class="box"><div class="label">数据待补齐</div><div class="value">{data_count}</div></div>
            <div class="box"><div class="label">风险未通过</div><div class="value">{risk_count}</div></div>
            <div class="box"><div class="label">行业专门复核</div><div class="value">{special_count}</div></div>
        </div>
        <p class="meta">{escape(coverage_text)}</p>
        <div class="note">
            持仓池会显示所有持仓的安全边际；非金融经营型公司和金融股分开显示，因为金融股使用市净率/净资产收益率口径，不能和普通自由现金流公司混排。
            旧版20%/35%/50%观察价按上涨空间倒推，不等于折价率；严格入场条件见下方入场观察区。S/A/B 是兼容旧版的研究评级。
            {f'<br><b>历史回放日期：</b>{escape(backtest_date)}。本模式使用当前 {escape(model_version or "模型")} 保守估值和历史价格重算安全边际，属于历史价格压力测试，不是严格 point-in-time 财报回测；当时未上市或无历史价格的股票会标记为 SKIP，财务与历史价格严重错配会标记为 DATA_MISMATCH。' if mode == 'historical_replay' and backtest_date else ''}
        </div>
        {f'<div class="warning">⚠️ 警告：本回放测试存在未来函数（Lookahead Bias）。系统使用当前的财务数据匹配历史股价。回放算出的高安全边际可能是由于公司近年利润大幅增长导致，不代表历史真实的投资机会。本结果只能用于观察价格压力，不可视为严格回测或买入依据。</div>' if mode == 'historical_replay' and backtest_date else ''}
    </div>

    {f'<div class="card">{entry_html}</div>' if entry_html else ''}

    {f'<div class="card">{holdings_html}</div>' if not holdings_df.empty else ''}

    {f'<div class="card">{holdings_risk}</div>' if holdings_risk else ''}

    <div class="card">
        {market_html}
    </div>

    {f'<div class="card">{valuation_detail}</div>' if valuation_detail else ''}

    <div class="card">
        {financial_html}
    </div>

    <div class="card">
        {rating_diag_html}
    </div>

    {f'<div class="card">{sector_diag_html}</div>' if sector_diag_html else ''}

    {f'<div class="card">{historical_status_html}</div>' if historical_status_html else ''}

    {f'<div class="card">{diagnostic_html}</div>' if diagnostic_html else ''}

    {f'<div class="card">{thicker_html}</div>' if thicker_html else ''}

    <div class="card">
        <h2>评级说明</h2>
        <div class="legend">
            <b>S</b>：安全边际很厚，优先人工研究。<br>
            <b>A</b>：安全边际较厚，强候选。<br>
            <b>B</b>：有一定安全边际，观察候选。<br>
            <b>C_THIN</b>：折价不足或质量门槛未通过，不代表价格接近入场。<br>
            <b>PASS</b>：当前价格高于保守价值。<br>
            <b>D_TRAP</b>：疑似价值陷阱，必须人工排雷。<br>
            <b>NO_DATA</b>：数据不足，不能判断。<br>
            <b>财报口径</b>：最近四季滚动表示最近四个季度合计；年报口径表示季度数据不足，退回最新年报。
        </div>
    </div>
</div>
</body>
</html>
"""

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html, encoding="utf-8")

    return html
