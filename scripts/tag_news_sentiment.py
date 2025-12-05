#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v1.0
# 작업 LLM: GPT-5.1 Codex
"""
[v1.0] scripts/tag_news_sentiment.py

수집된 네이버 뉴스 메타데이터(STOCK_NEWS_SENTIMENT)를 대상으로
간단한 룰 기반 감성 점수 및 카테고리를 부여합니다.

- MariaDB/Oracle 하이브리드 지원
- 추후 LLM 태깅 파이프라인으로 확장 가능하도록 구조화
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv

# 프로젝트 루트 경로 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
import shared.database as database

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TABLE_NAME = "STOCK_NEWS_SENTIMENT"

CATEGORY_RULES = {
    "실적": ["실적", "어닝", "영업이익", "매출", "컨센서스", "가이던스"],
    "수주": ["수주", "계약", "낙찰", "공급계약", "주문"],
    "신사업": ["신사업", "신규", "진출", "출시", "전략적", "투자"],
    "M&A": ["인수", "합병", "M&A", "지분 인수", "경영권"],
    "배당": ["배당", "중간배당", "현금배당", "자사주"],
    "규제": ["제재", "규제", "조사", "징계", "행정처분", "벌금"],
    "증자": ["유상증자", "증자", "CB", "BW", "전환사채", "신주발행"],
}

POSITIVE_KEYWORDS = [
    "호조", "수주", "계약", "상승", "호재", "증가", "개선", "최대", "선정",
    "승인", "신규", "기록", "수익", "배당", "성공", "확정"
]
NEGATIVE_KEYWORDS = [
    "악재", "하락", "감소", "경고", "적자", "손실", "부진", "취소", "연기",
    "실패", "리콜", "규제", "조사", "소송", "리스크", "파산", "증자", "유증",
    "철수", "중단"
]


def _is_mariadb() -> bool:
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"


def get_db_config():
    if _is_mariadb():
        return {
            "db_user": "dummy",  # 환경변수 기반
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


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def classify_category(text: str) -> str:
    """뉴스 카테고리를 분류합니다. 매칭되지 않으면 '기타' 반환."""
    lower_text = text.lower()
    for category, keywords in CATEGORY_RULES.items():
        for keyword in keywords:
            if keyword.lower() in lower_text:
                return category
    return "기타"  # 키워드 매칭 없으면 '기타'로 분류 (재처리 방지)


def score_sentiment(text: str) -> int:
    lower_text = text.lower()
    score = 0
    for keyword in POSITIVE_KEYWORDS:
        if keyword.lower() in lower_text:
            score += 10
    for keyword in NEGATIVE_KEYWORDS:
        if keyword.lower() in lower_text:
            score -= 10
    sentiment = max(0, min(100, 50 + score))
    return sentiment


def classify_article(headline: str, summary: str) -> Tuple[int, Optional[str]]:
    combined = normalize_text(f"{headline} {summary}")
    category = classify_category(combined)
    sentiment = score_sentiment(combined)
    return sentiment, category


def parse_args():
    parser = argparse.ArgumentParser(description="뉴스 감성/카테고리 태깅 스크립트")
    parser.add_argument("--days", type=int, default=365, help="대상 기간(일)")
    parser.add_argument("--limit", type=int, default=1000, help="최대 처리 건수")
    parser.add_argument("--batch", type=int, default=200, help="DB 업데이트 배치 크기")
    parser.add_argument("--sleep", type=float, default=0.0, help="배치 간 대기 시간")
    return parser.parse_args()


def fetch_articles(cursor, cutoff: datetime, limit: int) -> list:
    """미태깅 뉴스를 조회합니다. (CATEGORY IS NULL인 뉴스만 대상)"""
    if _is_mariadb():
        sql = f"""
            SELECT ID, STOCK_CODE, HEADLINE, SUMMARY
            FROM {TABLE_NAME}
            WHERE SCRAPED_AT >= %s
              AND CATEGORY IS NULL
            ORDER BY SCRAPED_AT DESC
            LIMIT %s
        """
        cursor.execute(sql, (cutoff, limit))
    else:
        sql = f"""
            SELECT ID, STOCK_CODE, HEADLINE, SUMMARY
            FROM {TABLE_NAME}
            WHERE SCRAPED_AT >= TO_TIMESTAMP(:1, 'YYYY-MM-DD HH24:MI:SS')
              AND CATEGORY IS NULL
            ORDER BY SCRAPED_AT DESC
            FETCH FIRST {limit} ROWS ONLY
        """
        cursor.execute(sql, [cutoff.strftime("%Y-%m-%d %H:%M:%S")])
    rows = cursor.fetchall()
    return rows or []


def update_batch(cursor, rows: list):
    if not rows:
        return
    if _is_mariadb():
        sql = f"""
            UPDATE {TABLE_NAME}
            SET SENTIMENT_SCORE = %s,
                CATEGORY = %s
            WHERE ID = %s
        """
        cursor.executemany(sql, [(row["sentiment"], row["category"], row["id"]) for row in rows])
    else:
        sql = f"""
            UPDATE {TABLE_NAME}
            SET SENTIMENT_SCORE = :1,
                CATEGORY = :2
            WHERE ID = :3
        """
        cursor.executemany(sql, [(row["sentiment"], row["category"], row["id"]) for row in rows])


def process_articles(connection, args):
    cursor = connection.cursor()
    cutoff = datetime.utcnow() - timedelta(days=args.days)
    articles = fetch_articles(cursor, cutoff, args.limit)
    if not articles:
        logger.info("태깅할 뉴스가 없습니다.")
        cursor.close()
        return

    logger.info(f"총 {len(articles)}건 태깅 대상 확보")
    batch = []
    processed = 0

    for row in articles:
        # 딕셔너리 또는 튜플 형태 모두 지원
        if isinstance(row, dict):
            row_id = row.get("ID") or row.get("id")
            headline = row.get("HEADLINE") or row.get("headline") or ""
            summary = row.get("SUMMARY") or row.get("summary") or ""
        else:
            row_id = row[0]
            headline = row[2] or ""
            summary = row[3] or ""
        sentiment, category = classify_article(headline, summary)
        batch.append({"id": row_id, "sentiment": sentiment, "category": category})
        if len(batch) >= args.batch:
            update_batch(cursor, batch)
            connection.commit()
            processed += len(batch)
            logger.info(f"   ↳ {processed}/{len(articles)}건 업데이트")
            batch = []
            if args.sleep > 0:
                time.sleep(args.sleep)

    if batch:
        update_batch(cursor, batch)
        connection.commit()
        processed += len(batch)
    cursor.close()
    logger.info(f"✅ 감성/카테고리 태깅 완료 ({processed}건)")


def main():
    load_dotenv()
    args = parse_args()
    db_config = get_db_config()
    conn = database.get_db_connection(**db_config)
    if not conn:
        logger.error("DB 연결 실패")
        return
    try:
        process_articles(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

