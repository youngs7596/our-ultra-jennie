#!/usr/bin/env python3
"""MariaDB 데이터베이스 및 테이블 스키마 생성 스크립트"""
import os
import sys
from dotenv import load_dotenv

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine, text
from shared.db import models

# .env 파일 로드
load_dotenv()

def main():
    print("=" * 60)
    print("  MariaDB 초기 설정 (Database + Tables 생성)")
    print("=" * 60)
    
    # 1. 데이터베이스 없이 root 접속 (DB 생성용)
    user = os.getenv("MARIADB_USER", "root")
    password = os.getenv("MARIADB_PASSWORD")
    host = os.getenv("MARIADB_HOST", "localhost")
    port = os.getenv("MARIADB_PORT", "3306")
    dbname = os.getenv("MARIADB_DBNAME", "jennie_db")
    
    if not password:
        print("❌ MARIADB_PASSWORD 환경 변수가 설정되지 않았습니다!")
        return
    
    from urllib.parse import quote_plus
    user_enc = quote_plus(user)
    password_enc = quote_plus(password)
    
    # Database 없이 접속 (초기 생성용)
    admin_url = f"mysql+pymysql://{user_enc}:{password_enc}@{host}:{port}/?charset=utf8mb4"
    
    try:
        print(f"🔌 MariaDB 접속 중... ({host}:{port})")
        admin_engine = create_engine(admin_url, pool_pre_ping=True)
        
        with admin_engine.connect() as conn:
            # 데이터베이스 생성
            print(f"🏗️ 데이터베이스 '{dbname}' 생성 중...")
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {dbname} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"))
            conn.commit()
            print(f"✅ 데이터베이스 '{dbname}' 생성 완료.")
        
        # 2. 생성한 DB에 접속하여 테이블 스키마 생성
        db_url = f"mysql+pymysql://{user_enc}:{password_enc}@{host}:{port}/{dbname}?charset=utf8mb4"
        db_engine = create_engine(db_url, pool_pre_ping=True)
        
        print(f"🛠️ 테이블 스키마 생성 중... (SQLAlchemy models)")
        models.Base.metadata.create_all(db_engine)
        print(f"✅ 모든 테이블 생성 완료!")
        
        # 3. 생성된 테이블 목록 확인
        with db_engine.connect() as conn:
            result = conn.execute(text("SHOW TABLES;"))
            tables = [row[0] for row in result]
            print(f"\n📋 생성된 테이블 목록 ({len(tables)}개):")
            for table in tables:
                print(f"  - {table}")
        
        print("\n🎉 MariaDB 초기 설정 완료!")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

