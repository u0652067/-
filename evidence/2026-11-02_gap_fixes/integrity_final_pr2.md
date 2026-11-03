# Integrity report 2026-11-03T14:30:29+00:00
Window: 2019-01-01..2026-10-31

| dimension | result |
|---|---|
| 覆蓋完整度 coverage | COMPLETE |
| 資料品質狀態 quality | REVIEWED_WITH_EXCLUSIONS (ERROR 4880, unclassified 0, blocking 554) |
| 技術面回測 technical_backtest_ready | True |
| 基本面回測 fundamental_backtest_ready | False — 618 financial statements lack a verified publication time |

### Coverage gaps
- none

### Quality notes
- 4880 ERROR issues (the security-day is excluded from the universe)
- 554 issues block the security from backtests for tradability.QUALITY_BLOCK_DAYS calendar days
- 618 financial statements without reliable publication time (never returned by fundamentals.latest_statements_as_of)

## Data-quality review
| check | severity | classification | blocks backtest | n |
|---|---|---|---|---:|
| move_exceeds_limit | WARN | CORPORATE_ACTION_DIV | False | 106 |
| move_exceeds_limit | WARN | CORPORATE_ACTION_DIV_RIGHTS | False | 174 |
| move_exceeds_limit | WARN | CORPORATE_ACTION_RIGHTS | False | 32 |
| move_exceeds_limit | WARN | ETF_NO_PRICE_LIMIT_UNVERIFIED | True | 554 |
| move_exceeds_limit | WARN | NEW_LISTING_NO_LIMIT | False | 91 |
| volume_without_close | ERROR | NO_CLOSE_FIXED_PRICE_ONLY | False | 4,880 |

## Corporate actions
| market | type | n |
|---|---|---:|
| TPEX | CAPITAL_REDUCTION | 147 |
| TPEX | DIV | 7,265 |
| TPEX | DIV_RIGHTS | 494 |
| TPEX | PAR_CHANGE | 14 |
| TPEX | RIGHTS | 477 |
| TWSE | CAPITAL_REDUCTION | 186 |
| TWSE | DIV | 7,774 |
| TWSE | DIV_RIGHTS | 524 |
| TWSE | PAR_CHANGE | 10 |
| TWSE | REVERSE_SPLIT | 6 |
| TWSE | RIGHTS | 493 |
| TWSE | SPLIT | 5 |

## Trading restrictions
| market | type | rows | securities | first | last |
|---|---|---:|---:|---|---|
| TPEX | ALTERED_TRADING | 55,067 | 97 | 2019-01-02 | 2026-11-03 |
| TPEX | DISPOSITION | 1,978 | 561 | 2019-01-03 | 2026-11-10 |
| TPEX | MANAGED | 230 | 1 | 2019-01-02 | 2019-12-13 |
| TPEX | PERIODIC_AUCTION | 21,887 | 51 | 2019-01-02 | 2026-11-03 |
| TPEX | STOP_TRADING | 3,993 | 188 | 2019-01-02 | 2026-11-03 |
| TWSE | ALTERED_TRADING | 31,420 | 95 | 2019-01-02 | 2026-11-03 |
| TWSE | DISPOSITION | 1,616 | 644 | 2018-12-21 | 2026-11-09 |
| TWSE | SUSPENDED | 2,678 | 2572 | 2019-01-21 | 2026-08-13 |

## Financial statements (point-in-time)
| fiscal year | basis | pit_reliable | n |
|---|---|---|---:|
| 2019 | DOC_UPLOAD_TIME | True | 6,750 |
| 2019 | STATUTORY_DEADLINE | False | 87 |
| 2020 | DOC_UPLOAD_TIME | True | 6,868 |
| 2020 | STATUTORY_DEADLINE | False | 91 |
| 2021 | DOC_UPLOAD_TIME | True | 7,076 |
| 2021 | STATUTORY_DEADLINE | False | 57 |
| 2022 | DOC_UPLOAD_TIME | True | 7,253 |
| 2022 | STATUTORY_DEADLINE | False | 56 |
| 2023 | DOC_UPLOAD_TIME | True | 7,369 |
| 2023 | STATUTORY_DEADLINE | False | 124 |
| 2024 | DOC_UPLOAD_TIME | True | 7,615 |
| 2024 | STATUTORY_DEADLINE | False | 78 |
| 2025 | DOC_UPLOAD_TIME | True | 7,787 |
| 2025 | STATUTORY_DEADLINE | False | 76 |
| 2026 | DOC_UPLOAD_TIME | True | 3,972 |
| 2026 | OPENAPI_FILE_DATE | True | 39 |
| 2026 | STATUTORY_DEADLINE | False | 42 |
| 2026 | UNKNOWN | False | 7 |

## Table rows
| table | rows |
|---|---:|
| securities | 55,634 |
| trading_calendar | 4,092 |
| raw_payloads | 35,241 |
| daily_prices | 4,016,883 |
| market_indices | 404,965 |
| corporate_actions | 17,395 |
| technical_indicators | 3,953,630 |
| institutional_trades | 3,565,589 |
| margin_trades | 3,625,985 |
| valuations | 3,412,110 |
| monthly_revenues | 163,697 |
| financial_statements | 55,347 |
| trading_restrictions | 118,869 |
| data_quality_issues | 5,837 |
| data_source_status | 33 |
| job_runs | 2,069 |
| system_logs | 220 |
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
| mops | balance_hist_tpex | OK | 0 | None |  |
| mops | balance_hist_twse | OK | 0 | None |  |
| mops | balance_tpex | OK | 0 | None |  |
| mops | balance_twse | OK | 0 | None |  |
| mops | income_hist_tpex | OK | 0 | None |  |
| mops | income_hist_twse | OK | 0 | None |  |
| mops | income_tpex | OK | 0 | None |  |
| mops | income_twse | OK | 0 | None |  |
| mops | revenue_tpex | OK | 0 | None |  |
| mops | revenue_twse | OK | 0 | None |  |
| tpex | tpex_altered | OK | 0 | 2026-11-03 |  |
| tpex | tpex_capital_reduction | OK | 0 | 2026-11-03 |  |
| tpex | tpex_disposition | OK | 0 | 2026-11-03 |  |
| tpex | tpex_ex_rights | OK | 0 | 2026-11-03 |  |
| tpex | tpex_institutional | OK | 0 | 2026-11-03 |  |
| tpex | tpex_margin | OK | 0 | 2026-11-03 |  |
| tpex | tpex_par_change | OK | 0 | 2026-11-03 |  |
| tpex | tpex_quotes | OK | 0 | 2026-11-03 |  |
| tpex | tpex_valuation | OK | 0 | 2026-11-03 |  |
| twse | twse_capital_reduction | OK | 0 | 2026-11-03 |  |
| twse | twse_disposition | OK | 0 | 2026-11-03 |  |
| twse | twse_etf_split | OK | 0 | 2026-11-03 |  |
| twse | twse_ex_rights | OK | 0 | 2026-11-03 |  |
| twse | twse_full_delivery | OK | 0 | 2026-11-03 |  |
| twse | twse_halt | OK | 0 | 2026-11-03 |  |
| twse | twse_institutional | OK | 0 | 2026-11-03 |  |
| twse | twse_margin | OK | 0 | 2026-11-03 |  |
| twse | twse_par_change | OK | 0 | 2026-11-03 |  |
| twse | twse_quotes | OK | 0 | 2026-11-03 |  |
| twse | twse_valuation | OK | 0 | 2026-11-03 |  |
| twse_doc | financial_report_uploads | OK | 0 | None | https://doc.twse.com.tw/server-java/t57sb01 failed after 3 attempts: _ssl.c:994: The handshake operation timed out |
| twse_isin | isin_tpex | OK | 0 | 2026-11-03 |  |
| twse_isin | isin_twse | OK | 0 | 2026-11-03 |  |

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
 "gap_datasets": {},
 "quality_review": null,
 "blocked_events": 0,
 "updated_at": "2026-11-02T02:47:33+00:00",
 "interrupted_reason": null
}
```
