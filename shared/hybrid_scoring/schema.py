#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout v1.0 Hybrid Scoring - DB 스키마 정의

새로운 테이블:
1. FACTOR_METADATA - 팩터별 예측력 메타데이터 (IC, IR, 가중치)
2. FACTOR_PERFORMANCE - 종목/섹터별 조건부 승률 통계
3. NEWS_FACTOR_STATS - 뉴스 카테고리별 영향도 통계
4. DAILY_QUANT_SCORE - 일별 정량 점수 기록 (역추적용)

[v1.0.3] Claude Opus 4.5 피드백 반영:
- Oracle UPSERT (MERGE INTO) 호환성 추가
"""

import logging
import os
from typing import Tuple, List, Any

logger = logging.getLogger(__name__)


def _is_mariadb() -> bool:
    """현재 DB 타입이 MariaDB인지 확인"""
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"


def is_oracle() -> bool:
    """현재 DB 타입이 Oracle인지 확인"""
    return os.getenv("DB_TYPE", "ORACLE").upper() == "ORACLE"


# =============================================================================
# [v1.0.3] Oracle 호환 UPSERT 유틸리티
# Claude Opus 4.5 피드백: "ON DUPLICATE KEY UPDATE는 MariaDB 전용"
# =============================================================================

def execute_upsert(cursor, 
                   table_name: str,
                   columns: List[str],
                   values: Tuple[Any, ...],
                   unique_keys: List[str],
                   update_columns: List[str] = None) -> bool:
    """
    [v1.0.3] DB 타입에 따라 적절한 UPSERT 실행
    
    Args:
        cursor: DB 커서
        table_name: 테이블명
        columns: 컬럼 리스트
        values: 값 튜플
        unique_keys: 유니크 키 컬럼들
        update_columns: 업데이트할 컬럼들 (None이면 unique_keys 제외 전체)
    
    Returns:
        성공 여부
    """
    if update_columns is None:
        update_columns = [c for c in columns if c not in unique_keys]
    
    if is_oracle():
        # Oracle: MERGE INTO
        return _execute_oracle_merge(cursor, table_name, columns, values, unique_keys, update_columns)
    else:
        # MariaDB: INSERT ... ON DUPLICATE KEY UPDATE
        return _execute_mariadb_upsert(cursor, table_name, columns, values, update_columns)


def _execute_mariadb_upsert(cursor,
                            table_name: str,
                            columns: List[str],
                            values: Tuple[Any, ...],
                            update_columns: List[str]) -> bool:
    """MariaDB용 UPSERT (INSERT ... ON DUPLICATE KEY UPDATE)"""
    placeholders = ', '.join(['%s'] * len(columns))
    column_str = ', '.join(columns)
    
    update_clause = ', '.join([f"{c} = VALUES({c})" for c in update_columns])
    
    sql = f"""
        INSERT INTO {table_name} ({column_str})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_clause}
    """
    
    cursor.execute(sql, values)
    return True


def _execute_oracle_merge(cursor,
                          table_name: str,
                          columns: List[str],
                          values: Tuple[Any, ...],
                          unique_keys: List[str],
                          update_columns: List[str]) -> bool:
    """Oracle용 UPSERT (MERGE INTO)"""
    # 값 딕셔너리 생성
    col_val_map = dict(zip(columns, values))
    
    # ON 조건 (유니크 키 매칭)
    on_clause = ' AND '.join([f"target.{k} = source.{k}" for k in unique_keys])
    
    # UPDATE SET 절
    update_set = ', '.join([f"target.{c} = source.{c}" for c in update_columns])
    
    # INSERT 절
    insert_cols = ', '.join(columns)
    insert_vals = ', '.join([f"source.{c}" for c in columns])
    
    # SOURCE 서브쿼리 (듀얼 테이블 사용)
    source_cols = ', '.join([f":v{i} AS {c}" for i, c in enumerate(columns)])
    
    sql = f"""
        MERGE INTO {table_name} target
        USING (SELECT {source_cols} FROM DUAL) source
        ON ({on_clause})
        WHEN MATCHED THEN
            UPDATE SET {update_set}
        WHEN NOT MATCHED THEN
            INSERT ({insert_cols})
            VALUES ({insert_vals})
    """
    
    # Oracle 바인드 변수 생성
    bind_vars = {f'v{i}': v for i, v in enumerate(values)}
    
    cursor.execute(sql, bind_vars)
    return True


# =============================================================================
# MariaDB DDL
# =============================================================================

MARIADB_SCHEMA = """
-- =============================================================================
-- FACTOR_METADATA: 팩터별 예측력 메타데이터
-- 주 1회 배치 작업으로 업데이트
-- =============================================================================
CREATE TABLE IF NOT EXISTS FACTOR_METADATA (
    ID INT AUTO_INCREMENT PRIMARY KEY,
    FACTOR_KEY VARCHAR(50) NOT NULL COMMENT '팩터 키 (예: momentum_6m, news_sentiment)',
    FACTOR_NAME VARCHAR(100) COMMENT '팩터 이름 (표시용)',
    MARKET_REGIME VARCHAR(20) DEFAULT 'ALL' COMMENT '시장 국면 (BULL, BEAR, SIDEWAYS, ALL)',
    
    -- 예측력 지표
    IC_MEAN DECIMAL(10,6) COMMENT 'Information Coefficient 평균',
    IC_STD DECIMAL(10,6) COMMENT 'IC 표준편차',
    IR DECIMAL(10,6) COMMENT 'Information Ratio (IC_MEAN / IC_STD)',
    HIT_RATE DECIMAL(5,4) COMMENT '적중률 (0.0 ~ 1.0)',
    
    -- 추천 가중치
    RECOMMENDED_WEIGHT DECIMAL(5,4) DEFAULT 0.10 COMMENT '추천 가중치 (0.0 ~ 1.0)',
    
    -- 표본 정보
    SAMPLE_COUNT INT DEFAULT 0 COMMENT '분석 표본 수',
    ANALYSIS_START_DATE DATE COMMENT '분석 시작일',
    ANALYSIS_END_DATE DATE COMMENT '분석 종료일',
    
    -- 메타 정보
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY UK_FACTOR_REGIME (FACTOR_KEY, MARKET_REGIME)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='팩터별 예측력 메타데이터';

-- =============================================================================
-- FACTOR_PERFORMANCE: 종목/섹터별 조건부 승률 통계
-- "삼성전자 + 외국인 순매수 + 뉴스점수 70↑" → 승률 80%
-- =============================================================================
CREATE TABLE IF NOT EXISTS FACTOR_PERFORMANCE (
    ID INT AUTO_INCREMENT PRIMARY KEY,
    
    -- 대상 식별
    TARGET_TYPE VARCHAR(20) NOT NULL COMMENT 'STOCK, SECTOR, ALL',
    TARGET_CODE VARCHAR(20) NOT NULL COMMENT '종목코드/섹터코드/ALL',
    TARGET_NAME VARCHAR(100) COMMENT '종목명/섹터명',
    
    -- 조건 정의
    CONDITION_KEY VARCHAR(100) NOT NULL COMMENT '조건 키 (예: news_score_70, foreign_buy)',
    CONDITION_DESC VARCHAR(200) COMMENT '조건 설명',
    
    -- 성과 통계
    WIN_RATE DECIMAL(5,4) COMMENT '승률 (0.0 ~ 1.0)',
    AVG_RETURN DECIMAL(10,4) COMMENT '평균 수익률 (%)',
    MEDIAN_RETURN DECIMAL(10,4) COMMENT '중앙 수익률 (%)',
    MAX_RETURN DECIMAL(10,4) COMMENT '최대 수익률 (%)',
    MIN_RETURN DECIMAL(10,4) COMMENT '최소 수익률 (%)',
    
    -- 측정 기간
    HOLDING_DAYS INT DEFAULT 5 COMMENT '보유 기간 (일)',
    
    -- 신뢰도
    SAMPLE_COUNT INT DEFAULT 0 COMMENT '표본 수',
    CONFIDENCE_LEVEL VARCHAR(10) DEFAULT 'LOW' COMMENT 'HIGH(30+), MID(15-29), LOW(<15)',
    
    -- 최근성 가중치 (Recency Weighting)
    RECENT_WIN_RATE DECIMAL(5,4) COMMENT '최근 3개월 승률',
    RECENT_SAMPLE_COUNT INT COMMENT '최근 3개월 표본 수',
    
    -- 메타 정보
    ANALYSIS_DATE DATE COMMENT '분석 기준일',
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY UK_TARGET_CONDITION (TARGET_TYPE, TARGET_CODE, CONDITION_KEY, HOLDING_DAYS)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='종목/섹터별 조건부 승률 통계';

-- 인덱스 추가 (조회 성능 최적화)
CREATE INDEX IF NOT EXISTS IDX_FACTOR_PERF_TARGET ON FACTOR_PERFORMANCE (TARGET_TYPE, TARGET_CODE);
CREATE INDEX IF NOT EXISTS IDX_FACTOR_PERF_CONDITION ON FACTOR_PERFORMANCE (CONDITION_KEY);
CREATE INDEX IF NOT EXISTS IDX_FACTOR_PERF_CONFIDENCE ON FACTOR_PERFORMANCE (CONFIDENCE_LEVEL);

-- =============================================================================
-- NEWS_FACTOR_STATS: 뉴스 카테고리별 영향도 통계
-- "수주 뉴스 발생 시 평균 +3.2%, 승률 78%"
-- =============================================================================
CREATE TABLE IF NOT EXISTS NEWS_FACTOR_STATS (
    ID INT AUTO_INCREMENT PRIMARY KEY,
    
    -- 대상 식별
    TARGET_TYPE VARCHAR(20) NOT NULL COMMENT 'STOCK, SECTOR, ALL',
    TARGET_CODE VARCHAR(20) NOT NULL COMMENT '종목코드/섹터코드/ALL',
    
    -- 뉴스 카테고리
    NEWS_CATEGORY VARCHAR(50) NOT NULL COMMENT '뉴스 카테고리 (실적, 수주, 규제, 신사업, M&A 등)',
    SENTIMENT VARCHAR(10) DEFAULT 'POSITIVE' COMMENT 'POSITIVE, NEGATIVE, NEUTRAL',
    
    -- 성과 통계
    WIN_RATE DECIMAL(5,4) COMMENT '승률 (상승 확률)',
    AVG_EXCESS_RETURN DECIMAL(10,4) COMMENT '평균 초과수익률 (%)',
    AVG_ABSOLUTE_RETURN DECIMAL(10,4) COMMENT '평균 절대수익률 (%)',
    
    -- 측정 기간별 통계
    RETURN_D1 DECIMAL(10,4) COMMENT 'D+1 평균 수익률',
    RETURN_D5 DECIMAL(10,4) COMMENT 'D+5 평균 수익률',
    RETURN_D20 DECIMAL(10,4) COMMENT 'D+20 평균 수익률',
    
    WIN_RATE_D1 DECIMAL(5,4) COMMENT 'D+1 승률',
    WIN_RATE_D5 DECIMAL(5,4) COMMENT 'D+5 승률',
    WIN_RATE_D20 DECIMAL(5,4) COMMENT 'D+20 승률',
    
    -- 신뢰도
    SAMPLE_COUNT INT DEFAULT 0 COMMENT '표본 수',
    CONFIDENCE_LEVEL VARCHAR(10) DEFAULT 'LOW' COMMENT 'HIGH(30+), MID(15-29), LOW(<15)',
    
    -- 메타 정보
    ANALYSIS_DATE DATE COMMENT '분석 기준일',
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY UK_NEWS_STATS (TARGET_TYPE, TARGET_CODE, NEWS_CATEGORY, SENTIMENT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='뉴스 카테고리별 영향도 통계';

-- 인덱스 추가
CREATE INDEX IF NOT EXISTS IDX_NEWS_STATS_TARGET ON NEWS_FACTOR_STATS (TARGET_TYPE, TARGET_CODE);
CREATE INDEX IF NOT EXISTS IDX_NEWS_STATS_CATEGORY ON NEWS_FACTOR_STATS (NEWS_CATEGORY);

-- =============================================================================
-- DAILY_QUANT_SCORE: 일별 정량 점수 기록 (역추적/백테스트용)
-- Scout가 왜 이 종목을 뽑았는지 추적 가능
-- =============================================================================
CREATE TABLE IF NOT EXISTS DAILY_QUANT_SCORE (
    ID INT AUTO_INCREMENT PRIMARY KEY,
    
    SCORE_DATE DATE NOT NULL COMMENT '점수 산출일',
    STOCK_CODE VARCHAR(20) NOT NULL COMMENT '종목 코드',
    STOCK_NAME VARCHAR(100) COMMENT '종목명',
    
    -- 정량 점수 (100점 만점)
    TOTAL_QUANT_SCORE DECIMAL(6,2) COMMENT '총 정량 점수',
    
    -- 팩터별 점수
    MOMENTUM_SCORE DECIMAL(6,2) COMMENT '모멘텀 점수',
    QUALITY_SCORE DECIMAL(6,2) COMMENT '품질 점수',
    VALUE_SCORE DECIMAL(6,2) COMMENT '가치 점수',
    TECHNICAL_SCORE DECIMAL(6,2) COMMENT '기술적 점수',
    NEWS_STAT_SCORE DECIMAL(6,2) COMMENT '뉴스 통계 점수',
    SUPPLY_DEMAND_SCORE DECIMAL(6,2) COMMENT '수급 점수',
    
    -- 조건부 승률 정보
    MATCHED_CONDITION VARCHAR(200) COMMENT '매칭된 조건',
    CONDITION_WIN_RATE DECIMAL(5,4) COMMENT '조건부 승률',
    CONDITION_SAMPLE_COUNT INT COMMENT '조건 표본 수',
    
    -- 필터링 결과
    IS_PASSED_FILTER TINYINT DEFAULT 0 COMMENT '1차 필터 통과 여부',
    FILTER_RANK INT COMMENT '정량 점수 기준 순위',
    
    -- LLM 분석 결과 (Phase 3 이후)
    LLM_SCORE DECIMAL(6,2) COMMENT 'LLM 정성 점수',
    HYBRID_SCORE DECIMAL(6,2) COMMENT '최종 하이브리드 점수',
    IS_FINAL_SELECTED TINYINT DEFAULT 0 COMMENT '최종 선정 여부',
    
    -- 메타 정보
    MARKET_REGIME VARCHAR(20) COMMENT '당시 시장 국면',
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY UK_DAILY_SCORE (SCORE_DATE, STOCK_CODE)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='일별 정량 점수 기록';

-- 인덱스 추가
CREATE INDEX IF NOT EXISTS IDX_DAILY_SCORE_DATE ON DAILY_QUANT_SCORE (SCORE_DATE);
CREATE INDEX IF NOT EXISTS IDX_DAILY_SCORE_STOCK ON DAILY_QUANT_SCORE (STOCK_CODE);
CREATE INDEX IF NOT EXISTS IDX_DAILY_SCORE_PASSED ON DAILY_QUANT_SCORE (IS_PASSED_FILTER);
"""


# =============================================================================
# Oracle DDL (호환성 유지)
# =============================================================================

ORACLE_SCHEMA = """
-- FACTOR_METADATA 테이블
CREATE TABLE FACTOR_METADATA (
    ID NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    FACTOR_KEY VARCHAR2(50) NOT NULL,
    FACTOR_NAME VARCHAR2(100),
    MARKET_REGIME VARCHAR2(20) DEFAULT 'ALL',
    IC_MEAN NUMBER(10,6),
    IC_STD NUMBER(10,6),
    IR NUMBER(10,6),
    HIT_RATE NUMBER(5,4),
    RECOMMENDED_WEIGHT NUMBER(5,4) DEFAULT 0.10,
    SAMPLE_COUNT NUMBER DEFAULT 0,
    ANALYSIS_START_DATE DATE,
    ANALYSIS_END_DATE DATE,
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT UK_FACTOR_REGIME UNIQUE (FACTOR_KEY, MARKET_REGIME)
);

-- FACTOR_PERFORMANCE 테이블
CREATE TABLE FACTOR_PERFORMANCE (
    ID NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    TARGET_TYPE VARCHAR2(20) NOT NULL,
    TARGET_CODE VARCHAR2(20) NOT NULL,
    TARGET_NAME VARCHAR2(100),
    CONDITION_KEY VARCHAR2(100) NOT NULL,
    CONDITION_DESC VARCHAR2(200),
    WIN_RATE NUMBER(5,4),
    AVG_RETURN NUMBER(10,4),
    MEDIAN_RETURN NUMBER(10,4),
    MAX_RETURN NUMBER(10,4),
    MIN_RETURN NUMBER(10,4),
    HOLDING_DAYS NUMBER DEFAULT 5,
    SAMPLE_COUNT NUMBER DEFAULT 0,
    CONFIDENCE_LEVEL VARCHAR2(10) DEFAULT 'LOW',
    RECENT_WIN_RATE NUMBER(5,4),
    RECENT_SAMPLE_COUNT NUMBER,
    ANALYSIS_DATE DATE,
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT UK_TARGET_CONDITION UNIQUE (TARGET_TYPE, TARGET_CODE, CONDITION_KEY, HOLDING_DAYS)
);

-- NEWS_FACTOR_STATS 테이블
CREATE TABLE NEWS_FACTOR_STATS (
    ID NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    TARGET_TYPE VARCHAR2(20) NOT NULL,
    TARGET_CODE VARCHAR2(20) NOT NULL,
    NEWS_CATEGORY VARCHAR2(50) NOT NULL,
    SENTIMENT VARCHAR2(10) DEFAULT 'POSITIVE',
    WIN_RATE NUMBER(5,4),
    AVG_EXCESS_RETURN NUMBER(10,4),
    AVG_ABSOLUTE_RETURN NUMBER(10,4),
    RETURN_D1 NUMBER(10,4),
    RETURN_D5 NUMBER(10,4),
    RETURN_D20 NUMBER(10,4),
    WIN_RATE_D1 NUMBER(5,4),
    WIN_RATE_D5 NUMBER(5,4),
    WIN_RATE_D20 NUMBER(5,4),
    SAMPLE_COUNT NUMBER DEFAULT 0,
    CONFIDENCE_LEVEL VARCHAR2(10) DEFAULT 'LOW',
    ANALYSIS_DATE DATE,
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT UK_NEWS_STATS UNIQUE (TARGET_TYPE, TARGET_CODE, NEWS_CATEGORY, SENTIMENT)
);

-- DAILY_QUANT_SCORE 테이블
CREATE TABLE DAILY_QUANT_SCORE (
    ID NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    SCORE_DATE DATE NOT NULL,
    STOCK_CODE VARCHAR2(20) NOT NULL,
    STOCK_NAME VARCHAR2(100),
    TOTAL_QUANT_SCORE NUMBER(6,2),
    MOMENTUM_SCORE NUMBER(6,2),
    QUALITY_SCORE NUMBER(6,2),
    VALUE_SCORE NUMBER(6,2),
    TECHNICAL_SCORE NUMBER(6,2),
    NEWS_STAT_SCORE NUMBER(6,2),
    SUPPLY_DEMAND_SCORE NUMBER(6,2),
    MATCHED_CONDITION VARCHAR2(200),
    CONDITION_WIN_RATE NUMBER(5,4),
    CONDITION_SAMPLE_COUNT NUMBER,
    IS_PASSED_FILTER NUMBER(1) DEFAULT 0,
    FILTER_RANK NUMBER,
    LLM_SCORE NUMBER(6,2),
    HYBRID_SCORE NUMBER(6,2),
    IS_FINAL_SELECTED NUMBER(1) DEFAULT 0,
    MARKET_REGIME VARCHAR2(20),
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT UK_DAILY_SCORE UNIQUE (SCORE_DATE, STOCK_CODE)
);
"""


def create_hybrid_scoring_tables(connection) -> bool:
    """
    하이브리드 스코어링에 필요한 테이블들을 생성합니다.
    
    Args:
        connection: DB 연결 객체 (MariaDB 또는 Oracle)
    
    Returns:
        성공 여부
    """
    try:
        cursor = connection.cursor()
        
        if _is_mariadb():
            logger.info("🔧 MariaDB용 하이브리드 스코어링 테이블 생성 중...")
            # MariaDB는 각 문장을 개별 실행
            statements = [s.strip() for s in MARIADB_SCHEMA.split(';') if s.strip()]
            for stmt in statements:
                if stmt and not stmt.startswith('--'):
                    try:
                        cursor.execute(stmt)
                    except Exception as e:
                        # CREATE INDEX IF NOT EXISTS가 지원되지 않는 경우 무시
                        if 'Duplicate key name' in str(e) or 'already exists' in str(e):
                            logger.debug(f"   (스키마) 인덱스 이미 존재: {e}")
                        else:
                            logger.warning(f"   (스키마) 문장 실행 경고: {e}")
        else:
            logger.info("🔧 Oracle용 하이브리드 스코어링 테이블 생성 중...")
            statements = [s.strip() for s in ORACLE_SCHEMA.split(';') if s.strip()]
            for stmt in statements:
                if stmt and not stmt.startswith('--'):
                    try:
                        cursor.execute(stmt)
                    except Exception as e:
                        if 'already exists' in str(e).lower() or 'ORA-00955' in str(e):
                            logger.debug(f"   (스키마) 테이블 이미 존재")
                        else:
                            logger.warning(f"   (스키마) 문장 실행 경고: {e}")
        
        connection.commit()
        cursor.close()
        
        logger.info("✅ 하이브리드 스코어링 테이블 생성 완료")
        return True
        
    except Exception as e:
        logger.error(f"❌ 하이브리드 스코어링 테이블 생성 실패: {e}", exc_info=True)
        return False


def get_default_factor_weights() -> dict:
    """
    [v1.0] 기본 팩터 가중치 반환 (3 AI 합의 - D+60 기준)
    
    핵심 발견 (2025-12-05 팩터 분석):
    - RSI 과매도: D+5 54.6%, D+60 60.1% → 핵심 팩터
    - ROE: D+60 65.6% → 장기 핵심 팩터
    - 복합조건(RSI+외인): 55.5% → 단기 유일한 알파
    - 모멘텀: IC 음수 → 한국 시장에서 역효과
    - 뉴스: D+5 43.7% (역신호), D+60 72.7% (순신호)
    
    Returns:
        {factor_key: weight} 딕셔너리
    """
    return {
        # =================================================
        # 핵심 팩터 (검증됨!)
        # =================================================
        'quality_roe': 0.25,      # ROE (D+60 적중률 65.6%, 최고!)
        'technical_rsi': 0.20,    # RSI 과매도 (D+60 적중률 60.1%)
        'compound_rsi_foreign': 0.15, # RSI+외인 복합조건 (55.5%)
        
        # =================================================
        # 보조 팩터
        # =================================================
        'supply_foreign': 0.12,   # 외국인 수급
        'supply_institution': 0.08, # 기관 수급
        'value_per': 0.07,        # PER (D+60 적중률 59.9%)
        'technical_volume': 0.05, # 거래량 추세
        
        # =================================================
        # 축소/무시 팩터 (역효과 또는 무효)
        # =================================================
        'momentum_6m': 0.02,      # 6개월 모멘텀 (IC=-0.005, 역방향!)
        'momentum_1m': 0.01,      # 1개월 단기 모멘텀 (IC=-0.04, 역방향!)
        'value_pbr': 0.02,        # PBR (48.6% 승률, 무효)
        'quality_growth': 0.03,   # 성장성
        
        # =================================================
        # 뉴스 (역발상 로직 적용)
        # =================================================
        'news_short_term': -0.05, # 뉴스 단기 (역신호! 패널티)
        'news_long_term': 0.10,   # 뉴스 장기 (순신호)
    }


def get_confidence_level(sample_count: int) -> str:
    """
    표본 수에 따른 신뢰도 등급 반환
    
    Args:
        sample_count: 표본 수
    
    Returns:
        'HIGH', 'MID', 'LOW'
    """
    if sample_count >= 30:
        return 'HIGH'
    elif sample_count >= 15:
        return 'MID'
    else:
        return 'LOW'


def get_confidence_weight(sample_count: int) -> float:
    """
    표본 수에 따른 신뢰도 가중치 반환 (Claude 설계)
    
    표본 30개 이상: 100%
    표본 20개: 80%
    표본 10개: 50%
    표본 5개 미만: 0% (사용하지 않음)
    
    Args:
        sample_count: 표본 수
    
    Returns:
        신뢰도 가중치 (0.0 ~ 1.0)
    """
    if sample_count >= 30:
        return 1.0
    elif sample_count >= 20:
        return 0.8
    elif sample_count >= 10:
        return 0.5
    elif sample_count >= 5:
        return 0.3
    else:
        return 0.0  # 표본 5개 미만은 사용하지 않음

