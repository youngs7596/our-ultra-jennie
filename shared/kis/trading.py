# youngs75_jennie/kis/trading.py
# Version: v3.5
# KIS API 주문 관련 기능

import logging
import time
import json
from datetime import datetime

logger = logging.getLogger(__name__)

class Trading:
    """KIS API 주문 관련 기능을 담당하는 클래스"""

    def __init__(self, client):
        self.client = client

    def _place_order(self, order_type, stock_code, quantity, price):
        """매수/매도 주문을 실행하는 내부 함수"""
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        
        # price가 0 (또는 None)이면 시장가(01), 아니면 지정가(00)
        if not price or price == 0:
            order_division = "01" # 시장가
            order_price = "0"
        else:
            order_division = "00" # 지정가
            order_price = str(price)

        data = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "PDNO": stock_code,
            "ORD_DVSN": order_division, 
            "ORD_QTY": str(quantity),
            "ORD_UNPR": order_price,
        }
        
        if self.client.TRADING_MODE == "MOCK":
            tr_id = "VTTC0012U" if order_type == "BUY" else "VTTC0011U"
        else: # REAL
            tr_id = "TTTC0012U" if order_type == "BUY" else "TTTC0011U"
        
        res_data = self.client.request('POST', URL, data=data, tr_id=tr_id)
        if res_data and res_data.get('output'):
            order_no = res_data['output'].get('ODNO')
            logger.info(f"   (Order) ✅ {order_type} 주문 성공! (주문번호: {order_no})")
            return order_no
        logger.error(f"   (Order) ❌ {order_type} 주문 실패! (응답: {res_data})")
        return None

    def place_buy_order(self, stock_code, quantity, price):
        # 핸들러에서 0 (시장가)을 전달할 것을 대비해 price 인자 유지
        return self._place_order("BUY", stock_code, quantity, price)

    def place_sell_order(self, stock_code, quantity, price):
        # 핸들러에서 0 (시장가)을 전달할 것을 대비해 price 인자 유지
        return self._place_order("SELL", stock_code, quantity, price)

    def check_order_status(self, order_no):
        """
        주문 체결 여부를 확인합니다.
        
        '주식일별주문체결조회' API를 사용하여 체결/미체결 여부를 확인합니다.
        - 먼저 체결 조회(CCLD_DVSN: "01")로 확인
        - 체결 내역이 없으면 전체 조회(CCLD_DVSN: "00")로 확인
        - 체결 내역이 있으면 → 체결 완료
        - 미체결 내역만 있으면 → 미체결
        """
        logger.info(f"   (Order) 주문({order_no}) 체결 상태 확인 (주식일별주문체결조회)...")
        
        if self.client.TRADING_MODE == "MOCK":
            tr_id = "VTTC0081R" # 모의투자 주식일별주문체결조회 (3개월 이내)
        else: # REAL
            tr_id = "TTTC0081R" # 실전투자 주식일별주문체결조회 (3개월 이내)
        
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        
        # 1단계: 체결 조회(CCLD_DVSN: "01")로 먼저 확인
        logger.info(f"   (Order) [1단계] 체결 조회(CCLD_DVSN: 01)로 확인...")
        params_filled = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "INQR_STRT_DT": datetime.now().strftime('%Y%m%d'),
            "INQR_END_DT": datetime.now().strftime('%Y%m%d'),
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "01",       # 01: 체결만 조회
            "ORD_GNO_BRNO": "",
            "ODNO": order_no,
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        api_start_time = time.time()
        res_data_filled = self.client.request('GET', URL, params=params_filled, tr_id=tr_id)
        api_duration = time.time() - api_start_time
        
        # 체결 조회 결과 확인
        if res_data_filled and res_data_filled.get('output1') and isinstance(res_data_filled['output1'], list):
            filled_list = res_data_filled['output1']
            if len(filled_list) > 0:
                # 체결 내역이 있으면 체결 완료
                filled_order = filled_list[0]
                tot_ccld_qty = int(filled_order.get('tot_ccld_qty', 0))
                avg_price = float(filled_order.get('avg_prvs', 0))
                if tot_ccld_qty > 0:
                    logger.info(f"   (Order) ✅ 주문({order_no}) 체결 확인! (체결 조회, 체결 수량: {tot_ccld_qty}, 평균가: {avg_price:,.0f}, API 소요: {api_duration:.2f}초)")
                    return True
        
        # 2단계: 체결 내역이 없으면 전체 조회(CCLD_DVSN: "00")로 확인
        logger.info(f"   (Order) [2단계] 체결 조회 결과 없음. 전체 조회(CCLD_DVSN: 00)로 확인...")
        params_all = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "INQR_STRT_DT": datetime.now().strftime('%Y%m%d'),
            "INQR_END_DT": datetime.now().strftime('%Y%m%d'),
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",       # 00: 전체 (체결 + 미체결)
            "ORD_GNO_BRNO": "",
            "ODNO": order_no,
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        api_start_time = time.time()
        res_data = self.client.request('GET', URL, params=params_all, tr_id=tr_id)
        api_duration = time.time() - api_start_time
        
        # 응답 정보 상세 로깅
        logger.info(f"   (Order) [체결 확인 API] 응답 정보:")
        logger.info(f"     - 소요 시간: {api_duration:.2f}초")
        if res_data:
            logger.info(f"     - 응답 데이터: {json.dumps(res_data, indent=2, ensure_ascii=False)}")
        else:
            logger.warning(f"     - 응답 데이터: None (오류 발생)")
        
        if res_data and res_data.get('output1') and isinstance(res_data['output1'], list):
            output_list = res_data['output1']
            logger.info(f"     - output1 리스트 길이: {len(output_list)}")
            
            # [수정] 주문 내역이 없으면 체결된 것으로 간주하지 않음.
            # API 응답이 일시적으로 비어있을 수 있으므로, 이 경우 False를 반환하여 재시도를 유도해야 함.
            if len(output_list) == 0:
                # 주문 내역이 없음 (이미 체결되어 목록에서 사라졌을 수 있음)
                logger.warning(f"   (Order) ⚠️ 주문({order_no})에 대한 내역이 없습니다. (이미 체결되었거나 존재하지 않을 수 있음, API 소요: {api_duration:.2f}초)")
                # [수정] 체결된 것으로 간주하지 않고 False를 반환하여 재시도 유도
                return False
            
            # 주문 내역이 있으면 첫 번째 항목 확인
            order_info = output_list[0]
            logger.info(f"     - 주문 내역 상세: {json.dumps(order_info, indent=2, ensure_ascii=False)}")
            
            try:
                # 체결 여부 판단: tot_ccld_qty (체결 수량)와 rmn_qty (잔량)로 판단
                tot_ccld_qty = int(order_info.get('tot_ccld_qty', 0))  # 총 체결 수량
                rmn_qty = int(order_info.get('rmn_qty', 0))  # 잔량
                ord_qty = int(order_info.get('ord_qty', 0))  # 주문 수량
                cncl_yn = order_info.get('cncl_yn', '')  # 취소 여부
                
                logger.info(f"     - 주문 수량: {ord_qty}")
                logger.info(f"     - 체결 수량: {tot_ccld_qty}")
                logger.info(f"     - 잔량: {rmn_qty}")
                logger.info(f"     - 취소 여부: {cncl_yn}")
                
                # 체결 여부 판단 로직
                if tot_ccld_qty > 0:
                    # 체결 수량이 있으면 체결됨
                    if rmn_qty == 0:
                        # 잔량이 0이면 전량 체결
                        avg_price = float(order_info.get('avg_prvs', 0))
                        logger.info(f"   (Order) ✅ 주문({order_no}) 전량 체결 확인! (체결 수량: {tot_ccld_qty}, 평균가: {avg_price:,.0f}, API 소요: {api_duration:.2f}초)")
                        return True
                    else:
                        # [수정] 부분 체결 시에도 False를 반환하여 전량 체결될 때까지 대기하도록 명확히 함
                        logger.info(f"   (Order) ... 주문({order_no}) 부분 체결 상태입니다. (체결: {tot_ccld_qty}, 잔량: {rmn_qty}, API 소요: {api_duration:.2f}초)")
                        return False
                # [수정] 잔량이 0이고 체결 수량도 0인 경우는 '주문 내역 없음'과 동일하게 취급.
                # 이 로직은 불필요하며, 위에서 len(output_list) == 0 케이스로 처리됨.
                else:
                    # 체결 수량이 0이고 잔량이 있으면 미체결
                    logger.info(f"   (Order) ... 주문({order_no})이 아직 '미체결' 상태입니다. (API 소요: {api_duration:.2f}초)")
                    return False
                    
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"   (Order) ❌ 주문 내역 파싱 오류: {e}")
                logger.error(f"     - order_info: {order_info}")
                return False
        else:
            # 응답 구조가 예상과 다름
            logger.error(f"   (Order) ❌ 응답 구조 오류:")
            logger.error(f"     - res_data 타입: {type(res_data)}")
            logger.error(f"     - res_data 내용: {res_data}")
            if res_data and 'output1' in res_data:
                logger.error(f"     - output1 타입: {type(res_data.get('output1'))}")
                logger.error(f"     - output1 내용: {res_data.get('output1')}")
        
        # API 오류 또는 예상치 못한 응답
        logger.error(f"   (Order) ❌ 주문({order_no}) 상태 확인 실패 (API 오류). (응답: {res_data}, API 소요: {api_duration:.2f}초)")
        return False # 오류 발생 시, 체결되지 않은 것으로 간주 (안전성)
        
    def get_filled_order_details(self, order_no):
        """
        '주식일별주문체결조회' API를 사용하여 특정 주문의 실제 체결 내역(평균 체결가, 총 체결 수량)을 조회합니다.
        
        Args:
            order_no (str): 조회할 주문 번호

        Returns:
            dict: {'avg_price': float, 'filled_qty': int} 형태의 딕셔너리. 체결 내역이 없거나 오류 시 None 반환.
        
        Note:
            - 3개월 이내: TTTC0081R (실전) / VTTC0081R (모의)
            - 3개월 이전: CTSC9215R (실전) / VTSC9215R (모의)
            - 현재는 3개월 이내 데이터만 조회 (오늘 날짜 기준)
        """
        logger.info(f"   (Order) 주문({order_no})의 '실제 체결 내역' 조회 시도...")

        if self.client.TRADING_MODE == "MOCK":
            tr_id = "VTTC0081R" # 모의투자 주식일별주문체결조회 (3개월 이내)
        else: # REAL
            tr_id = "TTTC0081R" # 실전투자 주식일별주문체결조회 (3개월 이내)

        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        
        params = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "INQR_STRT_DT": datetime.now().strftime('%Y%m%d'), # 조회시작일자 (오늘)
            "INQR_END_DT": datetime.now().strftime('%Y%m%d'),  # 조회종료일자 (오늘)
            "SLL_BUY_DVSN_CD": "00", # 00: 전체
            "INQR_DVSN": "00",       # 00: 전체
            "PDNO": "",              # 종목번호 (전체 조회를 위해 비워둠)
            "CCLD_DVSN": "01",       # 01: 체결
            "ORD_GNO_BRNO": "",      # 주문지점번호 (비워둠)
            "ODNO": order_no,        # 특정 주문 번호로 조회
            "INQR_DVSN_3": "00",     # 00: 전체
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)

        if res_data and res_data.get('output1') and isinstance(res_data['output1'], list):
            # API는 주문 번호로 필터링해도 리스트를 반환하므로 첫 번째 요소를 사용
            filled_order = res_data['output1'][0]
            
            # 1차: avg_prvs 필드 사용
            avg_price = float(filled_order.get('avg_prvs', 0))
            filled_qty = int(filled_order.get('tot_ccld_qty', 0))
            
            # 2차: avg_prvs가 0이거나 없으면 총 체결금액/체결수량으로 계산
            if avg_price == 0 and filled_qty > 0:
                tot_ccld_amt = float(filled_order.get('tot_ccld_amt', 0))
                if tot_ccld_amt > 0:
                    avg_price = tot_ccld_amt / filled_qty
                    logger.info(f"   (Order) avg_prvs가 0이므로 tot_ccld_amt/tot_ccld_qty로 계산: {avg_price:,.0f}원")
                else:
                    logger.warning(f"   (Order) ⚠️ avg_prvs와 tot_ccld_amt가 모두 0입니다. 체결 내역: {json.dumps(filled_order, ensure_ascii=False)}")

            if filled_qty > 0 and avg_price > 0:
                logger.info(f"   (Order) ✅ 주문({order_no}) 체결 내역 확인! (평균가: {avg_price:,.0f}, 수량: {filled_qty})")
                return {"avg_price": avg_price, "filled_qty": filled_qty}
            else:
                logger.warning(f"   (Order) ⚠️ 체결 내역이 유효하지 않습니다. (평균가: {avg_price}, 수량: {filled_qty}, 응답: {json.dumps(filled_order, ensure_ascii=False)})")
                return None

        logger.warning(f"   (Order) ⚠️ 주문({order_no})에 대한 체결 내역을 찾을 수 없거나, 체결 수량이 0입니다. (응답: {res_data})")
        return None

    def cancel_order(self, order_no, quantity):
        """
        특정 주문 번호에 대해 '전량 취소'를 요청합니다.
        """
        logger.info(f"   (Order) 주문({order_no}) 취소 시도...")
        
        if self.client.TRADING_MODE == "MOCK":
            tr_id = "VTTC0013U"
        else: # REAL
            tr_id = "TTTC0013U"
            
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        
        # KIS '주문 취소' API 파라미터
        data = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "KRX_FWDG_ORD_ORG_NO": "",
            "ORGN_ODNO": order_no, # 취소할 원주문 번호
            "ORD_DVSN": "01", # 원주문 구분 (01: 시장가, 00: 지정가 등 - 여기선 중요치 않으나 보통 01/00)
            "RVSE_CNCL_DVSN_CD": "02", # 02: 취소
            "QTY_ALL_DVSN_CD": "1", # 1: 전량 (이 경우 ORD_QTY는 0이어도 됨)
            "ORD_QTY": "0", # 전량 취소이므로 0
            "ORD_UNPR": "0",
        }
        
        # 요청 정보 상세 로깅
        logger.info(f"   (Order) [취소 API] 요청 정보:")
        logger.info(f"     - URL: {URL}")
        logger.info(f"     - TR_ID: {tr_id}")
        logger.info(f"     - Method: POST")
        logger.info(f"     - Data: {json.dumps(data, indent=2, ensure_ascii=False)}")
        
        res_data = self.client.request('POST', URL, data=data, tr_id=tr_id)
        
        # 응답 정보 상세 로깅
        logger.info(f"   (Order) [취소 API] 응답 정보:")
        if res_data:
            logger.info(f"     - 응답 데이터: {json.dumps(res_data, indent=2, ensure_ascii=False)}")
            logger.info(f"     - rt_cd: {res_data.get('rt_cd')}")
            logger.info(f"     - msg_cd: {res_data.get('msg_cd')}")
            logger.info(f"     - msg1: {res_data.get('msg1')}")
        else:
            logger.warning(f"     - 응답 데이터: None (오류 발생)")

        # 'rt_cd'가 '0'이면 성공 (KIS API는 취소 성공 시 output이 아닌 헤더로 응답)
        if res_data and res_data.get('rt_cd') == '0':
            logger.info(f"   (Order) ✅ 주문({order_no}) 취소 요청 성공.")
            return True
        
        logger.error(f"   (Order) ❌ 주문({order_no}) 취소 요청 실패! (응답: {res_data})")
        return False

    def get_account_balance(self):
        """
        계좌 잔고(보유 종목)를 조회합니다.
        
        Returns:
            list: 보유 종목 리스트. 각 항목은 {'code': str, 'name': str, 'quantity': int, 'avg_price': float, 'current_price': float} 형태.
        """
        logger.info("   (Balance) 계좌 잔고 조회 중...")
        
        if self.client.TRADING_MODE == "MOCK":
            tr_id = "VTTC8434R" # 모의투자 주식 잔고 조회
        else: # REAL
            tr_id = "TTTC8434R" # 실전투자 주식 잔고 조회
        
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        
        params = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "AFHR_FLPR_YN": "N", # N: 전일 매매 포함
            "OFL_YN": "",        # 공백: 전체
            "INQR_DVSN": "02",   # 02: 종목별
            "UNPR_DVSN": "01",   # 01: 단가
            "FUND_STTL_ICLD_YN": "N", # N: 펀드 결제분 제외
            "FNCG_AMT_AUTO_RDPT_YN": "N", # N: 융자금 자동상환 제외
            "PRCS_DVSN": "01",   # 01: 미체결 제외
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
        
        if not res_data or res_data.get('rt_cd') != '0':
            logger.error(f"   (Balance) ❌ 계좌 잔고 조회 실패! (응답: {res_data})")
            return []
        
        holdings = []
        output1 = res_data.get('output1', [])
        
        if not isinstance(output1, list):
            logger.warning(f"   (Balance) ⚠️ output1이 리스트가 아닙니다. (타입: {type(output1)})")
            return []
        
        for item in output1:
            # 잔고 수량이 0보다 큰 종목만 포함
            quantity = int(item.get('hldg_qty', 0))
            if quantity <= 0:
                continue
            
            code = item.get('pdno', '').strip()
            name = item.get('prdt_name', '').strip()
            avg_price = float(item.get('pchs_avg_pric', 0))  # 매입 평균가
            current_price = float(item.get('prpr', 0))  # 현재가
            
            if code:
                holdings.append({
                    'code': code,
                    'name': name,
                    'quantity': quantity,
                    'avg_price': avg_price,
                    'current_price': current_price
                })
        
        logger.info(f"   (Balance) ✅ 계좌 잔고 조회 완료! (보유 종목: {len(holdings)}개)")
        return holdings
    
    def get_cash_balance(self):
        """
        계좌의 현금 잔고(주문가능금액)를 조회합니다.
        매수가능조회 API를 사용하여 실제 주문 가능한 금액을 반환합니다.
        
        Returns:
            float: 주문 가능 현금 (원)
        """
        logger.info("   (Balance) 주문가능금액 조회 중...")
        
        # 매수가능조회 API 사용 (더 정확한 주문가능금액)
        if self.client.TRADING_MODE == "MOCK":
            tr_id = "VTTC8908R"  # 모의투자 매수가능조회
        else:
            tr_id = "TTTC8908R"  # 실전투자 매수가능조회
        
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        
        params = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "PDNO": "005930",  # 임의의 종목코드 (삼성전자)
            "ORD_UNPR": "0",   # 시장가
            "ORD_DVSN": "01",  # 시장가
            "CMA_EVLU_AMT_ICLD_YN": "Y",  # CMA 평가금액 포함
            "OVRS_ICLD_YN": "N"  # 해외포함여부
        }
        
        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
        
        if not res_data or res_data.get('rt_cd') != '0':
            logger.warning(f"   (Balance) ⚠️ 매수가능조회 실패, 잔고조회로 폴백 (응답: {res_data})")
            return self._get_cash_balance_fallback()
        
        output = res_data.get('output', {})
        
        # ord_psbl_cash: 주문가능현금 (실제 주문에 사용 가능한 금액)
        # nrcvb_buy_amt: 미수없는매수금액 (미수 없이 매수 가능한 금액)
        ord_psbl_cash = output.get('ord_psbl_cash', '')
        nrcvb_buy_amt = output.get('nrcvb_buy_amt', '')
        
        # nrcvb_buy_amt 우선 사용 (미수 없는 순수 매수가능금액)
        if nrcvb_buy_amt and nrcvb_buy_amt.strip():
            cash_balance = float(nrcvb_buy_amt)
            logger.info(f"   (Balance) ✅ 주문가능금액: {cash_balance:,.0f}원 (미수없는매수금액)")
        elif ord_psbl_cash and ord_psbl_cash.strip():
            cash_balance = float(ord_psbl_cash)
            logger.info(f"   (Balance) ✅ 주문가능현금: {cash_balance:,.0f}원")
        else:
            logger.warning(f"   (Balance) ⚠️ 주문가능금액 없음, 폴백")
            return self._get_cash_balance_fallback()
        
        return cash_balance
    
    def _get_cash_balance_fallback(self):
        """
        폴백: 기존 잔고조회 API로 D+2 예수금 조회
        """
        logger.info("   (Balance) [폴백] D+2 예수금 조회 중...")
        
        if self.client.TRADING_MODE == "MOCK":
            tr_id = "VTTC8434R"
        else:
            tr_id = "TTTC8434R"
        
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        
        params = {
            "CANO": self.client.ACCOUNT_PREFIX,
            "ACNT_PRDT_CD": self.client.ACCOUNT_SUFFIX,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
        
        if not res_data or res_data.get('rt_cd') != '0':
            logger.error(f"   (Balance) ❌ 폴백 조회도 실패!")
            return 0.0
        
        output2 = res_data.get('output2', [])
        if not output2 or len(output2) == 0:
            return 0.0
        
        dnca_tot_amt = output2[0].get('dnca_tot_amt', '0')
        cash_balance = float(dnca_tot_amt) if dnca_tot_amt else 0.0
        logger.info(f"   (Balance) ✅ 예수금(D+2): {cash_balance:,.0f}원")
        return cash_balance