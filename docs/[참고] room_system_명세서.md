# MUSIC-LOUNGE 플레이리스트 함께 듣기 방(Room) 백엔드 아키텍처 및 API 명세서

본 문서는 MUSIC-LOUNGE 프로젝트에 새롭게 추가된 **플레이리스트 함께 듣기 방(Playlist Room)** 기능의 데이터베이스 설계, 백엔드 서버 로직, REST API 및 실시간 SSE 이벤트 규격을 정리한 문서입니다.

---

## 1. 아키텍처 및 동작 원리

### (1) 시스템 계층 구조
1. **장르 라운지 (Lounge):** 기존 대분류 공간 (예: `ballad`, `hiphop`, `rnb`, `indie`).
2. **플레이리스트 (Playlist):** 사용자가 유튜브 링크 기반으로 생성하여 라운지에 공유한 곡 목록.
3. **함께 듣기 방 (Playlist Room):** 라운지에 공유된 플레이리스트를 기반으로 개설되는 실시간 동시 청취 및 전용 소통 공간.

### (2) 핵심 동작 메커니즘
- **호스트 주도형 재생 동기화:** 방을 개설한 사용자가 호스트(Host/DJ)가 되며, 재생/일시정지/구간이동/곡변경 권한을 독점합니다.
- **경과 시간 보정 (Time Drift Compensation):** 서버는 무거운 음원 바이너리를 스트리밍하지 않고, `synced_at`(서버 기준 시각)과 `current_position_sec`(기준 재생 초) 메타데이터를 관리합니다. 클라이언트가 접속하거나 상태를 조회할 때 `현재 시각 - synced_at`의 경과 초를 더해 정확한 재생 초를 산출합니다.
- **채팅 채널의 완전 분리:** 기존 라운지 전체 채팅(`messages`)과 별도로, 방 내부 전용 채팅 테이블(`room_messages`)과 독립 SSE 이벤트를 통해 방 참여자들만의 실시간 대화를 지원합니다.

---

## 2. 데이터베이스 스키마 변경 및 추가 명세

### (1) 플레이리스트 함께 듣기 방 (`playlist_rooms`)
방의 메타데이터와 실시간 재생 상태를 영구/준영구 보관합니다.

| 컬럼명 | 데이터 타입 | 제약 조건 | 기본값 | 설명 |
| :--- | :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 자동 증가 | 방 고유 식별 번호 |
| `lounge_room` | TEXT | NOT NULL, FK -> lounges(room) | - | 방이 개설된 장르 라운지 코드 (`hiphop` 등) |
| `playlist_id` | INTEGER | NOT NULL, FK -> playlists(id) ON DELETE CASCADE | - | 재생 대상 플레이리스트 ID |
| `host_id` | INTEGER | NOT NULL, FK -> users(id) ON DELETE CASCADE | - | 방 호스트(개설자)의 회원 번호 (`users.id`) |
| `title` | TEXT | NOT NULL | - | 방 표시 제목 (기본: 플레이리스트 제목) |
| `status` | TEXT | NOT NULL CHECK(status IN ('ACTIVE', 'PAUSED', 'CLOSED')) | 'ACTIVE' | 방 활성화 상태 |
| `current_track_index` | INTEGER | NOT NULL | 0 | 현재 재생 중인 곡의 인덱스 (0부터 시작) |
| `current_video_id` | TEXT | NULL | NULL | 현재 재생 중인 곡의 유튜브 Video ID |
| `current_position_sec`| REAL | NOT NULL | 0.0 | 마지막 기준 재생 위치 (초 단위) |
| `is_playing` | INTEGER | NOT NULL CHECK(is_playing IN (0, 1)) | 1 | 1: 재생 중, 0: 일시 정지 |
| `synced_at` | TEXT | NOT NULL | - | 마지막 상태 변경 UTC ISO-8601 타임스탬프 |
| `created_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 방 생성 일시 |
| `updated_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 최종 상태 수정 일시 |

### (2) 방 내부 전용 채팅 (`room_messages`)
방 참여자 간에 오가는 실시간 대화 내역을 격리 보관합니다.

| 컬럼명 | 데이터 타입 | 제약 조건 | 설명 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 메시지 고유 번호 |
| `room_id` | INTEGER | NOT NULL, FK -> playlist_rooms(id) ON DELETE CASCADE | 소속 방 번호 |
| `user_id` | TEXT | NOT NULL | 작성자 로그인 아이디 (`users.user_id`) |
| `nickname` | TEXT | NOT NULL | 메시지 작성 시점의 닉네임 |
| `text` | TEXT | NOT NULL (1~300자) | 채팅 본문 내용 |
| `created_at` | TEXT | NOT NULL | 작성 시각 (UTC ISO-8601) |

---

## 3. 백엔드 REST API 및 실시간 통신 명세

### 3.1. 라운지 내 방 목록 조회 및 방 생성
#### ① 라운지 내 활성 방 목록 조회
- **Method & Path:** `GET /api/rooms/<room>/playlist-rooms`
- **설명:** 해당 장르 라운지에서 현재 개설되어 음악을 재생 중인 활성 방 목록을 조회합니다.
- **Response (200 OK):**
```json
{
  "success": true,
  "data": [
    {
      "room_id": 1,
      "title": "DJ 지민의 새벽 힙합방",
      "lounge_room": "hiphop",
      "playlist_id": 10,
      "playlist_title": "Good Vibes Only",
      "host": {
        "user_pk": 1,
        "nickname": "지민",
        "member_id": "9f86d081884c7d659a2feaa0"
      },
      "current_track": {
        "title": "SUN GOES DOWN",
        "artist": "Khalid",
        "video_id": "ba7mB8oueCY",
        "duration": 185
      },
      "is_playing": true,
      "participant_count": 5,
      "created_at": "2026-11-13T06:00:00Z"
    }
  ]
}
```

#### ② 플레이리스트 기반 방 개설 (호스트)
- **Method & Path:** `POST /api/rooms/<room>/playlist-rooms`
- **Request Body (JSON):**
```json
{
  "playlist_id": 10,
  "title": "DJ 지민의 새벽 힙합방"
}
```
- **Response (201 Created):** 개설된 방 상세 정보 및 `is_host: true` 반환. 라운지에 `playlist_room_created` 알림 전파.

---

### 3.2. 방 입장, 상세 정보, 대기열 및 참여자 조회
#### ① 방 상세 정보 및 대기열/참여자 조회
- **Method & Path:** `GET /api/playlist-rooms/<int:room_id>`
- **설명:** 방에 입장했을 때 현재 재생 중인 곡, 재생 시간(서버 경과 시간 보정값), 재생 예정 목록, 참여자 목록 및 인원 수를 조회합니다.
- **Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "room_id": 1,
    "title": "DJ 지민의 새벽 힙합방",
    "lounge_room": "hiphop",
    "playlist_id": 10,
    "playlist_title": "Good Vibes Only",
    "host": {
      "user_pk": 1,
      "nickname": "지민",
      "member_id": "9f86d081884c7d659a2feaa0"
    },
    "is_host": false,
    "playback": {
      "current_track": {
        "title": "SUN GOES DOWN",
        "artist": "Khalid",
        "video_id": "ba7mB8oueCY",
        "duration": 185
      },
      "current_track_index": 0,
      "current_position_sec": 42.5,
      "is_playing": true,
      "synced_at": "2026-11-13T06:10:00Z"
    },
    "upcoming_tracks": [
      {
        "title": "STARGAZING",
        "artist": "Travis Scott",
        "video_id": "vid002",
        "duration": 270
      },
      {
        "title": "SICKO MODE",
        "artist": "Travis Scott",
        "video_id": "vid003",
        "duration": 312
      }
    ],
    "participant_count": 3,
    "participants": [
      {"member_id": "9f86d081...", "nickname": "지민", "is_host": true},
      {"member_id": "a3b2c1d0...", "nickname": "도윤", "is_host": false},
      {"member_id": "e5f6g7h8...", "nickname": "서연", "is_host": false}
    ]
  }
}
```

---

### 3.3. 호스트 전용 재생 제어 API
호스트 권한 검증(`session['user_pk'] == room['host_id']`)을 수행하며, 비인가 사용자의 요청은 `403 Forbidden`을 반환합니다.

#### ① 재생 제어 (일시정지, 재개, 탐색, 곡 변경, 다음/이전 곡)
- **Method & Path:** `POST /api/playlist-rooms/<int:room_id>/control`
- **Request Body (JSON):**
  - **재생 재개:** `{"action": "play", "position_sec": 45.0}`
  - **일시 정지:** `{"action": "pause", "position_sec": 45.0}`
  - **구간 이동 (Seek):** `{"action": "seek", "position_sec": 90.0}`
  - **특정 곡 변경:** `{"action": "change_track", "track_index": 2}`
  - **다음 곡 / 이전 곡:** `{"action": "next_track"}` 또는 `{"action": "prev_track"}`
- **동작:** DB 상태 즉시 업데이트 및 해당 방의 모든 접속자에게 `room_sync` SSE 이벤트 브로드캐스트.
- **Response (200 OK):** `{"ok": true, "sync": { ... }}`

#### ② 방 종료
- **Method & Path:** `DELETE /api/playlist-rooms/<int:room_id>`
- **설명:** 호스트가 방을 닫습니다. `status = 'CLOSED'` 처리 후 접속자들에게 `room_closed` 이벤트를 푸시합니다.

---

### 3.4. 방 내부 전용 채팅 API
#### ① 방 채팅 내역 조회
- **Method & Path:** `GET /api/playlist-rooms/<int:room_id>/messages`
- **Response (200 OK):** `[{"id": 1, "member_id": "...", "user": "도윤", "text": "안녕하세요!", "created_at": "..."}]`

#### ② 방 채팅 전송
- **Method & Path:** `POST /api/playlist-rooms/<int:room_id>/messages`
- **Request Body (JSON):** `{"text": "방 안에서만 공유되는 메시지입니다."}`
- **동작:** `room_messages` 테이블 저장 및 방 접속자들에게 `room_message` SSE 이벤트 푸시.

---

### 3.5. 방 실시간 이벤트 스트림 (SSE)
- **Method & Path:** `GET /api/playlist-rooms/<int:room_id>/events?connection=<토큰>`
- **이벤트 목록:**
  1. `room_snapshot`: 방 입장 직후 1회 전송 (현재 재생 상태, 예정곡, 참여자, 최근 대화 100건)
  2. `room_sync`: 호스트의 제어로 재생/정지/탐색/곡변경 시 방 전체 브로드캐스트
  3. `room_message`: 방 내부 신규 채팅 발생 시 브로드캐스트
  4. `room_presence`: 참여자 입/퇴장 시 인원수 및 목록 갱신
  5. `room_closed`: 호스트가 방을 종료했을 때 전송
