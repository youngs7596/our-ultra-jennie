
# 🏗️ Data Accumulation Utilities Implementation Report (Cycle 2)

This document details the technical implementation of the data accumulation framework required for the [Long-Term Data Strategy](./long_term_data_strategy.md).

**Implementation Date**: 2025-12-16
**Status**: deployed

## 1. Database Schema Updates (`shared/db/models.py`)

We introduced four new tables to capture critical "Memory" and "Context" data.

### A. `LLM_DECISION_LEDGER` (Priority 1)
*   **Purpose**: The "Brain's Memory". Records why a decision was made.
*   **Key Fields**:
    *   `debate_log`: Full text of the Bull vs. Bear debate.
    *   `dominant_keywords`: JSON list of key themes (e.g., ["Lithium", "War"]).
    *   `final_decision`: BUY / SELL / HOLD / NO_DECISION.
    *   `hunter_score`: The quantitative score at the time of decision.

### B. `SHADOW_RADAR_LOG` (Priority 2)
*   **Purpose**: The "Missed Opportunities" tracker.
*   **Key Fields**:
    *   `rejection_stage`: Where it failed (Hunter / Gate / Judge).
    *   `rejection_reason`: Why it was filtered out.
    *   `trigger_type`: Later event that made us look back (e.g., PRICE_SURGE).

### C. `MARKET_FLOW_SNAPSHOT` (Priority 2.5)
*   **Purpose**: Daily market tide tracking.
*   **Key Fields**:
    *   `foreign_net_buy`: Net buying by foreigners.
    *   `institution_net_buy`: Net buying by institutions.
    *   `program_net_buy`: Program trading net buying.
    *   `data_type`: DAILY.

### D. `STOCK_MINUTE_PRICE` (Priority 3)
*   **Purpose**: High-resolution data for detailed entry/exit and pattern analysis.
*   **Key Fields**:
    *   `price_time`: Timestamp of the minute bar.
    *   `open`, `high`, `low`, `close`, `volume`.

---

## 2. Shared Utilities (`shared/archivist.py`)

### The Archivist
A centralized class `Archivist` was created to handle all distinct logging operations safely.
*   **Pattern**: Uses `session_factory` to manage its own database sessions, ensuring logs are committed independently of the main transaction if needed (though currently integrated tightly).
*   **Resilience**: Wraps database operations in try/except blocks to prevent logging failures from crashing the main trading or analysis pipelines.

---

## 3. Scout Integration

The `Archivist` has been injected into the `Scout` service's main execution loop.
*   **Judge Log**: Specifically, `process_phase23_judge_v5_task` now accepts the archivist and automatically logs the full decision context to `LLM_DECISION_LEDGER` upon completion of the debate/judge phase.

---

## 4. Data Collection Scripts (`scripts/`)

Two new standalone scripts were developed for periodic data collection.

### `scripts/collect_market_flow.py`
*   **Schedule**: Daily at 16:00 KST (Mon-Fri).
*   **Logic**:
    1.  Authenticates with KIS API.
    2.  Iterates through the **Active Watchlist**.
    3.  Calls `inquire-investor` and `inquire-program-trade` endpoints.
    4.  Logs snapshots to `MARKET_FLOW_SNAPSHOT` via Archivist.

### `scripts/collect_intraday_data.py`
*   **Schedule**: Daily at 16:05 KST (Mon-Fri).
*   **Logic**:
    1.  Authenticates with KIS API.
    2.  Iterates through the **Active Watchlist**.
    3.  Fetches 1-minute OHLCV data for the current day.
    4.  Upserts records to `STOCK_MINUTE_PRICE` using `session.merge` to handle potential duplicates/re-runs.

---

## 5. Automation (Cron)

System-level cron jobs have been configured in the WSL2 environment:

```bash
# [Jennie v6.0] Data Accumulation
# Market Flow Snapshot (Daily 16:00 KST)
0 16 * * 1-5 ... python3 scripts/collect_market_flow.py ...

# Targeted Intraday Data (Daily 16:05 KST)
5 16 * * 1-5 ... python3 scripts/collect_intraday_data.py ...
```

Logs are preserved in `logs/cron/` for monitoring and debugging.
