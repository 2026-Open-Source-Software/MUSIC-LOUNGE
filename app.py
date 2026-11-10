from flask import (Flask,render_template,request,jsonify,session,redirect,url_for,Response,stream_with_context,send_file,)
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
)

hub = RoomHub()

USER_ID_RE = re.compile(r'^[A-Za-z0-9_]{4,20}$')
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{20,80}$')


def get_lounge_rooms():
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


def same_origin():
    origin = request.headers.get('Origin') or request.headers.get('Referer')
    if not origin:
        return True
    parsed = urlsplit(origin)
    return parsed.netloc == request.host


@app.before_request
def verify_request():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and not same_origin():
        return jsonify({'error': '허용되지 않은 요청 출처입니다.'}), 403

    user_pk = session.get('user_pk')
    user_id = session.get('user_id')
    if not user_pk and not user_id:
        return None

    with connect_db() as db:
        if user_pk:
            row = db.execute(
                'SELECT id, user_id, nickname, status FROM users WHERE id = ?',
                (user_pk,),
            ).fetchone()
        else:
            row = db.execute(
                'SELECT id, user_id, nickname, status FROM users WHERE user_id = ?',
                (user_id,),
            ).fetchone()

    if not row or row['status'] != 'ACTIVE':
        hub.logout(session.get('session_id'))
        session.clear()
        if request.path.startswith('/api/'):
            return auth_error()
        return redirect(url_for('login'))

    session['user_pk'] = row['id']
    session['user_id'] = row['user_id']
    session['nickname'] = row['nickname']
    if not session.get('session_id'):
        session['session_id'] = secrets.token_urlsafe(24)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise library.ValidationError('JSON 객체 형식의 요청이 필요합니다.')
    return data


def auth_error():
    return jsonify({'error': '로그인이 필요합니다.'}), 401


def room_error(room):
    lounge_rooms = get_lounge_rooms()
    if room not in lounge_rooms:
        return jsonify({'error': '존재하지 않는 라운지입니다.'}), 404
    return None


def notify_rooms(rooms):
    for room in set(rooms or []):
        hub.publish(room, 'playlists_changed', {'room': room})


@app.errorhandler(library.ValidationError)
def invalid_data(error):
    return jsonify({'error': str(error)}), 400


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

    if not USER_ID_RE.match(user_id):
        return jsonify({'error': '아이디는 영문, 숫자, 밑줄(_) 조합 4~20자여야 합니다.'}), 400
    if not (1 <= len(password) <= 128):
        return jsonify({'error': '비밀번호는 1~128자여야 합니다.'}), 400

    with connect_db() as db:
        account = db.execute(
            'SELECT id, user_id, nickname, password_hash, status FROM users WHERE user_id = ?',
            (user_id,),
        ).fetchone()

        if account:
            if account['status'] != 'ACTIVE':
                return jsonify({'error': '비활성화되었거나 정지된 계정입니다.'}), 403
            if not check_password_hash(account['password_hash'], password):
                return jsonify({'error': '아이디 또는 비밀번호가 올바르지 않습니다.'}), 401
            user_pk = account['id']
            nickname = account['nickname']
        else:
            if not (2 <= len(nickname) <= 20):
                return jsonify({'error': '새 계정 생성 시 닉네임은 2~20자여야 합니다.'}), 400
            try:
                cur = db.execute(
                    '''
                    INSERT INTO users (user_id, password_hash, nickname, tag, profile_image)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (user_id, generate_password_hash(password), nickname, tag, profile_image),
                )
                user_pk = cur.lastrowid
            except sqlite3.IntegrityError:
                return jsonify({'error': '이미 생성된 아이디입니다. 다시 로그인해주세요.'}), 409

    hub.logout(session.get('session_id'))
    session.clear()
    session.update(
        user_pk=user_pk,
        user_id=user_id,
        nickname=nickname,
        session_id=secrets.token_urlsafe(24),
    )

    if request.accept_mimetypes.best == 'application/json':
        return jsonify({'ok': True, 'redirect_url': url_for('mypage')})
    return redirect(url_for('mypage'))


@app.post('/logout')
def logout():
    hub.logout(session.get('session_id'))
    session.clear()
    return redirect(url_for('login'))


@app.get('/mypage')
def mypage():
    if not session.get('user_pk'):
        return redirect(url_for('login'))
    return render_template('mypage.html')



@app.route('/api/me', methods=['GET', 'PATCH', 'DELETE'])
def me():
    if not session.get('user_pk'):
        return auth_error()

    user_pk = session['user_pk']
    user_id = session['user_id']

    if request.method == 'GET':
        with connect_db() as db:
            row = db.execute(
                'SELECT id, user_id, nickname, tag, profile_image, role, status FROM users WHERE id = ?',
                (user_pk,),
            ).fetchone()
        if not row:
            return auth_error()
        return jsonify({
            'user_pk': row['id'],
            'user_id': row['user_id'],
            'nickname': row['nickname'],
            'tag': row['tag'],
            'profile_image': row['profile_image'],
            'role': row['role'],
            'member_id': library.member_id(row['user_id']),
        })

    if request.method == 'DELETE':
        with connect_db() as db:
            db.execute('DELETE FROM users WHERE id = ?', (user_pk,))
        hub.logout(session.get('session_id'))
        session.clear()
        for room in get_lounge_rooms():
            hub.publish(room, 'playlists_changed', {'room': room})
        return jsonify({'ok': True})

    # PATCH /api/me: 프로필 및 비밀번호 수정
    data = body()
    nickname = str(data.get('nickname', session['nickname'])).strip()
    if not (2 <= len(nickname) <= 20):
        raise library.ValidationError('닉네임은 2~20자여야 합니다.')

    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''

    with connect_db() as db:
        account = db.execute(
            'SELECT password_hash, tag, profile_image FROM users WHERE id = ?',
            (user_pk,),
        ).fetchone()
        if not account:
            return auth_error()

        tag = data.get('tag', account['tag'])
        if isinstance(tag, str):
            tag = tag.strip() or None
        profile_image = data.get('profile_image', account['profile_image'])
        if isinstance(profile_image, str):
            profile_image = profile_image.strip() or None

        if new_password:
            if not check_password_hash(account['password_hash'], current_password):
                return jsonify({'error': '현재 비밀번호가 일치하지 않습니다.'}), 401
            if not (1 <= len(new_password) <= 128):
                raise library.ValidationError('새 비밀번호는 1~128자여야 합니다.')
            db.execute(
                'UPDATE users SET nickname = ?, password_hash = ?, tag = ?, profile_image = ? WHERE id = ?',
                (nickname, generate_password_hash(new_password), tag, profile_image, user_pk),
            )
        else:
            db.execute(
                'UPDATE users SET nickname = ?, tag = ?, profile_image = ? WHERE id = ?',
                (nickname, tag, profile_image, user_pk),
            )

    session['nickname'] = nickname
    hub.update_nickname(user_id, nickname)
    for room in get_lounge_rooms():
        hub.publish(room, 'playlists_changed', {'room': room})

    return jsonify({
        'ok': True,
        'user_pk': user_pk,
        'user_id': user_id,
        'nickname': nickname,
        'tag': tag,
        'profile_image': profile_image,
        'member_id': library.member_id(user_id),
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



@app.route('/api/me/playlists', methods=['GET', 'POST'])
def my_playlists():
    if not session.get('user_pk'):
        return auth_error()
    if request.method == 'POST':
        created = library.create_playlist(session['user_pk'], session['nickname'], body())
        return jsonify(created), 201
    return jsonify(library.own_playlists(session['user_pk']))


@app.post('/api/playlists/cart-combine')
def cart_combine():
    if not session.get('user_pk'):
        return auth_error()
    data = body()
    shared_room = data.get('room') if data.get('is_public') else None
    p = library.create_playlist(
        session['user_pk'],
        session['nickname'],
        data,
        shared_room=shared_room,
    )
    if shared_room:
        notify_rooms([shared_room])
    return jsonify(p), 201


@app.route('/api/me/playlists/<int:playlist_id>', methods=['PATCH', 'DELETE'])
def manage_playlist(playlist_id):
    if not session.get('user_pk'):
        return auth_error()
    result = library.change_playlist(
        session['user_pk'],
        playlist_id,
        body() if request.method == 'PATCH' else None,
        delete=(request.method == 'DELETE'),
    )
    if not result:
        return jsonify({'error': '플레이리스트를 찾을 수 없거나 수정 권한이 없습니다.'}), 404
    playlist, rooms = result
    notify_rooms(rooms)
    return jsonify({'ok': True, 'playlist': playlist})


@app.put('/api/me/playlists/<int:playlist_id>/share')
def share_playlist(playlist_id):
    if not session.get('user_pk'):
        return auth_error()
    data = body()
    room = data.get('room')
    if error := room_error(room):
        return error
    enabled = bool(data.get('enabled', True))
    result = library.change_playlist(session['user_pk'], playlist_id, share=(room, enabled))
    if not result:
        return jsonify({'error': '플레이리스트를 찾을 수 없거나 권한이 없습니다.'}), 404
    playlist, rooms = result
    notify_rooms(rooms)
    return jsonify({'ok': True, 'playlist': playlist})



@app.post('/api/import')
def import_youtube():
    if not session.get('user_pk'):
        return auth_error()
    url = str(body().get('url', '')).strip()
    try:
        return jsonify(youtube_import.import_youtube_url(url))
    except youtube_import.ImportError as error:
        return jsonify({'error': str(error)}), 400


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
        return jsonify({'error': '재생 권한이 없거나 존재하지 않는 곡입니다.'}), 404

    file_url = url_for('track_audio_file', playlist_id=playlist_id, video_id=video_id)
    status = (
        audio_service.ensure_audio(video_id)
        if request.method == 'POST'
        else audio_service.audio_status(video_id)
    )
    if status.get('status') == 'ready':
        status = {**status, 'audio_url': file_url}
    return jsonify(status)


@app.get('/api/playlists/<int:playlist_id>/audio/<video_id>/file')
def track_audio_file(playlist_id, video_id):
    if not session.get('user_pk'):
        return auth_error()
    if not accessible_track(playlist_id, video_id):
        return jsonify({'error': '재생 권한이 없거나 존재하지 않는 곡입니다.'}), 404

    path = audio_service.audio_file_path(video_id)
    if not path:
        return jsonify({'error': '오디오 파일이 아직 준비되지 않았습니다.'}), 404
    return send_file(path, mimetype='audio/mpeg', conditional=True)


@app.get('/api/rooms')
def rooms():
    """lounges 테이블에서 장르별 라운지 정보를 조회하여 반환합니다."""
    return jsonify(get_lounge_rooms())


@app.get('/api/search')
def search_playlists():
    q = request.args.get('q', '').strip().lower()
    room = request.args.get('room', '').strip()
    items = []
    for p in library.saved_playlists():
        if not p.get('shared_rooms'):
            continue
        if room and room not in p.get('shared_rooms', []):
            continue
        if q and q not in p['title'].lower() and q not in p['user'].lower():
            continue
        items.append(library.public(p))
    items.sort(key=lambda x: (-x['likes'], x['id']))
    return jsonify(items)


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
        notify_rooms([room])
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
    notify_rooms([room])
    if isinstance(result, dict):
        return jsonify(result)
    return jsonify({'likes': result})


def recent_messages(room):
    with connect_db() as db:
        rows = db.execute(
            '''
            SELECT id, user_id, nickname, text, created_at
            FROM messages
            WHERE room = ?
            ORDER BY id DESC
            LIMIT 100
            ''',
            (room,),
        ).fetchall()
    return [
        {
            'id': row['id'],
            'member_id': library.member_id(row['user_id']),
            'user': row['nickname'],
            'text': row['text'],
            'created_at': row['created_at'],
        }
        for row in reversed(rows)
    ]


@app.route('/api/rooms/<room>/messages', methods=['GET', 'POST'])
def messages(room):
    if error := room_error(room):
        return error
    if not session.get('user_pk'):
        return auth_error()
    if request.method == 'GET':
        return jsonify(recent_messages(room))

    if not hub.is_user_in_room(room, session['user_id']):
        return jsonify({'error': '라운지에 실시간 연결된 상태에서만 채팅을 보낼 수 있습니다.'}), 409

    text = str(body().get('text', '')).strip()
    if not (1 <= len(text) <= 300):
        raise library.ValidationError('메시지는 1~300자여야 합니다.')

    now = datetime.now(timezone.utc).isoformat()
    with connect_db() as db:
        cursor = db.execute(
            'INSERT INTO messages (room, user_id, nickname, text, created_at) VALUES (?, ?, ?, ?, ?)',
            (room, session['user_id'], session['nickname'], text, now),
        )
        msg_id = cursor.lastrowid
        db.execute(
            '''
            DELETE FROM messages
            WHERE room = ? AND id <= COALESCE(
                (SELECT id FROM messages WHERE room = ? ORDER BY id DESC LIMIT 1 OFFSET 100),
                0
            )
            ''',
            (room, room),
        )

    msg = {
        'id': msg_id,
        'member_id': library.member_id(session['user_id']),
        'user': session['nickname'],
        'text': text,
        'created_at': now,
    }
    hub.publish(room, 'message', {'room': room, 'message': msg})
    return jsonify(msg), 201


@app.get('/api/rooms/<room>/events')
def room_events(room):
    if error := room_error(room):
        return error
    if not session.get('user_pk'):
        return auth_error()

    token = request.args.get('connection', '')
    if not TOKEN_RE.match(token):
        return jsonify({'error': '유효하지 않은 연결 토큰입니다.'}), 400

    session_id = session['session_id']
    user_id = session['user_id']
    nickname = session['nickname']
    member = library.member_id(user_id)
    queue = hub.join(room, session_id, token, user_id, member, nickname)

    snapshot = {
        'room': room,
        'messages': recent_messages(room),
        'participants': hub.participants(room),
        'playlists': library.room_playlists(room),
        'self': member,
    }

    @stream_with_context
    def stream():
        try:
            yield encode_event('snapshot', snapshot)
            while True:
                try:
                    event, data = queue.get(timeout=8)
                except Empty:
                    yield ': heartbeat\n\n'
                    continue
                yield encode_event(event, data)
                if event == 'closed':
                    break
        finally:
            hub.leave(room, session_id, token)

    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    }
    return Response(stream(), mimetype='text/event-stream', headers=headers)


@app.post('/api/rooms/<room>/leave')
def leave_room(room):
    if error := room_error(room):
        return error
    if not session.get('user_pk'):
        return auth_error()
    token = str(body().get('connection', ''))
    if TOKEN_RE.match(token):
        hub.leave(room, session.get('session_id'), token)
    return jsonify({'ok': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
