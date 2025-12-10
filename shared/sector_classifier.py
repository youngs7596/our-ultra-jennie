# youngs75_jennie/sector_classifier.py
# Version: v3.5
# 섹터/업종 분류 모듈
# 작업 LLM: Claude Sonnet 4.5

import logging
from typing import Dict, Optional
from . import database
from .db.connection import session_scope

logger = logging.getLogger(__name__)

class SectorClassifier:
    """
    종목의 섹터/업종 정보 관리
    
    데이터 소스 우선순위:
    1. 캐시 (메모리)
    2. DB (STOCK_MASTER 테이블)
    3. 종목명 기반 추론 (Fallback)
    3. KIS API (향후 확장)
    """
    
    # 주요 섹터 정의 (KOSPI 기준)
    # [수정] 백테스트 로그 분석을 통해 '기타' 종목 키워드 보강
    SECTOR_KEYWORDS = {
        '정보통신': ['삼성전자', 'SK하이닉스', 'NAVER', '카카오', '삼성SDI', 'LG전자', '삼성전기', 'SK텔레콤', '하이브', '크래프톤', 'KT', '대덕전자', '이수페타시스', '디아이', '코리아써키트', '해성디에스', '삼영'],
        '자유소비재': ['현대차', '기아', '현대모비스', '만도', '한온시스템', 'LG생활건강', '아모레퍼시픽', '신세계', '기아차', '코오롱모빌리티', '제이준코스메틱', '일정실업', '대일', '한세실업'],
        '에너지화학': ['LG화학', 'SK이노베이션', 'S-Oil', '롯데케미칼', '포스코퓨처엠', 'POSCO퓨처엠', '롯데에너지머티리얼즈', '엘앤에프', '효성', '이수화학'],
        '금융': ['KB금융', '신한지주', '하나금융지주', '삼성생명', '카카오뱅크', '기업은행', '에이플러스에셋'],
        '필수소비재': ['삼성바이오로직스', '셀트리온', 'SK바이오사이언스', 'KT&G', '아모레G', '일동홀딩스', '파미셀', '한올바이오파마', '한미약품', '일동제약', '삼성바이오'],
        '철강소재': ['POSCO홀딩스', '고려아연'],
        '조선운송': ['한국조선해양', 'HMM', '대한항공', '동양고속'],
        '건설기계': ['삼성물산', '현대건설', '두산에너빌리티', 'LS ELECTRIC', '두산', '대한전선', '일진전기', '우진', '금강공업'],
        '기타': [] # 그 외 모든 종목
    }
    
    def __init__(self, kis, db_pool_initialized=True):
        self.kis = kis
        self.db_pool_initialized = db_pool_initialized
        self.sector_cache = {}  # {stock_code: sector_name}
    
    def get_sector(self, stock_code: str, stock_name: str) -> str:
        """
        종목의 섹터 정보 조회
        
        Returns:
            섹터명 (예: '반도체', '자동차', '기타')
        """
        # 1. 캐시 확인
        if stock_code in self.sector_cache:
            return self.sector_cache[stock_code]
        
        sector = '기타' # 기본값

        # 2. DB (STOCK_MASTER) 조회
        # db_pool_initialized는 DB 풀 사용 가능 여부를 나타냄
        if self.db_pool_initialized:
            try:
                from .db.models import StockMaster
                # [수정] SECTOR_KOSPI200 컬럼을 우선적으로 조회
                with session_scope(readonly=True) as session:
                    result = session.query(StockMaster.sector_kospi200).filter(StockMaster.stock_code == stock_code).scalar()
                    if result and result not in ('etc', '미분류'):
                        sector = result
                        logger.debug(f"   [Sector] DB에서 '{stock_name}' 섹터 조회 (SECTOR_KOSPI200): {sector}")
                    else:
                        # DB에 없거나 'etc', '미분류'인 경우, 종목명 기반 추론으로 Fallback
                        sector = self._infer_sector_from_name(stock_name)
            except Exception as e:
                logger.warning(f"   [Sector] DB 조회 실패, 종목명 기반 추론으로 대체: {e}")
                sector = self._infer_sector_from_name(stock_name)
        else:
            # 3. 종목명 기반 추론 (Fallback)
            sector = self._infer_sector_from_name(stock_name)
        
        self.sector_cache[stock_code] = sector # 캐시에 저장
        logger.debug(f"   [Sector] {stock_name}({stock_code}) → {sector}")

        return sector
    
    def _infer_sector_from_name(self, stock_name: str) -> str:
        """종목명으로 섹터 추론"""
        for sector, keywords in self.SECTOR_KEYWORDS.items():
            if sector == '기타':
                continue
            for keyword in keywords:
                if keyword in stock_name:
                    return sector
        return '기타'
    
    def _normalize_sector(self, sector_raw: str) -> str:
        """KIS API 섹터명을 표준 섹터명으로 변환 (향후 확장용)"""
        sector_map = {
            '전기전자': '반도체',
            '운수장비': '자동차',
            '화학': '화학',
            '은행': '금융',
            '증권': '금융',
            '의약품': '바이오',
        }
        return sector_map.get(sector_raw, '기타')
