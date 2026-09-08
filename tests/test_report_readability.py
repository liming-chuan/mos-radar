"""Display changes must preserve signal meaning and handle saved CSV nulls."""
import unittest
import pandas as pd
from pandas.testing import assert_frame_equal
from test_safety import good_row, NOW, o
from quality_watch import annotate_watchlists, watch_html
from report import generate_report
from report_readability import local_time, overview_html


class ReadabilityTests(unittest.TestCase):
    def frame(self):
        frame = o.annotate_opportunities(pd.DataFrame([
            good_row(ticker='WAIT', roe=.2, company_name='Waiting Company'),
            good_row(ticker='TREND', roe=.2, company_name='Trend Company'),
            good_row(ticker='EXPENSIVE', roe=.2, price=120, market_cap=2400),
        ]), now=NOW)
        frame = annotate_watchlists(frame, now=NOW)
        frame.loc[frame.ticker.eq('TREND'), ['trend_status', 'trend_asof', 'trend_relative_6m']] = ['TREND_WATCH', '2026-09-04T00:00:00Z', .2]
        frame.loc[frame.ticker.eq('WAIT'), 'trend_status'] = 'TREND_WAIT'
        frame.loc[frame.ticker.eq('EXPENSIVE'), 'trend_asof'] = float('nan')
        return frame

    def test_watch_leads_with_trend_and_never_prints_nan(self):
        body = watch_html(self.frame())
        self.assertLess(body.index('TREND</b>'), body.index('WAIT</b>'))
        self.assertNotIn('nan', body)
        self.assertNotIn('T00:00', body)
        self.assertIn('2026-09-04', body)
        self.assertIn('等待价格', body)

    def test_report_does_not_mutate_saved_signals(self):
        frame = self.frame()
        before = frame.copy(deep=True)
        body = generate_report(frame, 'manual', market='hk')
        assert_frame_equal(frame, before)
        self.assertIn('合理估值观察', body)
        self.assertIn('研究上限与严格触发上限属于不同策略', body)

    def test_local_time_missing_and_new_york_dst(self):
        self.assertEqual(local_time(float('nan')), '未提供')
        self.assertEqual(local_time('2026-09-08T04:54:43Z'), '2026-09-08 12:54')
        self.assertEqual(local_time('2026-09-08T04:54:43Z','us'), '2026-09-08 00:54')
        self.assertEqual(local_time('2026-01-08T04:54:43Z','us'), '2026-01-07 23:54')

    def test_partial_and_legacy_are_not_complete_zero_opportunities(self):
        body = overview_html(pd.DataFrame([good_row(scan_status='PARTIAL_SOURCE_FAILURE')]))
        self.assertIn('缺少严格入场评估', body)
        self.assertIn('扫描中断', body)
        self.assertIn('未评估', body)

    def test_historical_no_current_watch_pool(self):
        frame = self.frame()
        frame['is_historical_replay'] = True
        frame['backtest_date'] = '2020-03-20'
        body = generate_report(frame, 'historical_replay')
        self.assertNotIn('02 · 合理估值与趋势观察', body)
        self.assertIn('未来函数', body)

    def test_private_holdings_not_in_overview(self):
        frame = self.frame()
        frame['is_holding'] = frame.ticker.eq('TREND')
        body = generate_report(frame, 'manual')
        overview = body.split('<div class="card overview">',1)[1].split('01 ·',1)[0]
        self.assertNotIn('Trend Company', overview)

    def test_names_and_review_reason_are_escaped(self):
        frame = self.frame()
        frame.loc[0, ['company_name','trend_event','trend_reason']] = ['<script>bad</script>', 'DATA_REVIEW', '<img src=x onerror=bad>']
        body = watch_html(frame)
        self.assertNotIn('<script>', body)
        self.assertNotIn('<img', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertIn('&lt;img', body)
