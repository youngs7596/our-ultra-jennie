"""
services/command-handler/messages.py

응답 메시지 템플릿을 한곳에 모아 handler 본문을 단순화합니다.
"""


HELP_TEXT = """📚 *Ultra Jennie 명령어 (24개)*

_(매수/매도는 실행 서비스로 큐 전송 후 처리됩니다)_ 

*매매 제어*: /pause /resume /stop 확인 /dryrun on|off
*수동 매매*: /buy 종목 [수량] /sell 종목 [수량|전량] /sellall 확인
*관심종목*: /watch 종목 /unwatch 종목 /watchlist
*조회*: /status /portfolio /pnl /balance /price 종목
*알림*: /mute 분 /unmute /alert 종목 가격 /alerts
*설정*: /risk conservative|moderate|aggressive /minscore 점수 /maxbuy 횟수 /config
*도움말*: /help /help 명령어"""


def stop_confirm_message() -> str:
    return (
        "⚠️ 긴급 중지 명령입니다.\n\n모든 매수/매도가 중단됩니다.\n"
        "확인하려면 `/stop 확인`을 입력하세요."
    )
