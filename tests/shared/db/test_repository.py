"""
tests/shared/db/test_repository.py - SQLAlchemy Repository Unit Tests
======================================================================

shared/db/repository.py 모듈의 Unit Test입니다.
In-memory SQLite를 사용하여 실제 DB 없이 테스트합니다.

실행 방법:
    pytest tests/shared/db/test_repository.py -v

커버리지 포함:
    pytest tests/shared/db/test_repository.py -v --cov=shared.db.repository --cov-report=term-missing
"""

import json
import pytest
from datetime import datetime, timezone, timedelta


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def db_session(in_memory_db):
    """In-memory SQLite 세션 fixture"""
    return in_memory_db["session"]


@pytest.fixture
def session_with_watchlist(db_session):
    """WatchList 데이터가 있는 세션"""
    from shared.db.models import WatchList
    
    # 샘플 WatchList 데이터 생성
    items = [
        WatchList(
            stock_code="005930",
            stock_name="삼성전자",
            is_tradable=1,
            per=10.5,
            pbr=1.2,
            market_cap=400000000000000,
            llm_score=85,
            llm_reason="반도체 업황 개선 기대\n\n[LLM_METADATA]{\"llm_grade\": \"A\", \"bear_strategy\": \"HOLD\"}"
        ),
        WatchList(
            stock_code="000660",
            stock_name="SK하이닉스",
            is_tradable=1,
            per=8.3,
            pbr=1.5,
            market_cap=100000000000000,
            llm_score=78,
            llm_reason="HBM 수요 증가"
        ),
        WatchList(
            stock_code="035420",
            stock_name="NAVER",
            is_tradable=0,  # 거래 불가
            per=25.0,
            pbr=2.0,
            market_cap=50000000000000,
            llm_score=65,
            llm_reason=None  # Null 케이스
        ),
    ]
    
    for item in items:
        db_session.add(item)
    db_session.commit()
    
    yield db_session


@pytest.fixture
def session_with_portfolio(db_session):
    """Portfolio 데이터가 있는 세션"""
    from shared.db.models import Portfolio
    
    items = [
        Portfolio(
            stock_code="005930",
            stock_name="삼성전자",
            quantity=100,
            average_buy_price=70000,
            total_buy_amount=7000000,
            current_high_price=75000,
            status="HOLDING",
            sell_state="INITIAL",
            stop_loss_price=63000,
        ),
        Portfolio(
            stock_code="000660",
            stock_name="SK하이닉스",
            quantity=50,
            average_buy_price=150000,
            total_buy_amount=7500000,
            current_high_price=160000,
            status="HOLDING",
            sell_state="TRAILING",
            stop_loss_price=140000,
        ),
        Portfolio(
            stock_code="035420",
            stock_name="NAVER",
            quantity=10,
            average_buy_price=200000,
            total_buy_amount=2000000,
            current_high_price=200000,
            status="SOLD",  # 매도 완료
            sell_state="SOLD",
            stop_loss_price=180000,
        ),
    ]
    
    for item in items:
        db_session.add(item)
    db_session.commit()
    
    yield db_session


@pytest.fixture
def session_with_trade_logs(db_session):
    """TradeLog 데이터가 있는 세션"""
    from shared.db.models import TradeLog
    
    now = datetime.now(timezone.utc)
    
    items = [
        # 오늘 매수
        TradeLog(
            stock_code="005930",
            trade_type="BUY",
            quantity=100,
            price=70000,
            trade_timestamp=now,
            key_metrics_json=json.dumps({"signal": "STRONG_BUY"})
        ),
        # 오늘 매수
        TradeLog(
            stock_code="000660",
            trade_type="BUY",
            quantity=50,
            price=150000,
            trade_timestamp=now - timedelta(minutes=1),
            key_metrics_json=json.dumps({"signal": "BUY"})
        ),
        # 어제 매도
        TradeLog(
            stock_code="035420",
            trade_type="SELL",
            quantity=10,
            price=210000,
            trade_timestamp=now - timedelta(days=1),
            key_metrics_json=json.dumps({"profit_amount": 100000})
        ),
        # 3일 전 매수
        TradeLog(
            stock_code="005380",
            trade_type="BUY",
            quantity=20,
            price=50000,
            trade_timestamp=now - timedelta(days=3),
            key_metrics_json=None
        ),
    ]
    
    for item in items:
        db_session.add(item)
    db_session.commit()
    
    yield db_session


@pytest.fixture
def session_with_config(db_session):
    """Config 데이터가 있는 세션"""
    from shared.db.models import Config
    
    items = [
        Config(
            config_key="TRADING_MODE",
            config_value="MOCK",
            description="거래 모드"
        ),
        Config(
            config_key="CASH_BALANCE",
            config_value="10000000",
            description="현금 잔고"
        ),
        Config(
            config_key="STRATEGY_PRESET",
            config_value=json.dumps({"name": "AGGRESSIVE", "params": {"take_profit": 0.1}}),
            description="전략 프리셋 JSON"
        ),
    ]
    
    for item in items:
        db_session.add(item)
    db_session.commit()
    
    yield db_session


# ============================================================================
# Tests: _parse_llm_reason (순수 함수)
# ============================================================================

class TestParseLlmReason:
    """LLM Reason 파싱 테스트"""
    
    def test_parse_reason_with_metadata(self):
        """메타데이터 포함된 reason 파싱"""
        from shared.db.repository import _parse_llm_reason
        
        raw = '반도체 업황 개선\n\n[LLM_METADATA]{"llm_grade": "A", "bear_strategy": "HOLD"}'
        
        reason, metadata = _parse_llm_reason(raw)
        
        assert reason == "반도체 업황 개선"
        assert metadata["llm_grade"] == "A"
        assert metadata["bear_strategy"] == "HOLD"
    
    def test_parse_reason_without_metadata(self):
        """메타데이터 없는 reason 파싱"""
        from shared.db.repository import _parse_llm_reason
        
        raw = "단순한 이유입니다."
        
        reason, metadata = _parse_llm_reason(raw)
        
        assert reason == "단순한 이유입니다."
        assert metadata == {}
    
    def test_parse_reason_none(self):
        """None 입력"""
        from shared.db.repository import _parse_llm_reason
        
        reason, metadata = _parse_llm_reason(None)
        
        assert reason == ""
        assert metadata == {}
    
    def test_parse_reason_empty_string(self):
        """빈 문자열 입력"""
        from shared.db.repository import _parse_llm_reason
        
        reason, metadata = _parse_llm_reason("")
        
        assert reason == ""
        assert metadata == {}
    
    def test_parse_reason_invalid_json(self):
        """유효하지 않은 JSON 메타데이터"""
        from shared.db.repository import _parse_llm_reason
        
        raw = "이유\n\n[LLM_METADATA]{invalid json}"
        
        reason, metadata = _parse_llm_reason(raw)
        
        assert reason == "이유"
        assert metadata == {}  # 파싱 실패 시 빈 딕셔너리


# ============================================================================
# Tests: WatchList
# ============================================================================

class TestWatchList:
    """WatchList 관련 테스트"""
    
    def test_get_active_watchlist(self, session_with_watchlist):
        """WatchList 전체 조회"""
        from shared.db.repository import get_active_watchlist
        
        watchlist = get_active_watchlist(session_with_watchlist)
        
        assert len(watchlist) == 3
        assert "005930" in watchlist
        assert watchlist["005930"]["name"] == "삼성전자"
        assert watchlist["005930"]["is_tradable"] is True
        assert watchlist["005930"]["llm_score"] == 85
    
    def test_get_active_watchlist_with_metadata(self, session_with_watchlist):
        """메타데이터 포함된 WatchList 조회"""
        from shared.db.repository import get_active_watchlist
        
        watchlist = get_active_watchlist(session_with_watchlist)
        
        # 삼성전자는 메타데이터가 있음
        samsung = watchlist["005930"]
        assert samsung["llm_grade"] == "A"
        assert samsung["bear_strategy"] == "HOLD"
        assert "반도체 업황" in samsung["llm_reason"]
    
    def test_get_active_watchlist_null_reason(self, session_with_watchlist):
        """reason이 NULL인 경우"""
        from shared.db.repository import get_active_watchlist
        
        watchlist = get_active_watchlist(session_with_watchlist)
        
        # NAVER는 llm_reason이 NULL
        naver = watchlist["035420"]
        assert naver["llm_reason"] == ""
        assert naver["llm_metadata"] == {}
    
    def test_get_active_watchlist_empty(self, db_session):
        """비어있는 WatchList 조회"""
        from shared.db.repository import get_active_watchlist
        
        watchlist = get_active_watchlist(db_session)
        
        assert watchlist == {}
    
    def test_get_watchlist_all(self, session_with_watchlist):
        """WatchList 전체 조회 (정렬, 제한)"""
        from shared.db.repository import get_watchlist_all
        
        # limit=2로 상위 2개만
        result = get_watchlist_all(session_with_watchlist, limit=2)
        
        assert len(result) == 2
        # llm_score 내림차순 정렬
        assert result[0].llm_score >= result[1].llm_score


# ============================================================================
# Tests: Portfolio
# ============================================================================

class TestPortfolio:
    """Portfolio 관련 테스트"""
    
    def test_get_active_portfolio(self, session_with_portfolio):
        """HOLDING 상태의 포트폴리오 조회"""
        from shared.db.repository import get_active_portfolio
        
        portfolio = get_active_portfolio(session_with_portfolio)
        
        # SOLD는 제외되어야 함
        assert len(portfolio) == 2
        codes = [p["code"] for p in portfolio]
        assert "005930" in codes
        assert "000660" in codes
        assert "035420" not in codes  # SOLD 상태
    
    def test_get_active_portfolio_fields(self, session_with_portfolio):
        """포트폴리오 필드 확인"""
        from shared.db.repository import get_active_portfolio
        
        portfolio = get_active_portfolio(session_with_portfolio)
        
        samsung = next(p for p in portfolio if p["code"] == "005930")
        assert samsung["name"] == "삼성전자"
        assert samsung["quantity"] == 100
        assert samsung["avg_price"] == 70000
        assert samsung["high_price"] == 75000
        assert samsung["sell_state"] == "INITIAL"
        assert samsung["stop_loss_price"] == 63000
    
    def test_get_active_portfolio_empty(self, db_session):
        """비어있는 포트폴리오 조회"""
        from shared.db.repository import get_active_portfolio
        
        portfolio = get_active_portfolio(db_session)
        
        assert portfolio == []
    
    def test_get_portfolio_summary_empty(self, db_session):
        """비어있는 포트폴리오 요약"""
        from shared.db.repository import get_portfolio_summary
        
        summary = get_portfolio_summary(db_session, use_realtime=False)
        
        assert summary["total_value"] == 0
        assert summary["positions_count"] == 0
    
    def test_get_portfolio_summary_with_data(self, session_with_portfolio):
        """포트폴리오 요약 (실시간 조회 비활성화)"""
        from shared.db.repository import get_portfolio_summary
        
        summary = get_portfolio_summary(session_with_portfolio, use_realtime=False)
        
        # 삼성: 100 * 70000 = 7,000,000
        # SK: 50 * 150000 = 7,500,000
        # Total invested: 14,500,000
        assert summary["total_invested"] == 14500000
        assert summary["positions_count"] == 2


# ============================================================================
# Tests: TradeLog
# ============================================================================

class TestTradeLog:
    """TradeLog 관련 테스트"""
    
    def test_get_today_total_buy_amount(self, session_with_trade_logs):
        """오늘 총 매수 금액"""
        from shared.db.repository import get_today_total_buy_amount
        
        total = get_today_total_buy_amount(session_with_trade_logs)
        
        # 오늘 매수: 100 * 70000 + 50 * 150000 = 14,500,000
        assert total == 14500000
    
    def test_get_today_buy_count(self, session_with_trade_logs):
        """오늘 매수 건수"""
        from shared.db.repository import get_today_buy_count
        
        count = get_today_buy_count(session_with_trade_logs)
        
        assert count == 2  # 오늘 매수 2건
    
    def test_get_trade_logs_today(self, session_with_trade_logs):
        """오늘 거래 내역"""
        from shared.db.repository import get_trade_logs
        
        logs = get_trade_logs(session_with_trade_logs)
        
        # 오늘 거래: 2건 (매수 2건)
        assert len(logs) == 2
        assert all(log["action"] == "BUY" for log in logs)
    
    def test_get_trade_logs_specific_date(self, session_with_trade_logs):
        """특정 날짜 거래 내역"""
        from shared.db.repository import get_trade_logs
        
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        logs = get_trade_logs(session_with_trade_logs, date_str=yesterday)
        
        # 어제 거래: 1건 (매도)
        assert len(logs) == 1
        assert logs[0]["action"] == "SELL"
        assert logs[0]["profit_amount"] == 100000  # key_metrics에서 추출
    
    def test_was_traded_recently_true(self, session_with_trade_logs):
        """최근 거래 있음"""
        from shared.db.repository import was_traded_recently
        
        result = was_traded_recently(session_with_trade_logs, "005930", hours=24)
        
        assert result is True
    
    def test_was_traded_recently_false(self, session_with_trade_logs):
        """최근 거래 없음"""
        from shared.db.repository import was_traded_recently
        
        # 3일 전에 거래한 종목, 24시간 이내 거래 확인
        result = was_traded_recently(session_with_trade_logs, "005380", hours=24)
        
        assert result is False  # 3일 전이라 24시간 외
    
    def test_was_traded_recently_longer_window(self, session_with_trade_logs):
        """더 긴 시간 윈도우"""
        from shared.db.repository import was_traded_recently
        
        # 3일 전 거래, 96시간(4일) 이내 확인
        result = was_traded_recently(session_with_trade_logs, "005380", hours=96)
        
        assert result is True
    
    def test_get_recently_traded_stocks_batch(self, session_with_trade_logs):
        """배치 최근 거래 조회"""
        from shared.db.repository import get_recently_traded_stocks_batch
        
        codes = ["005930", "000660", "005380", "999999"]
        result = get_recently_traded_stocks_batch(session_with_trade_logs, codes, hours=24)
        
        # 24시간 이내 거래: 005930, 000660
        assert "005930" in result
        assert "000660" in result
        assert "005380" not in result  # 3일 전
        assert "999999" not in result  # 거래 기록 없음
    
    def test_get_recently_traded_stocks_batch_empty_input(self, session_with_trade_logs):
        """빈 입력"""
        from shared.db.repository import get_recently_traded_stocks_batch
        
        result = get_recently_traded_stocks_batch(session_with_trade_logs, [], hours=24)
        
        assert result == set()
    
    def test_get_recent_trades(self, session_with_trade_logs):
        """최근 거래 조회 (Dashboard용)"""
        from shared.db.repository import get_recent_trades
        
        trades = get_recent_trades(session_with_trade_logs, limit=2)
        
        assert len(trades) == 2
        # 최신순 정렬 확인
        assert trades[0].trade_timestamp >= trades[1].trade_timestamp


# ============================================================================
# Tests: Config CRUD
# ============================================================================

class TestConfig:
    """CONFIG 테이블 CRUD 테스트"""
    
    def test_get_config_existing(self, session_with_config):
        """존재하는 설정값 조회"""
        from shared.db.repository import get_config
        
        value = get_config(session_with_config, "TRADING_MODE")
        
        assert value == "MOCK"
    
    def test_get_config_not_found(self, session_with_config):
        """존재하지 않는 설정값 조회"""
        from shared.db.repository import get_config
        
        value = get_config(session_with_config, "NON_EXISTENT_KEY", silent=True)
        
        assert value is None
    
    def test_get_config_json_value(self, session_with_config):
        """JSON 값 조회"""
        from shared.db.repository import get_config
        
        value = get_config(session_with_config, "STRATEGY_PRESET")
        data = json.loads(value)
        
        assert data["name"] == "AGGRESSIVE"
        assert data["params"]["take_profit"] == 0.1
    
    def test_set_config_new(self, db_session):
        """새 설정값 저장"""
        from shared.db.repository import set_config, get_config
        
        result = set_config(db_session, "NEW_KEY", "NEW_VALUE", "새 설정")
        
        assert result is True
        assert get_config(db_session, "NEW_KEY") == "NEW_VALUE"
    
    def test_set_config_update(self, session_with_config):
        """기존 설정값 업데이트"""
        from shared.db.repository import set_config, get_config
        
        # 기존값 확인
        assert get_config(session_with_config, "TRADING_MODE") == "MOCK"
        
        # 업데이트
        result = set_config(session_with_config, "TRADING_MODE", "REAL")
        
        assert result is True
        assert get_config(session_with_config, "TRADING_MODE") == "REAL"
    
    def test_delete_config_existing(self, session_with_config):
        """존재하는 설정값 삭제"""
        from shared.db.repository import delete_config, get_config
        
        result = delete_config(session_with_config, "TRADING_MODE")
        
        assert result is True
        assert get_config(session_with_config, "TRADING_MODE", silent=True) is None
    
    def test_delete_config_not_found(self, session_with_config):
        """존재하지 않는 설정값 삭제"""
        from shared.db.repository import delete_config
        
        result = delete_config(session_with_config, "NON_EXISTENT_KEY")
        
        assert result is False


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_portfolio_with_null_values(self, db_session):
        """NULL 값이 있는 포트폴리오"""
        from shared.db.models import Portfolio
        from shared.db.repository import get_active_portfolio
        
        # NULL 값이 많은 포트폴리오 생성
        db_session.add(Portfolio(
            stock_code="TEST01",
            stock_name="테스트",
            quantity=10,
            average_buy_price=None,  # NULL
            total_buy_amount=None,
            current_high_price=None,
            status="HOLDING",
            sell_state=None,
            stop_loss_price=None,
        ))
        db_session.commit()
        
        portfolio = get_active_portfolio(db_session)
        
        assert len(portfolio) == 1
        assert portfolio[0]["avg_price"] == 0.0  # None -> 0.0
        assert portfolio[0]["high_price"] == 0.0
        assert portfolio[0]["stop_loss_price"] == 0.0
    
    def test_trade_log_with_invalid_json(self, db_session):
        """유효하지 않은 JSON key_metrics"""
        from shared.db.models import TradeLog
        from shared.db.repository import get_trade_logs
        
        db_session.add(TradeLog(
            stock_code="TEST01",
            trade_type="BUY",
            quantity=10,
            price=10000,
            trade_timestamp=datetime.now(timezone.utc),
            key_metrics_json="{invalid json}",  # 잘못된 JSON
        ))
        db_session.commit()
        
        logs = get_trade_logs(db_session)
        
        # JSON 파싱 실패해도 에러 없이 처리
        assert len(logs) == 1
        assert logs[0]["profit_amount"] == 0.0  # 기본값
    
    def test_watchlist_numeric_fields(self, db_session):
        """WatchList 숫자 필드 변환"""
        from shared.db.models import WatchList
        from shared.db.repository import get_active_watchlist
        
        db_session.add(WatchList(
            stock_code="TEST01",
            stock_name="테스트",
            is_tradable=1,
            per=None,
            pbr=None,
            market_cap=None,
            llm_score=None,
        ))
        db_session.commit()
        
        watchlist = get_active_watchlist(db_session)
        
        assert watchlist["TEST01"]["per"] is None
        assert watchlist["TEST01"]["pbr"] is None
        assert watchlist["TEST01"]["market_cap"] is None
        assert watchlist["TEST01"]["llm_score"] == 0  # None -> 0

