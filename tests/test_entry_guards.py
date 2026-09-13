import unittest
from copy import deepcopy
import pandas as pd
from test_short_term import bars,plan,NOW,next_bars
from entry_guards import regime_gate,earnings_gate,apply_guards,entry_snapshot_valid,value_label
from portfolio_risk import allocate
from short_term import advance_trade,ledger_html


def event(now=NOW,date='2026-11-01'):
    return dict(source='test calendar',observed_at=now.isoformat(),dates=[date])


def guarded(ticker='TEST'):
    p=plan()
    p.update(ticker=ticker,sector='Tech')
    return apply_guards([p],dict(status='ALLOW',asof='2026-09-11',reason='test'),{ticker:event()},{},'us',NOW)[0]


def account():
    return dict(market='us',currency='USD',asof=NOW.isoformat(),equity=100000.,peak_equity=100000.,cash=100000.,
                cash_basis='before_pending_reservations',positions=[],pending_orders=[])


class GuardTests(unittest.TestCase):
    def test_regime_above_below_missing_future(self):
        stock,_=bars()
        self.assertEqual(regime_gate(stock,'us',NOW)['status'],'ALLOW')
        stock.loc[stock.index[-1],['Open','High','Low','Close','Adj Close']]=[60.,61.,59.,60.,60.]
        self.assertEqual(regime_gate(stock,'us',NOW)['status'],'BLOCK')
        self.assertEqual(regime_gate(stock.iloc[:-1],'us',NOW)['status'],'UNKNOWN')

    def test_calendar_covers_tenth_trading_day_and_unknown(self):
        self.assertEqual(earnings_gate(event(date='2026-09-25'),'us','2026-09-14',NOW)['status'],'BLOCK')
        self.assertEqual(earnings_gate(event(date='2026-09-28'),'us','2026-09-14',NOW)['status'],'ALLOW')
        for e in [None,dict(event(),dates=[]),dict(event(),dates=['NaT']),dict(event(),observed_at=None),event(NOW+pd.Timedelta(days=1)),event(NOW-pd.Timedelta(days=5))]:
            self.assertEqual(earnings_gate(e,'us','2026-09-14',NOW)['status'],'UNKNOWN')

    def test_gates_retain_technical_candidate(self):
        p=apply_guards([plan()],dict(status='BLOCK',reason='below MA50'),{'TEST':event()},{},'us',NOW)[0]
        self.assertEqual(p['status'],'GUARD_BLOCKED')
        self.assertEqual(p['technical_status'],'PENDING')
        self.assertIn('below MA50',p['reason'])
        self.assertEqual(guarded()['status'],'PENDING')

    def test_later_evidence_cannot_authorize_earlier_fill(self):
        p=guarded()
        self.assertTrue(entry_snapshot_valid(p,'us'))
        p['earnings_gate']['observed_at']='2026-09-14T15:00Z'
        self.assertFalse(entry_snapshot_valid(p,'us'))

    def test_old_rating_is_not_value_proof(self):
        self.assertNotIn('同时通过',value_label({'rating':'S','scan_time':NOW.isoformat()},plan(),NOW))

    def test_comparison_requires_both_sides_closed(self):
        p=dict(guarded(),state='CLOSED',exit_policy='FIXED',net_return=.01,exited_on='2026-09-25',exit_reason='TIME')
        b=dict(p,id=p['id']+':be-1',source_id=p['id'],exit_policy='BE_1R',net_return=.03)
        self.assertIn('完整配对 0组',ledger_html([p]))
        self.assertIn('平均收益差 +2.00 个百分点',ledger_html([p,b]))

    def test_breakeven_only_next_session_not_same_day_low(self):
        p=dict(guarded(),state='OPEN',exit_policy='BE_1R',fill=100.,stop=95.,initial_stop=95.,initial_risk=5.,
               target=120.,held_sessions=1,last_processed='2026-09-14',filled_on='2026-09-14')
        frame=next_bars(102,106,99,104)
        frame.index=pd.to_datetime(['2026-09-15'])
        out=advance_trade(p,frame,'us','2026-09-15T22:00Z')
        self.assertEqual(out['state'],'OPEN')
        self.assertEqual(out['stop'],95.)
        self.assertGreater(out['next_stop'],100.)
        frame=next_bars(98,101,97,100)
        frame.index=pd.to_datetime(['2026-09-16'])
        out=advance_trade(out,frame,'us','2026-09-16T22:00Z')
        self.assertEqual(out['state'],'CLOSED')
        self.assertLess(out['exit_price'],98.)

    def test_buy_day_high_does_not_arm_breakeven(self):
        p=dict(guarded(),state='PENDING',exit_policy='BE_1R',trigger=100.,max_entry=101.,stop=95.,target=120.)
        out=advance_trade(p,next_bars(100,110,99,108),'us','2026-09-14T22:00Z')
        self.assertEqual(out['state'],'OPEN')
        self.assertNotIn('next_stop',out)


class PortfolioTests(unittest.TestCase):
    def test_changed_earnings_date_cannot_reuse_allow_status(self):
        p=guarded();p['earnings_gate']['earliest_date']='2026-09-15'
        out=allocate([p],account(),NOW)
        self.assertEqual(out['allocations'][0]['shares'],0)

    def test_absent_account_and_bad_values(self):
        self.assertEqual(allocate([guarded()],None,NOW)['allocations'],[])
        for changes in [dict(equity=float('nan')),dict(asof=None),dict(peak_equity=1),dict(cash_basis='ambiguous')]:
            a=account();a.update(changes)
            self.assertEqual(allocate([guarded()],a,NOW)['status'],'ACCOUNT_REQUIRED')

    def test_cash_risk_sector_and_duplicate_reservation(self):
        a=account()
        plans=[guarded(str(i)) for i in range(5)]
        plans.append(deepcopy(plans[0]))
        out=allocate(plans,a,NOW)
        used=[x for x in out['allocations'] if x['status']=='RESERVED']
        self.assertGreater(len(used),0,out)
        self.assertEqual(len({x['ticker'] for x in used}),len(used))
        self.assertLessEqual(sum(x['reserved_cash']/(1+.0005) for x in used),25000.+1e-5)
        self.assertLessEqual(out['reserved_stop_risk'],2000.+1e-5)
        self.assertTrue(all(x['estimated_stop_risk']<=500.+1e-5 for x in used))

    def test_drawdown_half_and_pause(self):
        full=allocate([guarded()],account(),NOW)['allocations'][0]['shares']
        a=account();a['peak_equity']=100000/.95
        out=allocate([guarded()],a,NOW)
        self.assertEqual(out['risk_scale'],.5)
        self.assertLessEqual(out['allocations'][0]['shares'],full)
        a['peak_equity']=100000/.9
        self.assertEqual(allocate([guarded()],a,NOW)['status'],'PAUSED')

    def test_existing_sector_and_pending_cash_are_reserved(self):
        a=account();a['cash']=75000.;a['positions']=[dict(ticker='OLD',sector='Tech',shares=250,price=100,stop=99)]
        self.assertEqual(allocate([guarded()],a,NOW)['allocations'][0]['shares'],0)
        a=account();a['cash']=1000.;a['pending_orders']=[dict(ticker='OLD',sector='Energy',shares=9,price=100,stop=99)]
        out=allocate([guarded()],a,NOW)
        self.assertGreaterEqual(out['unreserved_cash'],0)
        self.assertEqual(out['allocations'][0]['shares'],0)

    def test_hk_lot_missing_is_not_assumed(self):
        a=account();a.update(market='hk',currency='HKD')
        p=guarded();p['market']='hk'
        self.assertEqual(allocate([p],a,NOW)['status'],'ACCOUNT_REQUIRED')
        a['lot_sizes']={'TEST':1000}
        out=allocate([p],a,NOW)
        self.assertEqual(out['allocations'][0]['shares']%1000,0)


if __name__=='__main__':
    unittest.main()
