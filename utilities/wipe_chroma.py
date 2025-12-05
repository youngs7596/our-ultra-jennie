# wipe_chroma.py
# [v1.0] ChromaDB의 벡터 데이터를 리셋하는 스크립트
#
# [주의!] 이 스크립트는 'rag_stock_data' 컬렉션의 모든 데이터를 삭제합니다!

import chromadb
import logging
import sys
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# --- 로거(Logger) 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Chroma 서버 설정 (환경 변수에서 로드) ---
# Docker 로컬: localhost:8000
CHROMA_SERVER_HOST = os.getenv("CHROMA_SERVER_HOST", "localhost")
CHROMA_SERVER_PORT = int(os.getenv("CHROMA_PORT", "8000"))
COLLECTION_NAME = "rag_stock_data"

def wipe_collection():
    logger.info("="*60)
    logger.info(f" [DB 리셋] '{COLLECTION_NAME}' 컬렉션 삭제 작업 시작...")
    logger.info(f" - Chroma 서버: {CHROMA_SERVER_HOST}:{CHROMA_SERVER_PORT}")
    logger.info("="*60)

    try:
        # 1. 서버 연결
        db_client = chromadb.HttpClient(
            host=CHROMA_SERVER_HOST,
            port=CHROMA_SERVER_PORT
        )
        db_client.heartbeat()
        logger.info(f"✅ 연결 성공!")

        # 2. 컬렉션 삭제 (Try-Except)
        try:
            logger.warning(f"... '{COLLECTION_NAME}' 컬렉션 삭제 시도 ...")
            db_client.delete_collection(name=COLLECTION_NAME)
            logger.info(f"✅ 삭제 성공! ('{COLLECTION_NAME}' 컬렉션이 삭제되었습니다.)")
        except Exception as e:
            logger.error(f"🔥 '{COLLECTION_NAME}' 컬렉션 삭제 실패! (오류: {e})")
            logger.warning("   (이미 삭제되었거나, 존재하지 않는 컬렉션일 수 있습니다.)")
            logger.warning("   (신규 생성을 위해 다음 단계로 진행합니다.)")

        # 3. 컬렉션 재생성 (빈 껍데기)
        # (App이 바로 Write할 수 있도록, 새 컬렉션을 미리 만들어줍니다)
        logger.info(f"... '{COLLECTION_NAME}' 컬렉션 재생성 시도 ...")
        db_client.create_collection(
            name=COLLECTION_NAME,
            # (참고: 나중에 다른 임베딩 모델을 사용하게 되면
            #  여기서 metadata={"hnsw:space": "cosine"} 등을 지정해야 할 수 있습니다)
        )
        logger.info(f"✅ 재생성 성공! ('{COLLECTION_NAME}'이 '빈 껍데기'로 준비되었습니다.)")
        
        # 4. 확인
        count = db_client.get_collection(name=COLLECTION_NAME).count()
        logger.info(f"✅ [최종 확인] 현재 '{COLLECTION_NAME}'의 청크 개수: {count} 개")
        logger.info("="*60)
        logger.info(f" [DB 리셋] 완료! 이제 데이터 수집 스크립트를 실행하세요!")
        logger.info("="*60)

    except Exception as e:
        logger.exception(f"🔥 DB 리셋 작업 실패!")
        sys.exit(1)

if __name__ == "__main__":
    wipe_collection()
