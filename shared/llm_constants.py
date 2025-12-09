"""
shared/llm_constants.py

LLM 관련 공통 상수와 JSON 스키마를 한곳에 모아 관리합니다.
기존 llm.py에서 분리하여 이후 교체/정리 시 영향을 최소화합니다.
"""

LLM_MODEL_NAME = "gemini-2.5-flash"  # 로컬/클라우드 공통 프리미엄 모델

# LLM이 반환할 JSON의 구조를 정의합니다.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["APPROVE", "REJECT", "SELL", "HOLD"]},
        "reason": {"type": "string"},
        "quantity": {
            "type": "integer",
            "description": "매수를 승인(APPROVE)할 경우, 매수할 주식의 수량. 그 외 결정에서는 0을 반환해야 합니다.",
        },
    },
    "required": ["decision", "reason", "quantity"],
}

# Top-N 랭킹 결재용 JSON 스키마
RANKING_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "best_stock_code": {
            "type": "string",
            "description": "후보 중에서 선택한 '단 하나의' 최고 종목 코드. 모든 후보가 부적절하면 'REJECT_ALL'.",
        },
        "reason": {
            "type": "string",
            "description": "선정 이유 (비교/뉴스 분석 포함)",
        },
        "quantity": {
            "type": "integer",
            "description": "LLM이 제안하는 최종 매수 수량. (REJECT_ALL이면 0)",
        },
    },
    "required": ["best_stock_code", "reason", "quantity"],
}

# 종목 심층 분석 및 점수 산출용 JSON 스키마
ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "매수 적합도 점수 (0~100점). 80점 이상 적극 매수.",
        },
        "grade": {
            "type": "string",
            "enum": ["S", "A", "B", "C", "D"],
            "description": "종합 등급 (S:90+, A:80+, B:70+, C:60+, D:60미만)",
        },
        "reason": {
            "type": "string",
            "description": "점수 산정 근거 (RAG 뉴스, 펀더멘털, 기술적 지표 종합)",
        },
    },
    "required": ["score", "grade", "reason"],
}

# 실시간 뉴스 감성 분석용 스키마
SENTIMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "뉴스 감성 점수 (0~100). 80이상: 강력호재, 20이하: 강력악재, 40~60: 중립.",
        },
        "reason": {
            "type": "string",
            "description": "점수 부여 사유 (한 문장 요약)",
        },
    },
    "required": ["score", "reason"],
}

GENERATION_CONFIG = {
    "temperature": 0.2,  # 낮을수록 일관성/사실 기반
    "response_mime_type": "application/json",  # 응답을 JSON으로 강제
    "response_schema": RESPONSE_SCHEMA,  # 기본 스키마
}

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]
