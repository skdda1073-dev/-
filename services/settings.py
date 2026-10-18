"""프로젝트 .env와 실행 환경에서 서버 설정만 읽는다. 키를 출력하지 않는다."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values


class ConfigurationError(RuntimeError):
    """설정 값 자체를 담지 않는 사용자용 설정 오류."""


@dataclass(frozen=True)
class Settings:
    tmap_app_key: str = field(default="", repr=False)
    public_data_key: str = field(default="", repr=False)
    naver_map_client_id: str = field(default="", repr=False)
    debug: bool = False
    port: int = 5000

    def configured(self):
        """존재 여부만 반환한다. 발급·권한·외부 연결 성공을 뜻하지 않는다."""
        return {
            "tmap_app_key": bool(self.tmap_app_key),
            "public_data_key": bool(self.public_data_key),
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

    debug_text = value("APP_DEBUG", "false").lower()
    if debug_text not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigurationError("APP_DEBUG는 true 또는 false로 설정하세요.")
    try:
        port = int(value("PORT", "5000"))
        if not 1 <= port <= 65535:
            raise ValueError("port range")
    except (ValueError, OverflowError):
        raise ConfigurationError("PORT는 1~65535 사이의 정수로 설정하세요.") from None
    return Settings(
        tmap_app_key=value("TMAP_APP_KEY"),
        public_data_key=value("PUBLIC_DATA_KEY"),
        naver_map_client_id=value("NAVER_MAP_CLIENT_ID"),
        debug=debug_text in {"true", "1", "yes"}, port=port,
    )
