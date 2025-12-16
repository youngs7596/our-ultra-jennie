# 🗄️ Long-Term Data Accumulation Strategy (Final v1.0)
**Goal**: Building the "Memory" of the System to evolve from a bot to a Thinking Partner.

## 1. The Core Philosophy
**"A great system is defined not by how much it hit, but by remembering *WHY* it didn't act."** (Junho)
We collect **decisions**, **context**, and **missed opportunities**.

## 2. Phase 0: The Infrastructure (Minji's Foundation)
Before collecting, we must ensure longevity.
*   **Schema Versioning**: All logs must have `schema_v` tag.
*   **Time Standard**: All timestamps in **UTC** (ISO 8601).
*   **Storage**: Separation of Raw (JSON/Text) vs Processed (SQL/Parquet).
*   **Backup**: Weekly automated backup to external cold storage.

## 3. Priority 1: The Brain's Memory (Decision Ledger)
**Must-Have Now.** Capturing the "Moment of Truth".

| Category | Metrics / Data Points | Purpose |
| :--- | :--- | :--- |
| **Context** | Hunter Score, Market Regime, **Dominant Keywords** (Vectorized). | "History Repeats" (Jennie's Narrative). |
| **Process** | **Debate Log**: "Counter-Position" Arguments (Risk vs Opportunity). | Fine-tune future logic on *our* dialectic. |
| **Ops Data** | `thinking_called` (bool), `thinking_reason`, `cost_estimate`, `gate_result`. | Analyze "Cost of Decision" (Junho's Ops). |
| **Outcome** | Class: Buy / Sell / Hold / **No-Decision** (Deferral).<br>Reason: One-line logic. | Differentiate "Cowardice" vs "Discipline". |
| **Result** | T+5, T+20 Return, MDD. | The "Answer Key". |

## 4. Priority 2: The "Shadow Radar" (Missed Opportunities)
**Strategic Value.** Capturing what we filtered out to calibrate risk sensitivity.
*   **Triggers**:
    *   Price Surge (+15%).
    *   **Volume Shock** (Top X% variance).
    *   **Gap Up/Down** events.
    *   **Volatility Breakout**.
*   **Data**: Hunter Score at that moment, Rejection Reason.
*   **Goal**: To answer "Are we being too cowardly?"

## 5. Priority 2.5: Market Flow Data (Trend Foundations)
**Minji's Request.** The "Tide" of the market.
*   **Daily**: Foreigner Net Buy, Institution Net Buy, Program Net Buy.
*   **Pattern**: e.g., "Foreign Net Buy 3-Day Streak".

## 6. Priority 3: Targeted Intraday Data
**optimization Value.** Only for **Radar Candidates**.
*   **Data**: 1-minute OHLCV & VWAP.
*   **Flow**: **Foreign Est. Net Buy**, **Program Net Buy** (1-min).
*   **Goal**: To analyze "Entry Timing" and distinct "Fake" moves (Jennie's K-Stock Tip).

## 7. Priority 4: Market Regime Meta-Data
**Context Value.**
*   **Data**: KOSPI Volatility, KRW/USD, US Yields, VIX, Short Interest, Credit Balance.
*   **Goal**: To automate "Mode Switching" (Aggressive vs Defensive).

## 8. Implementation Roadmap
1.  **Phase 0 (Setup)**: Define Schemas (SQL/JSON) with Versioning.
2.  **Phase 1 (The Brain)**: Implement `llm_decision_ledger` via `DecisionContext` object.
3.  **Phase 2 (The Shadow)**: Build `ShadowRadar` logic in Scout.
4.  **Phase 3 (The Flow)**: Update `news-crawler` or collector to fetch Intraday Investor Flow.
5.  **Phase 4 (Analysis)**: Build "Failure Analysis Dashboard".

*(Approved by: Jennie, Minji, Junho)*
