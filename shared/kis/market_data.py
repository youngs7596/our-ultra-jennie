# youngs75_jennie/kis/market_data.py
# Version: v3.5
# [모듈] KIS API 시세 및 데이터 조회

import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

class MarketData:
    """KIS API 시세 및 데이터 조회 관련 기능을 담당하는 클래스"""

    def __init__(self, client):
        self.client = client

    def check_market_open(self):
        """오늘이 주식 시장 운영일(휴장일 X)인지 확인합니다."""
        import os
        kst = pytz.timezone('Asia/Seoul')
        now = datetime.now(kst)
        
        # [v3.5] MOCK 모드에서 시간 제약 완화 옵션
        mock_skip_time_check = os.getenv("MOCK_SKIP_TIME_CHECK", "false").lower() == "true"
        
        # [v3.6] 정규장 시간 체크: 09:00 ~ 15:30 (NXT 프리마켓 제외)
        # KRX 정규장: 09:00~15:30
        is_regular_trading_hours = (
            0 <= now.weekday() <= 4 and  # 평일
            (
                (now.hour == 9) or  # 09:00 ~ 09:59
                (10 <= now.hour <= 14) or  # 10:00 ~ 14:59
                (now.hour == 15 and now.minute <= 30)  # 15:00 ~ 15:30
            )
        )
        
        if not is_regular_trading_hours:
            # MOCK 모드이고 시간 체크 스킵 옵션이 켜져있으면 통과
            if self.client.TRADING_MODE == "MOCK" and mock_skip_time_check:
                logger.info("   (Market) ⚠️ MOCK 모드: 시간 체크 스킵 (MOCK_SKIP_TIME_CHECK=true)")
                logger.info(f"   (Market) 현재 KST: {now.strftime('%Y-%m-%d %H:%M:%S')}, 요일: {now.weekday()}, 시간: {now.hour}:{now.minute:02d}")
                logger.info("   (Market) ✅ 테스트를 위해 장이 열려있는 것으로 간주합니다.")
                return True
            
            logger.info("   (Market) KIS API: 정규장 운영 시간이 아닙니다 (09:00~15:30 외 시간).")
            logger.info(f"   (Market) 현재 KST: {now.strftime('%Y-%m-%d %H:%M:%S')}, 요일: {now.weekday()}, 시간: {now.hour}:{now.minute:02d}") 
            return False

        if self.client.TRADING_MODE == "MOCK":
            logger.info("   (Market) KIS API: MOCK 모드이며, 정규장 운영 시간(평일 09:00~15:30)입니다.")
            return True

        logger.info("   (Market) KIS API: REAL 모드, 휴장일 여부 확인 중...")
        try:
            today_str = now.strftime('%Y%m%d')
            URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/chk-holiday"
            params = {"BASS_DT": today_str, "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
            res_data = self.client.request('GET', URL, params=params, tr_id="CTCA0903R")

            if res_data and res_data.get('output'):
                if res_data['output'][0]['opnd_yn'] == 'Y':
                    logger.info(f"   (Market) KIS API: 오늘은 정상 운영일({today_str})입니다.")
                    return True
                else:
                    logger.warning(f"   (Market) KIS API: 오늘은 휴장일({today_str})입니다.")
                    return False
            else:
                logger.error("   (Market) KIS API: 휴장일 API 응답 형식이 올바르지 않습니다.")
                return False
        except Exception as e:
            logger.error(f"   (Market) KIS API: 휴장일 확인 중 오류 발생: {e}")
            return False

    def get_stock_daily_prices(self, stock_code, num_days_to_fetch=30):
        """특정 종목 또는 지수의 과거 일봉 데이터를 조회합니다."""
        # [수정] 이 API는 최대 30일치만 반환함을 명시
        logger.warning(f"   (Data) [{stock_code}] 과거 일봉 데이터 조회 (최대 30일 제한)")
        
        today = datetime.now()
        end_date = today.strftime('%Y%m%d')
        start_date = (today - timedelta(days=num_days_to_fetch)).strftime('%Y%m%d')

        # [수정] 'inquire-daily-price' API는 30일 제한이 있음
        PRICE_URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code, "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": end_date, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
        tr_id = "FHKST01010400"

        res_data = self.client.request('GET', PRICE_URL, params=params, tr_id=tr_id)

        if res_data and res_data.get('output'):
            price_data_list = []
            output_data_list = res_data.get('output')
            for day_data in output_data_list:
                price_key = 'stck_clpr'
                high_key = 'stck_hgpr'
                low_key = 'stck_lwpr'
                date_key = 'stck_bsop_date'

                price_data_list.append({
                    'date': datetime.strptime(day_data[date_key], '%Y%m%d').strftime('%Y-%m-%d'),
                    'price': float(day_data[price_key]),
                    'high': float(day_data[high_key]),
                    'low': float(day_data[low_key])
                })
            logger.info(f"   (Data) ✅ [{stock_code}] 과거 가격 데이터 {len(price_data_list)}건 수신 완료.")
            return price_data_list
        else:
            logger.warning(f"   (Data) [{stock_code}] 과거 가격 데이터 조회 실패 (API 오류).")
            return None

    def get_stock_snapshot(self, stock_code, is_index=False):
        """특정 종목 또는 지수의 현재가 스냅샷을 조회합니다."""
        if is_index:
            if self.client.TRADING_MODE == "MOCK":
                logger.warning(f"   (Data) [MOCK 모드] 지수({stock_code}) 실시간 조회를 지원하지 않습니다. (None 반환)")
                return None
            URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
            params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": stock_code}
            tr_id = "FHPUP02100000"
        else:
            URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
            tr_id = "FHKST01010100"
            
        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
        if not (res_data and res_data.get('output')):
            return None

        output = res_data['output']

        price_key = 'bstp_nmix_prpr' if is_index else 'stck_prpr'
        high_key = 'bstp_nmix_hgpr' if is_index else 'stck_hgpr'
        low_key = 'bstp_nmix_lwpr' if is_index else 'stck_lwpr'
        open_key = 'bstp_nmix_oprc' if is_index else 'stck_oprc'
        name_key = 'hts_kor_isnm'

        snapshot_data = {
            'code': stock_code,
            'name': output.get(name_key, 'N/A'),
            'price': float(output.get(price_key, 0)),
            'high': float(output.get(high_key, 0)),
            'low': float(output.get(low_key, 0)),
            'open': float(output.get(open_key, 0)),
            'volume': int(output.get('acml_vol', 0))
        }

        if not is_index:
            snapshot_data.update({
                'per': float(output.get('per', 0.0)),
                'pbr': float(output.get('pbr', 0.0)),
                'eps': float(output.get('eps', 0.0)),
                'bps': float(output.get('bps', 0.0)),
                'market_cap': (int(output.get('stck_prpr', 0)) * int(output.get('lstn_stcn', 0))) // 1000000
            })

        return snapshot_data
        
    def get_overseas_stock_price(self, market_code, stock_code):
        """해외 주식/지수의 현재가를 조회합니다."""
        URL = f"{self.client.BASE_URL}/uapi/overseas-price/v1/quotations/price"
        params = {"AUTH": "", "EXCD": market_code, "SYMB": stock_code}
        tr_id = "HHDFS00000300"
        
        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
        if res_data and res_data.get('output'):
            output = res_data['output']
            return {
                'last': float(output.get('last', 0)),
                'open': float(output.get('open', 0)),
                'high': float(output.get('high', 0)),
                'low': float(output.get('low', 0))
            }
        return None

    def get_stock_minute_prices(self, stock_code, target_date_yyyymmdd: str, minute_interval: int = 5):
        """
        특정 종목의 특정 날짜 분봉 데이터를 조회합니다.
        
        Args:
            stock_code: 종목 코드 (6자리)
            target_date_yyyymmdd: 조회 날짜 (YYYYMMDD)
            minute_interval: 분봉 주기 (1, 3, 5, 10, 15, 30, 60)
        
        Returns:
            List[dict]: 분봉 데이터 리스트
        """
        logger.info(f"   (Data) [{stock_code}] {target_date_yyyymmdd} {minute_interval}분봉 데이터 조회 시도...")
        
        # KIS API 분봉 차트 조회 엔드포인트 (여러 가능성 시도)
        # 참고: KIS API 공식 문서 확인 필요
        # 가능한 엔드포인트들:
        # 1. /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
        # 2. /uapi/domestic-stock/v1/quotations/inquire-time-chartprice
        # 3. /uapi/domestic-stock/v1/quotations/inquire-time-condchartprice
        
        # 분봉 코드 매핑
        period_code_map = {
            1: "1",
            3: "3",
            5: "5",
            10: "10",
            15: "15",
            30: "30",
            60: "60"
        }
        period_code = period_code_map.get(minute_interval, "5")
        
        # 여러 엔드포인트와 TR_ID 조합 시도
        endpoints_to_try = [
            {
                "url": f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                "tr_id": "FHKST03010200",
                "params": {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_HOUR_1": target_date_yyyymmdd,
                    "FID_INPUT_HOUR_2": target_date_yyyymmdd,
                    "FID_PRC_DIV_CODE": period_code,
                    "FID_ORG_ADJ_PRC": "1"
                }
            },
            {
                "url": f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-chartprice",
                "tr_id": "FHKST03010200",
                "params": {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_DATE_1": target_date_yyyymmdd,
                    "FID_INPUT_DATE_2": target_date_yyyymmdd,
                    "FID_PERIOD_DIV_CODE": period_code,
                    "FID_ORG_ADJ_PRC": "1"
                }
            },
            {
                "url": f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-condchartprice",
                "tr_id": "FHKST03010200",
                "params": {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_DATE_1": target_date_yyyymmdd,
                    "FID_INPUT_DATE_2": target_date_yyyymmdd,
                    "FID_PERIOD_DIV_CODE": period_code
                }
            }
        ]
        
        for attempt in endpoints_to_try:
            URL = attempt["url"]
            tr_id = attempt["tr_id"]
            params = attempt["params"]
            
            logger.debug(f"   (Data) 시도 중: {URL}, TR_ID: {tr_id}")
            
            res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
            
            if res_data and res_data.get('rt_cd') == '0':
                output_list = res_data.get("output") or res_data.get("output2") or []
                if output_list:
                    logger.info(f"   (Data) ✅ 성공한 엔드포인트: {URL}")
                    break
            else:
                if res_data:
                    logger.debug(f"   (Data) 실패: {res_data.get('msg1', 'Unknown')}")
                continue
        else:
            # 모든 시도 실패
            logger.warning(f"   (Data) [{stock_code}] 모든 분봉 API 엔드포인트 시도 실패")
            logger.warning(f"   (Data) KIS API에서 분봉 데이터를 제공하지 않을 수 있습니다.")
            logger.warning(f"   (Data) → KIS API 공식 문서 확인 또는 고객 지원 문의 필요")
            return []
        
        # 성공한 경우 output_list는 이미 위에서 확인됨
        output_list = res_data.get("output") or res_data.get("output2") or []
        if not output_list:
            logger.warning(f"   (Data) [{stock_code}] {target_date_yyyymmdd} 분봉 데이터 없음")
            return []
        
        rows = []
        for minute_data in output_list:
            try:
                # 실제 필드명은 API 응답 확인 필요
                # 일반적인 KIS API 필드명 추정:
                date_str = minute_data.get("stck_bsop_date") or target_date_yyyymmdd
                time_str = minute_data.get("stck_cntg_hour") or minute_data.get("stck_time") or "090000"
                
                # YYYYMMDD + HHMMSS -> datetime
                try:
                    dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                except ValueError:
                    # 다른 형식 시도
                    try:
                        dt = datetime.strptime(f"{date_str} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}", "%Y%m%d %H:%M:%S")
                    except:
                        logger.debug(f"   (Data) 날짜 파싱 실패: {date_str}, {time_str}")
                        continue
                
                rows.append({
                    'datetime': dt,
                    'open': float(minute_data.get("stck_stdprc") or minute_data.get("stck_oprc") or minute_data.get("stck_oprc", 0)),
                    'high': float(minute_data.get("stck_hgpr") or 0),
                    'low': float(minute_data.get("stck_lwpr") or 0),
                    'close': float(minute_data.get("stck_clpr") or 0),
                    'volume': int(minute_data.get("acml_vol") or minute_data.get("acml_tr_pbmn") or 0)
                })
            except Exception as e:
                logger.debug(f"   (Data) 분봉 데이터 파싱 오류: {e}, 데이터: {minute_data}")
                continue
        
        logger.info(f"   (Data) ✅ [{stock_code}] 분봉 데이터 {len(rows)}건 수신 완료.")
        return rows

    def get_investor_trend(self, stock_code: str, start_date: str = None, end_date: str = None):
        """
        [v4.0 NEW] 종목별 투자자(외국인/기관/개인) 매매 동향 조회
        TR_ID: FHKST01010900 (국내주식투자자별일별매매상위) - *확인 필요*
        실제로는 'inquire-investor' 엔드포인트 사용 (종목별유효기간별매매거래실적추이)
        
        Args:
            stock_code: 종목 코드
            start_date: 시작일 (YYYYMMDD) - None이면 최근 30일
            end_date: 종료일 (YYYYMMDD) - None이면 오늘
        
        Returns:
            List[dict]: 일별 투자자 매매 동향
        """
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
        
        # TR_ID 확인: FHKST01010900 (투자자별매매동향/기간별)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date
        }
        tr_id = "FHKST01010900" 
        
        logger.info(f"   (Data) [{stock_code}] 투자자 동향 조회 ({start_date}~{end_date})")
        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
        
        rows = []
        if res_data and res_data.get('output'):
            for item in res_data['output']:
                try:
                    # 필드명 매핑 (KIS API 문서 기준)
                    # stck_bsop_date: 날짜
                    # prsn_ntby_qty: 개인순매수수량
                    # frgn_ntby_qty: 외국인순매수수량
                    # orgn_ntby_qty: 기관계순매수수량
                    # (금액 기준 필드도 있을 수 있음: prsn_ntby_tr_pbmn 등)
                    
                    rows.append({
                        'date': item['stck_bsop_date'],
                        'price': float(item.get('stck_clpr', 0)),
                        'individual_net_buy': int(item.get('prsn_ntby_qty', 0)),
                        'foreigner_net_buy': int(item.get('frgn_ntby_qty', 0)),
                        'institution_net_buy': int(item.get('orgn_ntby_qty', 0)),
                        'pension_net_buy': int(item.get('ntik_ntby_qty', 0) or 0), # 연기금 (있다면)
                        'program_net_buy': 0 # 별도 API 필요할 수 있음
                    })
                except Exception as e:
                    continue
        
        return rows

    def get_program_trend(self, stock_code: str, start_date: str = None, end_date: str = None):
        """
        [v4.0 NEW] 종목별 프로그램 매매 동향 조회
        TR_ID: FHKST01010600 (프로그램매매종합) - *종목별 아닐 수 있음*
        대체: 'inquire-program-trade' (가칭, 문서 확인 필요) 확인불가 시 투자자 동향에서 추출 시도.
        
        *KIS Open API에는 종목별 프로그램 매매 추이 전용 URL이 명확치 않음.*
        따라서 투자자 동향 응답에 포함되지 않는 경우, 별도로 구현하거나 생략.
        
        현재는 Skeleton만 구현하고, 실제 API 응답 확인 후 보완 예정.
        """
        # NOTE: KIS API에서 종목별 프로그램 매매 추이 엔드포인트를 찾기 어려움.
        # 일단 빈 리스트 반환하도록 처리 후 추후 보완.
        logger.warning(f"   (Data) [{stock_code}] 프로그램 매매 동향 API 미구현 (KIS 문서 확인 필요)")
        return []

    def get_stock_history_by_chart(self, stock_code: str, period_code: str = 'D', start_date: str = None, end_date: str = None):
        """
        [New] 차트 API를 사용하여 대량의 과거 데이터(최대 1000건 이상)를 조회합니다.
        
        Args:
            stock_code: 종목 코드
            period_code: 기간 코드 ('D': 일봉, 'W': 주봉, 'M': 월봉)
            start_date: 시작일 (YYYYMMDD)
            end_date: 종료일 (YYYYMMDD)
        
        Returns:
            List[dict]: 가격 데이터 리스트 (오래된 순서대로 정렬됨)
        """
        logger.info(f"   (Data) [{stock_code}] 차트 API로 과거 데이터 조회 시도 ({start_date} ~ {end_date})")
        
        # URL: 국내주식기간별시세(일/주/월/년)
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        
        # TR_ID: FHKST03010100 (주식기간별시세)
        tr_id = "FHKST03010100"
        
        # API 100건 제한 대응: 반복 조회
        all_rows = []
        current_end_date = end_date
        target_start_date = datetime.strptime(start_date, "%Y%m%d")
        
        while True:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": current_end_date,
                "FID_PERIOD_DIV_CODE": period_code,
                "FID_ORG_ADJ_PRC": "1"
            }
            
            # 잦은 호출 방지
            import time
            time.sleep(0.1)
            
            res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
            
            if res_data and res_data.get('output2'):
                output_list = res_data.get('output2')
                if not output_list:
                    break
                    
                chunk_rows = []
                min_date_in_chunk = None
                
                for item in output_list:
                    if not item.get('stck_clpr'):
                        continue
                    
                    dt_str = item['stck_bsop_date']
                    dt_obj = datetime.strptime(dt_str, '%Y%m%d')
                    
                    # 이미 수집한 날짜보다 미래거나 같으면 스킵 (중복 방지)
                    # (API가 inclusive하게 줄 수 있음)
                    
                    chunk_rows.append({
                        'date': dt_obj.strftime('%Y-%m-%d'),
                        'open': float(item['stck_oprc']),
                        'high': float(item['stck_hgpr']),
                        'low': float(item['stck_lwpr']),
                        'close': float(item['stck_clpr']),
                        'volume': int(item['acml_vol'])
                    })
                    
                    if min_date_in_chunk is None or dt_obj < min_date_in_chunk:
                        min_date_in_chunk = dt_obj
                
                if not chunk_rows:
                    break
                    
                all_rows.extend(chunk_rows)
                
                # 다음 조회를 위해 end_date 업데이트 (가장 과거 날짜 - 1일)
                if min_date_in_chunk and min_date_in_chunk > target_start_date:
                    current_end_date = (min_date_in_chunk - timedelta(days=1)).strftime('%Y%m%d')
                    logger.debug(f"   (Data) [{stock_code}] 추가 조회 필요. Next End Date: {current_end_date}")
                else:
                    # 목표 시작일 도달
                    break
                    
                # 무한 루프 방지 (데이터가 100개 미만이면 더 이상 없다는 뜻일 수 있음)
                if len(output_list) < 100:
                    break
            else:
                break
        
        # 중복 제거 (혹시 모를 겹침)
        unique_rows = {r['date']: r for r in all_rows}
        rows = list(unique_rows.values())
        
        # 날짜 오름차순 정렬
        rows.sort(key=lambda x: x['date'])
        
        logger.info(f"   (Data) ✅ [{stock_code}] 총 {len(rows)}건 차트 데이터 수신 완료.")
        return rows