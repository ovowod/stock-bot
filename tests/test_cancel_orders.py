import asyncio
import logging
import threading

import httpx
import pytest

from stock_bot import app as app_module
from stock_bot.app import create_app
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, ThreadedTransport, body_of, kiwoom_error
from tests.test_open_orders import open_order

URL = "/api/environments/domestic_paper/cancellations"
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


def kiwoom(*orders: dict[str, object]) -> FakeKiwoom:
    return (
        FakeKiwoom()
        .reply("ka10075", {"oso": list(orders) or [LIMIT_SELL]})
        .reply("kt10003", CANCEL_REPLY)
    )


def api_ids(fake: FakeKiwoom) -> list[str]:
    return [r.headers["api-id"] for r in fake.requests if "api-id" in r.headers]


def test_cancelling_all_remaining_rechecks_then_sends_kt10003_with_zero(make_client):
    fake = kiwoom()
    client = make_client(fake)

    response = client.post(URL, json=cancel())

    assert response.status_code == 200
    data = response.json()
    assert data["order_key"] == "cancel-1"
    assert data["order_no"] == "0000141"
    assert data["original_order_no"] == "0000070"
    assert data["cancel_quantity"] == 3
    assert data["accepted_at"]
    assert api_ids(fake) == ["ka10075", "kt10003"]
    [request] = fake.calls("kt10003")
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/dostk/ordr"
    assert body_of(request) == {
        "dmst_stex_tp": "KRX",
        "orig_ord_no": "0000070",
        "stk_cd": "000660",
        "cncl_qty": "0",
    }


def test_real_environment_cancels_are_refused_before_any_kiwoom_request(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = kiwoom()

    response = make_client(fake).post(
        "/api/environments/domestic_real/cancellations", json=cancel()
    )

    assert response.status_code == 403
    assert response.json()["error"]["kind"] == "order_not_allowed"
    assert fake.requests == []
    assert any(r.getMessage() == "cancel_blocked_real" for r in caplog.records)


@pytest.mark.parametrize(
    "body",
    [
        None,
        cancel(order_key=""),
        cancel(order_key="k" * 65),
        cancel(order_no=""),
        cancel(order_no="00000700"),
        cancel(order_no="12a"),
        cancel(quantity="0"),
        cancel(quantity="abc"),
        cancel(quantity="1" * 13),
        cancel(quantity=3),
    ],
)
def test_invalid_cancels_are_rejected_without_calling_kiwoom(make_client, body):
    fake = kiwoom()

    response = make_client(fake).post(URL, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_request"
    assert fake.requests == []


def test_the_same_key_is_cancelled_only_once(make_client):
    fake = kiwoom()
    client = make_client(fake)

    first = client.post(URL, json=cancel())
    second = client.post(URL, json=cancel())

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["kind"] == "duplicate_order"
    assert len(fake.calls("kt10003")) == 1


def test_cancel_and_order_keys_share_one_store(make_client):
    fake = kiwoom()
    client = make_client(fake)
    client.post(URL, json=cancel(order_key="shared"))

    order = {
        "order_key": "shared",
        "side": "buy",
        "code": "005930",
        "order_type": "market",
        "quantity": "1",
    }
    response = client.post("/api/environments/domestic_paper/orders", json=order)

    assert response.status_code == 409


async def _post_concurrently(fake: FakeKiwoom, bodies: list[dict[str, object]], gate_api_id: str):
    gate = threading.Event()

    def slow(request: httpx.Request) -> None:
        if request.headers.get("api-id") == gate_api_id:
            gate.wait(timeout=1)

    fake.on_request = slow
    app = create_app(
        environ=FAKE_ENV, transport=ThreadedTransport(fake), static_dir=None, log_dir=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        pending = [asyncio.create_task(client.post(URL, json=body)) for body in bodies]
        await asyncio.sleep(0.05)
        gate.set()
        return await asyncio.gather(*pending)


@pytest.mark.anyio
async def test_concurrent_cancels_with_the_same_key_send_one_cancel():
    fake = kiwoom()

    responses = await _post_concurrently(fake, [cancel()] * 3, "kt10003")

    assert sorted(r.status_code for r in responses) == [200, 409, 409]
    assert len(fake.calls("kt10003")) == 1


@pytest.mark.anyio
async def test_cancels_of_one_order_recheck_one_at_a_time():
    fake = kiwoom()

    responses = await _post_concurrently(
        fake, [cancel(order_key="a"), cancel(order_key="b")], "kt10003"
    )

    assert [r.status_code for r in responses] == [200, 200]
    # 뒤 요청의 재확인은 앞 요청의 취소 응답 뒤에 나간다.
    assert api_ids(fake) == ["ka10075", "kt10003", "ka10075", "kt10003"]


def test_a_missing_open_order_is_not_cancelled(make_client):
    fake = kiwoom(open_order(ord_no="0000099"))

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "open_order_not_found"
    assert fake.calls("kt10003") == []


@pytest.mark.parametrize(
    "overrides",
    [{"io_tp_nm": "+매수신용"}, {"stex_tp": "2", "stex_tp_txt": "NXT"}, {"sor_yn": "Y"}],
)
def test_orders_this_app_cannot_cancel_are_not_sent(make_client, overrides):
    fake = kiwoom(open_order(ord_no="0000070", oso_qty="3", **overrides))

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "cancel_not_supported"
    assert fake.calls("kt10003") == []


def test_a_failed_recheck_is_a_failure_not_an_unknown_result(make_client):
    fake = (
        FakeKiwoom().reply("ka10075", kiwoom_error(1, "조회 실패")).reply("kt10003", CANCEL_REPLY)
    )

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "open_orders_check_failed"
    assert fake.calls("kt10003") == []


def test_cancel_connection_failure_is_an_unknown_result_without_resending(make_client):
    fake = kiwoom()

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    fake.respond("kt10003", broken)
    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "order_result_unknown"
    assert "다시 시도하세요" in response.json()["error"]["message"]
    assert len(fake.calls("kt10003")) == 1


@pytest.mark.parametrize(
    "reply", [{}, {"ord_no": ""}, httpx.Response(200, text="<html>bad</html>")]
)
def test_malformed_cancel_responses_are_unknown_results(make_client, reply):
    fake = kiwoom().reply("kt10003", reply)

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "order_result_unknown"
    assert len(fake.calls("kt10003")) == 1


def test_kiwoom_rejecting_a_cancel_is_returned_as_is(make_client):
    fake = kiwoom().reply("kt10003", kiwoom_error(1, "[2000:취소 가능 수량이 없습니다]"))

    response = make_client(fake).post(URL, json=cancel())

    assert response.json()["error"]["kind"] not in {"order_result_unknown", "duplicate_order"}
    assert "취소 가능 수량이 없습니다" in response.json()["error"]["message"]
    assert len(fake.calls("kt10003")) == 1


def test_zero_cancel_quantity_from_kiwoom_is_returned_as_unknown_quantity(make_client):
    fake = kiwoom().reply("kt10003", {**CANCEL_REPLY, "cncl_qty": "000000000000"})

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 200
    assert response.json()["cancel_quantity"] is None


def test_cancel_events_are_logged_with_the_order_key(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    make_client(kiwoom()).post(URL, json=cancel(order_key="key-88"))

    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["cancel_requested"]["order_key"] == "key-88"
    assert events["cancel_requested"]["order_no"] == "0000070"
    assert events["open_order_checked"]["remaining_quantity"] == 3
    assert events["cancel_accepted"]["order_no"] == "0000141"
    assert events["cancel_accepted"]["original_order_no"] == "0000070"


def test_cancelling_part_of_the_remaining_sends_that_quantity(make_client):
    fake = kiwoom()

    response = make_client(fake).post(URL, json=cancel(quantity="2"))

    assert response.status_code == 200
    assert body_of(fake.calls("kt10003")[0])["cncl_qty"] == "2"


def test_cancelling_more_than_the_remaining_is_refused(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = kiwoom()

    response = make_client(fake).post(URL, json=cancel(quantity="4"))

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["kind"] == "cancel_quantity_exceeded"
    assert "3주" in error["message"]
    assert fake.calls("kt10003") == []
    blocked = [r for r in caplog.records if r.getMessage() == "cancel_blocked"]
    assert blocked[0].fields["remaining_quantity"] == 3  # type: ignore[attr-defined]


@pytest.mark.parametrize("cncl_qty", ["000000000000", "", None])
def test_partial_cancel_without_a_confirmed_quantity_reports_the_requested_one(
    make_client, cncl_qty
):
    reply = {**CANCEL_REPLY, "cncl_qty": cncl_qty}
    fake = kiwoom().reply("kt10003", reply)

    response = make_client(fake).post(URL, json=cancel(quantity="2"))

    assert response.json()["cancel_quantity"] == 2


def test_a_failing_cancel_listener_does_not_turn_an_accepted_cancel_into_a_failure(
    make_client, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="stock_bot")

    def broken(*args: object) -> None:
        raise KeyError("name")

    monkeypatch.setattr(app_module, "_cancel_embed", broken)
    fake = kiwoom()

    response = make_client(fake).post(URL, json=cancel())

    assert response.status_code == 200
    assert response.json()["order_no"] == "0000141"
    assert any(r.getMessage() == "cancel_listener_failed" for r in caplog.records)
