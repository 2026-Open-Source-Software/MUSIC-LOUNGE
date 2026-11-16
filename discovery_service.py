"""플레이리스트 찾기: 선택한 2~3곡이 모두 담긴 플레이리스트를 찾습니다.

- 곡 검색은 DB의 플레이리스트에 실제로 담긴 곡만 대상으로 합니다.
- 같은 곡 여부는 YouTube 영상 ID(video_id)로만 판단합니다.
- 공유 여부와 관계없이 모든 플레이리스트를 찾되, 수집한 플레이리스트(복사본)는 제외합니다.
"""
import re

import playlist_service as library

MAX_QUERY_LENGTH = 120
SONG_LIMIT = 10
PLAYLIST_LIMIT = 30
MIN_SONGS, MAX_SONGS = 2, 3
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')


def _tracks(playlist):
    """video_id가 있는 곡만 돌려줍니다(예전 문자열 형식 곡은 제외)."""
    for song in playlist.get('songs') or []:
        if isinstance(song, dict) and VIDEO_ID.fullmatch(str(song.get('video_id') or '')):
            yield song


def _originals():
    """수집한 플레이리스트(복사본)를 뺀 플레이리스트. 같은 목록이 여러 번 나오지 않게 합니다."""
    return [p for p in library.saved_playlists() if not p.get('collected_from')]


def _normalize(text):
    return ' '.join(str(text or '').casefold().split())


def search_songs(query, limit=SONG_LIMIT):
    query = _normalize(query)
    if not query:
        return []
    if len(query) > MAX_QUERY_LENGTH:
        raise library.ValidationError(f'곡 제목은 {MAX_QUERY_LENGTH}자 이하로 입력해주세요.')

    found = {}
    for playlist in _originals():
        seen_here = set()
        for song in _tracks(playlist):
            title, artist = _normalize(song.get('title')), _normalize(song.get('artist'))
            if query not in title and query not in artist:
                continue
            video_id = song['video_id']
            entry = found.setdefault(video_id, {
                'title': song.get('title') or f'YouTube · {video_id}',
                'artist': song.get('artist') or '',
                'video_id': video_id,
                'url': f'https://www.youtube.com/watch?v={video_id}',
                'playlist_count': 0,
                '_rank': 0 if title == query else 1 if title.startswith(query) else 2,
            })
            if video_id not in seen_here:
                entry['playlist_count'] += 1
                seen_here.add(video_id)

    ranked = sorted(found.values(), key=lambda s: (s['_rank'], -s['playlist_count'], _normalize(s['title'])))
    return [{k: v for k, v in song.items() if k != '_rank'} for song in ranked[:limit]]


def parse_video_ids(values):
    ids = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not (MIN_SONGS <= len(ids) <= MAX_SONGS):
        raise library.ValidationError('플레이리스트를 찾으려면 서로 다른 곡을 2~3개 선택해주세요.')
    if any(not VIDEO_ID.fullmatch(v) for v in ids):
        raise library.ValidationError('올바르지 않은 곡 정보입니다. 곡을 다시 검색해 선택해주세요.')
    if len(set(ids)) != len(ids):
        raise library.ValidationError('서로 다른 곡을 선택해주세요.')
    return ids


def find_playlists(video_ids, limit=PLAYLIST_LIMIT):
    wanted = parse_video_ids(video_ids)
    results = []
    for playlist in _originals():
        tracks = list(_tracks(playlist))
        if not set(wanted) <= {song['video_id'] for song in tracks}:
            continue
        songs = [{
            'title': song.get('title') or f'YouTube · {song["video_id"]}',
            'artist': song.get('artist') or '',
            'url': f'https://www.youtube.com/watch?v={song["video_id"]}',
            'video_id': song['video_id'],
        } for song in tracks]
        results.append({
            'id': playlist['id'],
            'title': playlist['title'],
            'user': playlist['user'],
            'member_id': library.member_id(playlist['owner_id']),
            'likes': playlist['likes'],
            'song_count': len(playlist.get('songs') or []),
            'shared_rooms': playlist['shared_rooms'],
            'cover_url': f'https://i.ytimg.com/vi/{tracks[0]["video_id"]}/hqdefault.jpg',
            'songs': songs,
        })
    results.sort(key=lambda p: (-p['likes'], -p['id']))
    return results[:limit]


def with_collect_state(playlists, user_pk):
    """각 플레이리스트에 수집 가능 여부(collect_state)를 붙입니다.

    sample: 기본 예시 목록(DB에 없음) · mine: 내가 만든 목록 · collected: 이미 수집함
    available: 수집 가능 · login: 로그인 필요
    """
    owned, collected = set(), set()
    if user_pk:
        for p in library.saved_playlists():
            if p['owner_pk'] == user_pk:
                owned.add(p['id'])
                if p.get('collected_from'):
                    collected.add(p['collected_from'].get('playlist_id'))

    def state(p):
        if p.get('sample'):
            return 'sample'
        if not user_pk:
            return 'login'
        if p['id'] in owned:
            return 'mine'
        return 'collected' if p['id'] in collected else 'available'

    return [{**p, 'collect_state': state(p)} for p in playlists]


def collect_playlist(user_pk, nickname, playlist_id):
    """찾은 플레이리스트를 내 계정에 '수집한 플레이리스트'로 복사합니다.

    수집한 목록은 원본 정보를 기억하고, 이름을 바꾸거나 라운지에 공유할 수 없습니다.
    원본이 없으면 None을 돌려줍니다.
    """
    playlists = library.saved_playlists()
    source = next((p for p in playlists if p['id'] == playlist_id and not p.get('collected_from')), None)
    if source is None:
        return None
    if source['owner_pk'] == user_pk:
        raise library.ValidationError('내가 만든 플레이리스트는 수집할 수 없어요.')
    if any(p['owner_pk'] == user_pk and (p.get('collected_from') or {}).get('playlist_id') == playlist_id
           for p in playlists):
        raise library.ValidationError('이미 수집한 플레이리스트예요. 마이페이지에서 확인해주세요.')
    songs = [{key: song[key] for key in ('title', 'url', 'artist', 'duration') if key in song}
             for song in _tracks(source)]
    if not songs:
        raise library.ValidationError('수집할 수 있는 곡이 없는 플레이리스트입니다.')
    return library.create_playlist(user_pk, nickname, {
        'title': source['title'][:60],
        'room': source['room'],
        'songs': songs[:100],
    }, collected_from={
        'playlist_id': source['id'],
        'owner_pk': source['owner_pk'],
        'nickname': source['user'],
    })
