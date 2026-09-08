"""Presentation only: short summaries of saved signals, never new signals."""
from html import escape
import pandas as pd


def text(value, fallback=''):
    return fallback if value is None or pd.isna(value) else str(value)


def local_time(value, market='hk', date_only=False):
    date = pd.to_datetime(value, utc=True, errors='coerce')
    if pd.isna(date):
        return '未提供'
    if date_only:
        return date.strftime('%Y-%m-%d')
    zone = 'Asia/Hong_Kong' if market == 'hk' else 'America/New_York'
    return date.tz_convert(zone).strftime('%Y-%m-%d %H:%M')


def amount(value, symbol='$'):
    from opportunity import number
    n = number(value)
    return '待核实' if n is None else f'{symbol}{n:.2f}'


def values(df, key):
    return df.get(key, pd.Series('', index=df.index, dtype=object)).fillna('')


def identity(row):
    return '<b>'+escape(text(row.get('ticker')))+'</b><span class="company-line">'+escape(text(row.get('company_name')))+'</span>'


def brief_reason(value, count=2):
    parts = [x.strip() for x in text(value).split('；') if x.strip()]
    return '；'.join(parts[:count]) + ('；其余见完整数据' if len(parts) > count else '')


def overview_html(df, symbol='$', historical=False):
    if historical:
        return ''
    strict = df[values(df, 'entry_status').isin(['ENTRY_REVIEW', 'DEEP_VALUE_REVIEW'])]
    quality = df[values(df, 'qv_status').eq('QUALITY_WATCH')]
    trend = quality[values(quality, 'trend_status').eq('TREND_WATCH')]
    wait = df[values(df, 'entry_status').isin(['NEAR_ENTRY', 'WAIT_PRICE'])]
    reviewed = 'entry_status' in df
    conclusion = (f'{len(strict)} 只达到严格入场复核条件' if len(strict) else '本次没有厚安全边际入场候选') if reviewed else '本次缺少严格入场评估，需要重新扫描'
    body = '<p class="eyebrow">本次结论</p><h2 class="conclusion">'+conclusion+'</h2>'
    body += '<table class="headline-metrics"><tr>'
    for label, value, note in [('严格入场复核', str(len(strict)) if reviewed else '未评估', '价格与质量均达标'),
                               ('合理估值观察', str(len(quality)) if 'qv_status' in df else '未评估', '独立研究池'),
                               ('其中趋势通过', str(len(trend)) if 'trend_status' in df else '未评估', '属于左侧观察池')]:
        body += f'<td><span>{label}</span><strong>{value}</strong><small>{note}</small></td>'
    body += '</tr></table><p class="sub">合理估值观察不代表厚安全边际；趋势通过也不是买入指令。</p>'
    if values(df, 'scan_status').eq('PARTIAL_SOURCE_FAILURE').any():
        body += '<p class="warning">扫描中断：以上仅为已扫描部分，不代表全市场结果。</p>'
    items = []
    if not strict.empty:
        row = strict.sort_values('distance_to_entry', ascending=False).iloc[0]
        items.append((row, '价格已达标 · 先核对公告', f'现价 {amount(row.get("price"), symbol)}；严格触发上限 {amount(row.get("entry_price"), symbol)}。'))
    if not trend.empty:
        row = trend.sort_values('trend_relative_6m', ascending=False).iloc[0]
        if not any(text(r.get('ticker')) == text(row.get('ticker')) for r, _, _ in items):
            items.append((row, '研究线索 · 趋势通过', f'现价 {amount(row.get("price"), symbol)}；研究估值上限 {amount(row.get("qv_price_limit"), symbol)}；严格触发上限 {amount(row.get("entry_price"), symbol)}。'))
    if not wait.empty:
        row = wait.sort_values('distance_to_entry', ascending=False).iloc[0]
        from opportunity import number
        gap = number(row.get('distance_to_entry'))
        if gap is not None and gap >= -.30 and not any(text(r.get('ticker')) == text(row.get('ticker')) for r, _, _ in items):
            items.append((row, '等待价格 · 距离最近', f'现价 {amount(row.get("price"), symbol)} → 严格触发上限 {amount(row.get("entry_price"), symbol)}；仍需下跌 {max(0,-gap):.1%}。'))
    for row, label, detail in items[:3]:
        body += '<div class="focus-item"><div class="focus-label">'+label+'</div>'+identity(row)+'<p>'+escape(detail)+'</p></div>'
    if not items:
        body += '<p>暂无需要优先展示的近期候选，继续等待价格或证据条件改善。</p>'
    return body


def quality_watch_html(df, symbol='$'):
    if df.empty or 'qv_status' not in df:
        return ''
    active = values(df, 'qv_status').eq('QUALITY_WATCH')
    trend = active & values(df, 'trend_status').eq('TREND_WATCH')
    body = '<h2>02 · 合理估值与趋势观察</h2>'
    body += f'<p>合理估值候选 <b>{int(active.sum())}</b> 只，其中趋势通过 <b>{int(trend.sum())}</b> 只。趋势通过的公司排在前面。</p>'
    review = values(df, 'trend_event').isin(['EXIT_REVIEW', 'DATA_REVIEW']) | values(df, 'qv_event').isin(['EXIT_REVIEW', 'DATA_REVIEW'])
    chosen = df[active | values(df, 'qv_status').eq('QUALITY_PRICE_WAIT') | review].copy()
    chosen['_display_priority'] = 3
    chosen.loc[values(chosen, 'qv_status').eq('QUALITY_WATCH'), '_display_priority'] = 2
    chosen.loc[review.reindex(chosen.index), '_display_priority'] = 1
    chosen.loc[(values(chosen, 'qv_status').eq('QUALITY_WATCH') & values(chosen, 'trend_status').eq('TREND_WATCH')), '_display_priority'] = 0
    chosen = chosen.sort_values(['_display_priority', 'qv_distance'], ascending=[True, False], na_position='last')
    if chosen.empty:
        return body+'<p class="empty">本次没有合理估值候选。需先通过质量、数据与价格条件。</p>'
    body += '<table class="watch-table"><thead><tr><th>股票</th><th>现价 / 研究上限</th><th>目前状态 · 下一步</th></tr></thead><tbody>'
    for _, row in chosen.head(10).iterrows():
        status = text(row.get('trend_status'))
        qv = text(row.get('qv_status'))
        needs_review = text(row.get('trend_event')) in {'EXIT_REVIEW', 'DATA_REVIEW'} or text(row.get('qv_event')) in {'EXIT_REVIEW', 'DATA_REVIEW'}
        if needs_review:
            label = '状态变化 · 需要复核'
            note = brief_reason(row.get('qv_reason') if qv != 'QUALITY_WATCH' else row.get('trend_reason'), 1)
        elif qv == 'QUALITY_PRICE_WAIT':
            label, note = '等待价格', '价格回到研究上限以内再评估趋势'
        elif status == 'TREND_WATCH':
            label, note = '趋势通过 · 优先研究', '核对最新公告与当前报价'
        elif status == 'TREND_WAIT':
            label, note = '等待趋势', '均线或相对强度尚未同时通过'
        else:
            label, note = '趋势证据不足', brief_reason(row.get('trend_reason'), 1)
        date = local_time(row.get('trend_asof'), date_only=True)
        body += '<tr'+(' class="highlight-row"' if status == 'TREND_WATCH' and qv == 'QUALITY_WATCH' else '')+'><td>'+identity(row)+'</td>'
        body += '<td><b>'+amount(row.get('price'), symbol)+'</b><br><span class="sub">上限 '+amount(row.get('qv_price_limit'), symbol)+'</span></td>'
        body += '<td><b>'+escape(label)+'</b><br>'+escape(note)
        if qv == 'QUALITY_WATCH':
            body += '<span class="sub block">趋势日期：'+date+'</span>'
        body += '</td></tr>'
    body += '</tbody></table>'
    if len(chosen) > 10:
        body += f'<p class="sub">显示 10 / {len(chosen)} 只；其余见完整扫描 CSV。</p>'
    body += '<p class="note">研究上限与严格触发上限属于不同策略。研究上限只代表合理估值筛选边界，不代表厚安全边际买入价。</p>'
    return body


def diagnostics_html(df, market='hk'):
    operating = df[~values(df, 'sector').str.lower().str.contains('financial') & ~values(df, 'model_type').eq('financial_pb_roe')]
    audit = operating[~values(operating, 'rating').eq('SKIP')]
    missing = pd.to_numeric(audit.get('fcf_ttm', pd.Series(index=audit.index, dtype=float)), errors='coerce').isna().sum()
    annual = values(audit, 'financial_period_type').eq('ANNUAL_FALLBACK').sum()
    body = '<h2>附录 · 数据覆盖与主要拦截原因</h2>'
    counts = values(operating, 'entry_status').value_counts()
    body += f'<p>经营型结果 {len(operating)} 只：等待价格 {counts.get("WAIT_PRICE",0)+counts.get("NEAR_ENTRY",0)} 只；仅因数据待补齐 {counts.get("DATA_REQUIRED",0)} 只；风险未通过 {counts.get("RISK_BLOCKED",0)} 只；行业专门复核 {counts.get("SPECIAL_REVIEW",0)} 只。</p>'
    body += f'<p class="sub">非金融且未跳过的 {len(audit)} 只中，扣除股权激励后的自由现金流缺失 {missing} 只，年报回退 {annual} 只。下列原因可重叠，数量不能相加。</p>'
    body += '<table class="audit-table"><thead><tr><th>分类</th><th>主要问题（各类前三项）</th></tr></thead><tbody>'
    blocked = operating[values(operating, 'entry_status').isin(['RISK_BLOCKED', 'DATA_REQUIRED'])]
    for key, label in [('entry_data_issues', '待补证据'), ('entry_risk_issues', '风险拦截')]:
        issues = values(blocked, key).str.split('；').explode()
        top = issues[issues.ne('')].value_counts().head(3)
        if not top.empty:
            lines = '<br>'.join(escape(str(k))+f'：{n} 只' for k,n in top.items())
            body += '<tr><td>'+label+'</td><td>'+lines+'</td></tr>'
    body += '</tbody></table>'
    if 'sbc_coverage_status' in audit:
        from cashflow_diagnostics import SBC_LABELS
        labels = '；'.join(f'{SBC_LABELS.get(str(k), str(k))} {n}只' for k,n in values(audit, 'sbc_coverage_status').replace('', 'NOT_FETCHED').value_counts().items())
        body += '<p class="sub">股权激励（SBC）证据：'+escape(labels)+'。有值仍需核验连续年份。</p>'
    if 'annual_cashflow_status' in audit:
        missing_table = values(audit, 'annual_cashflow_status').eq('UNAVAILABLE').sum()
        supplied = values(audit, 'statement_evidence_status').eq('SUPPLEMENTED').sum()
        body += f'<p class="sub">年度现金流表未返回 {missing_table} 只；采用公告补录 {supplied} 只。逐项原因、行业分布所需字段和完整清单保留在扫描与诊断 CSV。</p>'
    return body
