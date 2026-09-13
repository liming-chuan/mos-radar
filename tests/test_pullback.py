import unittest
from copy import deepcopy
from test_short_term import bars, NOW, plan
from short_term import make_plan, update_journal, ledger_html, annotate_existing_positions


def pullback_bars():
    stock,base = bars()
    for date,close in zip(stock.index[-5:],[100.,99.,98.5,97.5,100.]):
        stock.loc[date,['Open','High','Low','Close','Adj Close','Volume']] = [close-.2,close+.5,close-.5,close,close,700_000.]
    stock.loc[stock.index[-1],['Open','Low','Volume']] = [98.,97.2,1_300_000.]
    return stock,base


class PullbackTests(unittest.TestCase):
    def test_reclaim_without_new_20_day_high(self):
        stock,base = pullback_bars()
        p = make_plan('TEST',stock,base,now=NOW,strategy='pullback-1')
        self.assertEqual(p['status'],'PENDING',p)
        self.assertEqual(p['policy'],'pullback-1')
        self.assertTrue(p['id'].endswith('pullback-1'))
        self.assertLess(p['stop'],stock.Low.tail(4).min())
        self.assertNotEqual(make_plan('TEST',stock,base,now=NOW)['status'],'PENDING')

    def test_low_volume_is_watch_only(self):
        stock,base = pullback_bars()
        stock.loc[stock.index[-1],'Volume'] = 700_000.
        p = make_plan('TEST',stock,base,now=NOW,strategy='pullback-1')
        self.assertEqual(p['status'],'NEAR',p)
        self.assertIn('成交量确认',p['missing_conditions'])
        self.assertNotIn('trigger',p)
        self.assertEqual(update_journal([],[p],{},'us',NOW),[])

    def test_missing_calendar_bar_and_late_observation(self):
        stock,base = pullback_bars()
        self.assertEqual(make_plan('TEST',stock.iloc[:-1],base,now=NOW,strategy='pullback-1')['status'],'DATA')
        self.assertEqual(make_plan('TEST',stock,base,now='2026-09-14T15:00Z',strategy='pullback-1')['status'],'LATE')

    def test_prior_high_volume_or_no_pullback_rejected(self):
        stock,base = pullback_bars()
        stock.loc[stock.index[-4:-1],'Volume'] = 2_000_000.
        p = make_plan('TEST',stock,base,now=NOW,strategy='pullback-1')
        self.assertEqual(p['status'],'WAIT')
        self.assertIn('回踩缩量',p['missing_conditions'])

    def test_independent_journals_preserve_old_policy_and_dedupe(self):
        stock,base = pullback_bars()
        a = plan()
        b = make_plan('TEST',stock,base,now=NOW,strategy='pullback-1')
        journal = update_journal([], [a,b,a,b], {}, 'us', NOW)
        self.assertEqual(len(journal),2)
        self.assertEqual({t['policy'] for t in journal},{'breakout-1','pullback-1'})
        original = deepcopy(journal)
        self.assertEqual(update_journal(journal,[a,b],{},'us',NOW),original)
        html = ledger_html(journal)
        self.assertIn('强势回踩',html)
        self.assertIn('不能合并',html)

    def test_old_open_position_cannot_be_advertised_as_a_new_entry(self):
        old = dict(plan(),state='OPEN',id='original-plan')
        candidate = plan()
        annotate_existing_positions([candidate],[old])
        self.assertEqual(candidate['status'],'TRACKING')
        self.assertEqual(candidate['tracked_id'],'original-plan')
        current = plan()
        annotate_existing_positions([current],[dict(current,state='PENDING')])
        self.assertEqual(current['status'],'PENDING')


if __name__ == '__main__':
    unittest.main()
