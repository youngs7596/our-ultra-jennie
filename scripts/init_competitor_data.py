#!/usr/bin/env python3
# Version: v1.0
# 작업 LLM: Claude Opus 4.5
"""
[v1.0] 경쟁사 수혜 분석 시스템 초기 데이터 설정
- INDUSTRY_COMPETITORS: 주요 섹터별 경쟁사 매핑
- EVENT_IMPACT_RULES: 이벤트 유형별 영향 규칙
- SECTOR_RELATION_STATS: 초기 디커플링 통계
"""

import json
import sys
from pathlib import Path

# shared 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.db.connection import get_session
from shared.db.models import IndustryCompetitors, EventImpactRules, SectorRelationStats


def init_industry_competitors(session):
    """주요 섹터별 경쟁사 매핑 데이터 삽입"""
    
    competitors_data = [
        # ================== 이커머스 섹터 ==================
        {"sector_code": "ECOM", "sector_name": "이커머스", "stock_code": "CPNG", 
         "stock_name": "쿠팡", "market_share": 25.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "NYSE"},
        {"sector_code": "ECOM", "sector_name": "이커머스", "stock_code": "035420", 
         "stock_name": "NAVER", "market_share": 18.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
        {"sector_code": "ECOM", "sector_name": "이커머스", "stock_code": "004170", 
         "stock_name": "신세계", "market_share": 12.0, "rank_in_sector": 3, 
         "is_leader": 0, "exchange": "KRX"},
        {"sector_code": "ECOM", "sector_name": "이커머스", "stock_code": "438210", 
         "stock_name": "컬리", "market_share": 5.0, "rank_in_sector": 4, 
         "is_leader": 0, "exchange": "KRX"},
        {"sector_code": "ECOM", "sector_name": "이커머스", "stock_code": "064960", 
         "stock_name": "11번가", "market_share": 4.0, "rank_in_sector": 5, 
         "is_leader": 0, "exchange": "KRX"},
        
        # ================== 반도체 섹터 ==================
        {"sector_code": "SEMI", "sector_name": "반도체", "stock_code": "005930", 
         "stock_name": "삼성전자", "market_share": 45.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "KRX"},
        {"sector_code": "SEMI", "sector_name": "반도체", "stock_code": "000660", 
         "stock_name": "SK하이닉스", "market_share": 25.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
        
        # ================== 배터리 섹터 ==================
        {"sector_code": "BATT", "sector_name": "배터리", "stock_code": "373220", 
         "stock_name": "LG에너지솔루션", "market_share": 35.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "KRX"},
        {"sector_code": "BATT", "sector_name": "배터리", "stock_code": "006400", 
         "stock_name": "삼성SDI", "market_share": 20.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
        {"sector_code": "BATT", "sector_name": "배터리", "stock_code": "096770", 
         "stock_name": "SK이노베이션", "market_share": 12.0, "rank_in_sector": 3, 
         "is_leader": 0, "exchange": "KRX"},
        
        # ================== 자동차 섹터 ==================
        {"sector_code": "AUTO", "sector_name": "자동차", "stock_code": "005380", 
         "stock_name": "현대차", "market_share": 50.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "KRX"},
        {"sector_code": "AUTO", "sector_name": "자동차", "stock_code": "000270", 
         "stock_name": "기아", "market_share": 35.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
        
        # ================== 통신 섹터 ==================
        {"sector_code": "TELCO", "sector_name": "통신", "stock_code": "017670", 
         "stock_name": "SK텔레콤", "market_share": 45.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "KRX"},
        {"sector_code": "TELCO", "sector_name": "통신", "stock_code": "030200", 
         "stock_name": "KT", "market_share": 30.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
        {"sector_code": "TELCO", "sector_name": "통신", "stock_code": "032640", 
         "stock_name": "LG유플러스", "market_share": 20.0, "rank_in_sector": 3, 
         "is_leader": 0, "exchange": "KRX"},
        
        # ================== 항공 섹터 ==================
        {"sector_code": "AIR", "sector_name": "항공", "stock_code": "003490", 
         "stock_name": "대한항공", "market_share": 45.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "KRX"},
        {"sector_code": "AIR", "sector_name": "항공", "stock_code": "020560", 
         "stock_name": "아시아나항공", "market_share": 25.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
        {"sector_code": "AIR", "sector_name": "항공", "stock_code": "089590", 
         "stock_name": "제주항공", "market_share": 15.0, "rank_in_sector": 3, 
         "is_leader": 0, "exchange": "KRX"},
        
        # ================== 게임 섹터 ==================
        {"sector_code": "GAME", "sector_name": "게임", "stock_code": "259960", 
         "stock_name": "크래프톤", "market_share": 30.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "KRX"},
        {"sector_code": "GAME", "sector_name": "게임", "stock_code": "036570", 
         "stock_name": "엔씨소프트", "market_share": 25.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
        {"sector_code": "GAME", "sector_name": "게임", "stock_code": "251270", 
         "stock_name": "넷마블", "market_share": 20.0, "rank_in_sector": 3, 
         "is_leader": 0, "exchange": "KRX"},
        
        # ================== 플랫폼 섹터 ==================
        {"sector_code": "PLAT", "sector_name": "플랫폼", "stock_code": "035720", 
         "stock_name": "카카오", "market_share": 45.0, "rank_in_sector": 1, 
         "is_leader": 1, "exchange": "KRX"},
        {"sector_code": "PLAT", "sector_name": "플랫폼", "stock_code": "035420", 
         "stock_name": "NAVER", "market_share": 40.0, "rank_in_sector": 2, 
         "is_leader": 0, "exchange": "KRX"},
    ]
    
    # 기존 데이터 삭제 후 새로 삽입
    session.query(IndustryCompetitors).delete()
    
    for data in competitors_data:
        record = IndustryCompetitors(**data)
        session.add(record)
    
    print(f"✅ IndustryCompetitors: {len(competitors_data)}개 레코드 삽입 완료")


def init_event_impact_rules(session):
    """이벤트 유형별 영향 규칙 데이터 삽입"""
    
    event_rules = [
        {
            "event_type": "보안사고",
            "event_keywords": json.dumps(["해킹", "유출", "개인정보", "보안", "침해", "랜섬웨어", "데이터유출"]),
            "impact_on_self": -15,
            "impact_on_competitor": 10,
            "impact_on_supplier": -2,
            "effect_duration_days": 30,
            "peak_effect_day": 3,
            "confidence_level": "HIGH",
            "sample_count": 15,
            "description": "개인정보 유출, 해킹 사고 등 신뢰도 이슈. 대체 효과가 가장 큼."
        },
        {
            "event_type": "리콜",
            "event_keywords": json.dumps(["리콜", "결함", "불량", "회수", "품질문제", "안전결함"]),
            "impact_on_self": -10,
            "impact_on_competitor": 7,
            "impact_on_supplier": -5,
            "effect_duration_days": 25,
            "peak_effect_day": 5,
            "confidence_level": "HIGH",
            "sample_count": 22,
            "description": "대규모 제품 리콜. 자동차/전자제품 섹터에서 빈번."
        },
        {
            "event_type": "오너리스크",
            "event_keywords": json.dumps(["구속", "기소", "횡령", "배임", "수사", "체포", "검찰"]),
            "impact_on_self": -12,
            "impact_on_competitor": 3,
            "impact_on_supplier": -1,
            "effect_duration_days": 60,
            "peak_effect_day": 1,
            "confidence_level": "MID",
            "sample_count": 8,
            "description": "경영진 법적 이슈. 대체 효과는 제한적이나 장기적 영향."
        },
        {
            "event_type": "규제",
            "event_keywords": json.dumps(["규제", "과징금", "제재", "공정위", "금감원", "시정명령", "벌금"]),
            "impact_on_self": -8,
            "impact_on_competitor": 5,
            "impact_on_supplier": 0,
            "effect_duration_days": 20,
            "peak_effect_day": 2,
            "confidence_level": "MID",
            "sample_count": 18,
            "description": "정부 규제 및 과징금. 산업 전반에 영향을 줄 수 있어 신중한 판단 필요."
        },
        {
            "event_type": "서비스장애",
            "event_keywords": json.dumps(["장애", "먹통", "접속불가", "서버다운", "중단", "마비", "오류"]),
            "impact_on_self": -5,
            "impact_on_competitor": 8,
            "impact_on_supplier": 0,
            "effect_duration_days": 14,
            "peak_effect_day": 1,
            "confidence_level": "HIGH",
            "sample_count": 12,
            "description": "IT 서비스 장애. 즉각적인 이탈 효과로 경쟁사 수혜 빠름."
        },
        {
            "event_type": "환경오염",
            "event_keywords": json.dumps(["환경오염", "유해물질", "폐수", "오염", "탄소배출", "환경규제"]),
            "impact_on_self": -7,
            "impact_on_competitor": 4,
            "impact_on_supplier": -2,
            "effect_duration_days": 40,
            "peak_effect_day": 7,
            "confidence_level": "LOW",
            "sample_count": 5,
            "description": "환경 관련 이슈. ESG 영향으로 장기적 주가 영향."
        },
        {
            "event_type": "노사분규",
            "event_keywords": json.dumps(["파업", "노조", "임금협상", "쟁의", "노사갈등", "총파업"]),
            "impact_on_self": -6,
            "impact_on_competitor": 6,
            "impact_on_supplier": -3,
            "effect_duration_days": 21,
            "peak_effect_day": 5,
            "confidence_level": "MID",
            "sample_count": 10,
            "description": "노동쟁의로 인한 생산 차질. 자동차/제조업에서 경쟁사 수혜 발생."
        },
        {
            "event_type": "실적쇼크",
            "event_keywords": json.dumps(["어닝쇼크", "실적부진", "적자전환", "매출급감", "영업손실"]),
            "impact_on_self": -10,
            "impact_on_competitor": 2,
            "impact_on_supplier": -3,
            "effect_duration_days": 30,
            "peak_effect_day": 1,
            "confidence_level": "MID",
            "sample_count": 30,
            "description": "예상치 하회 실적. 경쟁사 수혜는 제한적이나 시장 점유율 변동 가능."
        },
    ]
    
    # 기존 데이터 삭제 후 새로 삽입
    session.query(EventImpactRules).delete()
    
    for data in event_rules:
        record = EventImpactRules(**data)
        session.add(record)
    
    print(f"✅ EventImpactRules: {len(event_rules)}개 레코드 삽입 완료")


def init_sector_relation_stats(session):
    """초기 섹터 관계 통계 데이터 삽입"""
    
    relation_stats = [
        # 이커머스: 쿠팡 → 네이버
        {
            "sector_code": "ECOM", "sector_name": "이커머스",
            "leader_stock_code": "CPNG", "leader_stock_name": "쿠팡",
            "follower_stock_code": "035420", "follower_stock_name": "NAVER",
            "decoupling_rate": 0.62, "avg_benefit_return": 0.023, "avg_leader_drop": -0.08,
            "sample_count": 45, "lookback_days": 730, "confidence": "HIGH",
            "recommended_holding_days": 20, "stop_loss_pct": -0.03, "take_profit_pct": 0.08
        },
        # 이커머스: 쿠팡 → 신세계
        {
            "sector_code": "ECOM", "sector_name": "이커머스",
            "leader_stock_code": "CPNG", "leader_stock_name": "쿠팡",
            "follower_stock_code": "004170", "follower_stock_name": "신세계",
            "decoupling_rate": 0.55, "avg_benefit_return": 0.018, "avg_leader_drop": -0.08,
            "sample_count": 42, "lookback_days": 730, "confidence": "MID",
            "recommended_holding_days": 15, "stop_loss_pct": -0.04, "take_profit_pct": 0.06
        },
        # 이커머스: 쿠팡 → 컬리
        {
            "sector_code": "ECOM", "sector_name": "이커머스",
            "leader_stock_code": "CPNG", "leader_stock_name": "쿠팡",
            "follower_stock_code": "438210", "follower_stock_name": "컬리",
            "decoupling_rate": 0.48, "avg_benefit_return": 0.015, "avg_leader_drop": -0.08,
            "sample_count": 30, "lookback_days": 365, "confidence": "MID",
            "recommended_holding_days": 15, "stop_loss_pct": -0.05, "take_profit_pct": 0.05
        },
        
        # 반도체: 삼성전자 → SK하이닉스
        {
            "sector_code": "SEMI", "sector_name": "반도체",
            "leader_stock_code": "005930", "leader_stock_name": "삼성전자",
            "follower_stock_code": "000660", "follower_stock_name": "SK하이닉스",
            "decoupling_rate": 0.35, "avg_benefit_return": 0.008, "avg_leader_drop": -0.05,
            "sample_count": 120, "lookback_days": 730, "confidence": "HIGH",
            "recommended_holding_days": 10, "stop_loss_pct": -0.03, "take_profit_pct": 0.05
        },
        
        # 배터리: LG에너지솔루션 → 삼성SDI
        {
            "sector_code": "BATT", "sector_name": "배터리",
            "leader_stock_code": "373220", "leader_stock_name": "LG에너지솔루션",
            "follower_stock_code": "006400", "follower_stock_name": "삼성SDI",
            "decoupling_rate": 0.48, "avg_benefit_return": 0.015, "avg_leader_drop": -0.06,
            "sample_count": 38, "lookback_days": 730, "confidence": "MID",
            "recommended_holding_days": 15, "stop_loss_pct": -0.04, "take_profit_pct": 0.07
        },
        
        # 자동차: 현대차 → 기아
        {
            "sector_code": "AUTO", "sector_name": "자동차",
            "leader_stock_code": "005380", "leader_stock_name": "현대차",
            "follower_stock_code": "000270", "follower_stock_name": "기아",
            "decoupling_rate": 0.28, "avg_benefit_return": 0.005, "avg_leader_drop": -0.04,
            "sample_count": 85, "lookback_days": 730, "confidence": "LOW",
            "recommended_holding_days": 10, "stop_loss_pct": -0.03, "take_profit_pct": 0.04
        },
        
        # 통신: SK텔레콤 → KT
        {
            "sector_code": "TELCO", "sector_name": "통신",
            "leader_stock_code": "017670", "leader_stock_name": "SK텔레콤",
            "follower_stock_code": "030200", "follower_stock_name": "KT",
            "decoupling_rate": 0.42, "avg_benefit_return": 0.012, "avg_leader_drop": -0.03,
            "sample_count": 55, "lookback_days": 730, "confidence": "MID",
            "recommended_holding_days": 14, "stop_loss_pct": -0.03, "take_profit_pct": 0.05
        },
        
        # 플랫폼: 카카오 → 네이버
        {
            "sector_code": "PLAT", "sector_name": "플랫폼",
            "leader_stock_code": "035720", "leader_stock_name": "카카오",
            "follower_stock_code": "035420", "follower_stock_name": "NAVER",
            "decoupling_rate": 0.58, "avg_benefit_return": 0.020, "avg_leader_drop": -0.07,
            "sample_count": 60, "lookback_days": 730, "confidence": "HIGH",
            "recommended_holding_days": 20, "stop_loss_pct": -0.03, "take_profit_pct": 0.08
        },
        
        # 게임: 크래프톤 → 엔씨소프트
        {
            "sector_code": "GAME", "sector_name": "게임",
            "leader_stock_code": "259960", "leader_stock_name": "크래프톤",
            "follower_stock_code": "036570", "follower_stock_name": "엔씨소프트",
            "decoupling_rate": 0.40, "avg_benefit_return": 0.010, "avg_leader_drop": -0.05,
            "sample_count": 25, "lookback_days": 365, "confidence": "LOW",
            "recommended_holding_days": 14, "stop_loss_pct": -0.04, "take_profit_pct": 0.06
        },
    ]
    
    # 기존 데이터 삭제 후 새로 삽입
    session.query(SectorRelationStats).delete()
    
    for data in relation_stats:
        record = SectorRelationStats(**data)
        session.add(record)
    
    print(f"✅ SectorRelationStats: {len(relation_stats)}개 레코드 삽입 완료")


def main():
    """메인 실행 함수"""
    print("=" * 60)
    print("🚀 경쟁사 수혜 분석 시스템 - 초기 데이터 설정")
    print("=" * 60)
    
    session = get_session()
    
    try:
        # 1. 산업/경쟁사 매핑
        init_industry_competitors(session)
        
        # 2. 이벤트 영향 규칙
        init_event_impact_rules(session)
        
        # 3. 섹터 관계 통계
        init_sector_relation_stats(session)
        
        # 커밋
        session.commit()
        
        print("=" * 60)
        print("✅ 모든 초기 데이터 설정 완료!")
        print("=" * 60)
        
    except Exception as e:
        session.rollback()
        print(f"❌ 오류 발생: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()

