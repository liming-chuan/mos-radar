import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import valuation as v
import price_update as p
import opportunity as o
import main
from historical_replay import apply_historical_replay
from report import generate_report

NOW = "2026-09-05T00:00:00Z"


def good_row(**overrides):
    r = asdict(v.AnalysisResult(ticker="TEST"))
    r.update(price=50., market_cap=1000., shares_outstanding=20., intrinsic_value_total=2000.,
             intrinsic_value_per_share=100., stress_value_per_share=70.,
             quote_currency="USD", financial_currency="USD", financial_period_type="TTM",
             fundamentals_asof="2026-09-04T00:00:00Z", financial_asof="2026-06-30",
             balance_asof="2026-06-30", price_asof="2026-09-04T00:00:00Z", price_data_status="OK",
             sbc_history_complete=True, financial_period_aligned=True, fcf_history_years=4, fcf_positive_years=4,
             fcf_ttm=100., fcf_3y_avg=95., fcf_5y_avg=90., fcf_conversion=1., fcf_volatility=.1,
             cash=200., total_debt=100., net_cash=100., ebitda=150., net_income_ttm=100.,
             operating_margin=.2, share_dilution_3y=.01, liquidity_value=10_000_000., liquidity_volume=200_000.,
             quality_score=12., cashflow_score=15., balance_sheet_score=12., data_quality_score=8.,
             final_score=90., mos_score=40., margin_of_safety=1., model_type="normal_fcf", rating="S")
    r.update(overrides)
    return r


def valuation_args(**overrides):
    args = dict(cfg=v.sector_config("Technology", "Software"), latest_fcf=100., fcf_3y_avg=90., fcf_5y_avg=80.,
                latest_net_income=100., net_income_5y_avg=90., revenue_cagr=.05, cash=200., debt=100.,
                equity=800., roe=.12, market_cap=1000., ncav=None, tangible_equity=None, risk_free_rate=.04)
    args.update(overrides)
    return args


class CashflowAndValuationTests(unittest.TestCase):
    def test_capex_sign_and_sbc(self):
        s = lambda x: pd.Series(x, index=pd.to_datetime(["2025-12-31", "2024-12-31"]))
        for capex in (s([20., 20.]), s([-20., -20.])):
            reported, owner = v.build_owner_fcf_series(s([100., 100.]), capex, s([10., 10.]))
            self.assertEqual(list(reported), [80, 80])
            self.assertEqual(list(owner), [70, 70])

    def test_sbc_missing_never_becomes_zero(self):
        s = pd.Series([100.], index=pd.to_datetime(["2025-12-31"]))
        _, owner = v.build_owner_fcf_series(s, -s*.2, None)
        self.assertTrue(owner.isna().all())

    def test_ttm_rejects_missing_and_nonconsecutive_quarters(self):
        dates = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
        s = pd.Series([1, 2, 3, 4, 5], index=dates)
        self.assertEqual(v.series_sum_latest(s), 10)
        s.iloc[1] = np.nan
        self.assertIsNone(v.series_sum_latest(s))
        self.assertIsNone(v.series_sum_latest(s.dropna()))

    def test_missing_debt_fails_closed(self):
        value, method, _ = v.estimate_intrinsic_value(**valuation_args(debt=None))
        self.assertIsNone(value)
        self.assertEqual(method, "MISSING_CASH_OR_DEBT")

    def test_nonpositive_value_not_discarded(self):
        value, _, details = v.estimate_intrinsic_value(**valuation_args(debt=10000.))
        self.assertEqual(value, 0)
        self.assertIn("=0", details)

    def test_losses_not_ignored_in_normalization(self):
        value, _, _ = v.estimate_intrinsic_value(**valuation_args(latest_fcf=-20., latest_net_income=-10.))
        self.assertEqual(value, 0)

    def test_pe_does_not_add_cash_or_deduct_debt_again(self):
        args = valuation_args(latest_fcf=None, fcf_3y_avg=None, fcf_5y_avg=None)
        a = v.estimate_intrinsic_value(**args)[0]
        args.update(cash=1., debt=500.)
        self.assertEqual(a, v.estimate_intrinsic_value(**args)[0])

    def test_stress_never_higher(self):
        args = valuation_args()
        base = v.estimate_intrinsic_value(**args)[0]
        cfg = args.pop("cfg")
        self.assertLessEqual(v.estimate_stress_value(cfg, **args), base)

    def test_fx_preserves_shares(self):
        frame = pd.DataFrame({"2025-12-31": [100., 25., 4., .2]}, index=["Cash", "Ordinary Shares Number", "Basic EPS", "Tax Rate For Calcs"])
        out = v.scale_financial_df(frame, 1.1)
        self.assertAlmostEqual(out.iloc[0, 0], 110)
        self.assertEqual(out.iloc[1, 0], 25)
        self.assertAlmostEqual(out.iloc[2, 0], 4.4)
        self.assertEqual(out.iloc[3, 0], .2)

    def test_subjective_feedback_never_inflates_value_or_score(self):
        for label in ("true_opportunity", "too_strict"):
            with patch.object(v, "get_feedback_map", return_value={"TEST": {"label": label}}):
                value, score, _, _ = v.apply_feedback("TEST", 100., 70., [])
                self.assertEqual((value, score), (100., 70.))

    def test_manual_trap_is_single_veto(self):
        r = v.AnalysisResult("TEST", trap_flags="manual_value_trap", trap_count=1)
        self.assertEqual(v.quality_rating_cap(r)[0], "D_TRAP")

    def test_annual_shares_used_for_three_year_dilution(self):
        dates = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
        s = pd.Series([110., 105., 100., 100.], index=dates)
        r = v.AnalysisResult("TEST")
        v.stamp_fundamental_evidence(r, s, s, s, s, s)
        self.assertAlmostEqual(r.share_dilution_3y, .1)
        quarters = s.copy()
        quarters.index = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
        r = v.AnalysisResult("TEST")
        v.stamp_fundamental_evidence(r, s, s, s, s, quarters)
        self.assertIsNone(r.share_dilution_3y)


class EntryTests(unittest.TestCase):
    def test_distinct_discount_and_upside(self):
        r = o.evaluate_entry(good_row(), NOW)
        self.assertEqual(r["discount_to_value"], .5)
        self.assertEqual(r["upside_to_value"], 1.)
        self.assertAlmostEqual(r["entry_price"], 62.5)
        self.assertIn(r["entry_status"], o.ENTRY_STATES)

    def test_boundary_wait_near_enter(self):
        for price, expected in [(62.5, "ENTRY_REVIEW"), (65., "NEAR_ENTRY"), (80., "WAIT_PRICE")]:
            with self.subTest(price=price):
                row = good_row(price=price, market_cap=price*20)
                self.assertEqual(o.evaluate_entry(row, NOW)["entry_status"], expected)

    def test_data_unknown_and_stale_never_enter(self):
        for changes in ({"total_debt": None}, {"price_asof": "2020-01-01"}, {"fundamentals_asof": "2026-10-01"},
                        {"sbc_history_complete": False}, {"model_version": "MOS_Radar_V6.6.2"},
                        {"share_dilution_3y": None}, {"price_data_status": "FALLBACK_PREVIOUS_PRICE"}):
            with self.subTest(changes=changes):
                result = o.evaluate_entry(good_row(**changes), NOW)
                self.assertEqual(result["entry_status"], "DATA_REQUIRED")
                self.assertIsNone(result["entry_price"])

    def test_risk_veto_even_at_ten_percent_of_value(self):
        for changes in ({"trap_count": 1}, {"fcf_ttm": -1.}, {"share_count_mismatch": True},
                        {"share_dilution_3y": .2}, {"stress_value_per_share": 0.},
                        {"total_debt": 1000., "cash": 0., "interest_coverage": 1.}):
            with self.subTest(changes=changes):
                self.assertEqual(o.evaluate_entry(good_row(price=10, **changes), NOW)["entry_status"], "RISK_BLOCKED")

    def test_cyclical_requires_fifty_percent_discount(self):
        r = o.evaluate_entry(good_row(model_type="energy_cyclical", price=60, market_cap=1200), NOW)
        self.assertEqual(r["required_discount"], .5)
        self.assertEqual(r["entry_price"], 50)
        self.assertEqual(r["entry_status"], "WAIT_PRICE")

    def test_price_cross_vs_value_revision(self):
        old = o.annotate_opportunities(pd.DataFrame([good_row(price=80, market_cap=1600)]), now=NOW)
        new = o.annotate_opportunities(pd.DataFrame([good_row()]), previous=old, now=NOW)
        self.assertEqual(new.iloc[0].entry_event, "PRICE_ENTERED")
        revised = good_row(price=80, market_cap=1600, intrinsic_value_per_share=150,
                           stress_value_per_share=120, fcf_ttm=200.)
        new = o.annotate_opportunities(pd.DataFrame([revised]), previous=old, now=NOW)
        self.assertEqual(new.iloc[0].entry_event, "VALUE_REVISION_ENTERED")

    def test_value_deterioration_does_not_become_buy_signal(self):
        old = o.annotate_opportunities(pd.DataFrame([good_row()]), now=NOW)
        new = o.annotate_opportunities(pd.DataFrame([good_row(price=30, intrinsic_value_per_share=70)]), previous=old, now=NOW)
        self.assertEqual(new.iloc[0].entry_status, "REVIEW_REQUIRED")
        self.assertEqual(new.iloc[0].entry_event, "EXITED")
        next_scan = o.annotate_opportunities(pd.DataFrame([good_row(price=30, intrinsic_value_per_share=70)]), previous=new, now=NOW)
        self.assertEqual(next_scan.iloc[0].entry_status, "REVIEW_REQUIRED")

    def test_model_change_is_first_observation(self):
        old = pd.DataFrame([good_row(model_version="OLD", entry_status="WAIT_PRICE", entry_policy_version="1")])
        new = o.annotate_opportunities(pd.DataFrame([good_row()]), previous=old, now=NOW)
        self.assertEqual(new.iloc[0].entry_event, "FIRST_OBSERVATION")

    def test_historical_never_entry(self):
        out = apply_historical_replay(pd.DataFrame([good_row()]), {"TEST": 50}, "2022-10-14")
        self.assertEqual(out.iloc[0].entry_status, "HISTORICAL_ONLY")
        self.assertTrue(pd.isna(out.iloc[0].entry_price))

    def test_report_escapes_and_explains(self):
        row = good_row(ticker="<script>x</script>")
        df = o.annotate_opportunities(pd.DataFrame([row]), now=NOW)
        report = generate_report(df, "manual")
        self.assertIn("严格入场观察区", report)
        self.assertIn("压力折价", report)
        self.assertNotIn("<script>x</script>", report)


class PriceAndCacheTests(unittest.TestCase):
    def test_price_refresh_applies_quality_cap(self):
        row = good_row(cashflow_score=0, quality_score=0, final_score=90)
        self.assertNotIn(p.rerate_row(pd.Series(row))[0], {"S", "A"})

    def test_price_refresh_preserves_saved_cap(self):
        row = good_row(rating="C_THIN", rating_cap="C_THIN")
        self.assertEqual(p.rerate_row(pd.Series(row))[0], "C_THIN")

    def test_current_price_uses_raw_close_not_adjusted(self):
        data = pd.DataFrame({"Close": [100.], "Adj Close": [60.]}, index=pd.to_datetime(["2026-09-04"]))
        with patch.object(p, "_quiet_download", return_value=data):
            self.assertEqual(p.batch_current_prices(["TEST"]), {"TEST": 100.})

    def test_price_refresh_and_failure(self):
        df = pd.DataFrame([good_row()])
        with patch.object(p, "batch_current_quotes", return_value={"TEST": (80., "2026-09-04T00:00:00Z")}):
            updated = p.update_prices_only(df)
            self.assertAlmostEqual(updated.iloc[0].market_cap, 1600.)
            self.assertAlmostEqual(updated.iloc[0].margin_of_safety, .25)
            self.assertEqual(updated.iloc[0].price_data_status, "OK")
        with patch.object(p, "batch_current_quotes", return_value={}), patch.object(p, "get_current_price", return_value=None):
            updated = p.update_prices_only(df)
            self.assertNotIn(updated.iloc[0].entry_status, o.ENTRY_STATES)
            self.assertNotIn(updated.iloc[0].rating, {"S", "A", "B"})

    def test_cache_rejects_old_model_and_transient_errors(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "CACHE_DIR", Path(tmp)):
            main.save_cached_analysis("TEST", good_row())
            self.assertIsNotNone(main.load_cached_analysis("TEST"))
            path = main.cache_path("TEST")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["model_version"] = "OLD"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(main.load_cached_analysis("TEST"))
            main.save_cached_analysis("ERROR", good_row(ticker="ERROR", rating="NO_DATA"))
            self.assertFalse(main.cache_path("ERROR").exists())

    def test_journal_excludes_holdings(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "STATE_DIR", Path(tmp)), patch.object(main, "load_holdings", return_value=["PRIVATE"]):
            rows = o.annotate_opportunities(pd.DataFrame([good_row(), good_row(ticker="PRIVATE")]), now=NOW)
            main.save_entry_history(rows)
            main.save_entry_history(rows)
            history = pd.read_csv(Path(tmp) / "mos_entry_history.csv")
            self.assertEqual(history.ticker.tolist(), ["TEST"])


class FakeTicker:
    def __init__(self, currency="USD", financial_currency="USD"):
        self.ticker = "TEST"
        annual = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
        quarter = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
        def frame(data, dates):
            return pd.DataFrame({key: [value]*4 if isinstance(value, (int, float)) else value for key, value in data.items()}, index=dates).T
        self.financials = frame({"Total Revenue": [1e9, 9.5e8, 9e8, 8.5e8], "Net Income": 8e7,
                                 "Gross Profit": 4e8, "Operating Income": 2e8, "EBITDA": 2.1e8, "Interest Expense": 1e6}, annual)
        self.financials.loc["Gross Profit"] = self.financials.loc["Total Revenue"] * .4
        self.financials.loc["Operating Income"] = self.financials.loc["Total Revenue"] * .2
        self.quarterly_financials = frame({"Total Revenue": 2.6e8, "Net Income": 2e7, "Gross Profit": 1.1e8,
                                           "Operating Income": 5.5e7, "EBITDA": 5.5e7, "Interest Expense": 2.5e5}, quarter)
        self.cashflow = frame({"Operating Cash Flow": 1e8, "Capital Expenditure": -2e7, "Stock Based Compensation": 5e6}, annual)
        self.quarterly_cashflow = frame({"Operating Cash Flow": 2.5e7, "Capital Expenditure": -5e6, "Stock Based Compensation": 1.25e6}, quarter)
        balance = {"Ordinary Shares Number": 2e7, "Cash And Cash Equivalents": 5e8, "Total Debt": 5e7,
                   "Stockholders Equity": 8e8, "Current Assets": 5.1e8, "Total Liabilities": 3e8,
                   "Total Assets": 1.1e9, "Goodwill And Other Intangible Assets": 1e8}
        self.balance_sheet = frame(balance, annual)
        self.quarterly_balance_sheet = frame(balance, quarter)
        self.fast_info = {}
        self.info = dict(longName="Fixture Operating Company", sector="Consumer Defensive", industry="Household Products",
                         currency=currency, financialCurrency=financial_currency, quoteType="EQUITY", regularMarketPrice=20.,
                         marketCap=4e8, sharesOutstanding=2e7, regularMarketTime=pd.Timestamp("2026-09-04T00:00:00Z").timestamp(),
                         averageVolume10days=1e6)


class PipelineTests(unittest.TestCase):
    def test_repeated_rate_limit_preserves_state(self):
        row = asdict(v.AnalysisResult("TEST", rating="NO_DATA", price_data_status="YAHOO_RATE_LIMIT"))
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "STATE_MARKET_PATH", Path(tmp)/"missing.csv"), patch.object(main, "load_scan_tickers", return_value=[str(i) for i in range(20)]), patch.object(main, "analyze_one", return_value=row) as analyze, patch.object(main, "annotate_pools", side_effect=lambda df: df), patch.object(main, "save_outputs") as save:
            result = main.run_full_scan()
            self.assertEqual(analyze.call_count, 5)
            self.assertEqual(result.iloc[0].scan_status, "PARTIAL_SOURCE_FAILURE")
            self.assertFalse(save.call_args.kwargs["write_state"])

    def analyze(self, ticker):
        with patch.object(v.yf, "Ticker", return_value=ticker), patch.object(v, "get_risk_free_rate", return_value=.04), patch.object(v, "get_feedback_map", return_value={}):
            return v.analyze_ticker("TEST", sleep_seconds=0)

    def test_full_analysis_to_entry_to_report(self):
        result = self.analyze(FakeTicker())
        self.assertNotEqual(result.rating, "ERROR", result.reason)
        self.assertEqual(result.financial_period_type, "TTM")
        self.assertTrue(result.financial_period_aligned)
        self.assertEqual(result.fcf_history_years, 4)
        self.assertEqual(result.fcf_ttm, 75e6)
        self.assertEqual(result.share_dilution_3y, 0.)
        row = asdict(result)
        row["fundamentals_asof"] = NOW
        df = o.annotate_opportunities(pd.DataFrame([row]), now=NOW)
        self.assertIn(df.iloc[0].entry_status, o.ENTRY_STATES, df.iloc[0].entry_reason)
        self.assertIn("价格已达标", generate_report(df, "manual"))

    def test_misaligned_quarters_fall_back_together(self):
        ticker = FakeTicker()
        ticker.quarterly_cashflow.columns = pd.to_datetime(["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
        result = self.analyze(ticker)
        self.assertEqual(result.financial_period_type, "ANNUAL_FALLBACK", result.reason)
        self.assertEqual(result.revenue_ttm, 1e9)
        self.assertEqual(result.financial_asof, "2025-12-31")

    def test_hk_currency_and_share_count_pipeline(self):
        ticker = FakeTicker("HKD", "CNY")
        with patch.object(v, "current_market", return_value="hk"), patch.object(v, "latest_download_price_volume", return_value=(20., 1e6)), patch.object(v, "get_fx_rate", return_value=1.1):
            result = self.analyze(ticker)
        self.assertNotEqual(result.rating, "ERROR", result.reason)
        self.assertAlmostEqual(result.fcf_ttm, 82.5e6)
        self.assertEqual(result.shares_outstanding, 2e7)
        self.assertFalse(result.share_count_mismatch)


if __name__ == "__main__":
    unittest.main()
