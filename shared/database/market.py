"""
shared/database/market.py

종목 마스터, 주가/펀더멘털 데이터, 뉴스 감성 저장 등
시장 데이터(Market Data) 전반을 담당합니다.
(기존 database_master.py + database_price.py + database_news.py + database_marketdata.py 일부 통합)
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from .core import _get_table_name, _is_mariadb

logger = logging.getLogger(__name__)


# ============================================================================
# [Master] 종목 마스터 조회
# ============================================================================

def get_stock_by_code(connection, stock_code: str) -> Optional[Dict]:
    cursor = connection.cursor()
    cursor.execute("""
        SELECT STOCK_CODE, STOCK_NAME, SECTOR
        FROM STOCK_MASTER
        WHERE STOCK_CODE = %s
        LIMIT 1
    """, [stock_code])
    row = cursor.fetchone()
    cursor.close()
    
    if not row:
        return None
    
    if isinstance(row, dict):
        return row
    return {
        "stock_code": row[0],
        "stock_name": row[1],
        "sector": row[2],
    }


def search_stock_by_name(connection, name: str) -> Optional[Dict]:
    cursor = connection.cursor()
    cursor.execute("""
        SELECT STOCK_CODE, STOCK_NAME, SECTOR
        FROM STOCK_MASTER
        WHERE STOCK_NAME = %s
        LIMIT 1
    """, [name])
    row = cursor.fetchone()
    cursor.close()
    
    if not row:
        return None
    
    if isinstance(row, dict):
        return row
    return {
        "stock_code": row[0],
        "stock_name": row[1],
        "sector": row[2],
    }


# ============================================================================
# [Price] 주가/펀더멘털 조회 및 저장
# ============================================================================

def save_all_daily_prices(connection, all_daily_prices_params: List[dict]):
    """일봉 데이터 Bulk 저장 (MariaDB/Oracle 호환)"""
    cursor = None
    try:
        cursor = connection.cursor()
        
        if _is_mariadb():
            # MariaDB: INSERT ... ON DUPLICATE KEY UPDATE
            sql = """
            INSERT INTO STOCK_DAILY_PRICES (STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                CLOSE_PRICE = VALUES(CLOSE_PRICE),
                HIGH_PRICE = VALUES(HIGH_PRICE),
                LOW_PRICE = VALUES(LOW_PRICE)
            """
            insert_data = []
            for p in all_daily_prices_params:
                insert_data.append((
                    p.get('p_code', p.get('stock_code')),
                    p.get('p_date', p.get('price_date')),
                    p.get('p_price', p.get('close_price')),
                    p.get('p_high', p.get('high_price')),
                    p.get('p_low', p.get('low_price'))
                ))
            cursor.executemany(sql, insert_data)
        else:
            # Oracle: MERGE
            sql_merge = """
            MERGE /*+ NO_PARALLEL */ INTO STOCK_DAILY_PRICES t
            USING (SELECT TO_DATE(:p_date, 'YYYY-MM-DD') AS price_date, :p_code AS stock_code, 
                          :p_price AS close_price, :p_high AS high_price, :p_low AS low_price FROM DUAL) s
            ON (t.STOCK_CODE = s.stock_code AND t.PRICE_DATE = s.price_date)
            WHEN MATCHED THEN
                UPDATE SET t.CLOSE_PRICE = s.close_price, t.HIGH_PRICE = s.high_price, t.LOW_PRICE = s.low_price
            WHEN NOT MATCHED THEN
                INSERT (STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE)
                VALUES (s.stock_code, s.price_date, s.close_price, s.high_price, s.low_price)
            """
            cursor.executemany(sql_merge, all_daily_prices_params)
        
        connection.commit()
        logger.info(f"✅ DB: 모든 종목의 일봉 데이터 {len(all_daily_prices_params)}건 Bulk 저장 완료.")
    except Exception as e:
        logger.error(f"❌ DB: 모든 종목 일봉 데이터 Bulk 저장 실패! (에러: {e})")
        if connection:
            connection.rollback()
    finally:
        if cursor:
            cursor.close()


def update_all_stock_fundamentals(connection, all_fundamentals_params: List[dict]):
    """주요 재무지표(PER, PBR, ROE) Bulk 저장/업데이트"""
    cursor = None
    try:
        cursor = connection.cursor()
        
        if _is_mariadb():
            sql = """
            INSERT INTO STOCK_FUNDAMENTALS (STOCK_CODE, TRADE_DATE, PER, PBR, ROE, MARKET_CAP)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                PER = VALUES(PER),
                PBR = VALUES(PBR),
                ROE = VALUES(ROE),
                MARKET_CAP = VALUES(MARKET_CAP)
            """
            insert_data = []
            for p in all_fundamentals_params:
                insert_data.append((
                    p.get('stock_code'),
                    p.get('trade_date'),
                    p.get('per'),
                    p.get('pbr'),
                    p.get('roe'),
                    p.get('market_cap'),
                ))
            cursor.executemany(sql, insert_data)
        else:
            sql_merge = """
            MERGE /*+ NO_PARALLEL */ INTO STOCK_FUNDAMENTALS t
            USING (SELECT TO_DATE(:trade_date, 'YYYY-MM-DD') AS trade_date,
                          :stock_code AS stock_code, :per AS per, :pbr AS pbr,
                          :roe AS roe, :market_cap AS market_cap FROM DUAL) s
            ON (t.STOCK_CODE = s.stock_code AND t.TRADE_DATE = s.trade_date)
            WHEN MATCHED THEN
                UPDATE SET t.PER = s.per, t.PBR = s.pbr, t.ROE = s.roe, t.MARKET_CAP = s.market_cap
            WHEN NOT MATCHED THEN
                INSERT (STOCK_CODE, TRADE_DATE, PER, PBR, ROE, MARKET_CAP)
                VALUES (s.stock_code, s.trade_date, s.per, s.pbr, s.roe, s.market_cap)
            """
            cursor.executemany(sql_merge, all_fundamentals_params)
        
        connection.commit()
        logger.info(f"✅ DB: 재무지표 {len(all_fundamentals_params)}건 저장/업데이트 완료.")
    except Exception as e:
        logger.error(f"❌ DB: 재무지표 저장 실패! (에러: {e})")
        if connection:
            connection.rollback()
    finally:
        if cursor:
            cursor.close()


def get_daily_prices(connection, stock_code: str, limit: int = 30, table_name: str = "STOCK_DAILY_PRICES_3Y") -> pd.DataFrame:
    cursor = connection.cursor()
    cursor.execute(f"""
        SELECT PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME
        FROM {table_name}
        WHERE STOCK_CODE = %s
        ORDER BY PRICE_DATE DESC
        LIMIT %s
    """, [stock_code, limit])
    rows = cursor.fetchall()
    cursor.close()
    
    if not rows:
        return pd.DataFrame()
    
    if isinstance(rows[0], dict):
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame(rows, columns=[
            "PRICE_DATE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME"
        ])
    df = df.iloc[::-1]  # 날짜 오름차순
    return df


def get_daily_prices_batch(connection, stock_codes: list, limit: int = 120, table_name: str = "STOCK_DAILY_PRICES_3Y"):
    """
    여러 종목의 일봉을 한 번에 조회하여 dict[code] = DataFrame 형태로 반환
    """
    if not stock_codes:
        return {}
    
    cursor = connection.cursor()
    # MariaDB에서 리스트 파라미터 처리가 드라이버(PyMySQL/MySQLConnector)에 따라 다를 수 있어 안전하게 문자열 치환 사용
    # *주의: SQL Injection 방지를 위해 stock_codes 내용 검증 필요하나, 내부 로직상 안전하다 가정
    placeholder = ','.join(['%s'] * len(stock_codes))
    cursor.execute(f"""
        SELECT STOCK_CODE, PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME
        FROM {table_name}
        WHERE STOCK_CODE IN ({placeholder})
        ORDER BY STOCK_CODE, PRICE_DATE DESC
    """, stock_codes)
    rows = cursor.fetchall()
    cursor.close()
    
    if not rows:
        return {}
    
    result = {code: [] for code in stock_codes}
    
    # rows를 code별로 묶기
    for row in rows:
        if isinstance(row, dict):
            code = row['STOCK_CODE']
            result[code].append(row)
        else:
            # 튜플 인덱스 주의
            code = row[0]
            result[code].append({
                "STOCK_CODE": row[0],
                "PRICE_DATE": row[1],
                "OPEN_PRICE": row[2],
                "HIGH_PRICE": row[3],
                "LOW_PRICE": row[4],
                "CLOSE_PRICE": row[5],
                "VOLUME": row[6],
            })
    
    # dict -> DataFrame 변환 및 날짜 오름차순
    for code, items in result.items():
        if not items:
            continue
        df = pd.DataFrame(items)
        df = df.iloc[::-1]
        result[code] = df
    
    return result


# ============================================================================
# [News] 뉴스 감성 저장
# ============================================================================

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
