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
    return s


def display_period(value: str) -> str:
    s = str(value or "")
    return PERIOD_ZH.get(s, s or "N/A")


def translate_tokens(text: str) -> str:
    out = str(text or "")
    for key, value in {**METHOD_ZH, **MODEL_STATUS_ZH, **REASON_TOKEN_ZH}.items():
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


def html_table(df: pd.DataFrame, title: str, limit: Optional[int] = None, show_pool: bool = False, currency_symbol: str = "$") -> str:
    if df.empty:
        return f"""
        <h2>{escape(title)}</h2>
        <div class="empty">暂无符合条件的公司。</div>
        """

    if limit is not None:
        df = df.head(limit)

    show_backtest_return = "return_since_backtest" in df.columns
    rows = []

    for _, r in df.iterrows():
        ticker = escape(str(r.get("ticker", "")))
        name = escape(str(r.get("company_name", "") or ""))
        sector = escape(display_sector(str(r.get("sector", "") or "")))
        rating = rating_badge(str(r.get("rating", "N/A")))

        rows.append(
            f"""
            <tr>
                <td class="ticker">{ticker}</td>
                <td>{name}<br><span class="sub">{sector}</span></td>
                <td>{rating}</td>
                <td>{escape(display_period(str(r.get("financial_period_type", "") or "N/A")))}</td>
                <td>{escape(display_method(str(r.get("valuation_method", "") or "")))}</td>
                <td class="num">{pct(r.get("margin_of_safety"))}</td>
                {f'<td class="num">{pct(r.get("return_since_backtest"))}</td>' if show_backtest_return else ''}
                <td class="num">{num(r.get("final_score"))}</td>
                <td class="num">{money(r.get("price"), currency_symbol)}</td>
                <td class="num">{money(r.get("intrinsic_value_per_share"), currency_symbol)}</td>
                <td class="num">{money(r.get("buy_price_20mos"), currency_symbol)}</td>
                <td class="num">{money(r.get("buy_price_35mos"), currency_symbol)}</td>
                <td class="num">{money(r.get("buy_price_50mos"), currency_symbol)}</td>
                <td class="num">{pct(r.get("fcf_yield"))}</td>
                <td class="num">{num(r.get("debt_to_ebitda"))}</td>
                <td class="reason">{short_reason(r.get("reason"), limit=110)}</td>
            </tr>
            """
        )

    return f"""
    <h2>{escape(title)}</h2>
    <table>
        <thead>
            <tr>
                <th>代码</th>
                <th>公司 / 行业</th>
                <th>评级</th>
                <th>财报口径</th>
                <th>估值法</th>
                <th>安全边际</th>
                {('<th>回放日至今</th>' if show_backtest_return else '')}
                <th>分数</th>
                <th>现价</th>
                <th>保守价值/股</th>
                <th>20%观察价</th>
                <th>35%观察价</th>
                <th>50%强关注价</th>
                <th>自由现金流收益率</th>
                <th>债务/经营利润</th>
                <th>理由</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """


def compact_table(df: pd.DataFrame, title: str, limit: Optional[int] = None, currency_symbol: str = "$") -> str:
    if df.empty:
        return f"""
        <h2>{escape(title)}</h2>
        <div class="empty">暂无符合条件的公司。</div>
        """

    if limit is not None:
        df = df.head(limit)

    show_backtest_return = "return_since_backtest" in df.columns
    rows = []

    for _, r in df.iterrows():
        ticker = escape(str(r.get("ticker", "")))
        name = escape(str(r.get("company_name", "") or ""))
        sector = escape(display_sector(str(r.get("sector", "") or "")))
        rating = rating_badge(str(r.get("rating", "N/A")))

        rows.append(
            f"""
            <tr>
                <td class="ticker">{ticker}</td>
                <td class="company">{name}<br><span class="sub">{sector}</span></td>
                <td>{rating}</td>
                <td>{escape(display_period(str(r.get("financial_period_type", "") or "N/A")))}</td>
                <td>{escape(display_method(str(r.get("valuation_method", "") or "")))}</td>
                <td class="num">{pct(r.get("margin_of_safety"))}</td>
                {f'<td class="num">{pct(r.get("return_since_backtest"))}</td>' if show_backtest_return else ''}
                <td class="num">{num(r.get("final_score"))}</td>
                <td class="num">{money(r.get("price"), currency_symbol)}</td>
                <td class="num">{money(r.get("intrinsic_value_per_share"), currency_symbol)}</td>
                <td class="num">{pct(r.get("fcf_yield"))}</td>
                <td class="num">{num(r.get("debt_to_ebitda"))}</td>
                <td class="reason">{short_reason(r.get("reason"), limit=140)}</td>
            </tr>
            """
        )

    return f"""
    <h2>{escape(title)}</h2>
    <table class="compact">
        <thead>
            <tr>
                <th>代码</th>
                <th>公司 / 行业</th>
                <th>评级</th>
                <th>财报口径</th>
                <th>估值法</th>
                <th>安全边际</th>
                {('<th>回放日至今</th>' if show_backtest_return else '')}
                <th>分数</th>
                <th>现价</th>
                <th>保守价值/股</th>
                <th>自由现金流收益率</th>
                <th>债务/经营利润</th>
                <th>理由</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """


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



def valuation_detail_html(df: pd.DataFrame, title: str = "候选估值拆解：最低估值法与候选值", currency_symbol: str = "$") -> str:
    if df.empty or "valuation_candidates" not in df.columns:
        return ""
    sample = sort_for_report(df.copy()).head(30)
    rows = []
    for _, r in sample.iterrows():
        rows.append(
            f"""
            <tr>
                <td class="ticker">{escape(str(r.get('ticker', '')))}</td>
                <td>{escape(str(r.get('company_name', '') or ''))}</td>
                <td>{rating_badge(str(r.get('rating', 'N/A')))}</td>
                <td>{escape(display_period(str(r.get('financial_period_type', '') or 'N/A')))}</td>
                <td>{escape(display_model_status(str(r.get('industry_model_status', '') or '')))}</td>
                <td>{escape(display_method(str(r.get('valuation_method', '') or '')))}</td>
                <td class="reason">{escape(display_candidates(str(r.get('valuation_candidates', '') or ''), currency_symbol))}</td>
            </tr>
            """
        )
    return f"""
    <h2>{escape(title)}</h2>
    <table class="compact">
        <thead>
            <tr>
                <th>代码</th>
                <th>公司</th>
                <th>评级</th>
                <th>财报口径</th>
                <th>行业模型状态</th>
                <th>最低估值法</th>
                <th>估值候选（总值）</th>
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    """


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

    watch = diversified_sample(sort_for_report(watch), limit=30, per_sector=5)
    return compact_table(watch, title, limit=30, currency_symbol=currency_symbol)


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

    market_high = high_margin_candidates(operating_market_df)
    financial_high = high_margin_candidates(financial_market_df)
    rating_diag_html = rating_distribution_html(df)
    sector_diag_html = sector_distribution_html(market_df)
    historical_status_html = historical_price_status_html(df) if mode == "historical_replay" else ""
    near_miss = near_miss_html(operating_market_df, "非金融接近候选：安全边际偏薄但值得复核 Top 30", limit=30, currency_symbol=currency_symbol)
    financial_near_miss = near_miss_html(financial_market_df, "金融股观察池：市净率/净资产收益率接近候选 Top 20", limit=20, currency_symbol=currency_symbol)
    diagnostic_html = diagnostic_sample_html(
        operating_market_df,
        "扫描诊断：非金融未进入 S/A/B 的样本 Top 30",
        currency_symbol=currency_symbol,
    )
    valuation_detail = valuation_detail_html(market_high if not market_high.empty else operating_market_df, currency_symbol=currency_symbol)
    holdings_risk = holdings_risk_html(holdings_df, currency_symbol=currency_symbol)

    total_holdings = len(holdings_df)
    market_high_count = len(market_high)
    financial_high_count = len(financial_high)
    near_miss_count = 0
    if not operating_market_df.empty and "rating" in operating_market_df.columns and "margin_of_safety" in operating_market_df.columns:
        mos = pd.to_numeric(operating_market_df["margin_of_safety"], errors="coerce")
        score = pd.to_numeric(operating_market_df.get("final_score", pd.Series(index=operating_market_df.index)), errors="coerce")
        near_miss_count = int(((operating_market_df["rating"] == "C_THIN") & ((mos >= 0.15) | ((mos >= 0.10) & (score >= 50)))).sum())

    s_count = count_rating(market_high, "S")
    a_count = count_rating(market_high, "A")
    b_count = count_rating(market_high, "B")

    holdings_s = count_rating(holdings_df, "S")
    holdings_a = count_rating(holdings_df, "A")
    holdings_b = count_rating(holdings_df, "B")
    holdings_pass = count_rating(holdings_df, "PASS") + count_rating(holdings_df, "C_THIN") + count_rating(holdings_df, "D_TRAP")

    holdings_html = html_table(
        holdings_df,
        "我的持仓池：全部持仓安全边际",
        limit=None,
        currency_symbol=currency_symbol,
    )

    market_html = html_table(
        market_high,
        f"非金融经营型股票池：安全边际较厚候选 Top {top_mos_count}",
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
        max-width: 1180px;
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
    .summary {{
        display: table;
        width: 100%;
        border-spacing: 10px;
        margin: 10px -10px 0 -10px;
    }}
    .box {{
        display: table-cell;
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 12px;
        width: 20%;
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
        white-space: nowrap;
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
</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>【MOS Radar {escape(market_label)}】{escape(title)}</h1>
        <div class="meta">生成时间：{escape(ts)} | 市场：{escape(market_label)} | 模式：{escape(mode)} | 模型：{escape(model_version or "N/A")}</div>

        <div class="summary">
            <div class="box">
                <div class="label">持仓池股票</div>
                <div class="value">{total_holdings}</div>
            </div>
            <div class="box">
                <div class="label">持仓 S/A/B</div>
                <div class="value">{holdings_s + holdings_a + holdings_b}</div>
            </div>
            <div class="box">
                <div class="label">持仓偏薄/无边际</div>
                <div class="value">{holdings_pass}</div>
            </div>
            <div class="box">
                <div class="label">非金融 S/A/B</div>
                <div class="value">{market_high_count}</div>
            </div>
            <div class="box">
                <div class="label">金融 S/A/B</div>
                <div class="value">{financial_high_count}</div>
            </div>
            <div class="box">
                <div class="label">非金融接近候选</div>
                <div class="value">{near_miss_count}</div>
            </div>
        </div>

        <div class="note">
            持仓池会显示所有持仓的安全边际；非金融经营型公司和金融股分开显示，因为金融股使用市净率/净资产收益率口径，不能和普通自由现金流公司混排。
            20%/35%/50%观察价按保守价值倒推，仅用于提醒人工复核，不是自动买卖建议。S/A/B 只代表值得研究，不代表买入。
            {f'<br><b>历史回放日期：</b>{escape(backtest_date)}。本模式使用当前 {escape(model_version or "模型")} 保守估值和历史价格重算安全边际，属于历史价格压力测试，不是严格 point-in-time 财报回测；当时未上市或无历史价格的股票会标记为 SKIP。' if mode == 'historical_replay' and backtest_date else ''}
        </div>
    </div>

    <div class="card">
        {holdings_html}
    </div>

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

    {f'<div class="card">{near_miss}</div>' if near_miss else ''}

    {f'<div class="card">{financial_near_miss}</div>' if financial_near_miss else ''}

    {f'<div class="card">{diagnostic_html}</div>' if diagnostic_html else ''}

    {f'<div class="card">{thicker_html}</div>' if thicker_html else ''}

    <div class="card">
        <h2>评级说明</h2>
        <div class="legend">
            <b>S</b>：安全边际很厚，优先人工研究。<br>
            <b>A</b>：安全边际较厚，强候选。<br>
            <b>B</b>：有一定安全边际，观察候选。<br>
            <b>C_THIN</b>：安全边际偏薄。<br>
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
