# services/buy-executor/executor.py
# Version: v3.5
# Buy Executor - 매수 결재 및 주문 실행 로직

import logging
import sys
import os
from datetime import datetime, timezone

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.database as database
import shared.auth as auth
from shared.position_sizing import PositionSizer
from shared.portfolio_diversification import DiversificationChecker
from shared.sector_classifier import SectorClassifier
from shared.market_regime import MarketRegimeDetector
from shared.strategy_presets import (
    apply_preset_to_config,
    resolve_preset_for_regime,
)

logger = logging.getLogger(__name__)


class BuyExecutor:
    """매수 결재 및 주문 실행 클래스"""
    
    def __init__(self, kis, config, gemini_api_key, telegram_bot=None):
        """
        Args:
            kis: KIS API 클라이언트
            config: ConfigManager 인스턴스
            gemini_api_key: Gemini API 키
            telegram_bot: TelegramBot 인스턴스 (optional)
        """
        self.kis = kis
        self.config = config
        self.gemini_api_key = gemini_api_key
        self.telegram_bot = telegram_bot
        
        self.position_sizer = PositionSizer(config)
        self.sector_classifier = SectorClassifier(kis, db_pool_initialized=True)
        self.diversification_checker = DiversificationChecker(config, self.sector_classifier)
        self.market_regime_detector = MarketRegimeDetector()
    
    def _get_db_connection(self):
        """DB 연결 생성 (SQLAlchemy 사용)"""
        return database.get_db_connection()

    def process_buy_signal(self, scan_result: dict, dry_run: bool = True) -> dict:
        """
        매수 신호 처리
        
        Cloud Run은 Stateless이므로 매 요청마다 DB 연결을 직접 생성/종료합니다.
        
        Args:
            scan_result: Buy Scanner로부터 받은 데이터
            dry_run: True면 로그만 기록, False면 실제 주문
        
        Returns:
            {
                "status": "success" | "skipped" | "error",
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "order_no": "12345",
                "quantity": 10,
                "price": 72000,
                "reason": "..."
            }
        """
        logger.info("=== 매수 신호 처리 시작 ===")
        
        # DB 연결
        db_conn = self._get_db_connection()
        if not db_conn:
            logger.error("❌ DB 연결 실패")
            return {"status": "error", "reason": "Database connection failed"}

        try:
            # 1. 후보 확인
            candidates = scan_result.get('candidates', [])
            if not candidates:
                logger.info("매수 후보가 없습니다.")
                return {"status": "skipped", "reason": "No candidates"}
            
            market_regime = scan_result.get('market_regime', 'UNKNOWN')
            shared_regime_cache = None
            if (market_regime in (None, 'UNKNOWN') or
                    not scan_result.get('strategy_preset') or
                    not scan_result.get('risk_setting')):
                shared_regime_cache = database.get_market_regime_cache()
                if shared_regime_cache:
                    market_regime = shared_regime_cache.get('regime', market_regime)

            logger.info(f"시장 상황: {market_regime}, 후보 수: {len(candidates)}개")
            
            preset_info = scan_result.get('strategy_preset', {}) or {}
            preset_name = preset_info.get('name')
            preset_params = preset_info.get('params', {})
            if not preset_params and shared_regime_cache:
                preset_info = shared_regime_cache.get('strategy_preset', {}) or {}
                preset_name = preset_info.get('name')
                preset_params = preset_info.get('params', {})

            if not preset_params:
                preset_name, preset_params = resolve_preset_for_regime(market_regime)
            apply_preset_to_config(self.config, preset_params)
            self.position_sizer.refresh_from_config()
            logger.info("전략 프리셋 적용: %s", preset_name)
            
            # 2. 안전장치 체크
            safety_check = self._check_safety_constraints(db_conn)
            if not safety_check['allowed']:
                logger.warning(f"⚠️ 안전장치 발동: {safety_check['reason']}")
                return {"status": "skipped", "reason": safety_check['reason']}
            
            # 2.5 중복 주문 및 보유 여부 체크 (Idempotency)
            # 이미 보유 중인지 확인
            current_portfolio = database.get_active_portfolio(db_conn)
            holding_codes = [p['code'] for p in current_portfolio]
            
            # LLM 랭킹 전, 후보 중 이미 보유한 종목 제외
            # 키 호환성 처리 (code 또는 stock_code)
            candidates = [c for c in candidates if c.get('stock_code', c.get('code')) not in holding_codes]
            if not candidates:
                logger.info("모든 후보 종목을 이미 보유 중입니다.")
                return {"status": "skipped", "reason": "All candidates already held"}
                
            # 최근 매수 주문 확인 (중복 실행 방지)
            # 후보 중 하나라도 최근에 매수 시도했으면 건너뛰기 (보수적 접근)
            for candidate in candidates:
                c_code = candidate.get('stock_code', candidate.get('code'))
                c_name = candidate.get('stock_name', candidate.get('name'))
                if database.check_duplicate_order(db_conn, c_code, 'BUY', time_window_minutes=10):
                    logger.warning(f"⚠️ 최근 매수 주문 이력 존재: {c_name}({c_code}) - 중복 실행 방지")
                    return {"status": "skipped", "reason": f"Duplicate order detected for {c_code}"}
            
            # 3. [Fast Hands] LLM 점수 기반 즉시 선정 (동기 호출 제거)
            # candidates는 이미 buy-scanner에서 필터링되어 넘어옴 (is_tradable=True인 경우만)
            # 하지만 안전을 위해 점수 역순 정렬 후 최고점자 선정
            candidates.sort(key=lambda x: x.get('llm_score', 0), reverse=True)
            selected_candidate = candidates[0]
            
            current_score = selected_candidate.get('llm_score', 0)
            
            # 점수 확인 (환경변수로 설정 가능, 기본값 70점 - B등급 이상만 매수)
            min_llm_score = int(os.getenv('MIN_LLM_SCORE', '70'))
            if current_score < min_llm_score: 
                 c_name = selected_candidate.get('stock_name', selected_candidate.get('name'))
                 logger.warning(f"⚠️ 최고점 후보({c_name})의 점수({current_score})가 기준({min_llm_score}점) 미달입니다. 매수 건너뜀.")
                 return {"status": "skipped", "reason": f"Low LLM Score: {current_score} < {min_llm_score}"}

            stock_code = selected_candidate.get('stock_code', selected_candidate.get('code'))
            stock_name = selected_candidate.get('stock_name', selected_candidate.get('name'))
            logger.info(f"✅ [Fast Hands] 최고점 후보 선정: {stock_name}({stock_code}) - {current_score}점")
            logger.info(f"   이유: {selected_candidate.get('llm_reason', '')[:100]}...")
            
            # 4. 계좌 잔고 조회 (순서 변경: 분산 검증에 필요)
            # KIS Gateway의 get_cash_balance 사용
            available_cash = self.kis.get_cash_balance()
            logger.info(f"가용 현금: {available_cash:,}원")

            # 5. 동적 포지션 사이징 (먼저 수행해야 수량 기반 분산 체크 가능)
            current_price = selected_candidate.get('current_price', 0)
            if not current_price:
                # 실시간 가격 조회
                snapshot = self.kis.get_stock_snapshot(stock_code)
                if not snapshot:
                    logger.error("실시간 가격 조회 실패")
                    return {"status": "error", "reason": "Failed to get current price"}
                current_price = float(snapshot['price'])
            
            # [수정] PositionSizer.calculate_quantity 메서드 사용
            # 기존: calculate_position_size (존재하지 않는 메서드)
            # 변경: calculate_quantity (ATR 등 추가 인자 필요)
            
            # ATR 계산 또는 기본값 사용 (2%)
            # 여기서는 간단히 가격의 2%를 ATR로 가정 (Fast Hands에서는 복잡한 계산 지양)
            atr = current_price * 0.02 
            
            # 현재 포트폴리오 가치 계산
            portfolio_value = sum([p.get('quantity', 0) * p.get('current_price', p.get('avg_price', 0)) for p in current_portfolio])
            total_assets = available_cash + portfolio_value
            
            sizing_result = self.position_sizer.calculate_quantity(
                stock_code=stock_code,
                stock_price=current_price,
                atr=atr,
                account_balance=available_cash,
                portfolio_value=portfolio_value
            )
            
            base_quantity = sizing_result.get('quantity', 0)
            
            # [v3.5] 동적 리스크 설정 적용 (비중 조절)
            risk_setting = (
                selected_candidate.get('risk_setting')
                or scan_result.get('risk_setting')
            )
            if (not risk_setting) and shared_regime_cache:
                risk_setting = shared_regime_cache.get('risk_setting')
            risk_setting = risk_setting or {}
            position_size_ratio = risk_setting.get('position_size_ratio', 1.0)
            
            position_size = int(base_quantity * position_size_ratio)
            
            if position_size < 1 and base_quantity >= 1:
                 logger.warning(f"⚠️ 리스크 비율({position_size_ratio}) 적용 후 수량이 0이 되어 최소 1주로 보정")
                 position_size = 1
            
            logger.info(f"📏 포지션 사이징: 기본 {base_quantity}주 x 비율 {position_size_ratio} = 최종 {position_size}주")
            
            if position_size <= 0:
                logger.warning(f"포지션 사이즈 계산 결과 0 이하: {position_size} (이유: {sizing_result.get('reason', 'Unknown')})")
                return {"status": "skipped", "reason": "Position size too small"}

            logger.info(f"포지션 사이즈: {position_size}주, 예상 금액: {position_size * current_price:,}원")

            # 6. 분산 검증 (위에서 구한 수량 사용)
            # [v3.5] Dynamic Limits 적용
            max_sector_pct = self.config.get_float('MAX_SECTOR_PCT', 30.0)
            max_stock_pct = self.config.get_float('MAX_POSITION_VALUE_PCT', 10.0)
            
            if market_regime == MarketRegimeDetector.REGIME_STRONG_BULL:
                max_sector_pct = 50.0
                max_stock_pct = 20.0
                logger.info(f"🚀 [Dynamic Limits] Strong Bull Market: Sector Limit -> 50%, Stock Limit -> 20%")

            is_approved, div_result = self._check_diversification(
                selected_candidate, current_portfolio, available_cash, position_size, current_price, db_conn,
                override_max_sector_pct=max_sector_pct, override_max_stock_pct=max_stock_pct
            )
            
            original_qty = position_size

            if not is_approved:
                # [Optimization] Smart Skip & Dynamic Resizing
                # 섹터 비중 초과로 인한 거절인 경우, 남은 룸만큼만 매수 시도
                if "섹터" in div_result.get('reason', '') and "비중 초과" in div_result.get('reason', ''):
                    current_sector_exposure = div_result.get('current_sector_exposure', 0.0)
                    remaining_room_pct = max_sector_pct - current_sector_exposure
                    
                    # 최소한의 룸(예: 0.5%)은 있어야 매수 진행
                    if remaining_room_pct > 0.5:
                        # [개선] 안전 마진 0.1% 적용 (부동소수점 오차 방지)
                        safe_room_pct = max(0, remaining_room_pct - 0.1)
                        max_allowed_amount = total_assets * (safe_room_pct / 100.0)
                        new_qty = int(max_allowed_amount / current_price)
                        
                        # [Smart Skip] 쪼그라든 수량이 원래 목표의 50% 미만이면 과감히 패스
                        if new_qty > 0:
                            resize_ratio = new_qty / original_qty
                            if resize_ratio < 0.5:
                                logger.info(f"⏭️ Smart Skip: 수량이 너무 적어 패스 ({position_size} -> {new_qty}, {resize_ratio*100:.1f}%)")
                                return {"status": "skipped", "reason": "Smart Skip (Sector Limit)"}
                            
                            logger.info(f"⚠️ 분산 투자 제한으로 수량 조정: {position_size} -> {new_qty} (섹터 여유: {remaining_room_pct:.2f}%, 안전 마진 적용)")
                            position_size = new_qty
                            
                            # 재검증 (혹시 모를 다른 규칙 위반 확인)
                            is_approved_retry, _ = self._check_diversification(
                                selected_candidate, current_portfolio, available_cash, position_size, current_price, db_conn,
                                override_max_sector_pct=max_sector_pct, override_max_stock_pct=max_stock_pct
                            )
                            if not is_approved_retry:
                                return {"status": "skipped", "reason": "Diversification check failed after resize"}
                        else:
                            return {"status": "skipped", "reason": "Resized quantity is 0"}
                    else:
                        logger.warning(f"포트폴리오 분산 기준 위반: {div_result['reason']}")
                        return {"status": "skipped", "reason": "Diversification check failed"}
                
                # 단일 종목 비중 초과로 인한 거절인 경우, 최대 허용 비중만큼만 매수 시도
                elif "단일 종목" in div_result.get('reason', '') and "비중 초과" in div_result.get('reason', ''):
                    # 현재 자산 대비 최대 허용 금액 계산
                    # [개선] 안전 마진 0.1% 적용
                    safe_stock_pct = max(0, max_stock_pct - 0.1)
                    max_allowed_amount = total_assets * (safe_stock_pct / 100.0)
                    new_qty = int(max_allowed_amount / current_price)
                    
                    if new_qty > 0 and new_qty < position_size:
                        # [Smart Skip]
                        resize_ratio = new_qty / original_qty
                        if resize_ratio < 0.5:
                            logger.info(f"⏭️ Smart Skip: 수량이 너무 적어 패스 ({position_size} -> {new_qty}, {resize_ratio*100:.1f}%)")
                            return {"status": "skipped", "reason": "Smart Skip (Stock Limit)"}

                        logger.info(f"⚠️ 단일 종목 제한으로 수량 조정: {position_size} -> {new_qty} (제한: {max_stock_pct}%, 안전 마진 적용)")
                        position_size = new_qty
                        
                        # 재검증
                        is_approved_retry, _ = self._check_diversification(
                            selected_candidate, current_portfolio, available_cash, position_size, current_price, db_conn,
                            override_max_sector_pct=max_sector_pct, override_max_stock_pct=max_stock_pct
                        )
                        if not is_approved_retry:
                            return {"status": "skipped", "reason": "Diversification check failed after resize"}
                    else:
                        return {"status": "skipped", "reason": "Resized quantity is 0 or invalid"}
                else:
                    logger.warning(f"포트폴리오 분산 기준 위반: {div_result['reason']}")
                    return {"status": "skipped", "reason": "Diversification check failed"}
            
            # 7. 매수 주문 실행
            if dry_run:
                logger.info(f"🔧 [DRY_RUN] 매수 주문: {stock_name}({stock_code}) {position_size}주 @ {current_price:,}원")
                order_no = f"DRY_RUN_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            else:
                order_no = self.kis.place_buy_order(
                    stock_code=stock_code,
                    quantity=position_size,
                    price=0  # 시장가
                )
                
                if not order_no:
                    logger.error("매수 주문 실패")
                    return {"status": "error", "reason": "Order failed"}
                
                logger.info(f"✅ 매수 주문 체결: 주문번호 {order_no}")
            
            # 8. DB 기록
            self._record_trade(
                db_conn=db_conn,
                stock_code=stock_code,
                stock_name=stock_name,
                order_no=order_no,
                quantity=position_size,
                price=current_price,
                buy_signal_type=selected_candidate.get('buy_signal_type', 'UNKNOWN'),
                factor_score=selected_candidate.get('factor_score', 0),
                llm_reason=selected_candidate.get('llm_reason', ''),
                dry_run=dry_run,
                risk_setting=risk_setting
            )
            
            # 9. 텔레그램 알림 발송
            if self.telegram_bot:
                try:
                    total_amount = position_size * current_price
                    
                    # Mock/Real 모드 및 DRY_RUN 표시
                    trading_mode = os.getenv('TRADING_MODE', 'REAL')
                    mode_indicator = ""
                    if trading_mode == "MOCK":
                        mode_indicator = "🧪 *[MOCK 테스트]*\n"
                    if dry_run:
                        mode_indicator += "⚠️ *[DRY RUN - 실제 주문 없음]*\n"
                    
                    message = f"""{mode_indicator}💰 *매수 체결*

📈 *종목*: {stock_name} ({stock_code})
💵 *가격*: {current_price:,}원
📊 *수량*: {position_size}주
💸 *총액*: {total_amount:,}원
📝 *신호*: {selected_candidate.get('buy_signal_type', 'UNKNOWN')}
⭐ *점수*: {selected_candidate.get('factor_score', 0):.1f}

{selected_candidate.get('llm_reason', '')[:200]}"""
                    
                    self.telegram_bot.send_message(message)
                    logger.info("✅ 텔레그램 알림 발송 완료")
                except Exception as e:
                    logger.warning(f"⚠️ 텔레그램 알림 발송 실패: {e}")
            
            logger.info("=== 매수 처리 완료 ===")
            return {
                "status": "success",
                "stock_code": stock_code,
                "stock_name": stock_name,
                "order_no": order_no,
                "quantity": position_size,
                "price": current_price,
                "total_amount": position_size * current_price,
                "dry_run": dry_run
            }
            
        except Exception as e:
            logger.error(f"❌ 매수 처리 중 오류: {e}", exc_info=True)
            return {"status": "error", "reason": str(e)}
        finally:
            # DB 연결 종료
            if db_conn and hasattr(db_conn, 'close'):
                try:
                    db_conn.close()
                    logger.info("DB 연결 종료")
                except Exception as e:
                    logger.error(f"DB 연결 종료 오류: {e}")
    
    def _check_safety_constraints(self, db_conn) -> dict:
        """안전장치 체크"""
        try:
            # 1. 오늘 매수 횟수 확인
            max_buy_count = self.config.get_int('MAX_BUY_COUNT_PER_DAY', default=5)
            today_buy_count = database.get_today_buy_count(db_conn)
            
            if today_buy_count >= max_buy_count:
                return {
                    "allowed": False,
                    "reason": f"Daily buy limit reached: {today_buy_count}/{max_buy_count}"
                }
            
            # 2. 최대 보유 종목 수 확인
            max_portfolio_size = self.config.get_int('MAX_PORTFOLIO_SIZE', default=10)
            current_portfolio = database.get_active_portfolio(db_conn)
            
            if len(current_portfolio) >= max_portfolio_size:
                return {
                    "allowed": False,
                    "reason": f"Portfolio size limit reached: {len(current_portfolio)}/{max_portfolio_size}"
                }
            
            return {"allowed": True, "reason": "OK"}
            
        except Exception as e:
            logger.error(f"안전장치 체크 오류: {e}", exc_info=True)
            return {"allowed": False, "reason": f"Safety check error: {e}"}
    
    def _llm_ranking_decision(self, candidates: list, market_regime: str) -> dict:
        """LLM 랭킹 결재 (사용 안함 - Fast Hands 대체)"""
        pass

    def _check_diversification(self, candidate: dict, current_portfolio: list, available_cash: float, position_size: int, current_price: float, db_conn, override_max_sector_pct: float = None, override_max_stock_pct: float = None) -> tuple:
        """포트폴리오 분산 검증"""
        try:
            # 섹터 정보 조회 (SectorClassifier 사용)
            stock_code = candidate.get('stock_code', candidate.get('code'))
            stock_name = candidate.get('stock_name', candidate.get('name', stock_code))
            sector = self.sector_classifier.get_sector(stock_code, stock_name)
            
            # 포트폴리오 dict 변환 (diversification_checker가 기대하는 형식)
            portfolio_cache = {}
            for item in current_portfolio:
                # 포트폴리오 종목의 섹터 정보도 조회 (없으면 UNKNOWN)
                # DB 스키마에 따라 'code' 또는 'stock_code' 사용
                p_code = item.get('stock_code') or item.get('code')
                if not p_code:
                    continue
                p_name = item.get('stock_name') or item.get('name', p_code)
                item_sector = self.sector_classifier.get_sector(p_code, p_name)
                portfolio_cache[p_code] = {
                    'code': p_code,
                    'name': p_name,
                    'quantity': item.get('quantity', 0),
                    'avg_price': item.get('buy_price', item.get('avg_price')), 
                    'current_price': item.get('current_price', item.get('buy_price', item.get('avg_price'))),
                    'sector': item_sector # 섹터 정보 추가
                }
            
            # 후보 종목 정보 구성
            candidate_stock = {
                'code': stock_code,
                'name': stock_name,
                'price': current_price,
                'quantity': position_size,
                'sector': sector # 섹터 정보 추가
            }
            
            # 분산 체크 호출
            result = self.diversification_checker.check_diversification(
                candidate_stock=candidate_stock,
                portfolio_cache=portfolio_cache,
                account_balance=available_cash,
                override_max_sector_pct=override_max_sector_pct,
                override_max_stock_pct=override_max_stock_pct
            )
            
            if not result['approved']:
                logger.warning(f"분산 기준 위반: {result['reason']}")
                return False, result
            
            return True, result
            
        except Exception as e:
            logger.error(f"분산 검증 오류: {e}", exc_info=True)
            # 에러 시 보수적으로 False 반환
            return False, {'reason': str(e)}
    
    def _record_trade(self, db_conn, stock_code: str, stock_name: str, order_no: str,
                     quantity: int, price: float, buy_signal_type: str, factor_score: float,
                     llm_reason: str, dry_run: bool, risk_setting: dict = None):
        """거래 기록"""
        try:
            # 1. PORTFOLIO 테이블에 추가
            # database.add_to_portfolio 함수가 없으므로 직접 SQL 실행 필요하거나 database.py에 해당 함수가 있는지 확인
            # shared/database.py 파일에는 add_to_portfolio 함수가 없고 execute_trade_and_log 함수가 있습니다.
            # 따라서 execute_trade_and_log 함수를 사용해야 합니다.
            
            # execute_trade_and_log 함수 사용
            stock_info = {
                'code': stock_code,
                'name': stock_name
            }
            
            llm_decision = {
                'reason': llm_reason
            }
            
            database.execute_trade_and_log(
                connection=db_conn,
                trade_type='BUY',  # DRY_RUN 여부는 key_metrics_dict에 저장 (TRADE_TYPE 컬럼 길이 제한 8자 준수)
                stock_info=stock_info,
                quantity=quantity,
                price=price,
                llm_decision=llm_decision,
                strategy_signal=buy_signal_type,
                key_metrics_dict={
                    'factor_score': factor_score, 
                    'is_dry_run': dry_run,
                    'risk_setting': risk_setting or {} # [v3.5] 리스크 설정 기록
                }
            )
            
            logger.info("✅ 거래 기록 완료 (Portfolio & TradeLog)")
            
        except Exception as e:
            logger.error(f"거래 기록 오류: {e}", exc_info=True)
            raise
