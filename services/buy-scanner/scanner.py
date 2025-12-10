# services/buy-scanner/scanner.py
# Version: v3.5
# Buy Scanner - 매수 신호 스캔 로직

import time
import logging
import sys
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.database as database
import shared.auth as auth
import shared.strategy as strategy
from shared.market_regime import MarketRegimeDetector, StrategySelector

# [v3.7] SQLAlchemy ORM 기반으로 리팩토링
from shared.db.connection import session_scope
from shared.db.repository import (
    get_active_watchlist,
    get_active_portfolio,
    get_recently_traded_stocks_batch,
)
from shared.factor_scoring import FactorScorer
from shared.strategy_presets import (
    apply_preset_to_config,
    resolve_preset_for_regime,
)
from strategy import bear_strategies

logger = logging.getLogger(__name__)


class BuyScanner:
    """매수 신호 스캔 클래스"""
    
    # 상수 정의
    BB_DISTANCE_THRESHOLD_PCT = 2.0
    RSI_OVERSOLD_BULL_THRESHOLD = 40
    MOMENTUM_SIGNAL_THRESHOLD = 3.0
    RELATIVE_STRENGTH_THRESHOLD = 2.0
    
    def __init__(self, kis, config):
        """
        Args:
            kis: KIS API 클라이언트
            config: ConfigManager 인스턴스
        """
        self.kis = kis
        self.config = config
        self.regime_detector = MarketRegimeDetector()
        self.strategy_selector = StrategySelector()
        self.factor_scorer = FactorScorer()
        
        # 캐시
        self._kospi_cache = None
        self._kospi_cache_date = None
        self._market_analysis_cache = None
        self._market_analysis_timestamp = 0 # [v3.5] Timestamp 기반 캐싱 (1시간 TTL)
        self._daily_prices_cache = None
        self._daily_prices_cache_date = None
    
    def scan_buy_opportunities(self) -> dict:
        """
        매수 신호 스캔
        
        Cloud Run은 Stateless이므로 매 요청마다 DB 연결을 직접 생성/종료합니다.
        Connection Pool을 사용하지 않아 Cold Start 시간을 최소화합니다.
        
        Returns:
            {
                "candidates": [...],
                "market_regime": "BULL",
                "scan_timestamp": "2025-11-17T10:00:00Z"
            }
        """
        scan_start_time = time.time()
        logger.info("=== 매수 신호 스캔 시작 ===")
        logger.info("Step 1: DB 연결 (Stateless 모드 자동 지원)...")
        
        try:
            # SQLAlchemy 세션 사용
            with session_scope(readonly=True) as session:
                logger.info("Step 2: DB 연결 성공! 시장 분석 시작...")
                
                # 1. 시장 분석
                market_analysis = self._analyze_market_regime(session)
            
            if not market_analysis:
                logger.error("시장 분석 실패")
                return None
            
            current_regime = market_analysis['regime']
            active_strategies = market_analysis['active_strategies']
            market_context_dict = market_analysis['market_context_dict']
            risk_setting = market_analysis.get('risk_setting', {})
            
            allow_bear_trading = self.config.get_bool('ALLOW_BEAR_TRADING', default=False)
            min_bear_confidence = self.config.get_int('MIN_LLM_CONFIDENCE_BEAR', default=80)
            bear_context = None

            with session_scope(readonly=True) as session:
                # 2. Watchlist 조회
                watchlist = get_active_watchlist(session)
                if not watchlist:
                    logger.info("Watchlist가 비어있습니다.")
                    return {
                        "candidates": [],
                        "market_regime": current_regime,
                        "scan_timestamp": datetime.now(timezone.utc).isoformat()
                    }

                # 하락장에서는 기본 중단, 단 설정에 따라 제한적 스캔 허용
                if current_regime == MarketRegimeDetector.REGIME_BEAR:
                    if not allow_bear_trading:
                        logger.warning("📉 하락장 감지! 매수 활동 중단 (ALLOW_BEAR_TRADING=false)")
                        return {
                            "candidates": [],
                            "market_regime": current_regime,
                            "scan_timestamp": datetime.now(timezone.utc).isoformat()
                        }
                    filtered_watchlist = {}
                    for code, info in watchlist.items():
                        metadata = info.get('llm_metadata') or {}
                        bear_strategy = metadata.get('bear_strategy')
                        llm_grade = metadata.get('llm_grade') or info.get('llm_grade')
                        if not bear_strategy or not llm_grade:
                            continue
                        strategy_meta = bear_strategy.get('market_regime_strategy', {})
                        if (
                            strategy_meta.get('decision') == 'TRADABLE'
                            and strategy_meta.get('strategy_type') != 'DO_NOT_TRADE'
                            and strategy_meta.get('confidence_score', 0) >= min_bear_confidence
                            and llm_grade in ('S', 'A', 'B')
                        ):
                            enriched = info.copy()
                            enriched['bear_strategy'] = bear_strategy
                            enriched['llm_grade'] = llm_grade
                            enriched['is_tradable'] = True
                            filtered_watchlist[code] = enriched
                    if not filtered_watchlist:
                        logger.warning("📉 하락장 제한적 매수 조건을 충족하는 후보가 없습니다.")
                        return {
                            "candidates": [],
                            "market_regime": current_regime,
                            "scan_timestamp": datetime.now(timezone.utc).isoformat()
                        }
                    watchlist = filtered_watchlist
                    bear_context = {
                        "position_ratio": self.config.get_float('BEAR_POSITION_RATIO', default=0.2),
                        "stop_loss_atr_mult": self.config.get_float('BEAR_STOP_LOSS_ATR_MULT', default=2.0),
                        "tp_pct": self.config.get_float('BEAR_FIRST_TP_PCT', default=0.03),
                        "partial_ratio": self.config.get_float('BEAR_PARTIAL_CLOSE_RATIO', default=0.5),
                        "volume_multiplier": self.config.get_float('BEAR_VOLUME_SPIKE_MULTIPLIER', default=1.5),
                        "atr_period": 14,
                        "bear_mode": True,
                    }
                    risk_setting = {
                        "stop_loss_pct": -0.02,
                        "target_profit_pct": 0.03,
                        "position_size_ratio": bear_context["position_ratio"],
                    }
                    logger.info(f"📉 제한적 매수 허용: {len(watchlist)}개 후보 (LLM B등급 이상)")
                
                # 3. Portfolio 조회 (중복 방지)
                current_portfolio = get_active_portfolio(session)
                owned_codes = {item['code'] for item in current_portfolio}
                
                # [Tiered Execution] 현금 비중 확인
                try:
                    available_cash = self.kis.get_cash_balance()
                    # 포트폴리오 가치 추정 (매수가 기준)
                    portfolio_value = sum([p.get('quantity', 0) * p.get('buy_price', 0) for p in current_portfolio])
                    total_assets = available_cash + portfolio_value
                    
                    cash_ratio = available_cash / total_assets if total_assets > 0 else 0
                    tier2_enabled = cash_ratio > 0.3
                    
                    logger.info(f"💰 자산 현황: 현금 {available_cash:,}원 / 총자산 {total_assets:,}원 (현금비중 {cash_ratio*100:.1f}%)")
                    if tier2_enabled:
                        logger.info("✨ [Tiered Execution] 현금 비중 30% 초과 -> Tier 2 (비주력) 종목 스캔 활성화")
                except Exception as e:
                    logger.warning(f"현금 비중 계산 실패 (Tier 2 비활성): {e}")
                    tier2_enabled = False
                
                logger.info(f"스캔 대상: {len(watchlist)}개 종목 (보유: {len(owned_codes)}개 제외)")
                
                # 4. 종목 스캔 (병렬 처리)
                buy_candidates = self._scan_stocks_parallel(
                    watchlist, owned_codes, current_regime, active_strategies, session, tier2_enabled, bear_context
                )
                
                # 5. 팩터 점수 기준 정렬 및 상위 5개 선정
                if buy_candidates:
                    buy_candidates.sort(key=lambda x: x.get('factor_score', 0), reverse=True)
                    top_5_candidates = buy_candidates[:5]
                    
                    logger.info(f"✅ 상위 5개 후보 선정 완료")
                    for idx, candidate in enumerate(top_5_candidates, 1):
                        logger.info(f"  {idx}. {candidate['name']}({candidate['code']}): {candidate['factor_score']:.2f}")
                    
                    scan_duration = time.time() - scan_start_time
                    logger.info(f"=== 스캔 완료 (소요: {scan_duration:.1f}초) ===")
                    
                    return {
                        "candidates": [self._serialize_candidate(c) for c in top_5_candidates],
                        "market_regime": current_regime,
                        "market_context": market_context_dict,
                        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
                        "risk_setting": risk_setting,
                        "strategy_preset": market_analysis.get('strategy_preset'),
                    }
                else:
                    logger.info("매수 후보가 없습니다.")
                    return {
                        "candidates": [],
                        "market_regime": current_regime,
                        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
                        "strategy_preset": market_analysis.get('strategy_preset'),
                    }
        
        except Exception as e:
            logger.error(f"❌ 스캔 중 오류: {e}", exc_info=True)
            return None
    
    def _analyze_market_regime(self, session) -> dict:
        """시장 상황 분석"""
        try:
            current_ts = time.time()
            
            # [v3.5] 프로세스 캐시 확인 (1시간 = 3600초 TTL)
            if (self._market_analysis_cache is not None and 
                current_ts - self._market_analysis_timestamp < 3600):
                logger.info(f"시장 분석 캐시 사용 (Age: {int(current_ts - self._market_analysis_timestamp)}s)")
                return self._market_analysis_cache

            # [v3.5+] Redis 공유 캐시 확인
            redis_cache = database.get_market_regime_cache()
            if redis_cache:
                logger.info("🔁 Redis Regime 캐시 사용 (공유)")
                self._market_analysis_cache = redis_cache
                self._market_analysis_timestamp = current_ts
                return redis_cache
            
            # KOSPI 데이터 조회
            kospi_code = "0001"
            ma_period = self.config.get_int('MARKET_INDEX_MA_PERIOD', default=20)
            kospi_prices_df = database.get_daily_prices(session, kospi_code, limit=ma_period, table_name="STOCK_DAILY_PRICES_3Y")
            
            if kospi_prices_df.empty or len(kospi_prices_df) < ma_period:
                raise Exception("KOSPI 과거 데이터 부족")
            
            # KOSPI 현재가 조회
            trading_mode = os.getenv("TRADING_MODE", "MOCK")
            if trading_mode == "MOCK":
                kospi_current_price = float(kospi_prices_df['CLOSE_PRICE'].iloc[-1])
                logger.info(f"MOCK 모드: KOSPI 현재가 = {kospi_current_price}")
            else:
                kospi_snapshot = self.kis.get_stock_snapshot(stock_code=kospi_code, is_index=True)
                if not kospi_snapshot:
                    raise Exception("KOSPI 실시간 스냅샷 조회 실패")
                kospi_current_price = kospi_snapshot['price']
            
            # KOSPI 현재가가 0이거나 유효하지 않은 경우 방어 로직
            if kospi_current_price <= 0:
                logger.warning(f"⚠️ KOSPI 현재가 조회 오류 (price={kospi_current_price}). 전일 종가로 대체합니다.")
                if not kospi_prices_df.empty:
                    kospi_current_price = float(kospi_prices_df['CLOSE_PRICE'].iloc[-1])
                    logger.info(f"   → 대체된 KOSPI 가격: {kospi_current_price}")
                else:
                    raise Exception("KOSPI 현재가 0 및 과거 데이터 없음")
            
            # 시장 상황 분석
            current_regime, regime_context = self.regime_detector.detect_regime(
                kospi_prices_df, kospi_current_price
            )
            
            # 전략 선택
            active_strategies = self.strategy_selector.select_strategies(current_regime)

            preset_name, preset_params = resolve_preset_for_regime(current_regime)
            apply_preset_to_config(self.config, preset_params)
            logger.info("전략 프리셋 적용: %s (%s)", preset_name, preset_params)
            
            # [v3.5] 동적 리스크 설정 가져오기
            risk_setting = self.regime_detector.get_dynamic_risk_setting(current_regime)
            
            # 캐시 저장
            market_context_dict = regime_context.copy()
            market_context_dict["regime"] = current_regime
            market_context_dict["active_strategies"] = active_strategies
            market_context_dict["risk_setting"] = risk_setting
            
            result = {
                'regime': current_regime,
                'active_strategies': active_strategies,
                'market_context_dict': market_context_dict,
                'risk_setting': risk_setting, # Top-level에도 추가
                'strategy_preset': {
                    'name': preset_name,
                    'params': preset_params,
                },
            }
            
            self._market_analysis_cache = result
            self._market_analysis_timestamp = current_ts
            database.set_market_regime_cache(result, ttl_seconds=3600)
            
            logger.info(f"시장 분석 완료: {current_regime}, 전략: {active_strategies}")
            return result
            
        except Exception as e:
            logger.error(f"시장 분석 오류: {e}", exc_info=True)
            # 기본값 반환
            return {
                'regime': MarketRegimeDetector.REGIME_SIDEWAYS,
                'active_strategies': [StrategySelector.STRATEGY_MEAN_REVERSION],
                'market_context_dict': {"error": str(e)}
            }
    
    def _scan_stocks_parallel(self, watchlist, owned_codes, current_regime, 
                             active_strategies, session, tier2_enabled=False,
                             bear_context=None) -> list:
        """종목 병렬 스캔"""
        buy_candidates = []
        filter_stats_lock = Lock()
        
        # 1. 거래 가능한 종목 필터링
        # [Tiered Execution] tier2_enabled가 True면 is_tradable 여부 상관없이(False도 포함) 스캔
        tradable_codes = []
        for stock_code, stock_info in watchlist.items():
            is_tradable = stock_info.get('is_tradable', True) or tier2_enabled
            if bear_context is not None:
                is_tradable = stock_info.get('bear_strategy') is not None
            if is_tradable and stock_code not in owned_codes:
                tradable_codes.append(stock_code)
        
        # 2. 최근 거래 종목 제외
        recently_traded_codes = get_recently_traded_stocks_batch(session, tradable_codes, hours=4)
        stock_codes_to_scan = [code for code in tradable_codes if code not in recently_traded_codes]
        
        logger.info(f"스캔 대상: {len(stock_codes_to_scan)}개 (최근 거래 제외: {len(recently_traded_codes)}개)")
        
        if not stock_codes_to_scan:
            return []

        # 3. 일봉 데이터 배치 조회
        # 4. KOSPI 데이터 (상대 강도 계산용)
        with session_scope(readonly=True) as db_session:
            daily_prices_dict = database.get_daily_prices_batch(db_session, stock_codes_to_scan, limit=120, table_name="STOCK_DAILY_PRICES_3Y")
            kospi_prices_df = database.get_daily_prices(db_session, "0001", limit=120, table_name="STOCK_DAILY_PRICES_3Y")
        
        # 5. 병렬 스캔
        max_workers = min(10, len(stock_codes_to_scan))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for stock_code, stock_info in watchlist.items():
                # tradable_codes에 포함된 종목만 스캔
                if stock_code in stock_codes_to_scan and stock_code in daily_prices_dict:
                    future = executor.submit(
                        self._analyze_stock,
                        stock_code,
                        stock_info,
                        daily_prices_dict[stock_code],
                        current_regime,
                        active_strategies,
                        kospi_prices_df,
                        bear_context
                    )
                    futures[future] = stock_code
            
            # 결과 수집
            for future in as_completed(futures):
                stock_code = futures[future]
                try:
                    buy_candidate = future.result()
                    if buy_candidate:
                        buy_candidates.append(buy_candidate)
                except Exception as e:
                    logger.error(f"[{stock_code}] 분석 중 오류: {e}")
        
        return buy_candidates
    
    def _analyze_stock(self, stock_code, stock_info, daily_prices_df, 
                      current_regime, active_strategies, kospi_prices_df,
                      bear_context=None) -> dict:
        """
        단일 종목 분석 (실시간 가격 반영)
        
        Returns:
            buy_candidate dict or None
        """
        try:
            # [Fast Hands] 1. 실시간 현재가 조회 (Gateway)
            # DB에 있는 과거 데이터(daily_prices_df)는 어제 종가 기준일 가능성이 높음.
            # 장중 대응을 위해 실시간 현재가를 조회하여 지표 계산에 반영해야 함.
            current_price = 0
            snapshot = self.kis.get_stock_snapshot(stock_code)
            
            if snapshot and snapshot.get('price'):
                current_price = float(snapshot['price'])
                
                # [Fast Hands] 2. DataFrame에 현재가 반영 (In-Memory Update)
                # daily_prices_df의 마지막 행이 오늘 날짜인지 확인
                if not daily_prices_df.empty:
                    last_date = daily_prices_df['PRICE_DATE'].iloc[-1]
                    today_str = datetime.now().strftime('%Y-%m-%d')
                    last_date_str = last_date.strftime('%Y-%m-%d') if hasattr(last_date, 'strftime') else str(last_date)[:10]
                    
                    if last_date_str == today_str:
                        # 이미 오늘 데이터가 있으면 업데이트 (종가 = 현재가)
                        daily_prices_df.iloc[-1, daily_prices_df.columns.get_loc('CLOSE_PRICE')] = current_price
                        if snapshot.get('high'):
                            daily_prices_df.iloc[-1, daily_prices_df.columns.get_loc('HIGH_PRICE')] = max(float(daily_prices_df['HIGH_PRICE'].iloc[-1]), float(snapshot['high']))
                        if snapshot.get('low'):
                            daily_prices_df.iloc[-1, daily_prices_df.columns.get_loc('LOW_PRICE')] = min(float(daily_prices_df['LOW_PRICE'].iloc[-1]), float(snapshot['low']))
                    else:
                        # 오늘 데이터가 없으면 행 추가
                        import pandas as pd
                        new_row = pd.DataFrame([{
                            'PRICE_DATE': datetime.now(),
                            'STOCK_CODE': stock_code,
                            'CLOSE_PRICE': current_price,
                            'HIGH_PRICE': float(snapshot.get('high', current_price)),
                            'LOW_PRICE': float(snapshot.get('low', current_price)),
                            'OPEN_PRICE': float(snapshot.get('open', current_price)) # OPEN_PRICE 컬럼이 있다면
                        }])
                        # 공통 컬럼만 선택하여 병합
                        common_cols = daily_prices_df.columns.intersection(new_row.columns)
                        daily_prices_df = pd.concat([daily_prices_df, new_row[common_cols]], ignore_index=True)
            else:
                # 실시간 조회 실패 시 DB의 마지막 종가 사용 (Fallback)
                if not daily_prices_df.empty:
                    current_price = float(daily_prices_df['CLOSE_PRICE'].iloc[-1])
                else:
                    return None # 데이터 없음
            
            # 필터링: 데이터 부족
            if daily_prices_df.empty or len(daily_prices_df) < self.config.get_int('BUY_GOLDEN_CROSS_LONG', default=20):
                return None
            
            bear_signal_payload = None
            if bear_context and stock_info.get('bear_strategy'):
                strategy_hint = stock_info['bear_strategy'].get('market_regime_strategy', {}).get('strategy_type')
                mapped_strategy = self.strategy_selector.map_llm_strategy(strategy_hint or "")
                if mapped_strategy == StrategySelector.STRATEGY_BEAR_SNIPE_DIP:
                    bear_signal_payload = bear_strategies.evaluate_snipe_dip(
                        daily_prices_df, current_price, self.config, bear_context
                    )
                elif mapped_strategy == StrategySelector.STRATEGY_BEAR_MOMENTUM_BREAKOUT:
                    bear_signal_payload = bear_strategies.evaluate_momentum_breakout(
                        daily_prices_df, current_price, kospi_prices_df, self.config, bear_context
                    )
                if bear_signal_payload is None:
                    return None

            # 공통 지표 계산 (업데이트된 daily_prices_df 기반)
            # last_close_price는 이제 실시간 현재가(current_price)와 동일
            last_close_price = current_price 
            rsi_value = strategy.calculate_rsi(daily_prices_df)
            
            # 신호 감지
            if bear_signal_payload:
                buy_signal_type = bear_signal_payload['signal']
                key_metrics_dict = bear_signal_payload['key_metrics']
                suggestion = stock_info['bear_strategy'].get('suggested_entry_focus')
                if suggestion:
                    key_metrics_dict['suggested_entry_focus'] = suggestion
                key_metrics_dict['bear_mode'] = True
                key_metrics_dict['llm_strategy_type'] = strategy_hint
            else:
                buy_signal_type, key_metrics_dict = self._detect_signals(
                    stock_code, daily_prices_df, last_close_price, rsi_value, current_regime, active_strategies, kospi_prices_df
                )
            
            if not buy_signal_type:
                return None
            
            # 팩터 점수 계산
            factor_score, factors = self._calculate_factor_score(
                stock_code, stock_info, daily_prices_df, kospi_prices_df, current_regime
            )
            
            # [New] 실시간 뉴스 감성 점수 반영
            sentiment_data = database.get_sentiment_score(stock_code)
            sentiment_score = sentiment_data.get('score', 50)
            sentiment_reason = sentiment_data.get('reason', '분석 없음')
            news_category = sentiment_data.get('category', None)
            
            # [v1.0] 역신호 카테고리 매수 금지 플래그
            # 팩터 분석 결과: 수주(43.7%), 배당(37.6%) 뉴스는 역신호!
            REVERSE_SIGNAL_CATEGORIES = {'수주', '배당', '자사주', '주주환원', '배당락'}
            
            if news_category and news_category in REVERSE_SIGNAL_CATEGORIES:
                # 역신호 카테고리 뉴스가 있으면 매수 보류
                if sentiment_score >= 70:  # 호재로 분류된 경우에만 필터링
                    logger.warning(f"⚠️ [{stock_code}] 역신호 카테고리({news_category}) 뉴스 감지 - "
                                  f"통계상 승률 50% 미만, 매수 보류 권장")
                    factors['reverse_signal_category'] = news_category
                    factors['reverse_signal_warning'] = True
                    # 점수 패널티 적용 (20% 감점)
                    penalty = factor_score * 0.2
                    factor_score -= penalty
                    logger.info(f"   📉 역신호 패널티 적용: -{penalty:.1f}점")
            
            # 가산점/필터링 로직 (기존 로직 수정)
            if sentiment_score >= 80 and news_category not in REVERSE_SIGNAL_CATEGORIES:
                # 호재 + 역신호 아닌 경우만 가산점 (기존 10% → 5%로 축소)
                boost = factor_score * 0.05
                factor_score += boost
                logger.info(f"📰 [{stock_code}] 뉴스 호재({sentiment_score}점)로 점수 상승: +{boost:.1f}점")
                factors['sentiment_bonus'] = boost
            elif sentiment_score <= 20:
                # 악재: 즉시 탈락 (점수 0점 처리)
                logger.warning(f"📰 [{stock_code}] 뉴스 악재({sentiment_score}점)로 매수 후보 제외: {sentiment_reason}")
                return None

            factors['sentiment_score'] = sentiment_score
            factors['sentiment_reason'] = sentiment_reason
            factors['news_category'] = news_category

            return {
                'code': stock_code,
                'name': stock_info.get('name', stock_code),
                'stock_info': stock_info,
                'daily_prices_df': daily_prices_df,  # Pub/Sub 메시지에는 미포함 (직렬화 불가)
                'buy_signal_type': buy_signal_type,
                'key_metrics_dict': key_metrics_dict,
                'factor_score': factor_score,
                'factors': factors,
                'current_price': float(last_close_price)
            }
            
        except Exception as e:
            logger.error(f"[{stock_code}] 분석 오류: {e}")
            return None
    
    def _detect_signals(self, stock_code, daily_prices_df, last_close_price, rsi_value, 
                       current_regime, active_strategies, kospi_prices_df) -> tuple:
        """
        매수 신호 감지
        
        Returns:
            (signal_type, key_metrics_dict) or (None, None)
        """
        for strategy_type in active_strategies:
            if strategy_type == StrategySelector.STRATEGY_MEAN_REVERSION:
                # 평균 회귀 전략
                bollinger_lower = strategy.calculate_bollinger_bands(
                    daily_prices_df, period=self.config.get_int('BUY_BOLLINGER_PERIOD', default=20)
                )
                
                if bollinger_lower:
                    bb_distance_pct = ((last_close_price - bollinger_lower) / bollinger_lower) * 100
                    logger.debug(f"[{stock_code}] BB 하단: {bollinger_lower:.2f}, 현재가: {last_close_price:.2f}, BB 거리: {bb_distance_pct:.2f}%")
                    
                    if last_close_price <= bollinger_lower:
                        logger.debug(f"[{stock_code}] BB_LOWER 신호 감지.")
                        return 'BB_LOWER', {
                            "close_price": float(last_close_price),
                            "bollinger_lower": float(bollinger_lower),
                            "strategy": "MEAN_REVERSION"
                        }
                    elif bb_distance_pct <= self.BB_DISTANCE_THRESHOLD_PCT and current_regime == MarketRegimeDetector.REGIME_BULL:
                        logger.debug(f"[{stock_code}] BB_LOWER_NEAR 신호 감지 (강세장).")
                        return 'BB_LOWER_NEAR', {
                            "close_price": float(last_close_price),
                            "bollinger_lower": float(bollinger_lower),
                            "bb_distance_pct": float(bb_distance_pct),
                            "strategy": "MEAN_REVERSION"
                        }
                
                # RSI 과매도
                if rsi_value:
                    rsi_threshold = self.config.get_int('BUY_RSI_OVERSOLD_THRESHOLD', default=30)
                    if current_regime == MarketRegimeDetector.REGIME_BULL:
                        rsi_threshold = self.RSI_OVERSOLD_BULL_THRESHOLD
                    
                    logger.debug(f"[{stock_code}] RSI: {rsi_value:.2f}, RSI 과매도 임계값: {rsi_threshold}")
                    if rsi_value <= rsi_threshold:
                        logger.debug(f"[{stock_code}] RSI_OVERSOLD 신호 감지.")
                        return 'RSI_OVERSOLD', {
                            "rsi": float(rsi_value),
                            "rsi_threshold": rsi_threshold,
                            "strategy": "MEAN_REVERSION"
                        }
            
            elif strategy_type == StrategySelector.STRATEGY_TREND_FOLLOWING:
                # 골든 크로스
                is_golden_cross = strategy.check_golden_cross(
                    daily_prices_df,
                    short_period=self.config.get_int('BUY_GOLDEN_CROSS_SHORT', default=5),
                    long_period=self.config.get_int('BUY_GOLDEN_CROSS_LONG', default=20)
                )
                logger.debug(f"[{stock_code}] 골든 크로스 확인: {is_golden_cross}")
                if is_golden_cross:
                    logger.debug(f"[{stock_code}] GOLDEN_CROSS 신호 감지.")
                    return 'GOLDEN_CROSS', {
                        "signal": "GOLDEN_CROSS_5_20",
                        "strategy": "TREND_FOLLOWING"
                    }
            
            elif strategy_type == StrategySelector.STRATEGY_MOMENTUM:
                # 모멘텀
                momentum = strategy.calculate_momentum(daily_prices_df, period=5)
                logger.debug(f"[{stock_code}] 모멘텀 (5일): {momentum:.2f}, 임계값: {self.MOMENTUM_SIGNAL_THRESHOLD}")
                if momentum and momentum >= self.MOMENTUM_SIGNAL_THRESHOLD:
                    logger.debug(f"[{stock_code}] MOMENTUM 신호 감지.")
                    return 'MOMENTUM', {
                        "momentum_pct": float(momentum),
                        "strategy": "MOMENTUM"
                    }
            
            elif strategy_type == StrategySelector.STRATEGY_RELATIVE_STRENGTH:
                # 상대 강도
                if kospi_prices_df is not None and not kospi_prices_df.empty:
                    relative_strength = strategy.calculate_relative_strength(
                        daily_prices_df, kospi_prices_df, period=5
                    )
                    if relative_strength and relative_strength >= self.RELATIVE_STRENGTH_THRESHOLD:
                        return 'RELATIVE_STRENGTH', {
                            "relative_strength_pct": float(relative_strength),
                            "strategy": "RELATIVE_STRENGTH"
                        }
        
        return None, None
    
    def _calculate_factor_score(self, stock_code, stock_info, daily_prices_df, 
                               kospi_prices_df, current_regime) -> tuple:
        """팩터 점수 계산"""
        try:
            # 재무 데이터
            roe = stock_info.get('roe')
            sales_growth = stock_info.get('sales_growth')
            eps_growth = stock_info.get('eps_growth')
            pbr = stock_info.get('pbr')
            per = stock_info.get('per')
            
            # 팩터 점수 계산
            momentum_score, _ = self.factor_scorer.calculate_momentum_score(daily_prices_df, kospi_prices_df)
            quality_score, _ = self.factor_scorer.calculate_quality_score(roe, sales_growth, eps_growth, daily_prices_df)
            value_score, _ = self.factor_scorer.calculate_value_score(pbr, per)
            technical_score, _ = self.factor_scorer.calculate_technical_score(daily_prices_df)
            
            # 최종 점수 (시장 상황별 가중치 적용)
            final_score, weight_info = self.factor_scorer.calculate_final_score(
                momentum_score, quality_score, value_score, technical_score, current_regime
            )
            
            factors_summary = {
                'momentum_score': round(momentum_score, 2),
                'quality_score': round(quality_score, 2),
                'value_score': round(value_score, 2),
                'technical_score': round(technical_score, 2),
                'final_score': round(final_score, 2),
                'market_regime': current_regime,
                'applied_weights': weight_info['applied_weights']
            }
            
            return final_score, factors_summary
            
        except Exception as e:
            logger.error(f"팩터 점수 계산 오류: {e}")
            return 500.0, {'error': str(e)}
    
    def _serialize_candidate(self, candidate: dict) -> dict:
        """
        Pub/Sub 메시지용 직렬화 (DataFrame 제거)
        """
        serialized = candidate.copy()
        
        # DataFrame은 제거 (직렬화 불가)
        if 'daily_prices_df' in serialized:
            del serialized['daily_prices_df']
        
        # stock_info에서 필요한 정보만 추출
        stock_info = serialized.get('stock_info', {})
        serialized['stock_info'] = {
            'code': stock_info.get('code', serialized['code']),
            'name': stock_info.get('name', serialized['name']),
            'roe': stock_info.get('roe'),
            'pbr': stock_info.get('pbr'),
            'per': stock_info.get('per'),
            'sales_growth': stock_info.get('sales_growth'),
            'eps_growth': stock_info.get('eps_growth'),
            'llm_score': stock_info.get('llm_score', 0),
            'llm_reason': stock_info.get('llm_reason', ''),
            'bear_strategy': stock_info.get('bear_strategy')
        }
        
        # 최상위 레벨에도 편의상 추가
        serialized['llm_score'] = stock_info.get('llm_score', 0)
        serialized['llm_reason'] = stock_info.get('llm_reason', '')
        
        return serialized
