"""
tests/shared/test_sector_classifier.py - 섹터 분류 테스트
========================================================

shared/sector_classifier.py의 SectorClassifier 클래스를 테스트합니다.
"""

import pytest
from unittest.mock import MagicMock, patch


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_kis():
    """Mock KIS 인스턴스"""
    return MagicMock()


@pytest.fixture
def classifier_no_db(mock_kis):
    """DB 없는 SectorClassifier"""
    from shared.sector_classifier import SectorClassifier
    return SectorClassifier(kis=mock_kis, db_pool_initialized=False)


@pytest.fixture
def classifier_with_db(mock_kis):
    """DB 있는 SectorClassifier"""
    from shared.sector_classifier import SectorClassifier
    return SectorClassifier(kis=mock_kis, db_pool_initialized=True)


# ============================================================================
# Tests: 종목명 기반 섹터 추론
# ============================================================================

class TestInferSectorFromName:
    """종목명 기반 섹터 추론 테스트"""
    
    def test_infer_samsung_electronics(self, classifier_no_db):
        """삼성전자 → 정보통신"""
        sector = classifier_no_db._infer_sector_from_name('삼성전자')
        assert sector == '정보통신'
    
    def test_infer_sk_hynix(self, classifier_no_db):
        """SK하이닉스 → 정보통신"""
        sector = classifier_no_db._infer_sector_from_name('SK하이닉스')
        assert sector == '정보통신'
    
    def test_infer_hyundai_motor(self, classifier_no_db):
        """현대차 → 자유소비재"""
        sector = classifier_no_db._infer_sector_from_name('현대차')
        assert sector == '자유소비재'
    
    def test_infer_kia(self, classifier_no_db):
        """기아 → 자유소비재"""
        sector = classifier_no_db._infer_sector_from_name('기아')
        assert sector == '자유소비재'
    
    def test_infer_lg_chem(self, classifier_no_db):
        """LG화학 → 에너지화학"""
        sector = classifier_no_db._infer_sector_from_name('LG화학')
        assert sector == '에너지화학'
    
    def test_infer_kb_financial(self, classifier_no_db):
        """KB금융 → 금융"""
        sector = classifier_no_db._infer_sector_from_name('KB금융')
        assert sector == '금융'
    
    def test_infer_samsung_biologics(self, classifier_no_db):
        """삼성바이오로직스 → 필수소비재"""
        sector = classifier_no_db._infer_sector_from_name('삼성바이오로직스')
        assert sector == '필수소비재'
    
    def test_infer_posco(self, classifier_no_db):
        """POSCO홀딩스 → 철강소재"""
        sector = classifier_no_db._infer_sector_from_name('POSCO홀딩스')
        assert sector == '철강소재'
    
    def test_infer_korean_air(self, classifier_no_db):
        """대한항공 → 조선운송"""
        sector = classifier_no_db._infer_sector_from_name('대한항공')
        assert sector == '조선운송'
    
    def test_infer_samsung_construction(self, classifier_no_db):
        """삼성물산 → 건설기계"""
        sector = classifier_no_db._infer_sector_from_name('삼성물산')
        assert sector == '건설기계'
    
    def test_infer_unknown_stock(self, classifier_no_db):
        """알 수 없는 종목 → 기타"""
        sector = classifier_no_db._infer_sector_from_name('알수없는종목')
        assert sector == '기타'
    
    def test_infer_partial_match(self, classifier_no_db):
        """부분 매칭"""
        sector = classifier_no_db._infer_sector_from_name('삼성전자우')
        assert sector == '정보통신'  # '삼성전자' 포함
    
    def test_infer_naver(self, classifier_no_db):
        """NAVER → 정보통신"""
        sector = classifier_no_db._infer_sector_from_name('NAVER')
        assert sector == '정보통신'


# ============================================================================
# Tests: get_sector (DB 없음)
# ============================================================================

class TestGetSectorNoDb:
    """DB 없이 섹터 조회 테스트"""
    
    def test_get_sector_from_name(self, classifier_no_db):
        """종목명 기반 섹터 조회"""
        sector = classifier_no_db.get_sector('005930', '삼성전자')
        
        assert sector == '정보통신'
    
    def test_get_sector_caching(self, classifier_no_db):
        """캐시 동작"""
        # 첫 번째 호출
        sector1 = classifier_no_db.get_sector('005930', '삼성전자')
        
        # 캐시에 저장되었는지 확인
        assert '005930' in classifier_no_db.sector_cache
        
        # 두 번째 호출 (캐시 사용)
        sector2 = classifier_no_db.get_sector('005930', '삼성전자')
        
        assert sector1 == sector2
    
    def test_get_sector_different_stocks(self, classifier_no_db):
        """여러 종목 조회"""
        sector1 = classifier_no_db.get_sector('005930', '삼성전자')
        sector2 = classifier_no_db.get_sector('005380', '현대차')
        sector3 = classifier_no_db.get_sector('051910', 'LG화학')
        
        assert sector1 == '정보통신'
        assert sector2 == '자유소비재'
        assert sector3 == '에너지화학'


# ============================================================================
# Tests: get_sector (DB 있음)
# ============================================================================

class TestGetSectorWithDb:
    """DB와 함께 섹터 조회 테스트"""
    
    @patch('shared.sector_classifier.session_scope')
    def test_get_sector_from_db(self, mock_session_scope, classifier_with_db):
        """DB에서 섹터 조회"""
        # Mock session 및 query 결과
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.scalar.return_value = '반도체'
        mock_session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_scope.return_value.__exit__ = MagicMock(return_value=False)
        
        sector = classifier_with_db.get_sector('005930', '삼성전자')
        
        assert sector == '반도체'
    
    @patch('shared.sector_classifier.session_scope')
    def test_get_sector_db_returns_etc(self, mock_session_scope, classifier_with_db):
        """DB가 'etc' 반환하면 종목명 기반 추론"""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.scalar.return_value = 'etc'
        mock_session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_scope.return_value.__exit__ = MagicMock(return_value=False)
        
        sector = classifier_with_db.get_sector('005930', '삼성전자')
        
        # 'etc'이면 종목명 기반 추론
        assert sector == '정보통신'
    
    @patch('shared.sector_classifier.session_scope')
    def test_get_sector_db_error_fallback(self, mock_session_scope, classifier_with_db):
        """DB 에러 시 종목명 기반 추론으로 폴백"""
        mock_session_scope.return_value.__enter__ = MagicMock(side_effect=Exception("DB Error"))
        
        sector = classifier_with_db.get_sector('005930', '삼성전자')
        
        # DB 에러면 종목명 기반 추론
        assert sector == '정보통신'
    
    @patch('shared.sector_classifier.session_scope')
    def test_get_sector_db_returns_none(self, mock_session_scope, classifier_with_db):
        """DB가 None 반환"""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.scalar.return_value = None
        mock_session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_scope.return_value.__exit__ = MagicMock(return_value=False)
        
        sector = classifier_with_db.get_sector('005930', '삼성전자')
        
        # None이면 종목명 기반 추론
        assert sector == '정보통신'


# ============================================================================
# Tests: _normalize_sector
# ============================================================================

class TestNormalizeSector:
    """섹터 정규화 테스트"""
    
    def test_normalize_electronics(self, classifier_no_db):
        """전기전자 → 반도체"""
        result = classifier_no_db._normalize_sector('전기전자')
        assert result == '반도체'
    
    def test_normalize_automobile(self, classifier_no_db):
        """운수장비 → 자동차"""
        result = classifier_no_db._normalize_sector('운수장비')
        assert result == '자동차'
    
    def test_normalize_bank(self, classifier_no_db):
        """은행 → 금융"""
        result = classifier_no_db._normalize_sector('은행')
        assert result == '금융'
    
    def test_normalize_medicine(self, classifier_no_db):
        """의약품 → 바이오"""
        result = classifier_no_db._normalize_sector('의약품')
        assert result == '바이오'
    
    def test_normalize_unknown(self, classifier_no_db):
        """알 수 없는 섹터 → 기타"""
        result = classifier_no_db._normalize_sector('알수없음')
        assert result == '기타'


# ============================================================================
# Tests: SECTOR_KEYWORDS
# ============================================================================

class TestSectorKeywords:
    """섹터 키워드 정의 테스트"""
    
    def test_sector_keywords_exist(self, classifier_no_db):
        """섹터 키워드 딕셔너리 존재"""
        from shared.sector_classifier import SectorClassifier
        
        assert hasattr(SectorClassifier, 'SECTOR_KEYWORDS')
        assert len(SectorClassifier.SECTOR_KEYWORDS) > 0
    
    def test_all_sectors_defined(self, classifier_no_db):
        """주요 섹터 정의 확인"""
        from shared.sector_classifier import SectorClassifier
        
        expected_sectors = [
            '정보통신', '자유소비재', '에너지화학', '금융',
            '필수소비재', '철강소재', '조선운송', '건설기계', '기타'
        ]
        
        for sector in expected_sectors:
            assert sector in SectorClassifier.SECTOR_KEYWORDS


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_empty_stock_name(self, classifier_no_db):
        """빈 종목명"""
        sector = classifier_no_db.get_sector('000000', '')
        
        assert sector == '기타'
    
    def test_special_characters_in_name(self, classifier_no_db):
        """종목명에 특수문자"""
        sector = classifier_no_db.get_sector('051910', 'LG화학(우)')
        
        assert sector == '에너지화학'  # 'LG화학' 포함
    
    def test_cache_persistence(self, classifier_no_db):
        """캐시 지속성"""
        # 여러 종목 조회
        classifier_no_db.get_sector('005930', '삼성전자')
        classifier_no_db.get_sector('005380', '현대차')
        classifier_no_db.get_sector('051910', 'LG화학')
        
        # 캐시에 모두 저장
        assert len(classifier_no_db.sector_cache) == 3
        assert classifier_no_db.sector_cache['005930'] == '정보통신'
        assert classifier_no_db.sector_cache['005380'] == '자유소비재'
        assert classifier_no_db.sector_cache['051910'] == '에너지화학'

