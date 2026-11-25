"""프로젝트 .env와 실행 환경에서 서버 설정만 읽는다. 키를 출력하지 않는다."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


class ConfigurationError(RuntimeError):
    """설정 값 자체를 담지 않는 사용자용 설정 오류."""


@dataclass(frozen=True)
class Settings:
    tmap_app_key: str = field(default="", repr=False)
    public_data_key: str = field(default="", repr=False)
    tago_api_key: str = field(default="", repr=False)
    intercity_data_key: str = field(default="", repr=False)
    naver_map_client_id: str = field(default="", repr=False)
    debug: bool = False
    port: int = 5000
    flask_secret_key: str = field(default="", repr=False)
    ride_room_db_path: str = field(default="", repr=False)
    session_cookie_secure: bool = True
    taxi_cache_ttl_seconds: int = 60
    taxi_cache_max_entries: int = 512
    ride_room_message_rate_per_minute: int = 15
    ride_room_create_rate_per_hour: int = 3
    weather_data_key: str = field(default="", repr=False)
    airkorea_data_key: str = field(default="", repr=False)
    airkorea_station_name: str = "중앙로"
    motis_base_url: str = field(default="", repr=False)
    gbis_api_key: str = field(default="", repr=False)

    @property
    def bus_data_key(self):
        """시내버스 전용 키를 우선하고, 기존 환경은 공용 키로 호환한다."""
        return self.tago_api_key or self.public_data_key

    def configured(self):
        """존재 여부만 반환한다. 발급·권한·외부 연결 성공을 뜻하지 않는다."""
        return {
            "tmap_app_key": bool(self.tmap_app_key),
            "public_data_key": bool(self.public_data_key),
            "tago_api_key": bool(self.bus_data_key),
            "naver_map_client_id": bool(self.naver_map_client_id),
        }


def load_settings(base_dir, environ=None):
    """cwd와 무관하게 프로젝트 .env 사용. 명시적 환경변수가 우선한다.

    ${...} 확장을 끄고 키의 $, %, = 문자를 그대로 보존한다.
    os.environ은 변경하지 않으며 이미 있는 .env를 생성·수정하지 않는다.
    """
    path = Path(base_dir) / ".env"
    try:
        file_values = dotenv_values(path, encoding="utf-8-sig", interpolate=False) if path.is_file() else {}
    except (OSError, UnicodeError):
        raise ConfigurationError(".env 파일을 읽을 수 없습니다. 파일 접근 권한과 UTF-8 인코딩을 확인하세요.") from None
    environment = os.environ if environ is None else environ

    def value(name, default=""):
        raw = environment.get(name, file_values.get(name, default))
        return default if raw is None else str(raw).strip()

    def integer(name, default, lower, upper):
        try:
            parsed = int(value(name, str(default)))
            if not lower <= parsed <= upper:
                raise ValueError("range")
            return parsed
        except (ValueError, OverflowError):
            raise ConfigurationError(f"{name} 설정 범위를 확인하세요.") from None

    def boolean(name, default):
        text = value(name, "true" if default else "false").lower()
        if text not in {"true", "false", "1", "0", "yes", "no"}:
            raise ConfigurationError(f"{name}는 true 또는 false로 설정하세요.")
        return text in {"true", "1", "yes"}

    debug_text = value("APP_DEBUG", "false").lower()
    if debug_text not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigurationError("APP_DEBUG는 true 또는 false로 설정하세요.")
    try:
        port = int(value("PORT", "5000"))
        if not 1 <= port <= 65535:
            raise ValueError("port range")
    except (ValueError, OverflowError):
        raise ConfigurationError("PORT는 1~65535 사이의 정수로 설정하세요.") from None
    motis_base_url = value("MOTIS_BASE_URL").rstrip("/")
    if motis_base_url:
        try:
            url = urlsplit(motis_base_url)
            local_http = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not url.hostname or url.username is not None or url.password is not None
                    or url.query or url.fragment or url.path
                    or not (local_http or url.scheme == "https")
                    or (url.port is not None and not 1 <= url.port <= 65535)):
                raise ValueError("invalid MOTIS URL")
        except ValueError:
            raise ConfigurationError("MOTIS_BASE_URL에 로컬 HTTP 또는 HTTPS 서버 주소를 입력하세요.") from None
    return Settings(
        tmap_app_key=value("TMAP_APP_KEY"),
        public_data_key=value("PUBLIC_DATA_KEY"),
        tago_api_key=value("TAGO_API_KEY"),
        gbis_api_key=value("GBIS_API_KEY"),
        intercity_data_key=value("INTERCITY_DATA_KEY") or value("PUBLIC_DATA_KEY"),
        weather_data_key=value("WEATHER_DATA_KEY") or value("PUBLIC_DATA_KEY"),
        airkorea_data_key=value("AIRKOREA_DATA_KEY") or value("PUBLIC_DATA_KEY"),
        airkorea_station_name=value("AIRKOREA_STATION_NAME", "중앙로") or "중앙로",
        motis_base_url=motis_base_url,
        naver_map_client_id=value("NAVER_MAP_CLIENT_ID"),
        debug=debug_text in {"true", "1", "yes"}, port=port,
        flask_secret_key=value("FLASK_SECRET_KEY"),
        ride_room_db_path=value("RIDE_ROOM_DB_PATH", str(Path(base_dir) / "instance" / "ride_rooms.sqlite3")),
        session_cookie_secure=boolean("SESSION_COOKIE_SECURE", True),
        taxi_cache_ttl_seconds=integer("TAXI_CACHE_TTL_SECONDS", 60, 1, 300),
        taxi_cache_max_entries=integer("TAXI_CACHE_MAX_ENTRIES", 512, 1, 4096),
        ride_room_message_rate_per_minute=integer("RIDE_ROOM_MESSAGE_RATE_PER_MINUTE", 15, 1, 120),
        ride_room_create_rate_per_hour=integer("RIDE_ROOM_CREATE_RATE_PER_HOUR", 3, 1, 30),
    )
