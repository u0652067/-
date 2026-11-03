"""PHASE 2 research & backtesting (technical data only).

Research-integrity rules enforced here:
  * strategies never touch the database: they receive a point-in-time `DataView` for the decision day;
  * candidates come only from `build_universe()` (restrictions, quality blocks, liquidity rules);
  * fundamentals are refused unless every statement used has a verified publication time
    (`fundamentals.latest_statements_as_of`) and the caller explicitly enables them;
  * orders fill at the next trading day's open with Taiwan costs, tick/lot rules, price limits and a
    volume-participation cap; unfilled orders are reported, never silently filled;
  * every run is stored append-only with config, params, data version and a results hash.

Backtest output is a research measurement. It never claims a strategy is effective, profitable or tradable.
"""

DISCLAIMER = ("研究用途的歷史模擬結果。回測績效不代表未來表現，不構成任何策略有效、可獲利或可交易的宣稱，"
              "亦非投資建議。Research-only historical simulation; not evidence that any strategy is effective, "
              "profitable or tradable.")
ENGINE_VERSION = "2.0.0"
