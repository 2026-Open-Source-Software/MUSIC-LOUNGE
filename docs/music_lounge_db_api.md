# 🎵 MUSIC LOUNGE 데이터베이스(DB) 및 API 통합 명세서

본 문서는 **취향 기반 음악 라운지(MUSIC LOUNGE)** 프로젝트의 최신 백엔드(Flask + SQLite + `ytdlp-server`) 구현 기준 데이터베이스 스키마와 REST API / SSE 실시간 이벤트 명세를 정리한 통합 문서입니다.

---

## Part 1. 데이터베이스(DB) 명세서

### 1.1 DB 설계 개요
* **DBMS**: SQLite (`PRAGMA foreign_keys = ON` 활성화)
* **설계 핵심 방향**:
  1. 불필요한 세션(`sessions`) 및 개별 트랙(`tracks`, `playlist_tracks`) 테이블을 제거하고 핵심 테이블 중심으로 경량화했습니다.
  2. 유튜브 메타데이터 및 수록곡 정보는 `yt-dlp`를 통해 추출한 후 `playlists` 테이블 내부에 JSON(`songs`) 형태로 직렬화하여 관리합니다.
  3. 좋아요 증감(`likes`)과 플레이리스트 수정 시각(`updated_at`)은 **SQLite Trigger**를 통해 DB 레벨에서 자동 갱신됩니다.

### 1.2 전체 테이블 요약
| 번호 | 테이블명 | 한글명 | 주요 역할 |
| :---: | :--- | :--- | :--- |
| **1** | `users` | 회원 정보 | 사용자 계정, 프로필(닉네임, 취향 태그, 이미지), 권한 및 상태 관리 |
| **2** | `lounges` | 장르별 라운지 | 음악 장르별(발라드, 힙합, R&B, 인디, 댄스 등) 라운지 공간 정보 관리 |
| **3** | `playlists` | 플레이리스트 | 사용자가 생성한 플레이리스트 제목, 수록곡·공유방 JSON(또는 유튜브 URL), 좋아요 수 관리 |
| **4** | `playlist_likes` | 플레이리스트 좋아요 매핑 | 회원별 플레이리스트 좋아요 이력 관리 및 중복 좋아요 방지 |
| **5** | `messages` | 실시간 채팅 내역 | 각 장르 라운지별 최근 채팅 메시지 보관 (방당 최신 100건 유지) |

---

### 1.3 테이블별 상세 스키마 명세

#### 1) `users` (회원 정보 테이블)
사용자의 계정 정보와 프로필, 권한 및 계정 상태를 관리합니다.

| 컬럼명 | 데이터 타입 | 제약 조건 | 기본값 | 설명 및 역할 |
| :--- | :--- | :--- | :--- | :--- |
| **`id`** | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | 자동 증가 | 내부 외래키(FK) 참조용 회원 고유 번호 (`session['user_pk']`) |
| **`user_id`** *(또는 `login_id`)* | `TEXT` | `NOT NULL UNIQUE` | - | 로그인 아이디 또는 이메일 (중복 불가, 고유 인덱스 자동 생성) |
| **`password_hash`** | `TEXT` | `NOT NULL` | - | 단방향 암호화된 비밀번호 해시값 (Werkzeug / BCrypt) |
| **`nickname`** | `TEXT` | `NOT NULL` | - | 라운지 및 플레이리스트에 표시될 사용자 닉네임 (2~20자) |
| **`tag`** | `TEXT` | `NULL` | `NULL` | 사용자 대표 음악 취향 태그 (예: `#힙합`, `#알앤비`) |
| **`profile_image`** | `TEXT` | `NULL` | `NULL` | 프로필 이미지 URL 또는 경로 |
| **`role`** | `TEXT` | `NOT NULL CHECK(role IN ('USER', 'ADMIN'))` | `'USER'` | 사용자 권한 등급 (`USER`: 일반 회원, `ADMIN`: 관리자) |
| **`status`** | `TEXT` | `NOT NULL CHECK(status IN ('ACTIVE', 'LOCKED', 'DELETED'))` | `'ACTIVE'` | 계정 상태 (`ACTIVE`: 정상, `LOCKED`: 정지, `DELETED`: 탈퇴) |

---

#### 2) `lounges` (장르별 라운지 테이블)
음악 장르별로 구분된 라운지 공간의 정보를 관리합니다.

| 컬럼명 | 데이터 타입 | 제약 조건 | 기본값 | 설명 및 역할 |
| :--- | :--- | :--- | :--- | :--- |
| **`id`** | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | 자동 증가 | 라운지 고유 번호 |
| **`room`** *(또는 `genre`)* | `TEXT` | `NOT NULL UNIQUE` | - | 장르 라운지 식별 코드 (예: `ballad`, `rnb`, `hiphop`, `indie`, `dance`) |
| **`name`** | `TEXT` | `NOT NULL` | - | 라운지 표시 이름 (예: `발라드`, `R&B`, `힙합`, `인디`, `댄스`) |
| **`description`** | `TEXT` | `NULL` | `NULL` | 라운지 설명 및 소개글 (이모지 포함 가능) |
| **`thumbnail_image`** | `TEXT` | `NULL` | `NULL` | 라운지 대표 썸네일 이미지 URL |

---

#### 3) `playlists` (플레이리스트 테이블)
사용자가 생성하고 관리하는 플레이리스트의 기본 정보, 수록곡(또는 유튜브 URL), 좋아요 수를 관리합니다.

| 컬럼명 | 데이터 타입 | 제약 조건 | 기본값 | 설명 및 역할 |
| :--- | :--- | :--- | :--- | :--- |
| **`id`** | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | 자동 증가 | 플레이리스트 고유 번호 |
| **`owner_id`** *(또는 `user_id`)* | `INTEGER` | `NOT NULL`, `FK -> users(id) ON DELETE CASCADE` | - | 생성한 회원 고유 번호 (회원 탈퇴 시 연쇄 삭제) |
| **`title`** | `TEXT` | `NOT NULL` | - | 플레이리스트 제목 (1~60자, 예: `Good Vibes Only`) |
| **`songs`** *(또는 `youtubeURL`)* | `TEXT` | `NOT NULL` | - | 유튜브 플레이리스트 URL 또는 추출된 수록곡 배열·공유 라운지(`room`, `shared_rooms`) JSON 문자열 |
| **`likes`** *(또는 `like_count`)* | `INTEGER` | `NOT NULL CHECK(likes >= 0)` | `0` | 좋아요 수 캐시 (랭킹 산정용, 음수 방지, 트리거로 자동 증감) |
| **`created_at`** | `DATETIME` | `NOT NULL` | `CURRENT_TIMESTAMP` | 플레이리스트 최초 생성 일시 |
| **`updated_at`** | `DATETIME` | `NOT NULL` | `CURRENT_TIMESTAMP` | 플레이리스트 최종 수정 일시 (정보 수정 시 트리거로 자동 갱신) |

> **💡 `playlists.songs` 내부 JSON 구조 상세**
> ```json
> {
>   "room": "hiphop",
>   "shared_rooms": ["hiphop", "rnb"],
>   "songs": [
>     {
>       "video_id": "ba7mB8oueCY",
>       "url": "https://www.youtube.com/watch?v=ba7mB8oueCY",
>       "title": "SUN GOES DOWN",
>       "artist": "Khalid",
>       "duration": 185
>     }
>   ]
> }
> ```

---

#### 4) `playlist_likes` (플레이리스트 좋아요 매핑 테이블)
어떤 회원이 어떤 플레이리스트에 좋아요를 눌렀는지 기록하며 중복 좋아요를 방지합니다.

| 컬럼명 | 데이터 타입 | 제약 조건 | 기본값 | 설명 및 역할 |
| :--- | :--- | :--- | :--- | :--- |
| **`user_id`** | `INTEGER` | `PK`, `NOT NULL`, `FK -> users(id) ON DELETE CASCADE` | - | 좋아요를 누른 회원 고유 번호 (`users.id`, 탈퇴 시 연쇄 삭제) |
| **`playlist_id`** | `INTEGER` | `PK`, `NOT NULL`, `FK -> playlists(id) ON DELETE CASCADE` | - | 좋아요 대상 플레이리스트 번호 (`playlists.id`, 삭제 시 연쇄 삭제) |
| **`created_at`** | `DATETIME` | `NOT NULL` | `CURRENT_TIMESTAMP` | 좋아요를 누른 일시 |

* **복합 기본키 제약**: `PRIMARY KEY (user_id, playlist_id)`를 통해 한 사용자가 동일 플레이리스트에 중복으로 좋아요를 누르는 것을 방지합니다.

---

#### 5) `messages` (실시간 라운지 채팅 내역 테이블)
라운지 내 실시간 채팅 메시지를 저장하고 최근 대화 목록(최대 100건)을 제공합니다.

| 컬럼명 | 데이터 타입 | 제약 조건 | 기본값 | 설명 및 역할 |
| :--- | :--- | :--- | :--- | :--- |
| **`id`** | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | 자동 증가 | 채팅 메시지 고유 번호 |
| **`room`** | `TEXT` | `NOT NULL` | - | 메시지가 전송된 라운지 코드 (`lounges.room`) |
| **`user_id`** | `TEXT` | `NOT NULL` | - | 작성자 로그인 아이디 (`users.user_id`) |
| **`nickname`** | `TEXT` | `NOT NULL` | - | 메시지 작성 시점의 닉네임 |
| **`text`** | `TEXT` | `NOT NULL` | - | 채팅 메시지 본문 (1~300자) |
| **`created_at`** | `TEXT` | `NOT NULL` | - | 작성 시각 (UTC ISO-8601 문자열) |

---

### 1.4 데이터베이스 트리거(Triggers) 및 인덱스(Indexes)
| 구분 | 명칭 | 대상 테이블 | 동작 시점 및 설명 |
| :--- | :--- | :--- | :--- |
| **Trigger** | `update_playlist_timestamp` | `playlists` | `title`, `songs`(`youtubeURL`) 수정 시 `updated_at = CURRENT_TIMESTAMP` 자동 갱신 |
| **Trigger** | `increment_playlist_like_count` | `playlist_likes` | `AFTER INSERT` 시 해당 `playlists.likes` 값을 `+ 1` 자동 증가 |
| **Trigger** | `decrement_playlist_like_count` | `playlist_likes` | `AFTER DELETE` 시 해당 `playlists.likes` 값을 `- 1` 자동 감소 |
| **Index** | `idx_messages_room_id` | `messages(room, id)` | 라운지별 최근 채팅 메시지 100건 고속 조회 및 오래된 메시지 정리 |

---
---

## Part 2. API 명세서

### 2.1 공통 규격 (Common Specifications)
* **인증 방식**: Flask 서버 세션 쿠키 (`SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE='Lax'`)
* **보안 및 출처(Origin) 검증**: `GET`, `HEAD`, `OPTIONS` 외의 상태 변경 요청(`POST`, `PATCH`, `PUT`, `DELETE`) 및 실시간 스트림(`/events`) 요청 시 `Origin` 헤더가 현재 호스트와 일치하는지 검증 (불일치 시 `403 Forbidden`)
* **데이터 형식**: 요청 및 응답 모두 `application/json` 사용 (단, `/login` 폼 전송은 `application/x-www-form-urlencoded` 지원)
* **공통 에러 응답 형식**:
  ```json
  {
    "error": "상세 에러 메시지 문자열"
  }
  ```
  * 주요 상태 코드: `400 Bad Request`(입력값 오류), `401 Unauthorized`(비로그인), `403 Forbidden`(권한/출처 오류), `404 Not Found`(대상 없음), `409 Conflict`(미접속/미준비 상태), `422 Unprocessable Entity`(유효성 실패), `502/504`(외부 `ytdlp-server` 연동 오류)

---

### 2.2 전체 API 엔드포인트 요약표

| 분류 | Method | Endpoint | 설명 | 인증 필요 |
| :--- | :---: | :--- | :--- | :---: |
| **인증/계정** | `POST` | `/login` | 로그인 및 미가입 아이디 자동 회원가입 | X |
| **인증/계정** | `POST` | `/logout` | 실시간 라운지 연결 종료 및 로그아웃 | O |
| **회원/프로필** | `GET` | `/api/me` | 내 프로필 정보(아이디, 닉네임, 태그, 권한 등) 조회 | O |
| **회원/프로필** | `PATCH` | `/api/me` | 내 닉네임, 취향 태그, 프로필 이미지, 비밀번호 수정 | O |
| **회원/프로필** | `DELETE` | `/api/me` | 회원 탈퇴 (연관 플레이리스트·좋아요 CASCADE 삭제) | O |
| **회원/프로필** | `GET` | `/api/me/likes` | 내가 좋아요 누른 플레이리스트 목록 조회 | O |
| **회원/프로필** | `GET` | `/api/members/<member_id>` | 특정 참여자 프로필 및 공개 플레이리스트 목록 조회 | X |
| **라운지** | `GET` | `/api/rooms` | 전체 장르 라운지 목록 및 현재 접속 인원수 조회 | X |
| **라운지** | `GET` | `/api/rooms/<room>` | 특정 라운지 상세 정보, 참여자 목록, Top 3 랭킹 조회 | X |
| **라운지** | `GET` | `/api/rooms/<room>/playlists` | 해당 라운지에 공유된 플레이리스트 목록 및 랭킹 조회 | X |
| **라운지** | `POST` | `/api/rooms/<room>/playlists` | 플레이리스트 생성과 동시에 해당 라운지에 즉시 공유 | O |
| **라운지** | `POST` | `/api/rooms/<room>/playlists/<id>/like` | 플레이리스트 좋아요 토글 (추가/취소) | O |
| **내 보관함** | `GET` | `/api/me/playlists` | 내 보관함 플레이리스트 목록 조회 | O |
| **내 보관함** | `POST` | `/api/me/playlists` | 내 보관함에 새 플레이리스트 생성 | O |
| **내 보관함** | `PATCH` | `/api/me/playlists/<id>` | 내 플레이리스트 제목·장르·수록곡 수정 | O |
| **내 보관함** | `DELETE` | `/api/me/playlists/<id>` | 내 플레이리스트 삭제 | O |
| **내 보관함** | `PUT` | `/api/me/playlists/<id>/share` | 내 플레이리스트의 특정 라운지 공유 설정/해제 | O |
| **내 보관함** | `POST` | `/api/playlists/<id>/copy` | 타인의 공유 플레이리스트를 내 보관함으로 복제 | O |
| **음원/유튜브** | `POST` | `/api/import` | 유튜브 영상/재생목록 URL에서 곡 메타데이터 추출 | O |
| **음원/유튜브** | `GET` | `/api/youtube/search` | 유튜브 키워드 곡 검색 (`ytdlp-server` 프록시) | O |
| **음원/유튜브** | `POST`, `GET` | `/api/playlists/<id>/audio/<video_id>` | MP3 변환 요청(`POST`) 및 변환 준비 상태 확인(`GET`) | O |
| **음원/유튜브** | `GET` | `/api/playlists/<id>/audio/<video_id>/file` | 변환된 MP3 오디오 파일 스트리밍 (`Range` 지원) | O |
| **통합 검색** | `GET` | `/api/search` | 제목, 닉네임, 취향 태그, 수록곡/가수 통합 검색 | X |
| **실시간/채팅** | `GET` | `/api/rooms/<room>/events` | 라운지 실시간 SSE 이벤트 스트림 구독 | O |
| **실시간/채팅** | `GET`, `POST` | `/api/rooms/<room>/messages` | 라운지 최근 채팅 100건 조회(`GET`) 및 메시지 전송(`POST`) | O |
| **실시간/채팅** | `POST` | `/api/rooms/<room>/sync` | 라운지 내 실시간 동시 청취(`sync_play`) 재생 위치 동기화 | O |
| **실시간/채팅** | `POST` | `/api/rooms/<room>/leave` | 라운지 현재 연결 명시적 퇴장 | O |

---

### 2.3 도메인별 상세 API 명세

#### 1) 인증 및 회원 프로필 API (`users` 연동)

##### ① 로그인 및 자동 회원가입 (`POST /login`)
* **설명**: 기존 계정이 존재하면 비밀번호와 상태(`ACTIVE`)를 검증하여 로그인하고, 존재하지 않는 `user_id`면 신규 계정을 생성(`INSERT INTO users`)한 뒤 즉시 로그인 세션을 발급합니다.
* **Request (`FormData` / `application/x-www-form-urlencoded`)**
  * `user_id` (필수): 영문, 숫자, 밑줄(`_`) 조합 4~20자
  * `password` (필수): 1~128자
  * `nickname` (신규 가입 시 필수): 2~20자
  * `tag` (선택): 대표 취향 태그 (예: `#힙합`)
  * `profile_image` (선택): 프로필 이미지 URL
* **Response (`200 OK` - `Accept: application/json` 요청 시)**
  ```json
  {
    "ok": true,
    "redirect_url": "/mypage"
  }
  ```

##### ② 내 프로필 조회 및 수정 (`GET /api/me`, `PATCH /api/me`)
* **`PATCH /api/me` Request Body**
  ```json
  {
    "nickname": "지민_힙합러",
    "tag": "#알앤비",
    "profile_image": "https://example.com/profile.png",
    "current_password": "기존비밀번호(비밀번호 변경 시 필수)",
    "new_password": "새비밀번호(선택)"
  }
  ```
* **`GET`, `PATCH /api/me` Response (`200 OK`)**
  ```json
  {
    "id": 1,
    "user_pk": 1,
    "user_id": "jimin123",
    "nickname": "지민_힙합러",
    "tag": "#알앤비",
    "profile_image": "https://example.com/profile.png",
    "role": "USER",
    "member_id": "9f86d081884c7d659a2feaa0"
  }
  ```

##### ③ 회원 탈퇴 (`DELETE /api/me`)
* **설명**: 현재 로그인한 회원 계정을 삭제하며, DB 외래키(`ON DELETE CASCADE`)를 통해 작성한 플레이리스트와 좋아요 이력이 함께 삭제됩니다.
* **Response (`200 OK`)**
  ```json
  {
    "ok": true,
    "message": "회원 탈퇴가 완료되었습니다."
  }
  ```

##### ④ 특정 참여자 프로필 및 공개 플레이리스트 조회 (`GET /api/members/<member_id>`)
* **설명**: 라운지에서 참여자 닉네임을 클릭했을 때 해당 유저의 공개 프로필과 공유한 플레이리스트 목록을 반환합니다. (별칭: `GET /api/users/<user_id>/playlists`)
* **Response (`200 OK`)**
  ```json
  {
    "member_id": "9f86d081884c7d659a2feaa0",
    "nickname": "지민",
    "tag": "#힙합",
    "profile_image": null,
    "playlists": [
      {
        "id": 9,
        "title": "Good Vibes Only",
        "user": "지민",
        "likes": 15,
        "shared_rooms": ["hiphop"],
        "songs": [ ... ]
      }
    ]
  }
  ```

---

#### 2) 장르별 라운지 및 랭킹 API (`lounges`, `playlists`, `playlist_likes` 연동)

##### ① 전체 장르 라운지 목록 조회 (`GET /api/rooms`)
* **설명**: `lounges` 테이블의 모든 장르 라운지 정보와 실시간 접속자 수(`current_users`)를 조회합니다.
* **Response (`200 OK`)**
  ```json
  {
    "ballad": {
      "id": 1,
      "room": "ballad",
      "name": "발라드",
      "emoji": "🌙",
      "description": "🌙 발라드 라운지",
      "thumbnail_image": null,
      "current_users": 3
    },
    "hiphop": {
      "id": 3,
      "room": "hiphop",
      "name": "힙합",
      "emoji": "🔥",
      "description": "🔥 힙합 라운지",
      "thumbnail_image": null,
      "current_users": 5
    }
  }
  ```

##### ② 특정 라운지 상세 정보 및 참여자·Top 3 랭킹 조회 (`GET /api/rooms/<room>`)
* **Response (`200 OK`)**
  ```json
  {
    "id": 3,
    "room": "hiphop",
    "name": "힙합",
    "emoji": "🔥",
    "description": "🔥 힙합 라운지",
    "thumbnail_image": null,
    "current_users": 2,
    "participants": [
      {
        "member_id": "9f86d081884c7d659a2feaa0",
        "nickname": "지민",
        "tag": "#힙합",
        "profile_image": null
      }
    ],
    "rankings": [
      {
        "rank": 1,
        "id": 9,
        "title": "새벽 드라이브 힙합 모음",
        "user": "지민",
        "likes": 15
      }
    ]
  }
  ```

##### ③ 플레이리스트 좋아요 토글 (`POST /api/rooms/<room>/playlists/<int:playlist_id>/like`)
* **설명**: 로그인한 회원이 해당 플레이리스트에 좋아요를 누르지 않았다면 `playlist_likes`에 추가(`likes + 1`)하고, 이미 눌렀다면 취소(`likes - 1`)합니다.
* **Response (`200 OK`)**
  ```json
  {
    "likes": 16,
    "liked": true
  }
  ```

---

#### 3) 내 플레이리스트 보관함 API (`playlists` 연동)

##### ① 플레이리스트 생성 (`POST /api/me/playlists` 또는 `POST /api/rooms/<room>/playlists`)
* **설명**: `/api/me/playlists` 호출 시 미공유 상태(`shared_rooms: []`)로 보관함에 생성하며, `/api/rooms/<room>/playlists` 호출 시 생성과 동시에 해당 라운지에 공유합니다.
* **Request Body**
  ```json
  {
    "title": "Good Vibes Only",
    "room": "hiphop",
    "songs": [
      {
        "video_id": "ba7mB8oueCY",
        "url": "https://www.youtube.com/watch?v=ba7mB8oueCY",
        "title": "SUN GOES DOWN",
        "artist": "Khalid",
        "duration": 185
      }
    ]
  }
  ```
* **Response (`201 Created`)**
  ```json
  {
    "id": 9,
    "member_id": "9f86d081884c7d659a2feaa0",
    "user": "지민",
    "title": "Good Vibes Only",
    "room": "hiphop",
    "room_name": "힙합",
    "shared_rooms": [],
    "likes": 0,
    "liked_by_me": false,
    "songs": [ ... ]
  }
  ```

##### ② 라운지 공유 설정/해제 (`PUT /api/me/playlists/<int:playlist_id>/share`)
* **Request Body**
  ```json
  {
    "room": "hiphop",
    "enabled": true
  }
  ```
* **Response (`200 OK`)**
  ```json
  {
    "ok": true,
    "playlist": { ... }
  }
  ```

##### ③ 플레이리스트 내 보관함으로 복제 (`POST /api/playlists/<int:playlist_id>/copy`)
* **Request Body (선택)**
  ```json
  {
    "title": "Good Vibes Only (내 보관함)"
  }
  ```
* **Response (`201 Created`)**: 복제되어 새로 생성된 내 플레이리스트 객체 반환

---

#### 4) 유튜브 연동 및 MP3 오디오 스트리밍 API (`ytdlp-server` 연동)

##### ① 유튜브 영상/재생목록 메타데이터 가져오기 (`POST /api/import`)
* **설명**: 유튜브 단일 영상 또는 공개 재생목록 URL을 받아 내부 `ytdlp-server`(`GET http://ytdlp-server:5001/api/extract`)를 통해 최대 100곡의 메타데이터를 추출합니다.
* **Request Body**
  ```json
  {
    "url": "https://www.youtube.com/playlist?list=PLxxxxxx"
  }
  ```
* **Response (`200 OK`)**
  ```json
  {
    "title": "재생목록 제목",
    "source_url": "https://www.youtube.com/playlist?list=PLxxxxxx",
    "skipped": 0,
    "limit_reached": false,
    "tracks": [
      {
        "video_id": "ba7mB8oueCY",
        "url": "https://www.youtube.com/watch?v=ba7mB8oueCY",
        "title": "SUN GOES DOWN",
        "artist": "Khalid",
        "duration": 185
      }
    ]
  }
  ```

##### ② 유튜브 키워드 곡 검색 (`GET /api/youtube/search?q=<검색어>`)
* **설명**: 유튜브 URL 없이 가수명·곡명으로 검색하여 바로 플레이리스트에 담을 수 있도록 검색 결과 트랙 배열을 반환합니다.

##### ③ MP3 준비 요청 및 상태 조회 (`POST`, `GET /api/playlists/<int:playlist_id>/audio/<video_id>`)
* **Response (`202 Accepted` - 변환 중 / `200 OK` - 변환 완료)**
  ```json
  {
    "state": "ready",
    "url": "/api/playlists/9/audio/ba7mB8oueCY/file"
  }
  ```

---

#### 5) 통합 검색 API

##### ① 플레이리스트·태그·수록곡 통합 검색 (`GET /api/search`)
* **Query Parameters**: `?q=드라이브&room=hiphop&tag=#힙합&limit=20&offset=0`
* **설명**: 플레이리스트 제목, 작성자 닉네임, 작성자 취향 태그(`users.tag`), 수록곡 제목(`song.title`), 아티스트(`song.artist`)를 통합 검색하여 페이지네이션 결과와 함께 반환합니다.

---

#### 6) 실시간 라운지 채팅·동기화 및 SSE 이벤트 스트림 API (`messages`, `realtime.py` 연동)

##### ① 실시간 이벤트 스트림 구독 (`GET /api/rooms/<room>/events?connection=<토큰>`)
* **설명**: `EventSource`(SSE) 연결을 통해 라운지 입장 및 실시간 이벤트를 수신합니다.
* **SSE 수신 이벤트 목록 (`text/event-stream`)**:
  | 이벤트명 (`event:`) | 발생 시점 | 데이터 (`data:`) 주요 필드 |
  | :--- | :--- | :--- |
  | **`snapshot`** | 최초 입장 직후 1회 전송 | `room`, `messages`(최근 100건), `participants`, `playlists`, `self` |
  | **`presence`** | 유저 입/퇴장, 닉네임/프로필 변경 시 | `room`, `participants`(닉네임·태그·프로필 포함), `count`(접속자 수) |
  | **`message`** | 라운지에 새 채팅 메시지 등록 시 | `room`, `message`(`id`, `member_id`, `user`, `text`, `created_at`) |
  | **`playlists_changed`** | 플리 공유/수정/삭제/좋아요 변동 시 | `room` (클라이언트가 목록/랭킹 API를 재조회하도록 알림) |
  | **`sync_play`** | 호스트가 동시 청취 재생 동기화 호출 시 | `room`, `host_member_id`, `host_nickname`, `playlist_id`, `video_id`, `position_sec`, `synced_at` |
  | **`closed`** | 로그아웃, 명시적 퇴장, 연결 종료 시 | `{}` |

##### ② 채팅 메시지 전송 (`POST /api/rooms/<room>/messages`)
* **Request Body**
  ```json
  {
    "text": "이 노래 진짜 좋다..!"
  }
  ```
* **Response (`201 Created`)**
  ```json
  {
    "id": 12,
    "member_id": "9f86d081884c7d659a2feaa0",
    "user": "지민",
    "text": "이 노래 진짜 좋다..!",
    "created_at": "2026-11-10T12:00:00+00:00"
  }
  ```

##### ③ 실시간 동시 청취 동기화 (`POST /api/rooms/<room>/sync`)
* **Request Body**
  ```json
  {
    "playlist_id": 9,
    "video_id": "ba7mB8oueCY",
    "position_sec": 45
  }
  ```
* **Response (`200 OK`)**: 라운지 내 모든 접속자에게 `sync_play` SSE 이벤트를 브로드캐스트하고 동기화 정보를 반환합니다.