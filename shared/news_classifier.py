# shared/news_classifier.py
# [v1.0] 뉴스 카테고리 분류기 - 경쟁사 수혜 분석 시스템
# 작업 LLM: Claude Opus 4.5
# 참조: GPT 제안 - 뉴스 카테고리 세분화

"""
뉴스 카테고리 분류기 (News Classifier)

GPT 제안 반영:
- 기존 카테고리 (실적, 수주, M&A 등)
- 신규 카테고리 (보안사고, 리콜, 오너리스크, 서비스장애)
- 악재 강도 매핑
- 경쟁사 수혜 점수 매핑
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import re
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# 뉴스 카테고리 정의 (GPT 제안 + 기존 카테고리 통합)
# ============================================================================

NEWS_CATEGORIES = {
    # ========== 호재 카테고리 ==========
    '실적호재': {
        'keywords': ['실적', '어닝', '매출', '영업이익', '순이익', '호실적', '서프라이즈', 
                    '흑자전환', '최대실적', '사상최대', '예상치 상회'],
        'sentiment': 'POSITIVE',
        'base_score': +15,
        'description': '실적 관련 호재'
    },
    '수주': {
        'keywords': ['수주', '계약', '공급계약', '납품', '협력', '파트너십', 'MOU', '양해각서'],
        'sentiment': 'POSITIVE',
        'base_score': +12,
        'description': '수주 및 계약 체결'
    },
    '신사업': {
        'keywords': ['신사업', '진출', '확장', '투자', '설비투자', 'CAPEX', '신규', '사업다각화'],
        'sentiment': 'POSITIVE',
        'base_score': +10,
        'description': '신사업 진출 및 투자'
    },
    'M&A': {
        'keywords': ['인수', '합병', '매각', 'M&A', '지분인수', '경영권', '공개매수'],
        'sentiment': 'MIXED',  # 인수는 호재, 피인수는 케이스바이케이스
        'base_score': +8,
        'description': '인수합병 관련'
    },
    '배당': {
        'keywords': ['배당', '주주환원', '자사주', '소각', '배당금', '중간배당', '특별배당'],
        'sentiment': 'POSITIVE',
        'base_score': +10,
        'description': '배당 및 주주환원'
    },
    '기술혁신': {
        'keywords': ['특허', '기술개발', '신기술', '혁신', 'R&D', '연구개발', '신제품', '출시'],
        'sentiment': 'POSITIVE',
        'base_score': +8,
        'description': '기술 혁신 및 특허'
    },
    
    # ========== 악재 카테고리 (신규 추가 - GPT 제안) ==========
    '보안사고': {
        'keywords': ['해킹', '유출', '개인정보', '보안', '침해', '랜섬웨어', '사이버공격', 
                    '데이터유출', '정보유출', '해커', '보안사고'],
        'sentiment': 'NEGATIVE',
        'base_score': -15,
        'competitor_benefit': +10,  # 경쟁사 수혜 점수
        'duration_days': 20,
        'confidence': 'HIGH',
        'description': '개인정보 유출, 해킹 등 보안사고'
    },
    '서비스장애': {
        'keywords': ['장애', '먹통', '접속불가', '서버다운', '시스템오류', '서비스중단',
                    '접속장애', '시스템장애', '서비스장애'],
        'sentiment': 'NEGATIVE',
        'base_score': -10,
        'competitor_benefit': +8,
        'duration_days': 7,
        'confidence': 'HIGH',
        'description': '서비스 장애 및 시스템 오류'
    },
    '리콜': {
        'keywords': ['리콜', '결함', '불량', '회수', '품질문제', '화재', '발화', '안전문제',
                    '자발적 리콜', '강제 리콜'],
        'sentiment': 'NEGATIVE',
        'base_score': -12,
        'competitor_benefit': +7,
        'duration_days': 30,
        'confidence': 'MEDIUM',
        'description': '대규모 리콜 및 품질 문제'
    },
    '오너리스크': {
        'keywords': ['구속', '기소', '횡령', '배임', '수사', '검찰', '체포', '재판',
                    '경영비리', '탈세', '뇌물', '부당거래'],
        'sentiment': 'NEGATIVE',
        'base_score': -12,
        'competitor_benefit': +3,  # 대체 효과 낮음
        'duration_days': 60,
        'confidence': 'LOW',
        'description': '오너/경영진 리스크'
    },
    '규제': {
        'keywords': ['과징금', '제재', '규제', '공정위', '시정명령', '독점', '담합',
                    '금융위', '벌금', '행정처분', '영업정지'],
        'sentiment': 'NEGATIVE',
        'base_score': -8,
        'competitor_benefit': +5,
        'duration_days': 30,
        'confidence': 'MEDIUM',
        'description': '규제 및 과징금'
    },
    '실적악화': {
        'keywords': ['어닝쇼크', '실적악화', '적자전환', '매출급감', '영업손실',
                    '적자', '부진', '예상치 하회', '실적부진'],
        'sentiment': 'NEGATIVE',
        'base_score': -10,
        'competitor_benefit': +4,
        'duration_days': 20,
        'confidence': 'MEDIUM',
        'description': '실적 악화 및 어닝쇼크'
    },
    '노사분쟁': {
        'keywords': ['파업', '노조', '노사갈등', '쟁의', '태업', '단체행동', '협상결렬'],
        'sentiment': 'NEGATIVE',
        'base_score': -7,
        'competitor_benefit': +5,
        'duration_days': 14,
        'confidence': 'MEDIUM',
        'description': '파업 및 노사 분쟁'
    },
    'ESG': {
        'keywords': ['환경오염', 'ESG', '탄소배출', '폐수', '유해물질', '갑질', 
                    '직장내괴롭힘', '산업재해', '환경규제'],
        'sentiment': 'NEGATIVE',
        'base_score': -6,
        'competitor_benefit': +3,
        'duration_days': 30,
        'confidence': 'LOW',
        'description': 'ESG 및 환경 이슈'
    },
    
    # ========== 중립 카테고리 ==========
    '시황': {
        'keywords': ['금리', '환율', '유가', 'FOMC', '금통위', '기준금리', '인플레이션',
                    '경기', '경제지표'],
        'sentiment': 'NEUTRAL',
        'base_score': 0,
        'description': '일반 시황 및 거시경제'
    },
    '인사': {
        'keywords': ['사장', '대표이사', '임원', '선임', '취임', '인사', 'CEO', '사임'],
        'sentiment': 'MIXED',
        'base_score': 0,
        'description': '경영진 인사 관련'
    },
}


# ============================================================================
# 뉴스 분류 결과 데이터 클래스
# ============================================================================

@dataclass
class NewsClassification:
    """뉴스 분류 결과"""
    category: str                    # 카테고리 이름
    sentiment: str                   # POSITIVE, NEGATIVE, NEUTRAL, MIXED
    base_score: int                  # 기본 점수
    competitor_benefit: int          # 경쟁사 수혜 점수 (악재인 경우)
    duration_days: int               # 영향 지속 기간
    confidence: str                  # 신뢰도 (HIGH, MEDIUM, LOW)
    matched_keywords: List[str]      # 매칭된 키워드 목록
    description: str                 # 카테고리 설명


# ============================================================================
# 뉴스 분류기 클래스
# ============================================================================

class NewsClassifier:
    """
    뉴스 제목/본문을 분석하여 카테고리를 분류합니다.
    
    사용 예:
        classifier = NewsClassifier()
        result = classifier.classify("쿠팡, 3370만명 개인정보 유출 사고 발생")
        print(result.category)  # '보안사고'
        print(result.competitor_benefit)  # 10
    """
    
    def __init__(self, categories: Dict = None):
        """
        Args:
            categories: 커스텀 카테고리 딕셔너리 (기본값: NEWS_CATEGORIES)
        """
        self.categories = categories or NEWS_CATEGORIES
        self._build_keyword_index()
    
    def _build_keyword_index(self):
        """키워드 인덱스 구축 (빠른 검색용)"""
        self._keyword_to_category = {}
        for category, info in self.categories.items():
            for keyword in info.get('keywords', []):
                keyword_lower = keyword.lower()
                if keyword_lower not in self._keyword_to_category:
                    self._keyword_to_category[keyword_lower] = []
                self._keyword_to_category[keyword_lower].append(category)
    
    def classify(self, text: str) -> Optional[NewsClassification]:
        """
        뉴스 텍스트를 분류합니다.
        
        Args:
            text: 뉴스 제목 또는 본문
        
        Returns:
            NewsClassification 또는 None (매칭 없음)
        """
        if not text:
            return None
        
        text_lower = text.lower()
        
        # 각 카테고리별 매칭 점수 계산
        category_scores = {}
        category_keywords = {}
        
        for category, info in self.categories.items():
            matched = []
            for keyword in info.get('keywords', []):
                if keyword.lower() in text_lower:
                    matched.append(keyword)
            
            if matched:
                # 매칭된 키워드 수 * 기본 점수 절대값 = 점수
                score = len(matched) * abs(info.get('base_score', 1))
                category_scores[category] = score
                category_keywords[category] = matched
        
        if not category_scores:
            return None
        
        # 가장 높은 점수의 카테고리 선택
        best_category = max(category_scores, key=category_scores.get)
        info = self.categories[best_category]
        
        return NewsClassification(
            category=best_category,
            sentiment=info.get('sentiment', 'NEUTRAL'),
            base_score=info.get('base_score', 0),
            competitor_benefit=info.get('competitor_benefit', 0),
            duration_days=info.get('duration_days', 0),
            confidence=info.get('confidence', 'MEDIUM'),
            matched_keywords=category_keywords[best_category],
            description=info.get('description', '')
        )
    
    def classify_batch(self, texts: List[str]) -> List[Optional[NewsClassification]]:
        """
        여러 뉴스 텍스트를 일괄 분류합니다.
        
        Args:
            texts: 뉴스 텍스트 리스트
        
        Returns:
            NewsClassification 리스트
        """
        return [self.classify(text) for text in texts]
    
    def is_negative_event(self, text: str) -> bool:
        """뉴스가 악재인지 확인"""
        result = self.classify(text)
        return result is not None and result.sentiment == 'NEGATIVE'
    
    def is_competitor_benefit_event(self, text: str) -> bool:
        """뉴스가 경쟁사 수혜 이벤트인지 확인"""
        result = self.classify(text)
        return result is not None and result.competitor_benefit > 0
    
    def get_competitor_benefit_score(self, text: str) -> int:
        """경쟁사 수혜 점수 반환"""
        result = self.classify(text)
        return result.competitor_benefit if result else 0
    
    def extract_negative_events(self, texts: List[str]) -> List[Tuple[str, NewsClassification]]:
        """
        여러 뉴스에서 악재만 추출합니다.
        
        Args:
            texts: 뉴스 텍스트 리스트
        
        Returns:
            [(원본 텍스트, 분류 결과)] 튜플 리스트
        """
        results = []
        for text in texts:
            classification = self.classify(text)
            if classification and classification.sentiment == 'NEGATIVE':
                results.append((text, classification))
        return results


# ============================================================================
# 유틸리티 함수
# ============================================================================

def get_negative_categories() -> List[str]:
    """악재 카테고리 목록 반환"""
    return [cat for cat, info in NEWS_CATEGORIES.items() 
            if info.get('sentiment') == 'NEGATIVE']


def get_competitor_benefit_categories() -> List[str]:
    """경쟁사 수혜가 있는 카테고리 목록 반환"""
    return [cat for cat, info in NEWS_CATEGORIES.items() 
            if info.get('competitor_benefit', 0) > 0]


def get_category_info(category: str) -> Optional[Dict]:
    """카테고리 상세 정보 반환"""
    return NEWS_CATEGORIES.get(category)


def format_classification_for_logging(classification: NewsClassification) -> str:
    """분류 결과를 로깅용 문자열로 포맷팅"""
    if not classification:
        return "분류 불가"
    
    emoji = "🔴" if classification.sentiment == 'NEGATIVE' else \
            "🟢" if classification.sentiment == 'POSITIVE' else "⚪"
    
    result = f"{emoji} [{classification.category}] {classification.description}"
    result += f" (점수: {classification.base_score:+d}"
    
    if classification.competitor_benefit > 0:
        result += f", 경쟁사 수혜: +{classification.competitor_benefit}"
    
    result += f", 키워드: {', '.join(classification.matched_keywords[:3])})"
    
    return result


# ============================================================================
# 싱글톤 인스턴스
# ============================================================================

_default_classifier = None

def get_classifier() -> NewsClassifier:
    """기본 뉴스 분류기 인스턴스 반환 (싱글톤)"""
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = NewsClassifier()
    return _default_classifier


# ============================================================================
# 호환성 함수 (CompetitorAnalyzer에서 사용)
# ============================================================================

def classify_news_category(title: str, summary: str = "") -> str:
    """
    뉴스 제목과 요약을 바탕으로 카테고리를 분류합니다.
    
    Args:
        title: 뉴스 제목
        summary: 뉴스 요약 (선택)
    
    Returns:
        카테고리 이름 (매칭 없으면 '기타')
    """
    classifier = get_classifier()
    text = f"{title} {summary}".strip()
    result = classifier.classify(text)
    return result.category if result else "기타"


def get_event_severity(category: str) -> int:
    """
    이벤트 카테고리에 따른 악재 심각도를 반환합니다.
    
    Args:
        category: 뉴스 카테고리
    
    Returns:
        심각도 점수 (음수일수록 심각)
    """
    info = NEWS_CATEGORIES.get(category)
    if info and info.get('sentiment') == 'NEGATIVE':
        return info.get('base_score', 0)
    return 0


def get_competitor_benefit(category: str) -> int:
    """
    이벤트 카테고리에 따른 경쟁사 수혜 점수를 반환합니다.
    
    Args:
        category: 뉴스 카테고리
    
    Returns:
        경쟁사 수혜 점수 (양수)
    """
    info = NEWS_CATEGORIES.get(category)
    if info:
        return info.get('competitor_benefit', 0)
    return 0


# ============================================================================
# 테스트
# ============================================================================

if __name__ == "__main__":
    # 테스트 뉴스 목록
    test_news = [
        "쿠팡, 3370만명 개인정보 유출 사고 발생",
        "삼성전자, 분기 실적 서프라이즈 기록",
        "카카오 서비스 전면 장애 발생... 이용자 불편 호소",
        "현대차, 전기차 리콜 20만대 발화 위험",
        "SK하이닉스, HBM 수주 대박... 목표가 상향",
        "네이버 대표이사 횡령 혐의로 검찰 소환",
        "공정위, 배달의민족에 과징금 500억 부과",
        "삼성SDI, 포드와 5조원 배터리 공급 계약 체결",
        "금리 인상 우려에 코스피 하락",
    ]
    
    classifier = get_classifier()
    
    print("=" * 70)
    print("뉴스 카테고리 분류 테스트")
    print("=" * 70)
    
    for news in test_news:
        result = classifier.classify(news)
        formatted = format_classification_for_logging(result)
        print(f"\n📰 {news}")
        print(f"   → {formatted}")
    
    print("\n" + "=" * 70)
    print("악재 카테고리:", get_negative_categories())
    print("경쟁사 수혜 카테고리:", get_competitor_benefit_categories())

