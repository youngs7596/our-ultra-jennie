#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v1.0
# 작업 LLM: GPT-5.1 Codex
"""
[v1.0] scripts/collect_naver_news.py

KOSPI 종목별 네이버 금융 뉴스를 순회하며 기사 메타데이터를 수집/저장합니다.
 - 단계별 Sleep 및 User-Agent 로테이션으로 차단 리스크 최소화
 - MariaDB / Oracle 모두 지원 (execute_upsert 사용)
 - 감성/카테고리는 후속 파이프라인에서 업데이트 가능 (기본값: 중립)
"""

import argparse
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import FinanceDataReader as fdr

# 프로젝트 루트 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
import shared.database as database
from shared.hybrid_scoring.schema import execute_upsert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 네이버 모바일 API 사용 (JSON 응답, 더 안정적)
BASE_URL = "https://m.stock.naver.com/api/news/stock"
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

TABLE_NAME = "STOCK_NEWS_SENTIMENT"


def _is_mariadb() -> bool:
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"


def ensure_table_exists(connection):
    cursor = connection.cursor()
    try:
        if _is_mariadb():
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    ARTICLE_URL VARCHAR(2000) UNIQUE,
                    STOCK_CODE VARCHAR(20) NOT NULL,
                    NEWS_DATE DATETIME NOT NULL,
                    PRESS VARCHAR(255),
                    HEADLINE VARCHAR(1000),
                    SUMMARY TEXT,
                    SOURCE VARCHAR(50) DEFAULT 'NAVER',
                    CATEGORY VARCHAR(50),
                    SENTIMENT_SCORE INT DEFAULT 50,
                    SCRAPED_AT DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        else:
            try:
                cursor.execute(f"SELECT 1 FROM {TABLE_NAME} WHERE ROWNUM=1")
            except Exception:
                cursor.execute(f"""
                    CREATE TABLE {TABLE_NAME} (
                        ID NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        ARTICLE_URL VARCHAR2(2000) UNIQUE,
                        STOCK_CODE VARCHAR2(20) NOT NULL,
                        NEWS_DATE TIMESTAMP NOT NULL,
                        PRESS VARCHAR2(255),
                        HEADLINE VARCHAR2(1000),
                        SUMMARY CLOB,
                        SOURCE VARCHAR2(50) DEFAULT 'NAVER',
                        CATEGORY VARCHAR2(50),
                        SENTIMENT_SCORE NUMBER DEFAULT 50,
                        SCRAPED_AT TIMESTAMP DEFAULT SYSTIMESTAMP
                    )
                """)
        connection.commit()
        logger.info(f"✅ 테이블 확인 완료: {TABLE_NAME}")
    except Exception as e:
        logger.error(f"❌ 테이블 생성 실패: {e}")
        connection.rollback()
        raise
    finally:
        cursor.close()


def build_headers() -> Dict[str, str]:
    ua = random.choice(DEFAULT_USER_AGENTS)
    return {
        "User-Agent": os.getenv("NAVER_NEWS_USER_AGENT", ua),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "close",
    }


def fetch_news_api(stock_code: str, page: int, page_size: int = 20) -> Optional[List[Dict]]:
    """네이버 모바일 API를 통해 뉴스 JSON 데이터를 가져옵니다."""
    url = f"{BASE_URL}/{stock_code}"
    params = {"page": page, "pageSize": page_size}
    headers = build_headers()
    headers["Referer"] = "https://m.stock.naver.com/"
    
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
            logger.warning(f"⚠️ [{stock_code}] 페이지 {page} 응답 코드 {resp.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ [{stock_code}] 페이지 {page} 요청 실패 ({attempt+1}/3): {e}")
        time.sleep(random.uniform(1.5, 3.0))
    return None


def parse_api_response(stock_code: str, api_data: List[Dict]) -> List[Dict]:
    """네이버 모바일 API 응답을 파싱하여 기사 목록을 반환합니다."""
    articles = []
    for item in api_data:
        # API 응답 구조: {"total": N, "items": [...]}
        news_items = item.get("items", [])
        for news in news_items:
            headline = news.get("title", "")
            if not headline:
                continue
            
            # 기사 URL 생성
            office_id = news.get("officeId", "")
            article_id = news.get("articleId", "")
            article_url = f"https://n.news.naver.com/mnews/article/{office_id}/{article_id}" if office_id and article_id else ""
            
            # 날짜 파싱 (형식: "202512042255")
            datetime_str = news.get("datetime", "")
            published_at = parse_api_datetime(datetime_str)
            if not published_at:
                continue
            
            articles.append({
                "stock_code": stock_code,
                "headline": headline,
                "article_url": article_url,
                "press": news.get("officeName", ""),
                "published_at": published_at,
                "summary": news.get("body", "")[:500] if news.get("body") else "",
            })
    return articles


def parse_api_datetime(text: str) -> Optional[datetime]:
    """API 날짜 형식(YYYYMMDDHHmm) 파싱"""
    if not text or len(text) < 8:
        return None
    try:
        if len(text) >= 12:
            return datetime.strptime(text[:12], "%Y%m%d%H%M")
        elif len(text) >= 8:
            return datetime.strptime(text[:8], "%Y%m%d")
    except ValueError:
        pass
    return None


def parse_datetime(text: str) -> Optional[datetime]:
    """레거시 날짜 형식 파싱 (호환성 유지)"""
    try:
        return datetime.strptime(text, "%Y.%m.%d %H:%M")
    except ValueError:
        try:
            return datetime.strptime(text, "%Y.%m.%d")
        except ValueError:
            return None


def save_articles(connection, articles: List[Dict]):
    if not articles:
        return 0
    cursor = connection.cursor()
    saved = 0
    for article in articles:
        try:
            columns = [
                "ARTICLE_URL", "STOCK_CODE", "NEWS_DATE",
                "PRESS", "HEADLINE", "SUMMARY",
                "SCRAPED_AT", "SOURCE", "CATEGORY", "SENTIMENT_SCORE"
            ]
            values = (
                article["article_url"],
                article["stock_code"],
                article["published_at"],
                article.get("press"),
                article.get("headline"),
                article.get("summary"),
                datetime.utcnow(),
                "NAVER",
                article.get("category"),
                article.get("sentiment_score", 50),
            )
            execute_upsert(
                cursor,
                TABLE_NAME,
                columns,
                values,
                unique_keys=["ARTICLE_URL"],
                update_columns=[
                    "STOCK_CODE", "NEWS_DATE", "PRESS", "HEADLINE",
                    "SUMMARY", "SCRAPED_AT", "SOURCE", "CATEGORY", "SENTIMENT_SCORE"
                ]
            )
            saved += 1
        except Exception as e:
            logger.debug(f"   ℹ️ 저장 스킵 ({article.get('headline', '')[:20]}...): {e}")
    connection.commit()
    cursor.close()
    return saved


def load_stock_codes(limit: int = 50) -> List[str]:
    codes = fdr.StockListing("KOSPI")["Code"].tolist()
    if limit:
        return codes[:limit]
    return codes


def collect_news_for_stock(stock_code: str,
                           min_date: datetime,
                           max_pages: int,
                           max_articles: int) -> List[Dict]:
    """종목별 뉴스를 수집합니다 (네이버 모바일 API 사용)."""
    articles = []
    page_size = 20  # API 페이지당 최대 기사 수
    
    for page in range(1, max_pages + 1):
        api_data = fetch_news_api(stock_code, page, page_size)
        if not api_data:
            logger.debug(f"   [{stock_code}] 페이지 {page} 데이터 없음")
            break
        
        parsed = parse_api_response(stock_code, api_data)
        if not parsed:
            logger.debug(f"   [{stock_code}] 페이지 {page} 파싱 결과 없음")
            break
        
        for article in parsed:
            if article["published_at"] < min_date:
                logger.debug(f"   [{stock_code}] 날짜 범위 초과 - 수집 중단")
                return articles
            articles.append(article)
            if len(articles) >= max_articles:
                return articles
        
        # 페이지와 페이지 사이 랜덤 대기 (IP 차단 방지)
        time.sleep(random.uniform(1.0, 2.0))
    
    return articles


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


def parse_args():
    parser = argparse.ArgumentParser(description="네이버 종목 뉴스 수집기")
    parser.add_argument("--days", type=int, default=365, help="수집 기간(일)")
    parser.add_argument("--codes", type=int, default=50, help="수집할 종목 수 (상위)")
    parser.add_argument("--max-pages", type=int, default=60, help="종목별 최대 페이지 크롤링 수")
    parser.add_argument("--max-articles", type=int, default=500, help="종목별 최대 기사 수")
    parser.add_argument("--sleep", type=float, default=2.0, help="종목 전환 시 기본 대기 시간")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()

    logger.info(f"🔎 네이버 뉴스 크롤링 시작 (기간: {args.days}일, 종목 {args.codes}개)")

    db_config = get_db_config()
    conn = database.get_db_connection(**db_config)
    if not conn:
        logger.error("DB 연결 실패로 종료합니다.")
        return

    ensure_table_exists(conn)

    min_date = datetime.now() - timedelta(days=args.days)
    stock_codes = load_stock_codes(args.codes)
    total_saved = 0

    for idx, code in enumerate(stock_codes, start=1):
        try:
            logger.info(f"[{idx}/{len(stock_codes)}] {code} 뉴스 수집")
            articles = collect_news_for_stock(
                code,
                min_date=min_date,
                max_pages=args.max_pages,
                max_articles=args.max_articles,
            )
            saved = save_articles(conn, articles)
            total_saved += saved
            logger.info(f"   ↳ {len(articles)}건 수집, {saved}건 저장 (누적 {total_saved})")
        except Exception as e:
            logger.error(f"❌ [{code}] 수집 실패: {e}")
        time.sleep(random.uniform(args.sleep, args.sleep + 1.5))

    conn.close()
    logger.info(f"✅ 네이버 뉴스 수집 완료 (총 저장: {total_saved}건)")


if __name__ == "__main__":
    main()

