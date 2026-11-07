"""Playlist validation and SQLite persistence, independent of Flask routes."""
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs
from storage import connect_db
from sample_data import ROOMS, SAMPLE_PLAYLISTS


class ValidationError(ValueError):
    pass


def member_id(user_id):
    return hashlib.sha256(user_id.encode()).hexdigest()[:24]


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
            video_id = parse_qs(parts.query).get('v', [''])[0] if parts.path == '/watch' else (path[1] if len(path) == 2 and path[0] in ('embed', 'shorts', 'live') else '')
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
    except ValueError:
        raise ValidationError('올바른 유튜브 동영상 링크를 입력해주세요. watch, youtu.be, shorts 링크를 사용할 수 있어요.') from None
    track = {'title': title.strip() or f'YouTube · {video_id}', 'url': f'https://www.youtube.com/watch?v={video_id}', 'video_id': video_id}
    if isinstance(value, dict):
        if isinstance(value.get('artist'), str):
            track['artist'] = value['artist'][:200]
        duration = value.get('duration')
        if isinstance(duration, (int, float)) and 0 <= duration <= 604800:
            track['duration'] = int(duration)
    return track


def validate_fields(data, existing=None):
    if not isinstance(data, dict):
        raise ValidationError('올바른 플레이리스트 정보를 입력해주세요.')
    title, songs = data.get('title'), data.get('songs')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
        raise ValidationError('플레이리스트 이름은 1~60자로 입력해주세요.')
    if not isinstance(songs, list) or not 1 <= len(songs) <= 100:
        raise ValidationError('유튜브 링크를 1~100개 입력해주세요.')
    old_text = [s for s in (existing or {}).get('songs', []) if isinstance(s, str)]
    normalized = [s if isinstance(s, str) and s in old_text else youtube_track(s) for s in songs]
    genre = data.get('room', (existing or {}).get('room'))
    if not isinstance(genre, str) or genre not in ROOMS:
        raise ValidationError('장르를 선택해주세요.')
    return title.strip(), normalized, genre


def saved_playlists():
    with connect_db() as db:
        rows = db.execute('SELECT id, room, payload FROM playlists ORDER BY id DESC').fetchall()
    result = []
    for row in rows:
        p = json.loads(row['payload'])
        p['room'] = row['room']
        p.setdefault('shared_rooms', [row['room']])  # Keep existing lounge shares.
        result.append(p)
    return result


def public(p):
    return {**{k: v for k, v in p.items() if k != 'owner_id'}, 'member_id': member_id(p['owner_id']) if p.get('owner_id') else f'sample-{p["id"]}', 'room_name': ROOMS[p['room']]['name']}


def room_playlists(room):
    result = [p for p in saved_playlists() if room in p['shared_rooms']]
    samples = [{**p, 'room': room, 'shared_rooms': [room], 'sample': True} for p in SAMPLE_PLAYLISTS[room]]
    return sorted([public(p) for p in result + samples], key=lambda p: (-p['likes'], p['id']))


def own_playlists(user_id):
    return [public(p) for p in saved_playlists() if p.get('owner_id') == user_id]


def create_playlist(user_id, nickname, data, shared_room=None):
    title, songs, room = validate_fields(data)
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        next_id = max(8, db.execute('SELECT COALESCE(MAX(id), 0) FROM playlists').fetchone()[0]) + 1
        p = {'id': next_id, 'owner_id': user_id, 'user': nickname, 'title': title, 'songs': songs, 'likes': 0, 'shared_rooms': [shared_room] if shared_room else []}
        db.execute('INSERT INTO playlists VALUES (?, ?, ?)', (next_id, room, json.dumps(p, ensure_ascii=False)))
    return public({**p, 'room': room})


def change_playlist(user_id, playlist_id, data=None, delete=False, share=None):
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM playlists WHERE id = ?', (playlist_id,)).fetchone()
        if not row:
            return None
        p = json.loads(row['payload'])
        if p.get('owner_id') != user_id:
            return None
        p['room'] = row['room']
        p.setdefault('shared_rooms', [row['room']])
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
            db.execute('UPDATE playlists SET room = ?, payload = ? WHERE id = ?', (p['room'], json.dumps(p, ensure_ascii=False), playlist_id))
    return public(p), affected


def like_playlist(room, playlist_id):
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM playlists WHERE id = ?', (playlist_id,)).fetchone()
        if row:
            p = json.loads(row['payload'])
            if room not in p.get('shared_rooms', [row['room']]):
                return None
            p['likes'] += 1
            db.execute('UPDATE playlists SET payload = ? WHERE id = ?', (json.dumps(p, ensure_ascii=False), playlist_id))
            return p['likes'], p.get('shared_rooms', [row['room']])
    # Samples remain public, but are not presented as connected participants.
    p = next((p for p in SAMPLE_PLAYLISTS[room] if p['id'] == playlist_id), None)
    if p:
        return p['likes'], [room]
    return None
