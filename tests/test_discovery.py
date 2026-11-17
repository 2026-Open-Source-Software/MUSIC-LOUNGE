import os
import sys
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ['MUSIC_LOUNGE_DB'] = str(Path(_tmp.name) / 'test.sqlite3')
os.environ.pop('DB_PATH', None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as web  # noqa: E402
import playlist_service as library  # noqa: E402
from storage import connect_db, init_db  # noqa: E402

A, B, C, D = 'aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc', 'ddddddddddd'


def setUpModule():
    # 여러 테스트 파일을 함께 실행해도 이 파일 전용 DB를 쓰도록 다시 지정합니다.
    os.environ['MUSIC_LOUNGE_DB'] = str(Path(_tmp.name) / 'test.sqlite3')
    init_db()


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

    def collect(self, client, playlist_id):
        return client.post(f'/api/discovery/playlists/{playlist_id}/collect', json={})

    def delete_playlist(self, playlist_id):
        with connect_db() as db:
            db.execute('DELETE FROM playlists WHERE id = ?', (playlist_id,))

    def reset_likes(self, playlist_id):
        with connect_db() as db:
            db.execute('UPDATE playlists SET likes = 0 WHERE id = ?', (playlist_id,))

    def test_collect_requires_login(self):
        self.assertEqual(self.collect(web.app.test_client(), self.private['id']).status_code, 401)

    def test_collect_copies_playlist_with_origin(self):
        client = web.app.test_client()
        alice_pk = self.login(client, 'alice')
        res = self.collect(client, self.private['id'])
        self.assertEqual(res.status_code, 201)
        copied = res.get_json()
        self.addCleanup(self.delete_playlist, copied['id'])
        self.assertNotEqual(copied['id'], self.private['id'])
        self.assertEqual(copied['title'], '비공개 목록')
        self.assertEqual(copied['user'], '앨리스')
        self.assertEqual(copied['shared_rooms'], [])
        self.assertEqual(copied['collected_from']['playlist_id'], self.private['id'])
        self.assertEqual(copied['collected_from']['nickname'], '밥')
        self.assertEqual(copied['collected_from']['likes'], 0)
        self.assertNotIn('owner_pk', copied['collected_from'])
        self.assertEqual([s['video_id'] for s in copied['songs']], [A, B, D])
        mine = {p['id']: p for p in library.own_playlists(alice_pk)}
        self.assertEqual(mine[copied['id']]['collected_from']['nickname'], '밥')
        with connect_db() as db:  # 원본 좋아요 수는 최신 값으로 표시
            db.execute('UPDATE playlists SET likes = 7 WHERE id = ?', (self.private['id'],))
        self.addCleanup(self.reset_likes, self.private['id'])
        self.assertEqual(next(p for p in library.own_playlists(alice_pk) if p['id'] == copied['id'])['collected_from']['likes'], 7)
        self.assertIsNone(mine[self.shared['id']]['collected_from'])
        original = next(p for p in library.saved_playlists() if p['id'] == self.private['id'])
        self.assertEqual(original['user'], '밥')

    def test_collected_copy_hidden_from_search(self):
        client = web.app.test_client()
        self.login(client, 'alice')
        copied = self.collect(client, self.private['id']).get_json()
        self.addCleanup(self.delete_playlist, copied['id'])
        playlists = self.client.get(f'/api/discovery/playlists?first={A}&second={B}').get_json()['playlists']
        self.assertEqual(sorted(p['id'] for p in playlists), sorted([self.shared['id'], self.private['id']]))
        songs = self.client.get('/api/discovery/songs?q=밤편지').get_json()['songs']
        self.assertEqual(songs[0]['playlist_count'], 2)  # 복사본은 세지 않음

    def test_collected_copy_is_read_only(self):
        client = web.app.test_client()
        self.login(client, 'alice')
        copied = self.collect(client, self.private['id']).get_json()
        self.addCleanup(self.delete_playlist, copied['id'])
        url = f'/api/me/playlists/{copied["id"]}'
        songs = [s['url'] for s in copied['songs']]
        for data in ({'title': '새 이름', 'room': 'indie', 'songs': songs},
                     {'title': '비공개 목록', 'room': 'ballad', 'songs': songs[:2]}):
            res = client.patch(url, json=data)
            self.assertEqual(res.status_code, 400)
            self.assertIn('수정할 수 없어요', res.get_json()['error'])
        self.assertEqual(client.put(url + '/share', json={'room': 'ballad', 'enabled': True}).status_code, 400)
        after = next(p for p in library.saved_playlists() if p['id'] == copied['id'])
        self.assertEqual((after['title'], after['room'], len(after['songs'])), ('비공개 목록', 'indie', 3))
        self.assertEqual(client.delete(url).status_code, 200)  # 삭제는 가능

    def test_collect_twice_or_own_is_rejected(self):
        client = web.app.test_client()
        self.login(client, 'alice')
        copied = self.collect(client, self.private['id']).get_json()
        self.addCleanup(self.delete_playlist, copied['id'])
        self.assertEqual(self.collect(client, self.private['id']).status_code, 400)
        self.assertEqual(self.collect(client, self.shared['id']).status_code, 400)  # 내 목록
        self.assertEqual(self.collect(client, copied['id']).status_code, 404)  # 복사본은 수집 대상 아님

    def test_collect_state_in_finder_and_lounge(self):
        states = lambda client, url: {p['id']: p['collect_state'] for p in client.get(url).get_json()['playlists']}
        finder = f'/api/discovery/playlists?first={A}&second={B}'
        self.assertEqual(set(states(self.client, finder).values()), {'login'})
        client = web.app.test_client()
        self.login(client, 'alice')
        self.assertEqual(states(client, finder), {self.shared['id']: 'mine', self.private['id']: 'available'})
        copied = self.collect(client, self.private['id']).get_json()
        self.addCleanup(self.delete_playlist, copied['id'])
        self.assertEqual(states(client, finder)[self.private['id']], 'collected')
        lounge = {p['id']: p['collect_state'] for p in client.get('/api/rooms/ballad/playlists').get_json()}
        self.assertEqual(lounge[self.shared['id']], 'mine')
        self.assertTrue(all(state == 'sample' for pid, state in lounge.items() if pid != self.shared['id']))

    def test_collect_missing_playlist(self):
        client = web.app.test_client()
        self.login(client, 'alice')
        self.assertEqual(self.collect(client, 99999).status_code, 404)

    def test_single_song(self):
        playlists = self.client.get(f'/api/discovery/playlists?first={C}').get_json()['playlists']
        self.assertEqual([p['title'] for p in playlists], ['공유 목록'])
        playlists = self.client.get(f'/api/discovery/playlists?first={A}').get_json()['playlists']
        self.assertEqual({p['title'] for p in playlists}, {'공유 목록', '비공개 목록'})

    def test_invalid_selection(self):
        for query in ('', f'first={A}&second={A}', f'first={A}&second=bad'):
            res = self.client.get(f'/api/discovery/playlists?{query}')
            self.assertEqual(res.status_code, 400, query)
            self.assertIn('error', res.get_json())


if __name__ == '__main__':
    unittest.main()
