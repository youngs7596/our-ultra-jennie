# youngs75_jennie/kis/request.py
# Version: v3.5
# [모듈] KIS API 요청 중앙 래퍼

import requests
import json
import logging
import time

logger = logging.getLogger(__name__)

def safe_request(client, method, url, headers=None, params=None, data=None, tr_id=""):
    """
    모든 REST API 요청을 처리하는 중앙 래퍼 함수입니다.
    - 헤더 관리, 예외 처리, 응답 코드 확인, 토큰 재발급 후 재시도를 담당합니다.
    - Rate Limiting: API 호출 후 자동으로 delay 적용
    """
    res = None
    
    try:
        for attempt in range(2): # 최대 2번 시도 (첫 시도 + 재발급 후 재시도)
            try:
                # ⭐ 헤더가 없거나 Authorization이 없으면 토큰 재발급 시도
                if client.headers is None or 'Authorization' not in client.headers:
                    logger.warning(f"   (Request) ⚠️ 헤더에 Authorization이 없습니다. 토큰 재발급 시도... (TR_ID: {tr_id})")
                    if hasattr(client, 'authenticate'):
                        if client.authenticate(force_new=True):
                            logger.info(f"   (Request) ✅ 토큰 재발급 성공!")
                        else:
                            logger.error(f"   (Request) ❌ 토큰 재발급 실패! 요청을 계속 진행합니다.")
                
                final_headers = client.headers.copy() if client.headers is not None else {}
                if headers:
                    final_headers.update(headers)

                final_headers.update({'tr_id': tr_id, 'custtype': 'P'})

                res = requests.request(method, url, headers=final_headers, params=params, data=json.dumps(data) if data else None, timeout=client.TIMEOUT)
                
                # 응답 본문이 비어있는지 확인 (404 등에서 발생 가능)
                if not res.text or res.text.strip() == '':
                    logger.warning(f"   (Request) ⚠️ API 응답이 비어있습니다. (상태 코드: {res.status_code}, TR_ID: {tr_id})")
                    # 404 응답의 경우, 주문이 이미 체결되어 미체결 목록에 없을 수 있음
                    if res.status_code == 404:
                        logger.info(f"   (Request) ℹ️ 404 응답: 주문이 이미 체결되었거나 존재하지 않을 수 있습니다.")
                        return {'rt_cd': '0', 'output1': []}  # 체결된 것으로 간주
                    return None
                
                try:
                    res_data = res.json()
                except json.JSONDecodeError as e:
                    logger.error(f"   (Request) ❌ JSON 파싱 실패: {e} (상태 코드: {res.status_code}, 응답 본문: {res.text[:200]})")
                    return None

                if res_data.get('msg_cd') in ['EGW00121', 'EGW00123'] and attempt == 0:
                    logger.warning("   (Auth) ⚠️ 유효하지 않은 토큰 감지. 새 토큰을 발급하여 재시도합니다.")
                    client.authenticate(force_new=True)
                    final_headers = client.headers.copy() if client.headers is not None else {}
                    final_headers.update({'tr_id': tr_id, 'custtype': 'P'})
                    continue

                # 500 Internal Server Error 처리
                if res.status_code == 500:
                    # 유지보수/장애로 인한 빈 응답 확인 ({"rt_cd":"1","msg_cd":"","msg1":""})
                    try:
                        error_body = res.json()
                        if error_body.get('rt_cd') == '1' and not error_body.get('msg_cd') and not error_body.get('msg1'):
                            # ⭐ 빈 500 에러는 토큰 문제일 수도 있으므로, 첫 시도에서 토큰 재발급 후 재시도
                            if attempt == 0:
                                logger.warning(f"   (Request) ⚠️ Empty 500 Error 감지. 토큰 문제일 수 있어 재발급 후 재시도합니다. (TR_ID: {tr_id})")
                                if hasattr(client, 'authenticate'):
                                    client.authenticate(force_new=True)
                                time.sleep(0.5)
                                continue
                            else:
                                # 재시도 후에도 같은 에러면 실제 유지보수/장애로 판단
                                logger.warning(f"   (Request) ⚠️ KIS API 유지보수/장애 모드 감지 (Empty 500 Error). 재시도 후에도 실패. (TR_ID: {tr_id})")
                                return None
                    except:
                        pass # JSON 파싱 실패 시 일반 500 처리로 넘어감

                    # 일반적인 500 에러는 1회 재시도
                    if attempt == 0:
                        logger.warning(f"   (Request) ⚠️ 500 Internal Server Error 감지. 0.5초 후 재시도합니다. (TR_ID: {tr_id})")
                        time.sleep(0.5)
                        continue

                res.raise_for_status()

                if tr_id == "KIS-WS-AUTH" and res_data.get('approval_key'):
                    logger.info(f"   (Auth) ✅ 웹소켓 승인 키 수신 성공. (tr_id: {tr_id})")
                    return res_data

                if res_data.get('rt_cd') != '0':                    
                    error_details = json.dumps(res_data, indent=2, ensure_ascii=False)
                    logger.error(f"API 응답 오류 ({tr_id}): {res_data.get('msg1')}\n--- 전체 응답 ---\n{error_details}")
                    return None
                return res_data
            
            except requests.exceptions.ConnectionError as e:
                # Connection refused는 Mock KIS API 서버 미실행 시 발생
                if "Connection refused" in str(e):
                    logger.warning(f"⚠️ (Request) Mock KIS API 서버에 연결할 수 없습니다 ({tr_id})")
                    logger.warning(f"⚠️ (Request) Mock KIS API 서버를 먼저 실행하세요: python utilities/mock_kis_api_server.py")
                else:
                    logger.error(f"API 연결 실패 ({tr_id}): {e}")
                return None
            except Exception as e:
                logger.error(f"API 요청 실패 ({tr_id}): {e}")
                if res is not None:
                    logger.error(f"--- 실패한 요청의 응답 상세 정보 ---")
                    logger.error(f"  - 상태 코드: {res.status_code}")
                    logger.error(f"  - 응답 헤더: {res.headers}")
                    logger.error(f"  - 응답 내용 (Text): {res.text}")
                return None
        
        return None # 재시도 후에도 실패한 경우
    
    finally:
        # ★★★ Rate Limiting: API 호출 후 항상 delay 적용 (KIS API 초당 요청 제한 준수)
        # 성공/실패 여부와 관계없이 다음 요청까지 대기
        if hasattr(client, 'API_CALL_DELAY'):
            time.sleep(client.API_CALL_DELAY)
            logger.debug(f"   (Request) Rate limiting: {client.API_CALL_DELAY}초 대기 완료")