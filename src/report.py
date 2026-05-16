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


def money(x) -> str:
    try:
        if pd.isna(x):
            return "N/A"
        x = float(x)
        if abs(x) >= 1e12:
            return f"${x/1e12:.2f}T"
        if abs(x) >= 1e9:
            return f"${x/1e9:.2f}B"
        if abs(x) >= 1e6:
            return f"${x/1e6:.2f}M"
        return f"${x:.2f}"
    except Exception:
        return "N/A"


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
    s = str(x or "")
    s = s.replace("\n", " ").strip()
    if len(s) > limit:
        return escape(s[:limit] + "...")
    return escape(s)


def html_table(df: pd.DataFrame, title: str, limit: Optional[int] = None, show_pool: bool = False) -> str:
    if df.empty:
        return f"""
        <h2>{escape(title)}</h2>
        <div class="empty">暂无符合条件的公司。</div>
        """

    if limit is not None:
        df = df.head(limit)

    rows = []

    for _, r in df.iterrows():
        ticker = escape(str(r.get("ticker", "")))
        name = escape(str(r.get("company_name", "") or ""))
        sector = escape(str(r.get("sector", "") or ""))
        rating = rating_badge(str(r.get("rating", "N/A")))

        rows.append(
            f"""
            <tr>
                <td class="ticker">{ticker}</td>
                <td>{name}<br><span class="sub">{sector}</span></td>
                <td>{rating}</td>
                <td class="num">{pct(r.get("margin_of_safety"))}</td>
                <td class="num">{num(r.get("final_score"))}</td>
                <td class="num">{money(r.get("price"))}</td>
                <td class="num">{money(r.get("intrinsic_value_per_share"))}</td>
                <td class="num">{money(r.get("buy_price_20mos"))}</td>
                <td class="num">{money(r.get("buy_price_35mos"))}</td>
                <td class="num">{money(r.get("buy_price_50mos"))}</td>
                <td class="num">{pct(r.get("fcf_yield"))}</td>
                <td class="num">{num(r.get("debt_to_ebitda"))}</td>
                <td>{short_reason(r.get("reason"))}</td>
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
                <th>安全边际</th>
                <th>分数</th>
                <th>现价</th>
                <th>保守价值/股</th>
                <th>20%观察价</th>
                <th>35%观察价</th>
                <th>50%强关注价</th>
                <th>FCF Yield</th>
                <th>债务/EBITDA</th>
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


def diagnostic_sample_html(df: pd.DataFrame) -> str:
    if df.empty or "rating" not in df.columns:
        return ""

    watch = df[df["rating"].isin(["C_THIN", "PASS", "D_TRAP", "NO_DATA", "SKIP", "ERROR"])].copy()
    if watch.empty:
        return ""

    watch = sort_for_report(watch)
    return html_table(watch, "扫描诊断：未进入 S/A/B 的样本 Top 30", limit=30)


def generate_report(
    df: pd.DataFrame,
    mode: str,
    output_path: Optional[str] = None,
    top_mos_count: int = 50,
    trap_count: int = 30,
    thin_count: int = 30,
) -> str:
    df = normalize_numeric(df)

    title_map = {
        "full_after_close": "盘后安全边际报告",
        "morning_email": "开盘前安全边际报告",
        "noon_update": "午盘安全边际变化",
        "afternoon_update": "下午安全边际变化",
        "manual": "手动安全边际扫描",
    }

    title = title_map.get(mode, "安全边际报告")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    holdings_df = holdings_all(df)

    if "is_holding" in df.columns:
        market_df = df[~df["is_holding"].map(is_true)].copy()
    else:
        market_df = df.copy()

    market_high = high_margin_candidates(market_df)
    rating_diag_html = rating_distribution_html(df)
    diagnostic_html = diagnostic_sample_html(market_df)

    total_holdings = len(holdings_df)
    market_high_count = len(market_high)

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
    )

    market_html = html_table(
        market_high,
        f"全市场股票池：安全边际较厚候选 Top {top_mos_count}",
        limit=top_mos_count,
    )

    thicker_html = ""
    if "price_change_since_scan" in market_high.columns:
        thicker = market_high[
            pd.to_numeric(market_high["price_change_since_scan"], errors="coerce") < -0.01
        ].copy()

        if not thicker.empty:
            thicker = thicker.sort_values(["price_change_since_scan", "margin_of_safety"], ascending=[True, False])
            thicker_html = html_table(thicker, "盘中下跌后安全边际继续变厚的市场候选", limit=20)

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
    .num {{
        text-align: right;
        white-space: nowrap;
        font-variant-numeric: tabular-nums;
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
        <h1>【MOS Radar】{escape(title)}</h1>
        <div class="meta">生成时间：{escape(ts)} | 模式：{escape(mode)}</div>

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
                <div class="label">市场 S/A/B 候选</div>
                <div class="value">{market_high_count}</div>
            </div>
            <div class="box">
                <div class="label">市场 S/A/B 分布</div>
                <div class="value">S{s_count}/A{a_count}/B{b_count}</div>
            </div>
        </div>

        <div class="note">
            持仓池会显示所有持仓的安全边际；全市场部分只显示 S / A / B，避免安全边际低的股票干扰判断。
            20%/35%/50%观察价按保守价值倒推，仅用于提醒人工复核，不是自动买卖建议。
        </div>
    </div>

    <div class="card">
        {holdings_html}
    </div>

    <div class="card">
        {market_html}
    </div>

    <div class="card">
        {rating_diag_html}
    </div>

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
            <b>NO_DATA</b>：数据不足，不能判断。
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
