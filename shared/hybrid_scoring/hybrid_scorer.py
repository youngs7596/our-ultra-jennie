#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout v5.0.2 HybridScorer - 정량+정성 하이브리드 점수 결합

세 설계의 핵심 아이디어 통합:
- Claude: 정량/정성 분리 후 기계적 결합, 최소 품질 기준
- Gemini: 기본 비율 60:40, 차이 30점 이상시 보수적 가중치
- GPT: 시장 국면별 동적 조정, 낮은 쪽 점수 우선

최종 점수 계산:
final_score = quant_score × quant_weight + llm_score × llm_weight

안전장치:
1. 점수 차이 30점 이상 → 낮은 쪽으로 가중치 이동
2. 최소 품질 기준 40점 미만 → 자동 탈락
3. 신뢰도 낮은 통계 → 정량 비중 감소

[v5.0.2] GPT 피드백 반영:
- 뉴스 통계 표본수 신뢰도 보정 추가
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .quant_scorer import QuantScoreResult, format_quant_score_for_prompt
from .schema import get_confidence_weight

logger = logging.getLogger(__name__)


@dataclass
class HybridScoreResult:
    """하이브리드 점수 결과 데이터 클래스"""
    stock_code: str
    stock_name: str
    
    # 점수 (100점 만점)
    quant_score: float          # 정량 점수
    llm_score: float            # LLM 정성 점수
    hybrid_score: float         # 최종 하이브리드 점수
    
    # 가중치 정보
    quant_weight: float         # 적용된 정량 가중치
    llm_weight: float           # 적용된 LLM 가중치
    
    # 안전장치 적용 여부
    safety_lock_applied: bool = False
    safety_lock_reason: str = ""
    
    # 최종 선정 여부
    is_selected: bool = False
    final_rank: int = 0
    
    # 등급
    grade: str = "C"
    
    # LLM 분석 결과
    llm_reason: str = ""
    
    # 조건부 승률 정보 (QuantScoreResult에서 복사)
    condition_win_rate: Optional[float] = None
    condition_sample_count: Optional[int] = None
    condition_confidence: str = "LOW"
    
    # [v5.0.2] 뉴스 통계 정보 (GPT 피드백 반영)
    news_stat_win_rate: Optional[float] = None
    news_stat_sample_count: Optional[int] = None
    news_stat_confidence: str = "LOW"
    
    # 상세 정보
    details: Dict = field(default_factory=dict)
    
    def to_watchlist_entry(self) -> Dict:
        """Watchlist 저장용 딕셔너리 변환"""
        return {
            'code': self.stock_code,
            'name': self.stock_name,
            'is_tradable': self.grade in ['A', 'B'],  # A/B 등급만 매수 가능
            'llm_score': self.hybrid_score,
            'llm_reason': self.llm_reason,
            'llm_metadata': {
                'llm_grade': self.grade,
                'quant_score': self.quant_score,
                'llm_raw_score': self.llm_score,
                'hybrid_score': self.hybrid_score,
                'safety_lock_applied': self.safety_lock_applied,
                'condition_win_rate': self.condition_win_rate,
                'source': 'hybrid_scorer_v5',
            }
        }


class HybridScorer:
    """
    정량+정성 하이브리드 점수 계산기
    
    핵심 로직:
    1. 기본 비율: 정량 60% + 정성 40% (Gemini 설계)
    2. 안전장치 (Safety Lock): 점수 차이 30점 이상시 낮은 쪽 우선 (GPT 설계)
    3. 최소 품질 기준: 40점 미만 자동 탈락 (Claude 설계)
    4. 시장 국면별 동적 조정 (GPT 설계)
    """
    
    # 기본 가중치 설정 (Gemini 설계 기반)
    DEFAULT_QUANT_WEIGHT = 0.60
    DEFAULT_LLM_WEIGHT = 0.40
    
    # 안전장치 임계값 (Gemini + GPT 설계)
    SAFETY_LOCK_THRESHOLD = 30  # 점수 차이 30점 이상시 발동
    MIN_QUALITY_SCORE = 40      # 최소 품질 기준 (Claude 설계)
    
    # 시장 국면별 가중치 조정 (GPT 설계)
    REGIME_WEIGHTS = {
        'STRONG_BULL': {'quant': 0.55, 'llm': 0.45},  # 상승장: LLM 비중 약간 증가
        'BULL': {'quant': 0.60, 'llm': 0.40},
        'SIDEWAYS': {'quant': 0.60, 'llm': 0.40},
        'BEAR': {'quant': 0.65, 'llm': 0.35},  # 하락장: 정량 비중 증가
    }
    
    # 최종 선정 수 (쿼터제)
    MAX_WATCHLIST_SIZE = 15
    
    def __init__(self, market_regime: str = 'SIDEWAYS'):
        """
        초기화
        
        Args:
            market_regime: 현재 시장 국면
        """
        self.market_regime = market_regime
        
        # 시장 국면에 따른 가중치 설정
        regime_config = self.REGIME_WEIGHTS.get(market_regime, self.REGIME_WEIGHTS['SIDEWAYS'])
        self.quant_weight = regime_config['quant']
        self.llm_weight = regime_config['llm']
        
        logger.info(f"✅ HybridScorer 초기화 (국면: {market_regime}, "
                   f"가중치: 정량 {self.quant_weight*100:.0f}% / 정성 {self.llm_weight*100:.0f}%)")
    
    def _apply_safety_lock(self, 
                           quant_score: float, 
                           llm_score: float) -> Tuple[float, float, bool, str]:
        """
        안전장치 (Safety Lock) 적용
        
        정량 점수와 LLM 점수의 차이가 30점 이상이면,
        낮은 쪽 점수로 가중치를 이동시켜 보수적으로 판단.
        
        Args:
            quant_score: 정량 점수 (0~100)
            llm_score: LLM 점수 (0~100)
        
        Returns:
            (조정된 정량 가중치, 조정된 LLM 가중치, 안전장치 적용 여부, 사유)
        """
        score_diff = abs(quant_score - llm_score)
        
        if score_diff < self.SAFETY_LOCK_THRESHOLD:
            # 차이가 임계값 미만 → 기본 가중치 유지
            return self.quant_weight, self.llm_weight, False, ""
        
        # 차이가 임계값 이상 → 낮은 쪽으로 가중치 이동
        if quant_score < llm_score:
            # 정량 점수가 낮음 → 정량 비중 증가 (보수적)
            adjusted_quant_weight = min(0.80, self.quant_weight + 0.15)
            adjusted_llm_weight = 1.0 - adjusted_quant_weight
            reason = f"정량({quant_score:.0f}) < 정성({llm_score:.0f}), 정량 비중 상향"
        else:
            # LLM 점수가 낮음 → LLM 비중 증가 (보수적)
            adjusted_llm_weight = min(0.55, self.llm_weight + 0.15)
            adjusted_quant_weight = 1.0 - adjusted_llm_weight
            reason = f"정성({llm_score:.0f}) < 정량({quant_score:.0f}), 정성 비중 상향"
        
        logger.debug(f"   ⚠️ Safety Lock 발동: {reason}")
        return adjusted_quant_weight, adjusted_llm_weight, True, reason
    
    def _determine_grade(self, hybrid_score: float, llm_score: float) -> str:
        """
        최종 등급 결정
        
        등급 기준:
        - A: 80점 이상 (강력 매수)
        - B: 65점 이상 (매수 추천)
        - C: 50점 이상 (관심 종목)
        - D: 40점 이상 (보류)
        - F: 40점 미만 (탈락)
        """
        if hybrid_score >= 80:
            return 'A'
        elif hybrid_score >= 65:
            return 'B'
        elif hybrid_score >= 50:
            return 'C'
        elif hybrid_score >= 40:
            return 'D'
        else:
            return 'F'
    
    def calculate_hybrid_score(self,
                               quant_result: QuantScoreResult,
                               llm_score: float,
                               llm_reason: str = "") -> HybridScoreResult:
        """
        단일 종목의 하이브리드 점수 계산
        
        [v5.0.2] GPT 피드백 반영:
        - 뉴스 통계 표본수 신뢰도 보정 추가
        
        Args:
            quant_result: QuantScorer의 정량 점수 결과
            llm_score: LLM의 정성 점수 (0~100)
            llm_reason: LLM의 분석 근거
        
        Returns:
            HybridScoreResult 객체
        """
        quant_score = quant_result.total_score
        
        # 1. 안전장치 적용 여부 확인
        adj_quant_w, adj_llm_w, safety_applied, safety_reason = self._apply_safety_lock(
            quant_score, llm_score
        )
        
        # 2. 하이브리드 점수 계산
        hybrid_score = (quant_score * adj_quant_w) + (llm_score * adj_llm_w)
        
        # 3. 조건부 승률 신뢰도 기반 조정 (표본 부족시 정량 점수 보정)
        condition_confidence_applied = False
        if quant_result.condition_sample_count is not None:
            confidence_weight = get_confidence_weight(quant_result.condition_sample_count)
            if confidence_weight < 1.0:
                condition_confidence_applied = True
                # 신뢰도가 낮으면 정량 점수 기여분을 감소시키고 중립값으로 보정
                quant_contribution = quant_score * adj_quant_w
                neutral_contribution = 50 * adj_quant_w  # 50점이 중립
                adjusted_quant_contribution = (
                    quant_contribution * confidence_weight +
                    neutral_contribution * (1 - confidence_weight)
                )
                hybrid_score = adjusted_quant_contribution + (llm_score * adj_llm_w)
        
        # 4. [v5.0.2] 뉴스 통계 신뢰도 보정 (GPT 피드백 반영)
        # 뉴스 통계 표본수가 적으면 news_stat_score 기여분을 감소
        news_confidence_applied = False
        if quant_result.news_stat_sample_count is not None and quant_result.news_stat_sample_count < 30:
            news_confidence_weight = get_confidence_weight(quant_result.news_stat_sample_count)
            if news_confidence_weight < 1.0:
                news_confidence_applied = True
                # 뉴스 통계 점수의 비중 (전체 100점 중 15점)
                news_score_ratio = 0.15
                
                # 뉴스 통계 점수를 중립값(7.5점)으로 보정
                news_neutral = 7.5  # 15점 만점의 중립값
                adjusted_news_score = (
                    quant_result.news_stat_score * news_confidence_weight +
                    news_neutral * (1 - news_confidence_weight)
                )
                
                # 정량 점수 내에서 뉴스 기여분 조정
                news_score_diff = adjusted_news_score - quant_result.news_stat_score
                
                # 하이브리드 점수에 반영 (정량 가중치 적용)
                hybrid_score += news_score_diff * adj_quant_w
                
                logger.debug(f"   📰 {quant_result.stock_name} 뉴스 통계 신뢰도 보정: "
                           f"표본={quant_result.news_stat_sample_count}, 가중치={news_confidence_weight:.2f}")
        
        # 5. 등급 결정
        grade = self._determine_grade(hybrid_score, llm_score)
        
        # 6. 최소 품질 기준 체크
        is_below_minimum = hybrid_score < self.MIN_QUALITY_SCORE
        if is_below_minimum:
            grade = 'F'
        
        return HybridScoreResult(
            stock_code=quant_result.stock_code,
            stock_name=quant_result.stock_name,
            quant_score=quant_score,
            llm_score=llm_score,
            hybrid_score=round(hybrid_score, 2),
            quant_weight=adj_quant_w,
            llm_weight=adj_llm_w,
            safety_lock_applied=safety_applied,
            safety_lock_reason=safety_reason,
            is_selected=False,  # 최종 선정은 select_top_candidates에서 결정
            grade=grade,
            llm_reason=llm_reason,
            condition_win_rate=quant_result.condition_win_rate,
            condition_sample_count=quant_result.condition_sample_count,
            condition_confidence=quant_result.condition_confidence,
            news_stat_win_rate=quant_result.news_stat_win_rate,
            news_stat_sample_count=quant_result.news_stat_sample_count,
            news_stat_confidence=quant_result.news_stat_confidence,
            details={
                'quant_details': quant_result.details,
                'matched_conditions': quant_result.matched_conditions,
                'condition_confidence_applied': condition_confidence_applied,
                'news_confidence_applied': news_confidence_applied,
            }
        )
    
    def calculate_batch_hybrid_scores(self,
                                      quant_results: List[QuantScoreResult],
                                      llm_scores: Dict[str, Tuple[float, str]]) -> List[HybridScoreResult]:
        """
        배치로 하이브리드 점수 계산
        
        Args:
            quant_results: QuantScorer 결과 리스트
            llm_scores: {stock_code: (llm_score, llm_reason)} 딕셔너리
        
        Returns:
            HybridScoreResult 리스트
        """
        results = []
        
        for quant_result in quant_results:
            code = quant_result.stock_code
            
            if code in llm_scores:
                llm_score, llm_reason = llm_scores[code]
            else:
                # LLM 점수가 없으면 중립값 사용
                llm_score = 50.0
                llm_reason = "LLM 분석 없음"
            
            hybrid_result = self.calculate_hybrid_score(
                quant_result, llm_score, llm_reason
            )
            results.append(hybrid_result)
        
        return results
    
    def select_top_candidates(self,
                              results: List[HybridScoreResult],
                              max_count: int = None) -> List[HybridScoreResult]:
        """
        최종 후보 선정 (쿼터제)
        
        Claude 설계: 절대 점수가 아닌 상대 순위 기준으로 선정
        
        Args:
            results: HybridScoreResult 리스트
            max_count: 최대 선정 수 (기본값: 15)
        
        Returns:
            최종 선정된 종목 리스트 (순위 포함)
        """
        if not results:
            return []
        
        if max_count is None:
            max_count = self.MAX_WATCHLIST_SIZE
        
        # 1. F등급(최소 품질 미달) 제외
        qualified = [r for r in results if r.grade != 'F']
        
        if not qualified:
            logger.warning("   (HybridScorer) ⚠️ 최소 품질 기준을 통과한 종목이 없습니다.")
            return []
        
        # 2. 하이브리드 점수 기준 내림차순 정렬
        sorted_results = sorted(qualified, key=lambda x: x.hybrid_score, reverse=True)
        
        # 3. 상위 N개 선정
        selected = sorted_results[:max_count]
        
        # 4. 순위 및 선정 여부 업데이트
        for i, result in enumerate(selected):
            result.final_rank = i + 1
            result.is_selected = True
        
        # 통계 로깅
        grade_counts = {}
        for r in selected:
            grade_counts[r.grade] = grade_counts.get(r.grade, 0) + 1
        
        logger.info(f"   (HybridScorer) ✅ 최종 선정: {len(selected)}/{len(results)}개 "
                   f"(등급 분포: {grade_counts})")
        
        return selected
    
    def generate_llm_prompt_context(self,
                                    quant_result: QuantScoreResult) -> str:
        """
        LLM 프롬프트용 정량 분석 컨텍스트 생성
        
        GPT 설계: "통계는 중요한 판단 근거이니 반드시 반영하세요" 메타 지시 포함
        
        Args:
            quant_result: QuantScorer의 정량 점수 결과
        
        Returns:
            프롬프트에 삽입할 컨텍스트 문자열
        """
        return format_quant_score_for_prompt(quant_result)
    
    def generate_summary_report(self, 
                                selected_results: List[HybridScoreResult]) -> str:
        """
        최종 선정 결과 요약 리포트 생성
        
        Args:
            selected_results: 최종 선정된 HybridScoreResult 리스트
        
        Returns:
            마크다운 형식 리포트
        """
        if not selected_results:
            return "## Scout v5.0 분석 결과\n\n선정된 종목이 없습니다."
        
        report = f"""## 🎯 Scout v5.0 Hybrid Scoring 분석 결과

**분석 시각**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC  
**시장 국면**: {self.market_regime}  
**가중치**: 정량 {self.quant_weight*100:.0f}% / 정성 {self.llm_weight*100:.0f}%  
**선정 종목 수**: {len(selected_results)}개

---

### 📊 최종 선정 종목

| 순위 | 종목명 | 등급 | 하이브리드 | 정량 | 정성 | 승률 | 비고 |
|:---:|:------|:---:|:---:|:---:|:---:|:---:|:-----|
"""
        
        for r in selected_results:
            win_rate_str = f"{r.condition_win_rate*100:.0f}%" if r.condition_win_rate else "-"
            safety_note = "⚠️" if r.safety_lock_applied else ""
            
            report += f"| {r.final_rank} | {r.stock_name} | {r.grade} | "
            report += f"{r.hybrid_score:.1f} | {r.quant_score:.1f} | {r.llm_score:.1f} | "
            report += f"{win_rate_str} | {safety_note} |\n"
        
        # 등급별 통계
        grade_a = sum(1 for r in selected_results if r.grade == 'A')
        grade_b = sum(1 for r in selected_results if r.grade == 'B')
        grade_c = sum(1 for r in selected_results if r.grade == 'C')
        
        report += f"""
---

### 📈 등급 분포

- **A등급 (강력 매수)**: {grade_a}개
- **B등급 (매수 추천)**: {grade_b}개
- **C등급 (관심 종목)**: {grade_c}개

### 🔒 안전장치 발동 현황

"""
        
        safety_applied = [r for r in selected_results if r.safety_lock_applied]
        if safety_applied:
            for r in safety_applied:
                report += f"- **{r.stock_name}**: {r.safety_lock_reason}\n"
        else:
            report += "- 안전장치 발동 종목 없음\n"
        
        return report


# =============================================================================
# 스코어링 파이프라인 통합 함수
# =============================================================================

def run_hybrid_scoring_pipeline(
    candidates: List[Dict],
    quant_scorer: 'QuantScorer',
    llm_analyzer,  # JennieBrain 또는 유사 객체
    db_conn=None,
    market_regime: str = 'SIDEWAYS',
    filter_cutoff: float = 0.5,
    max_watchlist: int = 15,
) -> Tuple[List[HybridScoreResult], str]:
    """
    Scout v5.0 하이브리드 스코어링 전체 파이프라인 실행
    
    단계:
    1. 정량 점수 계산 (QuantScorer)
    2. 정량 기반 1차 필터링 (하위 50% 탈락)
    3. LLM 정성 분석 (통과 종목만)
    4. 하이브리드 점수 결합 (HybridScorer)
    5. 최종 선정 (상위 15개)
    
    Args:
        candidates: 후보 종목 리스트 [{code, name, daily_prices_df, ...}]
        quant_scorer: QuantScorer 인스턴스
        llm_analyzer: LLM 분석기 (JennieBrain)
        db_conn: DB 연결 (일별 점수 저장용)
        market_regime: 시장 국면
        filter_cutoff: 1차 필터링 탈락 비율 (기본 0.5 = 하위 50%)
        max_watchlist: 최종 선정 수 (기본 15)
    
    Returns:
        (최종 선정 결과 리스트, 요약 리포트)
    """
    logger.info("=" * 60)
    logger.info("   🚀 Scout v5.0 Hybrid Scoring Pipeline 시작")
    logger.info("=" * 60)
    
    # Step 1: 정량 점수 계산
    logger.info(f"\n   [Step 1/4] 정량 점수 계산 ({len(candidates)}개 종목)")
    quant_results = []
    
    for candidate in candidates:
        result = quant_scorer.calculate_total_quant_score(
            stock_code=candidate['code'],
            stock_name=candidate.get('name', candidate['code']),
            daily_prices_df=candidate.get('daily_prices_df'),
            kospi_prices_df=candidate.get('kospi_prices_df'),
            roe=candidate.get('roe'),
            sales_growth=candidate.get('sales_growth'),
            eps_growth=candidate.get('eps_growth'),
            pbr=candidate.get('pbr'),
            per=candidate.get('per'),
            current_sentiment_score=candidate.get('sentiment_score', 50),
            news_category=candidate.get('news_category'),
            foreign_net_buy=candidate.get('foreign_net_buy'),
            institution_net_buy=candidate.get('institution_net_buy'),
            foreign_holding_ratio=candidate.get('foreign_holding_ratio'),
        )
        quant_results.append(result)
    
    logger.info(f"   ✅ 정량 점수 계산 완료 (평균: {sum(r.total_score for r in quant_results)/len(quant_results):.1f}점)")
    
    # Step 2: 정량 기반 1차 필터링
    logger.info(f"\n   [Step 2/4] 정량 기반 1차 필터링 (하위 {filter_cutoff*100:.0f}% 탈락)")
    filtered_results = quant_scorer.filter_candidates(quant_results, filter_cutoff)
    
    if not filtered_results:
        logger.warning("   ⚠️ 1차 필터링 통과 종목 없음!")
        return [], "1차 필터링 통과 종목 없음"
    
    # Step 3: LLM 정성 분석 (통과 종목만)
    logger.info(f"\n   [Step 3/4] LLM 정성 분석 ({len(filtered_results)}개 종목)")
    llm_scores: Dict[str, Tuple[float, str]] = {}
    
    for quant_result in filtered_results:
        # 정량 분석 컨텍스트를 포함한 프롬프트 생성
        prompt_context = format_quant_score_for_prompt(quant_result)
        
        # LLM 분석 요청 (실제 구현에서는 llm_analyzer 호출)
        # 여기서는 인터페이스만 정의
        try:
            if llm_analyzer and hasattr(llm_analyzer, 'analyze_with_context'):
                llm_result = llm_analyzer.analyze_with_context(
                    stock_code=quant_result.stock_code,
                    stock_name=quant_result.stock_name,
                    quant_context=prompt_context,
                )
                llm_scores[quant_result.stock_code] = (
                    llm_result.get('score', 50),
                    llm_result.get('reason', '')
                )
            else:
                # LLM 분석기 없으면 기존 방식 사용 (호환성)
                llm_scores[quant_result.stock_code] = (50, "LLM 분석 스킵")
        except Exception as e:
            logger.warning(f"   ⚠️ {quant_result.stock_code} LLM 분석 실패: {e}")
            llm_scores[quant_result.stock_code] = (50, f"분석 실패: {e}")
    
    # Step 4: 하이브리드 점수 결합 및 최종 선정
    logger.info(f"\n   [Step 4/4] 하이브리드 점수 결합 및 최종 선정")
    hybrid_scorer = HybridScorer(market_regime=market_regime)
    
    hybrid_results = hybrid_scorer.calculate_batch_hybrid_scores(
        filtered_results, llm_scores
    )
    
    final_selected = hybrid_scorer.select_top_candidates(
        hybrid_results, max_watchlist
    )
    
    # 요약 리포트 생성
    report = hybrid_scorer.generate_summary_report(final_selected)
    
    logger.info("\n" + "=" * 60)
    logger.info(f"   🏁 Scout v5.0 Pipeline 완료: {len(final_selected)}개 종목 선정")
    logger.info("=" * 60)
    
    return final_selected, report

