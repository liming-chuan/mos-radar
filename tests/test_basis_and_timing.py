import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from test_safety import main, good_row
from test_statement_evidence import record
from statement_evidence import supplement_annual, PATH
from report_timing import premarket_title, hk_time
from cashflow_diagnostics import sbc_coverage
from report import generate_report


class BasisAndTimingTests(unittest.TestCase):
    def test_consolidated_and_attributable_profits_are_separate(self):
        r = record()
        r['values'].pop('net_income')
        r['values'].update(net_income_attributable=80., net_income_consolidated=82.)
        inc = pd.DataFrame({pd.Timestamp('2025-12-31'): {'Net Income': 80.}})
        out, _, audit = supplement_annual('TEST.HK', 'CNY', inc, None, records=[r], now='2026-09-07')
        self.assertEqual(audit['status'], 'SUPPLEMENTED')
        self.assertEqual(out.loc['Net Income'].iloc[0], 80.)
        self.assertEqual(out.loc['Net Income Including Noncontrolling Interests'].iloc[0], 82.)

    def test_real_filing_attributable_profits_match_without_group_conflict(self):
        records = json.loads(PATH.read_text(encoding='utf-8'))
        inc = pd.DataFrame({pd.Timestamp('2025-03-31'): {'Net Income': 4335565000.},
                            pd.Timestamp('2024-03-31'): {'Net Income': 3990474000.}})
        out, _, audit = supplement_annual('0151.HK', 'CNY', inc, None, records=records, now='2026-09-08')
        self.assertEqual(audit['status'], 'SUPPLEMENTED')
        self.assertEqual(out.loc['Net Income Including Noncontrolling Interests'].iloc[0], 4328415000.)

    def test_true_conflict_keeps_both_values_for_review(self):
        inc = pd.DataFrame({pd.Timestamp('2025-12-31'): {'Net Income': 90.}})
        _, _, audit = supplement_annual('TEST.HK', 'CNY', inc, None, records=[record()], now='2026-09-07')
        self.assertEqual(audit['status'], 'REJECTED')
        self.assertEqual(audit['conflicts'][0]['provider'], 90.)
        self.assertEqual(audit['conflicts'][0]['filing'], 80.)

    def test_sbc_missing_table_and_line_are_distinct(self):
        self.assertEqual(sbc_coverage(None)[0], 'STATEMENT_UNAVAILABLE')
        frame = pd.DataFrame({pd.Timestamp('2025-12-31'): {'Operating Cash Flow': 100.}})
        self.assertEqual(sbc_coverage(frame)[0], 'LINE_MISSING')
        frame.loc['Stock Based Compensation'] = float('nan')
        self.assertEqual(sbc_coverage(frame)[0], 'VALUES_MISSING')

    def test_sbc_partial_history_keeps_dates(self):
        dates = pd.to_datetime(['2025-12-31', '2024-12-31', '2023-12-31'])
        frame = pd.DataFrame([[100.,100.,100.], [1.,float('nan'),0.]],
                             index=['Operating Cash Flow','Stock Based Compensation'], columns=dates)
        status, missing = sbc_coverage(frame)
        self.assertEqual(status, 'PARTIAL_HISTORY')
        self.assertEqual(missing, '2024-12-31')

    def test_sbc_explicit_zero_and_padding(self):
        dates = pd.to_datetime(['2025-12-31', '2024-12-31', '2023-12-31', '2022-12-31'])
        frame = pd.DataFrame([[0.,0.,0.,float('nan')]], index=['Stock Based Compensation'], columns=dates)
        self.assertEqual(sbc_coverage(frame)[0], 'AVAILABLE')
        self.assertEqual(sbc_coverage(frame.iloc[:,:2])[0], 'INSUFFICIENT_HISTORY')

    def test_premarket_boundary_and_other_modes(self):
        before = datetime(2026,9,7,1,29,tzinfo=timezone.utc)
        after = datetime(2026,9,7,1,30,tzinfo=timezone.utc)
        self.assertIsNone(premarket_title('premarket_scan','hk',before))
        self.assertIn('延迟',premarket_title('premarket_scan','hk',after))
        self.assertIsNone(premarket_title('manual','hk',after))
        self.assertIsNone(premarket_title('premarket_scan','us',after))

    def test_subject_uses_hk_date_and_late_label(self):
        with patch.object(main,'MARKET','hk'):
            self.assertIn('延迟',main.subject_for('premarket_scan',datetime(2026,9,7,6,34,tzinfo=timezone.utc)))
            self.assertIn('2026-09-08',main.subject_for('manual',datetime(2026,9,7,17,tzinfo=timezone.utc)))
        with self.assertRaises(ValueError):
            hk_time(datetime(2026,9,7))

    def test_report_displays_delay_duration_and_sbc_classification(self):
        row = good_row(scan_started_at='2026-09-07T05:39:00Z', scan_duration_seconds=3300,
                       sbc_coverage_status='LINE_MISSING')
        with patch('report_timing.hk_time',return_value=datetime(2026,9,7,14,34)):
            body = generate_report(pd.DataFrame([row]),'premarket_scan',market='hk')
        self.assertIn('延迟安全边际扫描',body)
        self.assertIn('55.0 分钟',body)
        self.assertIn('未返回SBC项目 1只',body)

    def test_new_schedule_maps_to_full_scan(self):
        self.assertEqual(main.SCHEDULE_MODE_MAP['7 0 * * 1-5'],'premarket_scan')


if __name__ == '__main__':
    unittest.main()
