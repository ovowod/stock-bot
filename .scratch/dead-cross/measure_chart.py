"""모의투자 차트 실측: Dead Cross 현황 보기의 조회 방식을 정하려고 한 번 돌리는 스크립트.

키움 명세에 없는 것을 잰다.
- 한 번 조회에 봉이 몇 개 오는지
- 이어서 받기(cont-yn)로 얼마나 과거까지 받을 수 있는지
- 맨 앞(가장 최근) 봉이 아직 만들어지는 중인 봉인지(실행 시각과 비교)

모의투자 환경(국내 모의·미국 모의)만 부른다. 차트 조회만 하며 주문 TR은 부르지 않는다.
결과는 이 파일 옆 measure-<시장>-<시각>.txt에 남긴다. 인증정보와 토큰은 남기지 않는다.

실행(프로젝트 루트에서):
    uv run python .scratch/dead-cross/measure_chart.py domestic
    uv run python .scratch/dead-cross/measure_chart.py us
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_bot.core.config import ENVIRONMENTS, Environment, load_env_file
from stock_bot.kiwoom.kiwoom import KiwoomClient

MAX_PAGES = 40
OUT_DIR = Path(__file__).parent
KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")

DOMESTIC_PATH = "/api/dostk/chart"
US_PATH = "/api/us/chart"


def cases(market: str) -> tuple[Environment, list[tuple[str, str, str, dict[str, str], str, str]]]:
    """(환경, [(이름, apiId, path, body, 목록 필드, 시각 필드)])."""
    today_kst = datetime.now(KST).strftime("%Y%m%d")
    today_ny = datetime.now(NEW_YORK).strftime("%Y%m%d")
    if market == "domestic":
        minute = {"stk_cd": "005930", "upd_stkpc_tp": "1", "base_dt": today_kst}
        return Environment.DOMESTIC_PAPER, [
            (
                "국내 1분봉",
                "ka10080",
                DOMESTIC_PATH,
                {**minute, "tic_scope": "1"},
                "stk_min_pole_chart_qry",
                "cntr_tm",
            ),
            (
                "국내 60분봉",
                "ka10080",
                DOMESTIC_PATH,
                {**minute, "tic_scope": "60"},
                "stk_min_pole_chart_qry",
                "cntr_tm",
            ),
            (
                "국내 일봉",
                "ka10081",
                DOMESTIC_PATH,
                {"stk_cd": "005930", "base_dt": today_kst, "upd_stkpc_tp": "1"},
                "stk_dt_pole_chart_qry",
                "dt",
            ),
        ]
    if market == "us":
        common = {
            "stex_tp": "ND",
            "stk_cd": "AAPL",
            "strt_dt": today_ny,
            "upd_stkpc_tp": "1",
            "exrt_appl_tp": "0",
        }
        return Environment.US_PAPER, [
            (
                "미국 1분봉",
                "usa06011",
                US_PATH,
                {**common, "tic_scope": "1"},
                "result_list",
                "cntr_tm",
            ),
            (
                "미국 120분봉",
                "usa06011",
                US_PATH,
                {**common, "tic_scope": "120"},
                "result_list",
                "cntr_tm",
            ),
            ("미국 일봉", "usa06012", US_PATH, common, "result_list", "dt"),
        ]
    raise SystemExit("사용법: measure_chart.py domestic|us")


async def measure(market: str) -> list[str]:
    environment, items = cases(market)
    spec = ENVIRONMENTS[environment]
    # 모의투자만 부른다. 실전 환경이면 아무것도 보내지 않고 멈춘다.
    if spec.is_real:
        raise SystemExit("실전 환경은 재지 않습니다.")
    load_env_file()
    client = KiwoomClient(os.environ)
    lines = [
        f"환경: {spec.environment.value}",
        f"실행 시각: {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST",
        f"뉴욕 시각: {datetime.now(NEW_YORK):%Y-%m-%d %H:%M:%S} ET",
    ]
    for name, api_id, path, body, list_key, time_key in items:
        lines.append("")
        lines.append(f"## {name} ({api_id}) 요청: {body}")
        cont_yn, next_key, total = "N", "", 0
        for page_no in range(1, MAX_PAGES + 1):
            try:
                page = await client._call_page(spec, api_id, path, body, cont_yn, next_key, page_no)
            except Exception as exc:  # 실측이 목적이라 오류도 결과로 남긴다.
                lines.append(f"{page_no}페이지 오류: {type(exc).__name__}: {exc}")
                break
            rows = page.data.get(list_key)
            if not isinstance(rows, list):
                lines.append(f"{page_no}페이지: {list_key}가 목록이 아님. 키: {sorted(page.data)}")
                break
            total += len(rows)
            if page_no == 1 and rows:
                lines.append(f"행 필드: {sorted(rows[0])}")
                lines.append(f"맨 앞 3행: {rows[:3]}")
            first = rows[0].get(time_key) if rows else None
            last = rows[-1].get(time_key) if rows else None
            lines.append(
                f"{page_no}페이지: {len(rows)}개, {time_key} {first} ~ {last}, "
                f"cont-yn={page.cont_yn}"
            )
            if page.cont_yn != "Y":
                break
            cont_yn, next_key = "Y", page.next_key
        else:
            lines.append(f"{MAX_PAGES}페이지에서 멈춤(더 있음)")
        lines.append(f"합계: {total}개")
    return lines


def main() -> None:
    market = sys.argv[1] if len(sys.argv) > 1 else ""
    lines = asyncio.run(measure(market))
    out = OUT_DIR / f"measure-{market}-{datetime.now(KST):%Y%m%d-%H%M}.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
