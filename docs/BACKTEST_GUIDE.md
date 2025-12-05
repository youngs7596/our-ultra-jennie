# 📊 백테스트 가이드 (Backtest Guide)

Ultra Jennie v1.0의 백테스트 시스템 사용 가이드입니다.

---

## 📋 목차

1. [개요](#1-개요)
2. [파일 구조](#2-파일-구조)
3. [단일 백테스트 실행](#3-단일-백테스트-실행)
4. [자동 파라미터 최적화](#4-자동-파라미터-최적화)
5. [결과 분석](#5-결과-분석)
6. [최적 결과 적용](#6-최적-결과-적용)
7. [전략 프리셋](#7-전략-프리셋)
8. [트러블슈팅](#8-트러블슈팅)

---

## 1. 개요

### 백테스트란?

백테스트는 과거 데이터를 기반으로 트레이딩 전략의 성과를 시뮬레이션하는 과정입니다. Ultra Jennie의 백테스트 시스템은 다음을 검증합니다:

- **매수 조건**: RSI 과매도, 볼린저 밴드 터치, 돌파 신호
- **매도 조건**: 목표 수익률, 손절, RSI 과매수
- **포지션 관리**: 종목당/섹터당 최대 비중, 현금 유지 비율

### 핵심 지표

| 지표 | 설명 | 목표 |
|------|------|------|
| **총 수익률** | 테스트 기간 동안의 누적 수익률 | 높을수록 좋음 |
| **월간 수익률** | 월평균 수익률 | 1.4% 이상 |
| **MDD** | 최대 낙폭 (Maximum Drawdown) | 10% 이하 |
| **점수** | 종합 점수 (수익률↑ MDD↓) | 높을수록 좋음 |

---

## 2. 파일 구조

```
utilities/
├── backtest_gpt_v2.py           # 단일 백테스트 실행
├── auto_optimize_backtest_gpt_v2.py  # 자동 파라미터 최적화
├── apply_strategy_preset.py     # 최적 결과를 CONFIG에 적용

configs/
└── gpt_v2_strategy_presets.json # 검증된 전략 프리셋

shared/
└── strategy_presets.py          # 프리셋 로더 및 유틸리티

logs/
├── backtest_*.log               # 개별 백테스트 로그
└── optimize_summary/            # 최적화 결과 요약
    ├── gpt_v2_opt_summary_latest.txt
    └── gpt_v2_opt_summary_*.txt
```

---

## 3. 단일 백테스트 실행

### 기본 실행

```bash
cd /home/youngs75/projects/my-ultra-jennie
python utilities/backtest_gpt_v2.py --days 180
```

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--days` | 180 | 백테스트 기간 (일) |
| `--universe-limit` | 50 | 유니버스 종목 수 제한 |
| `--top-n` | 5 | 일일 매수 후보 상위 N개 |
| `--log-level` | INFO | 로그 레벨 |
| `--log-dir` | logs | 로그 저장 디렉토리 |

### 매수/매도 파라미터

```bash
python utilities/backtest_gpt_v2.py \
  --days 180 \
  --rsi-buy 30 \
  --target-profit-pct 8.0 \
  --base-stop-loss-pct 5.0 \
  --llm-threshold 70 \
  --max-position-allocation 12.0 \
  --max-hold-days 30
```

### 파라미터 상세

#### 매수 조건
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `--rsi-buy` | 30 | RSI 과매도 기준 (이하일 때 매수 신호) |
| `--breakout-buffer-pct` | 0.5 | 돌파 버퍼 (%) |
| `--bb-buffer-pct` | 1.5 | 볼린저 밴드 버퍼 (%) |
| `--llm-threshold` | 70 | LLM 점수 최소 기준 |

#### 매도 조건
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `--target-profit-pct` | 8.0 | 목표 수익률 (%) |
| `--base-stop-loss-pct` | 5.0 | 기본 손절 비율 (%) |
| `--stop-loss-atr-mult` | 1.8 | ATR 기반 손절 배수 |
| `--sell-rsi-1` | 70.0 | RSI 과매수 1단계 (부분 매도) |
| `--sell-rsi-2` | 75.0 | RSI 과매수 2단계 |
| `--sell-rsi-3` | 80.0 | RSI 과매수 3단계 (전량 매도) |
| `--max-hold-days` | 30 | 최대 보유 기간 (일) |

#### 포지션 관리
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `--max-position-allocation` | 12.0 | 종목당 최대 비중 (%) |
| `--max-stock-pct` | 15.0 | 단일 종목 최대 비중 (%) |
| `--max-sector-pct` | 30.0 | 섹터당 최대 비중 (%) |
| `--cash-keep-pct` | 5.0 | 현금 유지 비율 (%) |
| `--max-buys-per-day` | 3 | 일일 최대 매수 횟수 |

### 출력 결과

```
=== 백테스트 결과 ===
최종 누적 수익률: 19.14%
최종 자산: 178,710,000원 (초기: 150,000,000원)
최대 낙폭(MDD): -6.08%
월간 수익률: 4.44% (목표: 1.4%)
누적 거래 횟수: 156회 | 보유 중인 포지션: 3개
--- ✅ 백테스트 완료 ---
```

---

## 4. 자동 파라미터 최적화

### 개요

`auto_optimize_backtest_gpt_v2.py`는 다양한 파라미터 조합을 병렬로 테스트하여 최적의 전략을 찾습니다.

### 기본 실행

```bash
# 빠른 테스트 (30개 조합, 90일)
python utilities/auto_optimize_backtest_gpt_v2.py \
  --days 90 \
  --max-combinations 30 \
  --max-workers 4

# 정밀 테스트 (100개 조합, 180일)
python utilities/auto_optimize_backtest_gpt_v2.py \
  --days 180 \
  --max-combinations 100 \
  --max-workers 4
```

### 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--days` | 180 | 각 조합 테스트 기간 |
| `--max-combinations` | 80 | 최대 테스트할 조합 수 |
| `--max-workers` | CPU/2 | 병렬 프로세스 수 |
| `--universe-limit` | 50 | 종목 수 제한 |
| `--top-n` | 5 | 상위 N개 종목 |
| `--timeout` | 600 | 각 백테스트 타임아웃 (초) |
| `--resume` | - | 이전 결과 파일에서 이어하기 |

### 탐색 파라미터 그리드

최적화는 다음 파라미터 조합을 탐색합니다:

```python
PARAMETER_GRID = {
    # 매수 조건
    "rsi_buy": [25, 30, 35],
    "breakout_buffer_pct": [0.3, 0.5, 0.8],
    "bb_buffer_pct": [1.0, 1.5, 2.0],
    "llm_threshold": [65, 70, 75],

    # 포지션 관리
    "max_position_allocation": [10.0, 12.0, 15.0],
    "max_stock_pct": [12.0, 15.0],
    "max_sector_pct": [30.0, 35.0],
    "cash_keep_pct": [3.0, 5.0],

    # 매도 조건
    "target_profit_pct": [6.0, 8.0, 10.0],
    "base_stop_loss_pct": [4.0, 5.0, 6.0],
    "stop_loss_atr_mult": [1.6, 1.8, 2.0],
    "sell_rsi_1": [68.0, 70.0],
    "sell_rsi_2": [73.0, 75.0],
    "sell_rsi_3": [78.0, 80.0],

    # 실행 빈도
    "max_buys_per_day": [3, 4],
    "max_hold_days": [25, 30, 35],
}
```

### 점수 계산 공식

```python
score = total_return * 2.0 + monthly_return * 8.0 - mdd * 6.0

# MDD가 12%를 넘으면 강한 페널티
if mdd > 12.0:
    score -= (mdd - 12.0) * 20.0
```

### 이어하기 (Resume)

중단된 최적화를 이어서 실행:

```bash
python utilities/auto_optimize_backtest_gpt_v2.py \
  --resume gpt_v2_opt_results_20251205_201355.json
```

---

## 5. 결과 분석

### 결과 파일

| 파일 | 설명 |
|------|------|
| `gpt_v2_opt_results_{timestamp}.json` | 전체 결과 (JSON) |
| `logs/optimize_summary/gpt_v2_opt_summary_latest.txt` | 최신 요약 |
| `logs/opt_runs/backtest_*.log` | 개별 백테스트 로그 |

### 요약 파일 확인

```bash
cat logs/optimize_summary/gpt_v2_opt_summary_latest.txt
```

출력 예시:
```
=== GPT v2 Optimization Summary ===
생성 시각: 2025-12-05T20:14:44.421391
총 테스트 조합: 100

🏅 Best Combination
- Score 110.29 | Total 19.14% | Monthly 4.44% | MDD -6.08% | Elapsed 45.2s
- Params: {"rsi_buy": 35, "target_profit_pct": 6.0, ...}

Top 10 Results
01. Score 110.29 | Total 19.14% | Monthly 4.44% | MDD -6.08%
02. Score 87.12 | Total 12.83% | Monthly 3.04% | MDD -6.20%
...
```

### 결과 해석

| 점수 범위 | 평가 |
|----------|------|
| 100+ | ⭐⭐⭐ 우수 |
| 70~100 | ⭐⭐ 양호 |
| 50~70 | ⭐ 보통 |
| 50 미만 | ❌ 개선 필요 |

---

## 6. 최적 결과 적용

### 프리셋 목록 확인

```bash
python -c "from shared.strategy_presets import list_preset_names; print(list_preset_names())"
```

### Dry Run (미리보기)

```bash
python utilities/apply_strategy_preset.py --preset balanced_champion --dry-run
```

출력:
```
[DRY RUN] 아래 값이 업데이트될 예정입니다:
  BUY_RSI_OVERSOLD_THRESHOLD = 35
  PROFIT_TARGET_FULL = 8.0
  SELL_STOP_LOSS_PCT = 6.0
  ...
```

### 실제 적용

```bash
python utilities/apply_strategy_preset.py --preset balanced_champion
```

### 수동으로 새 프리셋 추가

`configs/gpt_v2_strategy_presets.json` 파일에 추가:

```json
{
  "my_custom_preset": {
    "label": "My Custom Strategy",
    "description": "백테스트로 검증된 커스텀 전략",
    "params": {
      "rsi_buy": 35,
      "target_profit_pct": 6.0,
      "base_stop_loss_pct": 6.0,
      "llm_threshold": 65,
      "max_position_allocation": 15.0,
      "max_stock_pct": 15.0,
      "max_sector_pct": 30.0,
      "cash_keep_pct": 5.0,
      "stop_loss_atr_mult": 2.0,
      "sell_rsi_1": 70.0,
      "sell_rsi_2": 73.0,
      "sell_rsi_3": 78.0,
      "max_buys_per_day": 3,
      "max_hold_days": 25
    }
  }
}
```

---

## 7. 전략 프리셋

### 기본 제공 프리셋

| 프리셋 | 설명 | 시장 국면 |
|--------|------|----------|
| `balanced_champion` | 수익+안정성 균형 | SIDEWAYS |
| `aggressive_swing` | 공격적 추세 추종 | BULL |
| `iron_shield` | 최저 MDD 방어 | BEAR |

### 프리셋 상세

#### balanced_champion (균형형)
```json
{
  "rsi_buy": 35,
  "target_profit_pct": 8.0,
  "base_stop_loss_pct": 6.0,
  "llm_threshold": 65,
  "max_hold_days": 25
}
```

#### aggressive_swing (공격형)
```json
{
  "rsi_buy": 30,
  "target_profit_pct": 10.0,
  "base_stop_loss_pct": 6.0,
  "llm_threshold": 65,
  "max_buys_per_day": 4,
  "cash_keep_pct": 3.0
}
```

#### iron_shield (방어형)
```json
{
  "rsi_buy": 35,
  "target_profit_pct": 8.0,
  "base_stop_loss_pct": 4.0,
  "llm_threshold": 75,
  "max_hold_days": 35
}
```

### 시장 국면별 자동 선택

실서비스에서는 `MarketRegimeDetector`가 시장 국면을 감지하고, 자동으로 적절한 프리셋을 선택합니다:

```python
from shared.strategy_presets import resolve_preset_for_regime

preset_name, params = resolve_preset_for_regime("BULL")
# → ("aggressive_swing", {...})
```

---

## 8. v1.0 개선사항

### 8.1 LLM 점수 추정 로직 개선

기존에는 단순 추정 공식을 사용했으나, v1.0에서는 **DB Watchlist 우선 조회** 방식으로 개선되었습니다.

#### 동작 방식

```python
def _estimate_llm_score(self, code, factor_score, raw_score, signal):
    # 1. DB Watchlist에서 실제 Scout 결과 조회
    db_llm_score = self.watchlist_cache.get(code, {}).get('llm_score')
    if db_llm_score is not None and db_llm_score > 0:
        return float(db_llm_score)  # 실제 Scout 결과 사용
    
    # 2. DB에 없으면 개선된 추정 공식 사용
    # 기본점수 50 + 팩터 기여(35%) + 신호 기여(8%) + 신호 보너스 + 노이즈
```

#### 신호 유형별 보너스 (실제 Scout 결과 분석 기반)

| 신호 | 보너스 | 설명 |
|------|--------|------|
| `RES_BREAK` | +8점 | 저항선 돌파 - 강력한 모멘텀 |
| `GOLDEN_CROSS` | +6점 | 골든크로스 - 추세 전환 |
| `TREND_UP` | +4점 | 상승 추세 확인 |
| `RSI_OVERSOLD` | +3점 | 과매도 반등 기회 |
| `BB_TOUCH` | +2점 | 볼린저 밴드 터치 |

### 8.2 Death Cross 매도 조건 추가

v1.0에서 **Death Cross** 매도 조건이 추가되어 실제 `price-monitor`와 동일한 로직을 재현합니다.

#### Death Cross 조건

```python
# MA5가 MA20 아래로 떨어지면 매도 신호
if ma5 < ma20 and pos.high_price > pos.avg_price * 1.02:
    reason = "DEATH_CROSS"
```

- **조건 1**: MA5 < MA20 (데드크로스 발생)
- **조건 2**: 고점이 매수가 대비 2% 이상 (이미 상승했다가 하락)

이로써 단순 하락이 아닌, **상승 후 추세 반전** 시에만 매도하여 과도한 손절을 방지합니다.

---

## 9. 트러블슈팅

### 일반적인 오류

#### DB 연결 실패
```
❌ DB: MariaDB 연결 실패! (에러: (2003, "Can't connect to MySQL server..."))
```

**해결**: MariaDB가 실행 중인지 확인
```bash
sudo systemctl status mariadb
# 또는
mysql -u root -p -e "SELECT 1"
```

#### secrets.json 미발견
```
ℹ️ secrets.json(/app/config/secrets.json)이 존재하지 않습니다.
```

**해결**: 환경변수 설정
```bash
export SECRETS_FILE=/home/youngs75/projects/my-ultra-jennie/secrets.json
```

#### 데이터 부족
```
⚠️ 종목 XXXXXX 데이터 부족 (30일 미만)
```

**해결**: `STOCK_DAILY_PRICES_3Y` 테이블 데이터 확인

### 최적화 실패율이 높을 때

1. **타임아웃 증가**:
   ```bash
   --timeout 900
   ```

2. **종목 수 감소**:
   ```bash
   --universe-limit 30 --top-n 3
   ```

3. **로그 확인**:
   ```bash
   cat logs/opt_runs/backtest_*.log | grep -i error | tail -20
   ```

### 메모리 부족

```bash
# 워커 수 감소
--max-workers 2

# 또는 순차 실행
--max-workers 1
```

---

## 📚 관련 문서

- [Fast Hands, Slow Brain 전략](./FAST_HANDS_SLOW_BRAIN_STRATEGY.md)
- [Scout 하이브리드 스코어링](./SCOUT_HYBRID_SCORING.md)
- [README](../README.md)

---

*작성: Ultra Jennie v1.0 (2025-12-05)*

