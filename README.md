# MUSIC LOUNGE

장르별 라운지에서 유튜브 플레이리스트를 공유하고 같은 공간의 사용자들과 실시간으로 대화하는 Flask 앱입니다.

## 실행

Windows PowerShell에서 프로젝트 폴더로 이동한 뒤 실행하세요.

```powershell
python -m pip install -r requirements.txt
python app.py
```

- 홈: http://localhost:5000/
- 로그인: http://localhost:5000/login
- 마이페이지: http://localhost:5000/mypage

Flask 템플릿을 사용하므로 HTML을 직접 열거나 Live Server로 실행하지 마세요. 변경 적용 시 서버를 재시작하고 브라우저에서 Ctrl+F5로 새로고침하세요. 개발 중 자동 재시작이 필요하면 `FLASK_DEBUG=1`을 설정하세요.

Docker에서는 `docker compose up --build`로 실행합니다. 기존 Compose 설정은 프로젝트 디렉터리를 `/app`에 연결하므로 데이터도 로컬에 유지됩니다.

## 사용 흐름

1. 로그인합니다. 처음 사용하는 아이디는 계정이 생성되고, 기존 아이디는 비밀번호로 인증합니다.
2. 마이페이지에서 제목·기본 장르·유튜브 링크를 입력해 플레이리스트를 계정에 저장합니다.
3. 공유 설정에서 원하는 라운지를 선택합니다. 하나의 목록을 여러 라운지에 공유할 수 있습니다.
4. 홈에서 장르별 라운지에 입장하면 현재 참여자와 인원이 표시됩니다.
5. 닉네임을 누르면 해당 사용자가 현재 라운지에 공유한 목록이 나옵니다. 목록을 선택하고 전체 재생 또는 곡 이름을 눌러 MP3 플레이어로 감상할 수 있습니다.
6. 오른쪽 채팅에서 같은 라운지 사람들과 대화합니다.

라운지에서도 새 목록을 만들거나 보관함의 기존 목록을 공유할 수 있습니다. 공유 해제는 보관함 원본을 유지하고, 삭제는 보관함과 모든 공유 라운지에서 목록을 제거합니다. 수정과 삭제에는 본인 계정 인증이 필요합니다.

## 유튜브 링크

한 줄에 한 동영상 링크를 입력합니다. 최대 100곡이며 선택적으로 곡 이름을 앞에 붙일 수 있습니다.

```text
https://www.youtube.com/watch?v=M7lc1UVf-VE
곡 이름 | https://youtu.be/M7lc1UVf-VE
```

`watch`, `youtu.be`, `shorts`, `live`, `embed`, `music.youtube.com` 형식을 검증해 표준 동영상 URL과 ID로 저장합니다. 영상·공개 재생목록 링크는 편집창의 **곡 정보 가져오기**에 입력하세요. yt-dlp로 최대 100곡의 제목·채널·길이를 읽고 중복을 제외합니다. 저장 버튼을 누르면 계정에 보관됩니다. 재생은 서버가 MP3를 준비한 후 브라우저 오디오 플레이어에서 합니다. YouTube 새 창 링크도 유지합니다.

기존 계정·저장 목록은 그대로 읽습니다. 이전의 텍스트 곡명도 보존되며 링크로 교체하면 재생할 수 있습니다. 기본 샘플 목록은 예시로 표시되고 실제 접속자 수에는 포함되지 않습니다.

## 저장과 실시간 통신

- 계정, 플레이리스트, 공유 대상, 좋아요, 라운지별 최근 채팅 100개를 SQLite에 저장합니다.
- 기본 DB 위치는 `instance/lounge.sqlite3`입니다. `MUSIC_LOUNGE_DB`로 경로를 바꿀 수 있습니다.
- 비밀번호는 Werkzeug 비밀번호 해시로 저장합니다. 기존 계정을 초기화하거나 비밀번호를 평문으로 저장하지 않습니다.
- `SECRET_KEY`를 고정하면 서버 재시작 후에도 로그인 쿠키를 유지할 수 있습니다.
- 브라우저의 `EventSource`로 SSE 연결을 열고 서버가 참여자·메시지·공유 변경을 전달합니다. 주기적인 HTTP 폴링이나 예시 채팅이 아닙니다. 메시지 작성은 인증된 HTTP POST를 사용합니다.
- 재연결 시 최근 채팅·참여자·공유 목록을 다시 받아 복구합니다. 같은 계정의 여러 탭은 라운지별 한 명으로 집계합니다.
- 명시적 퇴장·페이지 이동·로그아웃은 연결을 해제합니다. 네트워크 단절은 heartbeat 전송으로 감지하므로 참여자 반영에 수 초가 걸릴 수 있습니다.
- 실시간 연결 허브는 **단일 Python 프로세스** 기준입니다. 여러 worker/서버로 확장할 때는 Redis 등의 공통 presence/pub-sub 저장소를 연결해야 합니다. SSE를 사용하는 리버스 프록시는 응답 버퍼링을 끄고 장기 연결을 허용하세요.

## 코드 구조와 API

- `app.py`: 인증, REST API, SSE endpoint, 화면 라우팅
- `playlist_service.py`: 유튜브 검증, 소유권, 공유, SQLite 플레이리스트 처리
- `storage.py`: DB 연결과 기존 데이터와 호환되는 테이블 초기화
- `realtime.py`: 라운지별 연결·참여자·이벤트 큐
- `sample_data.py`: 기존 장르와 공용 샘플 목록
- `static/js/api.js`: HTTP/SSE 통신, 재연결과 퇴장 처리
- `static/js/playlist-ui.js`: 공용 곡 목록과 MP3 플레이어 UI
- `youtube_import.py`: yt-dlp 기반 공개 영상/재생목록 메타데이터 가져오기
- `audio_service.py`: 제한된 백그라운드 MP3 변환 작업과 캐시
- `static/js/youtube-import.js`: 공용 가져오기 UI·중복 제거
- `static/app.js`: 라운지 화면 상태와 사용자 이벤트
- `static/js/mypage.js`: 계정·보관함 관리 UI

| API | 기능 |
| --- | --- |
| `POST /api/import` | `{url}`로 로그인 후 곡 정보 가져오기 |
| `POST, GET /api/playlists/<id>/audio/<video_id>` | MP3 준비 요청·상태 확인 |
| `GET /api/playlists/<id>/audio/<video_id>/file` | 권한 확인 후 MP3 제공, Range 지원 |
| `GET /api/rooms` | 라운지 목록 |
| `GET /api/rooms/<room>/playlists` | 해당 라운지 공유 목록·랭킹 |
| `POST /api/rooms/<room>/playlists` | 저장과 동시에 해당 라운지에 공유 |
| `GET, POST /api/me/playlists` | 내 보관함 조회·생성 |
| `PATCH, DELETE /api/me/playlists/<id>` | 본인 목록 수정·삭제 |
| `PUT /api/me/playlists/<id>/share` | `{room, enabled}`로 라운지 공유 설정 |
| `GET, PATCH /api/me` | 계정 조회·닉네임/비밀번호 변경 |
| `GET /api/rooms/<room>/events?connection=<token>` | 인증된 실시간 스트림 |
| `GET, POST /api/rooms/<room>/messages` | 최근 대화·메시지 전송 |
| `POST /api/rooms/<room>/leave` | 현재 연결 퇴장 |

SSE 이벤트는 `snapshot`, `presence`, `message`, `playlists_changed`, `closed`이며 방 ID와 독립적인 연결 토큰을 사용합니다. 사용자 식별에는 공개용 `member_id`를 사용하고 클라이언트가 보내는 작성자 이름을 신뢰하지 않습니다. 프론트엔드의 API 계층을 별도로 두어 추후 통신 방식을 교체할 수 있습니다.

## 검증

```powershell
python -m unittest discover -s tests -v
```

테스트는 임시 DB와 실제 로컬 HTTP 스트림을 사용합니다. 기존 DB를 건드리지 않으며 인증·계정 저장·유튜브 링크 검증·기존 텍스트 곡 보존·소유권·다중 라운지 공유·재시작 후 데이터·서로 다른 사용자 간 채팅·다중 탭 인원·라운지 분리·퇴장·로그아웃을 검증합니다.

구현 참고: [SSE 표준](https://html.spec.whatwg.org/multipage/server-sent-events.html), [YouTube 임베드 플레이어](https://developers.google.com/youtube/player_parameters).


## 곡 정보 가져오기와 MP3 재생

1. 로그인 후 마이페이지에서 새 플레이리스트를 만들거나 기존 목록을 수정합니다.
2. 유튜브 영상·공개 재생목록 링크를 **곡 정보 가져오기**에 입력합니다.
3. 가져온 곡을 확인하고 저장합니다. 기존 곡은 유지하며 영상 ID 중복을 제외합니다.
4. 마이페이지의 **MP3 플레이어 열기** 또는 라운지에서 공유 목록을 선택합니다.
5. **전체 재생**이나 곡 이름을 누르면 MP3 준비를 시작합니다. 준비 완료 후 재생합니다. 브라우저 정책에 따라 오디오 컨트롤의 재생 버튼을 한 번 더 눌러야 할 수 있습니다.
6. 볼륨·일시정지·구간 탐색·이전/다음 곡·자동 이어 듣기를 사용할 수 있습니다.

### 필요한 프로그램

- `python -m pip install -r requirements.txt`로 yt-dlp를 설치합니다.
- MP3 변환에는 **FFmpeg와 FFprobe**가 모두 필요합니다. 두 실행 파일을 PATH에 추가하거나 아래처럼 폴더 경로를 지정하세요.

```powershell
$env:FFMPEG_LOCATION = 'C:\ffmpeg\bin'
python app.py
```

지정한 폴더에는 `ffmpeg.exe`와 `ffprobe.exe`가 실제로 있어야 합니다. 설치 또는 PATH 변경 후 터미널을 새로 열고 `ffmpeg -version`, `ffprobe -version`으로 확인하세요.

Dockerfile에는 FFmpeg 설치가 포함되어 있습니다. 기존 이미지 대신 `docker compose up --build`로 다시 빌드하면 됩니다. 유튜브에 접속할 수 있는 서버 네트워크가 필요합니다.

### 처리 방식과 한도

- 곡 정보 가져오기는 다운로드 없이 메타데이터만 읽습니다. 실제 오디오 다운로드·MP3 변환은 재생을 요청할 때 시작합니다.
- 변환은 yt-dlp의 오디오 추출과 FFmpeg를 사용합니다. 동시 변환 2건, 대기 포함 최대 8건, 작업당 최대 180초입니다.
- 라이브·30분 초과·100MB 초과 콘텐츠는 지원하지 않습니다. 네트워크나 원본 서비스 제한에 따라 공개 영상도 실패할 수 있습니다.
- MP3는 DB와 같은 폴더의 `audio/`에 캐시합니다. 기본 위치는 `instance/audio/`입니다. 캐시는 24시간 유효하며 새 변환 후 오래된 파일부터 정리해 최대 약 512MB로 관리합니다.
- 파일은 static 폴더에 노출하지 않습니다. 본인 비공개 목록 또는 공유된 목록의 곡인지 로그인·소유권을 매번 확인합니다. 공유 해제/삭제 후에는 다른 계정이 URL을 알고 있어도 파일을 받을 수 없습니다.
- 실패 원인은 화면에 표시하며 다른 방식으로 재생됐다고 가장하지 않습니다. 기존 YouTube 새 창 링크를 이용할 수 있습니다.
- 재생할 오디오는 이용 권한이 있는 콘텐츠를 사용하세요.

### 검증 범위

`tests/test_media.py`에서 메타데이터 저장, 100곡 제한, FFmpeg 미설치, 변환 실패, 파일 접근 권한, MP3 캐시와 HTTP Range를 검증합니다. 테스트에서는 외부 유튜브 응답과 변환 프로세스를 대체합니다. 브라우저 검증에는 직접 만든 무음 MPEG 오디오를 사용합니다.

현재 개발 실행 환경은 유튜브 외부 접속이 제한되고 FFmpeg가 설치되어 있지 않아, 실제 유튜브 다운로드→FFmpeg 변환 전체 과정은 확인하지 못했습니다.

참고: [yt-dlp 공식 문서](https://github.com/yt-dlp/yt-dlp), [FFmpeg 다운로드](https://ffmpeg.org/download.html).
