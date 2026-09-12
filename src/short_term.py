"""Forward-only, daily breakout research. No broker orders or profit claims."""
from html import escape
import json
from pathlib import Path
import numpy as np
import pandas as pd
from market_sessions import calendar, latest_completed, timestamp

POLICY = 'breakout-1'
FIELDS = ['Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close', 'Dividends', 'Stock Splits']
LABELS = {'PENDING': '等待下一交易日触发（模拟）', 'LATE': '生成太晚，禁止补记买入',
          'EXPIRED': '计划有效期已结束',
          'WAIT': '未满足突破条件', 'DATA': '行情或证据不足', 'RISK': '基础风险未通过'}


def clean_bars(frame, market, now, start=None):
    if frame is None or not set(FIELDS).issubset(frame.columns):
        raise ValueError('缺少完整OHLCV、复权或公司行动数据')
    frame = frame[FIELDS].copy()
    frame.index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in frame.index])
    if frame.index.has_duplicates:
        raise ValueError('行情日期重复')
    frame = frame.loc[frame.index <= latest_completed(market, now)].sort_index()
    if start is not None:
        frame = frame.loc[frame.index >= start]
    frame = frame.apply(pd.to_numeric, errors='coerce')
    if frame.empty or not np.isfinite(frame.to_numpy()).all():
        raise ValueError('行情为空或含无效数值')
    if (frame[['Open', 'High', 'Low', 'Close', 'Adj Close']] <= 0).any().any():
        raise ValueError('价格必须为正')
    if ((frame.High < frame[['Open', 'Close', 'Low']].max(axis=1)) |
        (frame.Low > frame[['Open', 'Close', 'High']].min(axis=1)) |
        (frame.Volume < 0)).any():
        raise ValueError('OHLC或成交量异常')
    return frame


def make_plan(ticker, frame, benchmark, market='us', now=None):
    now = timestamp(now)
    result = dict(ticker=str(ticker), policy=POLICY, status='DATA', reason='', market=market,
                  observed_at=now.isoformat())
    def finish(status, reason):
        return dict(result, status=status, reason=reason)
    try:
        cal = calendar(market)
        session = latest_completed(market, now)
        expected = cal.sessions_in_range(session-pd.Timedelta(days=130), session)[-60:]
        bars = clean_bars(frame, market, now, expected[0])
        base = clean_bars(benchmark, market, now, expected[0])
        if len(expected) != 60 or not bars.tail(60).index.equals(expected) or not base.tail(60).index.equals(expected):
            raise ValueError('最近60个交易日不连续或最新完整交易日缺失')
        bars, base = bars.tail(60), base.tail(60)
        # Normalize historical OHLC to the last raw close's units; order levels remain raw prices.
        factor = bars['Adj Close']/bars.Close
        adjusted = bars[['Open', 'High', 'Low', 'Close']].mul(factor/factor.iloc[-1], axis=0)
        close = adjusted.Close
        avg_volume = bars.Volume.iloc[-21:-1].mean()
        turnover = (bars.Close*bars.Volume).iloc[-21:-1].mean()
        if bars.Close.iloc[-1] < (2 if market == 'hk' else 5) or turnover < 20_000_000 or avg_volume <= 0:
            return finish('WAIT', '价格或20日平均成交额未达初始流动性门槛')
        if (bars['Stock Splits'].tail(21) != 0).any():
            return finish('DATA', '近期拆合股：成交量与价格单位需人工复核')
        relative = close.iloc[-1]/close.iloc[-21] - base['Adj Close'].iloc[-1]/base['Adj Close'].iloc[-21]
        ma20, ma50 = close.tail(20).mean(), close.tail(50).mean()
        volume_ratio = bars.Volume.iloc[-1]/avg_volume
        result.update(signal_date=str(session.date()), close=float(close.iloc[-1]),
                      turnover=float(turnover), volume_ratio=float(volume_ratio), relative_20d=float(relative))
        if not (close.iloc[-1] > ma20 > ma50 and relative > 0 and
                close.iloc[-1] > adjusted.High.iloc[-21:-1].max() and volume_ratio >= 1.5):
            return finish('WAIT', '需收盘突破前20日高点、放量1.5倍、均线向上且20日强于基准')
        tr = pd.concat([adjusted.High-adjusted.Low,
                        (adjusted.High-close.shift()).abs(), (adjusted.Low-close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        trigger = float(bars.High.iloc[-1])*1.001
        stop, ceiling = trigger-1.5*atr, trigger+0.25*atr
        target = trigger+3.75*atr
        fee, slip = (0.0015 if market == 'hk' else 0.0005), 0.001
        net_gain = target*(1-slip)*(1-fee)/(ceiling*(1+fee))-1
        net_loss = 1-stop*(1-slip)*(1-fee)/(ceiling*(1+fee))
        if stop <= 0 or not 0.015 <= (ceiling-stop)/ceiling <= 0.08 or net_gain/net_loss < 1.5:
            return finish('WAIT', '追价上限处风险距离或扣费后盈亏比不合要求')
        entry_session = cal.next_session(session)
        result.update(id=f'{market}:{ticker}:{session.date()}:{POLICY}', trigger=trigger,
                      max_entry=ceiling, stop=stop, target=target, max_sessions=10,
                      fee_per_side=fee, slippage=slip, net_reward_risk=float(net_gain/net_loss),
                      entry_session=str(entry_session.date()), expires_at=cal.session_close(entry_session).isoformat())
        if now >= cal.session_open(entry_session):
            return finish('LATE', '计划生成时下一交易日已开盘，不追认当天早盘成交')
        return finish('PENDING', '仅下一交易日有效；跳空超过上限放弃；财报事件与交易单位仍需复核')
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return finish('DATA', str(exc))


def advance_trade(trade, frame, market, now=None):
    """Conservative daily fills; no intraday order or portfolio simulation."""
    trade = dict(trade)
    if trade['state'] not in {'PENDING', 'OPEN', 'DATA_REVIEW'}:
        return trade
    cal, end = calendar(market), latest_completed(market, now)
    start = pd.Timestamp(trade.get('last_processed', trade['entry_session']))
    if trade.get('last_processed'):
        start = cal.next_session(start)
    if start > end:
        return trade
    try:
        bars = clean_bars(frame, market, now, start)
        for date in cal.sessions_in_range(start, end):
            if date not in bars.index:
                raise ValueError('应有交易日缺行情，不能判定成交或退出')
            row = bars.loc[date]
            if row['Stock Splits'] != 0 or row.Dividends != 0 or row.Volume <= 0:
                raise ValueError('公司行动或停牌需复核，暂停收益计算')
            trade['state'] = 'OPEN' if 'fill' in trade else 'PENDING'
            if trade['state'] == 'PENDING':
                if timestamp(trade['observed_at']) >= cal.session_open(date):
                    trade.update(state='EXPIRED', exit_reason='计划晚于开盘，禁止回填')
                    return trade
                fill = max(float(row.Open), trade['trigger'])*(1+trade['slippage'])
                if row.Open <= trade['stop'] or fill > trade['max_entry'] or row.High < trade['trigger']:
                    trade.update(state='EXPIRED', exit_reason='有效日未触发或跳空越界')
                    return trade
                trade.update(state='OPEN', fill=fill, filled_on=str(date.date()), held_sessions=0)
            trade['held_sessions'] += 1
            exit_price, reason = None, ''
            # Daily bars cannot reveal ordering: when both levels touch, assume stop first.
            if row.Low <= trade['stop']:
                exit_price, reason = min(float(row.Open), trade['stop']), 'STOP'
            elif row.High >= trade['target']:
                exit_price, reason = trade['target'], 'TARGET'
            elif trade['held_sessions'] >= trade['max_sessions']:
                exit_price, reason = float(row.Close), 'TIME'
            trade['last_processed'] = str(date.date())
            if exit_price is not None:
                exit_price *= 1-trade['slippage']
                fee = trade['fee_per_side']
                trade.update(state='CLOSED', exit_price=exit_price, exit_reason=reason,
                             exited_on=str(date.date()),
                             net_return=exit_price*(1-fee)/(trade['fill']*(1+fee))-1)
                return trade
        trade.pop('data_reason', None)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        trade.update(state='DATA_REVIEW', data_reason=str(exc))
    return trade


def update_journal(journal, plans, frames, market, now=None):
    trades = [advance_trade(t, frames.get(t['ticker']), market, now) for t in journal]
    ids = {t['id'] for t in trades}
    active = {t['ticker'] for t in trades if t['state'] in {'PENDING', 'OPEN', 'DATA_REVIEW'}}
    for p in plans:
        if p['status'] == 'PENDING' and p['id'] not in ids and p['ticker'] not in active:
            trades.append(dict(p, state='PENDING'))
            ids.add(p['id'])
            active.add(p['ticker'])
    return trades


def ledger_html(trades):
    active = [t for t in trades if t['state'] in {'OPEN', 'DATA_REVIEW'}]
    closed = [t for t in trades if t['state'] == 'CLOSED']
    parts = [f'<h3>前瞻模拟跟踪</h3><p>已结束 {len(closed)}笔；持有或待核验 {len(active)}笔。'
             '只记录计划生成后的行情，未做历史收益补算。</p>']
    for t in active[:5]:
        reason = ('数据待复核：'+t.get('data_reason','')) if t['state'] == 'DATA_REVIEW' else f'模拟持有第{t.get("held_sessions",0)}个交易日'
        parts.append(f'<p><b>{escape(t["ticker"])}</b> · {escape(reason)}；止损参考 {t["stop"]:.2f}，'
                     f'目标参考 {t["target"]:.2f}，最晚第10个交易日退出。</p>')
    for t in sorted(closed, key=lambda t:t['exited_on'], reverse=True)[:3]:
        reason = {'STOP':'止损', 'TARGET':'目标', 'TIME':'到期'}.get(t['exit_reason'],'退出')
        parts.append(f'<p>{escape(t["ticker"])} · {escape(t["exited_on"])} {reason}；'
                     f'估计扣费后逐笔收益 {t["net_return"]:+.2%}（模拟，不是账户收益）。</p>')
    return ''.join(parts)


def report_html(market, state_dir=None, now=None):
    """Read only public research state. Old or missing plans never become buy signals."""
    root = Path(state_dir) if state_dir is not None else Path(__file__).resolve().parents[1]/'state'
    prefix = 'hk_' if market == 'hk' else ''
    path = root/f'{prefix}mos_short_term.json'
    intro = '<h2>2–10个交易日 · 短线实验池</h2><p class="sub">放量突破策略；尚无已验证的盈利优势。以下为日线模拟计划，不是实时成交确认。</p>'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('policy') != POLICY or data.get('market') != market:
            raise ValueError('版本或市场不符')
        ledger = ledger_html(data.get('trades', []))
        now = timestamp(now)
        if timestamp(data['observed_at']) > now or pd.Timestamp(data['signal_date']) != latest_completed(market, now):
            return intro+'<p>短线快照已过期，等待新一轮完整行情；旧触发价不再展示。下列跟踪记录也尚未更新。</p>'+ledger
        plans = [p for p in data['plans'] if p['status'] in {'PENDING', 'LATE'}]
        cards = []
        for p in plans[:5]:
            opened = now >= calendar(market).session_open(p['entry_session'])
            label = '交易时段已开始，需实时核验；本报告不确认触发' if opened else LABELS[p['status']]
            if p['status'] == 'LATE' or now >= timestamp(p['expires_at']):
                label = '计划已失效，禁止追认买入'
                cards.append(f'<p><b>{escape(p["ticker"])}</b> · {label}</p>')
                continue
            currency = 'HK$' if market == 'hk' else '$'
            cards.append(f'<div class="stock"><h3>{escape(p["ticker"])} · {label}</h3>'
                         f'<p>触发 {currency}{p["trigger"]:.2f}；最高追价 {currency}{p["max_entry"]:.2f}</p>'
                         f'<p>止损参考 {currency}{p["stop"]:.2f}；目标参考 {currency}{p["target"]:.2f}</p>'
                         f'<p class="sub">仅 {escape(p["entry_session"])} 常规交易时段有效；最长10个交易日，止损可提前。'
                         f'扣费情景盈亏比 {p["net_reward_risk"]:.2f}。尚需核查公告、财报日、盘口和每手股数。</p></div>')
        counts = pd.Series(['EXPIRED' if p['status'] == 'PENDING' and now >= timestamp(p['expires_at'])
                            else p['status'] for p in data['plans']], dtype=str).value_counts()
        summary = '；'.join(f'{LABELS.get(k,k)} {v}只' for k,v in counts.items())
        local = timestamp(data['observed_at']).tz_convert('Asia/Hong_Kong' if market == 'hk' else 'America/New_York')
        observed_label = f'{local:%Y-%m-%d %H:%M}（市场当地时间）'
        return intro+f'<p>行情截至 {escape(data["signal_date"])}；短线检查完成 {observed_label}；实际检查 {len(data["plans"])}只。{escape(summary)}</p>'+(''.join(cards) or '<p>当前没有可展示的短线计划；不放宽阈值凑数量。</p>')+ledger+'<p class="sub">完整清单与逐笔模拟记录见 short-term 工作流产物。模拟收益不等于账户收益，尚不能评估20%账户回撤约束。</p>'
    except (OSError, ValueError, KeyError, TypeError):
        return intro+'<p>短线任务尚未成功生成可用快照；请查看 short-term 工作流。不能用长期估值替代短线买点。</p>'
