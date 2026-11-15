# MUSIC LOUNGE

장르별 라운지에서 YouTube 플레이리스트를 공유하고 실시간으로 대화하는 웹 앱입니다. Flask와 SQLite를 사용하며, YouTube 메타데이터 추출과 MP3 변환은 별도의 yt-dlp API 서버가 담당합니다.

- 작업 저장소: [leetaegyeong126/MUSIC-LOUNGE](https://github.com/leetaegyeong126/MUSIC-LOUNGE)
- 원본 프로젝트: [2026-Open-Source-Software/MUSIC-LOUNGE](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE)

이 문서는 `develop`에 반영된 코드와 설정을 기준으로 합니다. 원본 프로젝트 PR의 반영 범위는 아래 [PR 반영 기준](#pr-반영-기준)에 정리했습니다.

## 주요 기능

- **계정과 보관함**: 로그인·신규 계정 생성, 프로필 수정, 플레이리스트 생성·수정·삭제, 여러 라운지에 공유
- **장르별 라운지**: 기본 발라드·R&B·힙합·인디 라운지, DB의 라운지 정보 조회, 공유 목록과 좋아요
- **실시간 소통**: SSE 기반 채팅·참여자·공유 목록 변경 알림, 계정별 참여 인원 및 선택한 플레이리스트의 청취 인원 표시
- **YouTube 가져오기·재생**: 공개 영상·재생목록의 곡 정보 가져오기, MP3 준비·캐시·재생

프론트엔드는 Jinja 템플릿, 일반 JavaScript ES modules, CSS로 구성합니다. 별도의 Node.js 빌드 과정은 없습니다.

## 빠른 실행: Docker Compose

Docker와 Docker Compose를 사용할 수 있는 환경에서 실행합니다.

```sh
git clone https://github.com/leetaegyeong126/MUSIC-LOUNGE.git
cd MUSIC-LOUNGE
docker compose up --build -d
```

| 화면·서비스 | 주소 |
| --- | --- |
| 메인 | http://localhost:8000/ |
| 로그인·회원가입 | http://localhost:8000/login |
| 마이페이지 | http://localhost:8000/mypage |
| yt-dlp 상태 확인 | http://localhost:5001/health |

```sh
docker compose ps
docker compose logs -f web ytdlp-server
docker compose down
```

`docker compose down` 후에도 DB 볼륨은 유지됩니다. `docker compose down -v`는 계정·플레이리스트·채팅과 MP3 캐시가 담긴 볼륨을 삭제하므로 데이터 초기화가 필요한 경우에만 사용하세요.

### 컨테이너 구조

```mermaid
flowchart LR
    Browser[브라우저] -->|localhost:8000| Web[web · Flask :5000]
    Web -->|HTTP · ytdlp-server:5001| Media[ytdlp-server · yt-dlp / FFmpeg]
    Media --> YouTube[YouTube]
    Web -->|SQLite 파일 읽기·쓰기| Volume[(db_data 볼륨)]
    DB[db · Alpine 보조 컨테이너] ---|/data 마운트| Volume
```

| Compose 서비스 | 컨테이너명 | 역할 | 호스트 포트 | 마운트 |
| --- | --- | --- | --- | --- |
| `web` | `music-lounge-app` | 화면, 인증, REST API, SSE, SQLite 접근, MP3 캐시 | `8000 → 5000` | 프로젝트 `.:/app`, `db_data:/app/instance` |
| `ytdlp-server` | `music-lounge-ytdlp` | 메타데이터 추출, YouTube 검색, 오디오 다운로드·MP3 변환 | `5001 → 5001` | 별도 영속 볼륨 없음 |
| `db` | `music-lounge-db` | `db_data` 볼륨을 공유하는 Alpine 컨테이너 | 없음 | `db_data:/data` |

**SQLite는 `web` 프로세스가 직접 사용하는 파일 DB입니다.** `db` 컨테이너는 `tail -f /dev/null`로 유지되며 MySQL·PostgreSQL 같은 DB 서버를 실행하지 않습니다. Nginx 컨테이너도 현재 구성에는 없습니다.

Compose에서는 DB가 `/app/instance/lounge.sqlite3`, 캐시가 `/app/instance/audio/`에 저장됩니다. 이 경로는 이름 있는 `db_data` 볼륨에 연결되므로 호스트 프로젝트의 `instance/`와는 별개입니다. yt-dlp 서버는 변환에 임시 디렉터리를 사용하고, 응답 종료 시 정리하도록 구현되어 있습니다.

`web`의 `depends_on`은 컨테이너 시작 순서를 지정합니다. 서비스 준비 완료를 검사하는 healthcheck 설정은 없으므로 시작 직후 미디어 요청이 실패하면 로그와 `/health`를 확인하세요.

## 로컬 개발 실행

Docker 없이 실행할 때도 **웹 앱과 yt-dlp API 서버를 각각 실행**해야 곡 정보 가져오기와 MP3 재생을 사용할 수 있습니다. Dockerfile의 Python 버전은 3.11입니다. MP3 변환에는 yt-dlp 서버 환경의 FFmpeg·FFprobe가 필요합니다.

### 1. 웹 앱 환경 준비

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell에서는 `python -m venv .venv`로 생성한 뒤 `.\.venv\Scripts\Activate.ps1`로 활성화합니다.

### 2. yt-dlp 서버 실행: 별도 터미널

```sh
python3 -m venv ytdlp_server/.venv
source ytdlp_server/.venv/bin/activate
python -m pip install -r ytdlp_server/requirements.txt
ffmpeg -version
ffprobe -version
python ytdlp_server/app.py
```

Windows에서는 `python -m venv ytdlp_server/.venv`로 생성하고 `.\ytdlp_server\.venv\Scripts\Activate.ps1`로 활성화합니다. FFmpeg·FFprobe는 PATH에 등록해야 합니다.

### 3. 웹 앱 실행: 첫 번째 터미널

macOS / Linux:

```sh
export YTDLP_SERVER_URL=http://127.0.0.1:5001
export FLASK_DEBUG=1
python app.py
```

Windows PowerShell:

```powershell
$env:YTDLP_SERVER_URL = 'http://127.0.0.1:5001'
$env:FLASK_DEBUG = '1'
python app.py
```

직접 실행한 웹 앱은 http://localhost:5000/ 에서 열립니다. 5000번 포트가 사용 중이면 다음처럼 다른 포트로 실행합니다.

```sh
python -m flask --app app run --host 127.0.0.1 --port 8000 --debug
```

HTML 파일을 직접 열거나 Live Server로 실행하면 Flask API와 템플릿이 동작하지 않습니다. 디버그 모드가 꺼져 있으면 Python·템플릿 변경 후 서버를 재시작하세요. CSS·JavaScript 변경이 보이지 않으면 브라우저를 강력 새로고침합니다.

### 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `YTDLP_SERVER_URL` | `http://ytdlp-server:5001` | 웹 앱이 호출하는 미디어 서버 주소. 로컬 직접 실행 시 `http://127.0.0.1:5001`로 설정 |
| `MUSIC_LOUNGE_DB` | 프로젝트의 `instance/lounge.sqlite3` | SQLite 경로. MP3 캐시도 이 경로의 상위 폴더 아래 `audio/`에 생성 |
| `DB_PATH` | 미설정 | SQLite 경로 우선 지정. `MUSIC_LOUNGE_DB`보다 우선하며 오디오 캐시 경로에는 적용되지 않음 |
| `SECRET_KEY` | 프로세스 시작 시 임의 생성 | 로그인 세션 서명 키. 재시작 후 세션을 유지하려면 고정된 비밀 값 설정 |
| `FLASK_DEBUG` | 미설정 | 웹 앱의 `python app.py` 실행 시 `1`이면 디버그 모드 |
| `PORT` | `5001` | yt-dlp 서버의 직접 실행 포트. 변경 시 웹 앱 주소 설정도 함께 변경 |

`.env` 파일을 자동으로 읽는 코드는 없습니다. 셸에서 설정하거나 Compose의 `environment`에 반영하세요. Compose에 있는 `FLASK_ENV=development`를 웹 앱이 직접 확인하지는 않으며, 현재 디버그 분기는 `FLASK_DEBUG`를 사용합니다.

## 사용 방법

1. 로그인 화면에서 계정을 생성하거나 기존 아이디·비밀번호로 로그인합니다. 신규 아이디는 영문·숫자·밑줄 4~20자, 닉네임은 2~20자입니다.
2. 마이페이지에서 플레이리스트 이름·기본 장르·YouTube 링크를 저장합니다. 편집창의 **곡 정보 가져오기**로 공개 영상이나 재생목록의 제목·채널·길이를 가져올 수 있습니다.
3. 공유 설정에서 라운지를 선택합니다. 하나의 목록을 여러 라운지에 공유할 수 있습니다.
4. 라운지에서 참여자의 공유 목록을 선택해 전체 재생 또는 곡별 재생을 사용하고 채팅에 참여합니다.
5. 공유 해제는 보관함 원본을 유지합니다. 플레이리스트 삭제는 모든 라운지의 공유 목록에서도 제거합니다.

한 줄에 한 동영상 링크를 입력하며, 플레이리스트당 최대 100곡을 저장합니다. 곡 제목을 지정할 수도 있습니다.

```text
https://www.youtube.com/watch?v=M7lc1UVf-VE
곡 이름 | https://youtu.be/M7lc1UVf-VE
```

`watch`, `youtu.be`, `shorts`, `live`, `embed`, `music.youtube.com` 링크를 검증해 표준 영상 URL과 ID로 저장합니다. 곡 정보 가져오기는 HTTPS 링크를 받습니다. 이전 데이터의 텍스트 곡명은 보존하지만, 실제 오디오 재생에는 영상 정보가 필요합니다. 라운지의 기본 샘플 목록은 예시 데이터입니다.

### 추가 개발 중인 기능

단일 입력창에 쉼표로 최대 3곡을 입력하고, 선택한 2~3곡이 함께 담긴 공유 목록을 찾는 UI는 [PR #18](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/18)로 분리했습니다. 해당 PR은 미병합 상태이며 추천 API는 후속 백엔드 작업이 필요합니다. 이 README 수정 PR에는 추천 기능의 코드·테스트를 포함하지 않으므로 현재 `develop`의 제공 기능이나 실행 조건에는 포함하지 않습니다.

## 저장·실시간 통신·미디어 처리

### SQLite와 SSE

`storage.py`가 시작 시 테이블을 생성하고 이전 스키마의 일부 컬럼·데이터를 마이그레이션합니다.

| 테이블 | 역할 |
| --- | --- |
| `users` | 계정, 비밀번호 해시, 닉네임, 프로필, 권한·상태 |
| `lounges` | 장르별 라운지 정보 |
| `playlists` | 소유자, 제목, 곡·공유 라운지 JSON, 좋아요 수, 생성·수정 시각 |
| `playlist_likes` | 계정별 좋아요 매핑 |
| `messages` | 라운지별 최근 채팅 100개 |

좋아요 수와 목록 수정 시각은 SQLite 트리거로 갱신합니다. 비밀번호는 Werkzeug 해시로 저장합니다.

브라우저의 `EventSource`가 참여자·메시지·공유 변경을 SSE로 받습니다. 메시지 작성과 청취 상태 변경은 인증된 HTTP POST를 사용합니다. 같은 계정의 여러 탭은 라운지별 한 명으로 집계하며, 재연결 시 스냅샷을 받습니다. 네트워크 단절 감지에는 heartbeat 전송까지 시간이 걸릴 수 있습니다.

SSE 이벤트는 `snapshot`, `presence`, `message`, `playlists_changed`, `closed`입니다. 연결 상태는 프로세스 메모리에 있으므로 **단일 웹 프로세스**를 전제로 합니다. 다중 worker·서버에는 Redis 같은 공통 상태·pub/sub 계층이 필요합니다. 프록시를 추가하면 SSE 버퍼링을 끄고 장기 연결을 허용해야 합니다. 청취 인원 표시는 브라우저 사이의 오디오 재생 위치 동기화를 의미하지 않습니다.

### 메타데이터와 MP3

- 웹 앱은 미디어 서버의 `/api/extract`로 메타데이터를 요청합니다. 가져오기는 최대 100개 항목을 확인하고 중복·비공개 영상 등을 제외합니다. 웹 앱의 동시 가져오기 요청은 2건입니다.
- 재생 요청 시 웹 앱이 백그라운드에서 미디어 서버의 `/api/stream`을 호출합니다. yt-dlp와 FFmpeg가 MP3로 변환하며 웹 앱이 응답 파일을 캐시합니다.
- 웹 앱의 오디오 작업은 동시 2건, 대기 포함 최대 8건입니다. 메타데이터 HTTP 요청 timeout은 65초, 오디오 요청 timeout은 180초입니다. 오디오 timeout은 전체 변환 시간을 강제로 제한하는 별도 작업 타이머가 아닙니다.
- MP3 응답 파일은 웹 앱에서 수신 완료 후 100MiB 이하인지 확인합니다. 캐시는 24시간 유효하며, 변환 완료 후 오래된 파일을 정리해 약 512MiB 한도를 관리합니다.
- 현재 미디어 서버 코드에는 30분 초과 영상이나 라이브 영상을 사전에 차단하는 검증이 없습니다. 오류 문구의 제한 안내와 실제 검증 범위가 다를 수 있습니다.
- 오디오 파일은 로그인 및 목록 접근 권한을 확인한 뒤 제공하며 HTTP Range를 지원합니다. 브라우저 정책에 따라 재생 버튼을 한 번 더 눌러야 할 수 있습니다.
- 외부 서비스·네트워크 제한으로 공개 영상도 실패할 수 있습니다. 화면의 오류 안내나 YouTube 원본 링크를 이용하세요. 이용 권한이 있는 콘텐츠를 사용하세요.

## 폴더 구조

```text
MUSIC-LOUNGE/
├── app.py                       # 화면·인증·REST API·SSE
├── storage.py                   # SQLite 초기화·이전 스키마 마이그레이션
├── playlist_service.py          # 곡 검증·목록 저장·공유·좋아요
├── realtime.py                  # 메모리 기반 연결·참여자·청취 상태
├── youtube_import.py            # 미디어 서버 메타데이터 호출·정규화
├── audio_service.py             # 백그라운드 오디오 요청·캐시
├── sample_data.py               # 기본 라운지·샘플 목록
├── templates/
│   ├── index.HTML               # 메인·라운지 화면 (확장자 대문자)
│   ├── login.html
│   └── mypage.html
├── static/
│   ├── app.js                   # 라운지 화면 동작
│   ├── style.css                # 공통 스타일
│   ├── css/                     # 홈·로그인·마이페이지·공통 화면 스타일
│   ├── js/                      # API/SSE·플레이어·가져오기·계정 UI
│   └── backgrounds/             # 라운지 배경 이미지
├── ytdlp_server/
│   ├── app.py                   # 추출·검색·MP3 변환 API
│   ├── Dockerfile
│   └── requirements.txt         # yt-dlp 서버 전용 의존성
├── docs/
│   └── music_lounge_db_api.md    # DB/API 상세 참고 명세
├── Dockerfile
├── docker-compose.yml
├── requirements.txt             # 웹 앱 의존성: Flask·requests
├── GIT_CONVENTION.md
└── instance/                    # 실행 시 생성되는 DB·캐시 (Git 제외)
```

## 주요 API

웹 앱의 미디어·계정·라운지 변경 API는 로그인이 필요합니다.

| 메서드 | 경로 | 기능 |
| --- | --- | --- |
| `POST` | `/login`, `/logout` | 로그인·신규 계정 생성, 로그아웃 |
| `GET, PATCH, DELETE` | `/api/me` | 계정 조회·수정·탈퇴 |
| `GET` | `/api/me/likes` | 좋아요한 목록 조회 |
| `GET, POST` | `/api/me/playlists` | 내 목록 조회·생성 |
| `PATCH, DELETE` | `/api/me/playlists/<id>` | 내 목록 수정·삭제 |
| `PUT` | `/api/me/playlists/<id>/share` | `{room, enabled}` 공유 설정 |
| `POST` | `/api/playlists/cart-combine` | 전달된 제목·곡으로 목록 생성, 선택적 라운지 공유 |
| `GET` | `/api/search?q=검색어&room=장르` | 공유 목록의 제목·작성자 검색, 최대 20개 |
| `POST` | `/api/import` | `{url}` 메타데이터 가져오기 |
| `GET, POST` | `/api/playlists/<id>/audio/<video_id>` | MP3 상태 조회·준비 |
| `GET` | `/api/playlists/<id>/audio/<video_id>/file` | MP3 파일 제공 |
| `GET` | `/api/rooms` | 라운지 정보·참여 인원 |
| `GET, POST` | `/api/rooms/<room>/playlists` | 라운지 목록 조회·생성 후 공유 |
| `POST` | `/api/rooms/<room>/playlists/<id>/like` | 좋아요 전환 |
| `GET, POST` | `/api/rooms/<room>/messages` | 최근 메시지 조회·전송 |
| `GET` | `/api/rooms/<room>/events?connection=<token>` | SSE 연결 |
| `POST` | `/api/rooms/<room>/listening` | `{connection, playlist_id}` 청취 목록 알림 |
| `POST` | `/api/rooms/<room>/leave` | `{connection}` 연결 퇴장 |

미디어 서버는 `/health`, `/api/extract?url=…`, `/api/search?q=…`, `/api/stream?url=…`를 제공합니다. 미디어 서버의 검색은 YouTube 검색이며 웹 앱의 `/api/search`와 대상이 다릅니다.

## 검증과 현재 한계

Compose 설정 문법을 확인합니다.

```sh
docker compose config --quiet
```

현재 `develop`에는 자동화 테스트 파일이 없습니다. 기존 README에서 안내하던 `tests/test_media.py`와 인증·채팅·미디어 통합 테스트는 저장소에서 확인되지 않으므로 실행·통과를 보장하지 않습니다. 로컬 개발 중인 추천 기능의 테스트는 별도 기능 PR에서 추가할 예정입니다.

실행 후 메인·로그인·마이페이지, 미디어 서버의 `/health`를 확인하고, 실제 YouTube 가져오기·MP3 변환·여러 브라우저의 SSE 채팅은 해당 실행 환경에서 별도로 확인해야 합니다.

현재 Docker CMD는 Flask 개발 서버를 사용하며, yt-dlp 서버는 직접 실행 시 디버그 모드입니다. 운영 배포 시 서버 실행 방식·접근 제어를 별도로 구성해야 합니다. yt-dlp 서버의 `gunicorn`은 의존성에 있지만 현재 CMD에서 사용하지 않습니다.

## PR 반영 기준

README는 PR의 계획 문구보다 **현재 파일에 구현된 동작**을 우선합니다. 원본 프로젝트의 다음 PR과 로컬 이력을 대조했습니다.

| PR | 현재 문서에 반영한 내용 |
| --- | --- |
| [#5](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/5) | yt-dlp 검색·스트리밍 API 서버 |
| [#8](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/8) · [#9](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/9) · [#10](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/10) | 인프라·백엔드 서비스·프론트엔드 기본 구성 |
| [#13](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/13) | web / ytdlp-server / db 3개 컨테이너, HTTP 미디어 연동, 공유 볼륨 |
| [#14](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/14) | SQLite 스키마 개선, lounges 조회, DB/API 문서 |
| [#15](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/15) | 메인·로그인·마이페이지 UI 개편, 미사용 파일 정리 |

2026-11-15 확인 시 AI 추천 [PR #16](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/16)은 미병합 상태이며 관련 서비스·Ollama 설정이 현재 코드에 없습니다. 함께 듣기 DB 확장 [PR #17](https://github.com/2026-Open-Source-Software/MUSIC-LOUNGE/pull/17)도 병합 없이 닫혀 있어 해당 PR의 추가 테이블을 현재 스키마로 기술하지 않습니다.

## 기여

브랜치·커밋·PR 규칙은 [GIT_CONVENTION.md](GIT_CONVENTION.md)를 따릅니다. 기능 브랜치에서 변경하고 `develop`을 대상으로 기능별 PR을 작성합니다. README 수정 PR 제목 예시는 `[docs][docs] 현재 구현 기준 실행·구조·기능 문서 갱신`입니다.

DB/API 상세 자료는 [통합 명세](docs/music_lounge_db_api.md)를 참고하세요. 개별 명세와 현재 동작이 다르면 코드를 함께 확인해주세요. 현재 저장소에는 `LICENSE` 파일이 없습니다.
