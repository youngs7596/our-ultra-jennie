#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily Briefing 실행 스크립트

평일 오후 5시에 cron으로 실행하여 텔레그램으로 일일 브리핑을 발송합니다.

사용법:
    python scripts/run_daily_briefing.py

cron 등록 (평일 17시):
    0 17 * * 1-5 cd /path/to/project && python scripts/run_daily_briefing.py
"""

import os
import sys
import logging
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# 환경변수 설정
os.environ.setdefault("DB_TYPE", "MARIADB")
os.environ.setdefault("SECRETS_FILE", os.path.join(PROJECT_ROOT, "secrets.json"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

import shared.auth as auth
import shared.database as database
from shared.db.connection import ensure_engine_initialized
from shared.kis.gateway_client import KISGatewayClient
from shared.notification import TelegramBot

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def is_weekday():
    """평일인지 확인 (월~금)"""
    return datetime.now().weekday() < 5


def get_portfolio_summary(connection) -> dict:
    """포트폴리오 요약 정보 조회"""
    try:
        cursor = connection.cursor()
        
        # 보유 종목 조회
        cursor.execute("""
            SELECT 
                STOCK_CODE, STOCK_NAME, QUANTITY, AVG_BUY_PRICE,
                CURRENT_PRICE, PROFIT_PCT, PROFIT_AMOUNT
            FROM PORTFOLIO
            WHERE QUANTITY > 0
            ORDER BY PROFIT_PCT DESC
        """)
        holdings = cursor.fetchall()
        
        # 오늘 거래 내역
        cursor.execute("""
            SELECT 
                STOCK_CODE, STOCK_NAME, TRADE_TYPE, QUANTITY, PRICE, PROFIT_PCT
            FROM TRADE_LOG
            WHERE DATE(TRADE_TIME) = CURDATE()
            ORDER BY TRADE_TIME DESC
        """)
        today_trades = cursor.fetchall()
        
        # 총 자산 계산
        total_value = sum(
            (h.get('QUANTITY', 0) or 0) * (h.get('CURRENT_PRICE', 0) or 0) 
            for h in holdings
        )
        total_profit = sum(h.get('PROFIT_AMOUNT', 0) or 0 for h in holdings)
        
        return {
            "holdings": holdings,
            "today_trades": today_trades,
            "total_value": total_value,
            "total_profit": total_profit,
            "holdings_count": len(holdings)
        }
    except Exception as e:
        logger.error(f"포트폴리오 조회 실패: {e}")
        return {
            "holdings": [],
            "today_trades": [],
            "total_value": 0,
            "total_profit": 0,
            "holdings_count": 0
        }


def get_watchlist_summary(connection) -> dict:
    """워치리스트 요약 정보 조회"""
    try:
        cursor = connection.cursor()
        
        # 상위 종목 조회
        cursor.execute("""
            SELECT 
                STOCK_CODE, STOCK_NAME, LLM_SCORE, LLM_GRADE, 
                FACTOR_SCORE, BUY_SIGNAL_TYPE
            FROM WATCHLIST
            WHERE IS_ACTIVE = 1
            ORDER BY LLM_SCORE DESC
            LIMIT 10
        """)
        top_picks = cursor.fetchall()
        
        # 총 종목 수
        cursor.execute("SELECT COUNT(*) as cnt FROM WATCHLIST WHERE IS_ACTIVE = 1")
        total_count = cursor.fetchone().get('cnt', 0)
        
        return {
            "top_picks": top_picks,
            "total_count": total_count
        }
    except Exception as e:
        logger.error(f"워치리스트 조회 실패: {e}")
        return {"top_picks": [], "total_count": 0}


def get_market_summary() -> dict:
    """시장 요약 정보 (KIS Gateway 사용)"""
    try:
        gateway = KISGatewayClient()
        
        # KOSPI 지수
        kospi = gateway.get_current_price("0001")  # KOSPI 지수
        kosdaq = gateway.get_current_price("1001")  # KOSDAQ 지수
        
        return {
            "kospi": kospi.get("stck_prpr", "N/A") if kospi else "N/A",
            "kospi_change": kospi.get("prdy_ctrt", "N/A") if kospi else "N/A",
            "kosdaq": kosdaq.get("stck_prpr", "N/A") if kosdaq else "N/A",
            "kosdaq_change": kosdaq.get("prdy_ctrt", "N/A") if kosdaq else "N/A",
        }
    except Exception as e:
        logger.warning(f"시장 정보 조회 실패: {e}")
        return {
            "kospi": "N/A",
            "kospi_change": "N/A", 
            "kosdaq": "N/A",
            "kosdaq_change": "N/A"
        }


def format_briefing_message(portfolio: dict, watchlist: dict, market: dict) -> str:
    """브리핑 메시지 포맷팅"""
    now = datetime.now()
    
    # 헤더
    msg = f"""📊 *Ultra Jennie 일일 브리핑*
📅 {now.strftime('%Y-%m-%d %H:%M')}

"""

    # 시장 현황
    msg += f"""━━━━━━━━━━━━━━━━━━━━
📈 *시장 현황*
• KOSPI: {market['kospi']} ({market['kospi_change']}%)
• KOSDAQ: {market['kosdaq']} ({market['kosdaq_change']}%)

"""

    # 포트폴리오 현황
    msg += f"""━━━━━━━━━━━━━━━━━━━━
💰 *포트폴리오 현황*
• 보유 종목: {portfolio['holdings_count']}개
• 총 평가금액: {portfolio['total_value']:,.0f}원
• 총 손익: {portfolio['total_profit']:+,.0f}원

"""

    # 보유 종목 TOP 5
    if portfolio['holdings']:
        msg += "*보유 종목 TOP 5:*\n"
        for i, h in enumerate(portfolio['holdings'][:5], 1):
            profit_emoji = "📈" if (h.get('PROFIT_PCT') or 0) > 0 else "📉"
            msg += f"  {i}. {h.get('STOCK_NAME', 'N/A')} {profit_emoji} {h.get('PROFIT_PCT', 0):+.1f}%\n"
        msg += "\n"

    # 오늘 거래
    if portfolio['today_trades']:
        msg += "*오늘 거래:*\n"
        for t in portfolio['today_trades'][:5]:
            trade_emoji = "🟢" if t.get('TRADE_TYPE') == 'BUY' else "🔴"
            msg += f"  {trade_emoji} {t.get('STOCK_NAME', 'N/A')} ({t.get('TRADE_TYPE', 'N/A')})\n"
        msg += "\n"

    # 워치리스트 TOP 5
    msg += f"""━━━━━━━━━━━━━━━━━━━━
🎯 *AI 추천 종목 TOP 5* (총 {watchlist['total_count']}개)
"""
    if watchlist['top_picks']:
        for i, w in enumerate(watchlist['top_picks'][:5], 1):
            grade = w.get('LLM_GRADE', 'N/A')
            score = w.get('LLM_SCORE', 0) or 0
            msg += f"  {i}. {w.get('STOCK_NAME', 'N/A')} [{grade}] {score:.0f}점\n"
    else:
        msg += "  (데이터 없음)\n"

    msg += "\n━━━━━━━━━━━━━━━━━━━━"
    msg += "\n_Ultra Jennie v1.0_"
    
    return msg


def run_daily_briefing():
    """일일 브리핑 실행"""
    logger.info("=== Daily Briefing 시작 ===")
    
    # 평일 체크 (옵션)
    if not is_weekday():
        logger.info("주말이므로 브리핑을 건너뜁니다.")
        return True
    
    try:
        # 1. DB 연결
        ensure_engine_initialized()
        connection = database.get_db_connection()
        
        if not connection:
            logger.error("DB 연결 실패")
            return False
        
        # 2. 데이터 수집
        portfolio = get_portfolio_summary(connection)
        watchlist = get_watchlist_summary(connection)
        market = get_market_summary()
        
        # 3. 메시지 생성
        message = format_briefing_message(portfolio, watchlist, market)
        
        # 4. 텔레그램 발송
        telegram_token = auth.get_secret("telegram-bot-token")
        telegram_chat_id = auth.get_secret("telegram-chat-id")
        
        if not telegram_token or not telegram_chat_id:
            logger.error("텔레그램 설정이 없습니다.")
            return False
        
        telegram_bot = TelegramBot(token=telegram_token, chat_id=telegram_chat_id)
        telegram_bot.send_message(message)
        
        logger.info("✅ Daily Briefing 발송 완료!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Daily Briefing 실패: {e}", exc_info=True)
        return False
    finally:
        if 'connection' in dir() and connection:
            connection.close()


if __name__ == "__main__":
    success = run_daily_briefing()
    sys.exit(0 if success else 1)

