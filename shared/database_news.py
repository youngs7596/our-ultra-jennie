"""
shared/database_news.py

뉴스/감성 관련 DB 유틸을 분리한 모듈입니다.
"""

import logging
from datetime import datetime

from .database_base import _get_table_name

logger = logging.getLogger(__name__)


def save_news_sentiment(connection, stock_code, title, score, reason, url, published_at):
    """
    뉴스 감성 분석 결과를 영구 저장합니다.
    MariaDB/Oracle 하이브리드 지원.
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        table_name = _get_table_name("NEWS_SENTIMENT")
        
        # 테이블 존재 여부 확인 (없으면 자동 생성)
        try:
            cursor.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
        except Exception:
            logger.warning(f"⚠️ 테이블 {table_name}이 없어 생성을 시도합니다.")
            create_sql = f"""
            CREATE TABLE {table_name} (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                STOCK_CODE VARCHAR(20) NOT NULL,
                NEWS_TITLE VARCHAR(1000),
                SENTIMENT_SCORE INT DEFAULT 50,
                SENTIMENT_REASON VARCHAR(2000),
                SOURCE_URL VARCHAR(2000),
                PUBLISHED_AT DATETIME,
                CREATED_AT DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY UK_NEWS_URL (SOURCE_URL(500))
            )
            """
            cursor.execute(create_sql)
            connection.commit()
            logger.info(f"✅ 테이블 {table_name} 생성 완료")

        # 중복 URL 체크 (이미 저장된 뉴스면 Skip)
        check_sql = f"SELECT 1 FROM {table_name} WHERE SOURCE_URL = %s"
        cursor.execute(check_sql, [url])
        if cursor.fetchone():
            logger.debug(f"ℹ️ [DB] 이미 존재하는 뉴스입니다. (Skip): {title[:20]}...")
            return

        # published_at이 int timestamp인 경우 변환
        if isinstance(published_at, int):
            published_at_str = datetime.fromtimestamp(published_at).strftime('%Y-%m-%d %H:%M:%S')
        else:
            published_at_str = str(published_at)[:19]

        insert_sql = f"""
        INSERT INTO {table_name} 
        (STOCK_CODE, NEWS_TITLE, SENTIMENT_SCORE, SENTIMENT_REASON, SOURCE_URL, PUBLISHED_AT)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_sql, [stock_code, title, score, reason, url, published_at_str])
        
        connection.commit()
        logger.info(f"✅ [DB] 뉴스 감성 저장 완료: {stock_code} ({score}점)")
        
    except Exception as e:
        logger.error(f"❌ [DB] 뉴스 감성 저장 실패: {e}")
        if connection:
            connection.rollback()
    finally:
        if cursor:
            cursor.close()
