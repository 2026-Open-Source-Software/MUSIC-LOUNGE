"""Run with: python -m unittest discover -s tests -v"""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from werkzeug.serving import make_server


class LoungeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous_db = os.environ.get('MUSIC_LOUNGE_DB')
        os.environ['MUSIC_LOUNGE_DB'] = str(Path(cls.temp.name) / 'test.sqlite3')
        import app as application
        cls.application = application
        application.app.config.update(TESTING=True)
        cls.server = make_server('127.0.0.1', 0, application.app, threaded=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        if cls.previous_db is None:
            os.environ.pop('MUSIC_LOUNGE_DB', None)
        else:
            os.environ['MUSIC_LOUNGE_DB'] = cls.previous_db
        cls.temp.cleanup()

    def setUp(self):
        with self.application.hub.lock:
            for token in list(self.application.hub.connections):
                self.application.hub.remove(token)
        from storage import connect_db
        with connect_db() as db:
            for table in ('accounts', 'playlists', 'messages'):
                db.execute(f'DELETE FROM {table}')
        self.owner = self.application.app.test_client()
        self.other = self.application.app.test_client()
        self.guest = self.application.app.test_client()
        self.login(self.owner, 'owner')
        self.login(self.other, 'other')
        self.streams = []

    def tearDown(self):
        for client, room, token, response, connection in self.streams:
            client.post(f'/api/rooms/{room}/leave', json={'connection': token})
            response.close()
            connection.close()

    def login(self, client, user, password='test-pass'):
        return client.post('/login', data={'nickname': user + ' music', 'user_id': user, 'password': password}, headers={'Accept': 'application/json'})

    def create(self, room='indie'):
        result = self.owner.post('/api/me/playlists', json={'room': room, 'title': 'Evening music', 'songs': ['Song | https://youtu.be/dQw4w9WgXcQ']})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def open_stream(self, client, room, token):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        connection.request('GET', f'/api/rooms/{room}/events?connection={token}', headers={'Cookie': f'session={client.get_cookie("session").value}'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.streams.append((client, room, token, response, connection))
        return response

    def read_event(self, response, expected):
        kind, lines = None, []
        for _ in range(300):
            line = response.readline().decode('utf-8').rstrip('\r\n')
            if line.startswith('event: '):
                kind = line[7:]
            elif line.startswith('data: '):
                lines.append(line[6:])
            elif not line:
                if kind == expected:
                    return json.loads('\n'.join(lines))
                kind, lines = None, []
        self.fail(f'Event {expected} was not received')

    def test_account_and_owned_playlist_lifecycle(self):
        self.assertEqual(self.guest.get('/api/me/playlists').status_code, 401)
        self.assertEqual(self.guest.get('/mypage').status_code, 302)
        self.assertEqual(self.login(self.guest, 'owner', 'wrong').status_code, 401)
        p = self.create()
        self.assertNotIn('owner_id', p)
        self.assertEqual(p['shared_rooms'], [])
        self.assertEqual(len(self.owner.get('/api/me/playlists').json), 1)
        self.assertEqual(self.other.get('/api/me/playlists').json, [])
        self.assertFalse(any(x['id'] == p['id'] for x in self.owner.get('/api/rooms/indie/playlists').json))
        for room in ('indie', 'rnb'):
            self.assertEqual(self.owner.put(f'/api/me/playlists/{p["id"]}/share', json={'room': room, 'enabled': True}).status_code, 200)
            self.assertTrue(any(x['id'] == p['id'] for x in self.other.get(f'/api/rooms/{room}/playlists').json))
        url = f'/api/me/playlists/{p["id"]}'
        self.assertEqual(self.other.patch(url, json={'title': 'stolen', 'songs': ['https://youtu.be/dQw4w9WgXcQ']}).status_code, 404)
        self.assertEqual(self.other.delete(url).status_code, 404)
        self.assertEqual(self.other.put(url + '/share', json={'room': 'hiphop', 'enabled': True}).status_code, 404)
        self.assertEqual(self.owner.patch(url, json={'title': 'Edited', 'room': 'indie', 'songs': ['https://www.youtube.com/shorts/dQw4w9WgXcQ']}).status_code, 200)
        self.assertEqual(self.owner.patch('/api/me', json={'nickname': 'New nickname', 'current_password': 'test-pass', 'new_password': 'new-pass'}).status_code, 200)
        self.assertEqual(self.owner.get('/api/me/playlists').json[0]['user'], 'New nickname')
        self.assertEqual(self.login(self.guest, 'owner', 'test-pass').status_code, 401)
        self.assertEqual(self.login(self.guest, 'owner', 'new-pass').status_code, 200)
        self.assertEqual(self.owner.post(f'/api/rooms/indie/playlists/{p["id"]}/like').json['likes'], 1)
        code = 'import playlist_service as s; p=s.own_playlists("owner")[0]; assert p["title"]=="Edited" and p["likes"]==1 and len(p["shared_rooms"])==2'
        subprocess.run([sys.executable, '-c', code], check=True, env=os.environ.copy())
        self.assertEqual(self.owner.put(url + '/share', json={'room': 'rnb', 'enabled': False}).status_code, 200)
        self.assertFalse(any(x['id'] == p['id'] for x in self.other.get('/api/rooms/rnb/playlists').json))
        self.assertEqual(self.owner.delete(url).status_code, 200)
        self.assertEqual(self.owner.get('/api/me/playlists').json, [])
        self.assertEqual(self.owner.post('/logout').status_code, 302)
        self.assertEqual(self.owner.get('/api/me').status_code, 401)

    def test_youtube_validation_and_legacy_preservation(self):
        from playlist_service import youtube_track
        valid = ['https://youtu.be/dQw4w9WgXcQ?t=10', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=abc', 'https://music.youtube.com/watch?v=dQw4w9WgXcQ', 'https://youtube.com/embed/dQw4w9WgXcQ', 'https://youtube.com/live/dQw4w9WgXcQ']
        for url in valid:
            self.assertEqual(youtube_track(url)['video_id'], 'dQw4w9WgXcQ')
        bad = ['javascript:alert(1)', 'https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ', 'https://youtu.be/abc', 'https://youtube.com/playlist?list=abc', 'https://youtube.com@evil.com/watch?v=dQw4w9WgXcQ', 'https://youtu.be:443/dQw4w9WgXcQ', 'not a link']
        for url in bad:
            r = self.owner.post('/api/me/playlists', json={'room': 'indie', 'title': 'Test', 'songs': [url]})
            self.assertEqual(r.status_code, 400, url)
        self.assertEqual(self.owner.post('/api/me/playlists', json={'room': [], 'title': 'Test', 'songs': valid}).status_code, 400)
        self.assertEqual(self.owner.post('/api/me/playlists', json=[]).status_code, 400)
        self.assertEqual(self.owner.post('/api/me/playlists', json={'room': 'indie', 'title': 'Test', 'songs': valid * 21}).status_code, 400)
        from storage import connect_db
        with connect_db() as db:
            p = {'id': 99, 'owner_id': 'owner', 'user': 'Old nickname', 'title': 'Legacy', 'songs': ['Old text track'], 'likes': 3}
            db.execute('INSERT INTO playlists VALUES (?, ?, ?)', (99, 'ballad', json.dumps(p)))
        self.assertEqual(self.owner.get('/api/me/playlists').json[0]['shared_rooms'], ['ballad'])
        self.assertEqual(self.owner.patch('/api/me/playlists/99', json={'title': 'Legacy edit', 'songs': ['Old text track']}).status_code, 200)
        self.assertEqual(self.owner.patch('/api/me/playlists/99', json={'title': 'Migrated', 'songs': [valid[0]]}).status_code, 200)

    def test_real_http_stream_chat_presence_room_isolation_and_logout(self):
        a = self.open_stream(self.owner, 'indie', 'a' * 32)
        snap = self.read_event(a, 'snapshot')
        self.assertEqual(len(snap['participants']), 1)
        second_tab = self.open_stream(self.owner, 'indie', 'b' * 32)
        self.assertEqual(len(self.read_event(second_tab, 'snapshot')['participants']), 1)
        b = self.open_stream(self.other, 'indie', 'c' * 32)
        self.assertEqual(len(self.read_event(b, 'snapshot')['participants']), 2)
        other_room = self.open_stream(self.other, 'hiphop', 'd' * 32)
        self.assertEqual(len(self.read_event(other_room, 'snapshot')['participants']), 1)
        message = self.owner.post('/api/rooms/indie/messages', json={'user': 'spoofed', 'text': 'Hello from indie'})
        self.assertEqual(message.status_code, 201)
        for stream in (a, second_tab, b):
            received = self.read_event(stream, 'message')['message']
            self.assertEqual(received['text'], 'Hello from indie')
            self.assertEqual(received['user'], 'owner music')
        self.assertEqual(self.owner.post('/api/rooms/rnb/messages', json={'text': 'not joined'}).status_code, 409)
        self.assertEqual(self.guest.post('/api/rooms/indie/messages', json={'text': 'unauthenticated'}).status_code, 401)
        self.assertEqual(self.other.post('/api/rooms/hiphop/messages', json={'text': 'Only hiphop'}).status_code, 201)
        self.assertEqual(self.read_event(other_room, 'message')['message']['text'], 'Only hiphop')
        self.assertEqual(len(self.owner.get('/api/rooms/indie/messages').json), 1)
        self.assertEqual(len(self.other.get('/api/rooms/hiphop/messages').json), 1)
        self.owner.post('/api/rooms/indie/leave', json={'connection': 'a' * 32})
        self.assertEqual(len(self.application.hub.participants('indie')), 2)
        self.owner.patch('/api/me', json={'nickname': 'Renamed'})
        self.assertIn('Renamed', [p['nickname'] for p in self.application.hub.participants('indie')])
        p = self.create()
        self.owner.put(f'/api/me/playlists/{p["id"]}/share', json={'room': 'indie', 'enabled': True})
        self.assertEqual(self.read_event(b, 'playlists_changed')['room'], 'indie')
        self.owner.post('/logout')
        self.assertEqual(len(self.application.hub.participants('indie')), 1)
        self.assertEqual(self.read_event(second_tab, 'closed'), {})
        # A reconnect receives history as well as the current participant snapshot.
        reconnect = self.open_stream(self.other, 'indie', 'e' * 32)
        snapshot = self.read_event(reconnect, 'snapshot')
        self.assertEqual(len(snapshot['participants']), 1)
        self.assertEqual(snapshot['messages'][0]['text'], 'Hello from indie')

    def test_templates_assets_and_origin_checks(self):
        for path in ('/', '/login', '/mypage', '/static/style.css', '/static/app.js', '/static/js/api.js', '/static/js/playlist-ui.js', '/static/js/mypage.js'):
            with self.owner.get(path) as response:
                self.assertEqual(response.status_code, 200, path)
        for room in self.application.ROOMS:
            with self.owner.get(f'/static/backgrounds/{room}.jpg') as response:
                self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner.patch('/api/me', json={'nickname': 'Attacker'}, headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.guest.get('/api/rooms/indie/events?connection=' + 'f' * 32).status_code, 401)
        self.assertEqual(self.owner.get('/api/rooms/missing/playlists').status_code, 404)
        self.assertEqual(self.owner.get('/api/rooms/indie/events?connection=bad').status_code, 400)


if __name__ == '__main__':
    unittest.main()
