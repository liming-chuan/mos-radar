"""Regressions reproduced from the September 5/6 scan reports."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import test_safety
from test_safety import v, o, main, good_row, valuation_args, FakeTicker, NOW
from report import generate_report, discount_label, distance_label, translate_tokens


class EvidenceRegressions(unittest.TestCase):
    def stamp(self, owner, reported, sbc, dates=None):
        dates = pd.to_datetime(dates or ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"])
        series = lambda x: pd.Series(x, index=dates)
        r = v.AnalysisResult("TEST")
        v.stamp_fundamental_evidence(r, series(owner), series(owner), series(reported), series(sbc), None, series(reported))
        return r

    def test_provider_empty_tail_is_not_missing_sbc(self):
        r = self.stamp([90]*4+[np.nan], [100]*4+[np.nan], [10]*4+[np.nan])
        self.assertTrue(r.sbc_history_complete)
        self.assertEqual(r.fcf_history_years, 4)

    def test_real_missing_sbc_at_any_year_stays_blocked(self):
        for position in (0, 2, 4):
            owner, sbc = [90]*5, [10]*5
            owner[position] = sbc[position] = np.nan
            self.assertFalse(self.stamp(owner, [100]*5, sbc).sbc_history_complete)

    def test_missing_annual_period_is_not_hidden(self):
        dates = ["2025-12-31", "2024-12-31", "2022-12-31", "2021-12-31", "2020-12-31"]
        self.assertFalse(self.stamp([90]*5, [100]*5, [10]*5, dates).sbc_history_complete)

    def test_padding_fix_reaches_analysis_pipeline(self):
        ticker = FakeTicker()
        ticker.cashflow[pd.Timestamp("2021-12-31")] = np.nan
        result = test_safety.PipelineTests().analyze(ticker)
        self.assertTrue(result.sbc_history_complete, result.reason)
        row = asdict(result)
        row["fundamentals_asof"] = NOW
        self.assertIn(o.evaluate_entry(row, NOW)["entry_status"], o.ENTRY_STATES)

    def test_small_asset_reference_does_not_cap_going_concern(self):
        cfg = v.sector_config("Industrials", "Specialty Industrial Machinery")
        args = valuation_args(cfg=cfg)
        baseline = v.estimate_intrinsic_value(**args)[0]
        args.update(ncav=1., tangible_equity=1.)
        value, method, details = v.estimate_intrinsic_value(**args)
        self.assertEqual(value, baseline)
        self.assertNotIn(method, {"ncav_2_3", "tangible_book_0_8x"})
        self.assertIn("ncav_2_3=", details)

    def test_zero_operating_value_not_rescued_by_asset_reference(self):
        args = valuation_args(cfg=v.sector_config("Industrials", "Machinery"), debt=10000., ncav=1000., tangible_equity=2000.)
        self.assertEqual(v.estimate_intrinsic_value(**args)[0], 0)

    def test_asset_only_requires_special_review(self):
        for method in ("ncav_2_3", "tangible_book_0_8x_holding_0.80x"):
            result = o.evaluate_entry(good_row(valuation_method=method), NOW)
            self.assertEqual(result["entry_status"], "SPECIAL_REVIEW")
            self.assertIsNone(result["entry_price"])

    def test_risk_and_missing_evidence_are_both_recorded(self):
        result = o.evaluate_entry(good_row(trap_count=1, sbc_history_complete=False, fcf_volatility=None), NOW)
        self.assertEqual(result["entry_status"], "RISK_BLOCKED")
        self.assertIn("SBC", result["entry_data_issues"])
        self.assertIn("价值陷阱", result["entry_risk_issues"])
        self.assertEqual(result["entry_reason"].count("波动"), 1)
        self.assertIsNone(result["entry_price"])

    def test_policy_version_survives_csv_float_inference(self):
        old = o.annotate_opportunities(pd.DataFrame([good_row(price=80, market_cap=1600)]), now=NOW)
        old["entry_policy_version"] = float(old.iloc[0].entry_policy_version)
        new = o.annotate_opportunities(pd.DataFrame([good_row()]), previous=old, now=NOW)
        self.assertEqual(new.iloc[0].entry_event, "PRICE_ENTERED")


class ReportRegressions(unittest.TestCase):
    def test_manual_never_shows_replay_column_from_empty_dataclass_field(self):
        df = o.annotate_opportunities(pd.DataFrame([good_row()]), now=NOW)
        body = generate_report(df, "manual")
        self.assertNotIn("回放日至今", body)
        self.assertIn('class="metrics"', body)
        self.assertIn("严格入场复核", body)
        self.assertNotIn("安全边际较厚候选 Top", body)

    def test_report_preserves_real_replay_return(self):
        df = pd.DataFrame([good_row(is_historical_replay=True, return_since_backtest=.2)])
        self.assertIn("回放日至今", generate_report(df, "historical_replay"))

    def test_price_direction_and_negative_discount_are_clear(self):
        self.assertEqual(discount_label(-1.5), "溢价 150.0%")
        self.assertEqual(distance_label(-.7), "仍需下跌 70.0%（距离较远）")
        self.assertEqual(distance_label(None), "未生成触发价")
        self.assertEqual(translate_tokens("revenue_decline_streak"), "收入连续下滑")

    def test_both_markets_keep_currency_and_escape_reasons(self):
        for market, symbol in (("us", "$50.00"), ("hk", "HK$50.00")):
            df = o.annotate_opportunities(pd.DataFrame([good_row(company_name="<script>bad</script>")]), now=NOW)
            df["entry_reason"] = '<img src=x onerror="bad">'
            body = generate_report(df, "manual", market=market)
            self.assertIn(symbol, body)
            self.assertNotIn("<script>bad", body)
            self.assertNotIn('<img src=x', body)


class StatePersistenceRegressions(unittest.TestCase):
    def test_partial_scan_report_cannot_overwrite_public_state_or_be_reloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public.csv"
            original = pd.DataFrame([good_row(ticker="PREVIOUS", scan_status="COMPLETE")])
            original.to_csv(public, index=False)
            original_bytes = public.read_bytes()
            partial = pd.DataFrame([good_row(scan_status="PARTIAL_SOURCE_FAILURE")])
            with patch.multiple(main, STATE_MARKET_PATH=public, RESULTS_PATH=root/"latest.csv",
                                REPORTS_DIR=root/"reports", DIAGNOSTICS_PATH=root/"diagnostics.csv", SNAPSHOT_PATH=root/"snapshot.csv"), \
                 patch.object(main, "annotate_pools", side_effect=lambda x: x), \
                 patch.object(main, "save_entry_history") as journal:
                main.save_outputs(partial)
                main.save_report_files(partial, "manual", generate_report(partial, "manual"))
                self.assertEqual(public.read_bytes(), original_bytes)
                journal.assert_not_called()
                self.assertEqual(main.load_public_market_state().ticker.tolist(), ["PREVIOUS"])


if __name__ == "__main__":
    unittest.main()
