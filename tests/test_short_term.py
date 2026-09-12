import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from market_sessions import calendar, latest_completed
from short_term import make_plan, advance_trade, update_journal, report_html, POLICY
from short_term_scan import select_universe, run
from report_timing import premarket_title, market_time
from trend_evidence import compute_trend

NOW = pd.Timestamp('2026-09-11T22:00:00Z')


def bars(market='us'):
    dates = calendar(market).sessions_in_range('2026-05-01', '2026-09-11')[-65:]
    close = np.linspace(80, 100, len(dates))
    frame = pd.DataFrame({'Open': close-.3, 'High': close+.5, 'Low': close-1,
                          'Close': close, 'Adj Close': close, 'Volume': 1_000_000.,
                          'Dividends': 0., 'Stock Splits': 0.}, index=dates)
    base = frame.copy()
    # Separate benchmark has flat price and exactly the same session coverage.
    base[['Open', 'High', 'Low', 'Close', 'Adj Close']] = [100.,101.,99.,100.,100.]
    frame.loc[dates[-1], ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']] = [100.,103.5,99.5,103.,103.,2_000_000.]
    return frame, base


def plan():
    stock, base = bars()
    return make_plan('TEST', stock, base, now=NOW)


def next_bars(o=104., h=104.5, l=103., c=104.):
    return pd.DataFrame(dict(Open=[o], High=[h], Low=[l], Close=[c],
                             Volume=[1_000_000.], **{'Adj Close':[c], 'Dividends':[0.], 'Stock Splits':[0.]}),
                        index=pd.to_datetime(['2026-09-14']))


class SessionTests(unittest.TestCase):
    def test_holiday_and_after_close_buffer(self):
        self.assertEqual(str(latest_completed('us', '2026-09-07T23:00Z').date()), '2026-09-04')
        self.assertEqual(str(latest_completed('hk', '2026-09-11T08:10Z').date()), '2026-09-10')
        self.assertEqual(str(latest_completed('hk', '2026-09-11T08:21Z').date()), '2026-09-11')
        self.assertEqual(str(latest_completed('hk', '2026-09-11T04:30Z').date()), '2026-09-10')

    def test_us_delay_dst_and_local_date(self):
        self.assertIn('延迟', premarket_title('premarket_scan', 'us', pd.Timestamp('2026-09-11T17:43Z')))
        self.assertIsNone(premarket_title('premarket_scan', 'us', pd.Timestamp('2026-12-01T14:29Z')))
        self.assertIn('延迟', premarket_title('premarket_scan', 'us', pd.Timestamp('2026-12-01T14:30Z')))
        self.assertEqual(str(market_time('us', pd.Timestamp('2026-09-12T01:00Z')).date()), '2026-09-11')

    def test_half_day(self):
        self.assertEqual(calendar('us').session_close('2026-11-27').hour, 18)
        self.assertEqual(str(latest_completed('us', '2026-11-27T18:21Z').date()), '2026-11-27')

    def test_midterm_after_close_includes_completed_today(self):
        dates = calendar('hk').sessions_in_range('2025-09-01','2026-09-11')
        prices = pd.Series(np.linspace(80,100,len(dates)),index=dates)
        result = compute_trend(prices,prices,'hk','2026-09-11T09:00Z')
        self.assertEqual(result['status'],'OK')
        self.assertTrue(result['trend_asof'].startswith('2026-09-11'))


class PlanTests(unittest.TestCase):
    def test_plan_levels_and_late_generation(self):
        p = plan()
        self.assertEqual(p['status'], 'PENDING', p)
        self.assertEqual(p['entry_session'], '2026-09-14')
        self.assertLess(p['stop'], p['trigger'])
        self.assertLess(p['max_entry'], p['target'])
        self.assertGreaterEqual(p['net_reward_risk'], 1.5)
        stock, base = bars()
        late = make_plan('TEST', stock, base, now='2026-09-14T15:00Z')
        self.assertEqual(late['status'], 'LATE')

    def test_missing_future_duplicate_and_incomplete_bars(self):
        stock, base = bars()
        self.assertEqual(make_plan('TEST', stock.iloc[:-1], base, now=NOW)['status'], 'DATA')
        self.assertEqual(make_plan('TEST', stock.drop(stock.index[-5]), base, now=NOW)['status'], 'DATA')
        self.assertEqual(make_plan('TEST', pd.concat([stock,stock.tail(1)]), base, now=NOW)['status'], 'DATA')
        extra = stock.tail(1).copy()
        extra.index = pd.to_datetime(['2026-09-14'])
        extra['High'] = 999
        self.assertEqual(make_plan('TEST',pd.concat([stock,extra]),base,now=NOW),plan())

    def test_volume_price_and_actions(self):
        stock, base = bars()
        stock.loc[stock.index[-1], 'Volume'] = 1_000_000
        self.assertEqual(make_plan('TEST',stock,base,now=NOW)['status'],'WAIT')
        stock.loc[stock.index[-1], 'Stock Splits'] = 2
        self.assertEqual(make_plan('TEST',stock,base,now=NOW)['status'],'DATA')
        stock.loc[stock.index[-1], 'High'] = 1
        self.assertEqual(make_plan('TEST',stock,base,now=NOW)['status'],'DATA')

    def test_adjusted_close_does_not_change_raw_order_prices(self):
        stock, base = bars()
        stock['Adj Close'] *= .5
        self.assertEqual(make_plan('TEST',stock,base,now=NOW)['trigger'], plan()['trigger'])

    def test_invalid_unused_old_bar_does_not_block_current_window(self):
        stock,base = bars()
        stock.iloc[0,0] = -1
        self.assertEqual(make_plan('TEST',stock,base,now=NOW)['status'],'PENDING')

    def test_public_universe_independent_of_value_rating(self):
        row = dict(ticker='TEST', quote_type='EQUITY', scan_time=NOW.isoformat(), liquidity_value=100e6,
                   equity=100, statement_evidence_status='NOT_CONFIGURED', rating='F', is_holding=False)
        rows = [row, dict(row,ticker='PRIVATE',is_holding=True), dict(row,ticker='ETF',quote_type='ETF'),
                dict(row,ticker='OLD',scan_time='2026-08-01'), dict(row,ticker='BAD',equity=-1)]
        self.assertEqual(select_universe(pd.DataFrame(rows),NOW),['TEST'])


class ForwardTests(unittest.TestCase):
    def trade(self):
        return dict(plan(), state='PENDING')

    def test_gap_above_limit_no_fill_and_no_retroactive_fill(self):
        p = self.trade()
        self.assertEqual(advance_trade(p,next_bars(110,112,100,109),'us','2026-09-14T22:00Z')['state'],'EXPIRED')
        p['observed_at'] = '2026-09-14T15:00Z'
        self.assertEqual(advance_trade(p,next_bars(),'us','2026-09-14T22:00Z')['state'],'EXPIRED')

    def test_same_day_stop_first_and_costs(self):
        p = self.trade()
        r = advance_trade(p,next_bars(p['trigger'],p['target']+1,p['stop']-1,p['trigger']),
                          'us','2026-09-14T22:00Z')
        self.assertEqual(r['exit_reason'],'STOP')
        self.assertLess(r['net_return'],p['stop']/p['trigger']-1)

    def test_incomplete_and_missing_do_not_fabricate_fills(self):
        p = self.trade()
        self.assertEqual(advance_trade(p,next_bars(),'us','2026-09-14T18:00Z'),p)
        self.assertEqual(advance_trade(p,None,'us','2026-09-14T22:00Z')['state'],'DATA_REVIEW')
        f = next_bars()
        f['Dividends'] = 1
        self.assertEqual(advance_trade(p,f,'us','2026-09-14T22:00Z')['state'],'DATA_REVIEW')

    def test_time_exit_idempotence_and_ten_sessions(self):
        p = self.trade()
        dates = calendar('us').sessions_in_range('2026-09-14','2026-09-25')
        f = pd.concat([next_bars(p['trigger'],p['trigger']+.2,p['trigger']-.1,p['trigger'])]*10)
        f.index = dates
        r = advance_trade(p,f,'us','2026-09-25T22:00Z')
        self.assertEqual(r['exit_reason'],'TIME')
        self.assertEqual(r['held_sessions'],10)
        self.assertEqual(advance_trade(r,f,'us','2026-09-25T22:00Z'),r)
        journal = update_journal([], [plan(),plan()], {}, 'us', NOW)
        self.assertEqual(len(journal),1)
        self.assertEqual(len(update_journal(journal,[plan()],{},'us',NOW)),1)

    def test_open_gap_stop_uses_worse_price(self):
        p = self.trade()
        p.update(state='OPEN', fill=p['trigger'], held_sessions=1, last_processed='2026-09-11')
        r = advance_trade(p,next_bars(90,92,89,91),'us','2026-09-14T22:00Z')
        self.assertLess(r['exit_price'],90)

    def test_pipeline_empty_provider_and_report_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            row = dict(ticker='TEST',quote_type='EQUITY',scan_time=NOW.isoformat(),liquidity_value=100e6,
                       equity=100,statement_evidence_status='NOT_CONFIGURED')
            pd.DataFrame([row]).to_csv(Path(d)/'mos_market_latest.csv',index=False)
            stock, base = bars()
            result = run('us',d,NOW,fetch=lambda *a: {'TEST':stock,'^GSPC':base})
            self.assertEqual(len(result['trades']),1)
            self.assertIn('最高追价',report_html('us',d,NOW))
            self.assertIn('快照已过期',report_html('us',d,'2026-09-14T22:00Z'))
            result = run('us',d,NOW,fetch=lambda *a: {})
            self.assertEqual(result['plans'][0]['status'],'DATA')
            self.assertEqual(len(result['trades']),1)
            self.assertNotIn('最高追价',report_html('us',d,NOW))


if __name__ == '__main__':
    unittest.main()
