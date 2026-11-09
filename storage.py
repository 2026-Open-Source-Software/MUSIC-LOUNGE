import os
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from sample_data import ROOMS


@contextmanager
def connect_db():
    default_path = str(Path(__file__).parent / "instance" / "lounge.sqlite3")
    path = Path(os.environ.get("DB_PATH") or os.environ.get("MUSIC_LOUNGE_DB") or default_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON;")
    db.execute("PRAGMA journal_mode = WAL;")
    db.execute("PRAGMA busy_timeout = 5000;")
    try:
        with db:
            yield db
    finally:
        db.close()


def _migrate_legacy_db(db):
    """구버전(accounts, payload) 또는 이전 컬럼명(login_id, like_count 등) 테이블을 새 속성명 스키마로 마이그레이션"""
    tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    # 1. accounts -> users (user_id, password_hash, nickname ...) 마이그레이션
    if "accounts" in tables and "users" not in tables:
        db.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                nickname TEXT NOT NULL,
                tag TEXT,
                profile_image TEXT,
                role TEXT NOT NULL DEFAULT 'USER' CHECK(role IN ('USER', 'ADMIN')),
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'LOCKED', 'DELETED'))
            )
        """)
        db.execute("""
            INSERT INTO users (user_id, nickname, password_hash)
            SELECT user_id, nickname, password_hash FROM accounts
        """)
        db.execute("DROP TABLE accounts")
    elif "users" in tables:
        user_cols = {r["name"] for r in db.execute("PRAGMA table_info(users)").fetchall()}
        if "login_id" in user_cols and "user_id" not in user_cols:
            db.execute("ALTER TABLE users RENAME COLUMN login_id TO user_id;")

    # 2. lounges (genre -> room) 마이그레이션
    if "lounges" in tables:
        lounge_cols = {r["name"] for r in db.execute("PRAGMA table_info(lounges)").fetchall()}
        if "genre" in lounge_cols and "room" not in lounge_cols:
            db.execute("ALTER TABLE lounges RENAME COLUMN genre TO room;")

    # 3. playlists (payload 구버전 또는 user_id/youtubeURL/like_count 버전 -> owner_id/songs/likes) 마이그레이션
    if "playlists" in tables:
        pl_cols = {r["name"] for r in db.execute("PRAGMA table_info(playlists)").fetchall()}
        if "payload" in pl_cols and "title" not in pl_cols:
            old_rows = db.execute("SELECT id, room, payload FROM playlists").fetchall()
            db.execute("DROP TABLE playlists")
            db.execute("""
                CREATE TABLE playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    songs TEXT NOT NULL,
                    likes INTEGER NOT NULL DEFAULT 0 CHECK(likes >= 0),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            for row in old_rows:
                p = json.loads(row["payload"])
                owner_str = p.get("owner_id")
                user_row = db.execute("SELECT id FROM users WHERE user_id = ?", (owner_str,)).fetchone()
                if user_row:
                    songs_data = json.dumps({
                        "room": row["room"],
                        "shared_rooms": p.get("shared_rooms", [row["room"]]),
                        "songs": p.get("songs", [])
                    }, ensure_ascii=False)
                    db.execute(
                        "INSERT INTO playlists (id, owner_id, title, songs, likes) VALUES (?, ?, ?, ?, ?)",
                        (row["id"], user_row["id"], p.get("title", "Untitled"), songs_data, p.get("likes", 0))
                    )
        else:
            if "user_id" in pl_cols and "owner_id" not in pl_cols:
                db.execute("ALTER TABLE playlists RENAME COLUMN user_id TO owner_id;")
            if "youtubeURL" in pl_cols and "songs" not in pl_cols:
                db.execute("ALTER TABLE playlists RENAME COLUMN youtubeURL TO songs;")
            if "like_count" in pl_cols and "likes" not in pl_cols:
                db.execute("ALTER TABLE playlists RENAME COLUMN like_count TO likes;")


def init_db():
    with connect_db() as db:
        _migrate_legacy_db(db)

        # 1. 회원 정보 (users) - login_id 대신 새 파일의 user_id 사용
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                nickname TEXT NOT NULL,
                tag TEXT,
                profile_image TEXT,
                role TEXT NOT NULL DEFAULT 'USER' CHECK(role IN ('USER', 'ADMIN')),
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'LOCKED', 'DELETED'))
            )
        """)

        # 2. 장르별 라운지 (lounges) - genre 대신 새 파일의 room 사용
        db.execute("""
            CREATE TABLE IF NOT EXISTS lounges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                thumbnail_image TEXT
            )
        """)

        # 3. 플레이리스트 (playlists) - owner_id, title, songs, likes 사용
        db.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                songs TEXT NOT NULL,
                likes INTEGER NOT NULL DEFAULT 0 CHECK(likes >= 0),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # 4. 플레이리스트 좋아요 매핑 (playlist_likes)
        db.execute("""
            CREATE TABLE IF NOT EXISTS playlist_likes (
                user_id INTEGER NOT NULL,
                playlist_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, playlist_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
            )
        """)

        # 5. 성능 최적화 인덱스
        db.execute("CREATE INDEX IF NOT EXISTS idx_playlists_owner_id ON playlists(owner_id);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_playlists_likes ON playlists(likes DESC);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_playlist_likes_playlist_id ON playlist_likes(playlist_id);")

        # 6. 자동화 트리거 (새 컬럼명 songs, likes 기준으로 재생성)
        db.execute("DROP TRIGGER IF EXISTS update_playlist_timestamp;")
        db.execute("DROP TRIGGER IF EXISTS trg_like_add;")
        db.execute("DROP TRIGGER IF EXISTS trg_like_remove;")

        db.execute("""
            CREATE TRIGGER update_playlist_timestamp
            AFTER UPDATE OF title, songs ON playlists
            FOR EACH ROW
            BEGIN
                UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
            END;
        """)
        db.execute("""
            CREATE TRIGGER trg_like_add
            AFTER INSERT ON playlist_likes
            FOR EACH ROW
            BEGIN
                UPDATE playlists SET likes = likes + 1 WHERE id = NEW.playlist_id;
            END;
        """)
        db.execute("""
            CREATE TRIGGER trg_like_remove
            AFTER DELETE ON playlist_likes
            FOR EACH ROW
            BEGIN
                UPDATE playlists
                SET likes = CASE WHEN likes > 0 THEN likes - 1 ELSE 0 END
                WHERE id = OLD.playlist_id;
            END;
        """)

        # 7. 기본 라운지 시드 데이터 동기화
        for room_key, info in ROOMS.items():
            db.execute(
                "INSERT OR IGNORE INTO lounges (room, name, description) VALUES (?, ?, ?)",
                (room_key, info["name"], f"{info['emoji']} {info['name']} 라운지")
            )

        # 8. 실시간 채팅 메시지 보관 테이블
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                user_id TEXT NOT NULL,
                nickname TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room, id);")

        if __name__ == "__main__":
    init_db()
    print("[DB 초기화 완료] 4개 핵심 테이블 및 채팅 테이블, 트리거 구축 완료")