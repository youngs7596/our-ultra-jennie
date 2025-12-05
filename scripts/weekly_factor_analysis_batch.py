#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_factor_analysis_batch.py
===============================
주간 팩터 분석 배치 잡

매주 1회 실행하여 다음 작업을 순차적으로 수행:
1. 뉴스 데이터 수집 (최근 7일)
2. 뉴스 감성/카테고리 태깅
3. DART 공시 수집 (최근 7일)
4. 외국인/기관 수급 데이터 수집 (최근 7일)
5. 분기별 재무 데이터 업데이트 (선택적)
6. 팩터 분석 실행

결과:
- FACTOR_METADATA 테이블 업데이트 (IC, IR, 가중치)
- FACTOR_PERFORMANCE 테이블 업데이트 (조건부 승률)
- NEWS_FACTOR_STATS 테이블 업데이트 (뉴스 카테고리별 영향도)

Scout-job, buy-scanner, price-monitor 등은 이 테이블에서 
최신 가중치와 통계를 로드하여 사용합니다.

사용법:
    # 기본 실행 (최근 7일 데이터 수집 + 팩터 분석)
    python scripts/weekly_factor_analysis_batch.py

    # 전체 데이터 재수집 (2년치) + 팩터 분석
    python scripts/weekly_factor_analysis_batch.py --full-refresh

    # 팩터 분석만 (데이터 수집 생략)
    python scripts/weekly_factor_analysis_batch.py --analysis-only

    # 특정 단계만 실행
    python scripts/weekly_factor_analysis_batch.py --step news
    python scripts/weekly_factor_analysis_batch.py --step trading
    python scripts/weekly_factor_analysis_batch.py --step analysis

Cron 설정 예시 (매주 일요일 오전 6시):
    0 6 * * 0 cd /path/to/project && python scripts/weekly_factor_analysis_batch.py >> logs/weekly_batch.log 2>&1
"""

import sys
import os
import subprocess
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트 경로 설정
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / 'logs' / 'weekly_factor_batch.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 로그 디렉토리 생성
(PROJECT_ROOT / 'logs').mkdir(exist_ok=True)


class WeeklyFactorAnalysisBatch:
    """주간 팩터 분석 배치 잡"""
    
    def __init__(self, full_refresh: bool = False, analysis_only: bool = False):
        self.full_refresh = full_refresh
        self.analysis_only = analysis_only
        self.scripts_dir = PROJECT_ROOT / 'scripts'
        self.results = {}
        
        # 데이터 수집 기간 설정
        if full_refresh:
            self.days = 730  # 2년
            self.max_pages = 50
            self.max_articles = 300
        else:
            self.days = 7  # 1주일
            self.max_pages = 5
            self.max_articles = 50
    
    def run_script(self, script_name: str, args: list = None) -> bool:
        """개별 스크립트 실행"""
        script_path = self.scripts_dir / script_name
        
        if not script_path.exists():
            logger.error(f"❌ 스크립트를 찾을 수 없습니다: {script_path}")
            return False
        
        cmd = [sys.executable, str(script_path)]
        if args:
            cmd.extend(args)
        
        logger.info(f"🚀 실행: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=7200  # 2시간 타임아웃
            )
            
            if result.returncode == 0:
                logger.info(f"✅ 완료: {script_name}")
                # 마지막 10줄만 출력
                if result.stdout:
                    for line in result.stdout.strip().split('\n')[-10:]:
                        logger.info(f"   {line}")
                return True
            else:
                logger.error(f"❌ 실패: {script_name} (exit code: {result.returncode})")
                if result.stderr:
                    for line in result.stderr.strip().split('\n')[-5:]:
                        logger.error(f"   {line}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"❌ 타임아웃: {script_name}")
            return False
        except Exception as e:
            logger.error(f"❌ 예외 발생: {script_name} - {e}")
            return False
    
    def step_collect_news(self) -> bool:
        """Step 1: 뉴스 데이터 수집"""
        logger.info("=" * 60)
        logger.info("📰 [Step 1/6] 네이버 뉴스 수집")
        logger.info("=" * 60)
        
        args = [
            '--codes', '200',
            '--days', str(self.days),
            '--max-pages', str(self.max_pages),
            '--max-articles', '600',  # 종목당 최대 600개 기사
            '--sleep', '2.0'
        ]
        
        return self.run_script('collect_naver_news.py', args)
    
    def step_tag_news(self) -> bool:
        """Step 2: 뉴스 감성/카테고리 태깅"""
        logger.info("=" * 60)
        logger.info("🏷️ [Step 2/6] 뉴스 감성/카테고리 태깅")
        logger.info("=" * 60)
        
        args = [
            '--days', str(self.days + 30),  # 여유있게
            '--limit', '100000',
            '--batch', '1000'
        ]
        
        return self.run_script('tag_news_sentiment.py', args)
    
    def step_collect_dart(self) -> bool:
        """Step 3: DART 공시 수집"""
        logger.info("=" * 60)
        logger.info("📋 [Step 3/6] DART 공시 수집")
        logger.info("=" * 60)
        
        args = [
            '--codes', '200',
            '--days', str(self.days)
        ]
        
        return self.run_script('collect_dart_filings.py', args)
    
    def step_collect_trading(self) -> bool:
        """Step 4: 외국인/기관 수급 데이터 수집"""
        logger.info("=" * 60)
        logger.info("📊 [Step 4/6] 외국인/기관 수급 데이터 수집")
        logger.info("=" * 60)
        
        args = [
            '--codes', '200',
            '--days', str(self.days)
        ]
        
        return self.run_script('collect_investor_trading.py', args)
    
    def step_collect_financials(self) -> bool:
        """Step 5: 분기별 재무 데이터 수집 (전체 새로고침 시에만)"""
        if not self.full_refresh:
            logger.info("=" * 60)
            logger.info("💰 [Step 5/6] 분기별 재무 데이터 수집 (건너뜀 - 주간 배치)")
            logger.info("=" * 60)
            return True
        
        logger.info("=" * 60)
        logger.info("💰 [Step 5/6] 분기별 재무 데이터 수집")
        logger.info("=" * 60)
        
        args = ['--codes', '200']
        
        return self.run_script('collect_quarterly_financials.py', args)
    
    def step_run_analysis(self) -> bool:
        """Step 6: 팩터 분석 실행"""
        logger.info("=" * 60)
        logger.info("🔬 [Step 6/6] 팩터 분석 실행")
        logger.info("=" * 60)
        
        args = ['--days', '730']  # 항상 2년치 데이터로 분석
        
        if self.full_refresh:
            args.append('--full')
        
        return self.run_script('run_factor_analysis.py', args)
    
    def run_all(self) -> dict:
        """전체 배치 실행"""
        start_time = datetime.now()
        
        logger.info("=" * 60)
        logger.info("🚀 주간 팩터 분석 배치 시작")
        logger.info(f"   시작 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"   모드: {'전체 새로고침' if self.full_refresh else '증분 업데이트'}")
        logger.info(f"   데이터 기간: {self.days}일")
        logger.info("=" * 60)
        
        steps = [
            ('news', self.step_collect_news),
            ('tag', self.step_tag_news),
            ('dart', self.step_collect_dart),
            ('trading', self.step_collect_trading),
            ('financials', self.step_collect_financials),
            ('analysis', self.step_run_analysis),
        ]
        
        # 분석만 모드
        if self.analysis_only:
            steps = [('analysis', self.step_run_analysis)]
        
        for step_name, step_func in steps:
            try:
                self.results[step_name] = step_func()
            except Exception as e:
                logger.error(f"❌ {step_name} 단계 예외: {e}")
                self.results[step_name] = False
        
        end_time = datetime.now()
        duration = end_time - start_time
        
        # 결과 요약
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 배치 실행 결과")
        logger.info("=" * 60)
        
        success_count = sum(1 for v in self.results.values() if v)
        total_count = len(self.results)
        
        for step_name, success in self.results.items():
            status = "✅" if success else "❌"
            logger.info(f"   {status} {step_name}")
        
        logger.info("")
        logger.info(f"   성공: {success_count}/{total_count}")
        logger.info(f"   소요 시간: {duration}")
        logger.info(f"   완료 시간: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)
        
        return self.results
    
    def run_single_step(self, step: str) -> bool:
        """단일 단계 실행"""
        step_map = {
            'news': self.step_collect_news,
            'tag': self.step_tag_news,
            'dart': self.step_collect_dart,
            'trading': self.step_collect_trading,
            'financials': self.step_collect_financials,
            'analysis': self.step_run_analysis,
        }
        
        if step not in step_map:
            logger.error(f"❌ 알 수 없는 단계: {step}")
            logger.info(f"   사용 가능한 단계: {', '.join(step_map.keys())}")
            return False
        
        return step_map[step]()


def main():
    parser = argparse.ArgumentParser(
        description='주간 팩터 분석 배치 잡',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
    # 기본 실행 (증분 업데이트)
    python scripts/weekly_factor_analysis_batch.py
    
    # 전체 새로고침 (2년치)
    python scripts/weekly_factor_analysis_batch.py --full-refresh
    
    # 팩터 분석만 실행
    python scripts/weekly_factor_analysis_batch.py --analysis-only
    
    # 특정 단계만 실행
    python scripts/weekly_factor_analysis_batch.py --step news
    python scripts/weekly_factor_analysis_batch.py --step analysis
        """
    )
    
    parser.add_argument(
        '--full-refresh',
        action='store_true',
        help='전체 데이터 재수집 (2년치, 시간 오래 걸림)'
    )
    
    parser.add_argument(
        '--analysis-only',
        action='store_true',
        help='팩터 분석만 실행 (데이터 수집 건너뜀)'
    )
    
    parser.add_argument(
        '--step',
        type=str,
        choices=['news', 'tag', 'dart', 'trading', 'financials', 'analysis'],
        help='특정 단계만 실행'
    )
    
    args = parser.parse_args()
    
    batch = WeeklyFactorAnalysisBatch(
        full_refresh=args.full_refresh,
        analysis_only=args.analysis_only
    )
    
    if args.step:
        success = batch.run_single_step(args.step)
        sys.exit(0 if success else 1)
    else:
        results = batch.run_all()
        all_success = all(results.values())
        sys.exit(0 if all_success else 1)


if __name__ == '__main__':
    main()

