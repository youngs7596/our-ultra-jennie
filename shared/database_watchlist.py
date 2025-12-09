"""
shared/database_watchlist.py - Watchlist 관련 함수

이 모듈은 WatchList 테이블의 CRUD 및 히스토리 관리 함수를 제공합니다.
"""

import json
import logging
from datetime import datetime, timezone
from shared.database_base import _is_mariadb

logger = logging.getLogger(__name__)


def save_to_watchlist(connection, candidates_to_save):
    """
    WatchList 저장 (MariaDB/Oracle 호환)
    
    [v4.1] UPSERT 방식으로 변경:
    - 새 종목: INSERT
    - 기존 종목: UPDATE (점수, 이유 갱신)
    - 24시간 지난 종목: 자동 삭제 (TTL)
    
    이렇게 하면 1시간마다 실행해도 이전 종목이 유지됨!
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        # [v4.1] Step 1: 24시간 지난 오래된 종목 삭제 (TTL)
        logger.info("   (DB) 1. 24시간 지난 오래된 종목 정리 중...")
        if _is_mariadb():
            cursor.execute("""
                DELETE FROM WatchList 
                WHERE LLM_UPDATED_AT < DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """)
        else:
            cursor.execute("""
                DELETE FROM WatchList 
                WHERE LLM_UPDATED_AT < SYSTIMESTAMP - INTERVAL '24' HOUR
            """)
        deleted_count = cursor.rowcount
        if deleted_count > 0:
            logger.info(f"   (DB) ✅ {deleted_count}개 오래된 종목 삭제")
        
        if not candidates_to_save:
            logger.info("   (DB) 저장할 후보가 없습니다. (기존 종목 유지)")
            connection.commit()
            return
        
        logger.info(f"   (DB) 2. 우량주 후보 {len(candidates_to_save)}건 UPSERT...")
        
        now = datetime.now(timezone.utc)
        
        # [v4.1] UPSERT 쿼리 (기존 종목은 UPDATE, 새 종목은 INSERT)
        if _is_mariadb():
            sql_upsert = """
            INSERT INTO WatchList (
                STOCK_CODE, STOCK_NAME, CREATED_AT, IS_TRADABLE,
                LLM_SCORE, LLM_REASON, LLM_UPDATED_AT
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                STOCK_NAME = VALUES(STOCK_NAME),
                IS_TRADABLE = VALUES(IS_TRADABLE),
                LLM_SCORE = VALUES(LLM_SCORE),
                LLM_REASON = VALUES(LLM_REASON),
                LLM_UPDATED_AT = VALUES(LLM_UPDATED_AT)
            """
        else:
            # Oracle: MERGE INTO 사용
            sql_upsert = """
            MERGE INTO WatchList w
            USING (SELECT :1 AS code, :2 AS name, :3 AS tradable, :4 AS score, :5 AS reason FROM DUAL) src
            ON (w.STOCK_CODE = src.code)
            WHEN MATCHED THEN
                UPDATE SET STOCK_NAME = src.name, IS_TRADABLE = src.tradable,
                           LLM_SCORE = src.score, LLM_REASON = src.reason, LLM_UPDATED_AT = SYSTIMESTAMP
            WHEN NOT MATCHED THEN
                INSERT (STOCK_CODE, STOCK_NAME, CREATED_AT, IS_TRADABLE, LLM_SCORE, LLM_REASON, LLM_UPDATED_AT)
                VALUES (src.code, src.name, SYSTIMESTAMP, src.tradable, src.score, src.reason, SYSTIMESTAMP)
            """
        
        insert_count = 0
        update_count = 0
        metadata_marker = "[LLM_METADATA]"
        
        for c in candidates_to_save:
            # LLM 점수와 이유 추출 (기본값: 점수 0, 이유 없음)
            llm_score = c.get('llm_score', 0)
            llm_reason = c.get('llm_reason', '') or ''
            llm_metadata = c.get('llm_metadata')

            if llm_metadata:
                try:
                    metadata_json = json.dumps(llm_metadata, ensure_ascii=False)
                    llm_reason = f"{llm_reason}\n\n{metadata_marker}{metadata_json}"
                except Exception as e:
                    logger.warning(f"⚠️ WatchList 메타데이터 직렬화 실패: {e}")

            # REASON 길이 제한 (TEXT 타입이지만 안전하게 제한)
            if len(llm_reason) > 60000:
                llm_reason = llm_reason[:60000] + "..."
            
            # [v4.1] 개별 UPSERT 실행 (MariaDB/Oracle)
            if _is_mariadb():
                params = (
                    c['code'], 
                    c['name'],
                    now,  # CREATED_AT
                    1 if c.get('is_tradable', True) else 0,
                    llm_score,
                    llm_reason,
                    now  # LLM_UPDATED_AT
                )
                cursor.execute(sql_upsert, params)
                # rowcount: 1=INSERT, 2=UPDATE (MariaDB ON DUPLICATE KEY UPDATE 특성)
                if cursor.rowcount == 1:
                    insert_count += 1
                elif cursor.rowcount == 2:
                    update_count += 1
            else:
                params = (
                    c['code'], 
                    c['name'], 
                    1 if c.get('is_tradable', True) else 0,
                    llm_score,
                    llm_reason
                )
                cursor.execute(sql_upsert, params)
                # Oracle MERGE는 rowcount가 항상 1
                insert_count += 1
        
        connection.commit()
        logger.info(f"   (DB) ✅ WatchList UPSERT 완료! (신규 {insert_count}건, 갱신 {update_count}건)")
    except Exception as e:
        logger.error(f"❌ DB: save_to_watchlist 실패! (에러: {e})")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()


def save_to_watchlist_history(connection, candidates_to_save, snapshot_date=None):
    """
    [v3.8] WatchList 스냅샷을 히스토리 테이블에 저장합니다. (Point-in-Time Backtest용)
    MariaDB/Oracle 하이브리드 지원 (Claude Opus 4.5)
    """
    cursor = None
    is_mariadb = _is_mariadb()
    
    try:
        cursor = connection.cursor()
        
        # 테이블 확인 및 생성
        table_name = "WATCHLIST_HISTORY"
        
        if is_mariadb:
            # MariaDB: 테이블 존재 여부 확인
            cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
            if not cursor.fetchone():
                logger.warning(f"⚠️ 테이블 {table_name}이 없어 생성을 시도합니다.")
                create_sql = f"""
                CREATE TABLE {table_name} (
                    SNAPSHOT_DATE DATE NOT NULL,
                    STOCK_CODE VARCHAR(16) NOT NULL,
                    STOCK_NAME VARCHAR(128),
                    IS_TRADABLE TINYINT DEFAULT 1,
                    LLM_SCORE INT,
                    LLM_REASON TEXT,
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (SNAPSHOT_DATE, STOCK_CODE)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
                cursor.execute(create_sql)
                logger.info(f"✅ 테이블 {table_name} 생성 완료")
        else:
            # Oracle: ROWNUM 사용
            try:
                cursor.execute(f"SELECT 1 FROM {table_name} WHERE ROWNUM=1")
            except Exception:
                logger.warning(f"⚠️ 테이블 {table_name}이 없어 생성을 시도합니다.")
                create_sql = f"""
                CREATE TABLE {table_name} (
                    SNAPSHOT_DATE DATE NOT NULL,
                    STOCK_CODE VARCHAR2(16) NOT NULL,
                    STOCK_NAME VARCHAR2(128),
                    IS_TRADABLE NUMBER(1) DEFAULT 1,
                    LLM_SCORE NUMBER,
                    LLM_REASON VARCHAR2(4000),
                    CREATED_AT TIMESTAMP DEFAULT SYSTIMESTAMP,
                    CONSTRAINT PK_{table_name} PRIMARY KEY (SNAPSHOT_DATE, STOCK_CODE)
                )
                """
                cursor.execute(create_sql)
                logger.info(f"✅ 테이블 {table_name} 생성 완료")

        if snapshot_date is None:
            snapshot_date = datetime.now().strftime('%Y-%m-%d')

        # 해당 날짜의 기존 데이터 삭제 (재실행 시 중복 방지)
        if is_mariadb:
            cursor.execute(f"DELETE FROM {table_name} WHERE SNAPSHOT_DATE = %s", (snapshot_date,))
        else:
            cursor.execute(f"DELETE /*+ NO_PARALLEL */ FROM {table_name} WHERE SNAPSHOT_DATE = TO_DATE(:1, 'YYYY-MM-DD')", [snapshot_date])
        
        if not candidates_to_save:
            connection.commit()
            return

        logger.info(f"   (DB) '{snapshot_date}' 기준 WatchList 히스토리 {len(candidates_to_save)}건 저장...")
        
        if is_mariadb:
            sql_insert = f"""
            INSERT INTO {table_name} (
                SNAPSHOT_DATE, STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """
        else:
            sql_insert = f"""
            INSERT /*+ NO_PARALLEL */ INTO {table_name} (
                SNAPSHOT_DATE, STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            ) VALUES (
                TO_DATE(:1, 'YYYY-MM-DD'), :2, :3, :4, :5, :6
            )
            """
        
        insert_data = []
        for c in candidates_to_save:
            llm_score = c.get('llm_score', 0)
            llm_reason = c.get('llm_reason', '')
            if len(llm_reason) > 3950:
                llm_reason = llm_reason[:3950] + "..."
                
            insert_data.append((
                snapshot_date,
                c['code'],
                c['name'],
                1 if c.get('is_tradable', True) else 0,
                llm_score,
                llm_reason
            ))
            
        cursor.executemany(sql_insert, insert_data)
        connection.commit()
        logger.info(f"   (DB) ✅ WatchList History 저장 완료")
        
    except Exception as e:
        logger.error(f"❌ DB: save_to_watchlist_history 실패! (에러: {e})")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()


def get_watchlist_history(connection, snapshot_date):
    """
    [v3.5] 특정 날짜의 WatchList 히스토리를 조회합니다.
    """
    watchlist = {}
    cursor = None
    is_mariadb = _is_mariadb()
    
    try:
        cursor = connection.cursor()
        
        if is_mariadb:
            sql = """
            SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            FROM WATCHLIST_HISTORY
            WHERE SNAPSHOT_DATE = %s
            """
            cursor.execute(sql, (snapshot_date,))
        else:
            sql = """
            SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            FROM WATCHLIST_HISTORY
            WHERE SNAPSHOT_DATE = TO_DATE(:1, 'YYYY-MM-DD')
            """
            cursor.execute(sql, [snapshot_date])
        
        for row in cursor:
            if isinstance(row, dict):
                watchlist[row['STOCK_CODE']] = {
                    "name": row['STOCK_NAME'], 
                    "is_tradable": bool(row['IS_TRADABLE']),
                    "llm_score": row['LLM_SCORE'] if row['LLM_SCORE'] is not None else 0,
                    "llm_reason": row['LLM_REASON'] if row['LLM_REASON'] is not None else ""
                }
            else:
                watchlist[row[0]] = {
                    "name": row[1], 
                    "is_tradable": bool(row[2]),
                    "llm_score": row[3] if row[3] is not None else 0,
                    "llm_reason": row[4] if row[4] is not None else ""
                }
        
        if watchlist:
            logger.info(f"✅ DB: {snapshot_date} WatchList History {len(watchlist)}개 로드 성공")
        else:
            logger.debug(f"ℹ️ DB: {snapshot_date} WatchList History 데이터 없음")
            
        return watchlist
    except Exception as e:
        logger.error(f"❌ DB: get_watchlist_history 실패! (에러: {e})")
        return {}
    finally:
        if cursor: cursor.close()
