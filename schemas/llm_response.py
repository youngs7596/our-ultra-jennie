from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, validator


GradeLiteral = Literal["S", "A", "B", "C", "D"]
DecisionLiteral = Literal["TRADABLE", "SKIP"]
StrategyLiteral = Literal["SNIPE_DIP", "MOMENTUM_BREAKOUT", "DO_NOT_TRADE"]
RiskLiteral = Literal["LOW", "MEDIUM", "HIGH"]


class MarketRegimeStrategy(BaseModel):
    decision: DecisionLiteral = Field(description="BEAR 장에서도 실제 매수를 허용할지 여부")
    strategy_type: StrategyLiteral = Field(description="추천 전략 유형")
    rationale: str = Field(description="전략 선택 근거")
    confidence_score: int = Field(ge=0, le=100, description="LLM 확신도 (0~100)")


class RiskAssessment(BaseModel):
    volatility_risk: RiskLiteral
    fundamental_risk: RiskLiteral


class BearMarketLLMResponse(BaseModel):
    symbol: str
    llm_grade: GradeLiteral
    market_regime_strategy: MarketRegimeStrategy
    risk_assessment: RiskAssessment
    suggested_entry_focus: Optional[str] = Field(
        default=None,
        description="LLM이 제안하는 진입 포커스 (예: RSI_DIV, VOLUME_FLUSH)",
    )

    def is_confident(self, min_grade: GradeLiteral = "B", min_confidence: int = 80) -> bool:
        """등급과 확신도가 모두 조건을 만족하는지 판별."""
        grade_order = ["S", "A", "B", "C", "D"]
        return (
            grade_order.index(self.llm_grade) <= grade_order.index(min_grade)
            and self.market_regime_strategy.confidence_score >= min_confidence
            and self.market_regime_strategy.strategy_type != "DO_NOT_TRADE"
            and self.market_regime_strategy.decision == "TRADABLE"
        )

    @validator("symbol")
    def validate_symbol(cls, value: str) -> str:
        if not value:
            raise ValueError("symbol은 비어 있을 수 없습니다.")
        return value

    def to_metadata(self) -> dict:
        """Watchlist 저장 시 활용할 메타데이터 dict."""
        return {
            "llm_grade": self.llm_grade,
            "bear_strategy": self.dict(),
            "suggested_entry_focus": self.suggested_entry_focus,
        }


