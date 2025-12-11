"""
tests/shared/test_redis_cache.py - Redis 캐시 모듈 Unit Tests
==============================================================

shared/redis_cache.py 모듈의 Unit Test입니다.
fakeredis를 사용하여 실제 Redis 없이 테스트합니다.

실행 방법:
    pytest tests/shared/test_redis_cache.py -v

커버리지 포함:
    pytest tests/shared/test_redis_cache.py -v --cov=shared.redis_cache --cov-report=term-missing
"""

import json
import pytest
from datetime import datetime, timezone, timedelta


class TestSentimentScore:
    """감성 점수 (Sentiment Score) 관련 테스트"""
    
    def test_set_sentiment_score_new_stock(self, fake_redis):
        """새 종목에 감성 점수 저장"""
        from shared.redis_cache import set_sentiment_score, get_sentiment_score
        
        # Given: 새 종목
        stock_code = "005930"
        score = 75
        reason = "긍정적 뉴스"
        
        # When: 점수 저장
        result = set_sentiment_score(stock_code, score, reason, redis_client=fake_redis)
        
        # Then: 성공하고 점수가 그대로 저장됨 (EMA 적용 안됨)
        assert result is True
        data = get_sentiment_score(stock_code, redis_client=fake_redis)
        assert data["score"] == 75  # 새 종목은 100% 반영
        assert reason in data["reason"]
    
    def test_set_sentiment_score_ema_applied(self, fake_redis):
        """기존 종목에 감성 점수 저장 시 EMA 적용"""
        from shared.redis_cache import set_sentiment_score, get_sentiment_score
        
        # Given: 기존 점수가 있는 종목
        stock_code = "005930"
        set_sentiment_score(stock_code, 50, "기존 뉴스", redis_client=fake_redis)
        
        # When: 새 점수 저장 (100점)
        set_sentiment_score(stock_code, 100, "새 뉴스", redis_client=fake_redis)
        
        # Then: EMA 적용됨 (기존 50% + 신규 50% = 50*0.5 + 100*0.5 = 75)
        data = get_sentiment_score(stock_code, redis_client=fake_redis)
        assert data["score"] == 75.0  # EMA: 50*0.5 + 100*0.5 = 75
    
    def test_get_sentiment_score_not_found(self, fake_redis):
        """존재하지 않는 종목 조회 시 기본값 반환"""
        from shared.redis_cache import get_sentiment_score
        
        # When: 없는 종목 조회
        data = get_sentiment_score("999999", redis_client=fake_redis)
        
        # Then: 기본값 반환
        assert data["score"] == 50
        assert "데이터 없음" in data["reason"] or "중립" in data["reason"]
    
    def test_get_sentiment_score_with_data(self, fake_redis_with_data):
        """미리 저장된 데이터 조회"""
        from shared.redis_cache import get_sentiment_score
        
        # Given: fake_redis_with_data fixture에 005930 데이터가 있음
        
        # When: 조회
        data = get_sentiment_score("005930", redis_client=fake_redis_with_data)
        
        # Then: 미리 저장된 데이터 반환
        assert data["score"] == 65.5
        assert "삼성전자" in data["reason"]
    
    def test_set_sentiment_score_without_redis(self, mocker):
        """Redis 연결 실패 시 저장 실패"""
        from shared import redis_cache
        
        # Given: get_redis_connection이 None을 반환하도록 mock
        mocker.patch.object(redis_cache, 'get_redis_connection', return_value=None)
        
        # When: 저장 시도
        result = redis_cache.set_sentiment_score("005930", 50, "test")
        
        # Then: False 반환 (실패)
        assert result is False


class TestMarketRegimeCache:
    """시장 국면 (Market Regime) 캐시 테스트"""
    
    def test_set_and_get_market_regime(self, fake_redis):
        """시장 국면 캐시 저장 및 조회"""
        from shared.redis_cache import set_market_regime_cache, get_market_regime_cache
        
        # Given: 시장 국면 데이터
        regime_data = {
            "regime": "BULL",
            "risk_level": "LOW",
            "preset": "AGGRESSIVE"
        }
        
        # When: 저장
        result = set_market_regime_cache(regime_data, ttl_seconds=3600, redis_client=fake_redis)
        
        # Then: 성공하고 조회 가능
        assert result is True
        cached = get_market_regime_cache(redis_client=fake_redis)
        assert cached["regime"] == "BULL"
        assert cached["risk_level"] == "LOW"
        assert "_cached_at" in cached  # 타임스탬프 자동 추가됨
    
    def test_get_market_regime_cache_expired(self, fake_redis):
        """만료된 캐시 조회 시 None 반환"""
        from shared.redis_cache import get_market_regime_cache, MARKET_REGIME_CACHE_KEY
        
        # Given: 오래된 캐시 (2시간 전)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        old_data = {
            "regime": "BEAR",
            "_cached_at": old_time
        }
        fake_redis.setex(MARKET_REGIME_CACHE_KEY, 3600, json.dumps(old_data))
        
        # When: max_age_seconds=3600 (1시간)으로 조회
        cached = get_market_regime_cache(max_age_seconds=3600, redis_client=fake_redis)
        
        # Then: 만료되어 None 반환
        assert cached is None
    
    def test_get_market_regime_cache_not_expired(self, fake_redis):
        """유효한 캐시 조회"""
        from shared.redis_cache import get_market_regime_cache, MARKET_REGIME_CACHE_KEY
        
        # Given: 최근 캐시 (30분 전)
        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        recent_data = {
            "regime": "NEUTRAL",
            "_cached_at": recent_time
        }
        fake_redis.setex(MARKET_REGIME_CACHE_KEY, 3600, json.dumps(recent_data))
        
        # When: max_age_seconds=3600 (1시간)으로 조회
        cached = get_market_regime_cache(max_age_seconds=3600, redis_client=fake_redis)
        
        # Then: 유효하여 데이터 반환
        assert cached is not None
        assert cached["regime"] == "NEUTRAL"
    
    def test_set_market_regime_cache_empty_payload(self, fake_redis):
        """빈 페이로드로 저장 시도"""
        from shared.redis_cache import set_market_regime_cache
        
        # When: 빈 딕셔너리로 저장 시도
        result = set_market_regime_cache({}, redis_client=fake_redis)
        
        # Then: False 반환
        assert result is False


class TestCompetitorBenefitScore:
    """경쟁사 수혜 점수 테스트"""
    
    def test_set_competitor_benefit_score(self, fake_redis):
        """경쟁사 수혜 점수 저장"""
        from shared.redis_cache import set_competitor_benefit_score, get_competitor_benefit_score
        
        # Given: 수혜 정보
        stock_code = "000660"  # SK하이닉스
        score = 15
        reason = "삼성전자 보안사고로 인한 반사이익"
        affected = "005930"
        event_type = "보안사고"
        
        # When: 저장
        result = set_competitor_benefit_score(
            stock_code, score, reason, affected, event_type,
            redis_client=fake_redis
        )
        
        # Then: 성공
        assert result is True
        data = get_competitor_benefit_score(stock_code, redis_client=fake_redis)
        assert data["score"] == 15
        assert data["affected_stock"] == "005930"
        assert data["event_type"] == "보안사고"
    
    def test_set_competitor_benefit_score_keep_higher(self, fake_redis):
        """기존 점수가 더 높으면 유지"""
        from shared.redis_cache import set_competitor_benefit_score, get_competitor_benefit_score
        
        # Given: 높은 점수가 이미 있음
        stock_code = "000660"
        set_competitor_benefit_score(
            stock_code, 20, "첫 번째 이벤트", "005930", "리콜",
            redis_client=fake_redis
        )
        
        # When: 더 낮은 점수로 저장 시도
        set_competitor_benefit_score(
            stock_code, 10, "두 번째 이벤트", "035720", "오너리스크",
            redis_client=fake_redis
        )
        
        # Then: 기존 높은 점수 유지
        data = get_competitor_benefit_score(stock_code, redis_client=fake_redis)
        assert data["score"] == 20  # 기존 점수 유지
        assert data["event_type"] == "리콜"  # 기존 이벤트 유지
    
    def test_get_competitor_benefit_score_not_found(self, fake_redis):
        """존재하지 않는 종목 조회"""
        from shared.redis_cache import get_competitor_benefit_score
        
        # When: 없는 종목 조회
        data = get_competitor_benefit_score("999999", redis_client=fake_redis)
        
        # Then: 기본값 반환
        assert data["score"] == 0
        assert data["reason"] == ""
        assert data["affected_stock"] == ""
    
    def test_get_all_competitor_benefits(self, fake_redis):
        """모든 경쟁사 수혜 점수 조회"""
        from shared.redis_cache import set_competitor_benefit_score, get_all_competitor_benefits
        
        # Given: 여러 종목에 수혜 점수 저장
        set_competitor_benefit_score("000660", 10, "이유1", "005930", "보안사고", redis_client=fake_redis)
        set_competitor_benefit_score("035420", 8, "이유2", "005930", "보안사고", redis_client=fake_redis)
        
        # When: 전체 조회
        all_benefits = get_all_competitor_benefits(redis_client=fake_redis)
        
        # Then: 모든 데이터 반환
        assert len(all_benefits) == 2
        assert "000660" in all_benefits
        assert "035420" in all_benefits
        assert all_benefits["000660"]["score"] == 10


class TestGenericRedisData:
    """일반 Redis 데이터 저장/조회 테스트"""
    
    def test_set_and_get_redis_data(self, fake_redis):
        """일반 데이터 저장 및 조회"""
        from shared.redis_cache import set_redis_data, get_redis_data
        
        # Given: 임의의 데이터
        key = "test:custom:data"
        data = {"foo": "bar", "count": 42}
        
        # When: 저장 및 조회
        result = set_redis_data(key, data, ttl=60, redis_client=fake_redis)
        retrieved = get_redis_data(key, redis_client=fake_redis)
        
        # Then: 성공
        assert result is True
        assert retrieved["foo"] == "bar"
        assert retrieved["count"] == 42
    
    def test_get_redis_data_not_found(self, fake_redis):
        """존재하지 않는 키 조회"""
        from shared.redis_cache import get_redis_data
        
        # When: 없는 키 조회
        data = get_redis_data("nonexistent:key", redis_client=fake_redis)
        
        # Then: 빈 딕셔너리 반환
        assert data == {}


class TestRedisConnection:
    """Redis 연결 관리 테스트"""
    
    def test_get_redis_connection_with_injected_client(self, fake_redis):
        """의존성 주입된 클라이언트 사용"""
        from shared.redis_cache import get_redis_connection
        
        # When: fake_redis 주입
        client = get_redis_connection(redis_client=fake_redis)
        
        # Then: 주입된 클라이언트 반환
        assert client is fake_redis
    
    def test_reset_redis_connection(self, fake_redis):
        """Redis 연결 리셋"""
        from shared import redis_cache
        
        # Given: 전역 클라이언트 설정 (테스트용으로 직접 설정)
        redis_cache._redis_client = fake_redis
        
        # When: 리셋
        redis_cache.reset_redis_connection()
        
        # Then: 전역 클라이언트가 None
        assert redis_cache._redis_client is None


class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_sentiment_score_with_special_characters(self, fake_redis):
        """특수문자가 포함된 이유 저장"""
        from shared.redis_cache import set_sentiment_score, get_sentiment_score
        
        # Given: 특수문자 포함 이유
        stock_code = "005930"
        reason = '삼성전자 "AI 반도체" 호재 & 실적 개선 (기대↑)'
        
        # When: 저장 및 조회
        set_sentiment_score(stock_code, 80, reason, redis_client=fake_redis)
        data = get_sentiment_score(stock_code, redis_client=fake_redis)
        
        # Then: 정상 저장/조회
        assert data["score"] == 80
        assert "AI 반도체" in data["reason"]
    
    def test_sentiment_score_boundary_values(self, fake_redis):
        """경계값 테스트 (0, 100)"""
        from shared.redis_cache import set_sentiment_score, get_sentiment_score
        
        # 최소값 (0)
        set_sentiment_score("TEST01", 0, "매우 부정적", redis_client=fake_redis)
        data = get_sentiment_score("TEST01", redis_client=fake_redis)
        assert data["score"] == 0
        
        # 최대값 (100)
        set_sentiment_score("TEST02", 100, "매우 긍정적", redis_client=fake_redis)
        data = get_sentiment_score("TEST02", redis_client=fake_redis)
        assert data["score"] == 100
    
    def test_market_regime_with_complex_data(self, fake_redis):
        """복잡한 시장 국면 데이터 저장"""
        from shared.redis_cache import set_market_regime_cache, get_market_regime_cache
        
        # Given: 복잡한 중첩 데이터
        complex_data = {
            "regime": "VOLATILE",
            "indicators": {
                "vix": 25.5,
                "kospi_change": -1.2,
                "foreign_net": -5000000000
            },
            "sectors": ["반도체", "바이오", "2차전지"],
            "market_context_dict": {
                "summary": "시장 불안정",
                "confidence": 0.85
            }
        }
        
        # When: 저장 및 조회
        set_market_regime_cache(complex_data, redis_client=fake_redis)
        cached = get_market_regime_cache(redis_client=fake_redis)
        
        # Then: 중첩 데이터 정상 저장/조회
        assert cached["regime"] == "VOLATILE"
        assert cached["indicators"]["vix"] == 25.5
        assert "반도체" in cached["sectors"]
        assert cached["market_context_dict"]["confidence"] == 0.85

