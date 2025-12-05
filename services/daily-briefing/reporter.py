# services/daily-briefing/reporter.py
# Version: v4.0
# Daily Briefing Service - LLM 기반 일일 보고서 생성
# LLM: Claude Opus 4.5 (최고 품질 보고서)

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import anthropic

import shared.database as database
import shared.auth as auth

logger = logging.getLogger(__name__)


class DailyReporter:
    """LLM 기반 일일 브리핑 리포터 (Claude Opus 4.5)"""
    
    def __init__(self, kis_client, telegram_bot):
        self.kis = kis_client
        self.bot = telegram_bot
        self.claude_client = None
        self._init_claude()
        
    def _init_claude(self):
        """Claude API 클라이언트 초기화"""
        try:
            # secrets.json에서 API 키 로드
            api_key = auth._load_local_secrets().get("claude-api-key")
            if not api_key:
                api_key = os.getenv("ANTHROPIC_API_KEY")
            
            if api_key:
                self.claude_client = anthropic.Anthropic(api_key=api_key)
                logger.info("✅ Claude Opus 4.5 클라이언트 초기화 완료")
            else:
                logger.warning("⚠️ Claude API 키가 없습니다. 기본 보고서로 대체됩니다.")
        except Exception as e:
            logger.error(f"❌ Claude 클라이언트 초기화 실패: {e}")
            self.claude_client = None
        
    def create_and_send_report(self):
        """리포트를 생성하고 텔레그램으로 발송합니다."""
        try:
            with database.get_db_connection_context() as db_conn:
                # 1. 데이터 수집
                report_data = self._collect_report_data(db_conn)
                
                # 2. LLM 기반 보고서 생성
                if self.claude_client:
                    message = self._generate_llm_report(report_data)
                else:
                    message = self._format_basic_message(report_data)
                
                # 3. 발송
                return self.bot.send_message(message)
                
        except Exception as e:
            logger.error(f"리포트 생성 중 오류: {e}", exc_info=True)
            return False
    
    def _collect_report_data(self, db_conn) -> Dict:
        """보고서 생성에 필요한 모든 데이터 수집"""
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        # 1. 현금 잔고
        cash_balance = self.kis.get_cash_balance()
        
        # 2. 포트폴리오 현황
        portfolio = database.get_active_portfolio(db_conn)
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
        today_trades = database.get_trade_logs(db_conn, date=today_str)
        trade_summary = self._summarize_trades(today_trades)
        
        # 4. Watchlist 현황 (Scout가 선정한 종목들)
        try:
            watchlist = database.get_watchlist_all(db_conn)
            watchlist_summary = [{
                'name': w.get('name', 'N/A'),
                'code': w.get('code', 'N/A'),
                'llm_score': w.get('llm_score', 0),
                'filter_reason': w.get('filter_reason', 'N/A')[:100] if w.get('filter_reason') else 'N/A'
            } for w in watchlist[:10]]  # 상위 10개만
        except:
            watchlist_summary = []
        
        # 5. 최근 뉴스 감성 (있으면)
        try:
            recent_news = self._get_recent_news_sentiment(db_conn)
        except:
            recent_news = []
        
        # 6. 어제 대비 성과 (있으면)
        try:
            yesterday_aum = self._get_yesterday_aum(db_conn)
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
    
    def _get_recent_news_sentiment(self, db_conn) -> List[Dict]:
        """최근 뉴스 감성 점수 조회"""
        try:
            cursor = db_conn.cursor()
            query = """
                SELECT STOCK_CODE, STOCK_NAME, SENTIMENT_SCORE, HEADLINE
                FROM NEWS_SENTIMENT 
                WHERE CREATED_AT >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                ORDER BY SENTIMENT_SCORE DESC
                LIMIT 5
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            
            return [{
                'code': row[0],
                'name': row[1],
                'score': row[2],
                'headline': row[3][:50] if row[3] else 'N/A'
            } for row in rows]
        except:
            return []
    
    def _get_yesterday_aum(self, db_conn) -> float:
        """어제의 총 자산 조회 (간단히 CONFIG에서)"""
        try:
            cursor = db_conn.cursor()
            cursor.execute("SELECT CONFIG_VALUE FROM CONFIG WHERE CONFIG_KEY = 'DAILY_AUM_YESTERDAY'")
            row = cursor.fetchone()
            cursor.close()
            return float(row[0]) if row else 0
        except:
            return 0
    
    def _generate_llm_report(self, data: Dict) -> str:
        """Claude Opus 4.5를 사용하여 일일 보고서 생성"""
        
        # 프롬프트 구성
        prompt = f"""당신은 'Supreme Jennie'입니다. 영석님의 AI 투자 비서로서, 오늘 하루의 투자 활동을 분석하고 
따뜻하면서도 전문적인 일일 브리핑을 작성해주세요.

## 오늘의 데이터 ({data['date']})

### 💰 자산 현황
- 총 운용 자산(AUM): {data['total_aum']:,.0f}원
- 현금 잔고: {data['cash_balance']:,.0f}원 ({data['cash_ratio']:.1f}%)
- 주식 평가액: {data['stock_valuation']:,.0f}원
- 어제 대비 변동: {data['daily_change_pct']:+.2f}%

### 📊 금일 거래 활동 (모두 체결 완료!)
- 매수 체결: {data['trades']['buy_count']}건 (총 {data['trades']['total_buy_amount']:,.0f}원)
- 매도 체결: {data['trades']['sell_count']}건 (총 {data['trades']['total_sell_amount']:,.0f}원)
- 실현 손익: {data['trades']['realized_profit']:,.0f}원
{self._format_trade_details_for_llm(data['trades']['details'])}

### 💼 보유 종목
{self._format_portfolio_for_llm(data['portfolio'])}

### 🎯 Scout 추천 종목 (Watchlist)
{self._format_watchlist_for_llm(data['watchlist'])}

### 📰 최근 주요 뉴스 감성
{self._format_news_for_llm(data['recent_news'])}

---

## 요청사항

위 데이터를 바탕으로 텔레그램용 일일 브리핑을 작성해주세요.

### 작성 가이드라인:
1. **톤**: Jennie답게 친근하면서도 전문적으로 (이모지 적절히 사용)
2. **구조**: 
   - 📅 인사 + 날짜
   - 💰 자산 현황 요약
   - 📊 금일 성과 분석 (좋았던 점, 아쉬운 점)
   - 💼 보유 종목 코멘트 (주요 종목 2-3개)
   - 🎯 내일 전략 제안
   - 💕 마무리 인사

3. **분량**: 텔레그램에 적합하게 500자 내외
4. **Markdown**: 텔레그램 Markdown 형식 사용 (*bold*, `code` 등)

### 특별 요청:
- 숫자는 읽기 쉽게 천 단위 콤마 사용
- 수익/손실에 따라 적절한 감정 표현
- 구체적인 종목명과 수치 언급
- 영석님을 격려하는 따뜻한 멘트로 마무리
"""

        try:
            response = self.claude_client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1500,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            report = response.content[0].text
            logger.info("✅ Claude Opus 4.5 일일 보고서 생성 완료")
            return report
            
        except Exception as e:
            logger.error(f"❌ LLM 보고서 생성 실패: {e}")
            return self._format_basic_message(data)
    
    def _format_portfolio_for_llm(self, portfolio: List[Dict]) -> str:
        """포트폴리오를 LLM 프롬프트용으로 포맷"""
        if not portfolio:
            return "- 보유 종목 없음"
        
        lines = []
        for item in portfolio:
            emoji = "🔴" if item['profit_pct'] > 0 else ("🔵" if item['profit_pct'] < 0 else "⚪")
            lines.append(
                f"- {item['name']}({item['code']}): "
                f"{item['quantity']}주, 평가 {item['valuation']:,.0f}원, "
                f"수익률 {item['profit_pct']:+.2f}% ({emoji})"
            )
        return "\n".join(lines)
    
    def _format_watchlist_for_llm(self, watchlist: List[Dict]) -> str:
        """Watchlist를 LLM 프롬프트용으로 포맷"""
        if not watchlist:
            return "- 추천 종목 없음"
        
        lines = []
        for item in watchlist:
            lines.append(
                f"- {item['name']}({item['code']}): "
                f"LLM 점수 {item['llm_score']}점 - {item['filter_reason'][:50]}..."
            )
        return "\n".join(lines)
    
    def _format_news_for_llm(self, news: List[Dict]) -> str:
        """뉴스를 LLM 프롬프트용으로 포맷"""
        if not news:
            return "- 특이 뉴스 없음"
        
        lines = []
        for item in news:
            emoji = "🔥" if item['score'] >= 70 else ("⚠️" if item['score'] <= 30 else "📰")
            lines.append(
                f"{emoji} {item['name']}: 감성 {item['score']}점 - {item['headline']}"
            )
        return "\n".join(lines)
    
    def _format_trade_details_for_llm(self, details: List[Dict]) -> str:
        """거래 상세 내역을 LLM 프롬프트용으로 포맷"""
        if not details:
            return ""
        
        lines = ["#### 체결 상세:"]
        for trade in details:
            action_emoji = "🟢" if trade['action'] == 'BUY' else "🔴"
            action_kr = "매수" if trade['action'] == 'BUY' else "매도"
            lines.append(
                f"  {action_emoji} [{action_kr} 체결] {trade['name']}: "
                f"{trade['quantity']}주 x {trade['price']:,.0f}원 = {trade['amount']:,.0f}원"
            )
        return "\n".join(lines)
    
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
        lines.append("🤖 *Jennie's Comment*")
        if profit > 0:
            lines.append("오늘도 수익을 냈어요! 🎉")
        elif profit < 0:
            lines.append("내일은 더 잘할게요! 💪")
        else:
            lines.append("기회를 노리는 중이에요! 👀")
            
        return "\n".join(lines)
