# Changelog

All notable MOS Radar version changes are recorded here.

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
