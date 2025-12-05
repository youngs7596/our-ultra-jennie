#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[v1.0] scripts/run_factor_analysis.py

FactorAnalyzer 배치 작업 실행 스크립트

주기적으로 실행하여 Scout의 '지능'을 업데이트합니다.
권장: 매주 일요일 또는 주말에 실행

분석 항목:
1. 팩터 예측력: 모멘텀, PER, PBR, ROE, RSI, 외국인매수의 IC/IR
2. 기본 조건: RSI 과매도, 거래량 급증 시 승률
3. 뉴스 카테고리: 실적/수주/신사업/M&A/배당/규제별 D+5 승률
4. 복합 조건: 뉴스+외인매수, RSI+외인매수 등 복합 조건 승률
5. 공시 영향도: DART 공시 유형별 승률

Usage:
    DB_TYPE=MARIADB python3 scripts/run_factor_analysis.py
    DB_TYPE=MARIADB python3 scripts/run_factor_analysis.py --codes 100 --regime BULL
"""

import argparse
import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

# 프로젝트 루트 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
import shared.database as database
from shared.hybrid_scoring.factor_analyzer import FactorAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def _is_mariadb() -> bool:
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"


def get_db_config():
    if _is_mariadb():
        return {
            "db_user": "dummy",
            "db_password": "dummy",
            "db_service_name": "dummy",
            "wallet_path": "dummy",
        }
    project_id = os.getenv("GCP_PROJECT_ID")
    db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), project_id)
    db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), project_id)
    wallet_path = os.path.join(PROJECT_ROOT, os.getenv("OCI_WALLET_DIR_NAME", "wallet"))
    return {
        "db_user": db_user,
        "db_password": db_password,
        "db_service_name": os.getenv("OCI_DB_SERVICE_NAME"),
        "wallet_path": wallet_path,
    }


def load_stock_codes(limit: int = None):
    """KOSPI 종목 코드 로드"""
    try:
        import FinanceDataReader as fdr
        codes = fdr.StockListing("KOSPI")["Code"].tolist()
        if limit:
            return codes[:limit]
        return codes
    except Exception as e:
        logger.warning(f"FinanceDataReader 로드 실패, DB에서 조회: {e}")
        return None


def parse_args():
    parser = argparse.ArgumentParser(description="FactorAnalyzer 배치 작업 실행")
    parser.add_argument("--codes", type=int, default=100, 
                        help="분석할 종목 수 (기본: 100)")
    parser.add_argument("--regime", type=str, default="ALL",
                        choices=["BULL", "BEAR", "SIDEWAYS", "ALL"],
                        help="시장 국면 (기본: ALL)")
    parser.add_argument("--skip-news", action="store_true",
                        help="뉴스 카테고리 분석 건너뛰기")
    parser.add_argument("--skip-compound", action="store_true",
                        help="복합 조건 분석 건너뛰기")
    parser.add_argument("--backtest", action="store_true",
                        help="[v5.0.5] 백테스트 시뮬레이션 실행")
    parser.add_argument("--backtest-days", type=int, default=180,
                        help="백테스트 기간 (일, 기본: 180)")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    
    logger.info("=" * 60)
    logger.info("🔬 FactorAnalyzer 배치 작업 시작")
    logger.info(f"   - 분석 종목 수: {args.codes}개")
    logger.info(f"   - 시장 국면: {args.regime}")
    logger.info("=" * 60)
    
    start_time = datetime.now()
    
    # DB 연결
    db_config = get_db_config()
    conn = database.get_db_connection(**db_config)
    if not conn:
        logger.error("❌ DB 연결 실패")
        return
    
    try:
        # 종목 코드 로드
        stock_codes = load_stock_codes(args.codes)
        if not stock_codes:
            logger.info("   DB에서 종목 코드 조회...")
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT STOCK_CODE 
                FROM STOCK_DAILY_PRICES_3Y
                WHERE STOCK_CODE != '0001'
                LIMIT %s
            """, (args.codes,))
            rows = cursor.fetchall()
            stock_codes = [r[0] if not isinstance(r, dict) else r['STOCK_CODE'] for r in rows]
            cursor.close()
        
        logger.info(f"   📊 분석 대상: {len(stock_codes)}개 종목")
        
        # FactorAnalyzer 실행
        analyzer = FactorAnalyzer(conn)
        results = analyzer.run_full_analysis(
            stock_codes=stock_codes,
            market_regime=args.regime
        )
        
        # 결과 요약
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info("\n" + "=" * 60)
        logger.info("📋 분석 결과 요약")
        logger.info("=" * 60)
        
        # 팩터 분석 결과
        if results.get('factor_analysis'):
            logger.info("\n[팩터 예측력 분석]")
            for fa in results['factor_analysis']:
                logger.info(f"   • {fa.factor_name}: IC={fa.ic_mean:.4f}, IR={fa.ir:.4f}, "
                           f"적중률={fa.hit_rate:.1%}, 추천가중치={fa.recommended_weight:.1%}")
        
        # 뉴스 카테고리 분석 결과
        if results.get('news_category_analysis'):
            logger.info("\n[뉴스 카테고리별 영향도]")
            for nc in results['news_category_analysis']:
                logger.info(f"   • {nc['category']}: 승률={nc['win_rate']:.1%}, "
                           f"평균수익률={nc['avg_return']:.2f}%, 표본={nc['sample_count']}")
        
        # 복합 조건 분석 결과
        if results.get('compound_condition_analysis'):
            logger.info("\n[복합 조건 분석]")
            for cc in results['compound_condition_analysis']:
                logger.info(f"   • {cc.condition_desc}: 승률={cc.win_rate:.1%}, "
                           f"평균수익률={cc.avg_return:.2f}%, 표본={cc.sample_count}")
        
        # 공시 분석 결과
        if results.get('disclosure_analysis'):
            logger.info("\n[공시 영향도 분석]")
            for da in results['disclosure_analysis']:
                logger.info(f"   • {da['category']}: 승률={da['win_rate']:.1%}, "
                           f"평균수익률={da['avg_return']:.2f}%, 표본={da['sample_count']}")
        
        # 오류
        if results.get('errors'):
            logger.warning(f"\n[오류] {len(results['errors'])}건 발생")
            for err in results['errors']:
                logger.warning(f"   • {err}")
        
        # [v5.0.5] 백테스트 실행
        if args.backtest:
            logger.info("\n" + "=" * 60)
            logger.info("🧪 백테스트 시뮬레이션 시작")
            logger.info("=" * 60)
            
            from datetime import timedelta
            start_date = datetime.now() - timedelta(days=args.backtest_days)
            
            backtest_results = analyzer.run_backtest(
                stock_codes=stock_codes,
                start_date=start_date,
                top_n=15,
                holding_days=5
            )
            
            if 'error' not in backtest_results:
                logger.info("\n[백테스트 전략별 성과]")
                for strategy_name, strategy_result in backtest_results.items():
                    if 'win_rate' in strategy_result:
                        logger.info(f"   • {strategy_name}: "
                                   f"승률={strategy_result['win_rate']:.1%}, "
                                   f"평균수익률={strategy_result['avg_return']:.2f}%")
        
        logger.info("\n" + "=" * 60)
        logger.info(f"✅ FactorAnalyzer 배치 작업 완료 ({elapsed:.1f}초)")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ 분석 중 오류 발생: {e}", exc_info=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

