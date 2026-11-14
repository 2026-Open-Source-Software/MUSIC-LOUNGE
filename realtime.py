"""Per-lounge event streams. Queues isolate slow clients and room membership."""
import json
from queue import Queue, Full
from threading import RLock
from playlist_service import member_id


class RoomHub:
    def __init__(self):
        self.lock = RLock()
        self.connections = {}

    def participants(self, room):
        with self.lock:
            users = {}
            for c in self.connections.values():
                if c['room'] == room:
                    key = member_id(c['user_id'])
                    person = users.setdefault(key, {'member_id': key, 'nickname': c['nickname'], 'listening': []})
                    playlist = c.get('listening')
                    if playlist is not None and playlist not in person['listening']:
                        person['listening'].append(playlist)
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

    def listening(self, token, room, session_id, playlist):
        with self.lock:
            c = self.connections.get(token)
            if not c or c['room'] != room or c['session_id'] != session_id:
                return False
            if c.get('listening') != playlist:
                c['listening'] = playlist
                self.presence(room)
            return True

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


def encode_event(kind, data):
    return f'event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
