import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager


@contextmanager
def connect_db():
    path = Path(os.environ.get("MUSIC_LOUNGE_DB", str(Path(__file__).parent / "instance" / "lounge.sqlite3")))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with connect_db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS accounts (user_id TEXT PRIMARY KEY, nickname TEXT NOT NULL, password_hash TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS playlists (id INTEGER PRIMARY KEY, room TEXT NOT NULL, payload TEXT NOT NULL)")

        db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, user_id TEXT NOT NULL, nickname TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room, id)")
