# shared/rabbitmq.py
# Version: v3.8
# 작업 LLM: Claude Opus 4.5
"""
[v3.8] RabbitMQ 연결 복구 로직 강화
- StreamLostError 예외 처리 추가
- 연결 끊김 시 자동 재연결
- heartbeat 설정으로 연결 유지
"""
import json
import logging
import threading
import time
from typing import Callable, Dict, Optional

import pika
from pika.exceptions import AMQPConnectionError, StreamLostError, ChannelClosedByBroker

logger = logging.getLogger(__name__)

# RabbitMQ 연결 설정
RABBITMQ_HEARTBEAT = 60  # 60초마다 heartbeat
RABBITMQ_BLOCKED_TIMEOUT = 300  # 5분 blocked timeout
RABBITMQ_RECONNECT_DELAY = 5  # 재연결 대기 시간


class RabbitMQPublisher:
    """RabbitMQ 메시지 발행 클래스 (지연 전송 지원)"""

    def __init__(
        self,
        amqp_url: str,
        queue_name: str,
        enable_delay: bool = True,
        durable: bool = True,
    ):
        self.amqp_url = amqp_url
        self.queue_name = queue_name
        self.enable_delay = enable_delay
        self.durable = durable
        self.delay_queue_name = f"{queue_name}.delay"

    def _with_connection(self):
        params = pika.URLParameters(self.amqp_url)
        # heartbeat 및 blocked_connection_timeout 설정
        params.heartbeat = RABBITMQ_HEARTBEAT
        params.blocked_connection_timeout = RABBITMQ_BLOCKED_TIMEOUT
        return pika.BlockingConnection(params)

    def _declare_main_queue(self, channel):
        channel.queue_declare(queue=self.queue_name, durable=self.durable)

    def _declare_delay_queue(self, channel):
        if not self.enable_delay:
            return
        arguments = {
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": self.queue_name,
        }
        channel.queue_declare(
            queue=self.delay_queue_name,
            durable=self.durable,
            arguments=arguments,
        )

    def _publish(
        self,
        payload: bytes,
        delay_seconds: int = 0,
        headers: Optional[Dict] = None,
        message_ttl_ms: Optional[int] = None,
    ):
        connection = self._with_connection()
        try:
            channel = connection.channel()
            self._declare_main_queue(channel)
            if self.enable_delay:
                self._declare_delay_queue(channel)

            target_queue = self.queue_name
            props_kwargs = {
                "delivery_mode": 2 if self.durable else 1,
                "headers": headers or {},
            }

            expiration_ms = None
            if delay_seconds and self.enable_delay:
                target_queue = self.delay_queue_name
                expiration_ms = int(delay_seconds * 1000)
            elif delay_seconds and not self.enable_delay:
                logger.warning(
                    "delay_seconds=%s 요청을 받았지만 enable_delay=False 입니다. 즉시 전송합니다.",
                    delay_seconds,
                )
            if not expiration_ms and message_ttl_ms:
                expiration_ms = message_ttl_ms
            if expiration_ms:
                props_kwargs["expiration"] = str(max(1, expiration_ms))

            properties = pika.BasicProperties(**props_kwargs)
            channel.basic_publish(
                exchange="",
                routing_key=target_queue,
                body=payload,
                properties=properties,
            )
        finally:
            connection.close()

    def publish(
        self,
        payload: dict,
        delay_seconds: int = 0,
        headers: Optional[Dict] = None,
        message_ttl_ms: Optional[int] = None,
    ) -> Optional[str]:
        """메시지를 발행하고 ID를 반환합니다."""
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self._publish(
                body,
                delay_seconds=delay_seconds,
                headers=headers,
                message_ttl_ms=message_ttl_ms,
            )
            message_id = f"rabbitmq:{self.queue_name}:{int(time.time() * 1000)}"
            logger.info(
                "✅ RabbitMQ 메시지 발행 성공: %s (delay=%ss)",
                message_id,
                delay_seconds,
            )
            return message_id
        except Exception as e:
            logger.error(f"❌ RabbitMQ 메시지 발행 실패: {e}", exc_info=True)
            return None


class RabbitMQWorker:
    """RabbitMQ 큐에서 메시지를 소비하는 백그라운드 워커 (v3.8: 연결 복구 강화)"""

    def __init__(self, amqp_url: str, queue_name: str, handler: Callable[[Dict], None]):
        self.amqp_url = amqp_url
        self.queue_name = queue_name
        self.handler = handler
        self._stop_event = threading.Event()
        self._thread = None
        self._reconnect_count = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("✅ RabbitMQ 워커 스레드를 시작했습니다. queue=%s", self.queue_name)

    def stop(self):
        logger.info("🛑 RabbitMQ 워커 종료 요청...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _create_connection(self):
        """RabbitMQ 연결 생성 (heartbeat 포함)"""
        params = pika.URLParameters(self.amqp_url)
        params.heartbeat = RABBITMQ_HEARTBEAT
        params.blocked_connection_timeout = RABBITMQ_BLOCKED_TIMEOUT
        return pika.BlockingConnection(params)

    def _safe_ack(self, channel, delivery_tag):
        """안전한 ACK 전송 (연결 끊김 시 무시)"""
        try:
            if channel.is_open:
                channel.basic_ack(delivery_tag=delivery_tag)
        except (StreamLostError, AMQPConnectionError, ChannelClosedByBroker) as e:
            logger.warning(f"⚠️ ACK 전송 실패 (연결 끊김): {e}")
        except Exception as e:
            logger.warning(f"⚠️ ACK 전송 실패: {e}")

    def _safe_nack(self, channel, delivery_tag, requeue=False):
        """안전한 NACK 전송 (연결 끊김 시 무시)"""
        try:
            if channel.is_open:
                channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)
        except (StreamLostError, AMQPConnectionError, ChannelClosedByBroker) as e:
            logger.warning(f"⚠️ NACK 전송 실패 (연결 끊김): {e}")
        except Exception as e:
            logger.warning(f"⚠️ NACK 전송 실패: {e}")

    def _run(self):
        while not self._stop_event.is_set():
            connection = None
            channel = None
            try:
                connection = self._create_connection()
                channel = connection.channel()
                channel.queue_declare(queue=self.queue_name, durable=True)
                channel.basic_qos(prefetch_count=1)
                
                self._reconnect_count = 0  # 연결 성공 시 카운터 리셋
                logger.info("✅ RabbitMQ 연결 성공 (queue=%s)", self.queue_name)

                def _callback(ch, method, properties, body):
                    if self._stop_event.is_set():
                        self._safe_nack(ch, method.delivery_tag, requeue=True)
                        return

                    try:
                        payload = json.loads(body.decode("utf-8"))
                        logger.info("📥 RabbitMQ 메시지 수신 (Queue: %s)", self.queue_name)
                        
                        # [v4.1] ACK를 먼저 보내서 long-running job에서 연결 끊김으로 인한 
                        # 메시지 재처리 방지 (at-most-once semantics)
                        # Scout Job은 이미 캐싱과 last_run 체크로 중복 방지가 되어있음
                        self._safe_ack(ch, method.delivery_tag)
                        
                        # handler 실행 (오래 걸릴 수 있음)
                        self.handler(payload)
                        
                    except (StreamLostError, AMQPConnectionError) as e:
                        # ACK 후 연결이 끊어져도 이미 메시지는 처리된 것으로 간주
                        logger.warning(f"⚠️ 메시지 처리 중 연결 끊김 (이미 ACK됨): {e}")
                        raise  # 상위 루프에서 재연결 처리
                    except Exception as e:
                        # handler 실패해도 이미 ACK됨 - 로그만 남김
                        logger.exception("❌ RabbitMQ 메시지 처리 실패 (이미 ACK됨).")

                channel.basic_consume(queue=self.queue_name, on_message_callback=_callback)

                while not self._stop_event.is_set() and channel.is_open:
                    try:
                        connection.process_data_events(time_limit=1)
                    except (StreamLostError, AMQPConnectionError) as e:
                        logger.warning(f"⚠️ 연결 끊김 감지: {e}")
                        break  # 재연결 루프로 이동

            except (AMQPConnectionError, StreamLostError, ChannelClosedByBroker) as e:
                self._reconnect_count += 1
                delay = min(RABBITMQ_RECONNECT_DELAY * self._reconnect_count, 60)  # 최대 60초
                logger.warning(
                    "⚠️ RabbitMQ 연결 실패 (시도 #%d). %d초 후 재시도합니다. 오류: %s",
                    self._reconnect_count, delay, e
                )
                time.sleep(delay)
            except Exception as exc:
                self._reconnect_count += 1
                delay = min(RABBITMQ_RECONNECT_DELAY * self._reconnect_count, 60)
                logger.exception("❌ RabbitMQ 워커 오류 (시도 #%d): %s", self._reconnect_count, exc)
                time.sleep(delay)
            finally:
                # 연결 정리
                try:
                    if connection and connection.is_open:
                        connection.close()
                except Exception:
                    pass
        
        logger.info("🛑 RabbitMQ 워커 종료 완료 (queue=%s)", self.queue_name)

