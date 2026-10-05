import logging

from tests.fake_kiwoom import (
    UST21070_HOLDING,
    UST21070_REPLY,
    kiwoom_error,
    us_fake,
    usa10099_row,
)

URL = "/api/environments/us_paper/account"


def test_us_holding_exchange_names_match_the_ranking_and_search_names(make_client):
    """잔고 TR은 거래소를 한글명(뉴욕·나스닥·아멕스)으로 준다. 순위·검색의 영문명으로 바꾼다."""
    names = {"IONQ": "뉴욕", "NVDA": "나스닥", "SPY": "아멕스", "ABCD": "미국"}
    holdings = [
        {**UST21070_HOLDING, "stk_cd": code, "stex_nm": name} for code, name in names.items()
    ]
    fake = us_fake().reply("ust21070", {**UST21070_REPLY, "result_list": holdings})

    body = make_client(fake).get(URL).json()

    exchanges = {h["code"]: h["exchange"] for h in body["holdings"]}
    # 모르는 이름은 받은 그대로 보여준다.
    assert exchanges == {"IONQ": "NYSE", "NVDA": "NASDAQ", "SPY": "AMEX", "ABCD": "미국"}


def test_us_holding_names_and_exchanges_come_from_the_stock_list(make_client):
    """잔고 TR은 ETF를 영문명으로, NYSE Arca ETF를 아멕스로 준다. 종목 목록 값으로 맞춘다."""
    holdings = [
        {
            **UST21070_HOLDING,
            "stk_cd": "SOXL",
            "frgn_stk_nm": "DIREXION SEMICONDUCTOR DAILY 3X",
            "stex_nm": "아멕스",
        },
        {
            **UST21070_HOLDING,
            "stk_cd": "ZZZZ",
            "frgn_stk_nm": "목록에 없는 종목",
            "stex_nm": "나스닥",
        },
    ]
    stock_list = {
        "list": [usa10099_row("SOXL", "미국 반도체 3배 디렉시온 ETF", "DIREXION 3X", "NY")]
    }
    fake = (
        us_fake()
        .reply("ust21070", {**UST21070_REPLY, "result_list": holdings})
        .reply("usa10099", stock_list)
    )

    body = make_client(fake).get(URL).json()

    shown = {h["code"]: (h["name"], h["exchange"]) for h in body["holdings"]}
    # 목록에 없는 종목은 잔고 TR의 이름과 거래소(영문으로 바꾼 값)를 쓴다.
    assert shown == {
        "SOXL": ("미국 반도체 3배 디렉시온 ETF", "NYSE"),
        "ZZZZ": ("목록에 없는 종목", "NASDAQ"),
    }


def test_us_holding_names_fall_back_when_the_stock_list_fails(make_client):
    fake = us_fake().reply("usa10099", kiwoom_error(1700, "허용된 요청 개수를 초과하였습니다"))

    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert response.json()["holdings"][0]["name"] == UST21070_HOLDING["frgn_stk_nm"]


def test_a_broken_holding_number_is_shown_as_empty_instead_of_failing(make_client, caplog):
    """모의 서버가 장중에 pl_amt를 '. 950'처럼 깨진 값으로 보낸 적이 있다. 그 칸만 비운다."""
    caplog.set_level(logging.INFO, logger="stock_bot")
    holdings = [{**UST21070_HOLDING, "pl_amt": ". 950"}]
    fake = us_fake().reply("ust21070", {**UST21070_REPLY, "result_list": holdings})

    response = make_client(fake).get(URL)

    assert response.status_code == 200
    holding = response.json()["holdings"][0]
    assert holding["profit_loss"] is None
    assert holding["return_rate"] is not None
    warnings = [r for r in caplog.records if r.getMessage() == "holding_value_unreadable"]
    assert warnings[0].fields["key"] == "pl_amt"  # type: ignore[attr-defined]
    assert warnings[0].fields["raw"] == ". 950"  # type: ignore[attr-defined]
