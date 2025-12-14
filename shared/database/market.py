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
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    
    # SQLAlchemy Session인 경우
    if isinstance(connection, Session):
        result = connection.execute(text("""
            SELECT STOCK_CODE, STOCK_NAME, SECTOR
            FROM STOCK_MASTER
            WHERE STOCK_CODE = :stock_code
            LIMIT 1
        """), {"stock_code": stock_code})
        row = result.fetchone()
        
        if not row:
            return None
        return {
            "stock_code": row[0],
            "stock_name": row[1],
            "sector": row[2],
        }
    
    # Raw connection인 경우
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
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    
    # SQLAlchemy Session인 경우
    if isinstance(connection, Session):
        result = connection.execute(text("""
            SELECT STOCK_CODE, STOCK_NAME, SECTOR
            FROM STOCK_MASTER
            WHERE STOCK_NAME = :name
            LIMIT 1
        """), {"name": name})
        row = result.fetchone()
        
        if not row:
            return None
        return {
            "stock_code": row[0],
            "stock_name": row[1],
            "sector": row[2],
        }
    
    # Raw connection인 경우
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


def get_all_stock_codes(connection) -> List[str]:
    """
    [v5.0] 모든 종목 코드 조회
    """
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    
    # SQLAlchemy Session인 경우
    if isinstance(connection, Session):
        result = connection.execute(text("SELECT STOCK_CODE FROM STOCK_MASTER"))
        return [row[0] for row in result.fetchall()]
    
    # Raw connection인 경우
    cursor = connection.cursor()
    cursor.execute("SELECT STOCK_CODE FROM STOCK_MASTER")
    rows = cursor.fetchall()
    cursor.close()
    
    if not rows:
        return []
        
    return [row[0] for row in rows]


# ============================================================================
# [Price] 주가/펀더멘털 조회 및 저장
# ============================================================================

def save_all_daily_prices(session, all_daily_prices_params: List[dict]):
    """
    [v5.0] 일봉 데이터 Bulk 저장 (SQLAlchemy)
    """
    from sqlalchemy import text
    
    if not all_daily_prices_params:
        return
    
    try:
        for p in all_daily_prices_params:
            session.execute(text("""
                INSERT INTO STOCK_DAILY_PRICES (STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE)
                VALUES (:code, :date, :price, :high, :low)
                ON DUPLICATE KEY UPDATE
                    CLOSE_PRICE = VALUES(CLOSE_PRICE),
                    HIGH_PRICE = VALUES(HIGH_PRICE),
                    LOW_PRICE = VALUES(LOW_PRICE)
            """), {
                'code': p.get('p_code', p.get('stock_code')),
                'date': p.get('p_date', p.get('price_date')),
                'price': p.get('p_price', p.get('close_price')),
                'high': p.get('p_high', p.get('high_price')),
                'low': p.get('p_low', p.get('low_price'))
            })
        
        session.commit()
        logger.info(f"✅ DB: 모든 종목의 일봉 데이터 {len(all_daily_prices_params)}건 Bulk 저장 완료.")
    except Exception as e:
        logger.error(f"❌ DB: 모든 종목 일봉 데이터 Bulk 저장 실패! (에러: {e})")
        session.rollback()


def update_all_stock_fundamentals(session, all_fundamentals_params: List[dict]):
    """
    [v5.0] 주요 재무지표(PER, PBR, ROE) Bulk 저장/업데이트 (SQLAlchemy)
    """
    from sqlalchemy import text
    
    try:
        for p in all_fundamentals_params:
            session.execute(text("""
                INSERT INTO STOCK_FUNDAMENTALS (STOCK_CODE, TRADE_DATE, PER, PBR, ROE, MARKET_CAP)
                VALUES (:stock_code, :trade_date, :per, :pbr, :roe, :market_cap)
                ON DUPLICATE KEY UPDATE
                    PER = VALUES(PER),
                    PBR = VALUES(PBR),
                    ROE = VALUES(ROE),
                    MARKET_CAP = VALUES(MARKET_CAP)
            """), {
                'stock_code': p.get('stock_code'),
                'trade_date': p.get('trade_date'),
                'per': p.get('per'),
                'pbr': p.get('pbr'),
                'roe': p.get('roe'),
                'market_cap': p.get('market_cap')
            })
        
        session.commit()
        logger.info(f"✅ DB: 재무지표 {len(all_fundamentals_params)}건 저장/업데이트 완료.")
    except Exception as e:
        logger.error(f"❌ DB: 재무지표 저장 실패! (에러: {e})")
        session.rollback()


def get_daily_prices(connection, stock_code: str, limit: int = 30, table_name: str = "STOCK_DAILY_PRICES_3Y") -> pd.DataFrame:
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    
    # SQLAlchemy Session인 경우
    if isinstance(connection, Session):
        result = connection.execute(text(f"""
            SELECT PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME
            FROM {table_name}
            WHERE STOCK_CODE = :stock_code
            ORDER BY PRICE_DATE DESC
            LIMIT :limit
        """), {"stock_code": stock_code, "limit": limit})
        rows = result.fetchall()
        
        if not rows:
            return pd.DataFrame()
        
        df = pd.DataFrame(rows, columns=[
            "PRICE_DATE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME"
        ])
        df = df.iloc[::-1]  # 날짜 오름차순
        return df
    
    # Raw connection인 경우
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
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    
    if not stock_codes:
        return {}
    
    result = {code: [] for code in stock_codes}
    
    # SQLAlchemy Session인 경우
    if isinstance(connection, Session):
        # SQLAlchemy에서 IN 절 처리
        placeholder = ','.join([f':code{i}' for i in range(len(stock_codes))])
        params = {f'code{i}': code for i, code in enumerate(stock_codes)}
        
        query_result = connection.execute(text(f"""
            SELECT STOCK_CODE, PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME
            FROM {table_name}
            WHERE STOCK_CODE IN ({placeholder})
            ORDER BY STOCK_CODE, PRICE_DATE DESC
        """), params)
        rows = query_result.fetchall()
        
        if not rows:
            return {}
        
        for row in rows:
            code = row[0]
            if code in result:
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
    
    # Raw connection인 경우
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

def save_news_sentiment(session, stock_code, title, score, reason, url, published_at):
    """
    뉴스 감성 분석 결과를 영구 저장합니다.
    SQLAlchemy ORM을 사용합니다.
    """
    try:
        from shared.db.models import NewsSentiment
        table_name = _get_table_name("NEWS_SENTIMENT")
        
        # 중복 URL 체크 (이미 저장된 뉴스면 Skip)
        existing = session.query(NewsSentiment).filter(NewsSentiment.source_url == url).first()
        if existing:
            logger.debug(f"ℹ️ [DB] 이미 존재하는 뉴스입니다. (Skip): {title[:20]}...")
            return

        # published_at이 int timestamp인 경우 변환
        published_at_dt = None
        if isinstance(published_at, int):
            published_at_dt = datetime.fromtimestamp(published_at)
        elif isinstance(published_at, str):
            try:
                published_at_dt = datetime.fromisoformat(published_at)
            except ValueError:
                published_at_dt = datetime.strptime(published_at[:19], '%Y-%m-%d %H:%M:%S')
        elif isinstance(published_at, datetime):
            published_at_dt = published_at

        new_sentiment = NewsSentiment(
            stock_code=stock_code,
            news_title=title,
            sentiment_score=score,
            sentiment_reason=reason,
            source_url=url,
            published_at=published_at_dt
        )
        session.add(new_sentiment)
        # session_scope 컨텍스트 매니저가 commit/rollback을 처리합니다.
        logger.info(f"✅ [DB] 뉴스 감성 저장 완료: {stock_code} ({score}점)")
        
    except Exception as e:
        logger.error(f"❌ [DB] 뉴스 감성 저장 실패: {e}")
        raise # session_scope에서 rollback을 처리하도록 예외를 다시 발생시킵니다.
