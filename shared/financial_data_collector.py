#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v3.7
"""
[v3.7] 재무 데이터 수집 모듈
작업 LLM: Claude Sonnet 4.5, Claude Opus 4.5

목적:
- WatchList 종목의 재무 데이터 수집 (ROE, 매출성장률, EPS성장률, PBR, PER)
- FINANCIAL_DATA 테이블에서 데이터 조회 및 계산
- KIS API 또는 네이버 증권에서 PBR/PER 수집
- [v3.6] MariaDB/Oracle 호환 지원
"""

import logging
import requests
import re
import os
from bs4 import BeautifulSoup
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

def _is_mariadb() -> bool:
    """현재 DB 타입이 MariaDB인지 확인"""
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"

def calculate_roe_from_financial_data(connection, stock_code):
    """
    FINANCIAL_DATA 테이블에서 ROE 계산
    
    ROE = (당기순이익 / 자기자본) * 100
    
    Args:
        connection: DB 연결
        stock_code: 종목 코드
    
    Returns:
        float: ROE (%), 없으면 None
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        # 최신 재무 데이터 조회 (TOTAL_EQUITY가 없으면 계산)
        sql = """
        SELECT NET_INCOME, TOTAL_EQUITY, TOTAL_ASSETS, TOTAL_LIABILITIES
        FROM (
            SELECT NET_INCOME, TOTAL_EQUITY, TOTAL_ASSETS, TOTAL_LIABILITIES
            FROM FINANCIAL_DATA
            WHERE STOCK_CODE = :1
            ORDER BY REPORT_DATE DESC
        )
        WHERE ROWNUM <= 1
        """
        cursor.execute(sql, [stock_code])
        row = cursor.fetchone()
        
        if row and row[0] is not None:  # NET_INCOME이 있어야 함
            net_income = float(row[0])
            total_equity = row[1]
            total_assets = row[2]
            total_liabilities = row[3]
            
            # TOTAL_EQUITY가 NULL이면 ASSETS - LIABILITIES로 계산
            if total_equity is None:
                if total_assets is not None and total_liabilities is not None:
                    total_equity = float(total_assets) - float(total_liabilities)
                else:
                    return None
            else:
                total_equity = float(total_equity)
            
            if total_equity > 0:
                roe = (net_income / total_equity) * 100
                return round(roe, 2)
        
        return None
        
    except Exception as e:
        logger.debug(f"   (Financial) {stock_code} ROE 계산 실패: {e}")
        return None
    finally:
        if cursor:
            cursor.close()


def get_growth_rates_from_financial_data(connection, stock_code):
    """
    FINANCIAL_DATA 테이블에서 매출 성장률 및 EPS 성장률 조회
    
    Args:
        connection: DB 연결
        stock_code: 종목 코드
    
    Returns:
        tuple: (sales_growth, eps_growth), 없으면 (None, None)
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        # 최신 재무 데이터 조회 (SALES_GROWTH, EPS_GROWTH 컬럼 사용)
        sql = """
        SELECT SALES_GROWTH, EPS_GROWTH
        FROM (
            SELECT SALES_GROWTH, EPS_GROWTH
            FROM FINANCIAL_DATA
            WHERE STOCK_CODE = :1
            ORDER BY REPORT_DATE DESC
        )
        WHERE ROWNUM <= 1
        """
        cursor.execute(sql, [stock_code])
        row = cursor.fetchone()
        
        if row:
            sales_growth = float(row[0]) if row[0] is not None else None
            eps_growth = float(row[1]) if row[1] is not None else None
            return sales_growth, eps_growth
        
        return None, None
        
    except Exception as e:
        logger.debug(f"   (Financial) {stock_code} 성장률 조회 실패: {e}")
        return None, None
    finally:
        if cursor:
            cursor.close()


def scrape_pbr_per_from_naver(stock_code, debug=False, max_retries=3):
    """
    네이버 증권에서 PBR, PER, ROE, EPS (여러 연도) 크롤링
    
    Args:
        stock_code: 종목 코드 (6자리)
        debug: 디버깅 모드 (True면 상세 로그 출력)
        max_retries: 최대 재시도 횟수
    
    Returns:
        tuple: (pbr, per, roe, eps_list, market_cap)
               eps_list는 [{'year': 2023, 'eps': 2131.0}, {'year': 2024, 'eps': 4950.0}] 형식
               실패 시 (None, None, None, [], None)
    """
    import time
    
    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    
    for attempt in range(max_retries):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # 재시도 시 대기
            if attempt > 0:
                wait_time = attempt * 2  # 2초, 4초
                if debug:
                    logger.info(f"   (Financial) {stock_code} 재시도 {attempt+1}/{max_retries} (대기 {wait_time}초)")
                time.sleep(wait_time)
            
            response = requests.get(url, headers=headers, timeout=15)
        
            if response.status_code != 200:
                if debug:
                    logger.warning(f"   (Financial) {stock_code} HTTP 요청 실패 (시도 {attempt+1}/{max_retries}): status_code={response.status_code}")
                if attempt < max_retries - 1:
                    continue  # 다음 재시도
                return None, None, None, [], None
        
            soup = BeautifulSoup(response.text, 'html.parser')
        
            pbr = None
            per = None
            roe = None
            eps_list = []  # 여러 연도 EPS 저장
            market_cap = None
        
            # 1. PER, PBR 찾기 (투자지표 영역 - Table 9)
            tables = soup.find_all('table')
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['th', 'td'])
                    for i, cell in enumerate(cells):
                        text = cell.get_text(strip=True)
                    
                        # PER 찾기 (예: "PER\lEPS(2025.06)..." 형식)
                        if text.startswith('PER') and 'EPS' in text:
                            # 다음 셀에서 "21.71배l4,477원" 형식의 값 찾기
                            if i + 1 < len(cells):
                                value_text = cells[i + 1].get_text(strip=True)
                                # "21.71배" 부분 추출
                                if '배' in value_text:
                                    try:
                                        per_text = value_text.split('배')[0].replace(',', '')
                                        per = float(per_text)
                                    except:
                                        pass
                    
                        # PBR 찾기 (예: "PBR\lBPS(2025.06)..." 형식)
                        if text.startswith('PBR') and 'BPS' in text:
                            if i + 1 < len(cells):
                                value_text = cells[i + 1].get_text(strip=True)
                                if '배' in value_text:
                                    try:
                                        pbr_text = value_text.split('배')[0].replace(',', '')
                                        pbr = float(pbr_text)
                                    except:
                                        pass
        
            # 2. ROE, EPS 찾기 (주요재무정보 테이블 - tb_type1_ifrs)
            # 네이버는 "주요재무정보" 테이블에 ROE와 EPS를 모두 포함하고 있습니다
            financial_table = None
            for table in tables:
                table_text = table.get_text()
                # "주요재무정보" 또는 "tb_type1_ifrs" 클래스가 있는 테이블 찾기
                if ('주요재무정보' in table_text or 'tb_type1_ifrs' in table.get('class', [])):
                    financial_table = table
                    break
            
            if financial_table:
                rows = financial_table.find_all('tr')
            
                # 데이터 행 처리
                for row in rows:
                    cells = row.find_all(['th', 'td'])
                    if len(cells) > 0:
                        first_cell = cells[0].get_text(strip=True)
                    
                        # "ROE(지배주주)" 행 찾기
                        if 'ROE' in first_cell and '지배' in first_cell:
                            # Cell[3]에 최근 연간 실적 (2024년) 값이 있음
                            if len(cells) > 3:
                                try:
                                    roe_text = cells[3].get_text(strip=True).replace(',', '')
                                    if roe_text and roe_text != '':
                                        roe = float(roe_text)
                                        if debug:
                                            logger.info(f"   (Financial) {stock_code} ROE 발견: {roe}%")
                                except:
                                    pass
                    
                        # "EPS(원)" 행 찾기 - 최근 2개 연도 수집 (2023, 2024)
                        if 'EPS' in first_cell and '원' in first_cell:
                            if debug:
                                logger.info(f"   (Financial) {stock_code} EPS 행 발견! first_cell='{first_cell}'")
                                logger.info(f"   (Financial) {stock_code} 셀 개수: {len(cells)}")
                                for cell_idx, cell in enumerate(cells[:6]):  # 처음 6개만 출력
                                    logger.info(f"   (Financial) {stock_code}   Cell[{cell_idx}]: {cell.get_text(strip=True)}")
                            
                            # Cell[2] = 2023년, Cell[3] = 2024년
                            # 실제 데이터 구조:
                            # Cell[0]: EPS(원)
                            # Cell[1]: 2021년 (8,057)
                            # Cell[2]: 2023년 (2,131)
                            # Cell[3]: 2024년 (4,950)
                            for data_idx, year in [(2, 2023), (3, 2024)]:
                                if len(cells) > data_idx:
                                    try:
                                        eps_text = cells[data_idx].get_text(strip=True).replace(',', '')
                                        
                                        if debug:
                                            logger.info(f"   (Financial) {stock_code}   Cell[{data_idx}] ({year}년): eps_text='{eps_text}'")
                                        
                                        if eps_text and eps_text != '':
                                            eps_value = float(eps_text)
                                            eps_list.append({'year': year, 'eps': eps_value})
                                            if debug:
                                                logger.info(f"   (Financial) {stock_code}   ✅ EPS 추가: {year}년 = {eps_value}원")
                                    except Exception as e_eps:
                                        if debug:
                                            logger.warning(f"   (Financial) {stock_code} EPS 파싱 실패 (data_idx={data_idx}): {e_eps}")
        
            # 시가총액 찾기
            # <em class="no_info">시가총액</em> 다음의 값
            market_cap_elem = soup.find('em', string=lambda x: x and '시가총액' in x)
            if market_cap_elem:
                # 다음 sibling에서 값 찾기
                next_elem = market_cap_elem.find_next('em')
                if next_elem:
                    market_cap_text = next_elem.get_text(strip=True)
                    try:
                        # "1조 2,345억" 형식 파싱
                        market_cap_text = market_cap_text.replace(',', '')
                        if '조' in market_cap_text:
                            parts = market_cap_text.split('조')
                            trillion = float(parts[0])
                            billion = 0
                            if len(parts) > 1 and '억' in parts[1]:
                                billion = float(parts[1].replace('억', '').strip())
                            market_cap = trillion * 1000000 + billion * 100  # 백만원 단위
                        elif '억' in market_cap_text:
                            billion = float(market_cap_text.replace('억', '').strip())
                            market_cap = billion * 100  # 백만원 단위
                    except:
                        pass
        
            # 성공적으로 데이터를 가져왔으면 반환
            if debug:
                logger.info(f"   (Financial) {stock_code} 크롤링 성공 (시도 {attempt+1}/{max_retries}): PBR={pbr}, PER={per}, ROE={roe}, EPS={len(eps_list)}개")
            
            return pbr, per, roe, eps_list, market_cap
        
        except requests.exceptions.Timeout:
            if debug:
                logger.warning(f"   (Financial) {stock_code} 타임아웃 (시도 {attempt+1}/{max_retries})")
            if attempt < max_retries - 1:
                continue  # 다음 재시도
        except requests.exceptions.RequestException as e:
            if debug:
                logger.warning(f"   (Financial) {stock_code} 네트워크 오류 (시도 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                continue  # 다음 재시도
        except Exception as e:
            if debug:
                logger.error(f"   (Financial) {stock_code} PBR/PER/ROE/EPS 크롤링 실패 (시도 {attempt+1}/{max_retries}): {e}")
                import traceback
                traceback.print_exc()
            else:
                logger.debug(f"   (Financial) {stock_code} PBR/PER/ROE/EPS 크롤링 실패: {e}")
            if attempt < max_retries - 1:
                continue  # 다음 재시도
    
    # 모든 재시도 실패
    logger.warning(f"   (Financial) {stock_code} 크롤링 최종 실패 ({max_retries}회 시도)")
    return None, None, None, [], None


def update_watchlist_financial_data(connection, stock_code):
    """
    WatchList 종목의 재무 데이터 업데이트
    
    Args:
        connection: DB 연결
        stock_code: 종목 코드
    
    Returns:
        bool: 성공 여부
    """
    cursor = None
    try:
        # 1. ROE 계산 (FINANCIAL_DATA 기반)
        roe_from_db = calculate_roe_from_financial_data(connection, stock_code)
        
        # 2. 성장률 조회 (FINANCIAL_DATA 기반)
        sales_growth, eps_growth_from_db = get_growth_rates_from_financial_data(connection, stock_code)
        
        # 3. PBR, PER, ROE, EPS 크롤링 (네이버 증권)
        pbr, per, roe_from_naver, eps_list_from_naver, market_cap = scrape_pbr_per_from_naver(stock_code)
        
        # 4. ROE 우선순위: 네이버 크롤링 > DB 계산
        roe = roe_from_naver if roe_from_naver is not None else roe_from_db
        
        # 5. EPS_GROWTH 직접 계산 (네이버 크롤링 데이터 기반, DB 저장은 생략)
        eps_growth_from_naver = None
        if eps_list_from_naver and len(eps_list_from_naver) > 0:
            logger.info(f"   (Financial) {stock_code} ✅ EPS 크롤링 성공: {len(eps_list_from_naver)}개 연도 - {eps_list_from_naver}")
            
            # EPS_GROWTH 직접 계산 (2023년 vs 2024년)
            eps_2023 = None
            eps_2024 = None
            for eps_data in eps_list_from_naver:
                if eps_data['year'] == 2023:
                    eps_2023 = eps_data['eps']
                elif eps_data['year'] == 2024:
                    eps_2024 = eps_data['eps']
            
            # EPS_GROWTH 계산: ((2024 - 2023) / 2023) * 100
            if eps_2023 is not None and eps_2024 is not None and eps_2023 != 0:
                eps_growth_from_naver = ((eps_2024 - eps_2023) / eps_2023) * 100
                logger.info(f"   (Financial) {stock_code} ✅ EPS_GROWTH 계산 성공: {eps_growth_from_naver:.2f}% (2023: {eps_2023}, 2024: {eps_2024})")
            else:
                logger.warning(f"   (Financial) {stock_code} ⚠️ EPS_GROWTH 계산 실패 (2023: {eps_2023}, 2024: {eps_2024})")
        else:
            logger.warning(f"   (Financial) {stock_code} ⚠️ EPS 크롤링 실패 (네이버 금융에서 데이터를 찾을 수 없음)")
        
        eps_growth = eps_growth_from_naver  # EPS_GROWTH 사용 (네이버 크롤링 기반)
        
        # 6. WatchList 업데이트 (NULL 값은 기존 값 유지)
        cursor = connection.cursor()
        now = datetime.now(timezone.utc)
        
        # [v3.6] MariaDB/Oracle 호환 - 조건부 SQL 생성
        update_fields = []
        params_list = []  # MariaDB용 순서 기반 파라미터
        params_dict = {'stock_code': stock_code}  # Oracle용 이름 기반 파라미터
        
        if _is_mariadb():
            # MariaDB: %s 플레이스홀더 사용
            if roe is not None:
                update_fields.append("ROE = %s")
                params_list.append(roe)
            if sales_growth is not None:
                update_fields.append("SALES_GROWTH = %s")
                params_list.append(sales_growth)
            if eps_growth is not None:
                update_fields.append("EPS_GROWTH = %s")
                params_list.append(eps_growth)
            if pbr is not None:
                update_fields.append("PBR = %s")
                params_list.append(pbr)
            if per is not None:
                update_fields.append("PER = %s")
                params_list.append(per)
            if market_cap is not None:
                update_fields.append("MARKET_CAP = %s")
                params_list.append(market_cap)
            
            # 항상 업데이트할 필드
            update_fields.append("FINANCIAL_UPDATED_AT = %s")
            params_list.append(now)
            
            if len(update_fields) == 1:  # FINANCIAL_UPDATED_AT만 있는 경우
                logger.debug(f"   (Financial) {stock_code} 업데이트할 데이터가 없음")
                return False
            
            # WHERE 조건 파라미터 추가
            params_list.append(stock_code)
            
            sql = f"""
            UPDATE WATCHLIST
            SET {', '.join(update_fields)}
            WHERE STOCK_CODE = %s
            """
            cursor.execute(sql, params_list)
        else:
            # Oracle: :name 플레이스홀더 사용
            if roe is not None:
                update_fields.append("ROE = :roe")
                params_dict['roe'] = roe
            if sales_growth is not None:
                update_fields.append("SALES_GROWTH = :sales_growth")
                params_dict['sales_growth'] = sales_growth
            if eps_growth is not None:
                update_fields.append("EPS_GROWTH = :eps_growth")
                params_dict['eps_growth'] = eps_growth
            if pbr is not None:
                update_fields.append("PBR = :pbr")
                params_dict['pbr'] = pbr
            if per is not None:
                update_fields.append("PER = :per")
                params_dict['per'] = per
            if market_cap is not None:
                update_fields.append("MARKET_CAP = :market_cap")
                params_dict['market_cap'] = market_cap
            
            # 항상 업데이트할 필드
            update_fields.append("FINANCIAL_UPDATED_AT = SYSTIMESTAMP")
            
            if len(update_fields) == 1:  # FINANCIAL_UPDATED_AT만 있는 경우
                logger.debug(f"   (Financial) {stock_code} 업데이트할 데이터가 없음")
                return False
            
            sql = f"""
            UPDATE WATCHLIST
            SET {', '.join(update_fields)}
            WHERE STOCK_CODE = :stock_code
            """
            cursor.execute(sql, params_dict)
        
        connection.commit()
        
        logger.info(f"   (Financial) {stock_code} ✅ WATCHLIST 업데이트 완료 - ROE: {roe}, EPS_GROWTH: {eps_growth}, SALES_GROWTH: {sales_growth}, PBR: {pbr}, PER: {per}")
        return True
        
    except Exception as e:
        logger.error(f"   (Financial) {stock_code} 재무 데이터 업데이트 실패: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()


def batch_update_watchlist_financial_data(connection, stock_codes, max_workers=1):
    """
    WatchList 종목들의 재무 데이터 일괄 업데이트
    
    Args:
        connection: DB 연결
        stock_codes: 종목 코드 리스트
        max_workers: 동시 처리 스레드 수 (기본값 1 - 네이버 차단 방지)
    
    Returns:
        dict: {'success': 성공 개수, 'failed': 실패 개수}
    """
    import time
    
    success_count = 0
    failed_count = 0
    
    logger.info(f"   (Financial) {len(stock_codes)}개 종목 재무 데이터 업데이트 시작... (순차 처리, 요청 간 딜레이)")
    
    # 순차 처리 (병렬 처리 제거)
    for idx, stock_code in enumerate(stock_codes):
        try:
            if update_watchlist_financial_data(connection, stock_code):
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            logger.error(f"   (Financial) {stock_code} 처리 중 예외: {e}")
            failed_count += 1
        
        # 매 요청마다 0.5초 대기 (네이버 차단 방지)
        if idx < len(stock_codes) - 1:  # 마지막 종목은 대기 불필요
            time.sleep(0.5)
        
        # 10개마다 진행 상황 로그
        if (idx + 1) % 10 == 0:
            logger.info(f"   (Financial) 진행 상황: {idx + 1}/{len(stock_codes)} 완료")
    
    logger.info(f"   (Financial) 재무 데이터 업데이트 완료 (성공: {success_count}, 실패: {failed_count})")
    
    return {'success': success_count, 'failed': failed_count}

