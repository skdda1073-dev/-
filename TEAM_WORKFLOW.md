# hallym_capstone 팀 작업·GitHub 사용 기준서

이 문서는 팀원이 **자기 역할 브랜치에만** 코드를 올리고, `main`을 실수로 건드리지 않도록 만든 쉬운 작업 안내서입니다.

> 가장 중요한 규칙: 작업 중에는 `main`으로 이동하거나 `main`에 push하지 않습니다. 각자 이름 브랜치에서만 작업하고 push합니다.

---

## 1. 우리 팀의 역할과 브랜치

| 담당자 | 역할 | GitHub 브랜치 | 주로 만지는 파일 |
| --- | --- | --- | --- |
| 고경완 | UI/UX | `고경완` | `templates/index.html`, `static/css/**`, 화면 관련 `static/js/**` |
| 권오준 | 데이터 | `권오준` | `data_preprocessing.ipynb`, 학교·건물 좌표 CSV 파일 |
| 이준엽 | 백엔드 | `이준엽` | `app.py`, `services/**`, `requirements.txt`, `static/js/api-service.js` |

### 역할별 파일 범위

#### 고경완 — UI/UX

- `templates/index.html`: 화면의 HTML 뼈대
- `static/css/main.css`: 공통 화면 스타일
- `static/css/result-card.css`: 결과 카드 스타일
- `static/css/search.css`: 검색창·자동완성 스타일
- `static/css/timeline.css`: 시간표·타임라인 스타일
- `static/js/ui-renderer.js`: 화면에 결과를 그리는 코드
- `static/js/search-controller.js`: 목적지 검색·자동완성 동작
- `static/js/gps-controller.js`: GPS 화면 흐름
- `static/js/map-controller.js`: 지도 화면·마커 표시

#### 권오준 — 데이터

- `data_preprocessing.ipynb`: 데이터 전처리
- `강원대학교 건물별 좌표.csv`
- `한림대학교 건물별 좌표.csv`
- `한림대_강원대_캠퍼스맵_통합_좌표.csv`

CSV의 **열 이름, 좌표 형식, 파일명**을 바꿀 때에는 이준엽에게 먼저 알립니다. 백엔드가 그 형식을 읽고 있을 수 있기 때문입니다.

#### 이준엽 — 백엔드

- `app.py`: Flask 서버와 API 경로
- `services/**`: 외부 API 호출·시간 계산 등 서버 기능 모듈
- `requirements.txt`: Python 패키지 목록
- `static/js/api-service.js`: 화면과 Flask API 사이의 요청·응답 연결

API 응답 JSON의 키 이름이나 형식을 바꿀 때에는 고경완에게 먼저 알립니다. 화면 코드가 그 값을 사용하고 있을 수 있기 때문입니다.

### 모두의 합의가 필요한 파일

- `static/js/main.js`: 각 JavaScript 모듈을 연결하고 처음 실행하는 파일
- `AGENTS.md`: Codex 작업 규칙
- `README.md`, [개발회고록](docs/개발회고록.md), `TEAM_WORKFLOW.md`: 공용 문서

이 파일들은 한 사람이 임의로 수정하지 않습니다. 팀 채팅에서 “무엇을 바꾸는지 / 왜 바꾸는지 / 영향 파일”을 먼저 공유하고 합의한 뒤 작업합니다.

---

## 2. `main`은 누가 어떻게 쓰나요?

`main`은 제출용·통합용 브랜치입니다.

- 각 팀원: 자기 이름 브랜치에만 push합니다.
- 통합 담당자: GitHub Pull Request(PR)를 검토한 뒤에만 `main`에 병합합니다.
- **누구도 터미널에서 `git push origin main`을 입력하지 않습니다.**
- 병합은 GitHub 웹사이트의 PR 화면에서 진행합니다. 가능하면 `Create a merge commit`을 선택해 각자의 커밋 이력을 남깁니다.

이 방식이면 누가 어떤 작업을 했는지 커밋과 PR로 남고, 교수님이 활동 내역을 확인하기에도 좋습니다. GitHub의 Contributors 목록에는 일반적으로 기본 브랜치(`main`)에 최종 병합된 커밋이 반영되므로, 각자 커밋의 이메일을 자기 GitHub 계정에 연결하고 PR도 남겨야 합니다.

---

## 3. 시작 전에 꼭 하는 두 줄

VS Code의 **일반 Terminal(zsh)** 에서 아래 두 줄을 각각 입력합니다.

```bash
git branch --show-current
git status --short
```

확인 방법:

- 첫 줄 결과가 내 이름(`고경완`, `권오준`, `이준엽`)이면 정상입니다.
- 첫 줄 결과가 `main`이면 **아무 작업도 하지 말고 멈춥니다.**
- 두 번째 명령에서 내가 만들지 않은 변경 파일이 보이면, 수정하거나 커밋하지 말고 팀에 먼저 물어봅니다.

> Codex의 입력창에 `Ask Codex to do anything`이라고 보인다면, 직접 명령 대신 “`git branch --show-current`과 `git status --short`만 실행해서 결과를 보여줘. 수정·커밋·push는 하지 마.”라고 요청해도 됩니다.

---

## 4. 고경완(UI/UX)의 매 작업 순서

### 1) 내 브랜치로 이동하고 최신 상태 받기

```bash
git switch 고경완
git pull origin 고경완
```

처음에 `git switch 고경완`이 실패하면 아래 두 줄만 대신 실행합니다.

```bash
git fetch origin
git switch --track origin/고경완
```

### 2) UI/UX 파일만 수정하기

예: CSS, `templates/index.html`, `ui-renderer.js`, `search-controller.js` 등 자신의 담당 파일만 수정합니다.

### 3) 수정 내용을 확인하기

```bash
git status --short
git diff --check
```

`git diff --check` 결과가 아무것도 안 나오면 공백 오류가 없다는 뜻입니다.

### 4) 수정한 파일만 골라서 커밋하기

아래는 예시입니다. 실제로 수정한 파일만 적습니다.

```bash
git add templates/index.html static/css/search.css static/js/search-controller.js
git commit -m "feat(ui): 목적지 검색 화면 개선"
```

### 5) 내 이름 브랜치로 보내기

처음 push할 때:

```bash
git push -u origin 고경완
```

그 다음부터는:

```bash
git push
```

---

## 5. 권오준(데이터)의 매 작업 순서

### 1) 내 브랜치로 이동하고 최신 상태 받기

```bash
git switch 권오준
git pull origin 권오준
```

처음에 브랜치가 없다는 오류가 나면:

```bash
git fetch origin
git switch --track origin/권오준
```

### 2) 데이터 파일만 수정하기

예: CSV 좌표 수정, 전처리 노트북 수정, 데이터 검증 결과 반영입니다.

### 3) 확인 → 커밋 → push

아래 예시에서 실제로 바꾼 파일만 `git add` 뒤에 적습니다.

```bash
git status --short
git diff --check
git add "한림대학교 건물별 좌표.csv" "한림대_강원대_캠퍼스맵_통합_좌표.csv"
git commit -m "data: 한림대 건물 좌표 보정"
git push -u origin 권오준
```

이미 한 번 push한 브랜치라면 마지막 줄은 `git push`만 입력해도 됩니다.

---

## 6. 이준엽(백엔드)의 매 작업 순서

### 1) 내 브랜치로 이동하고 최신 상태 받기

```bash
git switch 이준엽
git pull origin 이준엽
```

처음에 브랜치가 없다는 오류가 나면:

```bash
git fetch origin
git switch --track origin/이준엽
```

### 2) 백엔드 파일만 수정하기

예: `app.py`, `services/**`, `requirements.txt`, `static/js/api-service.js`입니다.

### 3) 확인 → 커밋 → push

```bash
git status --short
git diff --check
git add app.py static/js/api-service.js
git commit -m "fix(api): 출발지 오류 응답 처리"
git push -u origin 이준엽
```

이미 한 번 push한 브랜치라면 마지막 줄은 `git push`만 입력해도 됩니다.

---

## 7. 지금 완료된 모듈화 작업은 어떻게 올리나요?

현재 완료된 프런트엔드 모듈 분리·API 오류 처리 커밋은 역할 브랜치가 아니라 별도 작업 브랜치인 `refactor/module-split`에 있습니다. 이 커밋은 UI와 API 연결 파일이 함께 들어 있어 특정 한 사람의 이름 브랜치로 억지로 옮기지 않습니다.

이 작업을 GitHub에 먼저 안전하게 올리는 명령은 아래 한 줄입니다.

```bash
git push -u origin refactor/module-split
```

이 명령은 **현재 로컬의 `refactor/module-split` 커밋만** GitHub에 같은 이름의 새 브랜치로 올립니다. `main`에는 아무 커밋도 추가하지 않습니다.

그 뒤 GitHub에서 다음처럼 PR을 만듭니다.

1. GitHub 저장소를 엽니다.
2. `Compare & pull request`를 누릅니다.
3. `base`가 `main`, `compare`가 `refactor/module-split`인지 확인합니다.
4. 변경 파일과 화면 동작을 팀이 확인합니다.
5. 통합 담당자만 병합합니다.

> push는 **이미 커밋된 내용만** GitHub으로 보냅니다. 컴퓨터에 남아 있는 미커밋 파일이나 stage만 된 파일은 push에 포함되지 않습니다.

---

## 8. 절대 입력하지 말 것

아래 명령은 실수로 다른 사람의 코드나 `main`에 영향을 줄 수 있으므로 사용하지 않습니다.

```bash
git switch main
git push origin main
git push --force
git add .
git add -A
```

특히 `git add .`와 `git add -A`는 `.DS_Store`, `.env`, 다른 팀원의 파일까지 한꺼번에 커밋할 수 있습니다. 항상 `git add 파일경로` 방식으로 필요한 파일만 선택합니다.

---

## 9. 커밋 메시지 예시

커밋 메시지는 “무슨 역할이 무엇을 바꿨는지” 보이게 작성합니다.

```text
feat(ui): 목적지 검색 UI 개선
fix(ui): GPS 권한 거부 안내 표시
data: 강원대 건물 좌표 보정
data: 캠퍼스 좌표 CSV 누락값 수정
feat(api): 버스 도착 정보 API 추가
fix(api): 출발지 오류 응답 처리
docs: 팀 작업 기준서 추가
```

---

## 10. Pull Request(PR) 작성 방법

내 이름 브랜치에 push한 뒤 GitHub에서 PR을 만듭니다.

1. GitHub 저장소에서 `Pull requests`를 누릅니다.
2. `New pull request`를 누릅니다.
3. `base`는 항상 `main`으로 둡니다.
4. `compare`에는 내 이름 브랜치(`고경완`, `권오준`, `이준엽`)를 선택합니다.
5. 아래 양식을 채워 `Create pull request`를 누릅니다.
6. 팀 검토 뒤 통합 담당자만 병합합니다.

### PR 본문 복사 양식

```md
## 담당자 / 역할
고경완 / UI·UX

## 변경 목적
무엇을 왜 수정했는지 한두 줄로 작성

## 변경 파일
- `파일 경로`
- `파일 경로`

## 다른 역할에 미치는 영향
- 없음 / API 응답 확인 필요 / CSV 열 이름 확인 필요 중 작성

## 확인한 내용
- [ ] `git diff --check` 통과
- [ ] 화면 또는 API를 직접 확인함
- [ ] 수정하지 않은 기존 기능이 정상 동작함

## 이번에 건드리지 않은 범위
- 수정하지 않은 기능 또는 파일 작성
```

권오준과 이준엽은 첫 줄의 이름·역할만 자기 것으로 바꿔 사용합니다.

---

## 11. 기여 내역이 남게 하는 방법

각 팀원은 자신의 GitHub 계정 이메일로 커밋해야 합니다. 먼저 아래 명령으로 현재 설정을 확인합니다.

```bash
git config user.name
git config user.email
```

- 출력된 이메일이 본인 GitHub 계정에 등록·인증된 이메일이면 정상입니다.
- 다른 사람 이메일이거나 확신이 없으면 커밋하기 전에 팀장에게 알려서 설정을 확인합니다.
- 이름 브랜치 push, PR 생성, `main` 병합까지 남겨야 작업 이력과 최종 제출 이력이 모두 보기 좋게 남습니다.

---

## 12. 문제가 생기면 이렇게 합니다

| 화면 또는 메시지 | 할 일 |
| --- | --- |
| `main`이라고 표시됨 | 아무것도 수정하지 말고 멈춘 뒤, 내 이름 브랜치로 이동할지 팀에 확인 |
| `git status --short`에 낯선 파일이 보임 | 그 파일은 add·commit하지 말고 팀원에게 확인 |
| `git pull` 중 충돌(conflict)이 남 | 임의로 선택하지 말고 충돌 화면을 캡처해 담당자에게 공유 |
| push가 거절됨 | 오류 메시지를 그대로 공유하고, `git push --force`는 절대 사용하지 않음 |
| API·CSV 형식을 바꿔야 함 | UI/UX·데이터·백엔드 담당자에게 먼저 영향 범위를 알림 |

---

## 13. 보안과 불필요 파일

- API 키, 비밀번호, 토큰, 개인 경로는 커밋하지 않습니다.
- `.env`, `.DS_Store`는 GitHub에 올리지 않습니다.
- 다른 사람이 수정한 파일을 되돌리거나 삭제하지 않습니다.
- 작업 전에는 “무엇을 / 왜 / 어느 파일을 / 어떻게 확인할지” 먼저 팀에 설명합니다.

---

## 참고 문서

- [GitHub: 원격 저장소로 커밋 push하기](https://docs.github.com/en/get-started/using-git/pushing-commits-to-a-remote-repository)
- [GitHub: Pull Request 빠른 시작](https://docs.github.com/en/pull-requests/get-started/pull-request-quickstart)
- [GitHub: Contributors 목록 보기](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors)
