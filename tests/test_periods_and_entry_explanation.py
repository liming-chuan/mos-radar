import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch

import pandas as pd
import numpy as np

from test_safety import v, o, good_row, valuation_args, FakeTicker, NOW
from statement_periods import aligned_trailing_values
from report import generate_report, display_method


def trailing_frames(date="2026-06-30"):
    # Explicit trailing totals, deliberately different from the annual fixture.
    dates = pd.to_datetime([date])
    income = pd.DataFrame({dates[0]: {"Total Revenue": 1.2e9, "Net Income": 9e7,
                          "Operating Income": 2.2e8, "Gross Profit": 4.8e8,
                          "EBITDA": 2.3e8, "Interest Expense": 1e6}})
    cf = pd.DataFrame({dates[0]: {"Operating Cash Flow": 1.5e8,
                      "Capital Expenditure": -3e7, "Stock Based Compensation": 1e7}})
    return income, cf


class TrailingStatementTests(unittest.TestCase):
    def test_provider_trailing_totals_used_once(self):
        result, status = aligned_trailing_values(*trailing_frames(), now=NOW)
        self.assertEqual(status, "PROVIDER_TRAILING")
        self.assertEqual(result["owner_fcf"], 110e6)
        self.assertEqual(result["reported_fcf"], 120e6)

    def test_mismatched_latest_dates_rejected(self):
        inc, cf = trailing_frames()
        cf.columns = pd.to_datetime(["2026-03-31"])
        self.assertEqual(aligned_trailing_values(inc, cf, now=NOW)[1], "TRAILING_PERIOD_MISMATCH")

    def test_sbc_missing_cannot_be_replaced_with_annual_value(self):
        inc, cf = trailing_frames()
        cf = cf.drop("Stock Based Compensation")
        self.assertIsNone(aligned_trailing_values(inc, cf, now=NOW)[0])

    def test_missing_latest_data_never_backfilled_from_old_column(self):
        inc, cf = trailing_frames()
        cf[pd.Timestamp("2026-03-31")] = cf.iloc[:, 0]
        cf.loc["Stock Based Compensation", pd.Timestamp("2026-06-30")] = np.nan
        self.assertIsNone(aligned_trailing_values(inc, cf, now=NOW)[0])

    def test_future_stale_and_duplicate_periods_rejected(self):
        for date in ("2027-06-30", "2024-06-30"):
            self.assertIsNone(aligned_trailing_values(*trailing_frames(date), now=NOW)[0])
        inc, cf = trailing_frames()
        self.assertIsNone(aligned_trailing_values(pd.concat([inc, inc], axis=1), cf, now=NOW)[0])

    def test_same_year_end_not_promoted_to_fresher_ttm(self):
        self.assertEqual(aligned_trailing_values(*trailing_frames("2025-12-31"),
                         not_before="2025-12-31", now="2026-02-01")[1], "TRAILING_NOT_NEWER_THAN_ANNUAL")

    def test_losses_preserved(self):
        inc, cf = trailing_frames()
        cf.loc["Operating Cash Flow"] = 1e7
        self.assertEqual(aligned_trailing_values(inc, cf, now=NOW)[0]["owner_fcf"], -3e7)

    def fake(self):
        ticker = FakeTicker("HKD", "CNY")
        ticker.quarterly_cashflow = pd.DataFrame()
        inc, cf = trailing_frames()
        ticker.get_income_stmt = Mock(return_value=inc)
        ticker.get_cash_flow = Mock(return_value=cf)
        return ticker

    def analyze(self, ticker):
        validate = lambda inc, cf, **kw: aligned_trailing_values(inc, cf, now=NOW, **kw)
        with patch.object(v.yf, "Ticker", return_value=ticker), patch.object(v, "get_risk_free_rate", return_value=.04), \
             patch.object(v, "get_feedback_map", return_value={}), patch.object(v, "current_market", return_value="hk"), \
             patch.object(v, "latest_download_price_volume", return_value=(20., 1e6)), \
             patch.object(v, "get_fx_rate", return_value=1.1), \
             patch("statement_periods.aligned_trailing_values", side_effect=validate):
            return v.analyze_ticker("TEST", sleep_seconds=0)

    def test_hk_fallback_pipeline_records_source_and_fx_once(self):
        ticker = self.fake()
        result = self.analyze(ticker)
        self.assertEqual(result.financial_period_type, "TTM", result.reason)
        self.assertEqual(result.financial_period_source, "PROVIDER_TRAILING")
        self.assertEqual(result.financial_asof, "2026-06-30")
        self.assertAlmostEqual(result.fcf_ttm, 121e6)
        self.assertEqual(result.shares_outstanding, 2e7)
        self.assertTrue(result.sbc_history_complete)
        ticker.get_cash_flow.assert_called_once_with(freq="trailing", pretty=True)

    def test_failed_fallback_keeps_annual_evidence_and_no_fake_ttm(self):
        ticker = self.fake()
        ticker.get_cash_flow.return_value = pd.DataFrame()
        result = self.analyze(ticker)
        self.assertEqual(result.financial_period_type, "ANNUAL_FALLBACK")
        self.assertEqual(result.financial_period_source, "ANNUAL")
        self.assertEqual(result.trailing_fetch_status, "TRAILING_UNAVAILABLE")
        self.assertAlmostEqual(result.fcf_ttm, 82.5e6)

    def test_fallback_rate_limit_stops_further_requests(self):
        ticker = self.fake()
        ticker.get_cash_flow.side_effect = v.YahooRateLimitError("limited")
        result = self.analyze(ticker)
        self.assertEqual(result.price_data_status, "YAHOO_RATE_LIMIT")
        ticker.get_income_stmt.assert_not_called()

    def test_good_quarters_do_not_request_another_data_source(self):
        ticker = self.fake()
        ticker.quarterly_cashflow = FakeTicker().quarterly_cashflow
        self.assertEqual(self.analyze(ticker).financial_period_source, "QUARTER_SUM")
        ticker.get_cash_flow.assert_not_called()

    def test_new_ttm_does_not_replace_missing_annual_sbc_evidence(self):
        ticker = self.fake()
        ticker.cashflow = ticker.cashflow.drop("Stock Based Compensation")
        result = self.analyze(ticker)
        self.assertEqual(result.financial_period_source, "PROVIDER_TRAILING")
        self.assertFalse(result.sbc_history_complete)
        result.fundamentals_asof = NOW
        self.assertNotIn(o.evaluate_entry(asdict(result), NOW)["entry_status"], o.ENTRY_STATES)

    def test_missing_trailing_debt_metrics_do_not_borrow_annual_values(self):
        ticker = self.fake()
        ticker.get_income_stmt.return_value = ticker.get_income_stmt.return_value.drop(
            ["EBITDA", "Interest Expense", "Gross Profit"])
        result = self.analyze(ticker)
        self.assertEqual(result.financial_period_source, "PROVIDER_TRAILING")
        self.assertIsNone(result.ebitda)
        self.assertIsNone(result.interest_coverage)
        self.assertIsNone(result.gross_margin)
        entry = o.evaluate_entry(good_row(cash=100., total_debt=200.,
                                ebitda=result.ebitda, interest_coverage=result.interest_coverage), NOW)
        self.assertEqual(entry["entry_status"], "DATA_REQUIRED")
        self.assertIsNone(entry["entry_price"])


class NormalizationAndExplanationTests(unittest.TestCase):
    def test_holding_discount_does_not_replace_cashflow_multiple_in_label(self):
        self.assertEqual(display_method("cycle_normalized_fcf_8x_holding_0.75x"),
                         "周期正常化现金流 8倍，控股/投资资产折价 0.75倍")

    def test_industrials_use_historical_lower_base_without_fixed_forty_percent_cut(self):
        args = valuation_args(cfg=v.sector_config("Industrials", "Machinery"))
        value, method, _ = v.estimate_intrinsic_value(**args)
        self.assertEqual(value, 80*8+200-100)
        self.assertEqual(method, "cycle_normalized_fcf_8x")
        cfg = args.pop("cfg")
        self.assertLess(v.estimate_stress_value(cfg, **args), value)

    def test_commodity_multiple_remains_lower_than_industrial(self):
        mine = v.sector_config("Basic Materials", "Gold")
        self.assertEqual(v.estimate_intrinsic_value(**valuation_args(cfg=mine))[1], "cycle_normalized_fcf_4x")

    def test_cyclical_negative_cashflow_still_vetoes(self):
        value, _, _ = v.estimate_intrinsic_value(**valuation_args(
            cfg=v.sector_config("Industrials", "Machinery"), latest_fcf=-1., cash=0., debt=100.))
        self.assertEqual(value, 0)

    def test_binding_condition_matches_minimum_and_clears_when_blocked(self):
        result = o.evaluate_entry(good_row(), NOW)
        self.assertEqual(result["entry_binding_constraint"], "现金流收益率")
        self.assertEqual(result["entry_price"], min(result[k] for k in
                         ("entry_value_ceiling", "entry_stress_ceiling", "entry_yield_ceiling")))
        result = o.evaluate_entry(good_row(sbc_history_complete=False), NOW)
        self.assertEqual(result["entry_binding_constraint"], "")
        self.assertIsNone(result["entry_yield_ceiling"])

    def test_tied_constraints_and_stress_binding_are_explained(self):
        result = o.evaluate_entry(good_row(stress_value_per_share=50.), NOW)
        self.assertEqual(result["entry_binding_constraint"], "压力情景折价")
        result = o.evaluate_entry(good_row(stress_value_per_share=65/.9, fcf_ttm=150.), NOW)
        self.assertEqual(result["entry_binding_constraint"], "保守价值折价、压力情景折价")

    def test_negative_mean_is_risk_not_missing_data(self):
        s = pd.Series([-5., 1., -2.])
        self.assertEqual(v.volatility_evidence_status(s), "NONPOSITIVE_MEAN")
        result = o.evaluate_entry(good_row(fcf_volatility=None, fcf_volatility_status="NONPOSITIVE_MEAN"), NOW)
        self.assertIn("历史平均现金流非正", result["entry_risk_issues"])
        self.assertNotIn("波动", result["entry_data_issues"])

    def test_short_report_keeps_all_signal_rows_in_dataframe(self):
        rows = [good_row(ticker=f"FAR_{i}", price=300., market_cap=6000.) for i in range(12)]
        df = o.annotate_opportunities(pd.DataFrame(rows), now=NOW)
        body = generate_report(df, "manual")
        self.assertIn("没有处于触发价上方", body)
        self.assertIn("其余等待名单", body)
        self.assertIn("10 / 12", body)
        self.assertNotIn('class="stock"', body)
        self.assertEqual(len(df), 12)

    def test_near_watch_shows_limit_breakdown_and_cashflow_date(self):
        row = good_row(price=70, market_cap=1400, financial_period_source="QUARTER_SUM", financial_period_note="四季合计")
        body = generate_report(o.annotate_opportunities(pd.DataFrame([row]), now=NOW), "manual")
        self.assertIn("三个上限取最低", body)
        self.assertIn("2026-06-30", body)
        self.assertIn("当前限制", body)


if __name__ == "__main__":
    unittest.main()
