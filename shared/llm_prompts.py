"""
shared/llm_prompts.py - LLM 프롬프트 빌더 함수들

이 모듈은 JennieBrain에서 사용하는 모든 프롬프트 빌더 함수들을 제공합니다.

[v5.0] llm.py에서 분리됨 - 프롬프트 템플릿 관리 용이성 향상
"""

import logging

logger = logging.getLogger(__name__)


# =================================================================
# 유틸리티 함수들
# =================================================================

def _format_market_cap(mc):
    """시가총액 포맷팅"""
    if not mc:
        return "N/A"
    mc_in_won = int(mc) * 1_000_000
    if mc_in_won >= 1_000_000_000_000:
        return f"{mc_in_won / 1_000_000_000_000:.1f}조 원"
    elif mc_in_won >= 100_000_000:
        return f"{mc_in_won / 100_000_000:,.0f}억 원"
    return f"{mc_in_won:,.0f} 원"


def _format_per(p):
    """PER 포맷팅"""
    if p <= 0:
        return "N/A (적자 기업)"
    return f"{p:.2f} 배"


# =================================================================
# 매수 프롬프트 빌더
# =================================================================

def build_buy_prompt_mean_reversion(stock_snapshot, buy_signal_type):
    """
    '평균 회귀' 조건이 충족되었을 때 'BUY' 결재를 위한 프롬프트를 생성합니다.
    buy_signal_type에 따라 BB 또는 RSI 조건을 동적으로 표시합니다.
    """
    # 신호 유형에 따라 조건 설명 텍스트를 동적으로 설정
    if buy_signal_type == 'BB_LOWER':
        condition_desc = "[조건 1: '과매도'] 'Agent'가 '볼린저 밴드(20,2) 하단' 터치(또는 근접)를 확인했습니다. (통과)"
    elif buy_signal_type == 'RSI_OVERSOLD':
        condition_desc = "[조건 1: '과매도'] 'Agent'가 'RSI(14) 30 이하' 진입을 확인했습니다. (통과)"
    else:
        condition_desc = f"[조건 1: '과매도'] 알 수 없는 신호({buy_signal_type}) (검토 필요)"

    code = stock_snapshot.get('code', 'N/A')
    name = stock_snapshot.get('name', 'N/A')
    current_price = stock_snapshot.get('price', 0)
    remaining_budget = stock_snapshot.get('remaining_budget', 0)
    rag_context = stock_snapshot.get('rag_context', 'N/A')
    
    prompt = f"""
    [시스템 지침]
    당신은 영석님의 AI 주식 투자 보좌관 '제니'이며 20대 중반의 한국인 여성 페르소나를 따릅니다.

    [AI 결재 요청: 'BUY' (평균 회귀)]
    영석님, Agent가 우량주 목록에서 '과매도(평균 회귀)' 신호를 포착했습니다. 최종 검토 후 결정을 내려주세요.

    1. 종목 정보:
       - 종목명: {name} ({code})
       - 현재가: {current_price:,.0f} 원

    2. 매수 조건 (Agent가 1차 검증 완료):
       - {condition_desc}
       - [조건 2: '시장 상황'] KOSPI가 20일 이동평균선 위에 있습니다. (통과)

    3. 펀더멘털 (참고):
       - PER: {_format_per(stock_snapshot.get('per', 0.0))}
       - 시가총액: {_format_market_cap(stock_snapshot.get('market_cap', 0))}

    4. 최신 뉴스 (RAG 참고):
       {rag_context}

    5. 결재 (JSON):
       - 남은 예산: {remaining_budget:,.0f} 원
       - 위 모든 정보를 종합하여 'APPROVE' 또는 'REJECT'를 결정해주세요.
       - 만약 'APPROVE'한다면, 남은 예산과 현재가를 고려하여 매수할 수량(quantity)을 '정수'로 결정해주세요.
       - 수량 결정 가이드:
         1. (기본) 1주 매수를 기본으로 합니다.
         2. (확신) 뉴스가 매우 긍정적이거나 신호가 강력하다고 판단되면, 예산 내에서 2주 이상 매수를 고려할 수 있습니다.
         3. (주의) 총 매수 금액(현재가 * 수량)이 남은 예산을 절대 초과해서는 안 됩니다.
       - 'REJECT' 시에는 quantity를 0으로 설정해주세요.
    """
    return prompt.strip()


def build_buy_prompt_golden_cross(stock_snapshot, buy_signal_type='GOLDEN_CROSS'):
    """
    '추세 돌파' 전략들(골든 크로스, 모멘텀, 상대 강도, 저항선 돌파) 조건이 충족되었을 때 'BUY' 결재를 위한 프롬프트를 생성합니다.
    """
    code = stock_snapshot.get('code', 'N/A')
    name = stock_snapshot.get('name', 'N/A')
    current_price = stock_snapshot.get('price', 0)
    remaining_budget = stock_snapshot.get('remaining_budget', 0)
    rag_context = stock_snapshot.get('rag_context', 'N/A')
    
    # 신호 타입에 따른 조건 설명
    signal_descriptions = {
        'GOLDEN_CROSS': "[조건 1: '추세 돌파'] 'Agent'가 '5일 이평선 > 20일 이평선' 골든 크로스를 확인했습니다. (통과)",
        'MOMENTUM': "[조건 1: '모멘텀'] 'Agent'가 최근 5일간 3% 이상의 강한 상승세를 확인했습니다. (통과)",
        'RELATIVE_STRENGTH': "[조건 1: '상대 강도'] 'Agent'가 KOSPI 대비 2%p 이상 강한 상대 강도를 확인했습니다. (통과)",
        'RESISTANCE_BREAKOUT': "[조건 1: '저항선 돌파'] 'Agent'가 최근 20일 고점을 돌파한 것을 확인했습니다. (통과)"
    }
    condition_desc = signal_descriptions.get(buy_signal_type, "[조건 1: '추세 돌파'] 신호 포착 (통과)")

    prompt = f"""
    [시스템 지침]
    당신은 영석님의 AI 주식 투자 보좌관 '제니'이며 20대 중반의 한국인 여성 페르소나를 따릅니다.

    [AI 결재 요청: 'BUY' (추세 추종)]
    영석님, Agent가 우량주 목록에서 '추세 추종' 신호를 포착했습니다. 최종 검토 후 결정을 내려주세요.

    1. 종목 정보:
       - 종목명: {name} ({code})
       - 현재가: {current_price:,.0f} 원

    2. 매수 조건 (Agent가 1차 검증 완료):
       - {condition_desc}
       - [조건 2: '시장 상황'] 시장이 상승 추세에 있습니다. (통과)

    3. 펀더멘털 (참고):
       - PER: {_format_per(stock_snapshot.get('per', 0.0))}
       - 시가총액: {_format_market_cap(stock_snapshot.get('market_cap', 0))}

    4. 최신 뉴스 (RAG 참고):
       {rag_context}

    5. 결재 (JSON):
       - 남은 예산: {remaining_budget:,.0f} 원
       - 위 모든 정보를 종합하여 'APPROVE' 또는 'REJECT'를 결정해주세요.
       - 만약 'APPROVE'한다면, 남은 예산과 현재가를 고려하여 매수할 수량(quantity)을 '정수'로 결정해주세요.
       - 수량 결정 가이드:
         1. (기본) 1주 매수를 기본으로 합니다.
         2. (확신) 뉴스가 매우 긍정적이거나 신호가 강력하다고 판단되면, 예산 내에서 2주 이상 매수를 고려할 수 있습니다.
         3. (주의) 총 매수 금액(현재가 * 수량)이 남은 예산을 절대 초과해서는 안 됩니다.
       - 'REJECT' 시에는 quantity를 0으로 설정해주세요.
    """
    return prompt.strip()


def build_buy_prompt_ranking(candidates_data: list) -> str:
    """
    [v2.5] 'Top N 매수 후보' 랭킹 결재를 위한 프롬프트를 생성합니다.
    
    Args:
        candidates_data: 팩터 점수 상위 N개 후보 리스트 (각 후보는 dict 형태, 최대 5개)
    
    Returns:
        str: LLM에 전달할 프롬프트
    """
    
    # 후보들의 데이터를 프롬프트에 직렬화
    candidates_prompt_part = ""
    for i, candidate in enumerate(candidates_data, 1):
        factors = candidate['factors']
        
        # 팩터 상세 점수 포맷팅
        momentum_score = factors.get('momentum_score', 0)
        quality_score = factors.get('quality_score', 0)
        value_score = factors.get('value_score', 0)
        technical_score = factors.get('technical_score', 0)
        
        # RAG 컨텍스트 포맷팅 (너무 길면 축약)
        rag_text = candidate['rag_context'] if candidate['rag_context'] else '최신 뉴스 없음'
        if len(rag_text) > 500:
            rag_text = rag_text[:500] + "... (이하 생략)"
        
        # PER, PBR 포맷팅
        stock_info = candidate['stock_info']
        per_value = stock_info.get('per', 0)
        pbr_value = stock_info.get('pbr', 0)
        per_str = f"{per_value:.2f}배" if per_value and per_value > 0 else "N/A (적자)"
        pbr_str = f"{pbr_value:.2f}배" if pbr_value and pbr_value > 0 else "N/A"
        
        candidates_prompt_part += f"""
    ---
    [후보 {i}: {candidate['stock_name']} ({candidate['stock_code']})]
    - (코드) 팩터 점수: {candidate['factor_score']:.2f} / 1000
    - (코드) 기술적 신호: {candidate['buy_signal_type']}
    - (코드) 팩터 상세: 
      * 모멘텀: {momentum_score:.1f}/100
      * 품질: {quality_score:.1f}/100
      * 가치: {value_score:.1f}/100
      * 기술: {technical_score:.1f}/100
    - (펀더멘털) PER: {per_str}, PBR: {pbr_str}
    - (뉴스) RAG 최신 뉴스: 
      {rag_text}
    - (참고) Agent 계산 수량: {stock_info.get('calculated_quantity', 1)}주
    - (참고) 현재가: {candidate['current_price']:,.0f}원
    """
    
    prompt = f"""
[시스템 지침]
당신은 영석님의 AI 주식 투자 보좌관 '제니'이며, 최고의 퀀트 애널리스트입니다.
'Agent'가 v2.4 팩터 점수 기준으로 1차 필터링한 'Top {len(candidates_data)}개' 매수 후보 목록입니다.

[AI 결재 요청: 'BUY' (Top-N 랭킹)]
당신의 임무는 이 후보들을 '종합적'으로 비교 분석하여,
**'지금 당장 매수해야 할 단 하나의 최고 종목(The Single Best Pick)'**을 선정하는 것입니다.

[결정 가이드]
1. **(종합 비교)** 팩터 점수(코드 분석)가 가장 중요하지만, 이것이 절대적인 기준은 아닙니다.

2. **(RAG 교차 검증)** 팩터 점수가 높아도 RAG 뉴스(맥락)에 'CEO 리스크', '실적 악화', '대규모 매도' 등 **명백한 악재**가 있다면 순위에서 제외해야 합니다.

3. **(최종 선정)** 반대로, 팩터 점수가 2~3위라도 RAG 뉴스에 '대규모 수주', '어닝 서프라이즈' 등 **강력한 호재**가 있다면 1위로 선정할 수 있습니다.

4. **(전체 거절)** 만약 모든 후보가 악재가 있거나 매수 매력이 없다면, 'REJECT_ALL'을 선택하십시오.

5. **(수량 결정)** 최종 선정한 종목의 경우, Agent가 계산한 수량을 기본으로 하되, 확신도에 따라 조정할 수 있습니다.
   - 매우 확신: Agent 수량 그대로
   - 보통 확신: Agent 수량의 70~80%
   - 약간 불확실: Agent 수량의 50%

[후보 목록]
{candidates_prompt_part}
---

[결재 (JSON)]
위 결정 가이드에 따라 '단 하나의 최고 종목'을 선정하고, JSON 스키마에 맞춰 응답해주세요.
(반드시 `RANKING_RESPONSE_SCHEMA`의 `best_stock_code`, `reason`, `quantity` 필드를 포함해야 합니다.)

**중요**: 
- `best_stock_code`에는 반드시 위 후보 리스트에 있는 종목 코드 중 하나를 선택하거나, 모두 거절하려면 'REJECT_ALL'을 입력하세요.
- `reason`에는 선택한 종목이 왜 다른 후보들보다 우수한지, RAG 뉴스와 팩터 점수를 모두 고려한 종합적인 분석을 작성하세요.
- `quantity`는 'REJECT_ALL'이 아닌 경우에만 양의 정수를 입력하세요.
    """
    return prompt.strip()


# =================================================================
# 매도 프롬프트 빌더
# =================================================================

def build_sell_prompt(stock_info):
    """
    '수익 실현' (RSI 과열) 신호가 발생한 보유 종목에 대한 'SELL' 결재 프롬프트를 생성합니다.
    (stock_info는 'Portfolio' DB 딕셔너리)
    """
    
    name = stock_info.get('name', 'N/A')
    avg_price = stock_info.get('avg_price', 0)
    high_price = stock_info.get('high_price', 0)
    
    prompt = f"""
    [시스템 지침]
    당신은 영석님의 AI 주식 투자 보좌관 '제니'이며 20대 중반의 한국인 여성 페르소나를 따릅니다.

    [AI 결재 요청: 'SELL' (수익 실현)]
    영석님, Agent가 보유 종목에서 'RSI 과열(수익 실현)' 신호를 포착했습니다. 'SELL' 또는 'HOLD' 결정을 JSON으로 응답해주세요.

    1. 종목 정보:
       - 종목명: {name}
       - 종목코드: {stock_info.get('code', 'N/A')}

    2. 매도 조건 (Agent가 1차 검증 완료):
       - [신호]: 실시간 RSI가 '75' 이상 과열 구간에 진입했습니다. (통과)
       - [참고: 매수가]: {avg_price:,.0f} 원
       - [참고: 현재 고점]: {high_price:,.0f} 원

    3. 결재 (JSON):
       'SELL' 또는 'HOLD' 중 하나를 선택하고, 그 사유를 간결하게 작성하여 JSON으로 응답해주세요. quantity는 0으로 설정해주세요.
    """
    return prompt.strip()


def build_add_watchlist_prompt(stock_info):
    """관심 종목 편입 결재 프롬프트"""
    prompt = f"""
    [시스템 지침]
    당신은 영석님의 AI 주식 투자 보좌관 '제니'이며, 잠재적 투자 대상을 발굴하는 '수석 애널리스트' 역할을 수행합니다.
    [AI 결재 요청: 'ADD_WATCHLIST' (관심 종목 편입)]
    영석님, Scout이 발굴한 유망 종목 후보입니다. 아래 근거를 종합적으로 검토하여 Watchlist 편입 여부를 결정해주세요.
    1. 종목 정보:
       - 종목명: {stock_info.get('name', 'N/A')} ({stock_info.get('code', 'N/A')})
    2. 편입 근거 (Scout이 1차 분석):
       - 기술적 분석: {stock_info.get('technical_reason', '해당 없음')}
       - 뉴스/공시 분석 (RAG): {stock_info.get('news_reason', '해당 없음')}
    3. 펀더멘털 (참고):
       - PER: {stock_info.get('per', 'N/A'):.2f} 배
       - PBR: {stock_info.get('pbr', 'N/A'):.2f} 배
       - 시가총액: {_format_market_cap(stock_info.get('market_cap', 0))}
    4. 결재 (JSON):
       'APPROVE' 또는 'REJECT' 중 하나를 선택하고, 그 사유를 간결하게 작성하여 JSON으로 응답해주세요. quantity는 0으로 설정해주세요.
    """
    return prompt.strip()


# =================================================================
# 분석 프롬프트 빌더
# =================================================================

def build_analysis_prompt(stock_info):
    """종목 심층 분석 프롬프트 생성"""
    
    # [v4.0] 제니 피드백 반영 - 명확한 점수 계산
    news = stock_info.get('news_reason', '특별한 뉴스 없음')
    per = stock_info.get('per', 'N/A')
    pbr = stock_info.get('pbr', 'N/A')
    momentum = stock_info.get('momentum_score', 0)
    
    prompt = f"""당신은 주식 분석 AI입니다. 아래 종목을 분석하고 점수를 매기세요.

종목: {stock_info.get('name', 'N/A')} ({stock_info.get('code', 'N/A')})
시가총액: {_format_market_cap(stock_info.get('market_cap', 0))}
PER: {per}
PBR: {pbr}
모멘텀: {momentum}%
뉴스: {news}

## 점수 계산 (기본 50점에서 시작)

1. 뉴스 점수:
   - 호재(수주, 실적 호조): +15~25점
   - 긍정 뉴스: +5~10점  
   - 뉴스 없음: 0점
   - 악재: -10~20점

2. 펀더멘털:
   - PER<10: +10점
   - PBR<1: +5점
   - PER>30: -10점

3. 모멘텀:
   - 양수: +5점
   - 음수: -5점

## 등급
- S(80+): 강력추천
- A(70-79): 추천
- B(60-69): 관심
- C(50-59): 중립
- D(40-49): 주의
- F(<40): 회피

JSON으로 응답: {{"score": 숫자, "grade": "등급", "reason": "이유"}}

**점수는 반드시 50점 기준으로 가감하여 계산하세요. 뉴스가 없고 펀더멘털이 적정하면 약 55점입니다.**"""
    return prompt.strip()


def build_parameter_verification_prompt(current_params: dict, new_params: dict,
                                        current_performance: dict, new_performance: dict,
                                        market_summary: str) -> str:
    """파라미터 변경 검증 프롬프트 생성"""
    
    # 변경 폭 계산
    change_analysis = []
    for key, new_value in new_params.items():
        if key in current_params:
            current_value = float(current_params[key])
            new_value_float = float(new_value)
            change_pct = ((new_value_float - current_value) / current_value) * 100 if current_value != 0 else 0
            change_analysis.append(
                f"  - {key}: {current_value} → {new_value_float} (변경폭: {change_pct:+.1f}%)"
            )
    
    change_summary = "\n".join(change_analysis) if change_analysis else "  (변경 없음)"
    
    prompt = f"""
    [시스템 지침]
    당신은 최고의 AI 퀀트 전략 분석가입니다. 자동화된 백테스트 시스템이 찾아낸 새로운 전략 파라미터를 검증하고, 이 변경이 논리적으로 타당하며 과최적화(overfitting)의 위험이 없는지 최종 승인하는 임무를 맡았습니다.
    
    [Context]
    - 분석 기간: 최근 90일
    - 최근 시장 요약: "{market_summary}"
    
    [Current Strategy (AS-IS)]
    - 현재 성과(90일): MDD {current_performance['mdd']:.2f}%, 수익률 {current_performance['return']:.2f}%
    
    [Proposed Strategy (TO-BE)]
    - 제안 성과(90일): MDD {new_performance['mdd']:.2f}%, 수익률 {new_performance['return']:.2f}%
    
    [Parameter Changes]
    {change_summary}
    
    [Your Task - Critical Analysis]
    
    1. **Logical Validity (논리적 타당성)**
       - 제안된 파라미터 변경이 주어진 시장 요약과 논리적으로 부합합니까?
       - 변경 방향이 합리적입니까? (예: 변동성 증가 시 손절 기준 완화는 위험)
    
    2. **Overfitting Risk (과최적화 위험)**
       - 성과 향상이 과도하지 않습니까? (MDD 개선 + 수익률 향상이 동시에 크면 의심)
       - 백테스트 기간(90일)이 충분히 다양한 시장 상황을 포함했습니까?
    
    3. **Safety Guardrail (안전장치) - CRITICAL**
       - **모든 파라미터 변경 폭이 기존 값 대비 ±10% 이내입니까?**
       - 만약 10%를 초과하는 변경이 있다면, 매우 위험하므로 'REJECT' 하십시오.
    
    4. **Performance Improvement (성과 개선)**
       - MDD 개선: {new_performance['mdd'] - current_performance['mdd']:.2f}%p
       - 수익률 개선: {new_performance['return'] - current_performance['return']:.2f}%p
       - 이 개선이 실질적이고 지속 가능합니까?
    
    5. **Final Decision**
       - 종합적으로 판단하여 이 파라미터 변경을 실시간 거래에 적용하는 것을 승인(true) 또는 거절(false) 하십시오.
       - 신뢰도 점수(0.0~1.0)를 함께 제공하십시오.
         * 0.9~1.0: 매우 확신, 즉시 적용 권장
         * 0.7~0.9: 확신, 적용 가능
         * 0.5~0.7: 보통, 신중한 적용
         * 0.0~0.5: 불확실, 적용 비권장
    
    [Response Format]
    JSON 형식으로 응답하십시오:
    {{
      "is_approved": <true or false>,
      "reasoning": "<상세한 분석 및 판단 근거>",
      "confidence_score": <0.0 to 1.0>
    }}
    """
    return prompt.strip()


def build_news_sentiment_prompt(news_title, news_summary):
    """뉴스 감성 분석 프롬프트"""
    prompt = f"""
    [금융 뉴스 감성 분석]
    당신은 '금융 전문가'입니다. 아래 뉴스를 보고 해당 종목에 대한 호재/악재 여부를 점수로 판단해주세요.
    
    - 뉴스 제목: {news_title}
    - 뉴스 내용: {news_summary}
    
    [채점 기준]
    - 80 ~ 100점 (강력 호재): 실적 서프라이즈, 대규모 수주, 신기술 개발, 인수합병, 배당 확대
    - 60 ~ 79점 (호재): 긍정적 전망 리포트, 목표가 상향
    - 40 ~ 59점 (중립): 단순 시황, 일반적인 소식, 이미 반영된 뉴스
    - 20 ~ 39점 (악재): 실적 부진, 목표가 하향
    - 0 ~ 19점 (강력 악재): 어닝 쇼크, 유상증자(악재성), 횡령/배임, 계약 해지, 규제 강화
    
    [출력 형식]
    JSON으로 응답: {{ "score": 점수(int), "reason": "판단 이유(한 문장)" }}
    """
    return prompt.strip()


def build_debate_prompt(stock_info: dict, hunter_score: int = 0) -> str:
    """
    [Debate] Junho vs Minji Dynamic Role Allocation
    - Hunter Score >= 50 (Positive Sentiment):
        Junho (Bull): Momentum Trader ("추세를 즐겨라")
        Minji (Bear): Risk Manager ("과열을 경계하라") -> Devil's Advocate
    - Hunter Score < 50 (Negative Sentiment):
        Minji (Bull): Value Investor ("저평가 매수 기회다") -> Devil's Advocate
        Junho (Bear): Conservative Strategist ("아직 바닥 아니다")
    """
    name = stock_info.get('name', 'N/A')
    code = stock_info.get('code', 'N/A')
    news_reason = stock_info.get('news_reason', 'N/A')
    
    # Dynamic Role Assignment: Roles switch, but FRAMES remain constant.
    # The Conflict comes from colliding these Frames against the Market Situation.
    
    if hunter_score >= 50:
        # [Scenario: Positive/Bullish Market]
        # Minji (Risk Frame) checks the "Overheated" reality -> becomes Bear
        # Junho (Opportunity Frame) sees the "Growth" reality -> becomes Bull
        market_mood = "Positive/Overheated"
        
        bull_persona = """
    **2. 준호 (Bull - Opportunity Frame)**:
    - [Identity]: Macro Strategist & Momentum Believer.
    - [Frame]: "기회비용(Opportunity Cost)" 관점. "이 파도를 놓치면 후회한다."
    - [Logic]: 거시경제 흐름, 수급의 폭발력, 성장 스토리(Dream)에 집중.
    - [Style]: "물 들어올 때 노 저어야지", "이건 구조적 변화의 시작이야"
        """
        
        bear_persona = """
    **2. 민지 (Bear - Risk Frame)**:
    - [Identity]: Technical Analyst & Risk Manager.
    - [Frame]: "손실방어(Downside Protection)" 관점. "틀렸을 때 얼마나 아픈가?"
    - [Logic]: 기술적 과열(RSI/Bollinger), 밸류에이션 부담, 차익실현 리스크에 집중.
    - [Style]: "숫자는 과열이라고 말합니다", "지금 들어가면 상투 잡을 확률이 높아요"
        """
    else:
        # [Scenario: Negative/Bearish Market]
        # Minji (Risk Frame) checks the "Oversold" reality -> becomes Bull (Buying Safety)
        # Junho (Opportunity Frame) sees the "Broken Trend" reality -> becomes Bear (Cash is King)
        market_mood = "Negative/Fearful"
        
        bull_persona = """
    **1. 민지 (Bull - Value Frame)**:
    - [Identity]: Technical Analyst & Risk Manager.
    - [Frame]: "안전마진(Margin of Safety)" 관점. "더 잃을 게 없는 자리인가?"
    - [Logic]: 기술적 과매도, 역사적 저점 지지선, 펀더멘털 대비 과도한 공포에 집중.
    - [Style]: "데이터상 명백한 과매도입니다", "리스크보다 기대수익이 큰 구간이에요"
        """
        
        bear_persona = """
    **2. 준호 (Bear - Macro Frame)**:
    - [Identity]: Macro Strategist & Momentum Believer.
    - [Frame]: "추세추종(Trend Following)" 관점. "추세가 죽었는데 왜 사는가?"
    - [Logic]: 매크로 환경 악화, 하락 사이클 진입, 모멘텀 소멸에 집중.
    - [Style]: "떨어지는 칼날은 잡는 거 아냐", "바닥 확인하고 들어가도 늦지 않아"
        """

    prompt = f"""
    [Roleplay Simulation: The Wise Men's Debate (Frame Clash Mode)]
    당신은 투자 위원회의 '서기'입니다. 
    서로 다른 **해석 프레임(Frame)**을 가진 두 전문가가 
    현재 시장 상황({market_mood})을 두고 치열하게 논쟁하는 시나리오를 작성하세요.
    **단순한 말싸움이 아니라, '관점의 차이'가 명확히 드러나야 합니다.**

    [종목 정보]
    - 종목: {name} ({code})
    - 재료/뉴스: {news_reason}
    - 펀더멘털: PER {stock_info.get('per', 'N/A')}, PBR {stock_info.get('pbr', 'N/A')}
    - 시가총액: {stock_info.get('market_cap', 'N/A')}
    - Hunter Score: {hunter_score}점

    [등장인물 설정]
    {bull_persona}
    {bear_persona}

    [작성 지침]
    1. 총 4~6턴의 티키타카(대화)를 작성하세요.
    2. 서로의 이름을 부르며 자연스럽게 대화하세요.
    3. **절대 합의하지 마세요.** 프레임이 다르므로 평행선을 달리는 것이 정상입니다.
    4. **한국어**로 작성하고, 전문 용어를 섞어서 리얼리티를 살리세요.
    5. 결론을 내지 말고, 싸우는 상태로 끝내세요. (판단은 제니가 합니다)
    
    [출력 예시]
    준호: 민지 쌤, 이 뉴스 봤어? 글로벌 수급이 이쪽으로 쏠리고 있어. 이건 빅 사이클이야.
    민지: 팀장님, 흥분하지 마세요. 수급은 들어왔지만 RSI가 85입니다. 기술적으로는 명백한 과열이에요.
    준호: 아 답답하네. 대세 상승장에선 보조지표가 무의미해! 지금 안 사면 1년 뒤에 후회할 걸?
    민지: 하지만 1년 뒤가 아니라 당장 내일 급락하면요? 우리는 리스크를 관리해야 합니다. 
    ...
    """
    return prompt.strip()


def build_judge_prompt(stock_info: dict, debate_log: str) -> str:
    """Judge 최종 판결 프롬프트"""
    name = stock_info.get('name', 'N/A')
    news_reason = stock_info.get('news_reason', 'N/A')
    per = stock_info.get('per', 'N/A')
    pbr = stock_info.get('pbr', 'N/A')
    market_cap = stock_info.get('market_cap', 'N/A')
    
    prompt = f"""당신은 주식 투자 최종 판결자입니다. Bull과 Bear의 토론을 듣고 최종 점수를 매기세요.

## 종목 정보
- 종목: {name}
- PER: {per}, PBR: {pbr}
- 시가총액: {market_cap}

## 최신 뉴스/재료
{news_reason}

## Debate Log (Bull vs Bear 토론)
{debate_log}

## 점수 계산 (기본 50점에서 시작)

1. **토론 결과 가감점**:
   - Bull이 구체적 수치로 압승: +20~30점
   - Bull이 논리적 우세: +10~15점
   - 팽팽함 (무승부): 0점
   - Bear가 우세: -10~15점
   - Bear가 치명적 약점 지적 (적자, 고PER, 악재): -20~30점

2. **펀더멘털 가감점**:
   - PER<10, PBR<1 (저평가): +10점
   - PER>30 (고평가): -10점

3. **뉴스 가감점**:
   - 확실한 호재: +10점
   - 악재: -15점

## 등급
- S(80+): 강력매수
- A(70-79): 매수추천
- B(60-69): 관심
- C(50-59): 중립
- D(40-49): 주의
- F(<40): 회피

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판결 이유"}}

**중요: 기본 50점에서 시작하여 토론 결과에 따라 가감하세요. Bull과 Bear가 팽팽하면 50~55점입니다.**"""
    return prompt.strip()


def build_hunter_prompt_v5(stock_info: dict, quant_context: str = None) -> str:
    """
    [v1.0] 정량 통계 컨텍스트가 포함된 Hunter 프롬프트 생성
    
    GPT 설계 핵심: "이 통계는 중요한 판단 근거이니 반드시 반영하세요"
    """
    name = stock_info.get('name', 'N/A')
    code = stock_info.get('code', 'N/A')
    news = stock_info.get('news_reason', '특별한 뉴스 없음')
    
    # 정량 컨텍스트가 없으면 기존 방식으로 폴백
    if not quant_context:
        return build_analysis_prompt(stock_info)
    
    prompt = f"""당신은 데이터 기반 주식 분석 AI입니다. **정량 분석 결과를 반드시 참고**하여 점수를 매기세요.

## 종목 정보
종목: {name} ({code})

{quant_context}

## 최신 뉴스 (정성적 판단 영역)
{news}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## [중요] 당신의 역할

**정량 분석은 이미 완료되었습니다.** 당신은 다음 **정성적 요소만** 평가하세요:

1. **뉴스 맥락 해석**: 이 뉴스가 단기 이벤트인지, 펀더멘털 변화인지?
2. **리스크 체크**: CEO 리스크, 횡령, 규제 등 정량 분석이 놓친 위험 요소?
3. **타이밍 판단**: 이미 반영된 재료인지, 아직 미반영인지?

## 점수 계산 방식

**기준: 정량 점수를 기반으로 ±20점 범위 내에서 조정**

위 정량 점수가 70점이라면:
- 뉴스가 매우 긍정적 + 리스크 없음 → 80~90점
- 뉴스 중립 → 65~75점 (정량 점수 유지)
- 숨겨진 리스크 발견 → 50~60점
- 치명적 악재 발견 → 40점 미만

## 등급
- S(80+): 강력추천 - 정량+정성 모두 우수
- A(70-79): 추천 - 정량 우수 + 정성 양호
- B(60-69): 관심 - 정량 또는 정성 중 하나 우수
- C(50-59): 중립
- D(40-49): 주의 - 리스크 발견
- F(<40): 회피 - 치명적 리스크

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판단 이유"}}

⚠️ **중요**: 위 정량 분석의 조건부 승률과 표본 수는 역사적 데이터입니다. 
표본 수가 30개 이상이면 신뢰할 수 있고, 15개 미만이면 보수적으로 판단하세요."""

    return prompt.strip()


def build_judge_prompt_v5(stock_info: dict, debate_log: str, quant_context: str = None) -> str:
    """[v1.0] 정량 컨텍스트 포함 Judge 판결 프롬프트"""
    if not quant_context:
        return build_judge_prompt(stock_info, debate_log)
    
    name = stock_info.get('name', 'N/A')
    news_reason = stock_info.get('news_reason', 'N/A')
    per = stock_info.get('per', 'N/A')
    pbr = stock_info.get('pbr', 'N/A')
    
    prompt = f"""당신은 주식 투자 최종 판결자입니다. 
**정량 분석 결과**와 **Bull vs Bear 토론**을 종합하여 최종 점수를 매기세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## [핵심] 정량 분석 결과 (반드시 참고!)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{quant_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 종목 기본 정보
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

종목: {name}
PER: {per}, PBR: {pbr}

## 최신 뉴스/재료
{news_reason}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## Bull vs Bear 토론 로그
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{debate_log[:2000]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 최종 점수 계산 (하이브리드 방식)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**기본 점수 = 정량 점수 (위 분석 결과 참조)**

토론 결과에 따라 가감:
- Bull이 논리적 압승 + 데이터 뒷받침: +10~15점
- 팽팽한 토론: ±0점
- Bear가 치명적 약점 지적: -10~20점

## 등급
- S(80+): 정량+정성 모두 우수 → 강력매수
- A(70-79): 정량 우수 + 토론에서 Bull 우세 → 매수추천
- B(60-69): 정량 양호 + 토론 팽팽 → 관심
- C(50-59): 중립
- D(40-49): 정량 부족 또는 토론에서 Bear 우세 → 주의
- F(<40): 정량+정성 모두 부정적 → 회피

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판결 이유"}}

⚠️ **중요**: 정량 점수와 조건부 승률을 무시하지 마세요. 이것은 과거 데이터 기반의 객관적 지표입니다."""
    return prompt.strip()


def build_context_analysis_prompt(stock_code: str, stock_name: str, quant_context: str, 
                                   news_summary: str = "", fundamentals: dict = None) -> str:
    """[v1.0] HybridScorer용 정량 컨텍스트 포함 분석 프롬프트"""
    fundamentals_str = ""
    if fundamentals:
        fundamentals_str = f"""
[펀더멘털 정보]
- PER: {fundamentals.get('per', 'N/A')}
- PBR: {fundamentals.get('pbr', 'N/A')}
- ROE: {fundamentals.get('roe', 'N/A')}%
- 시가총액: {fundamentals.get('market_cap', 'N/A')}
"""
    
    prompt = f"""당신은 한국 주식 투자 전문가입니다.
아래의 정량 분석 결과와 뉴스/펀더멘털 정보를 종합하여 매수 적합도 점수(0~100)를 산출하세요.

{quant_context}

{fundamentals_str}

[최근 뉴스 요약]
{news_summary if news_summary else '최근 뉴스 없음'}

## 판단 기준

⚠️ **중요**: 위 정량 분석 결과의 승률과 조건부 통계는 과거 데이터 기반의 객관적 지표입니다.
이 통계를 무시하지 말고 반드시 판단의 핵심 근거로 활용하세요.

1. **정량 점수 참조** (60점 만점 중 정량이 차지하는 비중)
   - 정량 점수 70점 이상: 기본적으로 긍정적
   - 정량 점수 50점 미만: 신중한 접근 필요

2. **조건부 승률 참조**
   - 승률 70% 이상: 강력한 매수 신호
   - 승률 50-70%: 보통
   - 승률 50% 미만: 약세 신호
   - 표본 수 30개 미만: 통계 신뢰도 낮음, 보수적 판단

3. **뉴스 맥락 분석**
   - 정량이 좋아도 치명적 악재(횡령, 분식회계)가 있으면 감점
   
   ⚠️ **역신호 경고 (v1.0 팩터 분석 결과)**:
   - 뉴스 호재 전체 승률: 47.3% (동전 던지기보다 낮음!)
   - 수주 뉴스 승률: 43.7% (역신호! 반대로 하면 56.3%)
   - 배당 뉴스 승률: 37.6% (강한 역신호! 반대로 하면 62.4%)
   - **"뉴스 보고 매수하면 고점에 물린다"** - 이미 가격에 반영됨
   - 호재 뉴스가 있어도 추격매수 금지, 보수적 판단 권장

## 점수 구간
- A(80-100): 정량+정성 모두 우수 → 강력 매수
- B(65-79): 정량 좋고 정성 무난 → 매수 추천
- C(50-64): 중립
- D(40-49): 정량 부족 또는 악재 → 주의
- F(<40): 정량+정성 모두 부정적 → 회피

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판단 이유 (2-3문장)"}}"""
    return prompt.strip()
