# services/scout-job/scout_pipeline.py
# Version: v1.0
# Scout Job Pipeline Tasks - 종목 분석 파이프라인 함수
#
# scout.py에서 분리된 파이프라인 태스크 함수들

import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import shared.database as database

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """UTC 현재 시간"""
    return datetime.now(timezone.utc)


def is_hybrid_scoring_enabled() -> bool:
    """Scout v1.0 하이브리드 스코어링 활성화 여부 확인 (SCOUT_V5_ENABLED 환경변수 - 하위호환)"""
    return os.getenv("SCOUT_V5_ENABLED", "false").lower() == "true"


def process_quant_scoring_task(stock_info, quant_scorer, db_conn, kospi_prices_df=None):
    """
    [v1.0] Step 1: 정량 점수 계산 (LLM 호출 없음, 비용 0원)
    
    세 설계의 핵심 아이디어 구현:
    - Claude: 정량 점수를 LLM과 독립적으로 계산
    - Gemini: 비용 0원으로 1차 필터링
    - GPT: 조건부 승률 기반 점수 산출
    
    Args:
        stock_info: {'code': str, 'info': dict, 'snapshot': dict}
        quant_scorer: QuantScorer 인스턴스
        db_conn: DB 연결 (일봉 데이터 조회용)
        kospi_prices_df: KOSPI 일봉 데이터
    
    Returns:
        QuantScoreResult 객체
    """
    code = stock_info['code']
    info = stock_info['info']
    snapshot = stock_info.get('snapshot', {}) or {}
    
    try:
        # 일봉 데이터 조회
        daily_prices_df = database.get_daily_prices(db_conn, code, limit=150)
        
        # [v1.0] 데이터 부족 시 is_valid=False 설정 (묻어가기 방지)
        if daily_prices_df.empty or len(daily_prices_df) < 30:
            data_len = len(daily_prices_df) if not daily_prices_df.empty else 0
            logger.debug(f"   ⚠️ [Quant] {info['name']}({code}) 일봉 데이터 부족 ({data_len}일) → is_valid=False")
            from shared.hybrid_scoring import QuantScoreResult
            return QuantScoreResult(
                stock_code=code,
                stock_name=info['name'],
                total_score=0.0,
                momentum_score=0.0,
                quality_score=0.0,
                value_score=0.0,
                technical_score=0.0,
                news_stat_score=0.0,
                supply_demand_score=0.0,
                matched_conditions=[],
                condition_win_rate=None,
                condition_sample_count=0,
                condition_confidence='LOW',
                is_valid=False,
                invalid_reason=f'데이터 부족 ({data_len}일)',
                details={'note': f'데이터 부족 ({data_len}일)'},
            )
        
        # 정량 점수 계산
        result = quant_scorer.calculate_total_quant_score(
            stock_code=code,
            stock_name=info['name'],
            daily_prices_df=daily_prices_df,
            kospi_prices_df=kospi_prices_df,
            pbr=snapshot.get('pbr'),
            per=snapshot.get('per'),
            current_sentiment_score=info.get('sentiment_score', 50),
            foreign_net_buy=snapshot.get('foreign_net_buy'),
        )
        
        # [v1.0] 역신호 카테고리 체크
        REVERSE_SIGNAL_CATEGORIES = {'수주', '배당', '자사주', '주주환원', '배당락'}
        news_category = info.get('news_category') or snapshot.get('news_category')
        
        if news_category and news_category in REVERSE_SIGNAL_CATEGORIES:
            sentiment_score = info.get('sentiment_score', 50)
            if sentiment_score >= 70:
                logger.warning(f"   ⚠️ [v1.0] {info['name']}({code}) 역신호 카테고리({news_category}) 감지 - "
                              f"통계상 승률 50% 미만, 점수 패널티 적용")
                if result.details is None:
                    result.details = {}
                result.details['reverse_signal_category'] = news_category
                result.details['reverse_signal_warning'] = True
        
        logger.debug(f"   ✅ [Quant] {info['name']}({code}) - {result.total_score:.1f}점")
        return result
        
    except Exception as e:
        logger.error(f"   ❌ [Quant] {code} 정량 점수 계산 오류: {e}")
        from shared.hybrid_scoring import QuantScoreResult
        return QuantScoreResult(
            stock_code=code,
            stock_name=info['name'],
            total_score=0.0,
            momentum_score=0.0,
            quality_score=0.0,
            value_score=0.0,
            technical_score=0.0,
            news_stat_score=0.0,
            supply_demand_score=0.0,
            matched_conditions=[],
            condition_win_rate=None,
            condition_sample_count=0,
            condition_confidence='LOW',
            is_valid=False,
            invalid_reason=f'계산 오류: {str(e)[:30]}',
            details={'error': str(e)},
        )


def process_phase1_hunter_v5_task(stock_info, brain, quant_result, snapshot_cache=None, news_cache=None):
    """
    [v1.0] Phase 1 Hunter - 정량 컨텍스트 포함 LLM 분석
    [v1.0] 경쟁사 수혜 점수 반영 추가
    """
    from shared.hybrid_scoring import format_quant_score_for_prompt
    
    code = stock_info['code']
    info = stock_info['info']
    
    # 정량 컨텍스트 생성
    quant_context = format_quant_score_for_prompt(quant_result)
    
    # [v1.0] 경쟁사 수혜 점수 조회
    competitor_benefit = database.get_competitor_benefit_score(code)
    competitor_bonus = competitor_benefit.get('score', 0)
    competitor_reason = competitor_benefit.get('reason', '')
    
    snapshot = snapshot_cache.get(code) if snapshot_cache else None
    if not snapshot:
        return {
            'code': code,
            'name': info['name'],
            'info': info,
            'snapshot': None,
            'quant_result': quant_result,
            'hunter_score': 0,
            'hunter_reason': '스냅샷 조회 실패',
            'passed': False,
            'competitor_bonus': competitor_bonus,
        }
    
    news_from_chroma = news_cache.get(code, "최근 관련 뉴스 없음") if news_cache else "뉴스 캐시 없음"
    
    # [v1.0] 경쟁사 수혜 정보를 뉴스에 추가
    if competitor_bonus > 0:
        news_from_chroma += f"\n\n⚡ [경쟁사 수혜 기회] {competitor_reason} (+{competitor_bonus}점)"
    
    decision_info = {
        'code': code,
        'name': info['name'],
        'technical_reason': 'N/A',
        'news_reason': news_from_chroma if news_from_chroma not in ["뉴스 DB 미연결", "뉴스 검색 오류"] else ', '.join(info.get('reasons', [])),
        'per': snapshot.get('per'),
        'pbr': snapshot.get('pbr'),
        'market_cap': snapshot.get('market_cap'),
    }
    
    # [v1.0] 정량 컨텍스트 포함 Hunter 호출
    hunter_result = brain.get_jennies_analysis_score_v5(decision_info, quant_context)
    hunter_score = hunter_result.get('score', 0)
    
    # [v1.0] 경쟁사 수혜 가산점 적용 (최대 +10점)
    if competitor_bonus > 0:
        hunter_score = min(100, hunter_score + competitor_bonus)
        logger.info(f"   🎯 [경쟁사 수혜] {info['name']}({code}) +{competitor_bonus}점 가산 ({competitor_reason})")
    
    passed = hunter_score >= 60  # [v1.1] 75→60 완화 (v4와 동일)
    if hunter_score == 0: passed = False
    
    if passed:
        logger.info(f"   ✅ [v5 Hunter 통과] {info['name']}({code}) - Quant:{quant_result.total_score:.0f} → Hunter:{hunter_score}점")
    else:
        logger.debug(f"   ❌ [v5 Hunter 탈락] {info['name']}({code}) - Quant:{quant_result.total_score:.0f} → Hunter:{hunter_score}점")
    
    return {
        'code': code,
        'name': info['name'],
        'info': info,
        'snapshot': snapshot,
        'decision_info': decision_info,
        'quant_result': quant_result,
        'hunter_score': hunter_score,
        'hunter_reason': hunter_result.get('reason', ''),
        'passed': passed,
        'competitor_bonus': competitor_bonus,
        'competitor_reason': competitor_reason,
    }


def process_phase23_judge_v5_task(phase1_result, brain, archivist=None, market_regime="UNKNOWN"):
    """
    [v1.0] Phase 2-3: Debate + Judge (정량 컨텍스트 포함)
    
    정량 분석 결과를 Judge 프롬프트에 포함하여
    하이브리드 점수를 산출합니다.
    """
    from shared.hybrid_scoring import format_quant_score_for_prompt
    
    code = phase1_result['code']
    info = phase1_result['info']
    decision_info = phase1_result['decision_info']
    quant_result = phase1_result['quant_result']
    hunter_score = phase1_result['hunter_score']
    
    logger.info(f"   🔄 [v5 Phase 2-3] {info['name']}({code}) Debate-Judge 시작...")
    
    # 정량 컨텍스트 생성
    quant_context = format_quant_score_for_prompt(quant_result)
    
    # Phase 2: Debate (Bull vs Bear) - Dynamic Roles based on Hunter Score
    debate_log = brain.run_debate_session(decision_info, hunter_score=hunter_score)
    
    # Phase 3: Judge (정량 컨텍스트 포함)
    judge_result = brain.run_judge_scoring_v5(decision_info, debate_log, quant_context)
    score = judge_result.get('score', 0)
    grade = judge_result.get('grade', 'D')
    reason = judge_result.get('reason', '분석 실패')
    
    # [v1.0] 하이브리드 점수 계산 (정량 60% + 정성 40%)
    quant_score = quant_result.total_score
    llm_score = score
    
    score_diff = abs(quant_score - llm_score)
    if score_diff >= 30:
        if quant_score < llm_score:
            hybrid_score = quant_score * 0.75 + llm_score * 0.25
            logger.warning(f"   ⚠️ [Safety Lock] {info['name']} - 정량({quant_score:.0f}) << 정성({llm_score}) → 보수적 판단")
        else:
            hybrid_score = quant_score * 0.45 + llm_score * 0.55
            logger.warning(f"   ⚠️ [Safety Lock] {info['name']} - 정성({llm_score}) << 정량({quant_score:.0f}) → 보수적 판단")
    else:
        hybrid_score = quant_score * 0.60 + llm_score * 0.40
    
    is_tradable = hybrid_score >= 75
    approved = hybrid_score >= 50
    
    if hybrid_score >= 80:
        final_grade = 'S'
    elif hybrid_score >= 70:
        final_grade = 'A'
    elif hybrid_score >= 60:
        final_grade = 'B'
    elif hybrid_score >= 50:
        final_grade = 'C'
    else:
        final_grade = 'D'
    
    if approved:
        logger.info(f"   ✅ [v5 Judge 승인] {info['name']}({code}) - Hybrid:{hybrid_score:.1f}점 ({final_grade})")
    else:
        logger.info(f"   ❌ [v5 Judge 거절] {info['name']}({code}) - Hybrid:{hybrid_score:.1f}점 ({final_grade})")
    
    metadata = {
        'llm_grade': final_grade,
        'llm_updated_at': _utcnow().isoformat(),
        'source': 'hybrid_scorer_v5',
        'quant_score': quant_score,
        'llm_raw_score': llm_score,
        'hybrid_score': hybrid_score,
        'hunter_score': hunter_score,
        'condition_win_rate': quant_result.condition_win_rate,
    }
    
    # [v1.0] 스냅샷에서 재무 데이터 추출
    snapshot = phase1_result.get('snapshot') or {}
    
    # [Priority 1] Log to Decision Ledger (Archivist)
    if archivist:
        try:
            # Determine Final Decision
            final_decision = "HOLD"
            if approved:
                final_decision = "BUY"
            
            # Extract keywords from info['reasons'] (simple heuristic)
            reasons = info.get('reasons', [])
            keywords = []
            for r in reasons:
                keywords.extend([w for w in r.split() if len(w) > 1][:3])

            ledger_data = {
                'stock_code': code,
                'stock_name': info['name'],
                'hunter_score': hunter_score,
                'market_regime': market_regime,
                'dominant_keywords': keywords,
                'debate_log': debate_log,
                'counter_position_logic': debate_log[:500] if debate_log else None, # Placeholder for explicit extraction
                'thinking_called': 1 if judge_result.get('grade') != 'D' else 0, # Rough proxy
                'thinking_reason': "Judge_v5",
                'cost_estimate': 0.0, # Placeholder
                'gate_result': 'PASS' if score > 0 else 'REJECT',
                'final_decision': final_decision,
                'final_reason': reason
            }
            archivist.log_decision_ledger(ledger_data)
        except Exception as e:
            logger.error(f"   ⚠️ [Archivist] Failed to log decision: {e}")

    return {
        'code': code,
        'name': info['name'],
        'is_tradable': is_tradable,
        'llm_score': hybrid_score,
        'llm_reason': reason,
        'approved': approved,
        'llm_metadata': metadata,
        # 재무 데이터 추가
        'per': snapshot.get('per'),
        'pbr': snapshot.get('pbr'),
        'roe': snapshot.get('roe'),
        'market_cap': snapshot.get('market_cap'),
        'sales_growth': snapshot.get('sales_growth'),
        'eps_growth': snapshot.get('eps_growth'),
    }


def process_phase1_hunter_task(stock_info, brain, snapshot_cache=None, news_cache=None):
    """
    [v4.2] Phase 1 Hunter만 실행하는 태스크 (병렬 처리용)
    
    변경사항:
    - KIS API 스냅샷: 사전 캐시에서 조회 (API 호출 X)
    - ChromaDB 뉴스: 사전 캐시에서 조회 (HTTP 요청 X)
    - LLM 호출만 수행 → Rate Limit 대응 용이
    """
    code = stock_info['code']
    info = stock_info['info']
    
    snapshot = snapshot_cache.get(code) if snapshot_cache else None
    if not snapshot:
        logger.debug(f"   ⚠️ [Phase 1] {info['name']}({code}) Snapshot 캐시 미스")
        return {
            'code': code,
            'name': info['name'],
            'info': info,
            'snapshot': None,
            'hunter_score': 0,
            'hunter_reason': '스냅샷 조회 실패',
            'passed': False,
        }

    factor_info = ""
    momentum_value = None
    for reason in info.get('reasons', []):
        if '모멘텀' in reason:
            factor_info = reason
            try:
                match = re.search(r'([\d.-]+)%', reason)
                if match:
                    momentum_value = float(match.group(1))
            except Exception:
                pass
            break
    
    news_from_chroma = news_cache.get(code, "최근 관련 뉴스 없음") if news_cache else "뉴스 캐시 없음"
    
    all_reasons = info.get('reasons', []).copy()
    if news_from_chroma and news_from_chroma not in ["뉴스 DB 미연결", "최근 관련 뉴스 없음", "뉴스 검색 오류", "뉴스 조회 실패", "뉴스 캐시 없음"]:
        all_reasons.append(news_from_chroma)
    
    decision_info = {
        'code': code,
        'name': info['name'],
        'technical_reason': 'N/A (전략 변경)',
        'news_reason': news_from_chroma if news_from_chroma not in ["뉴스 DB 미연결", "뉴스 검색 오류"] else ', '.join(info['reasons']),
        'per': snapshot.get('per'),
        'pbr': snapshot.get('pbr'),
        'market_cap': snapshot.get('market_cap'),
        'factor_info': factor_info,
        'momentum_score': momentum_value
    }

    hunter_result = brain.get_jennies_analysis_score(decision_info)
    hunter_score = hunter_result.get('score', 0)
    
    passed = hunter_score >= 60
    if passed:
        logger.info(f"   ✅ [Phase 1 통과] {info['name']}({code}) - Hunter: {hunter_score}점")
    else:
        logger.debug(f"   ❌ [Phase 1 탈락] {info['name']}({code}) - Hunter: {hunter_score}점")
    
    return {
        'code': code,
        'name': info['name'],
        'info': info,
        'snapshot': snapshot,
        'decision_info': decision_info,
        'hunter_score': hunter_score,
        'hunter_reason': hunter_result.get('reason', ''),
        'passed': passed,
    }


def process_phase23_debate_judge_task(phase1_result, brain):
    """
    [v3.8] Phase 2-3 (Debate + Judge) 실행하는 태스크 (Phase 1 통과 종목만)
    GPT-5-mini로 심층 분석
    """
    code = phase1_result['code']
    info = phase1_result['info']
    decision_info = phase1_result['decision_info']
    hunter_score = phase1_result['hunter_score']
    
    logger.info(f"   🔄 [Phase 2-3] {info['name']}({code}) Debate-Judge 시작...")
    
    debate_log = brain.run_debate_session(decision_info, hunter_score=hunter_score)
    
    judge_result = brain.run_judge_scoring(decision_info, debate_log)
    score = judge_result.get('score', 0)
    grade = judge_result.get('grade', 'D')
    reason = judge_result.get('reason', '분석 실패')
    
    is_tradable = score >= 75
    approved = score >= 50
    
    if approved:
        logger.info(f"   ✅ [Judge 승인] {info['name']}({code}) - 최종: {score}점 ({grade})")
    else:
        logger.info(f"   ❌ [Judge 거절] {info['name']}({code}) - 최종: {score}점 ({grade})")
    
    metadata = {
        'llm_grade': grade,
        'llm_updated_at': _utcnow().isoformat(),
        'source': 'llm_judge',
        'hunter_score': hunter_score,
    }
    
    return {
        'code': code,
        'name': info['name'],
        'is_tradable': is_tradable,
        'llm_score': score,
        'llm_reason': reason,
        'approved': approved,
        'llm_metadata': metadata,
    }


def process_llm_decision_task(stock_info, kis_api, brain):
    """
    [Deprecated in v3.8] 기존 단일 패스 처리 (호환성 유지용)
    """
    code = stock_info['code']
    info = stock_info['info']
    decision_hash = stock_info['decision_hash']
    
    if hasattr(kis_api, 'API_CALL_DELAY'):
        time.sleep(kis_api.API_CALL_DELAY)
    
    snapshot = kis_api.get_stock_snapshot(code)
    if not snapshot:
        logger.warning(f"   ⚠️ [LLM 분석] {info['name']}({code}) Snapshot 조회 실패")
        return {
            'code': code,
            'name': info['name'],
            'is_tradable': False,
            'llm_score': 0,
            'llm_reason': '스냅샷 조회 실패',
            'approved': False,
            'llm_metadata': {
                'llm_grade': 'D',
                'decision_hash': decision_hash,
                'llm_updated_at': _utcnow().isoformat(),
                'source': 'llm',
            }
        }

    factor_info = ""
    momentum_value = None
    for reason in info.get('reasons', []):
        if '모멘텀 팩터' in reason:
            factor_info = reason
            try:
                match = re.search(r'상대 모멘텀: ([\d.-]+)%', reason)
                if match:
                    momentum_value = float(match.group(1))
            except Exception:
                pass
            break
    
    decision_info = {
        'code': code,
        'name': info['name'],
        'technical_reason': 'N/A (전략 변경)',
        'news_reason': ', '.join(info['reasons']),
        'per': snapshot.get('per'),
        'pbr': snapshot.get('pbr'),
        'market_cap': snapshot.get('market_cap'),
        'factor_info': factor_info,
        'momentum_score': momentum_value
    }

    hunter_result = brain.get_jennies_analysis_score(decision_info)
    hunter_score = hunter_result.get('score', 0)
    
    if hunter_score < 40:
        logger.info(f"   ❌ [Phase 1 탈락] {info['name']}({code}) - Hunter점수: {hunter_score}점 (미달)")
        return {
            'code': code,
            'name': info['name'],
            'is_tradable': False,
            'llm_score': hunter_score,
            'llm_reason': hunter_result.get('reason', 'Phase 1 필터링 탈락'),
            'approved': False,
            'llm_metadata': {
                'llm_grade': 'D',
                'decision_hash': decision_hash,
                'llm_updated_at': _utcnow().isoformat(),
                'source': 'llm_hunter_reject',
            }
        }
    
    logger.info(f"   ✅ [Phase 1 통과] {info['name']}({code}) - Hunter점수: {hunter_score}점 -> Debate 진출")

    debate_log = brain.run_debate_session(decision_info, hunter_score=hunter_score)
    
    judge_result = brain.run_judge_scoring(decision_info, debate_log)
    score = judge_result.get('score', 0)
    grade = judge_result.get('grade', 'D')
    reason = judge_result.get('reason', '분석 실패')
    
    is_tradable = score >= 75
    approved = score >= 50
    
    metadata = {
        'llm_grade': grade,
        'decision_hash': decision_hash,
        'llm_updated_at': _utcnow().isoformat(),
        'source': 'llm_judge',
        'debate_summary': debate_log[:200] + "..." if len(debate_log) > 200 else debate_log
    }
    
    if approved:
        logger.info(f"   🎉 [Judge 승인] {info['name']}({code}) - 최종: {score}점 ({grade})")
    else:
        logger.info(f"   ❌ [Judge 거절] {info['name']}({code}) - 최종: {score}점 ({grade})")
    
    return {
        'code': code,
        'name': info['name'],
        'is_tradable': is_tradable,
        'llm_score': score,
        'llm_reason': reason,
        'approved': approved,
        'llm_metadata': metadata,
    }


def fetch_kis_data_task(stock, kis_api):
    """KIS API로부터 종목 데이터 조회"""
    try:
        stock_code = stock['code']
        
        if hasattr(kis_api, 'API_CALL_DELAY'):
            time.sleep(kis_api.API_CALL_DELAY)
        
        price_data = kis_api.get_stock_daily_prices(stock_code, num_days_to_fetch=30)
        
        daily_prices = []
        if price_data is not None:
            if hasattr(price_data, 'empty') and not price_data.empty:
                for _, dp in price_data.iterrows():
                    close_price = dp.get('close_price') if 'close_price' in dp.index else dp.get('price')
                    high_price = dp.get('high_price') if 'high_price' in dp.index else dp.get('high')
                    low_price = dp.get('low_price') if 'low_price' in dp.index else dp.get('low')
                    date_val = dp.get('price_date') if 'price_date' in dp.index else dp.get('date')
                    
                    if close_price is not None:
                        daily_prices.append({
                            'p_date': date_val, 'p_code': stock_code,
                            'p_price': close_price, 'p_high': high_price, 'p_low': low_price
                        })
            elif isinstance(price_data, list) and len(price_data) > 0:
                for dp in price_data:
                    if isinstance(dp, dict):
                        close_price = dp.get('close_price') or dp.get('price')
                        high_price = dp.get('high_price') or dp.get('high')
                        low_price = dp.get('low_price') or dp.get('low')
                        date_val = dp.get('price_date') or dp.get('date')
                        
                        if close_price is not None:
                            daily_prices.append({
                                'p_date': date_val, 'p_code': stock_code,
                                'p_price': close_price, 'p_high': high_price, 'p_low': low_price
                            })
        
        fundamentals = None
        if stock.get("is_tradable", False):
            snapshot = kis_api.get_stock_snapshot(stock_code)
            if hasattr(kis_api, 'API_CALL_DELAY'):
                time.sleep(kis_api.API_CALL_DELAY)
            if snapshot:
                fundamentals = {
                    'code': stock_code,
                    'per': snapshot.get('per'),
                    'pbr': snapshot.get('pbr'),
                    'market_cap': snapshot.get('market_cap')
                }
        
        return daily_prices, fundamentals
    except Exception as e:
        logger.error(f"   (DW) ❌ {stock.get('name', 'N/A')} 처리 중 오류 발생: {e}")
        return [], None
