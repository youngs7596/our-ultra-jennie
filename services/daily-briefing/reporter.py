# services/daily-briefing/reporter.py
# Version: v5.0
# Daily Briefing Service - LLM 기반 일일 보고서 생성
# [v5.0] Centralized LLM using JennieBrain (Factory Pattern)

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

import shared.database as database
import shared.auth as auth
from shared.llm import JennieBrain

logger = logging.getLogger(__name__)


class DailyReporter:
    """LLM 기반 일일 브리핑 리포터 (Powered by JennieBrain)"""
    
    def __init__(self, kis_client, telegram_bot):
        self.kis = kis_client
        self.bot = telegram_bot
        # [v5.0] JennieBrain 초기화 (Factory/Tier 자동 처리)
        try:
            secrets = auth._load_local_secrets()
            project_id = secrets.get("project_id", "my-ultra-jennie")
            gemini_key_secret = "gemini-api-key" # Legacy init param, not strictly used in v6 Factory
            
            self.jennie_brain = JennieBrain(project_id, gemini_key_secret)
            logger.info("✅ DailyReporter: JennieBrain 연결 완료")
        except Exception as e:
            logger.error(f"❌ DailyReporter JennieBrain 초기화 실패: {e}")
            self.jennie_brain = None
        
    def create_and_send_report(self):
        """리포트를 생성하고 텔레그램으로 발송합니다."""
        try:
            from shared.db.connection import session_scope
            
            with session_scope() as session:
                # 1. 데이터 수집
                report_data = self._collect_report_data(session)
                
                # 2. LLM 기반 보고서 생성 (Centralized)
                if self.jennie_brain:
                    # 데이터 요약 생성
                    market_summary_text = self._format_market_summary(report_data)
                    execution_log_text = self._format_execution_log(report_data)
                    
                    message = self.jennie_brain.generate_daily_briefing(
                        market_summary_text, 
                        execution_log_text
                    )
                else:
                    message = self._format_basic_message(report_data)
                
                # 3. 발송
                return self.bot.send_message(message)
                
        except Exception as e:
            logger.error(f"리포트 생성 중 오류: {e}", exc_info=True)
            return False
    
    def _collect_report_data(self, session) -> Dict:
        """보고서 생성에 필요한 모든 데이터 수집"""
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        # 1. 현금 잔고
        cash_balance = self.kis.get_cash_balance()
        
        # 2. 포트폴리오 현황
        portfolio = database.get_active_portfolio(session)
        stock_valuation = 0
        portfolio_details = []
        
        for item in portfolio:
            stock_code = item['code']
            snapshot = self.kis.get_stock_snapshot(stock_code)
            current_price = float(snapshot.get('price', item['avg_price'])) if snapshot else float(item['avg_price'])
            
            quantity = int(item['quantity'])
            valuation = current_price * quantity
            stock_valuation += valuation
            
            profit_pct = ((current_price - item['avg_price']) / item['avg_price']) * 100
            profit_amount = (current_price - item['avg_price']) * quantity
            
            portfolio_details.append({
                'name': item['name'],
                'code': stock_code,
                'quantity': quantity,
                'avg_price': item['avg_price'],
                'current_price': current_price,
                'valuation': valuation,
                'profit_pct': profit_pct,
                'profit_amount': profit_amount
            })
        
        total_aum = cash_balance + stock_valuation
        
        # 3. 금일 거래 내역
        today_trades = database.get_trade_logs(session, date=today_str)
        trade_summary = self._summarize_trades(today_trades)
        
        # 4. Watchlist 현황
        try:
            watchlist = database.get_watchlist_all(session)
            watchlist_summary = [{
                'name': w.get('name', 'N/A'),
                'code': w.get('code', 'N/A'),
                'llm_score': w.get('llm_score', 0),
                'filter_reason': w.get('filter_reason', 'N/A')[:100] if w.get('filter_reason') else 'N/A'
            } for w in watchlist[:10]]
        except:
            watchlist_summary = []
        
        # 5. 최근 뉴스
        try:
            recent_news = self._get_recent_news_sentiment(session)
        except:
            recent_news = []
            
        # 6. 어제 대비 AUM 변동
        try:
            yesterday_aum = self._get_yesterday_aum(session)
            daily_change_pct = ((total_aum - yesterday_aum) / yesterday_aum * 100) if yesterday_aum > 0 else 0
        except:
            yesterday_aum = total_aum
            daily_change_pct = 0
            
        return {
            'date': today_str,
            'total_aum': total_aum,
            'cash_balance': cash_balance,
            'stock_valuation': stock_valuation,
            'cash_ratio': (cash_balance / total_aum * 100) if total_aum > 0 else 0,
            'portfolio': portfolio_details,
            'trades': trade_summary,
            'watchlist': watchlist_summary,
            'recent_news': recent_news,
            'daily_change_pct': daily_change_pct,
            'yesterday_aum': yesterday_aum
        }

    def _format_market_summary(self, data: Dict) -> str:
        """시장 정보 데이터 (LLM 입력용 Text)"""
        # 여기서는 간단히 자산 현황과 뉴스 기반으로 요약
        # 실제로는 지수 정보 등을 KIS에서 가져오면 더 좋음
        
        news_text = "\n".join([f"- {n['name']}: {n['headline']} (감성: {n['score']})" for n in data['recent_news']])
        
        summary = f"""
        [자산 현황]
        - 날짜: {data['date']}
        - 총 운용자산: {data['total_aum']:,.0f}원 (변동: {data['daily_change_pct']:+.2f}%)
        - 현금 비중: {data['cash_ratio']:.1f}%

        [주요 뉴스]
        {news_text if news_text else "특이 뉴스 없음"}
        """
        return summary

    def _format_execution_log(self, data: Dict) -> str:
        """실행 로그 데이터 (LLM 입력용 Text)"""
        trades = data['trades']
        portfolio = data['portfolio']
        
        trade_logs = []
        if trades['buy_count'] > 0 or trades['sell_count'] > 0:
            for t in trades['details']:
                action = "매수" if t['action'] == "BUY" else "매도"
                trade_logs.append(f"- {action}: {t['name']} {t['quantity']}주 ({t['reason']})")
        else:
            trade_logs.append("금일 체결된 매매 없음")
            
        pf_logs = []
        for p in portfolio:
            status = "수익중" if p['profit_pct'] > 0 else "손실중"
            pf_logs.append(f"- {p['name']}: {status} ({p['profit_pct']:+.2f}%)")
            
        return f"""
        [매매 수행]
        {chr(10).join(trade_logs)}
        
        [현재 포트폴리오]
        {chr(10).join(pf_logs) if pf_logs else "보유 종목 없음"}
        """

    def _summarize_trades(self, trades: List) -> Dict:
        """거래 내역 요약"""
        buy_count = 0
        sell_count = 0
        total_buy_amount = 0
        total_sell_amount = 0
        realized_profit = 0
        trade_details = []
        
        for trade in trades:
            action = trade.get('action', '')
            amount = float(trade.get('amount', 0))
            
            if action == 'BUY':
                buy_count += 1
                total_buy_amount += amount
            elif action == 'SELL':
                sell_count += 1
                total_sell_amount += amount
                realized_profit += float(trade.get('profit_amount', 0))
            
            trade_details.append({
                'action': action,
                'name': trade.get('stock_name', 'N/A'),
                'quantity': trade.get('quantity', 0),
                'price': trade.get('price', 0),
                'amount': amount,
                'reason': trade.get('reason', 'N/A')[:50] if trade.get('reason') else 'N/A'
            })
        
        return {
            'buy_count': buy_count,
            'sell_count': sell_count,
            'total_buy_amount': total_buy_amount,
            'total_sell_amount': total_sell_amount,
            'realized_profit': realized_profit,
            'details': trade_details[:10]  # 최근 10건만
        }
    
    def _get_recent_news_sentiment(self, session) -> List[Dict]:
        """최근 뉴스 감성 점수 조회"""
        from sqlalchemy import text
        try:
            result = session.execute(text("""
                SELECT STOCK_CODE, STOCK_NAME, SENTIMENT_SCORE, HEADLINE
                FROM NEWS_SENTIMENT 
                WHERE CREATED_AT >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                ORDER BY SENTIMENT_SCORE DESC
                LIMIT 5
            """))
            rows = result.fetchall()
            
            return [{
                'code': row[0],
                'name': row[1],
                'score': row[2],
                'headline': row[3][:50] if row[3] else 'N/A'
            } for row in rows]
        except:
            return []
    
    def _get_yesterday_aum(self, session) -> float:
        """어제의 총 자산 조회"""
        from sqlalchemy import text
        try:
            result = session.execute(text("SELECT CONFIG_VALUE FROM CONFIG WHERE CONFIG_KEY = 'DAILY_AUM_YESTERDAY'"))
            row = result.fetchone()
            return float(row[0]) if row else 0
        except:
            return 0
    
    def _format_basic_message(self, data: Dict) -> str:
        """LLM 없이 기본 메시지 포맷팅 (폴백)"""
        
        profit = data['trades']['realized_profit']
        profit_emoji = "🔴" if profit > 0 else ("🔵" if profit < 0 else "⚪")
        
        lines = []
        lines.append(f"📅 *Daily Briefing ({data['date']})*")
        lines.append("")
        
        lines.append("💰 *자산 현황*")
        lines.append(f"• 총 운용 자산: *{data['total_aum']:,.0f}원*")
        lines.append(f"• 현금: {data['cash_balance']:,.0f}원 ({data['cash_ratio']:.1f}%)")
        lines.append(f"• 주식: {data['stock_valuation']:,.0f}원")
        lines.append(f"• 어제 대비: {data['daily_change_pct']:+.2f}%")
        lines.append("")
        
        lines.append(f"📊 *금일 성과*")
        lines.append(f"• 실현 손익: {profit_emoji} *{profit:,.0f}원*")
        lines.append(f"• 거래: 매수 {data['trades']['buy_count']}건 / 매도 {data['trades']['sell_count']}건")
        lines.append("")
        
        if data['portfolio']:
            lines.append("💼 *보유 종목*")
            for item in data['portfolio'][:5]:
                p_emoji = "🔴" if item['profit_pct'] > 0 else ("🔵" if item['profit_pct'] < 0 else "⚪")
                lines.append(f"{p_emoji} {item['name']}: {item['profit_pct']:+.2f}%")
        
        lines.append("")
        lines.append("🤖 *Jennie's Comment (Basic Mode)*")
        lines.append("오늘은 기본적인 요약만 전달드려요. 그래도 화이팅입니다! 💪")
            
        return "\n".join(lines)
