# youngs75_jennie/kis/websocket.py
# Version: v3.5
# [모듈] KIS API 실시간 웹소켓

import logging
import websocket
import threading
import json
import time
import os

logger = logging.getLogger(__name__)

class WebsocketManager:
    """KIS API 실시간 웹소켓 통신을 담당하는 클래스"""

    def __init__(self, client):
        self.client = client
        self.ws = None
        self.ws_thread = None
        self.is_ws_connected = False
        self.connection_event = threading.Event() # 연결 신호용 Event
        self.connection_lock = threading.Lock() # Race condition 방지용 Lock
        self.message_count = 0  # 수신 메시지 카운터
        self.last_message_time = None  # 마지막 메시지 수신 시간
        self.subscribe_start_time = None  # 구독 시작 시간
        self.connection_start_time = None  # 연결 시작 시간
        self.ping_thread = None  # Keepalive ping 스레드
        self.ping_interval = 30  # 30초마다 ping 전송
        
        # Mock 모드 관련
        self.mock_mode = os.getenv('MOCK_SKIP_TIME_CHECK', 'false').lower() == 'true'
        self.mock_sio_client = None  # python-socketio 클라이언트 (Mock 모드용)

    def _subscribe_to_items(self, ws, approval_key, portfolio_codes):
        """
        구독 로직을 별도 스레드에서 실행 (on_open 블로킹 방지)
        """
        self.subscribe_start_time = time.time()
        try:
            logger.info(f"   (WS) {len(portfolio_codes)}개 종목 구독 시작...")
            if portfolio_codes:
                header_tr_type = "1"  # 1: 등록
                body_tr_id = "H0STCNT0" # H0STCNT0: 주식 현재가(체결)
                
                subscribed_count = 0
                for code in portfolio_codes:
                    if not self.is_ws_connected:
                        logger.warning(f"   (WS) 구독 중 웹소켓 연결이 끊어져 중단합니다. (구독 완료: {subscribed_count}/{len(portfolio_codes)})")
                        break
                        
                    send_data = json.dumps({
                        "header": {
                            "approval_key": approval_key, 
                            "custtype": "P", 
                            "tr_type": header_tr_type,
                            "content-type": "utf-8"
                        }, 
                        "body": {
                            "input": {
                                "tr_id": body_tr_id, 
                                "tr_key": code
                            }
                        }
                    })
                    
                    try:
                        ws.send(send_data)
                        subscribed_count += 1
                    except websocket.WebSocketConnectionClosedException:
                        logger.error(f"   (WS) 구독 요청 전송 실패: 연결이 이미 끊어짐")
                        break
                    except Exception as e:
                        logger.error(f"   (WS) 구독 요청 전송 중 오류: {e}")
                    
                    time.sleep(0.05)  # API 호출 간격
                
                elapsed_time = time.time() - self.subscribe_start_time
                logger.info(f"   (WS) 구독 완료: {subscribed_count}/{len(portfolio_codes)}개 종목 (소요 시간: {elapsed_time:.2f}초)")

        except websocket.WebSocketConnectionClosedException:
             logger.warning("   (WS) 구독 스레드: ws.send() 중 연결이 이미 끊겼습니다.")
        except Exception as e:
            logger.error(f"❌ (WS) 구독 스레드 실행 중 오류: {e}", exc_info=True)
            self.is_ws_connected = False
            self.connection_event.clear()

    def start_realtime_monitoring(self, portfolio_codes, on_price_func):
        """실시간 데이터 수신을 위한 웹소켓 스레드를 시작합니다."""
        
        # [Mock 모드] Flask-SocketIO 웹소켓 연결
        if self.mock_mode:
            logger.info("   (WS) [MOCK 모드] Mock KIS API 웹소켓 연결 시도...")
            return self._start_mock_websocket(portfolio_codes, on_price_func)
        
        # [실제 모드] KIS API 웹소켓 연결
        # Race condition 방지: Lock을 사용하여 동시에 하나의 연결만 생성
        with self.connection_lock:
            # 기존 연결이 있으면 먼저 종료
            if self.ws is not None:
                try:
                    self.is_ws_connected = False
                    self.connection_event.clear()
                    if self.ws_thread and self.ws_thread.is_alive():
                        if self.ws:
                            self.ws.close()
                        self.ws_thread.join(timeout=2.0)
                except Exception as e:
                    logger.warning(f"   (WS) 기존 연결 종료 중 오류 (무시): {e}")
                finally:
                    self._stop_ping_thread()
                    self.ws = None
                    self.ws_thread = None
            
            approval_key = self.client.auth.get_ws_approval_key()
            if not approval_key:
                logger.error("❌ (WS) 웹소켓 승인 키 발급 실패. 실시간 모니터링을 시작할 수 없습니다.")
                return

            ws_url = "ws://ops.koreainvestment.com:21000" if self.client.TRADING_MODE == "REAL" else "ws://ops.koreainvestment.com:31000"
            
            self.connection_event.clear() # 새 연결 시작 전, 신호 초기화

            def on_open(ws):
                self.connection_start_time = time.time()
                logger.info(f"--- (WS) 웹소켓 연결 성공 ---")
                self.is_ws_connected = True
                self.message_count = 0
                self.last_message_time = None
                self.connection_event.set()

                # 구독 로직을 별도 스레드에서 실행
                subscribe_thread = threading.Thread(
                    target=self._subscribe_to_items, 
                    args=(ws, approval_key, portfolio_codes), 
                    daemon=True
                )
                subscribe_thread.start()

                # ⭐ Keepalive ping 스레드 시작
                self._start_ping_thread(ws)

            def on_message(ws, message):
                try:
                    self.message_count += 1
                    self.last_message_time = time.time()
                    
                    if message[0] in ['0', '1']:
                        parts = message.split('|')
                        if len(parts) >= 4:
                            data_part = parts[3]
                            fields = data_part.split('^')
                            if len(fields) >= 6:
                                stock_code = fields[0]
                                current_price = float(fields[2])
                                current_high = float(fields[5])
                                
                                if on_price_func:
                                    on_price_func(stock_code, current_price, current_high)
                            else:
                                logger.warning(f"   (WS) 메시지 필드 수 부족: {len(fields)}개")
                except Exception as e:
                    logger.error(f"❌ (WS) 메시지 처리 중 오류: {e}", exc_info=True)

            def on_error(ws, error):
                logger.error(f"❌ (WS) 웹소켓 오류 발생: {error}")
                self.is_ws_connected = False
                self.connection_event.clear()

            def on_close(ws, close_status_code, close_msg):
                logger.warning(f"--- (WS) 웹소켓 연결 종료 (코드: {close_status_code}) ---")
                self.is_ws_connected = False
                self.connection_event.clear()
                self._stop_ping_thread()

        self.ws = websocket.WebSocketApp(ws_url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
        self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.ws_thread.start()
        logger.info("   (WS) 웹소켓 연결 스레드 시작됨")

    def _start_ping_thread(self, ws):
        """⭐ Keepalive ping 스레드를 시작합니다."""
        def ping_loop():
            ping_count = 0
            while self.is_ws_connected:
                try:
                    time.sleep(self.ping_interval)
                    
                    if not self.is_ws_connected:
                        break
                    
                    # ⭐ {"say": "hello"} 메시지로 연결 유지
                    ping_message = json.dumps({"say": "hello"})
                    try:
                        ws.send(ping_message)
                        ping_count += 1
                        if ping_count % 10 == 0:  # 10번마다 로깅 (약 5분마다)
                            logger.info(f"   (WS) [Keepalive] Ping 전송 #{ping_count} (연결 유지 중)")
                    except websocket.WebSocketConnectionClosedException:
                        logger.warning(f"   (WS) [Keepalive] Ping 전송 실패: 연결이 이미 끊어짐")
                        break
                    except Exception as e:
                        logger.error(f"   (WS) [Keepalive] Ping 전송 중 오류: {e}")
                        break
                except Exception as e:
                    logger.error(f"   (WS) [Keepalive] Ping 루프 오류: {e}")
                    break
        
        self.ping_thread = threading.Thread(target=ping_loop, daemon=True)
        self.ping_thread.start()
        logger.info("   (WS) [Keepalive] Ping 스레드 시작됨 (30초 간격)")

    def _stop_ping_thread(self):
        """Keepalive ping 스레드를 종료합니다."""
        if self.ping_thread and self.ping_thread.is_alive():
            self.ping_thread = None

    def stop(self):
        """WebSocket 연결 종료"""
        logger.info("   (WS) WebSocket 연결 종료 요청")
        self.is_ws_connected = False
        self.connection_event.clear()
        self._stop_ping_thread()
        
        if self.ws:
            try:
                self.ws.close()
            except Exception as e:
                logger.warning(f"   (WS) 연결 종료 중 오류 (무시): {e}")
        
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=2.0)

    def is_connected(self):
        return self.is_ws_connected
    
    def _start_mock_websocket(self, portfolio_codes, on_price_func):
        """
        [Mock 모드] Flask-SocketIO 기반 Mock KIS API 웹소켓 연결
        """
        try:
            import socketio
        except ImportError:
            logger.error("❌ (WS) [MOCK] python-socketio 라이브러리가 설치되지 않았습니다.")
            logger.error("   pip install python-socketio 를 실행하세요.")
            return
        
        # Mock KIS API 서버 URL
        mock_ws_url = os.getenv('KIS_BASE_URL_MOCK', 'http://localhost:9443')
        
        logger.info(f"   (WS) [MOCK] Mock 웹소켓 서버 연결 시도: {mock_ws_url}")
        
        # SocketIO 클라이언트 생성
        self.mock_sio_client = socketio.Client(logger=False, engineio_logger=False)
        
        # 이벤트 핸들러 등록
        @self.mock_sio_client.on('connect')
        def on_connect():
            logger.info(f"   (WS) [MOCK] ✅ 웹소켓 연결 성공")
            self.is_ws_connected = True
            self.connection_event.set()
            
            # 종목 구독 요청
            if portfolio_codes:
                logger.info(f"   (WS) [MOCK] {len(portfolio_codes)}개 종목 구독 요청...")
                self.mock_sio_client.emit('subscribe', {'codes': portfolio_codes})
        
        @self.mock_sio_client.on('connected')
        def on_connected(data):
            logger.info(f"   (WS) [MOCK] 서버 환영 메시지: {data.get('message', '')}")
        
        @self.mock_sio_client.on('subscribed')
        def on_subscribed(data):
            logger.info(f"   (WS) [MOCK] ✅ 구독 완료: {data.get('total', 0)}개 종목")
        
        @self.mock_sio_client.on('price_update')
        def on_price_update(data):
            """가격 업데이트 수신"""
            try:
                self.message_count += 1
                self.last_message_time = time.time()
                
                stock_code = data.get('stock_code')
                current_price = float(data.get('current_price', 0))
                current_high = float(data.get('high', current_price))
                
                # 매도 핸들러 콜백 호출
                if on_price_func and stock_code and current_price > 0:
                    on_price_func(stock_code, current_price, current_high)
                    
                    # 가격 업데이트 로그 (처음 5회는 매번, 이후 1분마다)
                    if self.message_count <= 5 or self.message_count % 12 == 0:  # 처음 5회 또는 1분마다
                        logger.info(f"   (WS) [MOCK] {stock_code} 가격 업데이트: {current_price:,.0f}원 (메시지 #{self.message_count})")
            except Exception as e:
                logger.error(f"   (WS) [MOCK] 가격 업데이트 처리 중 오류: {e}")
        
        @self.mock_sio_client.on('disconnect')
        def on_disconnect():
            logger.warning("   (WS) [MOCK] ⚠️ 웹소켓 연결 해제됨")
            self.is_ws_connected = False
            self.connection_event.clear()
        
        @self.mock_sio_client.on('error')
        def on_error(data):
            logger.error(f"   (WS) [MOCK] ❌ 웹소켓 오류: {data.get('message', '')}")
        
        # 웹소켓 연결 (별도 스레드에서 실행)
        def connect_mock_ws():
            try:
                self.mock_sio_client.connect(mock_ws_url, wait_timeout=10)
                logger.info("   (WS) [MOCK] 웹소켓 연결 대기 중...")
                self.mock_sio_client.wait()  # 연결 유지
            except Exception as e:
                logger.error(f"   (WS) [MOCK] ❌ 웹소켓 연결 실패: {e}")
                self.is_ws_connected = False
                self.connection_event.clear()
        
        self.ws_thread = threading.Thread(target=connect_mock_ws, daemon=True)
        self.ws_thread.start()
        
        # 연결 완료 대기 (최대 10초)
        if self.connection_event.wait(timeout=10):
            logger.info("   (WS) [MOCK] ✅ 웹소켓 초기화 완료")
        else:
            logger.error("   (WS) [MOCK] ❌ 웹소켓 연결 타임아웃 (10초)")
            self.is_ws_connected = False
