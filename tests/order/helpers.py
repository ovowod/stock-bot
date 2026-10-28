"""주문·취소·미체결 테스트가 함께 쓰는 요청 본문과 키움 응답."""

from tests.fake_kiwoom import usa10099_row

# kra-docs ka10075 responseExample의 한 줄.
OPEN_ORDER = {
    "acnt_no": "1234567890",
    "ord_no": "0000069",
    "mang_empno": "",
    "stk_cd": "005930",
    "tsk_tp": "",
    "ord_stt": "접수",
    "stk_nm": "삼성전자",
    "ord_qty": "1",
    "ord_pric": "0",
    "oso_qty": "1",
    "cntr_tot_amt": "0",
    "orig_ord_no": "0000000",
    "io_tp_nm": "+매수",
    "trde_tp": "시장가",
    "tm": "154113",
    "cntr_no": "",
    "cntr_pric": "0",
    "cntr_qty": "0",
    "cur_prc": "+74100",
    "sel_bid": "0",
    "buy_bid": "+74100",
    "unit_cntr_pric": "",
    "unit_cntr_qty": "",
    "tdy_trde_cmsn": "0",
    "tdy_trde_tax": "0",
    "ind_invsr": "",
    "stex_tp": "1",
    "stex_tp_txt": "KRX",
    "sor_yn": "N",
}


def open_order(**overrides: object) -> dict[str, object]:
    return {**OPEN_ORDER, **overrides}


# kra-docs kt10003 responseExample 모양.
CANCEL_REPLY = {"ord_no": "0000141", "base_orig_ord_no": "0000070", "cncl_qty": "000000000003"}


LIMIT_SELL = open_order(
    ord_no="0000070",
    stk_cd="000660",
    stk_nm="SK하이닉스",
    io_tp_nm="-매도",
    trde_tp="보통",
    ord_pric="-201000",
    ord_qty="10",
    oso_qty="3",
)


def cancel(**overrides: object) -> dict[str, object]:
    return {"order_key": "cancel-1", "order_no": "0000070", "quantity": "3", **overrides}


def domestic_order(**overrides: object) -> dict[str, object]:
    return {
        "order_key": "key-1",
        "side": "buy",
        "code": "005930",
        "order_type": "limit",
        "quantity": "3",
        "price": "61300",
        **overrides,
    }


# kra-docs ust21050 responseExample의 한 줄.
US_OPEN_ORDER = {
    "ord_cntr_tp": "10",
    "ord_no": "000000282",
    "orig_ord_no": "000000000",
    "frgn_ord_id": "NF.19ef9aa9-6f51-0000-19ef-9aa96f510000",
    "stex_nm": "미국",
    "crnc_code": "USD",
    "stk_cd": "NVDA",
    "frgn_stk_nm": "엔비디아",
    "frgn_trde_tp": "00",
    "frgn_trde_nm": "지정가",
    "slby_tp": "2",
    "slby_tp_nm": "매수",
    "ord_qty": "000000000005",
    "ord_uv": "200.0000",
    "stop_pric": "0.0000",
    "cntr_qty": "000000000000",
    "cntr_uv": "0.0000",
    "mdfy_qty": "000000000000",
    "mdfy_uv": "0.0000",
    "cncl_qty": "000000000000",
    "ord_remnq": "000000000005",
    "ord_time": "21:46:06",
    "ord_resp_time": "21:46:07",
    "ord_stat": "접수",
    "rsrv_tp": "일반",
    "natn_nm": "미국",
}


LISTING = [
    usa10099_row("NVDA", "엔비디아", "NVIDIA CORP"),
    usa10099_row("EWZ", "아이셰어즈 MSCI 브라질 ETF", "ISHARES MSCI BRAZIL ETF", "NY"),
]


def us_open_order(**overrides: object) -> dict[str, object]:
    return {**US_OPEN_ORDER, **overrides}
