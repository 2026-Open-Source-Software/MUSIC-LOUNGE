"""Bounded background MP3 preparation via backend yt-dlp API server and private, authenticated file delivery."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import tempfile
from threading import RLock, BoundedSemaphore
import time
import requests
from youtube_import import MediaError

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='mp3')
_slots = BoundedSemaphore(8)
_lock = RLock()
_jobs = {}
MAX_BYTES = 100 * 1024 * 1024
CACHE_BYTES = 512 * 1024 * 1024
CACHE_TTL = 86400


def cache_dir():
    db = Path(os.environ.get('MUSIC_LOUNGE_DB', str(Path(__file__).parent / 'instance' / 'lounge.sqlite3'))).resolve()
    root = db.parent / 'audio'
    root.mkdir(parents=True, exist_ok=True)
    return root


def audio_path(video_id):
    if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise MediaError('올바른 곡 ID가 아닙니다.', 400)
    return cache_dir() / (video_id + '.mp3')


def _cached(path):
    try:
        return path.is_file() and 0 < path.stat().st_size <= MAX_BYTES and time.time() - path.stat().st_mtime < CACHE_TTL
    except OSError:
        return False


def _prune(root, keep):
    files = sorted(root.glob('*.mp3'), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for path in files:
        try:
            size = path.stat().st_size
            if path != keep and (total > CACHE_BYTES or time.time() - path.stat().st_mtime >= CACHE_TTL):
                path.unlink()
                total -= size
        except OSError:
            pass


def _convert(video_id, path, key):
    error = None
    ytdlp_url = os.environ.get('YTDLP_SERVER_URL', 'http://ytdlp-server:5001')
    try:
        with tempfile.TemporaryDirectory(prefix='prepare-', dir=path.parent) as folder:
            output_path = Path(folder) / 'track.mp3'
            res = requests.get(f"{ytdlp_url}/api/stream", params={'url': video_id}, stream=True, timeout=180)
            if res.status_code != 200:
                raise MediaError('MP3를 준비하지 못했습니다. 공개·재생 가능한 영상인지 확인해주세요. 라이브·30분 초과·100MB 초과 영상은 지원하지 않습니다.')
            with open(output_path, 'wb') as f:
                for chunk in res.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            if not output_path.is_file() or not 0 < output_path.stat().st_size <= MAX_BYTES:
                raise MediaError('MP3를 준비하지 못했습니다. 공개·재생 가능한 영상인지 확인해주세요. 라이브·30분 초과·100MB 초과 영상은 지원하지 않습니다.')
            os.replace(output_path, path)
            with _lock:
                _prune(path.parent, path)
    except requests.Timeout:
        error = 'MP3 변환 시간이 초과됐습니다. 잠시 후 다시 시도해주세요.'
    except MediaError as exc:
        error = str(exc)
    except Exception:
        error = 'MP3를 준비하지 못했습니다. 서버와 인터넷 연결을 확인해주세요.'
    finally:
        with _lock:
            _jobs[key] = {'state': 'error' if error else 'ready', 'error': error, 'updated': time.time()}
        _slots.release()


def prepare(video_id):
    path = audio_path(video_id)
    key = str(path)
    with _lock:
        for old_key, job in list(_jobs.items()):
            if job['state'] != 'preparing' and time.time() - job.get('updated', 0) > CACHE_TTL:
                _jobs.pop(old_key, None)
        if _cached(path):
            return {'state': 'ready'}
        if _jobs.get(key, {}).get('state') == 'preparing':
            return {'state': 'preparing'}
        if not _slots.acquire(blocking=False):
            raise MediaError('MP3 준비 요청이 많습니다. 잠시 후 다시 시도해주세요.', 429)
        _jobs[key] = {'state': 'preparing', 'updated': time.time()}
        try:
            _pool.submit(_convert, video_id, path, key)
        except Exception:
            _slots.release()
            _jobs.pop(key, None)
            raise MediaError('MP3 준비 작업을 시작하지 못했습니다.', 503) from None
    return {'state': 'preparing'}


def status(video_id):
    path = audio_path(video_id)
    with _lock:
        if _cached(path):
            return {'state': 'ready'}
        item = _jobs.get(str(path))
        if item:
            return {k: v for k, v in item.items() if k != 'updated'}
    return {'state': 'idle'}
