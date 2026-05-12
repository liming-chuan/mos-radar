from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

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
        abs_x = abs(x)
        if abs_x >= 1e12:
            return f"${x/1e12:.2f}T"
        if abs_x >= 1e9:
            return f"${x/1e9:.2f}B"
        if abs_x >= 1e6:
            return f"${x/1e6:.2f}M"
        return f"${x:.2f}"
    except Exception:
        return "N/A"


def num(x) -> str:
    try:
        if pd.isna(x):
            return "N/A"
        return f"{float(x):.2f}"
    except Exception:
        return "N/A"


def _safe_sort(df: pd.DataFrame, columns, ascending=False) -> pd.DataFrame:
    existing = [c for c in columns if c in df.columns]
    if not existing:
        return df
    return df.sort_values(existing, ascending=ascending if isinstance(ascending, list) else [ascending] * len(existing))


def row_line(row, i: int) -> str:
    name = row.get("company_name") or ""
    ticker = row.get("ticker")
    sector = row.get("sector") or ""
    return (
        f"{i}. {ticker} {name}\n"
        f"   - 行业：{sector}\n"
        f"   - 评级：{row.get('rating', 'N/A')} | 最终分：{num(row.get('final_score'))}\n"
        f"   - 当前价：{money(row.get('price'))} | 保守价值/股：{money(row.get('intrinsic_value_per_share'))}\n"
        f"   - 安全边际：{pct(row.get('margin_of_safety'))} | FCF Yield：{pct(row.get('fcf_yield'))}\n"
        f"   - 债务/EBITDA：{num(row.get('debt_to_ebitda'))} | ROE：{pct(row.get('roe'))}\n"
        f"   - 理由：{row.get('reason', '')}\n"
    )


def table_block(df: pd.DataFrame, title: str, limit: int) -> str:
    if df.empty:
        return f"\n## {title}\n\n无。\n"
    lines = [f"\n## {title}\n"]
    for i, (_, row) in enumerate(df.head(limit).iterrows(), start=1):
        lines.append(row_line(row, i))
    return "\n".join(lines)


def generate_report(df: pd.DataFrame, mode: str, output_path: Optional[str] = None, top_mos_count: int = 50, trap_count: int = 30, thin_count: int = 30) -> str:
    df = df.copy()
    for c in ["margin_of_safety", "final_score", "fcf_yield", "price_change_since_scan", "mos_change_since_scan"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title_map = {
        "full_after_close": "盘后完整安全边际扫描",
        "morning_email": "开盘前安全边际报告",
        "noon_update": "午盘安全边际变化提醒",
        "afternoon_update": "下午安全边际变化提醒",
        "manual": "手动安全边际扫描",
    }
    title = title_map.get(mode, "安全边际报告")

    lines = [
        f"# 【MOS Radar】{title}",
        "",
        f"生成时间：{ts}",
        "",
        "说明：本报告只做安全边际候选筛选，不是买卖建议。最终买卖由你人工判断。",
        "",
        "评级：S=安全边际很厚且质量较高；A=强候选；B=观察；C=边际偏薄；D_TRAP=疑似价值陷阱；PASS=无安全边际。",
    ]

    candidate = df[df["rating"].isin(["S", "A", "B"])].copy()
    candidate = _safe_sort(candidate, ["rating", "margin_of_safety", "final_score"], ascending=[True, False, False])
    # Put S/A/B in desired order rather than alphabetical.
    order = {"S": 0, "A": 1, "B": 2}
    if not candidate.empty:
        candidate["_rank_order"] = candidate["rating"].map(order).fillna(9)
        candidate = candidate.sort_values(["_rank_order", "margin_of_safety", "final_score"], ascending=[True, False, False])

    lines.append(table_block(candidate, f"安全边际较厚候选 Top {top_mos_count}", top_mos_count))

    if "price_change_since_scan" in df.columns:
        thicker = df[df["rating"].isin(["S", "A", "B", "C_THIN"])].copy()
        thicker = thicker[pd.to_numeric(thicker.get("price_change_since_scan"), errors="coerce") < -0.01]
        thicker = thicker.sort_values(["price_change_since_scan", "margin_of_safety"], ascending=[True, False])
        lines.append(table_block(thicker, "盘中下跌导致安全边际变厚", 20))

        thinner = df[df["rating"].isin(["S", "A", "B", "C_THIN"])].copy()
        thinner = thinner[pd.to_numeric(thinner.get("price_change_since_scan"), errors="coerce") > 0.01]
        thinner = thinner.sort_values(["price_change_since_scan", "margin_of_safety"], ascending=[False, True])
        lines.append(table_block(thinner, "盘中上涨导致安全边际变薄", 20))

    thin = df[df["rating"].isin(["C_THIN", "PASS"])].copy()
    thin = _safe_sort(thin, ["margin_of_safety"], ascending=False)
    lines.append(table_block(thin, f"安全边际偏薄/没有安全边际 Top {thin_count}", thin_count))

    traps = df[df["rating"].isin(["D_TRAP", "NO_DATA", "ERROR"])].copy()
    traps = _safe_sort(traps, ["final_score"], ascending=False)
    lines.append(table_block(traps, f"价值陷阱/数据不足警告 Top {trap_count}", trap_count))

    lines.extend([
        "\n## 使用建议",
        "1. S/A 级只代表进入人工研究池，不代表直接买入。",
        "2. 半导体、能源、工业等周期股，必须再人工检查库存周期、订单、毛利率和正常化利润。",
        "3. D_TRAP 不代表不能投资，但代表便宜可能有原因，必须先排雷。",
        "4. 如果邮件里出现异常数据，优先检查数据源和最新财报，而不是直接按模型行动。",
    ])

    report = "\n".join(lines)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report, encoding="utf-8")
    return report
