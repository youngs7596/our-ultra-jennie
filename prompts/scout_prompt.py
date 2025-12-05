from __future__ import annotations

from textwrap import dedent


def build_bear_market_prompt(stock_payload: dict, market_regime: str) -> str:
    """
    Scout 단계에서 하락장 특화 판단을 위한 프롬프트 생성.

    Args:
        stock_payload: 종목 메타데이터(dict)
        market_regime: 현재 시장 레짐 문자열 (예: "BEAR")
    """

    def fmt(value, default="N/A"):
        return value if value not in (None, "", []) else default

    name = fmt(stock_payload.get("name"))
    code = fmt(stock_payload.get("code"))
    per = fmt(stock_payload.get("per"))
    pbr = fmt(stock_payload.get("pbr"))
    market_cap = fmt(stock_payload.get("market_cap"))
    factor_info = fmt(stock_payload.get("factor_info"))
    technical_summary = fmt(stock_payload.get("technical_summary"))
    news_reason = fmt(stock_payload.get("news_reason"))
    momentum_score = stock_payload.get("momentum_score", "N/A")

    payload_block = f"""
    [종목 스냅샷]
    - 종목명: {name} ({code})
    - PER: {per}, PBR: {pbr}, 시가총액: {market_cap}
    - 모멘텀 요약: {momentum_score}
    - 기술적 메모: {technical_summary}
    - 팩터/모멘텀 근거: {factor_info}
    - 뉴스/재료 요약: {news_reason}
    """.strip()

    prompt = f"""
    # 역할
    당신은 매우 보수적인 슈퍼리치 자산관리자이자 퀀트 트레이더입니다.
    최우선 목표는 "자본 보존"이며, 그 다음이 "알파 창출"입니다.

    # 현재 시장 상황
    🚨 **중요 경고**: 현재 시장 국면은 **'{market_regime}'** 입니다.
    - 변동성이 매우 크며 시스템 리스크가 존재합니다.
    - 대부분의 종목이 하락 중입니다.
    - **현금이 왕** 입니다. 평범한 종목은 추천하지 마세요.

    # 과제
    아래 종목 정보를 보고, 하락장에서도 예외적으로 매수할 가치가 있는지 판단하세요.
    {payload_block}

    # 전략 옵션 (하나만 선택)
    1. "DO_NOT_TRADE"  : 대부분의 경우 기본값입니다. 추천 가치가 없으면 반드시 이 전략을 선택하세요.
    2. "SNIPE_DIP"     : 블루칩인데 시장 공포로 과도하게 하락한 상황. 과매도 구간에서 저점 매수 스나이핑.
    3. "MOMENTUM_BREAKOUT": 시장이 빠지는 와중에도 상대적 강세를 보이며 상승 중인 섹터 리더.

    # 출력 요구사항
    아래 JSON 스키마를 정확히 따르십시오.
    {{
      "symbol": "{code}",
      "llm_grade": "S|A|B|C|D",
      "market_regime_strategy": {{
        "decision": "TRADABLE" 또는 "SKIP",
        "strategy_type": "SNIPE_DIP" 또는 "MOMENTUM_BREAKOUT" 또는 "DO_NOT_TRADE",
        "rationale": "전략 선택 이유",
        "confidence_score": 0~100
      }},
      "risk_assessment": {{
        "volatility_risk": "LOW|MEDIUM|HIGH",
        "fundamental_risk": "LOW|MEDIUM|HIGH"
      }},
      "suggested_entry_focus": "예: RSI_DIVERGENCE / VOLUME_FLUSH / BREAKOUT_LEVEL"
    }}

    *confidence_score* 는 매우 엄격하게 책정하세요.
    """

    return dedent(prompt).strip()


