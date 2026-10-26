import logging

from tests.fake_kiwoom import (
    UST21070_HOLDING,
    UST21070_REPLY,
    body_of,
    kiwoom_error,
    us_fake,
    usa10099_row,
)

URL = "/api/environments/us_paper/account"


def test_us_account_is_converted_for_the_screen(make_client):
    fake = us_fake()
    body = make_client(fake).get(URL).json()

    assert body_of(fake.calls("ust21070")[0]) == {"stex_tp": "", "stk_cd": ""}
    assert body_of(fake.calls("ust21110")[0]) == {}
    assert body["environment"] == "us_paper"
    assert body["market"] == "us"
    assert body["account_no"] == "8200****22"
    assert body["currency"] == "USD"
    # 문서에 없는 최상위 stex_tp("000030")는 응답에 담지 않는다.
    assert "stex_tp" not in body
    assert body["summary"] == {
        "total_evaluation": 156464.6701,
        "total_purchase": 157279.9717,
        "total_profit_loss": -1599.6616,
        "total_return_rate": -1.01,
        "today_realized_profit_loss": 0.0,
        "today_realized_return_rate": 0.0,
    }
    assert body["summary_krw"] == {
        "total_evaluation": 238530390,
        "total_purchase": 239773317,
        "total_profit_loss": -2438684,
        "today_realized_profit_loss": 0,
    }
    assert body["deposit"] == {
        "krw_deposit": 930907881,
        "currencies": [
            {
                "currency": "USD",
                "currency_name": "미국달러",
                "deposit": 18039493.57,
                "orderable": 18613792.11,
                "withdrawable": 18039493.57,
            }
        ],
    }
    assert body["holdings"] == [
        {
            "code": "AAPL",
            "name": "애플",
            "exchange": "미국",
            "currency": "USD",
            "quantity": 395,
            "sellable_quantity": 395,
            "purchase_price": 282.1603,
            "current_price": 275.24,
            "purchase_amount": 111453.3212,
            "evaluation_amount": 108719.8,
            "profit_loss": -3283.9512,
            "return_rate": -2.94,
            "evaluation_amount_krw": 165743335,
            "profit_loss_krw": -5006383,
        }
    ]


def test_empty_us_holdings_is_a_normal_result(make_client):
    fake = us_fake().reply("ust21070", {**UST21070_REPLY, "result_list": []})
    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert response.json()["holdings"] == []


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
