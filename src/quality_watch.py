"""Independent research watchlists. Engineering thresholds, not validated returns."""
from dataclasses import dataclass
import pandas as pd

from opportunity import evaluate_entry, number, truth


@dataclass(frozen=True)
class QualityPolicy:
    version: str = '1'
    max_pe: float = 20.0
    min_normalized_owner_yield: float = 0.05
    min_roe: float = 0.12


POLICY = QualityPolicy()
QUALITY_LABELS = {'QUALITY_WATCH': '优质合理估值·研究候选', 'QUALITY_PRICE_WAIT': '质量通过·价格偏高',
                  'QUALITY_DATA': '质量证据待补齐', 'QUALITY_RISK': '质量或风险未通过',
                  'QUALITY_SPECIAL': '需要专门模型', 'HISTORICAL_ONLY': '仅历史回放'}
TREND_LABELS = {'TREND_WATCH': '质量与趋势同时通过', 'TREND_WAIT': '等待趋势确认',
                'TREND_DATA': '趋势数据待补齐', 'NOT_ELIGIBLE': '先满足质量与价格条件',
                'HISTORICAL_ONLY': '仅历史回放'}
EVENT_LABELS = {'FIRST_OBSERVATION': '首次记录', 'UNCHANGED': '状态未变', 'ENTERED': '新进入观察池',
                'EXIT_REVIEW': '退出观察池·复核持有理由', 'DATA_REVIEW': '证据失效·先复核数据',
                'CHANGED': '观察状态变化'}


def evaluate_quality(row, now=None, policy=POLICY):
    result = dict(qv_policy_version=policy.version, qv_status='QUALITY_DATA', qv_reason='',
                  qv_price_limit=None, qv_distance=None, qv_pe=None, qv_normalized_owner_yield=None)
    def finish(status, reason):
        result.update(qv_status=status, qv_reason=reason)
        return result
    if truth(row.get('is_historical_replay')):
        return finish('HISTORICAL_ONLY', '历史价格与当前财报不可用于实时观察')
    # Reuse all existing data and quality vetoes; only the entry-price test differs.
    strict = evaluate_entry(row, now=now)
    blocked = {'DATA_REQUIRED': 'QUALITY_DATA', 'RISK_BLOCKED': 'QUALITY_RISK',
               'SPECIAL_REVIEW': 'QUALITY_SPECIAL', 'HISTORICAL_ONLY': 'HISTORICAL_ONLY'}
    if strict['entry_status'] in blocked:
        return finish(blocked[strict['entry_status']], strict['entry_reason'])
    if row.get('entry_status') == 'REVIEW_REQUIRED':
        return finish('QUALITY_RISK', '原估值下修复核尚未解除')
    roe = number(row.get('roe'))
    if roe is None:
        return finish('QUALITY_DATA', 'ROE缺失，不能证明资本盈利能力')
    if roe < policy.min_roe:
        return finish('QUALITY_RISK', 'ROE低于12%的初始质量要求')
    price, cap, earnings = [number(row.get(k)) for k in ('price', 'market_cap', 'net_income_ttm')]
    fcfs = [number(row.get(k)) for k in ('fcf_ttm', 'fcf_3y_avg', 'fcf_5y_avg')]
    if any(x is None or x <= 0 for x in [price, cap, earnings]+fcfs):
        return finish('QUALITY_DATA', '缺少可用的正现金流、利润或市值')
    normalized = min(fcfs)
    limit = min(price*earnings/cap*policy.max_pe, price*normalized/cap/policy.min_normalized_owner_yield)
    result.update(qv_price_limit=limit, qv_distance=limit/price-1, qv_pe=cap/earnings,
                  qv_normalized_owner_yield=normalized/cap)
    if price > limit:
        return finish('QUALITY_PRICE_WAIT', '质量通过，但价格超过20倍盈利或5%正常化Owner FCF收益率约束')
    return finish('QUALITY_WATCH', '通过质量门槛及合理估值初始规则；不等于厚安全边际或买入指令')


TREND_FIELDS = ['trend_asof', 'trend_adjusted_close', 'trend_ma50', 'trend_ma200',
                'trend_momentum_6m', 'trend_benchmark_momentum_6m', 'trend_relative_6m',
                'trend_benchmark', 'trend_benchmark_close', 'trend_observations']


def annotate_watchlists(df, previous=None, evidence=None, now=None):
    if df.empty:
        return df.copy()
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.to_datetime(now, utc=True)
    previous_rows = {} if previous is None or previous.empty else {str(r['ticker']): r for _, r in previous.iterrows()}
    evidence = evidence or {}
    rows = []
    for _, row in df.iterrows():
        qv = evaluate_quality(row, now)
        output = row.to_dict()
        output.update(qv, watch_observed_at=now.isoformat())
        output.update({k: None for k in TREND_FIELDS})
        output.update(trend_status='NOT_ELIGIBLE', trend_reason='先满足质量与合理估值条件')
        if qv['qv_status'] == 'HISTORICAL_ONLY':
            output.update(trend_status='HISTORICAL_ONLY', trend_reason='历史回放不生成实时趋势信号')
        elif qv['qv_status'] == 'QUALITY_WATCH':
            item = evidence.get(str(row['ticker']), {})
            output.update(trend_status='TREND_DATA', trend_reason=item.get('reason', '本次未取得可核验的完整价格历史'))
            date = pd.to_datetime(item.get('trend_asof'), utc=True, errors='coerce')
            age = (now-date).total_seconds()/86400 if pd.notna(date) else None
            if item.get('status') == 'OK' and age is not None and 0 <= age <= 5:
                output.update({k: item.get(k) for k in TREND_FIELDS})
                close, ma50, ma200, momentum, relative = [number(item.get(k)) for k in
                    ('trend_adjusted_close', 'trend_ma50', 'trend_ma200', 'trend_momentum_6m', 'trend_relative_6m')]
                if all(x is not None for x in (close, ma50, ma200, momentum, relative)):
                    strong = close > ma200 and ma50 > ma200 and momentum > 0 and relative > 0
                    output.update(trend_status='TREND_WATCH' if strong else 'TREND_WAIT',
                                  trend_reason='完整交易日趋势通过，需复核当前报价' if strong else '长均线或相对强度未同时通过；若此前持有，应复核趋势理由')
            elif item.get('status') == 'OK':
                output['trend_reason'] = '价格历史日期缺失或过期，不能沿用旧趋势'
        old = previous_rows.get(str(row['ticker']))
        comparable = old is not None and str(old.get('model_version')) == str(row.get('model_version')) and number(old.get('qv_policy_version')) == number(POLICY.version)
        for column, active in [('qv_status', 'QUALITY_WATCH'), ('trend_status', 'TREND_WATCH')]:
            event = 'FIRST_OBSERVATION'
            if comparable:
                status, before = output[column], old.get(column)
                if status == before:
                    event = 'UNCHANGED'
                elif before == active:
                    event = 'DATA_REVIEW' if status in {'QUALITY_DATA', 'TREND_DATA'} or qv['qv_status'] == 'QUALITY_DATA' else 'EXIT_REVIEW'
                elif status == active:
                    event = 'ENTERED'
                else:
                    event = 'CHANGED'
            output['qv_event' if column == 'qv_status' else 'trend_event'] = event
        rows.append(output)
    return pd.DataFrame(rows)


def enrich_watchlists(df, previous=None, market='us', now=None, fetch_history=True):
    first = annotate_watchlists(df, previous=previous, now=now)
    if first.empty or not fetch_history:
        return first
    eligible = first[first['qv_status'].eq('QUALITY_WATCH')].sort_values('liquidity_value', ascending=False)
    if eligible.empty:
        return first
    from trend_evidence import fetch_trend_evidence
    evidence = fetch_trend_evidence(eligible['ticker'].astype(str).tolist(), market, now=now)
    return annotate_watchlists(df, previous=previous, evidence=evidence, now=now)


def save_watch_history(public, path):
    """Caller supplies only public rows from a complete scan."""
    if public.empty or 'qv_status' not in public:
        return
    keep = public.qv_status.isin(['QUALITY_WATCH', 'QUALITY_PRICE_WAIT'])
    for key in ['qv_event', 'trend_event']:
        keep |= public[key].isin(['EXIT_REVIEW', 'DATA_REVIEW'])
    columns = ['ticker', 'model_version', 'qv_policy_version', 'watch_observed_at',
               'qv_status', 'qv_reason', 'qv_event', 'trend_status', 'trend_reason', 'trend_event',
               'price', 'price_asof', 'financial_asof', 'fundamentals_asof', 'entry_status',
               'qv_price_limit', 'qv_pe', 'qv_normalized_owner_yield', 'roe'] + TREND_FIELDS
    signals = public.loc[keep].reindex(columns=columns)
    if signals.empty:
        return
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()
    history = pd.concat([old, signals], ignore_index=True).drop_duplicates(['ticker', 'watch_observed_at'])
    path.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(path, index=False)


def watch_html(df, symbol='$'):
    from html import escape
    if df.empty or 'qv_status' not in df:
        return ''
    quality = df[df.qv_status.eq('QUALITY_WATCH')]
    trend = df[df.trend_status.eq('TREND_WATCH')]
    body = '<h2>独立研究观察池（试运行）</h2>'
    body += f'<p>合理估值候选 {len(quality)} 只，其中趋势同时通过 {len(trend)} 只；两者重叠，不重复计算机会。</p>'
    body += '<p>保留原厚安全边际门槛。此池采用 ROE≥12%、PE≤20、正常化 Owner FCF 收益率≥5%的初始规则，尚未验证收益。约20%是你的回撤容忍偏好，并非系统保证；缺少持仓金额时无法评估账户回撤。</p>'
    chosen = df[df.qv_status.isin(['QUALITY_WATCH', 'QUALITY_PRICE_WAIT']) | df.trend_event.isin(['EXIT_REVIEW', 'DATA_REVIEW']) | df.qv_event.isin(['EXIT_REVIEW', 'DATA_REVIEW'])]
    chosen = chosen.sort_values('qv_distance', ascending=False, na_position='last').head(10)
    for _, row in chosen.iterrows():
        limit = number(row.get('qv_price_limit'))
        limit_text = f'{symbol}{limit:.2f}' if limit is not None else '不可用'
        body += '<p><b>'+escape(str(row['ticker']))+'</b> — '+escape(QUALITY_LABELS.get(row.qv_status, row.qv_status))
        body += '；研究估值上限 '+limit_text+'（不是厚安全边际买入价）<br>'
        body += escape(str(row.qv_reason))+'<br>'+escape(TREND_LABELS.get(row.trend_status, row.trend_status))+'：'+escape(str(row.trend_reason))
        body += '<br>趋势截至 '+escape(str(row.get('trend_asof') or '暂无'))+'；'+escape(EVENT_LABELS.get(row.trend_event, row.trend_event))+'</p>'
    body += '<p>趋势要求：复权收盘价与50日均线高于200日均线，跳过最近21个交易日的6个月涨幅为正且高于市场基准。质量失效、价格超限或趋势转弱时复核；数据失效先补证据。这里只提供研究线索，不自动交易。</p>'
    return body
