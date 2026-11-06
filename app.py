
from flask import Flask, render_template, request, jsonify
from threading import Lock

app = Flask(__name__)
lock = Lock()


# 장르별 라운지 정보
ROOMS = {
    "ballad": {
        "name": "발라드",
        "emoji": "🌙"
    },
    "rnb": {
        "name": "R&B",
        "emoji": "💜"
    },
    "hiphop": {
        "name": "힙합",
        "emoji": "🔥"
    },
    "indie": {
        "name": "인디",
        "emoji": "🌿"
    }
}


# 장르별 기본 플레이리스트
PLAYLISTS = {
    "ballad": [
        {
            "id": 1,
            "user": "달빛소리",
            "title": "새벽 감성 모음",
            "songs": [
                "밤편지 — 아이유",
                "너의 모든 순간 — 성시경",
                "첫눈처럼 너에게 가겠다 — 에일리"
            ],
            "likes": 12
        },
        {
            "id": 2,
            "user": "별헤는밤",
            "title": "혼자 듣기 좋은 노래",
            "songs": [
                "그대라는 시 — 태연",
                "사랑했지만 — 김광석"
            ],
            "likes": 8
        }
    ],

    "rnb": [
        {
            "id": 3,
            "user": "Groovy",
            "title": "Midnight Groove",
            "songs": [
                "Instagram — DEAN",
                "D (half moon) — DEAN",
                "WA-R-R — Colde"
            ],
            "likes": 19
        },
        {
            "id": 4,
            "user": "Soulmate",
            "title": "부드러운 밤",
            "songs": [
                "ONLY — 이하이",
                "우주를 건너 — 백예린"
            ],
            "likes": 11
        }
    ],

    "hiphop": [
        {
            "id": 5,
            "user": "BeatMaker",
            "title": "오늘의 힙합",
            "songs": [
                "붕붕 — HAON",
                "METEOR — 창모"
            ],
            "likes": 21
        },
        {
            "id": 6,
            "user": "FlowKing",
            "title": "Drive & Rap",
            "songs": [
                "Aqua Man — 빈지노",
                "시차 — 우원재"
            ],
            "likes": 14
        }
    ],

    "indie": [
        {
            "id": 7,
            "user": "초록빛",
            "title": "산책할 때",
            "songs": [
                "주저하는 연인들을 위해 — 잔나비",
                "TOMBOY — 혁오"
            ],
            "likes": 10
        },
        {
            "id": 8,
            "user": "작은별",
            "title": "조용한 오후",
            "songs": [
                "나랑 아니면 — 검정치마",
                "긴 꿈 — 새소년"
            ],
            "likes": 7
        }
    ]
}


# 각 라운지의 채팅 메시지
MESSAGES = {
    room: [
        {
            "user": "LOUNGE BOT",
            "text": f'{info["name"]} 라운지에 오신 걸 환영해요!'
        }
    ]
    for room, info in ROOMS.items()
}


# 메인 페이지
@app.get("/")
def index():
    return render_template("index.html")


# 라운지 목록 조회
@app.get("/api/rooms")
def rooms():
    return jsonify(ROOMS)


# 장르별 플레이리스트 조회 및 랭킹 정렬
@app.get("/api/rooms/<room>/playlists")
def playlists(room):

    if room not in ROOMS:
        return jsonify({
            "error": "존재하지 않는 라운지입니다."
        }), 404

    with lock:
        sorted_playlists = sorted(
            PLAYLISTS[room],
            key=lambda p: (-p["likes"], p["id"])
        )

        return jsonify(sorted_playlists)


# 플레이리스트 좋아요 기능
@app.post("/api/rooms/<room>/playlists/<int:playlist_id>/like")
def like(room, playlist_id):

    if room not in ROOMS:
        return jsonify({
            "error": "존재하지 않는 라운지입니다."
        }), 404

    with lock:
        playlist = next(
            (
                p for p in PLAYLISTS[room]
                if p["id"] == playlist_id
            ),
            None
        )

        if playlist is None:
            return jsonify({
                "error": "플레이리스트를 찾을 수 없습니다."
            }), 404

        playlist["likes"] += 1

        return jsonify({
            "likes": playlist["likes"]
        })


# 채팅 메시지 조회 및 전송
@app.route(
    "/api/rooms/<room>/messages",
    methods=["GET", "POST"]
)
def messages(room):

    if room not in ROOMS:
        return jsonify({
            "error": "존재하지 않는 라운지입니다."
        }), 404

    # 채팅 메시지 불러오기
    if request.method == "GET":

        with lock:
            return jsonify(MESSAGES[room][-50:])

    # 새로운 채팅 메시지 전송
    data = request.get_json(silent=True) or {}

    user = str(data.get("user", "")).strip()[:20]
    message = str(data.get("text", "")).strip()[:300]

    if not user or not message:
        return jsonify({
            "error": "닉네임과 메시지를 입력해주세요."
        }), 400

    with lock:
        MESSAGES[room].append({
            "user": user,
            "text": message
        })

        MESSAGES[room] = MESSAGES[room][-100:]

    return jsonify({
        "ok": True
    }), 201


# 새로운 플레이리스트 생성
@app.post("/api/rooms/<room>/playlists")
def create_playlist(room):

    if room not in ROOMS:
        return jsonify({
            "error": "존재하지 않는 라운지입니다."
        }), 404

    data = request.get_json(silent=True) or {}

    user = str(data.get("user", "")).strip()[:20]
    title = str(data.get("title", "")).strip()[:60]
    songs = data.get("songs", [])

    # 입력 데이터 검사
    invalid_songs = (
        not isinstance(songs, list)
        or not (1 <= len(songs) <= 20)
    )

    if not invalid_songs:
        invalid_songs = any(
            not isinstance(song, str)
            or not song.strip()
            or len(song) > 100
            for song in songs
        )

    if not user or not title or invalid_songs:
        return jsonify({
            "error": "닉네임, 제목, 노래(1~20곡)를 확인해주세요."
        }), 400

    # 새로운 플레이리스트 등록
    with lock:
        new_id = max(
            p["id"]
            for group in PLAYLISTS.values()
            for p in group
        ) + 1

        playlist = {
            "id": new_id,
            "user": user,
            "title": title,
            "songs": [
                song.strip()
                for song in songs
            ],
            "likes": 0
        }

        PLAYLISTS[room].append(playlist)

    return jsonify(playlist), 201


# Flask 서버 실행
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
