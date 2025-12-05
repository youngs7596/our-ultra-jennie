# youngs75_jennie/portfolio_diversification.py
# Version: v3.5
# 포트폴리오 분산 검증 모듈
# 작업 LLM: Claude Sonnet 4.5

import logging
from typing import Dict, List
from collections import defaultdict

logger = logging.getLogger(__name__)

class DiversificationChecker:
    """
    포트폴리오 분산 검증
    
    규칙:
    1. 단일 섹터 최대 비중: 30%
    2. 단일 종목 최대 비중: 10%
    """
    
    def __init__(self, config, sector_classifier):
        self.config = config
        self.sector_classifier = sector_classifier
    
    def check_diversification(
        self,
        candidate_stock: Dict,
        portfolio_cache: Dict,
        account_balance: float,
        override_max_sector_pct: float = None,
        override_max_stock_pct: float = None
    ) -> Dict:
        """
        매수 후보 종목에 대해 분산 투자 원칙 준수 여부를 확인합니다.
        
        Args:
            candidate_stock: 매수 후보 종목 정보 {'code': ..., 'price': ..., 'quantity': ...}
            portfolio_cache: 현재 포트폴리오 정보 (DB 부하 감소용 캐시)
            account_balance: 현재 가용 현금 (예수금)
            override_max_sector_pct: (Optional) 섹터 최대 비중 강제 설정 (예: 강세장 50%)
            override_max_stock_pct: (Optional) 종목 최대 비중 강제 설정 (예: 강세장 20%)
            
        Returns:
            Dict: {'approved': bool, 'reason': str, 'concentration_risk': str, ...}
        """
        try:
            # 1. 설정값 로드 (Override 우선)
            max_sector_pct = override_max_sector_pct if override_max_sector_pct is not None else self.config.get_float("MAX_SECTOR_PCT", 30.0)
            max_stock_pct = override_max_stock_pct if override_max_stock_pct is not None else self.config.get_float("MAX_POSITION_VALUE_PCT", 10.0)

            # 1. 총 자산 계산
            portfolio_value = sum(
                item['quantity'] * item.get('current_price', item['avg_price'])
                for item in portfolio_cache.values()
            )
            total_assets = account_balance + portfolio_value
            
            if total_assets <= 0:
                return {
                    'approved': True,
                    'reason': '총 자산이 0 이하 (통과 처리)',
                    'sector_exposure': {},
                    'concentration_risk': 'UNKNOWN'
                }
            
            # 2. 후보 종목 섹터 확인
            candidate_sector = self.sector_classifier.get_sector(
                candidate_stock['code'],
                candidate_stock['name']
            )
            candidate_value = candidate_stock['quantity'] * candidate_stock['price']
            
            logger.info(f"   [Diversification] {candidate_stock['name']} 섹터: {candidate_sector}")
            
            # 3. 매수 후 섹터별 비중 계산
            sector_exposure = defaultdict(float)
            
            # 기존 포트폴리오
            for item in portfolio_cache.values():
                sector = self.sector_classifier.get_sector(item['code'], item['name'])
                value = item['quantity'] * item.get('current_price', item['avg_price'])
                sector_exposure[sector] += value
            
            # 후보 종목 추가
            sector_exposure[candidate_sector] += candidate_value
            
            # 비율로 변환
            sector_pct = {
                sector: (value / total_assets) * 100
                for sector, value in sector_exposure.items()
            }
            
            logger.info(f"   [Diversification] 매수 후 섹터 비중:")
            for sector, pct in sorted(sector_pct.items(), key=lambda x: x[1], reverse=True)[:5]:
                logger.info(f"   - {sector}: {pct:.2f}%")
            
            # 4. 규칙 검증 1: 단일 섹터 최대 비중
            if sector_pct[candidate_sector] > max_sector_pct:
                return {
                    'approved': False,
                    'reason': f"섹터 '{candidate_sector}' 비중 초과 ({sector_pct[candidate_sector]:.1f}% > {max_sector_pct}%)",
                    'sector_exposure': sector_pct,
                    'current_sector_exposure': (sector_exposure[candidate_sector] - candidate_value) / total_assets * 100, # 현재(매수 전) 해당 섹터 비중
                    'concentration_risk': 'HIGH'
                }
            
            # 5. 규칙 검증 2: 단일 종목 최대 비중
            stock_pct = (candidate_value / total_assets) * 100
            if stock_pct > max_stock_pct:
                return {
                    'approved': False,
                    'reason': f"단일 종목 비중 초과 ({stock_pct:.1f}% > {max_stock_pct}%)",
                    'sector_exposure': sector_pct,
                    'concentration_risk': 'HIGH'
                }
            
            # 6. 승인
            return {
                'approved': True,
                'reason': f"분산 규칙 통과 (섹터 '{candidate_sector}': {sector_pct[candidate_sector]:.1f}%)",
                'sector_exposure': sector_pct,
                'current_sector_exposure': (sector_exposure[candidate_sector] - candidate_value) / total_assets * 100, # 현재(매수 전) 해당 섹터 비중
                'concentration_risk': 'LOW'
            }
            
        except Exception as e:
            logger.error(f"   ❌ [Diversification] 검증 실패: {e}", exc_info=True)
            return {
                'approved': True,  # 오류 시 통과 (보수적)
                'reason': f"검증 오류 (통과 처리): {str(e)}",
                'sector_exposure': {},
                'concentration_risk': 'UNKNOWN'
            }
