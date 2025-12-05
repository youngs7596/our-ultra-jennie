#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# data_collector.py
# 백테스팅용 3년치(약 1100일) 일봉 OHLCV 데이터 수집기
#
# - 대상:
#   1) scout_job/scout_job.py 의 BLUE_CHIP_STOCKS 목록(거래불가 제외)
#   2) KOSPI 지수('0001') 반드시 포함
# - 소스:
#   youngs75_jennie.kis.KISClient.market_data.get_stock_daily_prices
# - 저장:
#   OCI DB 테이블 STOCK_DAILY_PRICES_3Y (DDL 포함)
#

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 프로젝트 루트 경로 추가
# 프로젝트 루트 경로 추가 (utilities의 상위 디렉토리)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import pandas as pd

import shared.auth as auth
import shared.database as database
from shared.kis.client import KISClient as KIS_API

# scout의 BLUE_CHIP_STOCKS 재활용 (bs4 의존성 제거를 위해 직접 정의)
BLUE_CHIP_STOCKS = [
    {"code": "0001", "name": "KOSPI", "is_tradable": False},
    {"code": "005930", "name": "삼성전자", "is_tradable": True},
    {"code": "373220", "name": "LG에너지솔루션", "is_tradable": True},
    {"code": "000660", "name": "SK하이닉스", "is_tradable": True},
    {"code": "207940", "name": "삼성바이오로직스", "is_tradable": True},
    {"code": "035420", "name": "NAVER", "is_tradable": True},
    {"code": "051910", "name": "LG화학", "is_tradable": True},
    {"code": "005380", "name": "현대차", "is_tradable": True},
    {"code": "006400", "name": "삼성SDI", "is_tradable": True},
    {"code": "035720", "name": "카카오", "is_tradable": True},
    {"code": "000270", "name": "기아", "is_tradable": True},
    {"code": "005490", "name": "POSCO홀딩스", "is_tradable": True},
    {"code": "105560", "name": "KB금융", "is_tradable": True},
    {"code": "096770", "name": "SK이노베이션", "is_tradable": True},
    {"code": "068270", "name": "셀트리온", "is_tradable": True},
    {"code": "028260", "name": "삼성물산", "is_tradable": True},
    {"code": "055550", "name": "신한지주", "is_tradable": True},
    {"code": "012330", "name": "현대모비스", "is_tradable": True},
    {"code": "323410", "name": "카카오뱅크", "is_tradable": True},
    {"code": "034730", "name": "SK", "is_tradable": True},
    {"code": "066570", "name": "LG전자", "is_tradable": True},
    {"code": "015760", "name": "한국전력", "is_tradable": True},
    {"code": "010950", "name": "S-Oil", "is_tradable": True},
    {"code": "086790", "name": "하나금융지주", "is_tradable": True},
    {"code": "259960", "name": "크래프톤", "is_tradable": True},
    {"code": "032830", "name": "삼성생명", "is_tradable": True},
    {"code": "003550", "name": "LG", "is_tradable": True},
    {"code": "017670", "name": "SK텔레콤", "is_tradable": True},
    {"code": "033780", "name": "KT&G", "is_tradable": True},
    {"code": "377300", "name": "카카오페이", "is_tradable": True},
    {"code": "009150", "name": "삼성전기", "is_tradable": True},
    {"code": "018260", "name": "삼성에스디에스", "is_tradable": True},
    {"code": "316140", "name": "우리금융지주", "is_tradable": True},
    {"code": "010130", "name": "고려아연", "is_tradable": True},
    {"code": "051900", "name": "LG생활건강", "is_tradable": True},
    {"code": "003670", "name": "포스코퓨처엠", "is_tradable": True},
    {"code": "036570", "name": "엔씨소프트", "is_tradable": True},
    {"code": "030200", "name": "KT", "is_tradable": True},
    {"code": "000810", "name": "삼성화재", "is_tradable": True},
    {"code": "302440", "name": "SK바이오사이언스", "is_tradable": True},
    {"code": "352820", "name": "하이브", "is_tradable": True},
    {"code": "090430", "name": "아모레퍼시픽", "is_tradable": True},
    {"code": "024110", "name": "기업은행", "is_tradable": True},
    {"code": "361610", "name": "SK아이이테크놀로지", "is_tradable": True},
    {"code": "086280", "name": "현대글로비스", "is_tradable": True},
    {"code": "011170", "name": "롯데케미칼", "is_tradable": True},
    {"code": "251270", "name": "넷마블", "is_tradable": True},
    {"code": "009540", "name": "한국조선해양", "is_tradable": True},
    {"code": "326030", "name": "SK바이오팜", "is_tradable": True},
    {"code": "034220", "name": "LG디스플레이", "is_tradable": True},
    {"code": "018880", "name": "한온시스템", "is_tradable": True}
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

# 수집 파라미터
NUM_DAYS_TO_FETCH = 1100  # 약 3년
KOSPI_CODE = "0001"

DDL_STOCK_DAILY_PRICES_3Y = """
CREATE TABLE STOCK_DAILY_PRICES_3Y (
  STOCK_CODE        VARCHAR2(16) NOT NULL,
  PRICE_DATE        DATE NOT NULL,
  OPEN_PRICE        NUMBER,
  HIGH_PRICE        NUMBER,
  LOW_PRICE         NUMBER,
  CLOSE_PRICE       NUMBER,
  VOLUME            NUMBER,
  CREATED_AT        TIMESTAMP DEFAULT SYSTIMESTAMP,
  CONSTRAINT PK_STOCK_DAILY_PRICES_3Y PRIMARY KEY (STOCK_CODE, PRICE_DATE)
)
"""

def ensure_table_exists(connection):
    """
    STOCK_DAILY_PRICES_3Y 테이블이 없으면 생성합니다.
    """
    try:
        cur = connection.cursor()
        # 세션 병렬 DML 비활성화 (ORA-12838 회피)
        try:
            cur.execute("ALTER SESSION DISABLE PARALLEL DML")
        except Exception:
            pass
        # 존재 여부 확인
        cur.execute("""
            SELECT COUNT(*) 
            FROM user_tables 
            WHERE table_name = 'STOCK_DAILY_PRICES_3Y'
        """)
        exists = cur.fetchone()[0] > 0
        if not exists:
            logger.info("테이블 'STOCK_DAILY_PRICES_3Y' 미존재. 생성 시도...")
            cur.execute(DDL_STOCK_DAILY_PRICES_3Y)
            # 테이블 병렬 옵션 해제
            try:
                cur.execute("ALTER TABLE STOCK_DAILY_PRICES_3Y NOPARALLEL")
            except Exception:
                pass
            connection.commit()
            logger.info("✅ 'STOCK_DAILY_PRICES_3Y' 생성 완료.")
        else:
            logger.info("✅ 'STOCK_DAILY_PRICES_3Y' 이미 존재.")
    except Exception as e:
        logger.error(f"❌ 테이블 생성 확인 중 오류: {e}", exc_info=True)
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass

def upsert_daily_prices(connection, rows):
    """
    수집된 일봉 데이터를 STOCK_DAILY_PRICES_3Y에 MERGE 저장합니다.
    rows: List[dict] with keys: date, code, open, high, low, close, volume
    """
    if not rows:
        return
    sql_merge = """
    MERGE /*+ NOPARALLEL(t) */ INTO STOCK_DAILY_PRICES_3Y t
    USING (
      SELECT TO_DATE(:p_date, 'YYYY-MM-DD') AS price_date,
             :p_code AS stock_code,
             :p_open AS open_price,
             :p_high AS high_price,
             :p_low  AS low_price,
             :p_close AS close_price,
             :p_volume AS volume
      FROM DUAL
    ) s
    ON (t.STOCK_CODE = s.stock_code AND t.PRICE_DATE = s.price_date)
    WHEN MATCHED THEN
      UPDATE /*+ NOPARALLEL(t) */ SET t.OPEN_PRICE = s.open_price,
                 t.HIGH_PRICE = s.high_price,
                 t.LOW_PRICE = s.low_price,
                 t.CLOSE_PRICE = s.close_price,
                 t.VOLUME = s.volume
    WHEN NOT MATCHED THEN
      INSERT /*+ NOPARALLEL(t) */ (STOCK_CODE, PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME)
      VALUES (s.stock_code, s.price_date, s.open_price, s.high_price, s.low_price, s.close_price, s.volume)
    """
    params = []
    for r in rows:
        params.append({
            "p_date": r["date"],
            "p_code": r["code"],
            "p_open": r.get("open"),
            "p_high": r.get("high"),
            "p_low": r.get("low"),
            "p_close": r.get("close"),
            "p_volume": r.get("volume", 0)
        })
    cur = None
    try:
        cur = connection.cursor()
        # 세션 병렬 DML 비활성화
        try:
            cur.execute("ALTER SESSION DISABLE PARALLEL DML")
        except Exception:
            pass
        cur.executemany(sql_merge, params)
        connection.commit()
        logger.info(f"✅ 저장 완료: {len(params)}건")
    except Exception as e:
        logger.error(f"❌ 저장 실패: {e}", exc_info=True)
        if connection:
            connection.rollback()
        raise
    finally:
        if cur:
            cur.close()

def _fetch_ohlcv_range(kis_api: KIS_API, stock_code: str, start_date_yyyymmdd: str, end_date_yyyymmdd: str):
    """
    KIS '일봉' API를 직접 호출하여 지정 구간의 데이터를 수집합니다. (주식용)
    """
    # [수정] 30일 이상 조회를 위해 '기간별 시세' API(inquire-daily-itemchartprice) 사용
    url = f"{kis_api.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": start_date_yyyymmdd,
        "FID_INPUT_DATE_2": end_date_yyyymmdd,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"  # [수정] 0:수정주가, 1:원주가. 수정주가를 사용해야 함.
    }
    # [수정] '기간별 시세' API의 TR_ID
    tr_id = "FHKST03010100"

    # KIS Gateway를 사용하지 않을 경우에만 딜레이 적용
    if hasattr(kis_api, 'API_CALL_DELAY'):
        time.sleep(kis_api.API_CALL_DELAY)

    res = kis_api.request("GET", url, headers=kis_api.headers, params=params, tr_id=tr_id)
    if not res:
        return []

    # [수정] '기간별 시세' API의 응답 필드는 'output2'
    output_list = res.get("output2")
    if not output_list:
        return []

    rows = []
    for day in output_list:
        try:
            date_key = "stck_bsop_date"
            price_key = "stck_clpr"
            open_key = "stck_oprc"
            high_key = "stck_hgpr"
            low_key = "stck_lwpr"
            dt = datetime.strptime(day[date_key], "%Y%m%d").strftime("%Y-%m-%d")
            rows.append({
                "date": dt,
                "code": stock_code,
                "open": float(day[open_key]),
                "high": float(day[high_key]), # 'stck_hgpr' 필드 사용
                "low": float(day[low_key]),
                "close": float(day[price_key]),
                "volume": int(day.get("acml_vol", 0))  # 거래량 필드 'acml_vol'로 수정
            })
        except Exception:
            continue
    return rows

def _fetch_index_ohlcv_full(kis_api: KIS_API, index_code: str, total_days: int):
    """
    지수(4자리 코드) 일봉을 충분한 기간(예: 3년)까지 거슬러 올라가며 페이징 수집합니다.
    - 일부 KIS 지수 API는 요청 범위와 무관하게 최근 N개만 반환하므로,
      가장 오래된 수신일을 기준으로 end_date를 계속 과거로 이동시키는 방식으로 누적합니다.
    """
    assert len(index_code) == 4
    url = f"{kis_api.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
    tr_id = "FHKUP03500100"

    end_dt = datetime.now() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=365)  # 초기 윈도우 1년

    all_rows = {}
    loops = 0
    max_loops = 200  # 안전장치
    while loops < max_loops:
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code,
            "FID_INPUT_DATE_1": start_dt.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end_dt.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D"
        }
        time.sleep(kis_api.API_CALL_DELAY)
        res = kis_api.request("GET", url, params=params, tr_id=tr_id)
        output_list = res.get("output2") if res else None
        if not output_list:
            break

        min_date = None
        new_count = 0
        for day in output_list:
            try:
                dt_raw = day["stck_bsop_date"]
                dt = datetime.strptime(dt_raw, "%Y%m%d")
                dt_str = dt.strftime("%Y-%m-%d")
                price = float(day["bstp_nmix_prpr"])
                high = float(day["bstp_nmix_hgpr"])
                low = float(day["bstp_nmix_lwpr"])
                key = (index_code, dt_str)
                if key not in all_rows:
                    all_rows[key] = {
                        "date": dt_str,
                        "code": index_code,
                        "open": None,
                        "high": high,
                        "low": low,
                        "close": price,
                        "volume": 0
                    }
                    new_count += 1
                if min_date is None or dt < min_date:
                    min_date = dt
            except Exception:
                continue

        # 종료 조건: 충분히 모았거나 더 이상 과거 데이터 없음
        if len(all_rows) >= total_days or not min_date:
            break
        # 다음 루프를 위해 end_date를 더 과거로 이동
        end_dt = min_date - timedelta(days=1)
        start_dt = end_dt - timedelta(days=365)
        loops += 1

    rows_sorted = sorted(all_rows.values(), key=lambda x: x["date"])
    return rows_sorted

def fetch_ohlcv_for_code(kis_api: KIS_API, stock_code: str):
    """
    KIS API를 통해 특정 종목/지수의 3년치 일봉 OHLCV를 여러 구간으로 나눠 수집합니다.
    """
    # 지수는 별도 페이징 로직 사용
    if len(stock_code) == 4:
        return _fetch_index_ohlcv_full(kis_api, stock_code, NUM_DAYS_TO_FETCH)

    end_dt = datetime.now() - timedelta(days=1)  # 전일까지
    start_dt = end_dt - timedelta(days=NUM_DAYS_TO_FETCH)
    chunk_days = 180  # 한번 호출할 구간 (API 응답 제한 회피)

    all_rows = {}
    cur_start = start_dt
    while cur_start <= end_dt:
        cur_end = min(cur_start + timedelta(days=chunk_days), end_dt)
        s = cur_start.strftime("%Y%m%d")
        e = cur_end.strftime("%Y%m%d")
        part_rows = _fetch_ohlcv_range(kis_api, stock_code, s, e)
        for r in part_rows:
            all_rows[(r["code"], r["date"])] = r
        cur_start = cur_end + timedelta(days=1)

    # 날짜 기준 정렬하여 반환
    rows_sorted = sorted(all_rows.values(), key=lambda x: x["date"])
    return rows_sorted

def main():
    # .env 파일 명시적 로드
    # 스크립트가 어디서 실행되든 프로젝트 루트의 .env 파일을 찾도록 경로 수정
    project_root_for_env = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root_for_env, '.env')
    load_dotenv(env_path)
    
    if not os.getenv("GCP_PROJECT_ID"):
        logger.error("❌ .env 파일 로드 실패: GCP_PROJECT_ID가 설정되지 않았습니다.")
        return
    logger.info("--- 🤖 3년치 데이터 수집기 시작 ---")
    db_conn = None
    kis_api = None
    try:
        # DB 연결
        db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), os.getenv("GCP_PROJECT_ID"))
        db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), os.getenv("GCP_PROJECT_ID"))
        wallet_path = os.path.join(PROJECT_ROOT, os.getenv("OCI_WALLET_DIR_NAME", "wallet"))
        db_conn = database.get_db_connection(
            db_user=db_user,
            db_password=db_password,
            db_service_name=os.getenv("OCI_DB_SERVICE_NAME"),
            wallet_path=wallet_path
        )
        if not db_conn:
            raise RuntimeError("OCI DB 연결 실패")
        ensure_table_exists(db_conn)

        # KIS API
        # 로컬 개발 시에는 .env 파일의 TRADING_MODE (MOCK)를 따르도록 수정
        trading_mode = os.getenv("TRADING_MODE", "REAL")
        kis_api = KIS_API(
            app_key=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY"), os.getenv("GCP_PROJECT_ID")),
            app_secret=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET"), os.getenv("GCP_PROJECT_ID")),
            base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
            account_prefix=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX"), os.getenv("GCP_PROJECT_ID")),
            account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
            trading_mode=trading_mode
        )
        if not kis_api.authenticate():
            raise RuntimeError("KIS API 인증 실패")

        # 1) KOSPI 포함하여 수집
        logger.info(f"--- (1/2) KOSPI({KOSPI_CODE}) 3년치 수집 ---")
        kospi_rows = fetch_ohlcv_for_code(kis_api, KOSPI_CODE)
        upsert_daily_prices(db_conn, kospi_rows)

        # 2) BLUE_CHIP_STOCKS 병렬 수집
        logger.info("--- (2/2) BLUE_CHIP_STOCKS 3년치 수집 ---")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        stocks_to_fetch = [s for s in BLUE_CHIP_STOCKS if s.get("is_tradable", True)]
        all_rows_to_save = []
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_stock = {executor.submit(fetch_ohlcv_for_code, kis_api, s['code']): s for s in stocks_to_fetch}
            
            for i, future in enumerate(as_completed(future_to_stock)):
                stock = future_to_stock[future]
                try:
                    rows = future.result()
                    if rows:
                        all_rows_to_save.extend(rows)
                        logger.info(f"   ({i+1}/{len(stocks_to_fetch)}) ✅ 수집 완료: {stock['name']} ({len(rows)}건)")
                    else:
                        logger.warning(f"   ({i+1}/{len(stocks_to_fetch)}) ⚠️ 데이터 없음: {stock['name']}")
                except Exception as exc:
                    logger.error(f"   ({i+1}/{len(stocks_to_fetch)}) ❌ 수집 실패: {stock['name']} - {exc}")

        if all_rows_to_save:
            logger.info(f"--- 모든 수집 데이터({len(all_rows_to_save)}건) Bulk 저장 시작 ---")
            upsert_daily_prices(db_conn, all_rows_to_save)
            logger.info(f"--- ✅ 모든 데이터 Bulk 저장 완료 ---")
        else:
            logger.warning("⚠️ 저장할 데이터가 없습니다.")

        logger.info("--- ✅ 3년치 데이터 수집 완료 ---")

    except Exception as e:
        logger.critical(f"❌ 수집기 실행 중 치명적 오류: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if db_conn:
            db_conn.close()
            logger.info("--- DB 연결 종료 ---")

if __name__ == "__main__":
    main()
