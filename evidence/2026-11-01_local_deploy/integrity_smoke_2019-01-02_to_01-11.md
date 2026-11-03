# Integrity report 2026-11-01T11:57:09+00:00
Window: 2019-01-02..2019-01-11

**Complete: True**

- no problems found

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
| TWSE | 2019 | 8 | 8 | 0 |

## Secondary dataset gaps (quote day without the dataset)
| dataset | market | days | first | last |
|---|---|---:|---|---|

## Monthly revenue
| market | expected | ok | missing/few |
|---|---:|---:|---|
| TPEX | 1 | 1 |  |
| TWSE | 1 | 1 |  |

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
