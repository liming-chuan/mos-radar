# Changelog

All notable MOS Radar version changes are recorded here.

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
