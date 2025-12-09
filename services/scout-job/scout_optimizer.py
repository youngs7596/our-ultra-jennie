# services/scout-job/scout_optimizer.py
# Version: v1.0
# Scout Job Auto-Parameter Optimizer - 자동 파라미터 최적화 함수
#
# scout.py에서 분리된 자동 최적화 파이프라인

import logging
from typing import Dict, Optional

import shared.database as database

logger = logging.getLogger(__name__)

# Backtester (optional)
try:
    from utilities.backtest import Backtester
    logger.info("✅ Backtester 모듈 임포트 성공")
except ImportError as e:
    logger.warning(f"⚠️ Backtester 모듈 임포트 실패 (백테스트 기능 비활성화): {e}")
    Backtester = None


def _get_param(params: Dict, key: str, default, cast_type=float):
    """파라미터 안전 조회"""
    try:
        if key not in params or params[key] is None:
            return default
        return cast_type(params[key])
    except (ValueError, TypeError):
        return default


def run_simple_backtest(db_conn, params):
    """
    [v2.2] 간단한 백테스트 실행
    """
    try:
        if Backtester is None:
            logger.error("   (Backtest) ❌ Backtester 모듈을 사용할 수 없습니다. 최적화를 건너뜁니다.")
            return None
        
        logger.info("   (Backtest) Backtester 기반 검증 실행 중...")
        
        backtester = Backtester(
            db_conn,
            max_buys_per_day=_get_param(params, 'MAX_BUYS_PER_DAY', 100, int),
            profit_target_full=_get_param(params, 'PROFIT_TARGET_FULL', 10.0, float),
            profit_target_partial=_get_param(params, 'PROFIT_TARGET_PARTIAL', 5.0, float),
            rsi_threshold_1=_get_param(params, 'RSI_THRESHOLD_1', 70.0, float),
            rsi_threshold_2=_get_param(params, 'RSI_THRESHOLD_2', 75.0, float),
            rsi_threshold_3=_get_param(params, 'RSI_THRESHOLD_3', 80.0, float),
            time_based_bull=_get_param(params, 'TIME_BASED_BULL', 30, int),
            time_based_sideways=_get_param(params, 'TIME_BASED_SIDEWAYS', 30, int),
            max_position_pct=_get_param(params, 'MAX_POSITION_PCT', 5, int),
            cash_keep_pct=_get_param(params, 'CASH_KEEP_PCT', 5, int),
            hybrid_mode=True,
        )
        
        metrics = backtester.run()
        if not metrics:
            logger.warning("   (Backtest) 백테스트 결과가 없습니다.")
            return None
        
        monthly_return = metrics.get('monthly_return_pct')
        total_return = metrics.get('total_return_pct')
        
        return {
            'mdd': float(metrics.get('mdd_pct', 0.0)),
            'return': float(monthly_return if monthly_return is not None else (total_return or 0.0))
        }
        
    except Exception as e:
        logger.error(f"   (Backtest) 백테스트 실행 오류: {e}", exc_info=True)
        return None


def generate_optimized_params(current_params):
    """
    [v2.2] 최적화 후보 파라미터 생성
    """
    new_params = {}
    if 'SELL_RSI_OVERBOUGHT_THRESHOLD' in current_params:
        current_value = float(current_params['SELL_RSI_OVERBOUGHT_THRESHOLD'])
        adjustment = current_value * 0.05
        new_value = min(80, current_value + adjustment)
        new_params['SELL_RSI_OVERBOUGHT_THRESHOLD'] = new_value
    
    if 'ATR_MULTIPLIER' in current_params:
        current_value = float(current_params['ATR_MULTIPLIER'])
        adjustment = 0.1
        new_value = min(3.0, current_value + adjustment)
        new_params['ATR_MULTIPLIER'] = new_value
    
    return new_params


def verify_params_with_llm(brain, current_params, current_performance, 
                           new_params, new_performance, market_summary):
    """
    [v2.2] LLM을 통한 파라미터 검증
    """
    try:
        logger.info("   (LLM) [v2.2] JennieBrain을 통한 AI 검증 시작...")
        result = brain.verify_parameter_change(
            current_params=current_params,
            new_params=new_params,
            current_performance=current_performance,
            new_performance=new_performance,
            market_summary=market_summary
        )
        if result:
            logger.info(f"   (LLM) ✅ AI 검증 완료: {result.get('is_approved')}")
        return result
    except Exception as e:
        logger.error(f"   (LLM) ❌ AI 검증 오류: {e}", exc_info=True)
        return None


def run_auto_parameter_optimization(db_conn, brain):
    """
    [v2.2] 자동 파라미터 최적화 파이프라인
    """
    logger.info("=" * 80)
    logger.info("   [v2.2 AUTO-OPTIMIZATION] 자동 파라미터 최적화 파이프라인 시작")
    logger.info("=" * 80)
    
    try:
        logger.info("   [Step 1/5] 현재 파라미터 조회 중...")
        current_params = database.get_all_config(db_conn)
        backtest_period = int(current_params.get('AUTO_OPTIMIZATION_PERIOD_DAYS', '90'))
        
        if not current_params:
            logger.warning("   ⚠️ CONFIG 테이블이 비어있습니다. 최적화를 건너뜁니다.")
            return False
        
        logger.info(f"   ✅ 현재 파라미터 {len(current_params)}개 조회 완료")
        
        logger.info("   [Step 2/5] 현재 파라미터로 백테스트 실행 중...")
        current_performance = run_simple_backtest(db_conn, current_params)
        
        if not current_performance:
            logger.warning("   ⚠️ 현재 파라미터 백테스트 실패. 최적화를 건너뜁니다.")
            return False
        
        logger.info(f"   ✅ 현재 성과: MDD {current_performance['mdd']:.2f}%, 연환산수익률 {current_performance['return']:.2f}%")
        
        logger.info("   [Step 3/5] 최적화 후보 파라미터 생성 중...")
        new_params = generate_optimized_params(current_params)
        logger.info(f"   ✅ 최적화 후보 파라미터 생성 완료 (변경: {len(new_params)}개)")
        
        logger.info("   [Step 4/5] 최적화 후보로 백테스트 실행 중...")
        new_performance = run_simple_backtest(db_conn, {**current_params, **new_params})
        
        if not new_performance:
            logger.warning("   ⚠️ 최적화 후보 백테스트 실패. 최적화를 건너뜁니다.")
            return False
        
        logger.info(f"   ✅ 최적화 성과: MDD {new_performance['mdd']:.2f}%, 연환산수익률 {new_performance['return']:.2f}%")
        
        logger.info("   [Step 5/5] AI 검증 (LLM) 시작...")
        market_summary = f"최근 {backtest_period}일 시장 요약"
        
        verification_result = verify_params_with_llm(
            brain, current_params, current_performance,
            new_params, new_performance, market_summary
        )
        
        if not verification_result:
            logger.warning("   ⚠️ AI 검증 실패. 최적화를 중단합니다.")
            return False
        
        is_approved = verification_result.get('is_approved', False)
        confidence = verification_result.get('confidence_score', 0.0)
        reasoning = verification_result.get('reasoning', 'N/A')
        
        logger.info(f"   ✅ AI 검증 완료: {is_approved}, 신뢰도: {confidence:.2f}")
        
        logger.info("   [v2.2] 최적화 이력 DB 저장 중...")
        ai_decision = 'APPROVED' if is_approved else 'REJECTED'
        
        optimization_id = database.save_optimization_history(
            connection=db_conn,
            current_params=current_params,
            new_params=new_params,
            current_performance=current_performance,
            new_performance=new_performance,
            ai_decision=ai_decision,
            ai_reasoning=reasoning,
            ai_confidence=confidence,
            market_summary=market_summary,
            backtest_period=backtest_period
        )
        
        if is_approved and confidence > 0.7:
            logger.info("   [Auto-Update] CONFIG 테이블 업데이트 시작...")
            update_count = 0
            for key, value in new_params.items():
                try:
                    database.set_config(db_conn, key, value)
                    update_count += 1
                    logger.info(f"   - {key}: {current_params.get(key)} → {value}")
                except Exception as e:
                    logger.error(f"   ❌ {key} 업데이트 실패: {e}")
            
            logger.info(f"   ✅ [Auto-Update] {update_count}/{len(new_params)}개 파라미터 업데이트 완료!")
            
            if optimization_id:
                database.mark_optimization_applied(db_conn, optimization_id)
            return True
        else:
            logger.warning(f"   ⚠️ [Auto-Update] 승인 거부 또는 신뢰도 부족 (신뢰도: {confidence:.2f} < 0.7)")
            return False
        
    except Exception as e:
        logger.error(f"   ❌ [AUTO-OPTIMIZATION] 오류 발생: {e}", exc_info=True)
        return False
    finally:
        logger.info("=" * 80)
