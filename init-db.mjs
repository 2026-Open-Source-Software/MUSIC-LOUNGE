import sqlite3 from 'sqlite3';
import path from 'path';
import fs from 'fs';

// 데이터 저장 디렉토리 설정 (Docker Volume 연동 경로)
const dbDir = path.resolve('./data');
if (!fs.existsSync(dbDir)) {
    fs.mkdirSync(dbDir, { recursive: true });
}

const dbPath = path.join(dbDir, 'music.db');
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) {
    console.error('SQLite 연결 실패:', err.message);
  } else {
    console.log('SQLite 연결 성공:', dbPath);
    // 동시성 성능 향상을 위한 WAL 모드 활성화 및 외래 키 활성화
    db.run('PRAGMA journal_mode = WAL;');
    db.run('PRAGMA foreign_keys = ON;');
  }
});

// 명세서 기반 테이블 및 트리거 구축
db.serialize(() => {
  // 1. 회원 정보 (users)
  db.run(`
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      login_id TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      nickname TEXT NOT NULL,
      tag TEXT,
      profile_image TEXT,
      role TEXT NOT NULL DEFAULT 'USER' CHECK(role IN ('USER', 'ADMIN')),
      status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'LOCKED', 'DELETED'))
    )
  `);

  // 2. 장르별 라운지 (lounges)
  db.run(`
    CREATE TABLE IF NOT EXISTS lounges (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      genre TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      description TEXT,
      thumbnail_image TEXT
    )
  `);

  // 3. 플레이리스트 (playlists)
  db.run(`
    CREATE TABLE IF NOT EXISTS playlists (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      youtubeURL TEXT NOT NULL,
      like_count INTEGER NOT NULL DEFAULT 0 CHECK(like_count >= 0),
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
  `);

  // 4. 플레이리스트 좋아요 매핑 (playlist_likes)
  db.run(`
    CREATE TABLE IF NOT EXISTS playlist_likes (
      user_id INTEGER NOT NULL,
      playlist_id INTEGER NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (user_id, playlist_id),
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
    )
  `);

  // 5. 조회 성능 인덱스 생성
  db.run(`CREATE INDEX IF NOT EXISTS idx_playlists_user ON playlists(user_id);`);
  db.run(`CREATE INDEX IF NOT EXISTS idx_playlists_likes_ranking ON playlists(like_count DESC);`);

  // 6. 자동화 규칙: 좋아요 추가 시 like_count 증가 트리거
  db.run(`
    CREATE TRIGGER IF NOT EXISTS trg_like_add 
    AFTER INSERT ON playlist_likes
    BEGIN
      UPDATE playlists SET like_count = like_count + 1 WHERE id = NEW.playlist_id;
    END;
  `);

  // 7. 자동화 규칙: 좋아요 취소 시 like_count 감소 트리거
  db.run(`
    CREATE TRIGGER IF NOT EXISTS trg_like_remove 
    AFTER DELETE ON playlist_likes
    BEGIN
      UPDATE playlists SET like_count = like_count - 1 WHERE id = OLD.playlist_id;
    END;
  `);

  console.log('데이터베이스 명세서 테이블, 인덱스, 트리거 구축 완료');

  // 8. 테이블 생성 직후 확인
  db.all("SELECT name FROM sqlite_master WHERE type='table';", [], (err, rows) => {
    if (err) {
      console.error('테이블 조회 실패:', err.message);
    } else {
      console.log('현재 생성된 테이블 목록:', rows.map(row => row.name));
    }
  });
});

export default db;