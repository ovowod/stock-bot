import logging
from typing import Any

import httpx
import pytest

from stock_bot import app as app_module
from stock_bot.notification import Embed
from tests.fake_kiwoom import FakeKiwoom, body_of, kiwoom_error
from tests.test_us_open_orders import LISTING, US_OPEN_ORDER, us_open_order

URL = "/api/environments/us_paper/cancellations"
# kra-docs ust20003 responseExample 모양.
US_CANCEL_REPLY = {
    "acnt_nm": "000",
    "stk_nm": "엔비디아",
    "ord_no": "000000285",
    "cncl_ord_qty": "000000000005",
}


def cancel(**overrides: object) -> dict[str, object]:
    return {"order_key": "us-cancel-1", "order_no": "000000282", "quantity": "5", **overrides}


def us_kiwoom(*orders: dict[str, object]) -> FakeKiwoom:
    rows = list(orders) if orders else [US_OPEN_ORDER]
    return (
        FakeKiwoom()
        .reply("ust21050", {"result_list": rows})
        .reply("usa10099", {"list": LISTING})
        .reply("ust20003", US_CANCEL_REPLY)
    )


def api_ids(fake: FakeKiwoom) -> list[str]:
    return [r.headers["api-id"] for r in fake.requests if "api-id" in r.headers]


def test_us_cancel_rechecks_then_sends_ust20003_with_the_listing_exchange(make_client):
    fake = us_kiwoom()

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 200
    data = response.json()
    assert data["order_no"] == "000000285"
    assert data["original_order_no"] == "000000282"
    assert data["cancel_quantity"] == 5
    assert [a for a in api_ids(fake) if a != "usa10099"] == ["ust21050", "ust20003"]
    [request] = fake.calls("ust20003")
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/us/ordr"
    assert body_of(request) == {"orig_ord_no": "000000282", "stex_tp": "ND", "stk_cd": "NVDA"}


@pytest.mark.parametrize(("code", "stex_tp"), [("NVDA", "ND"), ("EWZ", "NY")])
def test_us_cancel_maps_the_exchange_to_kiwoom_codes(make_client, code, stex_tp):
    fake = us_kiwoom(us_open_order(stk_cd=code))

    make_client(fake).post(URL, json=cancel())

    assert body_of(fake.calls("ust20003")[0])["stex_tp"] == stex_tp


def test_us_real_cancels_are_refused_before_any_kiwoom_request(make_client):
    fake = us_kiwoom()

    response = make_client(fake).post("/api/environments/us_real/cancellations", json=cancel())

    assert response.status_code == 403
    assert fake.requests == []


@pytest.mark.parametrize("order_no", ["", "0000002820", "12a"])
def test_invalid_us_order_numbers_are_rejected_without_calling_kiwoom(make_client, order_no):
    fake = us_kiwoom()

    response = make_client(fake).post(URL, json=cancel(order_no=order_no))

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_request"
    assert fake.requests == []


@pytest.mark.parametrize(
    ("quantity", "kind"),
    [("4", "partial_cancel_unsupported"), ("6", "cancel_quantity_exceeded")],
)
def test_us_cancels_must_take_all_remaining(make_client, quantity, kind):
    fake = us_kiwoom()

    response = make_client(fake).post(URL, json=cancel(quantity=quantity))

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == kind
    assert fake.calls("ust20003") == []


@pytest.mark.parametrize(
    "overrides", [{"rsrv_tp": "예약"}, {"stk_cd": "OTCX"}], ids=["reserved", "unlisted"]
)
def test_us_orders_this_app_cannot_cancel_are_not_sent(make_client, overrides):
    fake = us_kiwoom(us_open_order(**overrides))

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "cancel_not_supported"
    assert fake.calls("ust20003") == []


def test_us_cancel_of_a_missing_order_is_not_sent(make_client):
    fake = us_kiwoom(us_open_order(ord_no="000000999"))

    response = make_client(fake).post(URL, json=cancel())

    assert response.json()["error"]["kind"] == "open_order_not_found"
    assert fake.calls("ust20003") == []


def test_us_failed_recheck_is_a_failure(make_client):
    fake = us_kiwoom().reply("ust21050", kiwoom_error(1, "조회 실패"))

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "open_orders_check_failed"
    assert fake.calls("ust20003") == []


def test_us_duplicate_key_is_cancelled_once(make_client):
    fake = us_kiwoom()
    client = make_client(fake)

    client.post(URL, json=cancel())
    second = client.post(URL, json=cancel())

    assert second.status_code == 409
    assert len(fake.calls("ust20003")) == 1


def _broken(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize("reply", ["broken", {}, {"ord_no": ""}])
def test_us_cancel_without_a_clear_result_is_unknown(make_client, reply):
    fake = us_kiwoom()
    if reply == "broken":
        fake.respond("ust20003", _broken)
    else:
        fake.reply("ust20003", reply)

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "order_result_unknown"
    assert len(fake.calls("ust20003")) == 1


@pytest.mark.parametrize("cncl_ord_qty", ["000000000000", ""])
def test_unconfirmed_us_cancel_quantity_is_null(make_client, cncl_ord_qty):
    fake = us_kiwoom().reply("ust20003", {**US_CANCEL_REPLY, "cncl_ord_qty": cncl_ord_qty})

    response = make_client(fake).post(URL, json=cancel())

    assert response.json()["cancel_quantity"] is None


def test_us_cancel_events_are_logged(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    make_client(us_kiwoom()).post(URL, json=cancel(order_key="us-key-9"))

    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["cancel_requested"]["order_key"] == "us-key-9"
    assert events["cancel_accepted"]["original_order_no"] == "000000282"
    assert events["cancel_accepted"]["order_no"] == "000000285"


def test_accepted_us_cancel_is_described_for_discord(make_client, monkeypatch):
    seen: list[Embed] = []
    original = app_module._cancel_embed

    def capture(*args: Any) -> Embed:
        seen.append(original(*args))
        return seen[-1]

    monkeypatch.setattr(app_module, "_cancel_embed", capture)

    make_client(us_kiwoom()).post(URL, json=cancel())

    fields = {f.name: f.value for f in seen[0].fields}
    assert fields["투자 환경"] == "미국 모의"
    assert fields["종목"] == "엔비디아 (NVDA)"
    assert fields["원래 주문"] == "매수"
    assert fields["취소 수량"] == "5주"
    assert fields["주문번호"] == "000000282 → 000000285"
