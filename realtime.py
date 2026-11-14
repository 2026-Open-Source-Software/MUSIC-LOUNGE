"""Per-lounge event streams. Queues isolate slow clients and room membership."""
import json
from queue import Queue, Full
from threading import RLock
from playlist_service import member_id


class RoomHub:
    def __init__(self):
        self.lock = RLock()
        self.connections = {}
        self.playlist_room_conns = {}

    def participants(self, room):
        with self.lock:
            users = {}
            for c in self.connections.values():
                if c['room'] == room:
                    key = member_id(c['user_id'])
                    users[key] = {'member_id': key, 'nickname': c['nickname']}
            return sorted(users.values(), key=lambda p: p['nickname'])

    def publish(self, room, kind, data):
        with self.lock:
            for c in list(self.connections.values()):
                if c['room'] == room:
                    try:
                        c['queue'].put_nowait((kind, data))
                    except Full:
                        # A stalled subscriber reconnects and receives a fresh snapshot.
                        self.remove(c['token'])

    def presence(self, room):
        users = self.participants(room)
        self.publish(room, 'presence', {'room': room, 'participants': users, 'count': len(users)})

    def add(self, token, room, user_id, nickname, session_id):
        with self.lock:
            if token in self.connections:
                raise ValueError('Duplicate connection')
            c = {'token': token, 'room': room, 'user_id': user_id, 'nickname': nickname, 'session_id': session_id, 'queue': Queue(maxsize=200), 'active': True}
            self.connections[token] = c
            self.presence(room)
            return c

    def remove(self, token):
        with self.lock:
            c = self.connections.pop(token, None)
            if c:
                c['active'] = False
                try:
                    c['queue'].put_nowait(('closed', {}))
                except Full:
                    pass
                self.presence(c['room'])

    def leave(self, token, session_id):
        with self.lock:
            c = self.connections.get(token)
            if c and c['session_id'] == session_id:
                self.remove(token)

    def logout(self, session_id):
        with self.lock:
            for c in list(self.connections.values()):
                if c['session_id'] == session_id:
                    self.remove(c['token'])

    def contains(self, room, user_id, session_id):
        with self.lock:
            return any(c['room'] == room and c['user_id'] == user_id and c['session_id'] == session_id for c in self.connections.values())

    def rename(self, user_id, nickname):
        with self.lock:
            rooms = set()
            for c in self.connections.values():
                if c['user_id'] == user_id:
                    c['nickname'] = nickname
                    rooms.add(c['room'])
            for room in rooms:
                self.presence(room)

    def playlist_room_participants(self, room_id):
        """방에 접속 중인 참여자 목록 (다중 탭 접속 시 동일 사용자 중복 제거)"""
        with self.lock:
            conns = self.playlist_room_conns.get(room_id, {})
            users = {}
            for c in conns.values():
                key = member_id(c['user_id'])
                users[key] = {
                    'member_id': key,
                    'nickname': c['nickname'],
                    'user_id': c['user_id']
                }
            return sorted(users.values(), key=lambda p: p['nickname'])

    def playlist_room_join(self, room_id, token, user_id, nickname, session_id):
        with self.lock:
            if room_id not in self.playlist_room_conns:
                self.playlist_room_conns[room_id] = {}
            conns = self.playlist_room_conns[room_id]
            if token in conns:
                raise ValueError('Duplicate room connection')
            c = {
                'token': token,
                'room_id': room_id,
                'user_id': user_id,
                'nickname': nickname,
                'session_id': session_id,
                'queue': Queue(maxsize=200),
                'active': True,
            }
            conns[token] = c
            self.playlist_room_presence(room_id)
            return c

    def playlist_room_leave(self, room_id, token):
        with self.lock:
            conns = self.playlist_room_conns.get(room_id, {})
            c = conns.pop(token, None)
            if c:
                c['active'] = False
                try:
                    c['queue'].put_nowait(('closed', {}))
                except Full:
                    pass
                self.playlist_room_presence(room_id)

    def playlist_room_presence(self, room_id):
        parts = self.playlist_room_participants(room_id)
        self.publish_playlist_room(room_id, 'room_presence', {
            'room_id': room_id,
            'participants': parts,
            'count': len(parts)
        })

    def publish_playlist_room(self, room_id, kind, data):
        """방 내부 모든 접속자에게 실시간 이벤트 브로드캐스트"""
        with self.lock:
            conns = self.playlist_room_conns.get(room_id, {})
            for c in list(conns.values()):
                try:
                    c['queue'].put_nowait((kind, data))
                except Full:
                    self.playlist_room_leave(room_id, c['token'])

    def is_user_in_playlist_room(self, room_id, user_id):
        with self.lock:
            conns = self.playlist_room_conns.get(room_id, {})
            return any(c['user_id'] == user_id for c in conns.values())

def encode_event(kind, data):
    return f'event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
