#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# update_stock_master.py
# KOSPI/KOSDAQ 전체 종목 마스터 정보 수집기
#
# - 대상: KOSPI, KOSDAQ 전체 종목
# - 소스: KIS API (전체 종목 다운로드)
# - 저장: DB 테이블 STOCK_MASTER (MariaDB/Oracle 지원)
#
# [v2.0] MariaDB 지원 추가 (Claude Opus 4.5)

import os
import sys
import time
import logging
import requests
import zipfile
import io
import pandas as pd
from dotenv import load_dotenv
import urllib.request
import ssl

# 프로젝트 루트 경로 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
import shared.database as database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

def _is_mariadb() -> bool:
    """현재 DB 타입이 MariaDB인지 확인"""
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"

# Oracle용 DDL
DDL_STOCK_MASTER = """
CREATE TABLE STOCK_MASTER (
  STOCK_CODE        VARCHAR2(16) NOT NULL,
  STOCK_NAME        VARCHAR2(128),
  STD_CODE          VARCHAR2(16),
  SECURITY_GROUP    VARCHAR2(4),
  MARKET_CAP_SCALE  VARCHAR2(4),
  INDUSTRY_CODE     VARCHAR2(8),
  SECTOR_KOSPI200   VARCHAR2(64),
  IS_MANUFACTURING  CHAR(1),
  IS_LOW_LIQUIDITY  CHAR(1),
  IS_GOVERNANCE_IDX CHAR(1),
  IS_KOSPI100       CHAR(1),
  IS_KOSPI50        CHAR(1),
  IS_KRX100         CHAR(1),
  IS_KRX_AUTO       CHAR(1),
  IS_KRX_SEMI       CHAR(1),
  IS_KRX_BIO        CHAR(1),
  IS_KRX_BANK       CHAR(1),
  IS_SPAC           CHAR(1),
  IS_SHORT_TERM_OVERHEAT CHAR(1),
  IS_TRADING_HALT   CHAR(1),
  IS_ADMINISTRATIVE CHAR(1),
  MARKET_WARNING    VARCHAR2(4),
  BASE_PRICE        NUMBER,
  FACE_VALUE        NUMBER,
  LISTING_DATE      DATE,
  LISTED_SHARES     NUMBER,
  CAPITAL           NUMBER,
  SETTLEMENT_MONTH  VARCHAR2(4),
  MARKET_CAP        NUMBER,
  ROE               NUMBER(10,2),
  IS_KOSPI          NUMBER(1) DEFAULT 0,
  IS_KOSDAQ         NUMBER(1) DEFAULT 0,
  IS_ETF            NUMBER(1) DEFAULT 0,
  IS_ETN            NUMBER(1) DEFAULT 0,
  LAST_UPDATED      TIMESTAMP DEFAULT SYSTIMESTAMP,
  CONSTRAINT PK_STOCK_MASTER PRIMARY KEY (STOCK_CODE)
)
"""

# MariaDB용 DDL
DDL_STOCK_MASTER_MARIADB = """
CREATE TABLE IF NOT EXISTS STOCK_MASTER (
  STOCK_CODE        VARCHAR(20) NOT NULL,
  STOCK_NAME        VARCHAR(128),
  STD_CODE          VARCHAR(20),
  SECURITY_GROUP    VARCHAR(10),
  MARKET_CAP_SCALE  VARCHAR(10),
  INDUSTRY_CODE     VARCHAR(10),
  SECTOR_KOSPI200   VARCHAR(64),
  IS_MANUFACTURING  CHAR(1),
  IS_LOW_LIQUIDITY  CHAR(1),
  IS_GOVERNANCE_IDX CHAR(1),
  IS_KOSPI100       CHAR(1),
  IS_KOSPI50        CHAR(1),
  IS_KRX100         CHAR(1),
  IS_KRX_AUTO       CHAR(1),
  IS_KRX_SEMI       CHAR(1),
  IS_KRX_BIO        CHAR(1),
  IS_KRX_BANK       CHAR(1),
  IS_SPAC           CHAR(1),
  IS_SHORT_TERM_OVERHEAT CHAR(1),
  IS_TRADING_HALT   CHAR(1),
  IS_ADMINISTRATIVE CHAR(1),
  MARKET_WARNING    VARCHAR(10),
  BASE_PRICE        DECIMAL(15,2),
  FACE_VALUE        DECIMAL(15,2),
  LISTING_DATE      DATE,
  LISTED_SHARES     BIGINT,
  CAPITAL           BIGINT,
  SETTLEMENT_MONTH  VARCHAR(4),
  MARKET_CAP        BIGINT,
  ROE               DECIMAL(10,2),
  PER               DECIMAL(10,2),
  PBR               DECIMAL(10,2),
  IS_KOSPI          TINYINT DEFAULT 0,
  IS_KOSDAQ         TINYINT DEFAULT 0,
  IS_ETF            TINYINT DEFAULT 0,
  IS_ETN            TINYINT DEFAULT 0,
  LAST_UPDATED      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (STOCK_CODE)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

def ensure_table_exists(connection):
    """STOCK_MASTER 테이블이 없으면 생성합니다."""
    try:
        cur = connection.cursor()
        
        if _is_mariadb():
            # MariaDB: SHOW TABLES로 확인
            cur.execute("SHOW TABLES LIKE 'stock_master'")
            exists = cur.fetchone() is not None
        else:
            # Oracle: user_tables에서 확인
            cur.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'STOCK_MASTER'")
            row = cur.fetchone()
            exists = (list(row.values())[0] if isinstance(row, dict) else row[0]) > 0
        
        if not exists:
            logger.info("테이블 'STOCK_MASTER' 미존재. 생성 시도...")
            if _is_mariadb():
                cur.execute(DDL_STOCK_MASTER_MARIADB)
            else:
                cur.execute(DDL_STOCK_MASTER)
            connection.commit()
            logger.info("✅ 'STOCK_MASTER' 생성 완료.")
        else:
            logger.info("✅ 'STOCK_MASTER' 이미 존재.")
        
        cur.close()
    except Exception as e:
        logger.error(f"❌ 테이블 생성 확인 중 오류: {e}", exc_info=True)
        raise

base_dir = os.getcwd()

def kospi_master_download(base_dir):
    cwd = os.getcwd()
    logger.debug(f"current directory is {cwd}")
    ssl._create_default_https_context = ssl._create_unverified_context

    urllib.request.urlretrieve("https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
                               base_dir + "/kospi_code.zip")

    os.chdir(base_dir)
    logger.debug(f"change directory to {base_dir}")
    kospi_zip = zipfile.ZipFile('kospi_code.zip')
    kospi_zip.extractall()

    kospi_zip.close()

    if os.path.exists("kospi_code.zip"):
        os.remove("kospi_code.zip")    


def get_kospi_master_dataframe(base_dir):
    file_name = base_dir + "/kospi_code.mst"
    tmp_fil1 = base_dir + "/kospi_code_part1.tmp"
    tmp_fil2 = base_dir + "/kospi_code_part2.tmp"

    logger.info(f"file name: {file_name}")

    wf1 = open(tmp_fil1, mode="w", encoding="utf-8")
    wf2 = open(tmp_fil2, mode="w", encoding="utf-8")

    with open(file_name, mode="rb") as f:
        for row in f:
            row_decoded = row.decode('cp949', errors='replace')
            rf1 = row_decoded[0:len(row_decoded) - 228]
            rf1_1 = rf1[0:9].rstrip()
            rf1_2 = rf1[9:21].rstrip()
            rf1_3 = rf1[21:].strip()
            wf1.write(rf1_1 + ',' + rf1_2 + ',' + rf1_3 + '\n')
            rf2 = row_decoded[-228:]
            wf2.write(rf2)

    wf1.close()
    wf2.close()

    part1_columns = ['단축코드', '표준코드', '한글명']
    df1 = pd.read_csv(tmp_fil1, header=None, names=part1_columns, encoding='utf-8')

    field_specs = [2, 1, 4, 4, 4,
                   1, 1, 1, 1, 1,
                   1, 1, 1, 1, 1,
                   1, 1, 1, 1, 1,
                   1, 1, 1, 1, 1,
                   1, 1, 1, 1, 1,
                   1, 9, 5, 5, 1,
                   1, 1, 2, 1, 1,
                   1, 2, 2, 2, 3,
                   1, 3, 12, 12, 8,
                   15, 21, 2, 7, 1,
                   1, 1, 1, 1, 9,
                   9, 9, 5, 9, 8,
                   9, 3, 1, 1, 1
                   ]

    part2_columns = ['그룹코드', '시가총액규모', '지수업종대분류', '지수업종중분류', '지수업종소분류',
                     '제조업', '저유동성', '지배구조지수종목', 'KOSPI200섹터업종', 'KOSPI100',
                     'KOSPI50', 'KRX', 'ETP', 'ELW발행', 'KRX100',
                     'KRX자동차', 'KRX반도체', 'KRX바이오', 'KRX은행', 'SPAC',
                     'KRX에너지화학', 'KRX철강', '단기과열', 'KRX미디어통신', 'KRX건설',
                     'Non1', 'KRX증권', 'KRX선박', 'KRX섹터_보험', 'KRX섹터_운송',
                     'SRI', '기준가', '매매수량단위', '시간외수량단위', '거래정지',
                     '정리매매', '관리종목', '시장경고', '경고예고', '불성실공시',
                     '우회상장', '락구분', '액면변경', '증자구분', '증거금비율',
                     '신용가능', '신용기간', '전일거래량', '액면가', '상장일자',
                     '상장주수', '자본금', '결산월', '공모가', '우선주',
                     '공매도과열', '이상급등', 'KRX300', 'KOSPI', '매출액',
                     '영업이익', '경상이익', '당기순이익', 'ROE', '기준년월',
                     '시가총액', '그룹사코드', '회사신용한도초과', '담보대출가능', '대주가능'
                     ]

    df2 = pd.read_fwf(tmp_fil2, widths=field_specs, names=part2_columns)

    df = pd.merge(df1, df2, how='outer', left_index=True, right_index=True)

    # clean temporary file and dataframe
    del (df1)
    del (df2)
    os.remove(tmp_fil1)
    os.remove(tmp_fil2)
    
    print("Done")

    return df

def get_sector_master_dataframe(base_dir):

    ssl._create_default_https_context = ssl._create_unverified_context
    urllib.request.urlretrieve("https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip", base_dir + "/idxcode.zip")
    os.chdir(base_dir)

    idxcode_zip = zipfile.ZipFile('idxcode.zip')
    idxcode_zip.extractall()
    idxcode_zip.close()

    file_name = base_dir + "/idxcode.mst"    
    sector_map = {}

    with open(file_name, mode="rb") as f:
        for row in f:
            row_decoded = row.decode('cp949', errors='replace')
            code = row_decoded[1:5]  # 업종코드 4자리 (맨 앞 1자리 제거)
            name = row_decoded[3:43].rstrip() #업종명
            sector_map[code] = name

    logger.info(f"Sector map loaded: {len(sector_map)} entries")

    kospi200_sector_map = {
        "0": "미분류", "1": "건설기계", "2": "조선운송", "3": "철강소재",
        "4": "에너지화학", "5": "정보통신", "6": "금융", "7": "필수소비재",
        "8": "자유소비재"
    }
    

    return sector_map, kospi200_sector_map

def parse_mst_file() -> list:

    kospi_master_download(base_dir)
    df_kospi = get_kospi_master_dataframe(base_dir)
    sector_map, kospi200_sector_map  = get_sector_master_dataframe(base_dir)

    # DB 저장을 위한 데이터 형태로 변환
    stocks = []
    for _, row in df_kospi.iterrows():
        security_group = str(row.get('그룹코드', '')).strip()
        # 보통주(ST)만 필터링
        if security_group != 'ST':
            continue

        listing_date_str = str(row.get('상장일자', ''))
        listing_date = f"{listing_date_str[:4]}-{listing_date_str[4:6]}-{listing_date_str[6:]}" if len(listing_date_str) == 8 else None

        sector_code = str(row.get('KOSPI200섹터업종', '')).strip()
        sector_name = kospi200_sector_map.get(sector_code, 'etc')

        stocks.append({
            'stock_code': row['단축코드'][:], 
            'stock_name': row['한글명'],
            'std_code': str(row['표준코드']),
            'security_group': security_group,
            'market_cap_scale': str(row['시가총액규모']).strip(),
            'industry_code': str(row['지수업종대분류']).strip(),
            'sector_kospi200': sector_name,
            'is_manufacturing': str(row['제조업']).strip()[:1],
            'is_low_liquidity': str(row['저유동성']).strip()[:1],
            'is_governance_idx': str(row['지배구조지수종목']).strip()[:1],
            'is_kospi100': str(row['KOSPI100']).strip()[:1],
            'is_kospi50': str(row['KOSPI50']).strip()[:1],
            'is_krx100': str(row['KRX100']).strip()[:1],
            'is_krx_auto': str(row['KRX자동차']).strip()[:1],
            'is_krx_semi': str(row['KRX반도체']).strip()[:1],
            'is_krx_bio': str(row['KRX바이오']).strip()[:1],
            'is_krx_bank': str(row['KRX은행']).strip()[:1],
            'is_spac': str(row['SPAC']).strip()[:1],
            'is_short_term_overheat': str(row['단기과열']).strip()[:1],
            'is_trading_halt': str(row['거래정지']).strip()[:1],
            'is_administrative': str(row['관리종목']).strip()[:1],
            'market_warning': str(row['시장경고']).strip()[:1],
            'base_price': pd.to_numeric(row.get('기준가'), errors='coerce'),
            'face_value': pd.to_numeric(row.get('액면가'), errors='coerce'),
            'listing_date': listing_date,
            'listed_shares': pd.to_numeric(row.get('상장주수'), errors='coerce'),
            'capital': pd.to_numeric(row.get('자본금'), errors='coerce'),
            'settlement_month': str(row.get('결산월', '')),
            'market_cap': pd.to_numeric(row.get('시가총액'), errors='coerce'),
            'is_kospi': 1,
            'is_kosdaq': 0,
            'is_etf': 1 if str(row.get('그룹코드', '')).strip() == 'ET' else 0,
            'is_etn': 1 if str(row.get('그룹코드', '')).strip() == 'EN' else 0,
        })
    return stocks


def upsert_stock_master(connection, stocks):
    """수집된 종목 정보를 STOCK_MASTER에 UPSERT 저장합니다."""
    if not stocks:
        return

    is_mariadb = os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"
    
    if is_mariadb:
        # MariaDB: INSERT ... ON DUPLICATE KEY UPDATE
        sql_upsert = """
        INSERT INTO STOCK_MASTER (
            STOCK_CODE, STOCK_NAME, STD_CODE, SECURITY_GROUP, MARKET_CAP_SCALE, 
            INDUSTRY_CODE, SECTOR_KOSPI200, IS_MANUFACTURING, IS_LOW_LIQUIDITY, 
            IS_GOVERNANCE_IDX, IS_KOSPI100, IS_KOSPI50, IS_KRX100, IS_KRX_AUTO, 
            IS_KRX_SEMI, IS_KRX_BIO, IS_KRX_BANK, IS_SPAC, IS_SHORT_TERM_OVERHEAT,
            IS_TRADING_HALT, IS_ADMINISTRATIVE, MARKET_WARNING, BASE_PRICE, 
            FACE_VALUE, LISTING_DATE, LISTED_SHARES, CAPITAL, SETTLEMENT_MONTH, 
            MARKET_CAP, IS_KOSPI, IS_KOSDAQ, IS_ETF, IS_ETN
        ) VALUES (
            %(stock_code)s, %(stock_name)s, %(std_code)s, %(security_group)s, 
            %(market_cap_scale)s, %(industry_code)s, %(sector_kospi200)s, 
            %(is_manufacturing)s, %(is_low_liquidity)s, %(is_governance_idx)s, 
            %(is_kospi100)s, %(is_kospi50)s, %(is_krx100)s, %(is_krx_auto)s, 
            %(is_krx_semi)s, %(is_krx_bio)s, %(is_krx_bank)s, %(is_spac)s, 
            %(is_short_term_overheat)s, %(is_trading_halt)s, %(is_administrative)s, 
            %(market_warning)s, %(base_price)s, %(face_value)s, %(listing_date)s, 
            %(listed_shares)s, %(capital)s, %(settlement_month)s, %(market_cap)s, 
            %(is_kospi)s, %(is_kosdaq)s, %(is_etf)s, %(is_etn)s
        ) ON DUPLICATE KEY UPDATE
            STOCK_NAME = VALUES(STOCK_NAME),
            STD_CODE = VALUES(STD_CODE),
            SECURITY_GROUP = VALUES(SECURITY_GROUP),
            MARKET_CAP_SCALE = VALUES(MARKET_CAP_SCALE),
            INDUSTRY_CODE = VALUES(INDUSTRY_CODE),
            SECTOR_KOSPI200 = VALUES(SECTOR_KOSPI200),
            IS_MANUFACTURING = VALUES(IS_MANUFACTURING),
            IS_LOW_LIQUIDITY = VALUES(IS_LOW_LIQUIDITY),
            IS_GOVERNANCE_IDX = VALUES(IS_GOVERNANCE_IDX),
            IS_KOSPI100 = VALUES(IS_KOSPI100),
            IS_KOSPI50 = VALUES(IS_KOSPI50),
            IS_KRX100 = VALUES(IS_KRX100),
            IS_KRX_AUTO = VALUES(IS_KRX_AUTO),
            IS_KRX_SEMI = VALUES(IS_KRX_SEMI),
            IS_KRX_BIO = VALUES(IS_KRX_BIO),
            IS_KRX_BANK = VALUES(IS_KRX_BANK),
            IS_SPAC = VALUES(IS_SPAC),
            IS_SHORT_TERM_OVERHEAT = VALUES(IS_SHORT_TERM_OVERHEAT),
            IS_TRADING_HALT = VALUES(IS_TRADING_HALT),
            IS_ADMINISTRATIVE = VALUES(IS_ADMINISTRATIVE),
            MARKET_WARNING = VALUES(MARKET_WARNING),
            BASE_PRICE = VALUES(BASE_PRICE),
            FACE_VALUE = VALUES(FACE_VALUE),
            LISTING_DATE = VALUES(LISTING_DATE),
            LISTED_SHARES = VALUES(LISTED_SHARES),
            CAPITAL = VALUES(CAPITAL),
            SETTLEMENT_MONTH = VALUES(SETTLEMENT_MONTH),
            MARKET_CAP = VALUES(MARKET_CAP),
            IS_KOSPI = VALUES(IS_KOSPI),
            IS_KOSDAQ = VALUES(IS_KOSDAQ),
            IS_ETF = VALUES(IS_ETF),
            IS_ETN = VALUES(IS_ETN),
            LAST_UPDATED = CURRENT_TIMESTAMP
        """
        
        try:
            with connection.cursor() as cur:
                cur.executemany(sql_upsert, stocks)
                connection.commit()
                logger.info(f"✅ 저장 완료: {len(stocks)}건")
        except Exception as e:
            logger.error(f"❌ 저장 실패: {e}", exc_info=True)
            connection.rollback()
            raise
    else:
        # Oracle: MERGE INTO
        sql_merge = """
        MERGE INTO STOCK_MASTER t
        USING (
            SELECT :stock_code AS stock_code, :stock_name AS stock_name, :std_code AS std_code,
                   :security_group AS security_group, :market_cap_scale AS market_cap_scale,
                   :industry_code AS industry_code, :sector_kospi200 AS sector_kospi200,
                   :is_manufacturing AS is_manufacturing, :is_low_liquidity AS is_low_liquidity,
                   :is_governance_idx AS is_governance_idx, :is_kospi100 AS is_kospi100,
                   :is_kospi50 AS is_kospi50, :is_krx100 AS is_krx100, :is_krx_auto AS is_krx_auto,
                   :is_krx_semi AS is_krx_semi, :is_krx_bio AS is_krx_bio, :is_krx_bank AS is_krx_bank,
                   :is_spac AS is_spac, :is_short_term_overheat AS is_short_term_overheat,
                   :is_trading_halt AS is_trading_halt, :is_administrative AS is_administrative,
                   :market_warning AS market_warning, :base_price AS base_price, :face_value AS face_value,
                   TO_DATE(:listing_date, 'YYYY-MM-DD') AS listing_date, :listed_shares AS listed_shares,
                   :capital AS capital, :settlement_month AS settlement_month, :market_cap AS market_cap, :is_kospi AS is_kospi, 
                   :is_kosdaq AS is_kosdaq, :is_etf AS is_etf, :is_etn AS is_etn
            FROM DUAL
        ) s
        ON (t.STOCK_CODE = s.stock_code)
        WHEN MATCHED THEN
            UPDATE SET
                t.STOCK_NAME = s.stock_name, t.STD_CODE = s.std_code, t.SECURITY_GROUP = s.security_group,
                t.MARKET_CAP_SCALE = s.market_cap_scale, t.INDUSTRY_CODE = s.industry_code,
                t.SECTOR_KOSPI200 = s.sector_kospi200, t.IS_MANUFACTURING = s.is_manufacturing,
                t.IS_LOW_LIQUIDITY = s.is_low_liquidity, t.IS_GOVERNANCE_IDX = s.is_governance_idx,
                t.IS_KOSPI100 = s.is_kospi100, t.IS_KOSPI50 = s.is_kospi50, t.IS_KRX100 = s.is_krx100,
                t.IS_KRX_AUTO = s.is_krx_auto, t.IS_KRX_SEMI = s.is_krx_semi, t.IS_KRX_BIO = s.is_krx_bio,
                t.IS_KRX_BANK = s.is_krx_bank, t.IS_SPAC = s.is_spac,
                t.IS_SHORT_TERM_OVERHEAT = s.is_short_term_overheat, t.IS_TRADING_HALT = s.is_trading_halt,
                t.IS_ADMINISTRATIVE = s.is_administrative, t.MARKET_WARNING = s.market_warning,
                t.BASE_PRICE = s.base_price, t.FACE_VALUE = s.face_value, t.LISTING_DATE = s.listing_date,
                t.LISTED_SHARES = s.listed_shares, t.CAPITAL = s.capital,
                t.SETTLEMENT_MONTH = s.settlement_month, t.MARKET_CAP = s.market_cap, t.IS_KOSPI = s.is_kospi, 
                t.IS_KOSDAQ = s.is_kosdaq, t.IS_ETF = s.is_etf, t.IS_ETN = s.is_etn, 
                t.LAST_UPDATED = SYSTIMESTAMP
        WHEN NOT MATCHED THEN
            INSERT (
                STOCK_CODE, STOCK_NAME, STD_CODE, SECURITY_GROUP, MARKET_CAP_SCALE, INDUSTRY_CODE, SECTOR_KOSPI200,
                IS_MANUFACTURING, IS_LOW_LIQUIDITY, IS_GOVERNANCE_IDX, IS_KOSPI100, IS_KOSPI50, IS_KRX100,
                IS_KRX_AUTO, IS_KRX_SEMI, IS_KRX_BIO, IS_KRX_BANK, IS_SPAC, IS_SHORT_TERM_OVERHEAT,
                IS_TRADING_HALT, IS_ADMINISTRATIVE, MARKET_WARNING, BASE_PRICE, FACE_VALUE, LISTING_DATE, LISTED_SHARES, 
                CAPITAL, SETTLEMENT_MONTH, MARKET_CAP, IS_KOSPI, IS_KOSDAQ, IS_ETF, IS_ETN
            ) VALUES (
                s.stock_code, s.stock_name, s.std_code, s.security_group, s.market_cap_scale, s.industry_code, s.sector_kospi200,
                s.is_manufacturing, s.is_low_liquidity, s.is_governance_idx, s.is_kospi100, s.is_kospi50, s.is_krx100,
                s.is_krx_auto, s.is_krx_semi, s.is_krx_bio, s.is_krx_bank, s.is_spac, s.is_short_term_overheat,
                s.is_trading_halt, s.is_administrative, s.market_warning, s.base_price, s.face_value, s.listing_date, s.listed_shares, 
                s.capital, s.settlement_month, s.market_cap, s.is_kospi, s.is_kosdaq, s.is_etf, s.is_etn
            )
        """
        try:
            with connection.cursor() as cur:
                # ORA-12838 오류 방지를 위해 세션의 병렬 DML 비활성화
                try:
                    cur.execute("ALTER SESSION DISABLE PARALLEL DML")
                except Exception as e:
                    logger.warning(f"병렬 DML 비활성화 실패 (무시): {e}")
                cur.executemany(sql_merge, stocks)
                connection.commit()
                logger.info(f"✅ 저장 완료: {len(stocks)}건")
        except Exception as e:
            logger.error(f"❌ 저장 실패: {e}", exc_info=True)
            connection.rollback()
            raise

def _is_mariadb() -> bool:
    """현재 DB 타입이 MariaDB인지 확인"""
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"


def main():
    project_root_for_env = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root_for_env, '.env')
    load_dotenv(env_path)
    
    # secrets.json 경로 설정
    os.environ.setdefault("SECRETS_FILE", os.path.join(project_root_for_env, "secrets.json"))
    
    logger.info("--- 🤖 종목 마스터 업데이트 시작 ---")
    db_conn = None
    try:
        # [v5.0.6] MariaDB/Oracle 분기 처리
        if _is_mariadb():
            logger.info("   DB 타입: MariaDB")
            db_conn = database.get_db_connection(
                db_user="dummy", db_password="dummy",
                db_service_name="dummy", wallet_path="dummy"
            )
        else:
            logger.info("   DB 타입: Oracle")
            db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), os.getenv("GCP_PROJECT_ID"))
            db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), os.getenv("GCP_PROJECT_ID"))
            wallet_path = os.path.join(PROJECT_ROOT, os.getenv("OCI_WALLET_DIR_NAME", "wallet"))
            db_conn = database.get_db_connection(
                db_user=db_user, db_password=db_password,
                db_service_name=os.getenv("OCI_DB_SERVICE_NAME"), wallet_path=wallet_path
            )
        
        if not db_conn:
            raise RuntimeError("DB 연결 실패")
        
        ensure_table_exists(db_conn)

        stocks = parse_mst_file()
        upsert_stock_master(db_conn, stocks)

        logger.info("--- ✅ 종목 마스터 업데이트 완료 ---")

    except Exception as e:
        logger.critical(f"❌ 업데이트 중 치명적 오류: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if db_conn:
            db_conn.close()
            logger.info("--- DB 연결 종료 ---")

if __name__ == "__main__":
    main()
