# youngs75_jennie/__init__.py
# Version: v3.5
# [패키지 루트] youngs75_jennie 패키지를 로딩하고 공통 로깅 기본값을 초기화합니다.
# 1. Python이 해당 폴더를 패키지로 인식하도록 합니다.
# 2. 패키지 전체가 동일한 스트림 로깅 포맷을 사용하도록 구성합니다.

import logging
import os

# 1. 로그 파일 경로 설정 (필요 시 주석 해제하여 파일 핸들러 추가)
# LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'trade.log')

# 2. 로깅 기본 설정 (GCP/로컬 콘솔 수집)
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    handlers=[
        # logging.FileHandler(LOG_FILE_PATH, encoding='utf-8'),
        logging.StreamHandler()  # 콘솔(터미널)에 출력 (GCP가 자동 수집)
    ]
)

# 3. 패키지 로거 생성 (이름: "youngs75_jennie")
logger = logging.getLogger(__name__) 
logger.info("--- 📦 'youngs75_jennie' 패키지 로드됨 ---")