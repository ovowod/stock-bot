"""미국 거래소 이름과 키움 코드. 미국 시세·주문·순위·검색·SL / TP가 이 표를 쓴다."""

# 화면의 미국 거래소 이름 -> 키움 stex_tp. 미국 시세·주문 TR은 이 세 거래소만 받는다.
US_EXCHANGE_CODES = {"NYSE": "NY", "NASDAQ": "ND", "AMEX": "NA"}
# 키움 stex_tp -> 화면 이름. 순위·종목 목록이 주는 코드를 바꾼다.
US_EXCHANGE_NAMES = {code: name for name, code in US_EXCHANGE_CODES.items()}
US_EXCHANGES = tuple(US_EXCHANGE_CODES)
