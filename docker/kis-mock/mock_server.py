#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIS API Mock Server
로컬 테스트용 가짜 KIS API 서버 (HTTP REST API)
"""

from flask import Flask, request, jsonify
import random
import time
from datetime import datetime

app = Flask(__name__)

# Mock 데이터
STOCK_DATA = {
    "005930": {"name": "삼성전자", "base_price": 70000},
    "000660": {"name": "SK하이닉스", "base_price": 130000},
    "035420": {"name": "NAVER", "base_price": 210000},
    "035720": {"name": "카카오", "base_price": 45000},
    "0001": {"name": "KOSPI", "base_price": 2500},
}

# 토큰 저장소
tokens = {}

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "kis-mock"}), 200

@app.route('/oauth2/tokenP', methods=['POST'])
def token():
    """OAuth 토큰 발급"""
    data = request.json
    app_key = data.get('appkey')
    app_secret = data.get('appsecret')
    
    # 간단한 토큰 생성
    token = f"mock_token_{int(time.time())}"
    tokens[token] = {
        "app_key": app_key,
        "created_at": datetime.now(),
        "expires_in": 86400
    }
    
    return jsonify({
        "access_token": token,
        "access_token_token_expired": "2099-12-31 23:59:59",
        "token_type": "Bearer",
        "expires_in": 86400
    }), 200

@app.route('/uapi/domestic-stock/v1/quotations/inquire-price', methods=['GET'])
@app.route('/uapi/domestic-stock/v1/quotations/inquire-price-2', methods=['GET'])
def inquire_price():
    """현재가 조회"""
    # 대소문자 모두 지원
    stock_code = request.args.get('FID_INPUT_ISCD') or request.args.get('fid_input_iscd')
    
    if not stock_code:
        return jsonify({
            "rt_cd": "1",
            "msg1": "종목코드 파라미터 누락"
        }), 400
    
    if stock_code not in STOCK_DATA:
        return jsonify({
            "rt_cd": "1",
            "msg1": "종목코드 없음"
        }), 404
    
    stock = STOCK_DATA[stock_code]
    
    # 랜덤 가격 변동 (-3% ~ +3%)
    price_change = random.uniform(-0.03, 0.03)
    current_price = int(stock["base_price"] * (1 + price_change))
    
    # 다른 데이터도 랜덤 생성
    open_price = int(current_price * random.uniform(0.98, 1.02))
    high_price = int(current_price * random.uniform(1.00, 1.05))
    low_price = int(current_price * random.uniform(0.95, 1.00))
    volume = random.randint(1000000, 50000000)
    
    return jsonify({
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": {
            "stck_prpr": str(current_price),  # 현재가
            "stck_oprc": str(open_price),     # 시가
            "stck_hgpr": str(high_price),     # 고가
            "stck_lwpr": str(low_price),      # 저가
            "acml_vol": str(volume),          # 누적거래량
            "prdy_vrss": str(int(current_price * price_change)),  # 전일 대비
            "prdy_vrss_sign": "2" if price_change >= 0 else "5",  # 등락 기호
            "prdy_ctrt": f"{price_change * 100:.2f}",  # 전일 대비율
            "per": "12.34",
            "pbr": "1.23",
            "eps": "5000",
            "bps": "57000",
            "hts_kor_isnm": stock["name"]
        }
    }), 200

@app.route('/uapi/domestic-stock/v1/trading/order-cash', methods=['POST'])
def order_cash():
    """현금 주문 (매수/매도)"""
    data = request.json
    stock_code = data.get('PDNO')
    order_qty = data.get('ORD_QTY')
    order_price = data.get('ORD_UNPR', '0')
    order_type = data.get('ORD_DVSN')  # 00: 지정가, 01: 시장가
    
    # Mock 주문번호 생성
    order_no = f"MOCK{int(time.time())}{random.randint(1000, 9999)}"
    
    return jsonify({
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": {
            "KRX_FWDG_ORD_ORGNO": "00001",
            "ODNO": order_no,  # 주문번호
            "ORD_TMD": datetime.now().strftime("%H%M%S")
        }
    }), 200

@app.route('/uapi/domestic-stock/v1/trading/inquire-balance', methods=['GET'])
def inquire_balance():
    """잔고 조회"""
    # 빈 잔고 반환 (또는 테스트용 Mock 데이터)
    return jsonify({
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output1": [],  # 보유 종목 목록
        "output2": [{
            "dnca_tot_amt": "10000000",  # 예수금 총액
            "nxdy_excc_amt": "10000000",  # 익일 정산 금액
            "prvs_rcdl_excc_amt": "10000000",  # 전일 정산 금액
            "tot_evlu_amt": "0",  # 총 평가금액
            "pchs_amt_smtl_amt": "0",  # 매입금액합계
            "evlu_amt_smtl_amt": "0",  # 평가금액합계
            "evlu_pfls_smtl_amt": "0"  # 평가손익합계
        }]
    }), 200

@app.route('/uapi/domestic-stock/v1/quotations/inquire-daily-price', methods=['GET'])
def inquire_daily_price():
    """일별 시세 조회"""
    stock_code = request.args.get('fid_input_iscd')
    
    if stock_code not in STOCK_DATA:
        return jsonify({"rt_cd": "1", "msg1": "종목코드 없음"}), 404
    
    stock = STOCK_DATA[stock_code]
    base_price = stock["base_price"]
    
    # 최근 30일 데이터 생성
    output = []
    for i in range(30):
        date = datetime.now()
        date_str = date.strftime("%Y%m%d")
        
        # 랜덤 데이터
        close = int(base_price * random.uniform(0.95, 1.05))
        open_p = int(close * random.uniform(0.98, 1.02))
        high = int(close * random.uniform(1.00, 1.03))
        low = int(close * random.uniform(0.97, 1.00))
        volume = random.randint(1000000, 50000000)
        
        output.append({
            "stck_bsop_date": date_str,
            "stck_clpr": str(close),
            "stck_oprc": str(open_p),
            "stck_hgpr": str(high),
            "stck_lwpr": str(low),
            "acml_vol": str(volume)
        })
    
    return jsonify({
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": output
    }), 200

if __name__ == '__main__':
    print("🚀 KIS Mock Server 시작...")
    print("   포트: 9443")
    print("   Health Check: http://localhost:9443/health")
    app.run(host='0.0.0.0', port=9443, debug=True)

