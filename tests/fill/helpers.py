"""체결 알림 테스트가 함께 쓰는 키움 응답."""

import httpx

from tests.fake_kiwoom import FakeKiwoom


def fill_kiwoom(*open_order_pages: dict | httpx.Response) -> FakeKiwoom:
    """체결 알림이 부르는 조회 TR의 응답. 기본은 미체결 주문과 당일 실현손익이 없다."""
    return (
        FakeKiwoom()
        .reply("ka10075", *(open_order_pages or ({"oso": []},)))
        .reply("ka10077", {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": []})
        .reply("ust21050", {"result_list": []})
        .reply("usa10099", {"list": []})
    )
