"""플레이리스트 찾기: 선택한 2~3곡이 모두 담긴 플레이리스트를 찾습니다.

- 곡 검색은 DB의 플레이리스트에 실제로 담긴 곡만 대상으로 합니다.
- 같은 곡 여부는 YouTube 영상 ID(video_id)로만 판단합니다.
- 공유 여부와 관계없이 모든 플레이리스트를 찾습니다.
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


def _normalize(text):
    return ' '.join(str(text or '').casefold().split())


def search_songs(query, limit=SONG_LIMIT):
    query = _normalize(query)
    if not query:
        return []
    if len(query) > MAX_QUERY_LENGTH:
        raise library.ValidationError(f'곡 제목은 {MAX_QUERY_LENGTH}자 이하로 입력해주세요.')

    found = {}
    for playlist in library.saved_playlists():
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
    for playlist in library.saved_playlists():
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
