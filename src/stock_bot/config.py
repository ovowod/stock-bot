"""투자 환경 정의와 환경변수 로딩."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv

from stock_bot.errors import AppError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
LOG_DIR = PROJECT_ROOT / "logs"

REAL_DOMAIN = "https://api.kiwoom.com"
PAPER_DOMAIN = "https://mockapi.kiwoom.com"
REAL_REALTIME_URL = "wss://api.kiwoom.com:10000/api/websocket"
PAPER_REALTIME_URL = "wss://mockapi.kiwoom.com:10000/api/websocket"


def load_env_file(path: Path = ENV_FILE) -> None:
    """.env가 있으면 읽는다. 이미 설정된 환경변수(Docker env_file, 쉘)가 우선한다."""
    if path.is_file():
        load_dotenv(path, override=False)


class Market(StrEnum):
    DOMESTIC = "domestic"
    US = "us"


class Environment(StrEnum):
    DOMESTIC_REAL = "domestic_real"
    US_REAL = "us_real"
    DOMESTIC_PAPER = "domestic_paper"
    US_PAPER = "us_paper"


@dataclass(frozen=True)
class EnvironmentSpec:
    environment: Environment
    label: str
    market: Market
    is_real: bool
    credential_prefix: str
    domain: str

    @property
    def realtime_url(self) -> str:
        """실시간(WebSocket) 주소. REST 도메인과 같이 실전/모의로만 갈린다."""
        return REAL_REALTIME_URL if self.is_real else PAPER_REALTIME_URL


ENVIRONMENTS: dict[Environment, EnvironmentSpec] = {
    spec.environment: spec
    for spec in (
        EnvironmentSpec(
            Environment.DOMESTIC_REAL, "국내 실전", Market.DOMESTIC, True, "REAL", REAL_DOMAIN
        ),
        EnvironmentSpec(Environment.US_REAL, "미국 실전", Market.US, True, "REAL", REAL_DOMAIN),
        EnvironmentSpec(
            Environment.DOMESTIC_PAPER,
            "국내 모의",
            Market.DOMESTIC,
            False,
            "PAPER_KR",
            PAPER_DOMAIN,
        ),
        EnvironmentSpec(
            Environment.US_PAPER, "미국 모의", Market.US, False, "PAPER_US", PAPER_DOMAIN
        ),
    )
}


def parse_environment(value: str) -> EnvironmentSpec:
    """정해진 4개 외의 값은 거부한다. 기본값으로 대신 처리하지 않는다."""
    try:
        return ENVIRONMENTS[Environment(value)]
    except ValueError:
        raise AppError(
            "unknown_environment", "알 수 없는 투자 환경입니다.", 404, {"environment": value}
        ) from None


@dataclass(frozen=True)
class Credentials:
    app_key: str
    app_secret: str
    account_no: str

    def secrets(self) -> tuple[str, ...]:
        return (self.app_key, self.app_secret, self.account_no)


def load_credentials(spec: EnvironmentSpec, environ: Mapping[str, str]) -> Credentials:
    names = [
        f"{spec.credential_prefix}_{suffix}" for suffix in ("APP_KEY", "APP_SECRET", "ACCOUNT_NO")
    ]
    missing = [name for name in names if not environ.get(name, "").strip()]
    if missing:
        raise AppError(
            "config_error",
            f"환경변수가 설정되지 않았습니다: {', '.join(missing)}",
            503,
            {"missing": missing},
        )
    key, secret, account = (environ[name].strip() for name in names)
    return Credentials(key, secret, account)
