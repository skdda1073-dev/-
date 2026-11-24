"""Prepare the project's local Python environment and configuration template."""

import os
from pathlib import Path
import secrets
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]


def create_local_config():
    path = ROOT / ".env"
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    config = template.replace("FLASK_SECRET_KEY=\n", f"FLASK_SECRET_KEY={secrets.token_hex(32)}\n", 1)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("기존 .env를 사용합니다.")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as local:
        local.write(config)
    print(".env를 만들었습니다. 사용할 권한이 있는 API 키를 여기에 입력하세요.")


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Python 3.12 이상이 필요합니다. 팀 검증 버전은 Python 3.14.2입니다.")
    environment = ROOT / ".venv"
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python = environment / executable
    if not python.is_file():
        if environment.exists():
            raise SystemExit("기존 .venv의 Python을 찾을 수 없습니다. 해당 폴더를 확인한 뒤 다시 실행하세요.")
        venv.create(environment, with_pip=True)

    subprocess.run([python, "-m", "pip", "install", "-r", ROOT / "requirements.txt"], cwd=ROOT, check=True)
    create_local_config()
    print(f"실행 명령  .venv/{executable} app.py")
    print("기본 접속 주소는 http://localhost:5050 입니다.")


if __name__ == "__main__":
    main()
