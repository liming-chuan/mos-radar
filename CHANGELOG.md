# Changelog

All notable MOS Radar version changes are recorded here.

## v6.4.0 - 2026-05-17

- Fixed scheduled-run mode detection by mapping GitHub UTC cron strings exactly to morning, noon, afternoon, and after-close modes.
- Added public market state persistence through `state/mos_market_latest.csv`; non-full scheduled modes now fail fast when state is missing instead of silently running a full scan.
- Kept holdings private: persisted state excludes holding rows and holding/pool markers; holdings continue to come from `data/holdings.csv` or `HOLDINGS_TICKERS`.
- Added true quarterly TTM calculations for revenue, net income, reported FCF, SBC, and Owner FCF, with explicit `financial_period_type=TTM` or `ANNUAL_FALLBACK`.
- Replaced numeric `x or fallback` patterns with `coalesce_none` in key valuation paths so valid zero values are not treated as missing.
- Added valuation candidate detail, industry model status, data quality diagnostics, and report columns for financial period type and valuation method.
- Changed intraday price updates to batch yfinance downloads, refresh market cap/FCF yield, and rerate rows after price changes.
- Changed universe liquidity filtering to use explicit `liquidity_volume` and `volume_source`, no longer pretending Nasdaq screener `volume` is always average volume.
- Added GitHub Actions fundamentals cache scaffolding, public state artifact upload, and state commit step.
- Renamed historical replay language to historical price stress test and removed hard-coded old model version text.
- Updated README, model version, and version metadata.

## v6.3.6 - 2026-05-17

- Removed the Yahoo quote batch verification path from `Update Universe`.
- `Update Universe` now uses Nasdaq screener quote data directly for price, market-cap, and liquidity filtering, eliminating the noisy 55-batch `401 Unauthorized` logs.
- Kept the existing-universe protection: if Nasdaq screener returns zero rows, the workflow preserves the current `data/universe.csv` instead of writing an empty file.
- Updated README, model version, and version metadata.

## v6.3.5 - 2026-05-17

- Added Nasdaq screener fallback data for `Update Universe` when Yahoo quote batches return 401 or insufficient coverage.
- `Update Universe` now switches to the fallback when verified quote rows are below the requested target.
- Improved numeric parsing for dollar/comma formatted quote fields.
- Updated README, model version, and version metadata.

## v6.3.4 - 2026-05-17

- Changed `Update Universe` default limit from 1000 to 2000.
- Added explicit Update Universe diagnostics for received limit, market-cap threshold, volume threshold, verified quote row count, merged row count, and post-filter row count.
- Updated README, model version, and version metadata.

## v6.3.3 - 2026-05-17

- Fixed `Update Universe` when Yahoo quote verification returns zero rows.
- Empty quote verification now keeps the existing `data/universe.csv` instead of crashing with `KeyError: 'ticker'` or writing an empty universe.
- `Update Universe` now uploads `data/universe.csv` as the `mos-radar-universe` artifact for inspection.
- Updated README, model version, and version metadata.

## v6.3.2 - 2026-05-17

- Historical replay now sends email when `dry_run=false`.
- Added a `dry_run` input to the historical replay workflow so email sending is controlled directly from Run workflow.
- Suppressed noisy yfinance missing-history messages during historical replay and replaced them with one coverage summary.
- Stocks with no historical price on the replay date are now marked `SKIP` with `historical_price_status=NO_HISTORICAL_PRICE`.
- Historical replay reports now include a historical price coverage diagnostic table.
- Updated README, model version, and version metadata.

## v6.3.1 - 2026-05-17

- Split current-market scanning and historical replay into separate GitHub Actions workflows.
- `MOS Radar Daily Scanner` now runs only current-market scans and scheduled weekday jobs.
- Added `MOS Radar Historical Replay` for manual historical price replay runs.
- Both workflows upload CSV results and reports as GitHub Actions artifacts.
- Updated README and version metadata.

## v6.3.0 - 2026-05-17

- Added `historical_replay` mode for bear-market price stress testing.
- Added GitHub Actions manual inputs for `run_mode`, `backtest_date`, and `backtest_use_latest`.
- Historical replay fetches historical prices and recalculates margin of safety, FCF yield, market cap, score, rating, and post-backtest return.
- Historical replay writes dedicated result files such as `data/results/historical_replay_2022-10-14.csv` instead of overwriting the normal latest scan.
- Reports now label historical replay as a price stress test, not a strict point-in-time financial-statement backtest.
- Updated README and version metadata.

## v6.2.1 - 2026-05-17

- Added a market sector distribution table to scan reports so universe composition is visible.
- Changed the generic diagnostic sample to use only non-financial operating companies.
- Kept financial stocks in their dedicated PB/ROE observation sections, preventing duplicated financial names from dominating diagnostics.
- Updated README and version metadata.

## v6.2.0 - 2026-05-17

- Switched operating-company valuation from reported FCF to owner FCF: `Operating Cash Flow + CapEx - SBC`.
- Added reported FCF, SBC, total assets, total liabilities, NCAV, tangible equity, risk-free rate, applied discount rate, and accrual ratio to valuation outputs.
- Added dynamic discount-rate support using cached `^TNX` 10-year Treasury yield with a conservative fallback for GitHub Actions.
- Added NCAV and tangible-book valuation candidates for asset-heavy and cyclical models.
- Financial PB/ROE valuation now prefers tangible equity when available, reducing false positives from goodwill-heavy balance sheets.
- Added accrual-ratio value-trap detection when accounting earnings materially exceed owner FCF.
- Made cyclical FCF valuation more conservative by haircutting latest FCF and skipping latest-year capped FCF for cycle-sensitive industries.
- Updated README and version metadata for the V6.2 model.

## v6.1.0 - 2026-05-17

- Refactored the valuation model around asset/model type instead of treating all stocks as normal FCF businesses.
- Financial stocks now use financial-only scoring based on PB/ROE and no longer use FCF yield or debt/EBITDA in their score.
- Financial stocks can enter only the dedicated financial observation flow; they no longer pollute the main operating-company candidate pool.
- Funds, BDCs, closed-end funds, and NAV/NII-driven financial assets are skipped until a dedicated NAV/NII model is added.
- Data quality scoring now treats `NaN` as missing instead of counting it as valid data.
- Rating reasons are now more precise:
  - 20%+ margin of safety but weak score is labeled as insufficient quality/score, not simply "thin".
  - financial stocks explicitly state that PB/ROE is only a limited screening model.

## v6.0.5 - 2026-05-17

- Split report sections for non-financial operating companies and financial stocks.
- Financial stocks now appear in a dedicated observation section because they use PB/ROE instead of normal FCF valuation.
- The non-financial near-miss section no longer gets dominated by Financial Services names.
- Diagnostic samples are diversified by sector to avoid one sector filling the entire table.

## v6.0.4 - 2026-05-16

- Improved report readability for diagnostic sections.
- Added a separate "near miss" section for `C_THIN` names close to B-level review.
- Changed diagnostic tables to a compact layout so the reason column does not collapse into vertical text.
- Kept the main S/A/B candidate table unchanged.

## v6.0.3 - 2026-05-16

- Added `HOLDINGS_TICKERS` secret support so holdings can be used on GitHub Actions without committing private holdings files.
- Added report diagnostics:
  - full rating distribution
  - sample rows for tickers that did not enter S/A/B
- This makes empty candidate reports explainable instead of only showing "no qualifying companies".

## v6.0.2 - 2026-05-16

- Added quote currency and financial statement currency fields to valuation output.
- Added a hard skip for tickers where quote currency and financial currency differ, preventing ADR/currency mismatches from creating false margin-of-safety signals.
- Added a precious metals/mining model with more conservative multiples.
- Capped precious metals/mining stocks at B because current FCF is highly cycle-dependent.
- Added an abnormal FCF yield cap: FCF yield above 25% caps rating at `C_THIN` pending manual review.

## v6.0.1 - 2026-05-16

- Optimized `update_universe.py` for GitHub Actions free runners.
- Replaced one-by-one yfinance ticker verification with batched Yahoo quote requests.
- Added per-batch HTTP timeout so one stuck ticker cannot freeze the whole universe update.
- Changed Update Universe default limit from 500 to 1000.
- Added `UNIVERSE_BATCH_SIZE` for controlled batch verification.

## v6.0.0 - 2026-05-16

- Added `DRY_RUN=true` support to generate reports without sending email.
- Added lightweight retry handling for yfinance data fetches and price updates.
- Upgraded model identifier from `MOS_Radar_V5` to `MOS_Radar_V6`.
- Added more value trap checks:
  - consecutive revenue decline
  - gross margin decline
  - operating margin decline
  - high FCF volatility
  - weak interest coverage
  - debt exceeding market cap
  - debt over 5x average FCF
- Added quality/risk rating caps so weak fundamentals can cap high margin-of-safety names.
- Added 20% / 35% / 50% margin-of-safety observation prices to reports.
- Kept the architecture compatible with GitHub Actions free runners: no database, no paid API, no heavy background service.

## v5.0.0 - 2026-05-16

- Documented the current MOS Radar V5 model in `README.md`.
- Confirmed GitHub Actions runs only on NY market weekdays.
- Current architecture:
  - GitHub Actions scheduled scanner
  - yfinance data source
  - conservative intrinsic value model
  - margin of safety rating system
  - HTML email report through SMTP
- V5 model highlights:
  - sector-aware conservative valuation
  - lowest valid valuation candidate as intrinsic value
  - financial stock PB/ROE model
  - REIT/real estate skip until AFFO/NOI support is added
  - value trap flags and scoring

## Version Policy

- `main` always contains the latest working code.
- Each stable upgrade is saved with a Git tag, for example `v5.0.0`, `v6.0.0`.
- Major model upgrades use a new major version.
- Small fixes use patch versions, for example `v5.0.1`.
- Before pushing a new version, run local validation with a small ticker sample.
