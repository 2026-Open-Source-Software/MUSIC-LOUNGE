import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

_tmp = tempfile.mkdtemp()
os.environ['MUSIC_LOUNGE_DB'] = str(Path(_tmp) / 'test.sqlite3')
os.environ.pop('DB_PATH', None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as lounge  # noqa: E402
import playlist_service as library  # noqa: E402
import recommend_service as rec  # noqa: E402
from storage import connect_db  # noqa: E402


def tearDownModule():
    shutil.rmtree(_tmp, ignore_errors=True)


def vid(n):
    return f'video{n:06d}'  # 11 characters


def song(n, title=None):
    return {'title': title or f'곡{n}', 'url': f'https://www.youtube.com/watch?v={vid(n)}'}


def video(n, title, channel='채널'):
    return {'video_id': vid(n), 'url': '', 'title': title, 'artist': channel, 'duration': 200}


def yt_item(n, title, uploader='채널'):
    return {'id': vid(n), 'title': title, 'uploader': uploader, 'duration': 200}


def itunes(*tracks, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = {'results': [{'kind': 'song', 'trackName': t, 'artistName': a} for t, a in tracks]}
    return response


def make_user(user_id):
    with connect_db() as db:
        row = db.execute('SELECT id FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if row:
            return row['id']
        return db.execute(
            "INSERT INTO users (user_id, password_hash, nickname) VALUES (?, 'x', ?)", (user_id, user_id)
        ).lastrowid


class Base(unittest.TestCase):
    def setUp(self):
        with connect_db() as db:
            db.execute('DELETE FROM playlist_likes')
            db.execute('DELETE FROM playlists')
            db.execute('DELETE FROM messages')
        rec.limiter.reset()
        rec.catalog.reset()
        patcher = mock.patch.dict(os.environ, {'AI_COOLDOWN_SECONDS': '0', 'ITUNES_MAX_PER_MINUTE': '100'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.me = make_user('me_user')
        self.other = make_user('other_user')


class MatchingTests(unittest.TestCase):
    def test_korean_video_with_english_catalog_title(self):
        title = '[MV] IU(아이유) _ Ending Scene(이런 엔딩)'
        self.assertTrue(rec.video_matches({'title': 'Ending Scene', 'artist': 'IU'}, title, '1theK (원더케이)'))

    def test_catalog_title_with_korean_alias_and_artist_in_channel(self):
        song_ = {'title': 'Through the Night (밤편지)', 'artist': 'IU'}
        self.assertTrue(rec.video_matches(song_, '밤편지 (Live Clip)', '이지금 [IU Official]'))

    def test_punctuation_differences(self):
        song_ = {'title': "How can I love the heartbreak, you're the one I love", 'artist': 'AKMU'}
        self.assertTrue(rec.video_matches(song_, "AKMU - '...(How can I love the heartbreak, you`re the one I love)' M/V", 'AKMU'))

    def test_wrong_song_or_artist_rejected(self):
        self.assertFalse(rec.video_matches({'title': 'Ending Scene', 'artist': 'IU'}, '[MV] IU _ Lilac', 'x'))
        self.assertFalse(rec.video_matches({'title': 'Ending Scene', 'artist': 'IU'}, 'Ending Scene - 다른가수', 'x'))

    def test_covers_and_compilations_rejected(self):
        song_ = {'title': 'Ending Scene', 'artist': 'IU'}
        for title in ['IU - Ending Scene (cover by 누구)', '아이유 IU Ending Scene 커버', 'IU Ending Scene 노래방 MR',
                      'IU Ending Scene Piano Instrumental', 'IU 노래 모음 Ending Scene 1시간']:
            self.assertFalse(rec.video_matches(song_, title, 'x'), title)

    def test_karaoke_titles_and_channels_rejected(self):
        song_ = {'title': 'Through the Night', 'artist': 'IU'}
        cases = [('[TJ노래방] 밤편지 - IU / TJ Karaoke', 'x'),
                 ('밤 편지 - 아이유(Through the Night - IU) (KY.49492) / KY Karaoke', 'x'),
                 ('IU - Through the Night', 'TJ노래방 공식 유튜브채널'),
                 ('IU - Through the Night', '금영 노래방 공식 유튜브 채널'),
                 ('IU - Through the Night (Melody) (Karaoke Version)', 'ZZang KARAOKE'),
                 ('IU Through the Night 멜로디제거', 'x')]
        for title, channel in cases:
            self.assertFalse(rec.video_matches(song_, title, channel), (title, channel))
        self.assertTrue(rec.video_matches(song_, 'IU - Through the Night [가사/Lyrics]', '웅키'))

    def test_names_match_whole_words_only(self):
        # "IU" inside "Stadium", "Some" inside "Handsome" must not count
        self.assertFalse(rec.video_matches({'title': 'Lilac', 'artist': 'IU'}, 'Lilac (Stadium Live)', 'Some Band'))
        self.assertFalse(rec.video_matches({'title': 'Some', 'artist': 'BOL4'}, 'Handsome Day - BOL4', 'x'))
        self.assertFalse(rec.video_matches({'title': 'Rain', 'artist': 'Adele'}, 'Adele - Set Fire to the Raining Sky', 'x'))

    def test_spacing_differences_still_match(self):
        self.assertTrue(rec.video_matches({'title': '이런 엔딩', 'artist': '아이유'}, '아이유 - 이런엔딩 (Live)', 'x'))
        self.assertTrue(rec.video_matches({'title': 'BREATHE', 'artist': 'LEEHI'}, "LEE HI - '한숨 (BREATHE)' M/V", 'OfficialLEEHI'))
        self.assertTrue(rec.video_matches({'title': 'Here I am Again', 'artist': 'Yerin Baek'},
                                          '[사랑의 불시착 OST Part 4] 백예린 (Yerin Baek) - 다시 난, 여기 (Here I Am Again) MV', 'STONE MUSIC'))

    def test_notes_in_brackets_do_not_loosen_the_match(self):
        song_ = {'title': 'Someone Like You (Live)', 'artist': 'Adele'}
        self.assertFalse(rec.video_matches(song_, 'Adele - Hello (Live at the BBC)', 'Adele'))

    def test_search_term_from_video(self):
        self.assertEqual(rec.search_term_from_video('[MV] IU(아이유) _ Ending Scene(이런 엔딩)'), 'IU Ending Scene')
        self.assertEqual(rec.search_term_from_video("AKMU - '한숨' M/V"), 'AKMU 한숨')


class CatalogTests(Base):
    def test_filters_covers_and_uses_us_store(self):
        reply = itunes(('Ending Scene', 'IU'), ('Ending Scene (Cover)', '누구'), ('Ending Scene', 'Karaoke Hits'))
        with mock.patch.object(rec.requests, 'get', return_value=reply) as get:
            self.assertEqual(rec.catalog.search('IU Ending Scene'), [{'title': 'Ending Scene', 'artist': 'IU'}])
        self.assertEqual(get.call_args.kwargs['params']['country'], 'US')

    def test_cache_and_budget(self):
        with mock.patch.dict(os.environ, {'ITUNES_MAX_PER_MINUTE': '2'}), \
             mock.patch.object(rec.requests, 'get', return_value=itunes(('A', 'B'))) as get:
            rec.catalog.search('a')
            rec.catalog.search('a')  # cached
            rec.catalog.search('b')
            with self.assertRaises(rec.CatalogUnavailable):
                rec.catalog.search('c')
        self.assertEqual(get.call_count, 2)

    def test_network_errors(self):
        with mock.patch.object(rec.requests, 'get', side_effect=requests.ConnectionError()):
            with self.assertRaises(rec.CatalogUnavailable):
                rec.catalog.search('x')
        with mock.patch.object(rec.requests, 'get', return_value=itunes(status=503)):
            with self.assertRaises(rec.CatalogUnavailable):
                rec.catalog.search('y')

    def test_confirm_candidate_prefers_the_same_song_else_a_real_one(self):
        with mock.patch.object(rec.catalog, 'search', return_value=[{'title': 'Lilac', 'artist': 'IU'}, {'title': 'Ending Scene', 'artist': 'IU'}]):
            self.assertEqual(rec.confirm_candidate({'title': 'Ending Scene', 'artist': '아이유'})['title'], 'Ending Scene')
            self.assertEqual(rec.confirm_candidate({'title': '없는 노래', 'artist': 'IU'})['title'], 'Lilac')
        with mock.patch.object(rec.catalog, 'search', return_value=[]):
            self.assertIsNone(rec.confirm_candidate({'title': '없는 노래', 'artist': '없는 가수'}))


class LibraryTests(Base):
    def test_seeds_are_own_and_liked_only_not_lounge(self):
        library.create_playlist(self.me, 'me', {'title': '내 것', 'songs': [song(1)], 'room': 'ballad'})
        liked = library.create_playlist(self.other, 'o', {'title': '좋아요', 'songs': [song(2)], 'room': 'ballad'})
        library.create_playlist(self.other, 'o', {'title': '공유', 'songs': [song(3)], 'room': 'ballad'}, shared_room='ballad')
        library.create_playlist(self.other, 'o', {'title': '다른 라운지', 'songs': [song(4)], 'room': 'rnb'}, shared_room='rnb')
        library.like_playlist(self.me, None, liked['id'])

        pool, playlists = rec.load_library(self.me)
        self.assertEqual(pool[vid(1)]['why'], '내 플레이리스트')
        self.assertEqual(pool[vid(2)]['why'], '좋아요한 플레이리스트')
        self.assertNotIn(vid(3), pool)  # shared in a lounge: not a base song any more
        self.assertNotIn(vid(4), pool)
        self.assertEqual(len(playlists), 4)  # every playlist still counts for "saved together"

    def test_pick_seeds_no_duplicates(self):
        pool = {vid(i): {'track': {'title': str(i)}, 'weight': float(i), 'why': 'x'} for i in range(1, 6)}
        seeds = rec.pick_seeds(pool, rng=random.Random(1))
        self.assertEqual(len({s['video_id'] for s in seeds}), rec.SEED_COUNT)


CATALOG = {
    'IU Ending Scene': [{'title': 'Ending Scene', 'artist': 'IU'}],
    'AKMU 한숨': [{'title': 'BREATHE', 'artist': 'AKMU'}],  # catalog has a different song: video does not match
    'IU Lilac': [{'title': 'LILAC', 'artist': 'IU'}],
}


class RelatedTests(Base):
    seeds = [{'video_id': vid(1), 'track': {'title': '밤편지'}, 'weight': 1, 'why': 'x'}]

    def test_only_confirmed_songs_with_catalog_names(self):
        mix = [video(10, '[MV] IU(아이유) _ Ending Scene(이런 엔딩)'), video(11, "AKMU - '한숨' M/V"),
               video(12, 'IU Ending Scene cover'), video(13, '[MV] IU _ Lilac', '이지금 [IU Official]')]
        with mock.patch.object(rec, 'fetch_related', return_value=mix), \
             mock.patch.object(rec.catalog, 'search', side_effect=lambda term: CATALOG.get(term, [])):
            songs, stats = rec.recommend_related(self.seeds, [])
        self.assertEqual([(s['video_id'], s['title'], s['artist']) for s in songs],
                         [(vid(10), 'Ending Scene', 'IU'), (vid(13), 'LILAC', 'IU')])
        self.assertTrue(all(s['verified'] for s in songs))
        self.assertEqual(stats['dropped'], 2)

    def test_stops_checking_once_enough(self):
        mix = [video(10 + i, f'[MV] IU _ Ending Scene {i}') for i in range(12)]
        with mock.patch.object(rec, 'fetch_related', return_value=mix), \
             mock.patch.object(rec.catalog, 'search', side_effect=lambda term: [{'title': term.split(' ', 1)[1], 'artist': 'IU'}]) as search:
            songs, _ = rec.recommend_related(self.seeds, [], count=2)
        self.assertEqual(len(songs), 2)
        self.assertLessEqual(search.call_count, 3)

    def test_catalog_down_returns_nothing_unconfirmed(self):
        with mock.patch.object(rec, 'fetch_related', return_value=[video(10, '[MV] IU _ Ending Scene')]), \
             mock.patch.object(rec.catalog, 'search', side_effect=rec.CatalogUnavailable('x')):
            songs, stats = rec.recommend_related(self.seeds, [])
        self.assertEqual(songs, [])
        self.assertTrue(stats['unavailable'])

    def test_db_co_occurrence_is_confirmed_too(self):
        playlists = [{'videos': [vid(1), vid(5)], 'points': 1,
                      'tracks': {vid(1): {'video_id': vid(1), 'title': '밤편지'}, vid(5): {'video_id': vid(5), 'title': 'IU - Lilac'}}}]
        with mock.patch.object(rec, 'fetch_related', return_value=[]), \
             mock.patch.object(rec.catalog, 'search', side_effect=lambda term: CATALOG.get(term, [])):
            songs, _ = rec.recommend_related(self.seeds, playlists)
        self.assertEqual([(s['video_id'], s['title']) for s in songs], [(vid(5), 'LILAC')])


class RouteTests(Base):
    def setUp(self):
        super().setUp()
        self.client = lounge.app.test_client()
        with self.client.session_transaction() as s:
            s.update(user_pk=self.me, user_id='me_user', nickname='me_user', session_id='s' * 24)

    def post(self, data):
        return self.client.post('/api/rooms/ballad/ai-recommend', json=data)

    def ollama(self, songs):
        reply = mock.Mock(status_code=200, ok=True, text='')
        reply.json.return_value = {'message': {'content': json.dumps({'recommendations': songs}, ensure_ascii=False)}}
        return reply

    def test_login_required_and_hint_length(self):
        self.assertEqual(lounge.app.test_client().post('/api/rooms/ballad/ai-recommend', json={}).status_code, 401)
        self.assertEqual(self.post({'request': 'x' * 101}).status_code, 400)

    def test_related_mode(self):
        library.create_playlist(self.me, 'me', {'title': '내 것', 'songs': [song(1)], 'room': 'ballad'})
        with mock.patch.object(rec, 'fetch_related', return_value=[video(7, '[MV] IU _ Ending Scene')]), \
             mock.patch.object(rec.catalog, 'search', side_effect=lambda term: CATALOG.get(term, [])):
            response = self.post({})
        data = response.get_json()
        self.assertEqual(response.status_code, 200, data)
        self.assertEqual(data['mode'], 'related')
        self.assertEqual(data['recommendations'][0]['title'], 'Ending Scene')

    def test_related_mode_catalog_down(self):
        library.create_playlist(self.me, 'me', {'title': '내 것', 'songs': [song(1)], 'room': 'ballad'})
        with mock.patch.object(rec, 'fetch_related', return_value=[video(7, '[MV] IU _ Ending Scene')]), \
             mock.patch.object(rec.catalog, 'search', side_effect=rec.CatalogUnavailable('x')):
            response = self.post({})
        self.assertEqual(response.status_code, 503)
        self.assertIn('iTunes', response.get_json()['error'])

    def test_request_mode_replaces_invented_songs_and_drops_unplayable(self):
        proposals = [{'title': '없는노래', 'artist': '아이유'}, {'title': '진짜없음', 'artist': '가짜가수'}, {'title': 'Lilac', 'artist': 'IU'}]
        catalog = {'없는노래 아이유': [{'title': 'Ending Scene', 'artist': 'IU'}], '진짜없음 가짜가수': [], 'Lilac IU': [{'title': 'LILAC', 'artist': 'IU'}]}

        def youtube(url, params, timeout):
            hits = {'IU Ending Scene': [yt_item(1, '[MV] IU(아이유) _ Ending Scene(이런 엔딩)')], 'IU LILAC': [yt_item(2, 'LILAC 커버', '누구')]}
            response = mock.Mock(status_code=200)
            response.json.return_value = hits.get(params['q'], [])
            return response

        with mock.patch.object(rec, '_chat_request', return_value=self.ollama(proposals)) as chat, \
             mock.patch.object(rec.catalog, 'search', side_effect=lambda term: catalog[term]), \
             mock.patch.object(rec.requests, 'get', side_effect=youtube):
            response = self.post({'request': '비 오는 날'})
        data = response.get_json()
        self.assertEqual(response.status_code, 200, data)
        self.assertEqual([(s['video_id'], s['title'], s['artist']) for s in data['recommendations']], [(vid(1), 'Ending Scene', 'IU')])
        self.assertEqual(data['stats']['dropped'], 2)  # no catalog song; only a cover video
        self.assertEqual(data['stats']['replaced'], 1)
        self.assertIn('<request>비 오는 날</request>', chat.call_args[0][1]['messages'][1]['content'])

    def test_lounge_is_not_given_to_the_ai(self):
        """Same request in two lounges: the prompt has no genre or chat and is identical."""
        with connect_db() as db:
            db.execute("INSERT INTO messages (room, user_id, nickname, text, created_at) VALUES ('ballad', 'x', 'x', '힙합 듣고 싶다', 't')")
        prompts = []
        for room in ('ballad', 'hiphop'):
            rec.limiter.reset()
            with mock.patch.object(rec, '_chat_request', side_effect=requests.ConnectionError()) as chat:
                self.client.post(f'/api/rooms/{room}/ai-recommend', json={'request': '신나는 노래'})
            prompts.append(chat.call_args[0][1]['messages'])
        self.assertEqual(prompts[0], prompts[1])
        text = json.dumps(prompts[0], ensure_ascii=False)
        for word in ('발라드', '힙합', '<chat>', '라운지'):
            self.assertNotIn(word, text)

    def test_request_mode_nothing_confirmed(self):
        with mock.patch.object(rec, '_chat_request', return_value=self.ollama([{'title': 'a', 'artist': 'b'}])), \
             mock.patch.object(rec.catalog, 'search', return_value=[]):
            response = self.post({'request': '아무거나'})
        self.assertEqual(response.status_code, 502)

    def test_no_seeds_uses_llm_and_reports_ollama_down(self):
        with mock.patch.object(rec, '_chat_request', side_effect=requests.ConnectionError()):
            response = self.post({})
        self.assertEqual(response.status_code, 503)
        self.assertIn('Ollama', response.get_json()['error'])

    def test_saving_recommendation_as_playlist(self):
        track = rec._public_track(video(3, 'x'), {'title': 'Ending Scene', 'artist': 'IU'}, ['r'])
        payload = {'title': 'AI 추천', 'room': 'ballad', 'songs': [{k: track[k] for k in ('title', 'url', 'artist', 'duration')}]}
        response = self.client.post('/api/me/playlists', json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()['songs'][0]['video_id'], vid(3))


if __name__ == '__main__':
    unittest.main()
