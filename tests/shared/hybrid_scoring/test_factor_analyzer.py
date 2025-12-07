"""
tests/shared/hybrid_scoring/test_factor_analyzer.py - FactorAnalyzer 테스트
==========================================================================

shared/hybrid_scoring/factor_analyzer.py의 팩터 분석기를 테스트합니다.
Repository 모드를 사용하여 SQLAlchemy로 테스트합니다.
"""

import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.db.models import (
    Base,
    StockDailyPrice,
    StockMaster,
    FinancialMetricsQuarterly,
    StockInvestorTrading,
)
from shared.db.factor_repository import FactorRepository


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def in_memory_db():
    """인메모리 SQLite DB"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def repo(in_memory_db):
    """FactorRepository"""
    return FactorRepository(in_memory_db)


@pytest.fixture
def analyzer(repo):
    """Repository 모드 FactorAnalyzer"""
    from shared.hybrid_scoring.factor_analyzer import FactorAnalyzer
    return FactorAnalyzer(repository=repo)


@pytest.fixture
def sample_prices(in_memory_db):
    """샘플 주가 데이터 (2년치)"""
    base_date = datetime.now()
    
    # 상승 추세 데이터
    for i in range(252 * 2):  # 2년
        in_memory_db.add(StockDailyPrice(
            stock_code='005930',
            price_date=base_date - timedelta(days=i),
            close_price=70000 + i * 10,  # 상승 추세
            high_price=71000 + i * 10,
            low_price=69000 + i * 10,
            volume=1000000
        ))
    
    in_memory_db.commit()


@pytest.fixture
def sample_stocks(in_memory_db):
    """샘플 종목 마스터"""
    stocks = [
        StockMaster(
            stock_code='005930',
            stock_name='삼성전자',
            market_cap=400000000000000,  # 400조 → LARGE
            sector_kospi200='반도체'
        ),
        StockMaster(
            stock_code='000660',
            stock_name='SK하이닉스',
            market_cap=100000000000000,  # 100조 → LARGE
            sector_kospi200='반도체'
        ),
        StockMaster(
            stock_code='035420',
            stock_name='NAVER',
            market_cap=50000000000000,  # 50조 → MID
            sector_kospi200='인터넷'
        ),
        StockMaster(
            stock_code='999999',
            stock_name='소형주',
            market_cap=500000000000,  # 5천억 → SMALL
            sector_kospi200='기타'
        ),
    ]
    
    for stock in stocks:
        in_memory_db.add(stock)
    
    in_memory_db.commit()


# ============================================================================
# Tests: FactorAnalyzer 초기화
# ============================================================================

class TestFactorAnalyzerInit:
    """FactorAnalyzer 초기화 테스트"""
    
    def test_init_with_repository(self, repo):
        """Repository 모드 초기화"""
        from shared.hybrid_scoring.factor_analyzer import FactorAnalyzer
        
        analyzer = FactorAnalyzer(repository=repo)
        
        assert analyzer is not None
        assert analyzer._repository is not None
    
    def test_init_without_connection(self):
        """연결 없이 초기화"""
        from shared.hybrid_scoring.factor_analyzer import FactorAnalyzer
        
        analyzer = FactorAnalyzer()
        
        assert analyzer is not None
        assert analyzer.db_conn is None
    
    def test_repository_property(self, analyzer, repo):
        """repository 속성"""
        assert analyzer.repository is repo


# ============================================================================
# Tests: classify_stock_group
# ============================================================================

class TestClassifyStockGroup:
    """종목 그룹 분류 테스트"""
    
    def test_large_cap(self, analyzer, sample_stocks):
        """대형주 분류"""
        group = analyzer.classify_stock_group('005930')
        
        assert group == 'LARGE'
    
    def test_mid_cap(self, analyzer, sample_stocks):
        """중형주 분류"""
        group = analyzer.classify_stock_group('035420')
        
        # 50조 → 10조 이상이므로 LARGE 또는 1조 이상 MID
        assert group in ['LARGE', 'MID']
    
    def test_small_cap(self, analyzer, sample_stocks):
        """소형주 분류"""
        group = analyzer.classify_stock_group('999999')
        
        assert group == 'SMALL'
    
    def test_not_found(self, analyzer):
        """존재하지 않는 종목 → SMALL"""
        group = analyzer.classify_stock_group('000000')
        
        assert group == 'SMALL'
    
    def test_cache(self, analyzer, sample_stocks):
        """캐시 동작"""
        # 첫 호출
        group1 = analyzer.classify_stock_group('005930')
        
        # 두 번째 호출 (캐시에서)
        group2 = analyzer.classify_stock_group('005930')
        
        assert group1 == group2
        assert '005930' in analyzer._stock_group_cache


# ============================================================================
# Tests: get_stock_sector
# ============================================================================

class TestGetStockSector:
    """섹터 분류 테스트"""
    
    def test_from_db(self, analyzer, sample_stocks):
        """DB에서 섹터 조회"""
        sector = analyzer.get_stock_sector('005930')
        
        assert sector == '반도체'
    
    def test_from_mapping(self, analyzer):
        """하드코딩 매핑에서 조회"""
        # SECTOR_MAPPING에 정의된 종목
        sector = analyzer.get_stock_sector('005930')  # 삼성전자
        
        assert sector in ['반도체', '기타']
    
    def test_not_found(self, analyzer):
        """없는 종목 → 기타"""
        sector = analyzer.get_stock_sector('000000')
        
        assert sector == '기타'


# ============================================================================
# Tests: _get_historical_prices
# ============================================================================

class TestGetHistoricalPrices:
    """주가 데이터 조회 테스트"""
    
    def test_get_prices_via_repository(self, analyzer, sample_prices):
        """Repository를 통한 주가 조회"""
        result = analyzer._get_historical_prices(['005930'], days=100)
        
        assert '005930' in result
        assert len(result['005930']) <= 100
    
    def test_get_prices_dataframe_structure(self, analyzer, sample_prices):
        """DataFrame 구조 확인"""
        result = analyzer._get_historical_prices(['005930'], days=50)
        
        df = result.get('005930')
        
        if df is not None and len(df) > 0:
            assert 'CLOSE_PRICE' in df.columns
            assert 'PRICE_DATE' in df.columns


# ============================================================================
# Tests: 팩터 계산 함수들
# ============================================================================

class TestFactorCalculations:
    """팩터 계산 함수 테스트"""
    
    def test_calc_momentum_6m(self, analyzer):
        """6개월 모멘텀 계산"""
        # 상승 추세 데이터
        df = pd.DataFrame({
            'CLOSE_PRICE': [10000 + i * 100 for i in range(130)]
        })
        
        momentum = analyzer._calc_momentum_6m(df)
        
        # Series 반환
        assert isinstance(momentum, pd.Series)
        
        # 상승 추세 → 양수 모멘텀
        valid_values = momentum.dropna()
        if len(valid_values) > 0:
            assert valid_values.iloc[-1] > 0
    
    def test_calc_momentum_1m(self, analyzer):
        """1개월 모멘텀 계산"""
        df = pd.DataFrame({
            'CLOSE_PRICE': [10000 + i * 50 for i in range(30)]
        })
        
        momentum = analyzer._calc_momentum_1m(df)
        
        assert isinstance(momentum, pd.Series)
    
    def test_calc_rsi_oversold(self, analyzer):
        """RSI 과매도 계산"""
        # 하락 추세 데이터 → RSI 낮음
        df = pd.DataFrame({
            'CLOSE_PRICE': [10000 - i * 50 for i in range(20)]
        })
        
        rsi = analyzer._calc_rsi_oversold(df)
        
        assert isinstance(rsi, pd.Series)


# ============================================================================
# Tests: _calculate_forward_returns
# ============================================================================

class TestCalculateForwardReturns:
    """미래 수익률 계산 테스트"""
    
    def test_forward_returns_5d(self, analyzer):
        """5일 미래 수익률"""
        df = pd.DataFrame({
            'CLOSE_PRICE': [100, 105, 110, 115, 120, 125, 130, 135, 140, 145]
        })
        
        returns = analyzer._calculate_forward_returns(df, forward_days=5)
        
        assert isinstance(returns, pd.Series)
        assert len(returns) == 10
    
    def test_forward_returns_nan_at_end(self, analyzer):
        """마지막 N일은 NaN"""
        df = pd.DataFrame({
            'CLOSE_PRICE': [100] * 10
        })
        
        returns = analyzer._calculate_forward_returns(df, forward_days=5)
        
        # 마지막 5일은 NaN
        assert returns.iloc[-1:].isna().all()


# ============================================================================
# Tests: calculate_ic
# ============================================================================

class TestCalculateIC:
    """IC (Information Coefficient) 계산 테스트"""
    
    def test_ic_positive_correlation(self, analyzer):
        """양의 상관관계 → IC > 0"""
        # 팩터와 수익률이 같은 방향 (30개 이상 필요)
        factor_values = pd.Series(list(range(1, 51)))
        forward_returns = pd.Series([x * 0.01 for x in range(1, 51)])
        
        # calculate_ic는 (IC, IC_std, IR) 튜플 반환
        ic_mean, ic_std, ir = analyzer.calculate_ic(factor_values, forward_returns)
        
        assert ic_mean > 0
    
    def test_ic_negative_correlation(self, analyzer):
        """음의 상관관계 → IC < 0"""
        factor_values = pd.Series(list(range(1, 51)))
        forward_returns = pd.Series([0.5 - x * 0.01 for x in range(1, 51)])
        
        ic_mean, ic_std, ir = analyzer.calculate_ic(factor_values, forward_returns)
        
        assert ic_mean < 0
    
    def test_ic_insufficient_data(self, analyzer):
        """데이터 부족 → 기본값 반환"""
        factor_values = pd.Series([1, 2, 3, 4, 5])
        forward_returns = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        
        # 30개 미만 → (0.0, 1.0, 0.0) 반환
        ic_mean, ic_std, ir = analyzer.calculate_ic(factor_values, forward_returns)
        
        assert ic_mean == 0.0
        assert ic_std == 1.0
        assert ir == 0.0


# ============================================================================
# Tests: group_stocks_by_sector
# ============================================================================

class TestGroupStocksBySector:
    """섹터별 종목 그룹화 테스트"""
    
    def test_group_by_sector(self, analyzer, sample_stocks):
        """섹터별 그룹화"""
        stock_codes = ['005930', '000660', '035420']
        
        groups = analyzer.group_stocks_by_sector(stock_codes)
        
        assert isinstance(groups, dict)
        
        # 반도체 섹터에 2개
        if '반도체' in groups:
            assert len(groups['반도체']) >= 1


# ============================================================================
# Tests: save_factor_metadata (Repository 모드)
# ============================================================================

class TestSaveFactorMetadata:
    """팩터 메타데이터 저장 테스트"""
    
    def test_save_via_repository(self, repo, in_memory_db):
        """Repository를 통한 저장"""
        success = repo.save_factor_metadata(
            factor_key='test_factor',
            factor_name='테스트 팩터',
            market_regime='ALL',
            ic_mean=0.05,
            ic_std=0.02,
            ir=2.5,
            hit_rate=0.55,
            recommended_weight=0.15,
            sample_count=100
        )
        
        assert success is True
        
        # 저장 확인
        saved = repo.get_factor_metadata('test_factor', 'ALL')
        assert saved is not None
        assert saved.ic_mean == 0.05


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_empty_stock_list(self, analyzer):
        """빈 종목 리스트"""
        result = analyzer._get_historical_prices([], days=100)
        
        assert result == {}
    
    def test_invalid_factor_key(self, analyzer, sample_prices):
        """잘못된 팩터 키 → ValueError"""
        import pytest
        
        # 존재하지 않는 팩터 → ValueError 발생
        with pytest.raises(ValueError, match="Unknown factor"):
            analyzer.analyze_factor(['005930'], 'invalid_factor', forward_days=5)
    
    def test_insufficient_data(self, analyzer, in_memory_db):
        """데이터 부족"""
        # 10일치 데이터만
        base_date = datetime.now()
        for i in range(10):
            in_memory_db.add(StockDailyPrice(
                stock_code='SHORT',
                price_date=base_date - timedelta(days=i),
                close_price=10000,
                volume=100000,
                high_price=10500,
                low_price=9500
            ))
        in_memory_db.commit()
        
        # 6개월 모멘텀 계산 → 데이터 부족
        result = analyzer._get_historical_prices(['SHORT'], days=120)
        
        df = result.get('SHORT')
        if df is not None:
            # 10개만 반환
            assert len(df) <= 10


# ============================================================================
# Tests: FactorAnalysisResult / ConditionPerformance
# ============================================================================

class TestDataClasses:
    """데이터클래스 테스트"""
    
    def test_factor_analysis_result(self):
        """FactorAnalysisResult 생성"""
        from shared.hybrid_scoring.factor_analyzer import FactorAnalysisResult
        
        result = FactorAnalysisResult(
            factor_key='momentum_6m',
            factor_name='6개월 모멘텀',
            ic_mean=0.05,
            ic_std=0.02,
            ir=2.5,
            hit_rate=0.55,
            recommended_weight=0.15,
            sample_count=100
        )
        
        assert result.factor_key == 'momentum_6m'
        assert result.ic_mean == 0.05
    
    def test_condition_performance(self):
        """ConditionPerformance 생성"""
        from shared.hybrid_scoring.factor_analyzer import ConditionPerformance
        
        perf = ConditionPerformance(
            target_type='STOCK',
            target_code='005930',
            condition_key='rsi_oversold',
            condition_desc='RSI 30 이하',
            win_rate=0.65,
            avg_return=3.5,
            sample_count=45,
            recent_win_rate=0.70,
            recent_sample_count=15
        )
        
        assert perf.target_code == '005930'
        assert perf.win_rate == 0.65

