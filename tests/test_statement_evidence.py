import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict

import pandas as pd

from test_safety import v, o, main, good_row, FakeTicker, NOW
import statement_evidence as e


def record():
    return dict(ticker='TEST.HK', currency='CNY', period_start='2025-01-01', period_end='2025-12-31',
                published_at='2026-03-31', reviewed_at='2026-09-04', reviewed_by='test fixture',
                source_url='https://www.hkexnews.hk/fixture.pdf', source_pages='100-102',
                verified=True, unit=1, scope='consolidated',
                values=dict(revenue=1000., net_income=80., operating_income=200.,
                            operating_cash_flow=100., capital_expenditure=-20., sbc=5.))


class EvidenceTests(unittest.TestCase):
    def test_checked_in_want_want_records_preserve_unknown_sbc(self):
        records = json.loads(e.PATH.read_text(encoding='utf-8'))
        inc, cf, audit = e.supplement_annual('0151.HK', 'CNY', None, None,
                                            now='2026-09-07', records=records)
        self.assertEqual(audit['status'], 'SUPPLEMENTED')
        self.assertEqual(cf.loc['Operating Cash Flow', pd.Timestamp('2025-03-31')], 4161620000)
        self.assertEqual(cf.loc['Capital Expenditure', pd.Timestamp('2025-03-31')], 640573000)
        self.assertNotIn('Stock Based Compensation', cf.index)

    def apply(self, records, income=None, cashflow=None):
        return e.supplement_annual('TEST.HK', 'CNY', income, cashflow, now=NOW, records=records)

    def test_empty_provider_restored_with_traceable_values(self):
        inc, cf, audit = self.apply([record()])
        reported, owner = v.build_owner_fcf_series(cf.loc['Operating Cash Flow'],
                          cf.loc['Capital Expenditure'], cf.loc['Stock Based Compensation'])
        self.assertEqual(owner.iloc[0], 75.)
        self.assertEqual(audit['status'], 'SUPPLEMENTED')
        self.assertEqual(audit['sources'][0]['source_pages'], '100-102')

    def test_conflict_rolls_back_all_changes(self):
        inc = pd.DataFrame({pd.Timestamp('2025-12-31'): {'Net Income': 99.}})
        out, cf, audit = self.apply([record()], inc)
        pd.testing.assert_frame_equal(out, inc)
        self.assertIsNone(cf)
        self.assertEqual(audit['status'], 'REJECTED')
        self.assertEqual(audit['filled'], [])

    def test_missing_sbc_is_never_assumed_zero(self):
        r = record()
        del r['values']['sbc']
        _, cf, audit = self.apply([r])
        self.assertEqual(audit['status'], 'SUPPLEMENTED')
        self.assertNotIn('Stock Based Compensation', cf.index)
        _, owner = v.build_owner_fcf_series(cf.loc['Operating Cash Flow'], cf.loc['Capital Expenditure'], None)
        self.assertTrue(owner.isna().all())

    def test_review_currency_units_and_source_validation(self):
        for key, bad in [('verified', False), ('currency', 'HKD'), ('unit', 1000),
                         ('reviewed_by', ''), ('source_pages', ''), ('scope', 'parent'),
                         ('source_url', 'https://www.hkexnews.hk.evil.example/a.pdf')]:
            r = record()
            r[key] = bad
            with self.subTest(key=key):
                self.assertEqual(self.apply([r])[2]['status'], 'REJECTED')

    def test_half_year_future_and_duplicate_periods_rejected(self):
        for key, bad in [('period_start', '2025-07-01'), ('published_at', '2027-01-01'),
                         ('reviewed_at', '2025-01-01')]:
            r = record()
            r[key] = bad
            self.assertEqual(self.apply([r])[2]['status'], 'REJECTED')
        self.assertEqual(self.apply([record(), record()])[2]['status'], 'REJECTED')

    def test_nonfinite_and_boolean_amounts_rejected(self):
        for bad in [float('nan'), float('inf'), True, '100']:
            r = record()
            r['values']['sbc'] = bad
            self.assertEqual(self.apply([r])[2]['status'], 'REJECTED')

    def test_explicit_zero_and_negative_cashflow_preserved(self):
        r = record()
        r['values'].update(sbc=0., operating_cash_flow=-100.)
        _, cf, audit = self.apply([r])
        self.assertEqual(audit['status'], 'SUPPLEMENTED')
        self.assertEqual(cf.loc['Operating Cash Flow'].iloc[0], -100.)
        self.assertEqual(cf.loc['Stock Based Compensation'].iloc[0], 0.)

    def test_existing_values_match_and_other_ticker_untouched(self):
        inc, cf, _ = self.apply([record()])
        self.assertEqual(self.apply([record()], inc, cf)[2]['status'], 'MATCHED')
        r = record()
        r['ticker'] = 'OTHER.HK'
        self.assertEqual(self.apply([r])[2]['status'], 'NONE')

    def test_conflicting_evidence_and_changed_hash_block_entry(self):
        r = good_row(statement_evidence_status='REJECTED')
        self.assertEqual(o.evaluate_entry(r, NOW)['entry_status'], 'DATA_REQUIRED')
        r = good_row(ticker='TEST.HK', statement_evidence_fingerprint='old')
        self.assertIn('公告补录版本变化', o.evaluate_entry(r, NOW)['entry_data_issues'])

    def test_cache_invalidated_after_evidence_change(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, 'CACHE_DIR', Path(tmp)), \
             patch.object(e, 'PATH', Path(tmp)/'evidence.json'):
            e.PATH.write_text('[]', encoding='utf-8')
            main.save_cached_analysis('TEST.HK', good_row(ticker='TEST.HK'))
            self.assertIsNotNone(main.load_cached_analysis('TEST.HK'))
            e.PATH.write_text('[ ]', encoding='utf-8')
            self.assertIsNone(main.load_cached_analysis('TEST.HK'))

    def test_hk_pipeline_supplements_before_fx_and_keeps_history_gate(self):
        ticker = FakeTicker('HKD', 'CNY')
        ticker.cashflow = pd.DataFrame()
        ticker.quarterly_cashflow = pd.DataFrame()
        r = record()
        r['ticker'] = 'TEST'
        r['values'].update(revenue=1e9, net_income=8e7, operating_income=2e8,
                           operating_cash_flow=1e8, capital_expenditure=-2e7, sbc=5e6)
        with tempfile.TemporaryDirectory() as tmp, patch.object(e, 'PATH', Path(tmp)/'evidence.json'), \
             patch.object(v.yf, 'Ticker', return_value=ticker), patch.object(v, 'current_market', return_value='hk'), \
             patch.object(v, 'latest_download_price_volume', return_value=(20., 1e6)), \
             patch.object(v, 'get_risk_free_rate', return_value=.04), patch.object(v, 'get_feedback_map', return_value={}), \
             patch.object(v, 'get_fx_rate', return_value=1.1):
            e.PATH.write_text(json.dumps([r]), encoding='utf-8')
            result = v.analyze_ticker('TEST', sleep_seconds=0)
        self.assertNotEqual(result.rating, 'ERROR', result.reason)
        self.assertEqual(result.annual_cashflow_status, 'UNAVAILABLE')
        self.assertEqual(result.statement_evidence_status, 'SUPPLEMENTED')
        self.assertAlmostEqual(result.fcf_ttm, 82.5e6)
        self.assertEqual(result.fcf_history_years, 1)
        self.assertFalse(result.sbc_history_complete)
        self.assertNotIn(o.evaluate_entry(asdict(result), NOW)['entry_status'], o.ENTRY_STATES)

    def test_diagnostics_include_missing_periods_and_provenance(self):
        r = good_row(annual_cashflow_missing='SBC缺失@2025-12-31', statement_evidence_audit='source')
        df = main.build_data_quality_diagnostics(pd.DataFrame([r]))
        self.assertIn('2025-12-31', df.iloc[0]['annual_cashflow_missing'])
        self.assertEqual(df.iloc[0]['statement_evidence_audit'], 'source')


if __name__ == '__main__':
    unittest.main()
