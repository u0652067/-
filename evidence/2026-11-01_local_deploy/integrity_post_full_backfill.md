# Integrity report 2026-11-02T12:56:31+00:00
Window: 2019-01-01..2026-10-31

**Complete: True**

- no problems found

## Table rows
| table | rows |
|---|---:|
| securities | 55,054 |
| trading_calendar | 4,090 |
| raw_payloads | 15,729 |
| daily_prices | 4,014,475 |
| market_indices | 404,698 |
| corporate_actions | 8,788 |
| technical_indicators | 3,951,262 |
| institutional_trades | 3,563,322 |
| margin_trades | 3,621,536 |
| valuations | 3,410,130 |
| monthly_revenues | 163,697 |
| financial_statements | 84 |
| data_quality_issues | 5,822 |
| data_source_status | 17 |
| job_runs | 2,064 |
| system_logs | 107 |
| notifications | 0 |

## Quotes by market / year
| market | year | known trading days | with quotes | missing |
|---|---|---:|---:|---:|
| TPEX | 2019 | 242 | 242 | 0 |
| TPEX | 2020 | 245 | 245 | 0 |
| TPEX | 2021 | 244 | 244 | 0 |
| TPEX | 2022 | 246 | 246 | 0 |
| TPEX | 2023 | 239 | 239 | 0 |
| TPEX | 2024 | 242 | 242 | 0 |
| TPEX | 2025 | 243 | 243 | 0 |
| TPEX | 2026 | 198 | 198 | 0 |
| TWSE | 2019 | 242 | 242 | 0 |
| TWSE | 2020 | 245 | 245 | 0 |
| TWSE | 2021 | 244 | 244 | 0 |
| TWSE | 2022 | 246 | 246 | 0 |
| TWSE | 2023 | 239 | 239 | 0 |
| TWSE | 2024 | 242 | 242 | 0 |
| TWSE | 2025 | 243 | 243 | 0 |
| TWSE | 2026 | 198 | 198 | 0 |

## Secondary dataset gaps (quote day without the dataset)
| dataset | market | days | first | last |
|---|---|---:|---|---|

## Monthly revenue
| market | expected | ok | missing/few |
|---|---:|---:|---|
| TPEX | 93 | 93 |  |
| TWSE | 93 | 93 |  |

## Source status
| source | dataset | status | fails | latest | last error |
|---|---|---|---:|---|---|
| mops | balance_tpex | OK | 0 | None |  |
| mops | balance_twse | OK | 0 | None |  |
| mops | income_tpex | OK | 0 | None |  |
| mops | income_twse | OK | 0 | None |  |
| mops | revenue_tpex | OK | 0 | None |  |
| mops | revenue_twse | OK | 0 | None |  |
| tpex | tpex_institutional | OK | 0 | 2026-11-02 |  |
| tpex | tpex_margin | OK | 0 | 2026-10-30 |  |
| tpex | tpex_quotes | OK | 0 | 2026-11-02 |  |
| tpex | tpex_valuation | OK | 0 | 2026-11-02 |  |
| twse | twse_ex_rights | OK | 0 | 2026-11-02 |  |
| twse | twse_institutional | OK | 0 | 2026-11-02 |  |
| twse | twse_margin | OK | 0 | 2026-10-30 |  |
| twse | twse_quotes | OK | 0 | 2026-11-02 |  |
| twse | twse_valuation | OK | 0 | 2026-11-02 |  |
| twse_isin | isin_tpex | OK | 0 | 2026-11-02 |  |
| twse_isin | isin_twse | OK | 0 | 2026-11-02 |  |

## Re-runnable days
0 days; rerun with `scripts\backfill.ps1 start` (resumes, only missing datasets are fetched).

## Backfill progress
```
{
 "status": "DONE",
 "phase": null,
 "window": "2019-01-01..2026-10-31",
 "current_day": "2026-10-30",
 "days_done": 2044,
 "days_total": 2044,
 "eta_utc": null,
 "note": "indicators through 2026-10-31 (3948909 rows)",
 "months_by_status": {
  "DONE": 94
 },
 "missing_day_counts": {},
 "failed_days": 0,
 "revenue_months_done": 186,
 "revenue_months_not_done": {},
 "corporate_action_chunks_failed": {},
 "blocked_events": 0,
 "updated_at": "2026-11-02T02:47:33+00:00",
 "interrupted_reason": null
}
```
