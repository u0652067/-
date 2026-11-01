# Integrity report 2026-11-01T11:57:35+00:00
Window: 2019-01-01..2026-11-01

**Complete: False**

- TPEX revenue: 92 months missing/few
- TWSE revenue: 92 months missing/few
- 2029 weekdays never resolved (no data, not known holiday)

## Table rows
| table | rows |
|---|---:|
| securities | 54,976 |
| trading_calendar | 30 |
| raw_payloads | 120 |
| daily_prices | 27,038 |
| market_indices | 2,431 |
| corporate_actions | 11 |
| technical_indicators | 26,452 |
| institutional_trades | 22,480 |
| margin_trades | 24,684 |
| valuations | 23,414 |
| monthly_revenues | 1,637 |
| financial_statements | 0 |
| data_quality_issues | 53 |
| data_source_status | 11 |
| job_runs | 16 |
| system_logs | 5 |
| notifications | 0 |

## Quotes by market / year
| market | year | known trading days | with quotes | missing |
|---|---|---:|---:|---:|
| TPEX | 2019 | 8 | 8 | 0 |
| TPEX | 2026 | 5 | 5 | 0 |
| TWSE | 2019 | 8 | 8 | 0 |
| TWSE | 2026 | 5 | 5 | 0 |

## Secondary dataset gaps (quote day without the dataset)
| dataset | market | days | first | last |
|---|---|---:|---|---|

## Monthly revenue
| market | expected | ok | missing/few |
|---|---:|---:|---|
| TPEX | 93 | 1 | 2019-02, 2019-03, 2019-04, 2019-05, 2019-06, 2019-07, 2019-08, 2019-09, 2019-10, 2019-11, 2019-12, 2020-01, 2020-02, 2020-03, 2020-04, 2020-05, 2020-06, 2020-07, 2020-08, 2020-09, 2020-10, 2020-11, 2020-12, 2021-01, 2021-02, 2021-03, 2021-04, 2021-05, 2021-06, 2021-07, 2021-08, 2021-09, 2021-10, 202 |
| TWSE | 93 | 1 | 2019-02, 2019-03, 2019-04, 2019-05, 2019-06, 2019-07, 2019-08, 2019-09, 2019-10, 2019-11, 2019-12, 2020-01, 2020-02, 2020-03, 2020-04, 2020-05, 2020-06, 2020-07, 2020-08, 2020-09, 2020-10, 2020-11, 2020-12, 2021-01, 2021-02, 2021-03, 2021-04, 2021-05, 2021-06, 2021-07, 2021-08, 2021-09, 2021-10, 202 |

## Source status
| source | dataset | status | fails | latest | last error |
|---|---|---|---:|---|---|
| tpex | tpex_institutional | OK | 0 | 2026-10-30 |  |
| tpex | tpex_margin | OK | 0 | 2026-10-30 |  |
| tpex | tpex_quotes | OK | 0 | 2026-10-30 |  |
| tpex | tpex_valuation | OK | 0 | 2026-10-30 |  |
| twse | twse_ex_rights | OK | 0 | 2026-10-30 |  |
| twse | twse_institutional | OK | 0 | 2026-10-30 |  |
| twse | twse_margin | OK | 0 | 2026-10-30 |  |
| twse | twse_quotes | OK | 0 | 2026-10-30 |  |
| twse | twse_valuation | OK | 0 | 2026-10-30 |  |
| twse_isin | isin_tpex | OK | 0 | 2026-11-01 |  |
| twse_isin | isin_twse | OK | 0 | 2026-11-01 |  |

## Re-runnable days
0 days; rerun with `scripts\backfill.ps1 start` (resumes, only missing datasets are fetched).

## Backfill progress
```
{
 "status": "NEVER_RUN"
}
```
