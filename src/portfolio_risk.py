"""Private, sequential cash/risk reservations. Never places or confirms an order."""
import argparse
import json
import math
from pathlib import Path
from market_sessions import timestamp, latest_completed
from entry_guards import earnings_gate, GATE_VERSION


def positive(value, name, zero=False):
    if isinstance(value,bool):
        raise ValueError(name+'无效')
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not zero and number==0):
        raise ValueError(name+'无效')
    return number


def allocate(plans, account=None, now=None):
    """Research limits: 0.5% single risk, 10% issuer, 25% sector, 2% open risk, five issuers."""
    if account is None:
        return dict(status='ACCOUNT_REQUIRED',reason='未提供账户净值、峰值、现金、持仓和挂单；不输出股数',allocations=[])
    try:
        if not account.get('asof') or account.get('cash_basis')!='before_pending_reservations':
            raise ValueError('需要快照时间和明确的现金口径 before_pending_reservations')
        equity = positive(account['equity'],'净值')
        peak = positive(account['peak_equity'],'历史峰值')
        cash = positive(account['cash'],'现金',True)
        if peak < equity or cash > equity:
            raise ValueError('峰值或现金与净值不一致；峰值须调整出入金影响')
        market = account['market']
        if market not in {'hk','us'} or account['currency'] != ('HKD' if market=='hk' else 'USD'):
            raise ValueError('市场或币种不一致；不自动做汇率转换')
        age = (timestamp(now)-timestamp(account['asof'])).total_seconds()
        if not 0 <= age <= 86400:
            raise ValueError('账户快照超过24小时或来自未来')
        positions, orders = account['positions'],account['pending_orders']
        if not isinstance(positions,list) or not isinstance(orders,list):
            raise ValueError('持仓与挂单必须明确提供列表，空仓请填空列表')
        fee, slip = (.0015 if market=='hk' else .0005),.001
        issuer,sector_totals,sectors = {},{},{}
        open_risk = reserved = held_value = 0.
        for pending,rows in [(False,positions),(True,orders)]:
            for row in rows:
                ticker, sector = str(row['ticker']).strip(),str(row['sector']).strip()
                if not ticker or not sector or sector.upper() in {'UNKNOWN','NAN','NONE'}:
                    raise ValueError('持仓/挂单缺少代码或行业')
                if ticker in sectors and sectors[ticker]!=sector:
                    raise ValueError('同一股票行业不一致')
                sectors[ticker]=sector
                qty=positive(row['shares'],'股数')
                price=positive(row['price'],'现价/挂单上限')
                stop=positive(row['stop'],'止损')
                if stop>=price:
                    raise ValueError('持仓已触及止损或止损无效，先复核退出')
                amount=qty*price
                cost=amount*(1+fee) if pending else amount
                issuer[ticker]=issuer.get(ticker,0)+amount
                sector_totals[sector]=sector_totals.get(sector,0)+amount
                open_risk += cost-qty*stop*(1-slip)*(1-fee)
                if pending:
                    reserved += cost
                else:
                    held_value += amount
        cash -= reserved
        if cash<0 or cash+reserved+held_value > equity+1e-6:
            raise ValueError('持仓估值或挂单预留超过账户净值/现金')
        drawdown=1-equity/peak
        factor=0 if drawdown>=.1-1e-12 else (.5 if drawdown>=.05-1e-12 else 1.)
        budget=min(.005*equity*factor, max(0,.02*equity-open_risk))
        result=dict(status='PAUSED' if factor==0 else 'READY',drawdown=drawdown,risk_scale=factor,
                    reason='回撤达10%，暂停新仓' if factor==0 else '测算不等于实盘委托；需入场前重新核验',allocations=[])
        seen=set()
        ranked=sorted(plans,key=lambda p:(-float(p.get('relative_20d',0)),-float(p.get('volume_ratio',0)),p['ticker'],p.get('policy','')))
        for p in ranked:
            ticker=p['ticker']
            item=dict(ticker=ticker,policy=p.get('policy'),status='BLOCKED',reason='',shares=0)
            def block(reason):
                item['reason']=reason
                result['allocations'].append(item)
            if ticker in seen:
                block('同股票多策略合并，只预留一次')
                continue
            if p.get('market')!=market or p.get('status')!='PENDING' or p.get('market_gate',{}).get('status')!='ALLOW' or p.get('earnings_gate',{}).get('status')!='ALLOW':
                block('市场、技术或排雷条件未通过')
                continue
            if not p.get('observed_at') or not p.get('expires_at') or not p['earnings_gate'].get('observed_at'):
                block('缺少计划或财报观察时间')
                continue
            if p['market_gate'].get('asof')!=str(latest_completed(market,now).date()) or not 0<=(timestamp(now)-timestamp(p['earnings_gate']['observed_at'])).total_seconds()<=4*86400:
                block('大盘或财报快照已过期')
                continue
            if timestamp(now)>=timestamp(p['expires_at']) or (timestamp(now)-timestamp(p['observed_at'])).total_seconds()<0:
                block('计划已过期或观察时间无效')
                continue
            evidence=p['earnings_gate']
            checked=earnings_gate(dict(dates=[evidence.get('earliest_date')],
                                       observed_at=evidence.get('observed_at'),source=evidence.get('source')),
                                  market,p.get('entry_session'),now)
            if p.get('gate_version')!=GATE_VERSION or checked['status']!='ALLOW':
                block('财报日期复核未通过或计划缺少新版排雷证据')
                continue
            if factor==0:
                block('账户回撤达到暂停阈值')
                continue
            sector=str(p.get('sector','')).strip()
            if not sector or sector.upper() in {'UNKNOWN','NAN','NONE'}:
                block('缺少股票行业')
                continue
            if ticker in sectors and sectors[ticker]!=sector:
                block('计划行业与持仓行业冲突')
                continue
            lot=positive(account.get('lot_sizes',{}).get(ticker),'每手股数') if market=='hk' else 1.
            if not lot.is_integer():
                raise ValueError('每手股数必须为整数')
            entry=positive(p['max_entry'],'最高追价')
            stop=positive(p['stop'],'止损')
            if entry<=stop:
                block('止损距离无效')
                continue
            if ticker not in issuer and len(issuer)>=5:
                block('最多同时持有/预留五只股票')
                continue
            per_share=entry*(1+fee)-stop*(1-slip)*(1-fee)
            allowed=min(cash/(entry*(1+fee)),max(0,.1*equity*factor-issuer.get(ticker,0))/entry,
                        max(0,.25*equity-sector_totals.get(sector,0))/entry,
                        min(budget,max(0,.02*equity-open_risk))/per_share)
            qty=int(max(0,allowed)//int(lot))*int(lot)
            if qty==0:
                block('现金、风险、单股、行业或每手约束不足一手')
                continue
            cost=qty*entry*(1+fee)
            cash-=cost
            open_risk+=qty*per_share
            issuer[ticker]=issuer.get(ticker,0)+qty*entry
            sectors[ticker]=sector
            sector_totals[sector]=sector_totals.get(sector,0)+qty*entry
            seen.add(ticker)
            item.update(status='RESERVED',shares=qty,reserved_cash=cost,estimated_stop_risk=qty*per_share,
                        reason='条件性资金预留，不是成交；未收到实时价格，禁止直接当成订单')
            result['allocations'].append(item)
        result.update(unreserved_cash=cash,reserved_stop_risk=open_risk)
        return result
    except (KeyError,ValueError,TypeError,OverflowError) as exc:
        return dict(status='ACCOUNT_REQUIRED',reason='账户/计划资料待复核：'+str(exc),allocations=[])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--account',type=Path)
    parser.add_argument('--state',type=Path,required=True)
    args=parser.parse_args()
    result=allocate(json.loads(args.state.read_text(encoding='utf-8'))['plans'],json.loads(args.account.read_text(encoding='utf-8')) if args.account else None)
    # Always private; never write account values into public state or print them into job logs.
    target=Path(__file__).resolve().parents[1]/'reports/private/position_sizing.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Private sizing result saved; status='+result['status'])
