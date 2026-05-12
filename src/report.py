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
        return f"{float(x):.2f}"
    except Exception:
        return "N/A"


def is_true(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y"}
    return bool(v)


def row_line(row, i: int) -> str:
    holding_tag = "【持仓池】" if is_true(row.get("is_holding", False)) else ""

    return (
        f"{i}. {row.get('ticker')} {holding_tag} {row.get('company_name', '')}\n"
        f"   - 行业：{row.get('sector', 'N/A')}\n"
        f"   - 评级：{row.get('rating', 'N/A')} | 最终分：{num(row.get('final_score'))}\n"
        f"   - 当前价：{money(row.get('price'))} | 保守价值/股：{money(row.get('intrinsic_value_per_share'))}\n"
        f"   - 安全边际：{pct(row.get('margin_of_safety'))} | FCF Yield：{pct(row.get('fcf_yield'))}\n"
        f"   - 债务/EBITDA：{num(row.get('debt_to_ebitda'))} | ROE：{pct(row.get('roe'))}\n"
        f"   - 理由：{row.get('reason', '')}\n"
    )


def table_block(df: pd.DataFrame, title: str, limit: int | None = None) -> str:
    if df.empty:
        return f"\n## {title}\n\n暂无符合条件的公司。\n"

    if limit is not None:
        df = df.head(limit)

    lines = [f"\n## {title}\n"]
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        lines.append(row_line(row, i))

    return "\n".join(lines)


def normalize_numeric(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for c in [
        "margin_of_safety",
        "final_score",
        "fcf_yield",
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
    }

    if "rating" in df.columns:
        df["_rating_order"] = df["rating"].map(rating_order).fillna(9)
    else:
        df["_rating_order"] = 9

    sort_cols = []
    ascending = []

    if "_rating_order" in df.columns:
        sort_cols.append("_rating_order")
        ascending.append(True)

    if "margin_of_safety" in df.columns:
        sort_cols.append("margin_of_safety")
        ascending.append(False)

    if "final_score" in df.columns:
        sort_cols.append("final_score")
        ascending.append(False)

    if sort_cols:
        df = df.sort_values(sort_cols, ascending=ascending)

    return df


def high_margin_candidates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "rating" not in df.columns:
        return pd.DataFrame()

    out = df[df["rating"].isin(["S", "A", "B"])].copy()
    return sort_for_report(out)


def all_holdings(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "is_holding" not in df.columns:
        return pd.DataFrame()

    holding_mask = df["is_holding"].map(is_true)
    out = df[holding_mask].copy()
    return sort_for_report(out)


def generate_report(
    df: pd.DataFrame,
    mode: str,
    output_path: Optional[str] = None,
    top_mos_count: int = 50,
    trap_count: int = 30,
    thin_count: int = 30,
) -> str:
    df = normalize_numeric(df)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    title_map = {
        "full_after_close": "盘后安全边际报告",
        "morning_email": "开盘前安全边际报告",
        "noon_update": "午盘安全边际变化",
        "afternoon_update": "下午安全边际变化",
        "manual": "手动安全边际扫描",
    }

    title = title_map.get(mode, "安全边际报告")

    lines = [
        f"# 【MOS Radar】{title}",
        "",
        f"生成时间：{ts}",
        "",
        "说明：",
        "- 我的持仓池：显示所有持仓公司的安全边际，包括 S / A / B / C_THIN / PASS / D_TRAP / NO_DATA。",
        "- 全市场股票池：只显示安全边际较厚的 S / A / B。",
        "- 本报告不是买卖建议，最终买卖由你人工判断。",
    ]

    holdings_all = all_holdings(df)

    if "is_holding" in df.columns:
        market_df = df[~df["is_holding"].map(is_true)].copy()
    else:
        market_df = df.copy()

    market_high = high_margin_candidates(market_df)

    lines.append(
        table_block(
            holdings_all,
            "我的持仓池：全部持仓安全边际",
            limit=None,
        )
    )

    lines.append(
        table_block(
            market_high,
            f"全市场股票池：安全边际较厚候选 Top {top_mos_count}",
            limit=top_mos_count,
        )
    )

    if "price_change_since_scan" in market_high.columns:
        thicker = market_high[
            pd.to_numeric(market_high["price_change_since_scan"], errors="coerce") < -0.01
        ].copy()

        if not thicker.empty:
            thicker = thicker.sort_values(
                ["price_change_since_scan", "margin_of_safety"],
                ascending=[True, False],
            )

        lines.append(
            table_block(
                thicker,
                "盘中下跌后安全边际继续变厚的市场候选",
                limit=20,
            )
        )

    lines.extend(
        [
            "\n## 评级解释",
            "- S：安全边际很厚，且质量较高，优先人工研究。",
            "- A：安全边际较厚，强候选。",
            "- B：有一定安全边际，观察候选。",
            "- C_THIN：安全边际偏薄，不优先。",
            "- PASS：没有安全边际。",
            "- D_TRAP：疑似价值陷阱。",
            "- NO_DATA：数据不足，不能判断。",
            "",
            "## 使用建议",
            "1. 持仓池部分用于帮你每天检查自己的持仓安全边际有没有变厚或变薄。",
            "2. 如果持仓评级是 PASS / C_THIN，说明当前价格下安全边际不足，需要人工复核是否继续持有。",
            "3. 如果持仓评级是 D_TRAP，不代表一定要卖，但必须检查财报、现金流、债务和业务是否恶化。",
            "4. 半导体、能源、工业周期股，需要额外人工检查周期位置、库存、订单、毛利率和正常化利润。",
            "5. 完整 CSV 保存在 data/results/mos_latest.csv。",
        ]
    )

    report = "\n".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report, encoding="utf-8")

    return report
