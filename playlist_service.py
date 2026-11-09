"""Playlist validation and SQLite persistence using the migrated 4-table schema."""
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs
from storage import connect_db
from sample_data import ROOMS, SAMPLE_PLAYLISTS


class ValidationError(ValueError):
    pass


def member_id(user_id):
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:24]


def youtube_track(value):
    if isinstance(value, str):
        title, separator, url = value.rpartition(' | ')
        if not separator:
            url, title = value, ''
    elif isinstance(value, dict):
        title, url = value.get('title', ''), value.get('url', '')
    else:
        raise ValidationError('유튜브 링크를 입력해주세요.')
    if not isinstance(url, str) or not isinstance(title, str) or len(url) > 2048 or len(title) > 120:
        raise ValidationError('유튜브 링크와 곡 제목(120자 이하)을 확인해주세요.')
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or '').lower()
        if parts.scheme not in ('http', 'https') or parts.username or parts.password or parts.port:
            raise ValueError()
        if host in ('youtu.be', 'www.youtu.be'):
            video_id = parts.path.strip('/')
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            path = parts.path.strip('/').split('/')
            video_id = parse_qs(parts.query).get('v', [''])[0] if parts.path == '/watch' else (
                path[1] if len(path) == 2 and path[0] in ('embed', 'shorts', 'live') else ''
            )
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
    except ValueError:
        raise ValidationError('올바른 유튜브 동영상 링크를 입력해주세요. watch, youtu.be, shorts 링크를 사용할 수 있어요.') from None
    track = {
        'title': title.strip() or f'YouTube · {video_id}',
        'url': f'https://www.youtube.com/watch?v={video_id}',
        'video_id': video_id
    }
    if isinstance(value, dict):
        if isinstance(value.get('artist'), str):
            track['artist'] = value['artist'][:200]
        duration = value.get('duration')
        if isinstance(duration, (int, float)) and 0 <= duration <= 604800:
            track['duration'] = int(duration)
    return track


def _serialize_songs_col(room, shared_rooms, songs):
    return json.dumps({
        'room': room,
        'shared_rooms': shared_rooms,
        'songs': songs
    }, ensure_ascii=False)


def _deserialize_songs_col(raw_value):
    """playlists.songs 컬럼의 JSON(room, shared_rooms, songs) 또는 단일 URL 문자열 파싱"""
    default_room = next(iter(ROOMS))
    if not raw_value:
        return default_room, [default_room], []
    raw_value = raw_value.strip()
    if raw_value.startswith('{'):
        try:
            data = json.loads(raw_value)
            room = data.get('room') if data.get('room') in ROOMS else default_room
            shared = [r for r in data.get('shared_rooms', [room]) if r in ROOMS]
            return room, shared, data.get('songs', [])
        except ValueError:
            pass
    elif raw_value.startswith('['):
        try:
            songs = json.loads(raw_value)
            return default_room, [default_room], songs
        except ValueError:
            pass
    try:
        track = youtube_track(raw_value)
        return default_room, [default_room], [track]
    except ValidationError:
        return default_room, [default_room], [raw_value]


def validate_fields(data, existing=None):
    if not isinstance(data, dict):
        raise ValidationError('올바른 플레이리스트 정보를 입력해주세요.')
    title = data.get('title')
    songs = data.get('songs')
    if isinstance(songs, str):
        songs = [songs]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
        raise ValidationError('플레이리스트 이름은 1~60자로 입력해주세요.')
    if not isinstance(songs, list) or not 1 <= len(songs) <= 100:
        raise ValidationError('유튜브 링크를 1~100개 입력해주세요.')
    old_text = [s for s in (existing or {}).get('songs', []) if isinstance(s, str)]
    normalized = [s if isinstance(s, str) and s in old_text else youtube_track(s) for s in songs]
    room = data.get('room') or (existing or {}).get('room') or 'ballad'
    if isinstance(room, str):
        room = room.lower()
    if room not in ROOMS:
        raise ValidationError('장르를 선택해주세요.')
    return title.strip(), normalized, room


def _row_to_playlist(row):
    room, shared_rooms, songs = _deserialize_songs_col(row['songs'])
    return {
        'id': row['id'],
        'owner_id': row['user_id'],       # users.user_id (문자열 로그인 아이디)
        'owner_pk': row['owner_id'],      # playlists.owner_id (users.id 정수 PK)
        'user': row['nickname'],          # users.nickname (JOIN 결과)
        'title': row['title'],
        'songs': songs,
        'likes': row['likes'],
        'room': room,
        'shared_rooms': shared_rooms,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def saved_playlists():
    with connect_db() as db:
        rows = db.execute("""
            SELECT p.id, p.owner_id, p.title, p.songs, p.likes, p.created_at, p.updated_at,
                   u.user_id, u.nickname
            FROM playlists p
            JOIN users u ON p.owner_id = u.id
            ORDER BY p.id DESC
        """).fetchall()
    return [_row_to_playlist(row) for row in rows]


def public(p):
    return {
        **{k: v for k, v in p.items() if k not in ('owner_id', 'owner_pk')},
        'member_id': member_id(p['owner_id']) if p.get('owner_id') else f'sample-{p["id"]}',
        'room_name': ROOMS[p['room']]['name']
    }


def room_playlists(room):
    result = [p for p in saved_playlists() if room in p['shared_rooms']]
    samples = [{**p, 'room': room, 'shared_rooms': [room], 'sample': True} for p in SAMPLE_PLAYLISTS[room]]
    return sorted([public(p) for p in result + samples], key=lambda p: (-p['likes'], p['id']))


def own_playlists(user_id):
    return [public(p) for p in saved_playlists() if p.get('owner_id') == user_id]


def create_playlist(user_id, nickname, data, shared_room=None):
    title, songs, room = validate_fields(data)
    shared_rooms = [shared_room] if shared_room else []
    songs_payload = _serialize_songs_col(room, shared_rooms, songs)
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        user_row = db.execute('SELECT id FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if not user_row:
            raise ValidationError('사용자 정보를 찾을 수 없습니다.')
        next_id = max(8, db.execute('SELECT COALESCE(MAX(id), 0) FROM playlists').fetchone()[0]) + 1
        db.execute(
            'INSERT INTO playlists (id, owner_id, title, songs, likes) VALUES (?, ?, ?, ?, 0)',
            (next_id, user_row['id'], title, songs_payload)
        )
        row = db.execute("""
            SELECT p.id, p.owner_id, p.title, p.songs, p.likes, p.created_at, p.updated_at,
                   u.user_id, u.nickname
            FROM playlists p
            JOIN users u ON p.owner_id = u.id
            WHERE p.id = ?
        """, (next_id,)).fetchone()
    return public(_row_to_playlist(row))


def change_playlist(user_id, playlist_id, data=None, delete=False, share=None):
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("""
            SELECT p.id, p.owner_id, p.title, p.songs, p.likes, p.created_at, p.updated_at,
                   u.user_id, u.nickname
            FROM playlists p
            JOIN users u ON p.owner_id = u.id
            WHERE p.id = ?
        """, (playlist_id,)).fetchone()
        if not row or row['user_id'] != user_id:
            return None

        p = _row_to_playlist(row)
        affected = set(p['shared_rooms'])

        if delete:
            db.execute('DELETE FROM playlists WHERE id = ?', (playlist_id,))
        else:
            if share is not None:
                room, enabled = share
                if enabled and room not in p['shared_rooms']:
                    p['shared_rooms'].append(room)
                elif not enabled and room in p['shared_rooms']:
                    p['shared_rooms'].remove(room)
            else:
                p['title'], p['songs'], p['room'] = validate_fields(data, p)
            affected.update(p['shared_rooms'])
            songs_payload = _serialize_songs_col(p['room'], p['shared_rooms'], p['songs'])
            # title, songs 수정 시 update_playlist_timestamp 트리거가 updated_at을 자동 갱신
            db.execute(
                'UPDATE playlists SET title = ?, songs = ? WHERE id = ?',
                (p['title'], songs_payload, playlist_id)
            )
    return public(p), affected


def like_playlist(user_id, room, playlist_id):
    """playlist_likes 테이블에 INSERT/DELETE 하여 trg_like_add / trg_like_remove 트리거로 likes 자동 증감"""
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        user_row = db.execute('SELECT id FROM users WHERE user_id = ?', (user_id,)).fetchone()
        row = db.execute("""
            SELECT p.id, p.owner_id, p.title, p.songs, p.likes, p.created_at, p.updated_at,
                   u.user_id, u.nickname
            FROM playlists p
            JOIN users u ON p.owner_id = u.id
            WHERE p.id = ?
        """, (playlist_id,)).fetchone()

        if row and user_row:
            p = _row_to_playlist(row)
            if room and room not in p['shared_rooms']:
                return None
            existing = db.execute(
                'SELECT 1 FROM playlist_likes WHERE user_id = ? AND playlist_id = ?',
                (user_row['id'], playlist_id)
            ).fetchone()
            if existing:
                db.execute(
                    'DELETE FROM playlist_likes WHERE user_id = ? AND playlist_id = ?',
                    (user_row['id'], playlist_id)
                )
                is_liked = False
            else:
                db.execute(
                    'INSERT INTO playlist_likes (user_id, playlist_id) VALUES (?, ?)',
                    (user_row['id'], playlist_id)
                )
                is_liked = True

            updated_likes = db.execute('SELECT likes FROM playlists WHERE id = ?', (playlist_id,)).fetchone()['likes']
            return updated_likes, p['shared_rooms'], is_liked

    if room in SAMPLE_PLAYLISTS:
        p = next((p for p in SAMPLE_PLAYLISTS[room] if p['id'] == playlist_id), None)
        if p:
            return p['likes'], [room], False
    return None