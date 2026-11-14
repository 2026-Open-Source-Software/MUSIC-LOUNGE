from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for,
    Response,
    stream_with_context,
    send_file,
)
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from queue import Empty
from urllib.parse import urlsplit
from werkzeug.security import generate_password_hash, check_password_hash

from storage import init_db, connect_db
from sample_data import ROOMS
import playlist_service as library
from realtime import RoomHub, encode_event
import youtube_import
import audio_service

init_db()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY') or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=128 * 1024,
)

hub = RoomHub()


def get_lounge_rooms():
    """DB의 lounges 테이블에서 장르별 라운지 목록을 조회합니다."""
    rooms = {}
    try:
        with connect_db() as db:
            rows = db.execute(
                'SELECT id, room, name, description, thumbnail_image FROM lounges ORDER BY id ASC'
            ).fetchall()
        for row in rows:
            room_key = row['room']
            fallback = ROOMS.get(room_key, {})
            rooms[room_key] = {
                'id': row['id'],
                'room': room_key,
                'name': row['name'],
                'emoji': fallback.get('emoji', '🎵'),
                'description': row['description'] or f"{fallback.get('emoji', '🎵')} {row['name']} 라운지",
                'thumbnail_image': row['thumbnail_image'],
                'current_users': len(hub.participants(room_key)),
            }
    except sqlite3.Error:
        pass

    if not rooms:
        for room_key, info in ROOMS.items():
            rooms[room_key] = {
                'room': room_key,
                'name': info['name'],
                'emoji': info.get('emoji', '🎵'),
                'description': f"{info.get('emoji', '🎵')} {info['name']} 라운지",
                'thumbnail_image': None,
                'current_users': len(hub.participants(room_key)),
            }
    return rooms


@app.before_request
def request_identity():
    origin = request.headers.get('Origin')
    if origin and (request.method not in ('GET', 'HEAD', 'OPTIONS') or request.path.endswith('/events')):
        if urlsplit(origin).netloc != request.host or urlsplit(origin).scheme != request.scheme:
            return jsonify({'error': '같은 사이트에서 요청해주세요.'}), 403

    user_pk = session.get('user_pk')
    user_id = session.get('user_id')
    if not user_pk and not user_id:
        return None

    session.setdefault('session_id', secrets.token_urlsafe(24))
    with connect_db() as db:
        if user_pk:
            account = db.execute(
                'SELECT id, user_id, nickname, status FROM users WHERE id = ?',
                (user_pk,),
            ).fetchone()
        else:
            account = db.execute(
                'SELECT id, user_id, nickname, status FROM users WHERE user_id = ?',
                (user_id,),
            ).fetchone()

    if not account or account['status'] != 'ACTIVE':
        hub.logout(session.get('session_id'))
        session.clear()
    else:
        session['user_pk'] = account['id']
        session['user_id'] = account['user_id']
        session['nickname'] = account['nickname']


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise library.ValidationError('요청 내용을 확인해주세요.')
    return data


def auth_error():
    return jsonify({'error': '로그인이 필요합니다.'}), 401


def room_error(room):
    lounge_rooms = get_lounge_rooms()
    return (jsonify({'error': '존재하지 않는 라운지입니다.'}), 404) if room not in lounge_rooms else None


@app.errorhandler(library.ValidationError)
def invalid_data(error):
    return jsonify({'error': str(error)}), 400


@app.errorhandler(youtube_import.MediaError)
def media_error(error):
    return jsonify({'error': str(error)}), error.status


@app.get('/')
def index():
    return render_template('index.HTML')


@app.get('/login')
def login():
    return render_template('login.html')


@app.post('/login')
def login_submit():
    nickname = request.form.get('nickname', '').strip()
    user_id = request.form.get('user_id', '').strip()
    password = request.form.get('password', '')
    tag = request.form.get('tag', '').strip() or None
    profile_image = request.form.get('profile_image', '').strip() or None

    if not re.fullmatch(r'[A-Za-z0-9_]{4,20}', user_id):
        return jsonify({'error': '아이디는 영문, 숫자, 밑줄(_) 4~20자로 입력해주세요.'}), 400
    if not password.strip() or len(password) > 128:
        return jsonify({'error': '비밀번호는 1~128자로 입력해주세요.'}), 400

    try:
        with connect_db() as db:
            account = db.execute(
                'SELECT * FROM users WHERE user_id = ?',
                (user_id,),
            ).fetchone()
            if account:
                if account['status'] != 'ACTIVE':
                    return jsonify({'error': '이용이 제한되거나 탈퇴한 계정입니다.'}), 403
                if not check_password_hash(account['password_hash'], password):
                    return jsonify({'error': '아이디 또는 비밀번호를 확인해주세요.'}), 401
                nickname = account['nickname']
                user_pk = account['id']
            else:
                if not (2 <= len(nickname) <= 20):
                    return jsonify({'error': '닉네임은 2~20자로 입력해주세요.'}), 400
                cursor = db.execute(
                    'INSERT INTO users (user_id, password_hash, nickname, tag, profile_image) VALUES (?, ?, ?, ?, ?)',
                    (user_id, generate_password_hash(password), nickname, tag, profile_image),
                )
                user_pk = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({'error': '이미 생성된 아이디입니다. 다시 로그인해주세요.'}), 409

    hub.logout(session.get('session_id'))
    session.clear()
    session.update(
        user_id=user_id,
        user_pk=user_pk,
        nickname=nickname,
        session_id=secrets.token_urlsafe(24),
    )

    if request.accept_mimetypes.best == 'application/json':
        return jsonify({'ok': True, 'redirect_url': url_for('mypage')})
    return redirect(url_for('mypage'))


@app.get('/mypage')
def mypage():
    return render_template('mypage.html') if session.get('user_pk') else redirect(url_for('login'))


@app.post('/logout')
def logout():
    hub.logout(session.get('session_id'))
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/me', methods=['GET', 'PATCH', 'DELETE'])
def profile():
    user_pk = session.get('user_pk')
    user_id = session.get('user_id')
    if not user_pk:
        return auth_error()

    if request.method == 'DELETE':
        with connect_db() as db:
            db.execute('DELETE FROM users WHERE id = ?', (user_pk,))
        hub.logout(session.get('session_id'))
        session.clear()
        for room in get_lounge_rooms():
            hub.publish(room, 'playlists_changed', {'room': room})
        return jsonify({'ok': True, 'message': '회원 탈퇴가 완료되었습니다.'})

    if request.method == 'PATCH':
        data = body()
        nickname = data.get('nickname', session['nickname'])
        if not isinstance(nickname, str) or not (2 <= len(nickname.strip()) <= 20):
            raise library.ValidationError('닉네임은 2~20자로 입력해주세요.')
        nickname = nickname.strip()

        with connect_db() as db:
            db.execute('BEGIN IMMEDIATE')
            account = db.execute('SELECT * FROM users WHERE id = ?', (user_pk,)).fetchone()
            if not account:
                return auth_error()

            if data.get('new_password'):
                password = data['new_password']
                current = data.get('current_password', '')
                if not isinstance(password, str) or not password.strip() or len(password) > 128:
                    raise library.ValidationError('비밀번호는 1~128자로 입력해주세요.')
                if not isinstance(current, str) or not check_password_hash(account['password_hash'], current):
                    raise library.ValidationError('현재 비밀번호가 일치하지 않습니다.')
                db.execute(
                    'UPDATE users SET password_hash = ? WHERE id = ?',
                    (generate_password_hash(password), user_pk),
                )

            tag = data.get('tag', account['tag'])
            profile_image = data.get('profile_image', account['profile_image'])
            db.execute(
                'UPDATE users SET nickname = ?, tag = ?, profile_image = ? WHERE id = ?',
                (nickname, tag, profile_image, user_pk),
            )

        session['nickname'] = nickname
        hub.rename(user_id, nickname)
        for room in get_lounge_rooms():
            hub.publish(room, 'playlists_changed', {'room': room})

    with connect_db() as db:
        account = db.execute(
            'SELECT id, user_id, nickname, tag, profile_image, role FROM users WHERE id = ?',
            (user_pk,),
        ).fetchone()
    if not account:
        return auth_error()

    return jsonify({
        'id': account['id'],
        'user_pk': account['id'],
        'user_id': account['user_id'],
        'nickname': account['nickname'],
        'tag': account['tag'],
        'profile_image': account['profile_image'],
        'role': account['role'],
        'member_id': library.member_id(account['user_id']),
    })


@app.get('/api/me/likes')
def my_liked_playlists():
    if not session.get('user_pk'):
        return auth_error()
    with connect_db() as db:
        rows = db.execute(
            '''
            SELECT p.id, p.owner_id, p.title, p.songs, p.likes, p.created_at, p.updated_at,
                   u.user_id, u.nickname
            FROM playlist_likes pl
            JOIN playlists p ON pl.playlist_id = p.id
            JOIN users u ON p.owner_id = u.id
            WHERE pl.user_id = ?
            ORDER BY pl.created_at DESC
            ''',
            (session['user_pk'],),
        ).fetchall()
    return jsonify([library.public(library._row_to_playlist(r)) for r in rows])


@app.post('/api/import')
def import_youtube():
    if not session.get('user_pk'):
        return auth_error()
    return jsonify(youtube_import.extract(body().get('url')))


def accessible_track(playlist_id, video_id):
    p = next((p for p in library.saved_playlists() if p['id'] == playlist_id), None)
    if not p:
        return False
    is_owner = (
        p.get('owner_pk') == session.get('user_pk')
        or p.get('owner_id') == session.get('user_id')
    )
    if not is_owner and not p.get('shared_rooms'):
        return False
    return any(isinstance(song, dict) and song.get('video_id') == video_id for song in p['songs'])


@app.route('/api/playlists/<int:playlist_id>/audio/<video_id>', methods=['GET', 'POST'])
def track_audio(playlist_id, video_id):
    if not session.get('user_pk'):
        return auth_error()
    if not accessible_track(playlist_id, video_id):
        return jsonify({'error': '재생할 곡을 찾을 수 없습니다.'}), 404
    info = audio_service.prepare(video_id) if request.method == 'POST' else audio_service.status(video_id)
    if info['state'] == 'ready':
        info['url'] = url_for('audio_file', playlist_id=playlist_id, video_id=video_id)
    return jsonify(info), 202 if info['state'] == 'preparing' else 200


@app.get('/api/playlists/<int:playlist_id>/audio/<video_id>/file')
def audio_file(playlist_id, video_id):
    if not session.get('user_pk'):
        return auth_error()
    if not accessible_track(playlist_id, video_id):
        return jsonify({'error': '재생할 곡을 찾을 수 없습니다.'}), 404
    if audio_service.status(video_id)['state'] != 'ready':
        return jsonify({'error': 'MP3가 아직 준비되지 않았습니다.'}), 409
    response = send_file(audio_service.audio_path(video_id), mimetype='audio/mpeg', conditional=True)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.get('/api/rooms')
def rooms():
    return jsonify(get_lounge_rooms())


@app.route('/api/me/playlists', methods=['GET', 'POST'])
def my_playlists():
    if not session.get('user_pk'):
        return auth_error()
    if request.method == 'POST':
        return jsonify(library.create_playlist(session['user_pk'], session['nickname'], body())), 201
    return jsonify(library.own_playlists(session['user_pk']))


@app.post('/api/playlists/cart-combine')
def cart_combine():
    if not session.get('user_pk'):
        return auth_error()
    data = body()
    lounge_rooms = get_lounge_rooms()
    shared_room = data.get('shared_room') or (data.get('room') if data.get('is_public') else None)
    if shared_room not in lounge_rooms:
        shared_room = None
    p = library.create_playlist(session['user_pk'], session['nickname'], data, shared_room=shared_room)
    if shared_room:
        hub.publish(shared_room, 'playlists_changed', {'room': shared_room})
    return jsonify(p), 201


@app.get('/api/search')
def search_playlists():
    q = (request.args.get('q') or '').strip().lower()
    room = (request.args.get('room') or '').strip().lower()
    lounge_rooms = get_lounge_rooms()
    results = [p for p in library.saved_playlists() if p['shared_rooms']]
    if room in lounge_rooms:
        results = [p for p in results if room in p['shared_rooms']]
    if q:
        results = [p for p in results if q in p['title'].lower() or q in p['user'].lower()]
    results.sort(key=lambda p: (-p['likes'], p['id']))
    return jsonify([library.public(p) for p in results[:20]])


@app.route('/api/me/playlists/<int:playlist_id>', methods=['PATCH', 'DELETE'])
def manage_playlist(playlist_id):
    if not session.get('user_pk'):
        return auth_error()
    result = library.change_playlist(
        session['user_pk'],
        playlist_id,
        body() if request.method == 'PATCH' else None,
        delete=request.method == 'DELETE',
    )
    if result is None:
        return jsonify({'error': '플레이리스트를 찾을 수 없습니다.'}), 404
    p, affected = result
    for room in affected:
        hub.publish(room, 'playlists_changed', {'room': room})
    return jsonify({'ok': True, 'playlist': p})


@app.put('/api/me/playlists/<int:playlist_id>/share')
def share_playlist(playlist_id):
    if not session.get('user_pk'):
        return auth_error()
    data = body()
    room, enabled = data.get('room'), data.get('enabled')
    lounge_rooms = get_lounge_rooms()
    if not isinstance(room, str) or room not in lounge_rooms or not isinstance(enabled, bool):
        raise library.ValidationError('라운지와 공유 여부를 확인해주세요.')
    result = library.change_playlist(session['user_pk'], playlist_id, share=(room, enabled))
    if result is None:
        return jsonify({'error': '플레이리스트를 찾을 수 없습니다.'}), 404
    hub.publish(room, 'playlists_changed', {'room': room})
    return jsonify({'ok': True, 'playlist': result[0]})


@app.route('/api/rooms/<room>/playlists', methods=['GET', 'POST'])
def playlists(room):
    if error := room_error(room):
        return error
    if request.method == 'POST':
        if not session.get('user_pk'):
            return auth_error()
        p = library.create_playlist(
            session['user_pk'],
            session['nickname'],
            {**body(), 'room': room},
            shared_room=room,
        )
        hub.publish(room, 'playlists_changed', {'room': room})
        return jsonify(p), 201
    return jsonify(library.room_playlists(room))


@app.post('/api/rooms/<room>/playlists/<int:playlist_id>/like')
def like(room, playlist_id):
    if error := room_error(room):
        return error
    if not session.get('user_pk'):
        return auth_error()
    result = library.like_playlist(session['user_pk'], room, playlist_id)
    if result is None:
        return jsonify({'error': '플레이리스트를 찾을 수 없습니다.'}), 404
    likes_count, shared_rooms, is_liked = result
    for target in shared_rooms:
        hub.publish(target, 'playlists_changed', {'room': target})
    return jsonify({'likes': likes_count, 'is_liked_by_me': is_liked})


def chat_history(room):
    with connect_db() as db:
        rows = db.execute(
            'SELECT id, user_id, nickname, text, created_at FROM messages WHERE room = ? ORDER BY id DESC LIMIT 100',
            (room,),
        ).fetchall()
    return [
        {
            'id': r['id'],
            'member_id': library.member_id(r['user_id']),
            'user': r['nickname'],
            'text': r['text'],
            'created_at': r['created_at'],
        }
        for r in reversed(rows)
    ]


@app.route('/api/rooms/<room>/messages', methods=['GET', 'POST'])
def messages(room):
    if error := room_error(room):
        return error
    if not session.get('user_pk'):
        return auth_error()
    if request.method == 'GET':
        return jsonify(chat_history(room))

    text = body().get('text')
    if not isinstance(text, str) or not (1 <= len(text.strip()) <= 300):
        raise library.ValidationError('메시지는 1~300자로 입력해주세요.')

    with hub.lock:
        if not hub.contains(room, session['user_id'], session['session_id']):
            return jsonify({'error': '라운지에 연결된 후 메시지를 보낼 수 있습니다.'}), 409

    now = datetime.now(timezone.utc).isoformat()
    with connect_db() as db:
        cursor = db.execute(
            'INSERT INTO messages (room, user_id, nickname, text, created_at) VALUES (?, ?, ?, ?, ?)',
            (room, session['user_id'], session['nickname'], text.strip(), now),
        )
        message = {
            'id': cursor.lastrowid,
            'member_id': library.member_id(session['user_id']),
            'user': session['nickname'],
            'text': text.strip(),
            'created_at': now,
        }
        db.execute(
            'DELETE FROM messages WHERE room = ? AND id NOT IN (SELECT id FROM messages WHERE room = ? ORDER BY id DESC LIMIT 100)',
            (room, room),
        )
    hub.publish(room, 'message', {'room': room, 'message': message})
    return jsonify(message), 201


@app.get('/api/rooms/<room>/events')
def events(room):
    if error := room_error(room):
        return error
    if not session.get('user_pk'):
        return auth_error()
    token = request.args.get('connection', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,80}', token):
        raise library.ValidationError('연결 식별자가 올바르지 않습니다.')

    user_id = session['user_id']
    nickname = session['nickname']
    session_id = session['session_id']
    with hub.lock:
        if token in hub.connections:
            return jsonify({'error': '이미 사용 중인 연결입니다.'}), 409

    @stream_with_context
    def stream():
        connection = None
        try:
            with hub.lock:
                connection = hub.add(token, room, user_id, nickname, session_id)
                snapshot = {
                    'room': room,
                    'messages': chat_history(room),
                    'participants': hub.participants(room),
                    'playlists': library.room_playlists(room),
                    'self': library.member_id(user_id),
                }
            yield 'retry: 2000\n' + encode_event('snapshot', snapshot)
            last_heartbeat = time.monotonic()
            while connection['active']:
                try:
                    kind, data = connection['queue'].get(timeout=5)
                    yield encode_event(kind, data)
                except Empty:
                    yield ': heartbeat\n\n'
                if time.monotonic() - last_heartbeat >= 10:
                    yield ': heartbeat\n\n'
                    last_heartbeat = time.monotonic()
            yield encode_event('closed', {})
        finally:
            if connection is not None:
                hub.remove(token)

    return Response(
        stream(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'},
    )


@app.post('/api/rooms/<room>/leave')
def leave(room):
    if not session.get('user_pk'):
        return auth_error()
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        hub.leave(data.get('connection', ''), session['session_id'])
    return jsonify({'ok': True})

 =====================================================================
# [신규 API 1] 라운지 내 방 목록 조회 및 새 방 개설 (요구사항 1, 6, 7)
# =====================================================================

@app.get('/api/rooms/<room>/playlist-rooms')
def list_playlist_rooms(room):
    """특정 라운지에 개설된 활성 플레이리스트 방 목록 조회"""
    if error := room_error(room):
        return error

    with connect_db() as db:
        rows = db.execute("""
            SELECT pr.*, u.nickname AS host_nickname, u.user_id AS host_user_id,
                   p.title AS playlist_title, p.songs
            FROM playlist_rooms pr
            JOIN users u ON pr.host_id = u.id
            JOIN playlists p ON pr.playlist_id = p.id
            WHERE pr.lounge_room = ? AND pr.status != 'CLOSED'
            ORDER BY pr.id DESC
        """, (room,)).fetchall()

    room_list = []
    for r in rows:
        songs = json.loads(r['songs'])
        cur_idx = r['current_track_index']
        cur_track = songs[cur_idx] if 0 <= cur_idx < len(songs) else None
        participants = hub.playlist_room_participants(r['id'])

        room_list.append({
            'room_id': r['id'],
            'title': r['title'],
            'lounge_room': r['lounge_room'],
            'playlist_id': r['playlist_id'],
            'playlist_title': r['playlist_title'],
            'host': {
                'user_pk': r['host_id'],
                'user_id': r['host_user_id'],
                'nickname': r['host_nickname'],
                'member_id': library.member_id(r['host_user_id'])
            },
            'current_track': cur_track,
            'is_playing': bool(r['is_playing']),
            'status': r['status'],
            'participant_count': len(participants),
            'created_at': r['created_at']
        })

    return jsonify({'success': True, 'data': room_list})


@app.post('/api/rooms/<room>/playlist-rooms')
def create_playlist_room(room):
    """라운지에서 플레이리스트를 선택하여 함께 듣기 방 생성 (호스트 권한 획득)"""
    if error := room_error(room):
        return error
    if not session.get('user_pk'):
        return auth_error()

    data = body()
    playlist_id = data.get('playlist_id')
    title = str(data.get('title') or '').strip()

    if not isinstance(playlist_id, int):
        raise library.ValidationError('재생할 플레이리스트를 지정해주세요.')

    with connect_db() as db:
        pl = db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if not pl:
            return jsonify({'error': '플레이리스트를 찾을 수 없습니다.'}), 404

        songs = json.loads(pl['songs'])
        if not songs:
            return jsonify({'error': '수록곡이 비어있는 플레이리스트입니다.'}), 400

        first_track = songs
        first_video_id = first_track.get('video_id') if isinstance(first_track, dict) else None
        room_title = title if (1 <= len(title) <= 50) else f"{session['nickname']}님의 함께 듣기 방"
        now_iso = datetime.now(timezone.utc).isoformat()

        cur = db.execute("""
            INSERT INTO playlist_rooms (
                lounge_room, playlist_id, host_id, title,
                current_track_index, current_video_id, current_position_sec, is_playing, synced_at
            ) VALUES (?, ?, ?, ?, 0, ?, 0.0, 1, ?)
        """, (room, playlist_id, session['user_pk'], room_title, first_video_id, now_iso))
        new_room_id = cur.lastrowid

    # 라운지 전체에 새 방 개설 알림 브로드캐스트
    hub.publish(room, 'playlist_room_created', {'room': room, 'room_id': new_room_id, 'title': room_title})

    return jsonify({
        'success': True,
        'data': {
            'room_id': new_room_id,
            'title': room_title,
            'playlist_id': playlist_id,
            'is_host': True
        }
    }), 201


# =====================================================================
# [신규 API 2] 방 상세 정보, 대기열, 참여자 목록 및 시간 동기화 (요구사항 2, 3)
# =====================================================================

def compute_synchronized_position(current_pos, is_playing, synced_at_iso, duration=0):
    """서버 기준 시각과 현재 시각의 차이를 합산해 실시간 재생 위치(초) 산출"""
    if not is_playing or not synced_at_iso:
        return round(float(current_pos), 1)
    try:
        synced_dt = datetime.fromisoformat(synced_at_iso)
        elapsed = max(0.0, (datetime.now(timezone.utc) - synced_dt).total_seconds())
        realtime_pos = float(current_pos) + elapsed
        if duration > 0 and realtime_pos > duration:
            realtime_pos = float(duration)
        return round(realtime_pos, 1)
    except Exception:
        return round(float(current_pos), 1)


@app.get('/api/playlist-rooms/<int:room_id>')
def get_playlist_room_detail(room_id):
    """방 입장 시 현재 재생곡, 대기열, 참여자 목록, 동기화된 재생 위치 반환"""
    if not session.get('user_pk'):
        return auth_error()

    with connect_db() as db:
        r = db.execute("""
            SELECT pr.*, u.nickname AS host_nickname, u.user_id AS host_user_id,
                   p.title AS playlist_title, p.songs
            FROM playlist_rooms pr
            JOIN users u ON pr.host_id = u.id
            JOIN playlists p ON pr.playlist_id = p.id
            WHERE pr.id = ?
        """, (room_id,)).fetchone()

    if not r or r['status'] == 'CLOSED':
        return jsonify({'error': '존재하지 않거나 종료된 방입니다.'}), 404

    songs = json.loads(r['songs'])
    cur_idx = r['current_track_index']
    cur_track = songs[cur_idx] if 0 <= cur_idx < len(songs) else None
    duration = cur_track.get('duration', 0) if isinstance(cur_track, dict) else 0

    # 경과 시간 반영한 동기화 재생 초 계산
    realtime_sec = compute_synchronized_position(
        r['current_position_sec'],
        r['is_playing'],
        r['synced_at'],
        duration
    )

    # 현재 곡 이후의 재생 예정 목록 (대기열)
    upcoming_tracks = songs[cur_idx + 1:] if cur_idx + 1 < len(songs) else []
    participants = hub.playlist_room_participants(room_id)
    is_host = (session['user_pk'] == r['host_id'])

    return jsonify({
        'success': True,
        'data': {
            'room_id': r['id'],
            'title': r['title'],
            'lounge_room': r['lounge_room'],
            'playlist_id': r['playlist_id'],
            'playlist_title': r['playlist_title'],
            'is_host': is_host,
            'host': {
                'user_pk': r['host_id'],
                'user_id': r['host_user_id'],
                'nickname': r['host_nickname'],
                'member_id': library.member_id(r['host_user_id'])
            },
            'playback': {
                'current_track_index': cur_idx,
                'current_track': cur_track,
                'current_position_sec': realtime_sec,
                'is_playing': bool(r['is_playing']),
                'synced_at': r['synced_at']
            },
            'upcoming_tracks': upcoming_tracks,
            'all_tracks_count': len(songs),
            'participant_count': len(participants),
            'participants': participants
        }
    })


# =====================================================================
# [신규 API 3] 호스트 전용 재생 제어 및 방 종료 (요구사항 4)
# =====================================================================

@app.post('/api/playlist-rooms/<int:room_id>/control')
def control_playlist_room(room_id):
    """호스트 전용 재생 제어 (재생/일시정지/탐색/곡변경/다음곡/이전곡)"""
    if not session.get('user_pk'):
        return auth_error()

    with connect_db() as db:
        r = db.execute("SELECT * FROM playlist_rooms WHERE id = ?", (room_id,)).fetchone()
        if not r or r['status'] == 'CLOSED':
            return jsonify({'error': '방을 찾을 수 없습니다.'}), 404

        # 호스트 권한 검증
        if r['host_id'] != session['user_pk']:
            return jsonify({'error': '호스트만 재생을 제어할 수 있습니다.'}), 403

        pl = db.execute("SELECT songs FROM playlists WHERE id = ?", (r['playlist_id'],)).fetchone()
        songs = json.loads(pl['songs'])

        data = body()
        action = data.get('action')
        now_iso = datetime.now(timezone.utc).isoformat()

        cur_idx = r['current_track_index']
        cur_pos = r['current_position_sec']
        is_playing = r['is_playing']

        if action == 'play':
            is_playing = 1
            if 'position_sec' in data:
                cur_pos = float(data['position_sec'])
        elif action == 'pause':
            is_playing = 0
            if 'position_sec' in data:
                cur_pos = float(data['position_sec'])
        elif action == 'seek':
            cur_pos = max(0.0, float(data.get('position_sec', 0.0)))
        elif action == 'change_track':
            new_idx = int(data.get('track_index', 0))
            if 0 <= new_idx < len(songs):
                cur_idx = new_idx
                cur_pos = 0.0
                is_playing = 1
        elif action == 'next_track':
            if cur_idx + 1 < len(songs):
                cur_idx += 1
                cur_pos = 0.0
                is_playing = 1
        elif action == 'prev_track':
            if cur_idx - 1 >= 0:
                cur_idx -= 1
                cur_pos = 0.0
                is_playing = 1
        else:
            raise library.ValidationError('올바른 제어 액션을 지정해주세요.')

        new_video_id = songs[cur_idx].get('video_id') if isinstance(songs[cur_idx], dict) else None

        db.execute("""
            UPDATE playlist_rooms
            SET current_track_index = ?, current_video_id = ?, current_position_sec = ?,
                is_playing = ?, synced_at = ?
            WHERE id = ?
        """, (cur_idx, new_video_id, cur_pos, is_playing, now_iso, room_id))

    sync_payload = {
        'room_id': room_id,
        'action': action,
        'current_track_index': cur_idx,
        'current_track': songs[cur_idx],
        'current_position_sec': cur_pos,
        'is_playing': bool(is_playing),
        'synced_at': now_iso,
        'upcoming_tracks': songs[cur_idx + 1:] if cur_idx + 1 < len(songs) else []
    }

    # 방 접속 중인 모든 사용자에게 실시간 동기화 브로드캐스트
    hub.publish_playlist_room(room_id, 'room_sync', sync_payload)

    return jsonify({'success': True, 'data': sync_payload})


@app.delete('/api/playlist-rooms/<int:room_id>')
def close_playlist_room(room_id):
    """호스트가 방을 종료"""
    if not session.get('user_pk'):
        return auth_error()

    with connect_db() as db:
        r = db.execute("SELECT * FROM playlist_rooms WHERE id = ?", (room_id,)).fetchone()
        if not r:
            return jsonify({'error': '방을 찾을 수 없습니다.'}), 404
        if r['host_id'] != session['user_pk']:
            return jsonify({'error': '호스트만 방을 종료할 수 있습니다.'}), 403

        db.execute("UPDATE playlist_rooms SET status = 'CLOSED' WHERE id = ?", (room_id,))

    hub.publish_playlist_room(room_id, 'room_closed', {'room_id': room_id})
    return jsonify({'success': True, 'message': '방이 성공적으로 종료되었습니다.'})


# =====================================================================
# [신규 API 4] 방 내부 전용 채팅 (요구사항 5)
# =====================================================================

@app.get('/api/playlist-rooms/<int:room_id>/messages')
def get_room_messages(room_id):
    """방 내부 최근 채팅 100건 조회"""
    if not session.get('user_pk'):
        return auth_error()

    with connect_db() as db:
        rows = db.execute("""
            SELECT id, room_id, user_id, nickname, text, created_at
            FROM room_messages
            WHERE room_id = ?
            ORDER BY id DESC
            LIMIT 100
        """, (room_id,)).fetchall()

    return jsonify([
        {
            'id': r['id'],
            'member_id': library.member_id(r['user_id']),
            'user': r['nickname'],
            'text': r['text'],
            'created_at': r['created_at']
        }
        for r in reversed(rows)
    ])


@app.post('/api/playlist-rooms/<int:room_id>/messages')
def send_room_message(room_id):
    """방 내부 전용 채팅 발송 (방 접속자에게만 브로드캐스트)"""
    if not session.get('user_pk'):
        return auth_error()

    text = str(body().get('text') or '').strip()
    if not (1 <= len(text) <= 300):
        raise library.ValidationError('메시지는 1~300자로 입력해주세요.')

    now_iso = datetime.now(timezone.utc).isoformat()
    with connect_db() as db:
        cur = db.execute("""
            INSERT INTO room_messages (room_id, user_id, nickname, text, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (room_id, session['user_id'], session['nickname'], text, now_iso))
        msg_id = cur.lastrowid

    msg_payload = {
        'id': msg_id,
        'room_id': room_id,
        'member_id': library.member_id(session['user_id']),
        'user': session['nickname'],
        'text': text,
        'created_at': now_iso
    }

    # 방 내부 참여자에게만 실시간 푸시
    hub.publish_playlist_room(room_id, 'room_message', msg_payload)
    return jsonify(msg_payload), 201


# =====================================================================
# [신규 API 5] 방 실시간 SSE 스트림 & 퇴장 (요구사항 1, 2, 3, 5)
# =====================================================================

@app.get('/api/playlist-rooms/<int:room_id>/events')
def playlist_room_events(room_id):
    """방 접속자용 전용 SSE 스트림 (스냅샷, 동기화, 방채팅, 참여자 변동 수신)"""
    if not session.get('user_pk'):
        return auth_error()

    token = request.args.get('connection', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,80}', token):
        raise library.ValidationError('연결 식별자가 올바르지 않습니다.')

    with connect_db() as db:
        r = db.execute("""
            SELECT pr.*, u.nickname AS host_nickname, u.user_id AS host_user_id,
                   p.title AS playlist_title, p.songs
            FROM playlist_rooms pr
            JOIN users u ON pr.host_id = u.id
            JOIN playlists p ON pr.playlist_id = p.id
            WHERE pr.id = ?
        """, (room_id,)).fetchone()

    if not r or r['status'] == 'CLOSED':
        return jsonify({'error': '방을 찾을 수 없습니다.'}), 404

    songs = json.loads(r['songs'])
    cur_idx = r['current_track_index']
    cur_track = songs[cur_idx] if 0 <= cur_idx < len(songs) else None
    duration = cur_track.get('duration', 0) if isinstance(cur_track, dict) else 0

    realtime_sec = compute_synchronized_position(
        r['current_position_sec'],
        r['is_playing'],
        r['synced_at'],
        duration
    )

    user_id = session['user_id']
    nickname = session['nickname']
    session_id = session['session_id']
    member = library.member_id(user_id)

    connection = hub.playlist_room_join(room_id, token, user_id, nickname, session_id)

    # 방 입장 즉시 1회 전송되는 스냅샷
    snapshot = {
        'room_id': room_id,
        'title': r['title'],
        'playlist_title': r['playlist_title'],
        'playback': {
            'current_track_index': cur_idx,
            'current_track': cur_track,
            'current_position_sec': realtime_sec,
            'is_playing': bool(r['is_playing']),
            'synced_at': r['synced_at']
        },
        'upcoming_tracks': songs[cur_idx + 1:] if cur_idx + 1 < len(songs) else [],
        'participants': hub.playlist_room_participants(room_id),
        'self': member
    }

    @stream_with_context
    def stream():
        try:
            yield encode_event('room_snapshot', snapshot)
            last_heartbeat = time.monotonic()
            while connection['active']:
                try:
                    kind, data = connection['queue'].get(timeout=5)
                    yield encode_event(kind, data)
                except Empty:
                    yield ': heartbeat\n\n'
                if time.monotonic() - last_heartbeat >= 10:
                    yield ': heartbeat\n\n'
                    last_heartbeat = time.monotonic()
            yield encode_event('closed', {})
        finally:
            hub.playlist_room_leave(room_id, token)

    return Response(
        stream(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'}
    )


@app.post('/api/playlist-rooms/<int:room_id>/leave')
def leave_playlist_room(room_id):
    """방 명시적 퇴장"""
    if not session.get('user_pk'):
        return auth_error()
    token = str((request.get_json(silent=True) or {}).get('connection', ''))
    if token:
        hub.playlist_room_leave(room_id, token)
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=os.environ.get('FLASK_DEBUG') == '1')
