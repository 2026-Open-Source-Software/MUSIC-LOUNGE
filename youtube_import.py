"""Public YouTube metadata import; no media is downloaded by this module."""
import importlib.util
import json
import re
import subprocess
import sys
from threading import BoundedSemaphore
from urllib.parse import urlsplit, parse_qs
from playlist_service import ValidationError, youtube_track

_slots = BoundedSemaphore(2)
MAX_TRACKS = 100


class MediaError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def check_module():
    if importlib.util.find_spec('yt_dlp') is None:
        raise MediaError('yt-dlp가 없습니다. 서버에서 python -m pip install -r requirements.txt를 실행해주세요.', 503)


def normalize_url(url):
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise ValidationError('유튜브 영상 또는 공개 재생목록 링크를 입력해주세요.')
    try:
        p = urlsplit(url.strip())
        if p.scheme != 'https' or p.hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be') or p.username or p.password or p.port not in (None, 443):
            raise ValueError()
        playlist = parse_qs(p.query).get('list', [''])[0]
        if playlist:
            if (p.path not in ('/playlist', '/watch') and p.hostname not in ('youtu.be', 'www.youtu.be')) or not re.fullmatch(r'[A-Za-z0-9_-]{10,150}', playlist):
                raise ValueError()
            return 'https://www.youtube.com/playlist?list=' + playlist
        return youtube_track('https://' + p.hostname + p.path + ('?' + p.query if p.query else ''))['url']
    except (ValueError, ValidationError):
        raise ValidationError('https://로 시작하는 올바른 유튜브 영상·재생목록 링크를 입력해주세요.') from None


def parse_metadata(data):
    if not isinstance(data, dict):
        raise MediaError('유튜브에 연결하지 못했습니다. 공개 링크와 서버의 인터넷 연결을 확인해주세요.')
    entries = data.get('entries', [data])
    if not isinstance(entries, list):
        raise MediaError('유튜브 재생목록 정보를 읽지 못했습니다.')
    tracks, seen, skipped = [], set(), 0
    for item in entries[:MAX_TRACKS]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        vid = item.get('id')
        if (not isinstance(vid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid) or vid in seen
            or item.get('availability') in ('private', 'needs_auth', 'premium_only', 'subscriber_only')
            or item.get('title') in ('[Private video]', '[Deleted video]')):
            skipped += 1
            continue
        seen.add(vid)
        title, duration = item.get('title'), item.get('duration')
        tracks.append({'video_id': vid, 'url': 'https://www.youtube.com/watch?v=' + vid,
                       'title': title[:120] if isinstance(title, str) and title.strip() else 'YouTube · ' + vid,
                       'artist': str(item.get('channel') or item.get('uploader') or 'YouTube')[:200],
                       'duration': int(duration) if isinstance(duration, (int, float)) and 0 <= duration <= 604800 else 0})
    if not tracks:
        raise MediaError('가져올 수 있는 공개 영상이 없습니다.', 422)
    title = data.get('title')
    return {'title': title[:60] if isinstance(title, str) else '', 'tracks': tracks, 'skipped': skipped, 'limit_reached': len(entries) >= MAX_TRACKS}


def extract(url):
    url = normalize_url(url)
    check_module()
    if not _slots.acquire(blocking=False):
        raise MediaError('다른 곡 정보를 가져오는 중입니다. 잠시 후 다시 시도해주세요.', 429)
    try:
        try:
            result = subprocess.run([sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-cache-dir', '--flat-playlist', '--dump-single-json', '--skip-download', '--ignore-errors', '--playlist-end', '100', '--socket-timeout', '15', '--retries', '1', '--', url], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=65)
            data = json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            raise MediaError('유튜브 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요.', 504) from None
        except (ValueError, OSError):
            raise MediaError('곡 정보를 가져오지 못했습니다. 공개 링크와 서버의 인터넷 연결을 확인해주세요.') from None
        return {**parse_metadata(data), 'source_url': url}
    finally:
        _slots.release()
