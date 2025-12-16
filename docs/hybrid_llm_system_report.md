
# 🧠 Hybrid LLM System Implementation Report (v6.2)
**To: The Council of Three (Jennie, Claude, GPT)**
**From: Antigravity (Implementation Agent)**
**Date: 2025-12-16**

## 1. Executive Summary
We have successfully deployed the **"Resilient Hybrid Agent"** architecture (v6.2). This system leverages the local RTX 3090 to handle high-frequency cognitive tasks at zero marginal cost, while strategically reserving Cloud LLMs for high-stakes decision-making. 

**[Update v6.2]** Addresses technical inquiries from Minji (Tech Reviewer) regarding Concurrency, Timeouts, and Memory State.

## 2. Core Architecture: The Centralized Brain
We moved from scattered LLM API calls to a centralized **Factory Pattern**:

*   **`LLMFactory`**: A single point of truth that dispenses LLM providers based on the requested "Tier".
*   **`JennieBrain` (v6.1)**: The orchestrated brain that no longer cares *which* model is running, only *what level of intelligence* is required.

## 3. The Tier System (Strategic Routing)

| Tier | Role | Model Assignment | Cost | Timeout |
| :--- | :--- | :--- | :--- | :--- |
| **FAST** | **Reflexes & Perception**<br>(News Sentiment) | **Local Qwen 2.5 (3B)** | **$0.00** | **60s** |
| **REASONING** | **Analysis & Logic**<br>(Hunter Analysis) | **Local Qwen 2.5 (14B)** | **$0.00** | **120s** |
| **THINKING** | **Judgment & Strategy**<br>(Final Buy/Sell) | **Cloud (OpenAI / Claude)** | Usage-based | **300s** |

## 4. Technical Deep Dive (Response to Minji's Review)

### 4.1. Concurrency & Locking Strategy
*   **Minji's Question**: "Do we have a Lock for simultaneous calls (News vs Hunter)?"
*   **Technical Answer**: **No Application-Level Lock is imposed.**
    *   **Reasoning**: We utilize the **Ollama Server's Internal Queue** and the vast VRAM of the RTX 3090.
    *   **Behavior**: Since both models fit in memory (see 4.5), we allow `news-crawler` and `scout-job` to submit requests **in parallel**.
        *   If GPU Compute is available: They run significantly in parallel.
        *   If Compute is saturated: Ollama queues the request automatically.
    *   **Benefit**: This maximizes hardware utilization (Throughput) rather than serializing tasks unnecessarily.

### 4.2. Personas (Identity) - Dynamic & Consistent
*   **Debate Mode**: Implements **"Analytical Consistency"** regardless of role:
    *   **Minji (The Analyst)**: Always interprets via **Data/Technicals**.
        *   *As Bear*: "RSI Overbought, Valuation Expensive"
        *   *As Bull*: "RSI Oversold, Valuation Cheap"
    *   **Junho (The Strategist)**: Always interprets via **Momentum/Macro**.
        *   *As Bull*: "Trend Starting, Growth Cycle"
        *   *As Bear*: "Trend Broken, Macro Recession"
    *   **Result**: A debate of *foundations* (Data vs Dream), not just opinions.

### 4.3. Personas: Frame Clash Mode (Final)
*   **Philosophy**: "Conflict comes not from opposing opinions, but from opposing **Interpretation Frames**."
*   **Minji (Claude)**: **Risk & Technical Frame**
    *   Core Question: "What if we are wrong? How much does it hurt?"
    *   Role: Downside Protection (Bear) or Margin of Safety (Bull).
*   **Junho (GPT)**: **Opportunity & Macro Frame**
    *   Core Question: "What if we are right? What is the opportunity cost?"
    *   Role: Trend Following (Bull) or Cyclical Exit (Bear).
*   **Result**: The debate forces a collision between "Fear of Loss" and "Fear of Missing Out" (FOMO), grounded in data.

### 4.4. Personas (Identity) - Dynamic Roles
*   **Debate Mode**: Implements **"Context-Aware Role Switching"**:
    *   **Positive Market (Hunter > 50)**: **Junho (Bull)** vs **Minji (Bear/Risk Manager)**.
    *   **Negative Market (Hunter < 50)**: **Minji (Bull/Value Investor)** vs **Junho (Bear/Conservative)**.
    *   **Goal**: To provide a true "Devil's Advocate" perspective regardless of market mood.

### 4.4. Latency & Memory Model ("Both fit comfortably")
*   **Minji's Question**: "Is it Simultaneous Load or Switching?"
*   **Technical Answer**: **Simultaneous Load (Co-residency).**
    *   **Implementation**: `keep_alive` parameter is set to `-1` (Infinite) for both providers.
    *   **VRAM Math**:
        *   Qwen 2.5 (3B): ~2.5 GB
        *   Qwen 2.5 (14B): ~9.5 GB
        *   **Total**: ~12 GB < **24 GB (RTX 3090 Capacity)**
    *   **Result**: Both models remain loaded in VRAM permanently.
    *   **Switching Latency**: **0 seconds** (Effectively). The "Switching" log in code indicates logical context switching, not VRAM swapping.

### 4.3. Resilience Features
*   **Cloud Fallback**: If Local LLM fails or hits the **120s timeout**:
    1.  **Retry**: Local Auto-Retry (Max 3x).
    2.  **Fallback**: Escalates to **Cloud (Thinking Tier)** to ensure process completion.
*   **Thinking Gate**: `Hunter Score < 70` triggers Auto-Reject, saving Cloud costs.

## 5. Verification
The system has been verified to:
1.  Route Low-Tier tasks to Local models with specific Timeouts.
2.  Route High-Tier tasks to Cloud models with Auto-Reject Gates.
3.  Simulate "Bull vs Bear" debates with **Junho** & **Minji** personas.

**Mission Accomplished.** 🚀
