#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# crawler_job.py
# Version: v9.1
# 작업 LLM: Claude Opus 4.5
# Crawler Job - Cloud Scheduler(HTTP)에 의해 10분마다 실행되는 스크립트
# [v9.0] KOSPI 200 전체 뉴스 수집 (WatchList 의존성 제거)
# [v9.1] 경쟁사 수혜 분석 연동 (Claude Opus 4.5)

from concurrent.futures import ThreadPoolExecutor, as_completed
import time
# import chromadb  # Lazy import로 변경 (초기화 시간 단축)
import sys
import json
import urllib.parse
import feedparser # type: ignore
import logging
import os 
import calendar
from dotenv import load_dotenv 
from datetime import datetime, timedelta, timezone

# [v9.0] FinanceDataReader for KOSPI 200 Universe
try:
    import FinanceDataReader as fdr
    FDR_AVAILABLE = True
except ImportError:
    FDR_AVAILABLE = False

# 'youngs75_jennie' 패키지를 찾기 위해 프로젝트 루트 폴더를 Python 경로에 추가
# Dockerfile에서 /app/crawler_job.py로 복사되므로, /app이 프로젝트 루트
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.append(PROJECT_ROOT)

# ==============================================================================
# 로거(Logger) 설정
# ==============================================================================
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

try:
    import shared.auth as auth
    import shared.database as database
    from shared.llm import JennieBrain # 감성 분석을 위한 JennieBrain 임포트
    from shared.gemini import ensure_gemini_api_key
    # [v9.1] 경쟁사 수혜 분석 모듈
    from shared.news_classifier import NewsClassifier, get_classifier
    from shared.hybrid_scoring.competitor_analyzer import CompetitorAnalyzer
    logger.info("✅ 'shared' 패키지 모듈 import 성공")
except ImportError as e: # type: ignore
    logger.error(f"🚨 'shared' 공용 패키지를 찾을 수 없습니다! (오류: {e})")
    auth = None
    database = None
    JennieBrain = None
    ensure_gemini_api_key = None
    NewsClassifier = None
    get_classifier = None
    CompetitorAnalyzer = None
except Exception as e:
    logger.error(f"🚨 'shared' 패키지 import 중 예상치 못한 오류 발생: {e}", exc_info=True)
    auth = None
    database = None
    JennieBrain = None
    ensure_gemini_api_key = None
    NewsClassifier = None
    get_classifier = None
    CompetitorAnalyzer = None

from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ==============================================================================
# 1. 전역 설정 (Constants)
# ==============================================================================

# Chroma 서버
CHROMA_SERVER_HOST = os.getenv("CHROMA_SERVER_HOST", "10.178.0.2") 
CHROMA_SERVER_PORT = 8000
COLLECTION_NAME = "rag_stock_data"

# RAG 설정
DATA_TTL_DAYS = 7
VERTEX_AI_BATCH_SIZE = 10
MAX_SENTIMENT_DOCS_PER_RUN = int(os.getenv("MAX_SENTIMENT_DOCS_PER_RUN", "40"))
SENTIMENT_COOLDOWN_SECONDS = float(os.getenv("SENTIMENT_COOLDOWN_SECONDS", "0.2"))

# --- 🔽 '일반 경제' RSS 피드 🔽 ---
GENERAL_RSS_FEEDS = [
    {"source_name": "Maeil Business (Economy)", "url": "https://www.mk.co.kr/rss/50000001/"},
    {"source_name": "Maeil Business (Stock)", "url": "https://www.mk.co.kr/rss/50100001/"},
    {"source_name": "Investing.com (News)", "url": "https://kr.investing.com/rss/news.rss"}
]

# ==============================================================================
# LangChain, Chroma 클라이언트 초기화
# ==============================================================================

# ==============================================================================
# 전역 변수 (지연 초기화)
# ==============================================================================

# 환경 변수 로드 (모듈 임포트 시)
load_dotenv()

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
DB_SERVICE_NAME = os.getenv("OCI_DB_SERVICE_NAME")
WALLET_DIR_NAME = os.getenv("OCI_WALLET_DIR_NAME", "wallet")
WALLET_PATH = os.path.join(PROJECT_ROOT, WALLET_DIR_NAME)

# 지연 초기화를 위한 전역 변수 (None으로 시작)
embeddings = None
text_splitter = None
db_client = None
vectorstore = None
jennie_brain = None # JennieBrain 인스턴스

def initialize_services():
    """
    LangChain 및 ChromaDB 서비스를 초기화합니다.
    run_collection_job() 실행 시에만 호출됩니다.
    """
    global embeddings, text_splitter, db_client, vectorstore, jennie_brain
    
    logger.info("... [RAG Crawler v8.1] LangChain 및 AI 컴포넌트 초기화 시작 ...")
    try:
        api_key = ensure_gemini_api_key()
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=api_key,
        )
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        logger.info("✅ LangChain 컴포넌트(Embedding, Splitter) 초기화 성공.")
        
        # JennieBrain 초기화 (감성 분석용)
        try:
            jennie_brain = JennieBrain(
                project_id=GCP_PROJECT_ID,
                gemini_api_key_secret=os.getenv("SECRET_ID_GEMINI_API_KEY")
            )
            logger.info("✅ JennieBrain (감성 분석기) 초기화 성공.")
        except Exception as e:
            logger.warning(f"⚠️ JennieBrain 초기화 실패 (감성 분석 Skip): {e}")
            jennie_brain = None

    except Exception as e:
        logger.exception("🔥 LangChain 컴포넌트 초기화 실패!")
        raise
    
    logger.info(f"... [RAG Crawler v8.1] Chroma 서버 ({CHROMA_SERVER_HOST}:{CHROMA_SERVER_PORT}) 연결 시도 ...")
    try:
        # Lazy import: chromadb는 실제 사용 시점에만 import
        import chromadb
        
        db_client = chromadb.HttpClient(host=CHROMA_SERVER_HOST, port=CHROMA_SERVER_PORT)
        vectorstore = Chroma(client=db_client, collection_name=COLLECTION_NAME, embedding_function=embeddings)
        db_client.heartbeat() 
        logger.info(f"✅ Chroma 서버 ({CHROMA_SERVER_HOST}) 연결 성공!")
    except Exception as e:
        logger.exception(f"🔥 Chroma 서버 ({CHROMA_SERVER_HOST}) 연결 실패!")
        raise

# ==============================================================================
# 핵심 함수 정의
# ==============================================================================

def get_kospi_200_universe():
    """
    [v9.0] KOSPI 시가총액 상위 200개 종목을 가져옵니다.
    Scout와 동일한 Universe를 사용하여 뉴스를 수집합니다.
    """
    universe_size = int(os.getenv("SCOUT_UNIVERSE_SIZE", "200"))
    logger.info(f"  (1/6) [v9.0] KOSPI 시총 상위 {universe_size}개 종목 로드 중...")
    
    # 1. FinanceDataReader 시도
    if FDR_AVAILABLE:
        try:
            logger.info("  (1/6) FinanceDataReader로 KOSPI 종목 조회 중...")
            df = fdr.StockListing('KOSPI')
            
            if df is not None and not df.empty:
                # 시가총액 기준 정렬 (Marcap 또는 Market Cap 컬럼)
                cap_col = None
                for col in ['Marcap', 'MarCap', 'Market Cap', 'marcap']:
                    if col in df.columns:
                        cap_col = col
                        break
                
                if cap_col:
                    df = df.sort_values(by=cap_col, ascending=False)
                
                # 상위 N개 추출
                top_stocks = df.head(universe_size)
                
                # Code, Name 컬럼 찾기
                code_col = 'Code' if 'Code' in top_stocks.columns else 'Symbol'
                name_col = 'Name' if 'Name' in top_stocks.columns else 'name'
                
                universe = []
                for _, row in top_stocks.iterrows():
                    code = str(row.get(code_col, '')).zfill(6)
                    name = row.get(name_col, f'종목_{code}')
                    if code and len(code) == 6:
                        universe.append({"code": code, "name": name})
                
                if universe:
                    logger.info(f"✅ (1/6) FinanceDataReader로 {len(universe)}개 종목 로드 완료!")
                    return universe
        except Exception as e:
            logger.warning(f"⚠️ (1/6) FinanceDataReader 실패: {e}")
    
    # 2. Fallback: DB의 WatchList 사용
    logger.info("  (1/6) Fallback: DB WatchList 조회 중...")
    return get_watchlist_from_db()


def get_watchlist_from_db():
    """
    [v9.0] DB에서 WatchList를 조회합니다 (Fallback용).
    """
    db_conn = None
    try:
        db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), GCP_PROJECT_ID)
        db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), GCP_PROJECT_ID)
        
        db_conn = database.get_db_connection(
            db_user=db_user,
            db_password=db_password,
            db_service_name=DB_SERVICE_NAME,
            wallet_path=WALLET_PATH
        )
        if not db_conn:
            logger.error("🔥 (1/6) DB 연결에 실패했습니다. (Skip)")
            return []
 
        cursor = db_conn.cursor()
        sql = "SELECT stock_code, stock_name FROM WATCHLIST"
        cursor.execute(sql)
        
        watchlist = []
        for row in cursor.fetchall():
            watchlist.append({"code": row[0], "name": row[1]})
 
        logger.info(f"✅ (1/6) 'WatchList' {len(watchlist)}개 로드 성공.")
        return watchlist
        
    except Exception as e:
        logger.exception(f"🔥 (1/6) DB 'get_watchlist_from_db' 함수 실행 중 오류 발생!")
        return []
    finally:
        if db_conn:
            db_conn.close()
            logger.info("... (1/6) DB 연결이 종료되었습니다.")

def get_numeric_timestamp(feed_entry):
    """
    feed_entry에서 '발행 시간'을 UTC 기준 숫자 타임스탬프로 변환합니다.
    """
    if hasattr(feed_entry, 'published_parsed') and feed_entry.published_parsed:
        try:
            return int(calendar.timegm(feed_entry.published_parsed))
        except Exception:
            return int(datetime.now(timezone.utc).timestamp())
    else:
        return int(datetime.now(timezone.utc).timestamp())

def crawl_news_for_stock(stock_code, stock_name):
    """
    Google News RSS를 사용하여 특정 종목의 뉴스를 수집합니다.
    """
    logger.info(f"  (2/6) [App 5] '{stock_name}({stock_code})' Google News RSS 피드 수집 중...")
    documents = []
    try:
        query = f'"{stock_name}" OR "{stock_code}"'
        encoded_query = urllib.parse.quote(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(rss_url)
        
        if not feed.entries:
            logger.info(f"  (2/6) '{stock_name}' 관련 신규 뉴스가 없습니다. (Skip)")
            return []

        for entry in feed.entries:
            doc = Document(
                page_content=f"뉴스 제목: {entry.title}\n링크: {entry.link}",
                metadata={
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "source": f"Google News RSS ({entry.get('source', {}).get('title', 'N/A')})",
                    "source_url": entry.link, 
                    "created_at_utc": get_numeric_timestamp(entry)
                }
            )
            documents.append(doc)
    except Exception as e:
        logger.exception(f"🔥 (2/6) '{stock_name}' 뉴스 수집 중 오류 발생")
    return documents

def crawl_general_news():
    """
    미리 정의된 'GENERAL_RSS_FEEDS' 목록의 일반 경제 뉴스를 수집합니다.
    """
    logger.info(f"  (3/6) [App 5] '일반 경제' RSS {len(GENERAL_RSS_FEEDS)}개 피드 수집 중...")
    documents = []
    
    for feed_info in GENERAL_RSS_FEEDS:
        source = feed_info["source_name"]
        url = feed_info["url"]
        logger.info(f"  (3/6) ... '{source}' 수집 중 ...")
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                logger.info(f"  (3/6) '{source}'에 신규 뉴스가 없습니다. (Skip)")
                continue

            for entry in feed.entries:
                doc = Document(
                    page_content=f"뉴스 제목: {entry.title}\n링크: {entry.link}",
                    metadata={
                        "source": source,
                        "source_url": entry.link, 
                        "created_at_utc": get_numeric_timestamp(entry)
                    }
                )
                documents.append(doc)
        except Exception as e:
            logger.exception(f"🔥 (3/6) '{source}' 뉴스 수집 중 오류 발생")
            
    logger.info(f"✅ (3/6) '일반 경제' 뉴스 총 {len(documents)}개 수집 완료.")
    return documents

def filter_new_documents(documents):
    """
    ChromaDB에 'source_url'이 이미 존재하는지 확인하여 새로운 문서만 필터링합니다.
    """
    step_id = "(4/6)"
    logger.info(f"  {step_id} [App 5] 수집된 문서 {len(documents)}개 일괄 중복 검사 시작...")
    if not documents:
        return []

    urls_to_check = list(set([doc.metadata["source_url"] for doc in documents if "source_url" in doc.metadata]))
    if not urls_to_check:
        return documents

    existing_results = vectorstore.get(where={"source_url": {"$in": urls_to_check}})
    existing_urls = set(item['source_url'] for item in existing_results.get('metadatas', []))
    new_docs = [doc for doc in documents if doc.metadata.get("source_url") not in existing_urls]

    logger.info(f"✅ {step_id} 중복 검사 완료. 새로운 문서 {len(new_docs)}개 발견.")
    return new_docs

def process_sentiment_analysis(documents):
    """
    [New] 수집된 뉴스 중 종목 뉴스에 대해 실시간 감성 분석을 수행합니다.
    분석 결과는 Redis 및 Oracle DB에 저장됩니다.
    """
    if not jennie_brain or not documents:
        return

    logger.info(f"  [Sentiment] 신규 문서 {len(documents)}개에 대한 감성 분석 시작...")
    
    # DB 연결 (저장용)
    db_conn = None
    try:
        db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), GCP_PROJECT_ID)
        db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), GCP_PROJECT_ID)
        db_conn = database.get_db_connection(db_user, db_password, DB_SERVICE_NAME, WALLET_PATH)
    except Exception as e:
        logger.error(f"❌ [Sentiment] DB 연결 실패: {e}")

    processed_count = 0
    for doc in documents:
        if 0 < MAX_SENTIMENT_DOCS_PER_RUN <= processed_count:
            logger.info(
                "  [Sentiment] 1회 실행당 분석 제한(%s개)에 도달했습니다. 나머지는 다음 주기에 처리됩니다.",
                MAX_SENTIMENT_DOCS_PER_RUN
            )
            break

        stock_code = doc.metadata.get("stock_code")
        # 종목 코드가 있는 뉴스만 분석 (일반 경제 뉴스는 제외)
        if not stock_code:
            continue
            
        title = doc.metadata.get("source", "제목 없음").replace("Google News RSS", "") # 메타데이터 구조에 따라 조정 필요. 
        # 위 크롤링 로직을 보면 metadata['source']는 출처명이고, 제목은 page_content에 있음.
        # page_content 파싱 필요: "뉴스 제목: {title}\n링크: {link}"
        content_lines = doc.page_content.split('\n')
        news_title = content_lines[0].replace("뉴스 제목: ", "") if len(content_lines) > 0 else "제목 없음"
        news_link = doc.metadata.get("source_url")
        published_at = doc.metadata.get("created_at_utc")

        # 1. LLM 감성 분석
        try:
            result = jennie_brain.analyze_news_sentiment(news_title, news_title)
            score = result.get('score', 50)
            reason = result.get('reason', '분석 불가')
        except Exception as e:
            logger.warning(f"⚠️ [Sentiment] 분석 중 오류 (Skip): {e}")
            continue

        # 2. Redis 저장 (Fast Hands용)
        try:
            database.set_sentiment_score(stock_code, score, reason)
        except Exception as e:
            logger.warning(f"⚠️ [Sentiment] Redis 저장 실패 (Skip): {e}")
            continue
        
        # 3. Oracle DB 저장 (기록용)
        if db_conn:
            try:
                database.save_news_sentiment(db_conn, stock_code, news_title, score, reason, news_link, published_at)
            except Exception as e:
                logger.warning(f"⚠️ [Sentiment] DB 저장 실패 (Skip): {e}")
                continue
        
        processed_count += 1
        
        if SENTIMENT_COOLDOWN_SECONDS > 0:
            time.sleep(SENTIMENT_COOLDOWN_SECONDS)
            
    if db_conn:
        db_conn.close()
        
    logger.info(f"✅ [Sentiment] 종목 뉴스 {processed_count}건 감성 분석 및 저장 완료.")


def process_competitor_benefit_analysis(documents):
    """
    [v9.1] 뉴스에서 경쟁사 수혜 기회를 분석합니다.
    
    악재(보안사고, 리콜, 오너리스크 등) 발생 시:
    1. 해당 종목의 경쟁사들을 조회
    2. 수혜 점수를 계산하여 Redis에 저장
    3. DB에 이벤트 기록
    """
    if not get_classifier or not CompetitorAnalyzer or not documents:
        return
    
    logger.info(f"  [경쟁사 수혜] 신규 문서 {len(documents)}개 경쟁사 수혜 분석 시작...")
    
    # 모듈 초기화
    classifier = get_classifier()
    competitor_analyzer = CompetitorAnalyzer()
    
    # DB 연결 (SQLAlchemy)
    from shared.db.connection import init_engine, get_session
    from shared.db.models import IndustryCompetitors, CompetitorBenefitEvents
    from datetime import timedelta
    
    try:
        init_engine(None, None, None, None)
        session = get_session()
    except Exception as e:
        logger.error(f"❌ [경쟁사 수혜] DB 연결 실패: {e}")
        return
    
    benefit_events_created = 0
    
    for doc in documents:
        stock_code = doc.metadata.get("stock_code")
        if not stock_code:
            continue
        
        # 뉴스 제목 추출
        content_lines = doc.page_content.split('\n')
        news_title = content_lines[0].replace("뉴스 제목: ", "") if len(content_lines) > 0 else ""
        news_link = doc.metadata.get("source_url")
        
        # 1. 뉴스 분류
        classification = classifier.classify(news_title)
        if not classification:
            continue
        
        # 2. 악재인지 확인 (경쟁사 수혜가 있는 카테고리만)
        if classification.sentiment != 'NEGATIVE' or classification.competitor_benefit <= 0:
            continue
        
        logger.info(f"  🔴 [악재 감지] {stock_code} - {classification.category}: {news_title[:50]}...")
        
        # 3. 해당 종목의 섹터 및 경쟁사 조회
        affected_stock = session.query(IndustryCompetitors).filter(
            IndustryCompetitors.stock_code == stock_code
        ).first()
        
        if not affected_stock:
            logger.debug(f"     → {stock_code}는 경쟁사 매핑에 없음 (Skip)")
            continue
        
        sector_code = affected_stock.sector_code
        sector_name = affected_stock.sector_name
        affected_name = affected_stock.stock_name
        
        # 4. 동일 섹터 경쟁사 조회
        competitors = session.query(IndustryCompetitors).filter(
            IndustryCompetitors.sector_code == sector_code,
            IndustryCompetitors.stock_code != stock_code,
            IndustryCompetitors.is_active == 1
        ).all()
        
        if not competitors:
            logger.debug(f"     → {sector_name} 섹터에 경쟁사 없음 (Skip)")
            continue
        
        # 5. 각 경쟁사에 대해 수혜 이벤트 생성
        expires_at = datetime.now(timezone.utc) + timedelta(days=classification.duration_days)
        
        for competitor in competitors:
            # 기존 동일 이벤트가 있는지 확인 (24시간 내 중복 방지)
            existing = session.query(CompetitorBenefitEvents).filter(
                CompetitorBenefitEvents.affected_stock_code == stock_code,
                CompetitorBenefitEvents.beneficiary_stock_code == competitor.stock_code,
                CompetitorBenefitEvents.event_type == classification.category,
                CompetitorBenefitEvents.detected_at >= datetime.now(timezone.utc) - timedelta(hours=24)
            ).first()
            
            if existing:
                logger.debug(f"     → {competitor.stock_name} 이미 이벤트 존재 (Skip)")
                continue
            
            # 수혜 이벤트 생성
            benefit_event = CompetitorBenefitEvents(
                affected_stock_code=stock_code,
                affected_stock_name=affected_name,
                event_type=classification.category,
                event_title=news_title[:1000],
                event_severity=classification.base_score,
                source_url=news_link,
                beneficiary_stock_code=competitor.stock_code,
                beneficiary_stock_name=competitor.stock_name,
                benefit_score=classification.competitor_benefit,
                sector_code=sector_code,
                sector_name=sector_name,
                status='ACTIVE',
                expires_at=expires_at
            )
            session.add(benefit_event)
            benefit_events_created += 1
            
            logger.info(
                f"  ✅ [수혜 등록] {competitor.stock_name}({competitor.stock_code}) "
                f"+{classification.competitor_benefit}점 ← {affected_name} {classification.category}"
            )
            
            # 6. Redis에 수혜 점수 저장 (Scout Job에서 활용)
            try:
                database.set_competitor_benefit_score(
                    stock_code=competitor.stock_code,
                    score=classification.competitor_benefit,
                    reason=f"경쟁사 {affected_name}의 {classification.category}로 인한 수혜",
                    affected_stock=stock_code,
                    event_type=classification.category,
                    ttl=classification.duration_days * 86400
                )
            except Exception as e:
                logger.warning(f"⚠️ [경쟁사 수혜] Redis 저장 실패: {e}")
    
    # 커밋
    try:
        session.commit()
        logger.info(f"✅ [경쟁사 수혜] 수혜 이벤트 {benefit_events_created}건 생성 완료")
    except Exception as e:
        session.rollback()
        logger.error(f"❌ [경쟁사 수혜] DB 커밋 실패: {e}")
    finally:
        session.close()


def add_documents_to_chroma(documents):
    """
    새로운 Document 리스트를 분할(Chunking) 후 벡터로 변환하여 ChromaDB에 저장합니다.
    """
    step_id = "(5/6)"
    if not documents:
        logger.info(f"  {step_id} [App 5] Chroma에 저장할 새로운 문서가 없습니다. (Skip Write)")
        return

    logger.info(f"  {step_id} [App 5] '새' 문서 {len(documents)}개 텍스트 분할 및 임베딩 중...")
    try:
        splitted_docs = text_splitter.split_documents(documents)
        
        for i in range(0, len(splitted_docs), VERTEX_AI_BATCH_SIZE): # type: ignore
            batch_docs = splitted_docs[i : i + VERTEX_AI_BATCH_SIZE]
            logger.info(f"  {step_id} [App 4] '새' 청크 {i+1} ~ {i+len(batch_docs)}번 (총 {len(batch_docs)}개) 저장 시도...")
            vectorstore.add_documents(
                batch_docs
            )
        
        logger.info(f"✅ {step_id} [App 4] Chroma 서버에 '새' 청크 총 {len(splitted_docs)}개 저장 완료!")
    except Exception as e:
        logger.exception(f"🔥 {step_id} [App 4] Chroma 서버에 'Write' 중 심각한 오류 발생")

def cleanup_old_data_job():
    """
    DATA_TTL_DAYS(7일)가 지난 오래된 뉴스 데이터를 ChromaDB에서 삭제합니다.
    """
    logger.info(f"\n[데이터 정리] {DATA_TTL_DAYS}일 경과한 오래된 RAG 데이터 삭제 시작...")
    try:
        ttl_limit_timestamp = int((datetime.now(timezone.utc) - timedelta(days=DATA_TTL_DAYS)).timestamp())
        collection = vectorstore._collection
        
        logger.info(f"... [데이터 정리] created_at_utc < {ttl_limit_timestamp} 데이터 삭제 중 ...")
        collection.delete(where={"created_at_utc": {"$lt": ttl_limit_timestamp}})
        
        logger.info("✅ [데이터 정리] 오래된 데이터 삭제 완료.")
    except Exception as e:
        logger.warning(f"⚠️ [데이터 정리] 데이터 삭제 중 오류 발생: {e}")

# ==============================================================================
# 메인 작업 실행 함수
# ==============================================================================

def run_collection_job():
    """
    뉴스 수집 및 저장을 위한 메인 태스크.
    이 함수가 스크립트의 '진입점(Entrypoint)'이 됩니다.
    [v9.0] KOSPI 200 전체 뉴스 수집 (Scout Universe와 동일)
    """
    logger.info(f"\n--- [RAG 수집 봇 v9.0] 작업 시작 ---")
    
    # 서비스 초기화 (지연 초기화)
    try:
        initialize_services()
    except Exception as e:
        logger.error(f"🔥 서비스 초기화 실패: {e}")
        return
    
    try:
        all_fetched_documents = []

        # 1. '일반 경제' RSS 수집
        general_news_docs = crawl_general_news()
        all_fetched_documents.extend(general_news_docs)

        # 2. [v9.0] KOSPI 200 Universe 로드 (Scout와 동일)
        universe = get_kospi_200_universe()
        logger.info(f"  (2/6) [v9.0] KOSPI Universe {len(universe)}개 종목 뉴스 수집 시작...")

        # 3. 각 종목별 뉴스 크롤링을 병렬로 실행
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_stock = {executor.submit(crawl_news_for_stock, stock["code"], stock["name"]): stock for stock in universe}
            for future in as_completed(future_to_stock):
                stock = future_to_stock[future]
                try:
                    fetched_docs = future.result()
                    all_fetched_documents.extend(fetched_docs)
                except Exception as exc:
                    logger.error(f"🔥 '{stock['name']}' 뉴스 수집 스레드에서 오류 발생: {exc}")

        # 4. '새로운' 문서만 필터링 (Deduplication)
        new_documents_to_add = filter_new_documents(all_fetched_documents)
        
        # [New] 4-1. 새로운 문서 감성 분석 및 저장
        process_sentiment_analysis(new_documents_to_add)
        
        # [v9.1] 4-2. 경쟁사 수혜 분석 및 저장
        process_competitor_benefit_analysis(new_documents_to_add)
        
        # 5. '새로운' 문서만 Chroma 서버에 저장 (Write)
        add_documents_to_chroma(new_documents_to_add)
        
        # 6. 오래된 데이터 정리
        cleanup_old_data_job()
        
        logger.info(f"--- [RAG 수집 봇 v9.1] 작업 완료 ---")
        
    except Exception as e:
        logger.exception(f"🔥 [RAG 수집 봇 v9.0] 메인 작업 중 심각한 오류 발생")

# =============================================================================
# 메인 실행 블록
# =============================================================================

if __name__ == "__main__":
    
    start_time = time.time()

    # 메인 작업 실행
    try:
        run_collection_job()
    except Exception as e:
        logger.critical(f"❌ [RAG Crawler v8.1] 'run_collection_job' 실행 중 알 수 없는 오류: {e}")
        
    end_time = time.time()
    logger.info(f"--- [RAG 수집 봇 v8.1] 스크립트 종료 (총 소요시간: {end_time - start_time:.2f}초) ---")
