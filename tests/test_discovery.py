import os
import sys
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ['MUSIC_LOUNGE_DB'] = str(Path(_tmp.name) / 'test.sqlite3')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as web  # noqa: E402
import playlist_service as library  # noqa: E402
from storage import connect_db  # noqa: E402

A, B, C, D = 'aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc', 'ddddddddddd'


def song(video_id, title, artist=None):
    item = {'title': title, 'url': f'https://www.youtube.com/watch?v={video_id}'}
    if artist:
        item['artist'] = artist
    return item


class DiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect_db() as db:
            db.execute('DELETE FROM playlists')
            db.execute('DELETE FROM users')
            for user_id, nickname in (('alice', '앨리스'), ('bob', '밥')):
                db.execute('INSERT INTO users (user_id, nickname, password_hash) VALUES (?, ?, ?)',
                           (user_id, nickname, 'x'))
        # 공유된 목록, 공유하지 않은 목록 모두 포함되어야 함
        cls.shared = library.create_playlist('alice', '앨리스', {
            'title': '공유 목록', 'room': 'ballad',
            'songs': [song(A, '밤편지', 'IU'), song(B, '너의 모든 순간', '성시경'), song(C, 'Hype Boy', 'NewJeans')]},
            shared_room='ballad')
        cls.private = library.create_playlist('bob', '밥', {
            'title': '비공개 목록', 'room': 'indie',
            'songs': [song(A, '밤편지', 'IU'), song(B, '너의 모든 순간'), song(D, '밤편지 (Live)', 'IU')]})
        cls.client = web.app.test_client()

    def test_search_songs_from_db_without_login(self):
        res = self.client.get('/api/discovery/songs?q=밤편지')
        self.assertEqual(res.status_code, 200)
        songs = res.get_json()['songs']
        self.assertEqual([s['video_id'] for s in songs], [A, D])  # 제목 일치가 먼저
        self.assertEqual(songs[0]['playlist_count'], 2)
        self.assertEqual(songs[0]['artist'], 'IU')

    def test_search_by_artist_and_case(self):
        songs = self.client.get('/api/discovery/songs?q=newjeans').get_json()['songs']
        self.assertEqual([s['video_id'] for s in songs], [C])

    def test_search_empty_and_too_long(self):
        self.assertEqual(self.client.get('/api/discovery/songs?q=').get_json()['songs'], [])
        self.assertEqual(self.client.get('/api/discovery/songs?q=없는노래').get_json()['songs'], [])
        self.assertEqual(self.client.get('/api/discovery/songs?q=' + 'a' * 121).status_code, 400)

    def test_playlists_include_unshared(self):
        res = self.client.get(f'/api/discovery/playlists?first={A}&second={B}')
        self.assertEqual(res.status_code, 200)
        playlists = res.get_json()['playlists']
        self.assertEqual({p['title'] for p in playlists}, {'공유 목록', '비공개 목록'})
        card = next(p for p in playlists if p['title'] == '공유 목록')
        self.assertEqual(card['user'], '앨리스')
        self.assertEqual(card['song_count'], 3)
        self.assertTrue(card['cover_url'].startswith('https://i.ytimg.com/vi/'))
        self.assertEqual({s['video_id'] for s in card['songs']}, {A, B, C})
        self.assertNotIn('owner_id', card)  # 로그인 아이디는 노출하지 않음

    def test_playlists_match_by_video_id_only(self):
        # D는 같은 '밤편지'지만 다른 영상이라 C와 함께 담긴 목록은 없음
        playlists = self.client.get(f'/api/discovery/playlists?first={C}&second={D}').get_json()['playlists']
        self.assertEqual(playlists, [])

    def test_three_songs(self):
        playlists = self.client.get(f'/api/discovery/playlists?first={A}&second={B}&third={C}').get_json()['playlists']
        self.assertEqual([p['title'] for p in playlists], ['공유 목록'])

    def login(self, client, user_id):
        with connect_db() as db:
            row = db.execute('SELECT id, nickname FROM users WHERE user_id = ?', (user_id,)).fetchone()
        with client.session_transaction() as sess:
            sess.update(user_pk=row['id'], user_id=user_id, nickname=row['nickname'])
        return row['id']

    def test_import_requires_login(self):
        res = web.app.test_client().post(f'/api/discovery/playlists/{self.private["id"]}/import', json={})
        self.assertEqual(res.status_code, 401)

    def test_import_copies_unshared_playlist(self):
        client = web.app.test_client()
        alice_pk = self.login(client, 'alice')
        res = client.post(f'/api/discovery/playlists/{self.private["id"]}/import', json={})
        self.assertEqual(res.status_code, 201)
        copied = res.get_json()
        self.assertNotEqual(copied['id'], self.private['id'])
        self.assertEqual(copied['title'], '비공개 목록')
        self.assertEqual(copied['user'], '앨리스')
        self.assertEqual(copied['shared_rooms'], [])  # 가져온 목록은 공유하지 않음
        self.assertEqual([s['video_id'] for s in copied['songs']], [A, B, D])
        self.assertIn(copied['id'], [p['id'] for p in library.own_playlists(alice_pk)])
        # 원본은 그대로
        original = next(p for p in library.saved_playlists() if p['id'] == self.private['id'])
        self.assertEqual(original['user'], '밥')
        with connect_db() as db:
            db.execute('DELETE FROM playlists WHERE id = ?', (copied['id'],))

    def test_import_missing_playlist(self):
        client = web.app.test_client()
        self.login(client, 'alice')
        self.assertEqual(client.post('/api/discovery/playlists/99999/import', json={}).status_code, 404)

    def test_invalid_selection(self):
        for query in (f'first={A}', f'first={A}&second={A}', f'first={A}&second=bad'):
            res = self.client.get(f'/api/discovery/playlists?{query}')
            self.assertEqual(res.status_code, 400, query)
            self.assertIn('error', res.get_json())


if __name__ == '__main__':
    unittest.main()
