"""
tests/shared/hybrid_scoring/test_factor_repository.py - FactorRepository 테스트
==============================================================================

shared/db/factor_repository.py의 SQLAlchemy Repository를 테스트합니다.
in-memory SQLite를 사용하여 실제 DB 없이 테스트합니다.
"""

import pytest
from datetime import datetime, date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pandas as pd

from shared.db.models import (
    Base,
    StockDailyPrice,
    StockMaster,
    FinancialMetricsQuarterly,
    StockInvestorTrading,
    StockNewsSentiment,
    StockDisclosures,
    FactorMetadata,
    FactorPerformance,
    NewsFactorStats,
)
from shared.db.factor_repository import FactorRepository


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def in_memory_db():
    """인메모리 SQLite DB 세션"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def repo(in_memory_db):
    """FactorRepository 인스턴스"""
    return FactorRepository(in_memory_db)


@pytest.fixture
def sample_stock_prices(in_memory_db):
    """샘플 주가 데이터"""
    base_date = datetime.now()
    
    for i in range(10):
        price = StockDailyPrice(
            stock_code='005930',
            price_date=base_date - timedelta(days=i),
            close_price=70000 + i * 100,
            volume=1000000 + i * 10000,
            high_price=71000 + i * 100,
            low_price=69000 + i * 100
        )
        in_memory_db.add(price)
    
    in_memory_db.commit()


@pytest.fixture
def sample_stock_master(in_memory_db):
    """샘플 종목 마스터"""
    stocks = [
        StockMaster(
            stock_code='005930',
            stock_name='삼성전자',
            market_cap=400000000000000,  # 400조
            sector_kospi200='반도체',
            industry_code='IT'
        ),
        StockMaster(
            stock_code='000660',
            stock_name='SK하이닉스',
            market_cap=100000000000000,  # 100조
            sector_kospi200='반도체',
            industry_code='IT'
        ),
        StockMaster(
            stock_code='035420',
            stock_name='NAVER',
            market_cap=50000000000000,  # 50조
            sector_kospi200='인터넷',
            industry_code='서비스'
        ),
    ]
    
    for stock in stocks:
        in_memory_db.add(stock)
    
    in_memory_db.commit()


@pytest.fixture
def sample_financials(in_memory_db):
    """샘플 재무 데이터"""
    financials = [
        FinancialMetricsQuarterly(
            stock_code='005930',
            quarter_date=date(2024, 9, 30),
            per=10.5,
            pbr=1.2,
            roe=15.0
        ),
        FinancialMetricsQuarterly(
            stock_code='005930',
            quarter_date=date(2024, 6, 30),
            per=11.0,
            pbr=1.3,
            roe=14.5
        ),
        FinancialMetricsQuarterly(
            stock_code='000660',
            quarter_date=date(2024, 9, 30),
            per=8.0,
            pbr=1.5,
            roe=18.0
        ),
    ]
    
    for f in financials:
        in_memory_db.add(f)
    
    in_memory_db.commit()


# ============================================================================
# Tests: 주가 데이터 조회
# ============================================================================

class TestHistoricalPrices:
    """주가 데이터 조회 테스트"""
    
    def test_get_historical_prices(self, repo, sample_stock_prices):
        """단일 종목 주가 조회"""
        df = repo.get_historical_prices('005930', days=10)
        
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10
        assert 'CLOSE_PRICE' in df.columns
        assert 'VOLUME' in df.columns
    
    def test_get_historical_prices_sorted(self, repo, sample_stock_prices):
        """날짜 오름차순 정렬"""
        df = repo.get_historical_prices('005930', days=10)
        
        # 날짜가 오름차순이어야 함
        dates = df['PRICE_DATE'].tolist()
        assert dates == sorted(dates)
    
    def test_get_historical_prices_not_found(self, repo):
        """존재하지 않는 종목"""
        df = repo.get_historical_prices('999999', days=10)
        
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
    
    def test_get_historical_prices_bulk(self, repo, in_memory_db):
        """여러 종목 일괄 조회"""
        # 두 종목 데이터 추가
        base_date = datetime.now()
        for code in ['005930', '000660']:
            for i in range(5):
                in_memory_db.add(StockDailyPrice(
                    stock_code=code,
                    price_date=base_date - timedelta(days=i),
                    close_price=70000,
                    volume=1000000,
                    high_price=71000,
                    low_price=69000
                ))
        in_memory_db.commit()
        
        result = repo.get_historical_prices_bulk(['005930', '000660'], days=5)
        
        assert '005930' in result
        assert '000660' in result
        assert len(result['005930']) == 5
        assert len(result['000660']) == 5


# ============================================================================
# Tests: 종목 마스터 조회
# ============================================================================

class TestStockMaster:
    """종목 마스터 조회 테스트"""
    
    def test_get_market_cap(self, repo, sample_stock_master):
        """시가총액 조회"""
        market_cap = repo.get_market_cap('005930')
        
        assert market_cap == 400000000000000
    
    def test_get_market_cap_not_found(self, repo):
        """존재하지 않는 종목"""
        market_cap = repo.get_market_cap('999999')
        
        assert market_cap is None
    
    def test_get_stock_sector(self, repo, sample_stock_master):
        """섹터 정보 조회"""
        sector, industry = repo.get_stock_sector('005930')
        
        assert sector == '반도체'
        assert industry == 'IT'
    
    def test_get_stock_sector_not_found(self, repo):
        """존재하지 않는 종목"""
        sector, industry = repo.get_stock_sector('999999')
        
        assert sector is None
        assert industry is None


# ============================================================================
# Tests: 재무 데이터 조회
# ============================================================================

class TestFinancialData:
    """재무 데이터 조회 테스트"""
    
    def test_get_financial_data(self, repo, sample_financials):
        """재무 데이터 조회"""
        data = repo.get_financial_data(['005930', '000660'])
        
        assert '005930' in data
        assert '000660' in data
        assert len(data['005930']) == 2  # 2분기
        assert len(data['000660']) == 1  # 1분기
    
    def test_get_financial_data_values(self, repo, sample_financials):
        """재무 데이터 값 확인"""
        data = repo.get_financial_data(['005930'])
        
        # 최신 분기
        quarter_data = list(data['005930'].values())[0]
        
        assert 'per' in quarter_data
        assert 'pbr' in quarter_data
        assert 'roe' in quarter_data
    
    def test_get_financial_data_not_found(self, repo):
        """데이터 없는 종목"""
        data = repo.get_financial_data(['999999'])
        
        assert data == {}


# ============================================================================
# Tests: 수급 데이터 조회
# ============================================================================

class TestSupplyDemandData:
    """수급 데이터 조회 테스트"""
    
    def test_get_supply_demand_data(self, repo, in_memory_db):
        """수급 데이터 조회"""
        # 데이터 추가
        base_date = date.today()
        for i in range(5):
            in_memory_db.add(StockInvestorTrading(
                stock_code='005930',
                trade_date=base_date - timedelta(days=i),
                foreign_net_buy=1000000000,
                institution_net_buy=500000000
            ))
        in_memory_db.commit()
        
        result = repo.get_supply_demand_data(['005930'], days=5)
        
        assert '005930' in result
        assert len(result['005930']) == 5
        assert 'FOREIGN_NET_BUY' in result['005930'].columns
    
    def test_get_supply_demand_data_empty(self, repo):
        """데이터 없는 종목"""
        result = repo.get_supply_demand_data(['999999'], days=5)
        
        assert '999999' in result
        assert len(result['999999']) == 0


# ============================================================================
# Tests: 뉴스 감성 데이터 조회
# ============================================================================

class TestNewsSentimentHistory:
    """뉴스 감성 데이터 조회 테스트"""
    
    def test_get_news_sentiment_history(self, repo, in_memory_db):
        """뉴스 감성 히스토리 조회"""
        base_date = date.today()
        for i in range(3):
            in_memory_db.add(StockNewsSentiment(
                stock_code='005930',
                news_date=base_date - timedelta(days=i),
                sentiment_score=80 - i * 10,
                category='실적'
            ))
        in_memory_db.commit()
        
        result = repo.get_news_sentiment_history(['005930'], days=10)
        
        assert '005930' in result
        assert len(result['005930']) == 3
        assert 'SENTIMENT_SCORE' in result['005930'].columns


# ============================================================================
# Tests: 공시 데이터 조회
# ============================================================================

class TestDisclosures:
    """공시 데이터 조회 테스트"""
    
    def test_get_disclosures(self, repo, in_memory_db):
        """공시 데이터 조회"""
        base_date = date.today()
        for i in range(2):
            in_memory_db.add(StockDisclosures(
                stock_code='005930',
                disclosure_date=base_date - timedelta(days=i * 30),
                category='수주'
            ))
        in_memory_db.commit()
        
        result = repo.get_disclosures(['005930'], lookback_days=365)
        
        assert '005930' in result
        assert len(result['005930']) == 2


# ============================================================================
# Tests: 팩터 메타데이터 저장/조회
# ============================================================================

class TestFactorMetadata:
    """팩터 메타데이터 CRUD 테스트"""
    
    def test_save_factor_metadata_insert(self, repo, in_memory_db):
        """팩터 메타데이터 INSERT"""
        result = repo.save_factor_metadata(
            factor_key='momentum_6m',
            factor_name='6개월 모멘텀',
            market_regime='BULL',
            ic_mean=0.05,
            ic_std=0.02,
            ir=2.5,
            hit_rate=0.55,
            recommended_weight=0.15,
            sample_count=100
        )
        
        assert result is True
        
        # 저장 확인
        saved = repo.get_factor_metadata('momentum_6m', 'BULL')
        assert saved is not None
        assert saved.ic_mean == 0.05
    
    def test_save_factor_metadata_update(self, repo, in_memory_db):
        """팩터 메타데이터 UPDATE"""
        # INSERT
        repo.save_factor_metadata(
            factor_key='test_factor',
            factor_name='Test',
            market_regime='ALL',
            ic_mean=0.01,
            ic_std=0.01,
            ir=1.0,
            hit_rate=0.50,
            recommended_weight=0.10,
            sample_count=50
        )
        
        # UPDATE
        repo.save_factor_metadata(
            factor_key='test_factor',
            factor_name='Test Updated',
            market_regime='ALL',
            ic_mean=0.02,  # 변경
            ic_std=0.01,
            ir=2.0,  # 변경
            hit_rate=0.55,  # 변경
            recommended_weight=0.10,
            sample_count=100  # 변경
        )
        
        # 확인
        saved = repo.get_factor_metadata('test_factor', 'ALL')
        assert saved.ic_mean == 0.02
        assert saved.ir == 2.0
        assert saved.hit_rate == 0.55
        assert saved.sample_count == 100


# ============================================================================
# Tests: 팩터 성과 저장/조회
# ============================================================================

class TestFactorPerformance:
    """팩터 성과 CRUD 테스트"""
    
    def test_save_factor_performance(self, repo, in_memory_db):
        """팩터 성과 저장"""
        result = repo.save_factor_performance(
            target_type='STOCK',
            target_code='005930',
            target_name='삼성전자',
            condition_key='rsi_oversold',
            condition_desc='RSI 30 이하',
            win_rate=0.65,
            avg_return=3.5,
            sample_count=45,
            holding_days=5
        )
        
        assert result is True
        
        # 저장 확인
        saved = repo.get_factor_performance('STOCK', '005930', 'rsi_oversold', 5)
        assert saved is not None
        assert saved.win_rate == 0.65
    
    def test_confidence_level_auto_calculation(self, repo, in_memory_db):
        """신뢰도 자동 계산"""
        # HIGH (30+)
        repo.save_factor_performance(
            target_type='ALL', target_code='ALL', target_name='ALL',
            condition_key='high_conf', condition_desc='Test',
            win_rate=0.60, avg_return=2.0, sample_count=50
        )
        
        # MID (15-29)
        repo.save_factor_performance(
            target_type='ALL', target_code='ALL', target_name='ALL',
            condition_key='mid_conf', condition_desc='Test',
            win_rate=0.55, avg_return=1.5, sample_count=20
        )
        
        # LOW (<15)
        repo.save_factor_performance(
            target_type='ALL', target_code='ALL', target_name='ALL',
            condition_key='low_conf', condition_desc='Test',
            win_rate=0.50, avg_return=1.0, sample_count=10
        )
        
        high = repo.get_factor_performance('ALL', 'ALL', 'high_conf')
        mid = repo.get_factor_performance('ALL', 'ALL', 'mid_conf')
        low = repo.get_factor_performance('ALL', 'ALL', 'low_conf')
        
        assert high.confidence_level == 'HIGH'
        assert mid.confidence_level == 'MID'
        assert low.confidence_level == 'LOW'


# ============================================================================
# Tests: 뉴스 팩터 통계 저장
# ============================================================================

class TestNewsFactorStats:
    """뉴스 팩터 통계 CRUD 테스트"""
    
    def test_save_news_factor_stats(self, repo, in_memory_db):
        """뉴스 팩터 통계 저장"""
        result = repo.save_news_factor_stats(
            target_type='ALL',
            target_code='ALL',
            news_category='수주',
            sentiment='POSITIVE',
            win_rate=0.75,
            avg_excess_return=3.2,
            avg_absolute_return=4.5,
            sample_count=40,
            return_d5=2.5,
            win_rate_d5=0.72
        )
        
        assert result is True


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_empty_stock_codes(self, repo):
        """빈 종목 리스트"""
        result = repo.get_historical_prices_bulk([], days=10)
        assert result == {}
        
        result = repo.get_financial_data([])
        assert result == {}
    
    def test_special_characters_in_name(self, repo, in_memory_db):
        """종목명에 특수문자"""
        in_memory_db.add(StockMaster(
            stock_code='123456',
            stock_name='LG화학(우)',
            market_cap=10000000000000
        ))
        in_memory_db.commit()
        
        market_cap = repo.get_market_cap('123456')
        assert market_cap is not None

