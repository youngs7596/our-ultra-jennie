# Implementation Plan - The "Resilient Hybrid Agent" Architecture

This plan synthesizes feedback from the "3 Wise Men" (Jennie, Claude, GPT) to create a robust, production-grade integration of Local LLMs.

## Strategic Vision: Configurable Hybrid
> [!IMPORTANT]
> **Core Philosophy**: "Thinking is Authority, not just Compute."
> We treat the **Thinking Tier** (Judge) as a high-stakes decision engine that defaults to Cloud for quality, but can fallback or opt-in to Local.
> We treat the **Reasoning Tier** (Hunter) as a high-volume data processor that defaults to Local for cost efficiency.

### 🎯 Target Architecture
1.  **Hunter (News/Analysis)**: **Local** `qwen2.5:14b`. (Volume optimized)
2.  **Judge (Trading Decision)**: **Cloud** `gpt-5-mini` / `claude-sonnet` (Quality optimized).
3.  **Reporter (Daily Briefing)**: **Cloud** `claude-opus` (Quality optimized).
4.  **Resilience**: If Local fails (timeout/crash), automatically escalate to Cloud.

## Proposed Changes

### 1. New Architecture: Factory & State Management

#### [NEW] [shared/llm_factory.py](file:///home/youngs75/projects/my-ultra-jennie/shared/llm_factory.py)
- **`LLMFactory`**: Central point for model retrieval.
    - **`ModelStateManager`**: Global singleton to control which local model is loaded in VRAM. Prevents race conditions.
    - **Dynamic Routing**: Uses `infrastructure/env-vars-wsl.yaml` to map Tiers to Providers (Ollama vs OpenAI vs Claude).
    - **Fallback Logic**: If `generate_json` raises a `LocalModelFailure` exception, the Factory (or JennieBrain wrapper) automatically retries with the configured Cloud provider.

- **`LLMTier` Enum**:
    - `FAST`: **Local `qwen2.5:3b`**. (For ultra-fast sentiment checks).
    - `REASONING`: **Local `qwen2.5:14b`**. (For News summarization/extraction).
    - `THINKING`: **Cloud** (Default). (For Judge/Debate/Reporting).

### 2. Provider Implementation (Defensive)

#### [MODIFY] [shared/llm_providers.py](file:///home/youngs75/projects/my-ultra-jennie/shared/llm_providers.py)
- **Add `OllamaLLMProvider`**:
    - **[Robustness] Retry**: 3x Retry loop for JSON parsing errors.
    - **[Robustness] Tag Cleaning**: Regex removal of `<think>...</think>` tags (crucial or DeepSeek).
    - **[Robustness] Timeout**:
        - Fast: 60s
        - Reasoning: 120s
        - Thinking: 300s
    - **[Ops] Keep-Alive**: `keep_alive: -1` (Infinite) to prevent unloading overhead.

### 3. Service Refactoring

#### [MODIFY] [shared/llm.py](file:///home/youngs75/projects/my-ultra-jennie/shared/llm.py)
- **`JennieBrain` Refactor**:
    - **Remove** direct `self.provider_gemini` etc.
    - **Add** `self.get_model(tier: LLMTier)` which calls Factory.
    - **Error Handling**: Wrap `run_judge_scoring` in a try/except block. If `LocalModelFailure` occurs, log warning and retry with `Tier.THINKING_CLOUD`.
    - **Centralization**: Move `generate_daily_briefing` logic inside `JennieBrain`.

#### [MODIFY] [services/daily-briefing/reporter.py](file:///home/youngs75/projects/my-ultra-jennie/services/daily-briefing/reporter.py)
- Simplify to just call `JennieBrain.generate_daily_briefing`.

### 4. Configuration
- **`infrastructure/env-vars-wsl.yaml`**:
    - `TIER_FAST_PROVIDER`: `ollama`
    - `TIER_REASONING_PROVIDER`: `ollama`
    - `TIER_THINKING_PROVIDER`: `openai`
    - `LOCAL_MODEL_FAST`: `qwen2.5:3b`
    - `LOCAL_MODEL_REASONING`: `qwen2.5:14b`
    - `LOCAL_MODEL_THINKING`: `deepseek-r1:32b`

## Verification Plan

### Metrics (Quantifiable)
1.  **Score Divergence**: Run 10 sample Judge tasks on both Local(DeepSeek) and Cloud(GPT/Claude). Calculate average score difference. (Pass if diff < 15%)
2.  **Reliability Rate**: Run 50 Hunter tasks. Count JSON failures/Timeouts. (Pass if failure rate < 5%)
3.  **Latency**: Measure average time for Hunter task. (Pass if < 10s for 14b)

### Manual Test Steps
1.  **Hybrid Flow**: Run `scout.py`. Verify Hunter uses Local (Ollama logs) and Judge uses Cloud.
2.  **Latency Check**: Confirm ZERO 32s model swapping pauses in Hybrid mode.
3.  **Fallback Test**: Manually stop Ollama service (`docker stopollama`), run `scout.py`. Verify JennieBrain catches connection error and escalates to Cloud for Hunter tasks (or fails gracefully if strict local).
