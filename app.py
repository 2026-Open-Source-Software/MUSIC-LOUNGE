from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context, send_file
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
    MAX_CONTENT_LENGTH=128 * 1024
)
hub = RoomHub()


@app.before_request
def request_identity():
    origin = request.headers.get('Origin')
    if origin and (request.method not in ('GET', 'HEAD', 'OPTIONS') or request.path.endswith('/events')):
        if urlsplit(origin).netloc != request.host or urlsplit(origin).scheme != request.scheme:
            return jsonify({'error': '같은 사이트에서 요청해주세요.'}), 403
    if session.get('user_id'):
        session.setdefault('session_id', secrets.token_urlsafe(24))
        with connect_db() as db:
            account = db.execute(
                'SELECT id, nickname, status FROM users WHERE user_id = ?',
                (session['user_id'],)
            ).fetchone()
        if not account or account['status'] != 'ACTIVE':
            session.clear()
        else:
            session['user_pk'] = account['id']
            session['nickname'] = account['nickname']


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise library.ValidationError('요청 내용을 확인해주세요.')
    return data


def auth_error():
    return jsonify({'error': '로그인이 필요합니다.'}), 401


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
    user_id, password = request.form.get('user_id', ''), request.form.get('password', '')
    tag = request.form.get('tag', '').strip() or None
    if not 2 <= len(nickname) <= 20:
        return jsonify({'error': '닉네임은 2~20자로 입력해주세요.'}), 400
    if not re.fullmatch(r'[A-Za-z0-9_]{4,20}', user_id):
        return jsonify({'error': '아이디는 영문, 숫자, 밑줄(_) 4~20자로 입력해주세요.'}), 400
    if not password.strip() or len(password) > 128:
        return jsonify({'error': '비밀번호는 1~128자로 입력해주세요.'}), 400
    try:
        with connect_db() as db:
            account = db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
            if account:
                if account['status'] != 'ACTIVE':
                    return jsonify({'error': '이용이 제한되거나 탈퇴한 계정입니다.'}), 403
                if not check_password_hash(account['password_hash'], password):
                    return jsonify({'error': '아이디 또는 비밀번호를 확인해주세요.'}), 401
                nickname = account['nickname']
                user_pk = account['id']
            else:
                cursor = db.execute(
                    'INSERT INTO users (user_id, password_hash, nickname, tag) VALUES (?, ?, ?, ?)',
                    (user_id, generate_password_hash(password), nickname, tag)
                )
                user_pk = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({'error': '이미 생성된 아이디입니다. 다시 로그인해주세요.'}), 409
    hub.logout(session.get('session_id'))
    session.clear()
    session.update(user_id=user_id, user_pk=user_pk, nickname=nickname, session_id=secrets.token_urlsafe(24))
    if request.accept_mimetypes.best == 'application/json':
        return jsonify({'ok': True, 'redirect_url': url_for('mypage')})
    return redirect(url_for('mypage'))


@app.get('/mypage')
def mypage():
    return render_template('mypage.html') if session.get('user_id') else redirect(url_for('login'))


@app.post('/logout')
def logout():
    hub.logout(session.get('session_id'))
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/me', methods=['GET', 'PATCH', 'DELETE'])
def profile():
    user_id = session.get('user_id')
    if not user_id:
        return auth_error()

    if request.method == 'DELETE':
        with connect_db() as db:
            db.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
        hub.logout(session.get('session_id'))
        session.clear()
        for room in ROOMS:
            hub.publish(room, 'playlists_changed', {'room': room})
        return jsonify({'ok': True, 'message': '회원 탈퇴가 완료되었습니다.'})

    if request.method == 'PATCH':
        data = body()
        nickname = data.get('nickname', session['nickname'])
        if not isinstance(nickname, str) or not 2 <= len(nickname.strip()) <= 20:
            raise library.ValidationError('닉네임은 2~20자로 입력해주세요.')
        nickname = nickname.strip()
        with connect_db() as db:
            db.execute('BEGIN IMMEDIATE')
            account = db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
            if data.get('new_password'):
                password, current = data['new_password'], data.get('current_password', '')
                if not isinstance(password, str) or not password.strip() or len(password) > 128:
                    raise library.ValidationError('비밀번호는 1~128자로 입력해주세요.')
                if not isinstance(current, str) or not check_password_hash(account['password_hash'], current):
                    raise library.ValidationError('현재 비밀번호가 일치하지 않습니다.')
                db.execute('UPDATE users SET password_hash = ? WHERE user_id = ?', (generate_password_hash(password), user_id))
            tag = data.get('tag', account['tag'])
            profile_image = data.get('profile_image', account['profile_image'])
            db.execute(
                'UPDATE users SET nickname = ?, tag = ?, profile_image = ? WHERE user_id = ?',
                (nickname, tag, profile_image, user_id)
            )
        session['nickname'] = nickname
        hub.rename(user_id, nickname)
        for room in ROOMS:
            hub.publish(room, 'playlists_changed', {'room': room})

    with connect_db() as db:
        account = db.execute(
            'SELECT id, user_id, nickname, tag, profile_image, role FROM users WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    return jsonify({
        'id': account['id'],
        'user_id': account['user_id'],
        'nickname': account['nickname'],
        'tag': account['tag'],
        'profile_image': account['profile_image'],
        'role': account['role'],
        'member_id': library.member_id(user_id)
    })


@app.get('/api/me/likes')
def my_liked_playlists():
    if not session.get('user_id'):
        return auth_error()
    with connect_db() as db:
        rows = db.execute("""
            SELECT p.id, p.owner_id, p.title, p.songs, p.likes, p.created_at, p.updated_at,
                   u.user_id, u.nickname
            FROM playlist_likes pl
            JOIN playlists p ON pl.playlist_id = p.id
            JOIN users u ON p.owner_id = u.id
            WHERE pl.user_id = ?
            ORDER BY pl.created_at DESC
        """, (session['user_pk'],)).fetchall()
    return jsonify([library.public(library._row_to_playlist(r)) for r in rows])


@app.errorhandler(youtube_import.MediaError)
def media_error(error):
    return jsonify({'error': str(error)}), error.status


@app.post('/api/import')
def import_youtube():
    if not session.get('user_id'):
        return auth_error()
    return jsonify(youtube_import.extract(body().get('url')))


def accessible_track(playlist_id, video_id):
    p = next((p for p in library.saved_playlists() if p['id'] == playlist_id), None)
    if not p or (p.get('owner_id') != session['user_id'] and not p['shared_rooms']):
        return False
    return any(isinstance(song, dict) and song.get('video_id') == video_id for song in p['songs'])


@app.route('/api/playlists/<int:playlist_id>/audio/<video_id>', methods=['GET', 'POST'])
def track_audio(playlist_id, video_id):
    if not session.get('user_id'):
        return auth_error()
    if not accessible_track(playlist_id, video_id):
        return jsonify({'error': '재생할 곡을 찾을 수 없습니다.'}), 404
    info = audio_service.prepare(video_id) if request.method == 'POST' else audio_service.status(video_id)
    if info['state'] == 'ready':
        info['url'] = url_for('audio_file', playlist_id=playlist_id, video_id=video_id)
    return jsonify(info), 202 if info['state'] == 'preparing' else 200


@app.get('/api/playlists/<int:playlist_id>/audio/<video_id>/file')
def audio_file(playlist_id, video_id):
    if not session.get('user_id'):
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
    return jsonify(ROOMS)


def room_error(room):
    return (jsonify({'error': '존재하지 않는 라운지입니다.'}), 404) if room not in ROOMS else None


@app.route('/api/me/playlists', methods=['GET', 'POST'])
def my_playlists():
    if not session.get('user_id'):
        return auth_error()
    if request.method == 'POST':
        return jsonify(library.create_playlist(session['user_id'], session['nickname'], body())), 201
    return jsonify(library.own_playlists(session['user_id']))


@app.post('/api/playlists/cart-combine')
def cart_combine():
    if not session.get('user_id'):
        return auth_error()
    data = body()
    shared_room = data.get('shared_room') if data.get('shared_room') in ROOMS else None
    p = library.create_playlist(session['user_id'], session['nickname'], data, shared_room=shared_room)
    if shared_room:
        hub.publish(shared_room, 'playlists_changed', {'room': shared_room})
    return jsonify(p), 201


@app.get('/api/search')
def search_playlists():
    q = (request.args.get('q') or '').strip().lower()
    room = (request.args.get('room') or '').strip().lower()
    results = [p for p in library.saved_playlists() if p['shared_rooms']]
    if room in ROOMS:
        results = [p for p in results if room in p['shared_rooms']]
    if q:
        results = [p for p in results if q in p['title'].lower() or q in p['user'].lower()]
    results.sort(key=lambda p: (-p['likes'], p['id']))
    return jsonify([library.public(p) for p in results[:20]])


@app.route('/api/me/playlists/<int:playlist_id>', methods=['PATCH', 'DELETE'])
def manage_playlist(playlist_id):
    if not session.get('user_id'):
        return auth_error()
    result = library.change_playlist(
        session['user_id'],
        playlist_id,
        body() if request.method == 'PATCH' else None,
        delete=request.method == 'DELETE'
    )
    if result is None:
        return jsonify({'error': '플레이리스트를 찾을 수 없습니다.'}), 404
    p, affected = result
    for room in affected:
        hub.publish(room, 'playlists_changed', {'room': room})
    return jsonify({'ok': True, 'playlist': p})


@app.put('/api/me/playlists/<int:playlist_id>/share')
def share_playlist(playlist_id):
    if not session.get('user_id'):
        return auth_error()
    data = body()
    room, enabled = data.get('room'), data.get('enabled')
    if not isinstance(room, str) or room not in ROOMS or not isinstance(enabled, bool):
        raise library.ValidationError('라운지와 공유 여부를 확인해주세요.')
    result = library.change_playlist(session['user_id'], playlist_id, share=(room, enabled))
    if result is None:
        return jsonify({'error': '플레이리스트를 찾을 수 없습니다.'}), 404
    hub.publish(room, 'playlists_changed', {'room': room})
    return jsonify({'ok': True, 'playlist': result[0]})


@app.route('/api/rooms/<room>/playlists', methods=['GET', 'POST'])
def playlists(room):
    if error := room_error(room):
        return error
    if request.method == 'POST':
        if not session.get('user_id'):
            return auth_error()
        p = library.create_playlist(session['user_id'], session['nickname'], {**body(), 'room': room}, shared_room=room)
        hub.publish(room, 'playlists_changed', {'room': room})
        return jsonify(p), 201
    return jsonify(library.room_playlists(room))


@app.post('/api/rooms/<room>/playlists/<int:playlist_id>/like')
def like(room, playlist_id):
    if error := room_error(room):
        return error
    if not session.get('user_id'):
        return auth_error()
    result = library.like_playlist(session['user_id'], room, playlist_id)
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
            (room,)
        ).fetchall()
    return [
        {'id': r['id'], 'member_id': library.member_id(r['user_id']), 'user': r['nickname'], 'text': r['text'], 'created_at': r['created_at']}
        for r in reversed(rows)
    ]


@app.route('/api/rooms/<room>/messages', methods=['GET', 'POST'])
def messages(room):
    if error := room_error(room):
        return error
    if not session.get('user_id'):
        return auth_error()
    if request.method == 'GET':
        return jsonify(chat_history(room))
    text = body().get('text')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
        raise library.ValidationError('메시지는 1~300자로 입력해주세요.')
    with hub.lock:
        if not hub.contains(room, session['user_id'], session['session_id']):
            return jsonify({'error': '라운지에 연결된 후 메시지를 보낼 수 있습니다.'}), 409
        now = datetime.now(timezone.utc).isoformat()
        with connect_db() as db:
            cursor = db.execute(
                'INSERT INTO messages (room, user_id, nickname, text, created_at) VALUES (?, ?, ?, ?, ?)',
                (room, session['user_id'], session['nickname'], text.strip(), now)
            )
            message = {
                'id': cursor.lastrowid,
                'member_id': library.member_id(session['user_id']),
                'user': session['nickname'],
                'text': text.strip(),
                'created_at': now
            }
            db.execute(
                'DELETE FROM messages WHERE room = ? AND id NOT IN (SELECT id FROM messages WHERE room = ? ORDER BY id DESC LIMIT 100)',
                (room, room)
            )
        hub.publish(room, 'message', {'room': room, 'message': message})
    return jsonify(message), 201


@app.get('/api/rooms/<room>/events')
def events(room):
    if error := room_error(room):
        return error
    if not session.get('user_id'):
        return auth_error()
    token = request.args.get('connection', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,80}', token):
        raise library.ValidationError('연결 식별자가 올바르지 않습니다.')
    user_id, nickname, session_id = session['user_id'], session['nickname'], session['session_id']
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
                    'self': library.member_id(user_id)
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
    return Response(stream(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})


@app.post('/api/rooms/<room>/leave')
def leave(room):
    if not session.get('user_id'):
        return auth_error()
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        hub.leave(data.get('connection', ''), session['session_id'])
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=os.environ.get('FLASK_DEBUG') == '1')