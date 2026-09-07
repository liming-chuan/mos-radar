import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
import numpy as np
import pandas as pd
from test_safety import good_row, NOW
from quality_watch import annotate_watchlists, evaluate_quality, enrich_watchlists, save_watch_history, watch_html
from trend_evidence import compute_trend, fetch_trend_evidence

class WatchTests(unittest.TestCase):
    def row(self, **kwargs):
        return good_row(roe=.2, **kwargs)

    def test_quality_is_separate_from_strict_price(self):
        row = self.row(price=80, market_cap=1600)
        self.assertEqual(evaluate_quality(row, NOW)['qv_status'], 'QUALITY_WATCH')

    def test_missing_sbc_stays_blocked(self):
        self.assertEqual(evaluate_quality(self.row(sbc_history_complete=False), NOW)['qv_status'], 'QUALITY_DATA')

    def test_low_roe_blocked(self):
        row=self.row();row['roe']=.05
        self.assertEqual(evaluate_quality(row, NOW)['qv_status'], 'QUALITY_RISK')

    def test_price_limit_uses_weakest_cashflow(self):
        out=evaluate_quality(self.row(), NOW)
        self.assertAlmostEqual(out['qv_price_limit'],90)

    def test_historical_no_realtime_limit(self):
        out=evaluate_quality(self.row(is_historical_replay=True), NOW)
        self.assertEqual(out['qv_status'],'HISTORICAL_ONLY')
        self.assertIsNone(out['qv_price_limit'])

    def test_stale_saved_trend_cleared(self):
        frame=pd.DataFrame([self.row(trend_status='TREND_WATCH',trend_ma200=1)])
        out=annotate_watchlists(frame, now=NOW).iloc[0]
        self.assertEqual(out.trend_status,'TREND_DATA')
        self.assertTrue(pd.isna(out.trend_ma200))

    def evidence(self):
        dates=pd.bdate_range(end='2026-09-04',periods=230)
        return compute_trend(pd.Series(np.linspace(50,100,230),dates),pd.Series(np.linspace(90,100,230),dates),now=NOW)

    def test_trend_and_event(self):
        frame=pd.DataFrame([self.row()])
        first=annotate_watchlists(frame,evidence={'TEST':self.evidence()},now=NOW)
        self.assertEqual(first.iloc[0].trend_status,'TREND_WATCH')
        out=annotate_watchlists(frame,previous=first,now=NOW)
        self.assertEqual(out.iloc[0].trend_event,'DATA_REVIEW')

    def test_short_or_stale_history_blocked(self):
        for end,n in [('2026-09-04',100),('2026-07-31',220)]:
            s=pd.Series(np.arange(n)+10,pd.bdate_range(end=end,periods=n))
            self.assertEqual(compute_trend(s,s,now=NOW)['status'],'MISSING')

    def test_current_day_excluded(self):
        dates=pd.bdate_range(end='2026-09-07',periods=230)
        s=pd.Series(np.arange(230)+10,dates)
        out=compute_trend(s,s,market='hk',now='2026-09-07T03:00:00Z')
        self.assertTrue(out['trend_asof'].startswith('2026-09-04'))

    def test_missing_benchmark_latest_blocked(self):
        dates=pd.bdate_range(end='2026-09-04',periods=230)
        s=pd.Series(np.arange(230)+10,dates)
        self.assertEqual(compute_trend(s,s.iloc[:-1],now='2026-09-05T12:00:00Z')['status'],'MISSING')

    def test_empty_provider_stops_requests(self):
        with patch('trend_evidence.yf.download',return_value=pd.DataFrame()) as download:
            out=fetch_trend_evidence([str(x) for x in range(120)],now=NOW)
        self.assertEqual(download.call_count,1)
        self.assertEqual(len(out),120)
        self.assertEqual(out['110']['status'],'LIMIT')

    def test_ineligible_does_not_fetch(self):
        with patch('trend_evidence.fetch_trend_evidence') as fetch:
            enrich_watchlists(pd.DataFrame([self.row(sbc_history_complete=False)]),now=NOW)
        fetch.assert_not_called()

    def test_journal_idempotent(self):
        frame=annotate_watchlists(pd.DataFrame([self.row()]),now=NOW)
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'history.csv'
            save_watch_history(frame,p);save_watch_history(frame,p)
            self.assertEqual(len(pd.read_csv(p)),1)

    def test_report_escapes_ticker(self):
        frame=annotate_watchlists(pd.DataFrame([self.row(ticker='<script>')]),now=NOW)
        body=watch_html(frame)
        self.assertNotIn('<script>',body)
        self.assertIn('&lt;script&gt;',body)
