"""키움 REST API를 흉내 내는 가짜 서버. 응답 형태는 kra-docs의 responseExample을 따른다."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

Reply = dict[str, Any] | httpx.Response

FAKE_ENV = {
    "REAL_APP_KEY": "real-key-AAAA1111",
    "REAL_APP_SECRET": "real-secret-BBBB2222",
    "REAL_ACCOUNT_NO": "5012345611",
    "PAPER_KR_APP_KEY": "paperkr-key-CCCC3333",
    "PAPER_KR_APP_SECRET": "paperkr-secret-DDDD4444",
    "PAPER_KR_ACCOUNT_NO": "8100000111",
    "PAPER_US_APP_KEY": "paperus-key-EEEE5555",
    "PAPER_US_APP_SECRET": "paperus-secret-FFFF6666",
    "PAPER_US_ACCOUNT_NO": "8200000222",
}


@dataclass
class FakeKiwoom:
    expires_dt: str = "29991231235959"
    # api-id -> 응답 목록. 페이지마다 하나씩 꺼내 쓰고, 마지막 응답은 계속 재사용한다.
    replies: dict[str, list[Reply]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    issued_tokens: list[str] = field(default_factory=list)
    on_request: Callable[[httpx.Request], None] | None = None
    # api-id -> 요청을 보고 응답을 고르는 함수. 요청 본문에 따라 다른 응답이 필요할 때 쓴다.
    responders: dict[str, Callable[[httpx.Request], Reply]] = field(default_factory=dict)

    def reply(self, api_id: str, *pages: Reply) -> FakeKiwoom:
        self.replies[api_id] = list(pages)
        return self

    def respond(self, api_id: str, responder: Callable[[httpx.Request], Reply]) -> FakeKiwoom:
        self.responders[api_id] = responder
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request:
            self.on_request(request)
        if request.url.path == "/oauth2/token":
            token = f"token-{len(self.issued_tokens) + 1}-XYZW9876"
            self.issued_tokens.append(token)
            return httpx.Response(
                200,
                json={
                    "expires_dt": self.expires_dt,
                    "token_type": "bearer",
                    "token": token,
                    "return_code": 0,
                    "return_msg": "정상적으로 처리되었습니다",
                },
            )
        api_id = request.headers["api-id"]
        if api_id in self.responders:
            page = self.responders[api_id](request)
        else:
            pages = self.replies[api_id]
            index = sum(1 for r in self.requests if r.headers.get("api-id") == api_id) - 1
            page = pages[min(index, len(pages) - 1)]
        if isinstance(page, httpx.Response):
            return page
        return page_response(page)

    def calls(self, api_id: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.headers.get("api-id") == api_id]

    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/oauth2/token"]


def page_response(body: dict[str, Any], cont_yn: str = "N", next_key: str = "") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"cont-yn": cont_yn, "next-key": next_key},
        json={"return_code": 0, "return_msg": "조회가 완료되었습니다", **body},
    )


def body_of(request: httpx.Request) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(request.content or b"{}")
    return result


def kiwoom_error(code: int, message: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"return_code": code, "return_msg": message})


ACCOUNT_REPLY = {"acctNo": "8100000111"}

KT00018_HOLDING = {
    "stk_cd": "A005930",
    "stk_nm": "삼성전자",
    "evltv_prft": "-00000000196888",
    "prft_rt": "-52.71",
    "pur_pric": "000000000124500",
    "pred_close_pric": "000000045400",
    "rmnd_qty": "000000000000003",
    "trde_able_qty": "000000000000003",
    "cur_prc": "000000059000",
    "pred_buyq": "000000000000000",
    "pred_sellq": "000000000000000",
    "tdy_buyq": "000000000000000",
    "tdy_sellq": "000000000000000",
    "pur_amt": "000000000373500",
    "pur_cmsn": "000000000000050",
    "evlt_amt": "000000000177000",
    "sell_cmsn": "000000000000020",
    "tax": "000000000000318",
    "sum_cmsn": "000000000000070",
    "poss_rt": "2.12",
    "crd_tp": "00",
    "crd_tp_nm": "",
    "crd_loan_dt": "",
}

KT00018_REPLY = {
    "tot_pur_amt": "000000017598258",
    "tot_evlt_amt": "000000025789890",
    "tot_evlt_pl": "000000008138825",
    "tot_prft_rt": "46.25",
    "prsm_dpst_aset_amt": "000001012632507",
    "tot_loan_amt": "000000000000000",
    "tot_crd_loan_amt": "000000000000000",
    "tot_crd_ls_amt": "000000000000000",
    "acnt_evlt_remn_indv_tot": [KT00018_HOLDING],
}

KT00001_REPLY = {
    "entr": "000000000017534",
    "pymn_alow_amt": "000000000085341",
    "ord_alow_amt": "000000000085341",
    "d1_entra": "000000000017450",
    "d2_entra": "000000000012550",
    "stk_entr_prst": [],
}

UST21070_HOLDING = {
    "stex_nm": "미국",
    "crnc_code": "USD",
    "stk_cd": "AAPL",
    "frgn_stk_nm": "애플",
    "qty": "000000000395",
    "poss_qty": "000000000395",
    "sell_alowq": "000000000395",
    "pred_cntr_sellq": "000000000000",
    "pred_cntr_buyq": "000000000000",
    "tdy_cntr_sellq": "000000000000",
    "tdy_cntr_buyq": "000000000000",
    "frgn_stk_book_uv": "282.1603",
    "now_pric": "275.2400",
    "evlt_amt": "108719.8000",
    "pl_amt": "-3283.9512",
    "pl_rt": "-2.94",
    "evlt_amt_krw": "000165743335",
    "pl_amt_krw": "-00005006383",
    "natn_nm": "미국",
    "exch_rate": "1524.50",
    "frgn_stk_book_uv_krw": "000000430153",
    "now_pric_krw": "000000419603",
    "frgn_stk_book_amt": "111453.3212",
    "frgn_stk_book_amt_krw": "000169910588",
}

UST21070_REPLY = {
    "stex_tp": "000030",
    "crnc_code": "USD",
    "tot_evlt_amt": "156464.6701",
    "tot_prch_amt": "157279.9717",
    "tot_pl_amt": "-1599.6616",
    "tot_pl_rt": "-1.01",
    "tdy_book_amt": "0.0000",
    "tdy_pl_amt": "0.0000",
    "tdy_pl_rt": "0.00",
    "tot_evlt_amt_krw": "000000238530390",
    "tot_prch_amt_krw": "000000239773317",
    "tot_pl_amt_krw": "-00000002438684",
    "tdy_book_amt_krw": "000000000000000",
    "tdy_pl_amt_krw": "000000000000000",
    "result_list": [UST21070_HOLDING],
}

UST21110_REPLY = {
    "krw_entra": "000000930907881",
    "ch_uncla": "000000000000000",
    "etc_loana": "000000000000000",
    "result_list": [
        {
            "crnc_code": "USD",
            "crnc_nm": "미국달러",
            "fc_entra": "18039493.57",
            "fc_pymn_alowa": "18039493.57",
            "futr_repl_profa": "0.00",
            "fc_booka": "000027114520714",
            "fc_ord_alowa": "18613792.11",
            "futr_profa_booka": "000000000000000",
            "fc_ch_uncla": "0.00",
            "fc_etc_loana": "0.00",
        }
    ],
}


def domestic_fake(account_no: str = "8100000111") -> FakeKiwoom:
    return (
        FakeKiwoom()
        .reply("ka00001", {"acctNo": account_no})
        .reply("kt00018", KT00018_REPLY)
        .reply("kt00001", KT00001_REPLY)
    )


def us_fake(account_no: str = "8200000222") -> FakeKiwoom:
    return (
        FakeKiwoom()
        .reply("ka00001", {"acctNo": account_no})
        .reply("ust21070", UST21070_REPLY)
        .reply("ust21110", UST21110_REPLY)
    )


# 순위 TR 응답. 값은 모의 서버에서 실제로 받은 형태를 따른다(국내 종목코드의 _AL 접미어 포함).
KA10032_ROW = {
    "stk_cd": "000660_AL",
    "now_rank": "1",
    "pred_rank": "2",
    "stk_nm": "SK하이닉스",
    "cur_prc": "+1841000",
    "pred_pre_sig": "2",
    "pred_pre": "+8000",
    "flu_rt": "+0.44",
    "sel_bid": "+1842000",
    "buy_bid": "+1841000",
    "now_trde_qty": "2911335",
    "pred_trde_qty": "3274713",
    "trde_prica": "5359250",
}

USA20540_ROW = {
    "rank": "1",
    "stex_tp": "NY",
    "stk_cd": "SOXL",
    "stk_nm": "미국 반도체 3배 디렉시온 ETF",
    "stk_enm": "DIREXION DAILY SEMICONDUCTOR BULL 3X ETF",
    "cur_prc": "-162.6000",
    "pred_pre_sig": "5",
    "pred_pre": "-1.1100",
    "flu_rt": "-0.68",
    "acc_trde_qty": "632235",
    "pred_trde_qty": "62660789",
    "trde_prica": "104125",
}


KA10027_ROW = {
    "stk_cls": "14",
    "stk_cd": "069920_AL",
    "stk_nm": "엑시온그룹",
    "cur_prc": "+1349",
    "pred_pre_sig": "1",
    "pred_pre": "+311",
    "flu_rt": "+29.96",
    "sel_req": "0",
    "buy_req": "95187",
    "now_trde_qty": "1306705",
    "cntr_str": "118.83",
    "cnt": "1",
}

KA10030_ROW = {
    "stk_cd": "114800_AL",
    "stk_nm": "KODEX 인버스",
    "cur_prc": "-975",
    "pred_pre_sig": "5",
    "pred_pre": "-6",
    "flu_rt": "-0.61",
    "trde_qty": "505027412",
    "pred_rt": "+100.00",
    "trde_tern_rt": "+68.45",
    "trde_amt": "495085",
    "opmr_trde_qty": "",
    "opmr_pred_rt": "",
    "opmr_trde_rt": "",
    "opmr_trde_amt": "",
    "af_mkrt_trde_qty": "",
    "af_mkrt_pred_rt": "",
    "af_mkrt_trde_rt": "",
    "af_mkrt_trde_amt": "",
    "bf_mkrt_trde_qty": "",
    "bf_mkrt_pred_rt": "",
    "bf_mkrt_trde_rt": "",
    "bf_mkrt_trde_amt": "",
}

USA20910_ROW = {
    "rank": "1",
    "stex_tp": "ND",
    "stk_cd": "SAIQ",
    "stk_nm": "와이즈샛.스페이스",
    "stk_enm": "WISESAT.SPACE HOLDINGS CORPORATION",
    "cur_prc": "+12.9100",
    "pred_pre_sig": "2",
    "pred_pre": "+11.0600",
    "flu_rt": "+597.84",
    "sel_req": "1484",
    "buy_req": "647",
    "trde_qty": "5005644",
    "cnt": "1",
}

USA20530_ROW = {
    "rank": "1",
    "stex_tp": "NA",
    "stk_cd": "SDEV",
    "stk_nm": "스테이블코인 개발",
    "stk_enm": "STABLECOIN DEVELOPMENT CORPORATION",
    "cur_prc": "+9.1864",
    "pred_pre_sig": "2",
    "pred_pre": "+1.7064",
    "flu_rt": "+22.81",
    "acc_trde_qty": "2603716",
    "pred_rt": "-98.30",
    "trde_prica": "23701",
}


KA00198_ROW = {
    "stk_nm": "성호전자",
    "bigd_rank": "3",
    "rank_chg": "+3",
    "rank_chg_sign": "+",
    "past_curr_prc": "+31400",
    "base_comp_sign": "2",
    "base_comp_chgr": "+10.18",
    "prev_base_sign": "3",
    "prev_base_chgr": "0.00",
    "dt": "20261005",
    "tm": "170000",
    "stk_cd": "043260",
}

USA01980_REPLY = {
    "base_date": "20261005",
    "base_time": "170000",
    "result_list": [
        {
            "rank": "04",
            "stk_cd": "NVDA",
            "stk_nm": "엔비디아",
            "sign": "-",
            "chg_val": "02",
            "curr_pric": "233.9500",
            "sign_for_gjga": "-",
            "diff_rate_for_gjga": "0.6200",
            "sign_for_prev": "",
            "diff_rate_for_prev": "0.0000",
            "stex_tp": "ND",
        }
    ],
}


# 종목 목록 TR 응답. 값은 모의 서버에서 실제로 받은 형태를 따른다.
def ka10099_row(code: str, name: str, market: str = "거래소", **extra: str) -> dict[str, str]:
    return {
        "code": code,
        "name": name,
        "listCount": "0000000027931470",
        "auditInfo": "정상",
        "regDay": "19760324",
        "lastPrice": "00005130",
        "state": "증거금40%|담보대출|신용가능",
        "marketCode": "0",
        "marketName": market,
        "upName": "전기전자",
        "upSizeName": "대형주",
        "companyClassName": "",
        "orderWarning": "0",
        "nxtEnable": "Y",
        "kind": "A",
        **extra,
    }


def usa10099_row(
    code: str, name: str, english: str, stex: str = "ND", **extra: str
) -> dict[str, str]:
    names = {"NY": "NYSE", "ND": "NASDAQ", "NA": "AMEX", "NP": "OTC"}
    return {
        "stex_tp": stex,
        "stk_cd": code,
        "stk_nm": name,
        "stk_enm": english,
        "mkgb": names.get(stex, stex),
        "upgb": "컴퓨터 및 전자장비",
        "isEtf": "N",
        **extra,
    }


KOSPI_ROWS = [
    ka10099_row("005930", "삼성전자"),
    ka10099_row("000660", "SK하이닉스"),
    ka10099_row("069500", "KODEX 200", "ETF", upName=""),
    ka10099_row("000040", "KR모터스", auditInfo="관리종목", upName="운송장비/부품"),
]
KOSDAQ_ROWS = [ka10099_row("247540", "에코프로비엠", "코스닥", upName="일반전기전자")]
US_ROWS = [
    usa10099_row("AAPL", "애플", "APPLE INC"),
    usa10099_row(
        "SPY", "S&P 500 SPDR ETF", "STATE STREET SPDR S&P 500 ETF", "NY", isEtf="Y", upgb=""
    ),
    usa10099_row(
        "SPY", "S&P 500 SPDR ETF", "STATE STREET SPDR S&P 500 ETF", "NY", isEtf="Y", upgb=""
    ),
    usa10099_row("APLE", "애플 호스피탈리티", "APPLE HOSPITALITY REIT INC", "NY"),
    usa10099_row("OTCX", "장외종목", "OTC EXAMPLE", "NP"),
]


def stock_list_fake(
    kospi: Reply | None = None, kosdaq: Reply | None = None, us: Reply | None = None
) -> FakeKiwoom:
    """ka10099는 mrkt_tp(0 코스피, 10 코스닥)에 따라, usa10099는 하나의 응답을 준다."""
    domestic = {
        "0": kospi if kospi is not None else {"list": KOSPI_ROWS},
        "10": kosdaq if kosdaq is not None else {"list": KOSDAQ_ROWS},
    }
    return (
        FakeKiwoom()
        .respond("ka10099", lambda request: domestic[body_of(request)["mrkt_tp"]])
        .reply("usa10099", us if us is not None else {"list": US_ROWS})
    )


def ranking_fake(api_id: str, *pages: Reply) -> FakeKiwoom:
    return FakeKiwoom().reply(api_id, *pages)


class ThreadedTransport(httpx.AsyncBaseTransport):
    """핸들러를 스레드에서 실행해, 한 요청이 기다리는 동안 다른 요청이 진행될 수 있게 한다."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        return await asyncio.to_thread(self._handler, request)
