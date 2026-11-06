"""Bounded background MP3 preparation and private, authenticated file delivery."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from threading import RLock, BoundedSemaphore
import time
from youtube_import import MediaError, check_module

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


def ffmpeg_dir():
    configured = os.environ.get('FFMPEG_LOCATION')
    if configured:
        candidate = Path(configured)
        folder = candidate.parent if candidate.is_file() else candidate
        ffmpeg = folder / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        ffprobe = folder / ('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
        if ffmpeg.is_file() and ffprobe.is_file():
            return str(folder.resolve())
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if ffmpeg and ffprobe:
        return str(Path(ffmpeg).parent)
    raise MediaError('MP3 재생에 필요한 FFmpeg·FFprobe가 없습니다. 설치 후 PATH 또는 FFMPEG_LOCATION을 설정하고 서버를 재시작해주세요.', 503)


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


def _convert(video_id, path, ffmpeg, key):
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='prepare-', dir=path.parent) as folder:
            output = Path(folder) / 'track.%(ext)s'
            command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-cache-dir', '--no-playlist',
                       '--no-progress', '--format', 'bestaudio/best', '--extract-audio', '--audio-format', 'mp3',
                       '--audio-quality', '192K', '--ffmpeg-location', ffmpeg, '--max-filesize', '100M',
                       '--match-filter', '!is_live & duration <= 1800', '--socket-timeout', '15', '--retries', '1',
                       '--output', str(output), '--', 'https://www.youtube.com/watch?v=' + video_id]
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
            converted = Path(folder) / 'track.mp3'
            if result.returncode or not converted.is_file() or not 0 < converted.stat().st_size <= MAX_BYTES:
                raise MediaError('MP3를 준비하지 못했습니다. 공개·재생 가능한 영상인지 확인해주세요. 라이브·30분 초과·100MB 초과 영상은 지원하지 않습니다.')
            os.replace(converted, path)
            with _lock:
                _prune(path.parent, path)
    except subprocess.TimeoutExpired:
        error = 'MP3 변환 시간이 초과됐습니다. 잠시 후 다시 시도해주세요.'
    except MediaError as exc:
        error = str(exc)
    except Exception:
        error = 'MP3를 준비하지 못했습니다. 서버의 FFmpeg와 인터넷 연결을 확인해주세요.'
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
        check_module()
        ffmpeg = ffmpeg_dir()
        if not _slots.acquire(blocking=False):
            raise MediaError('MP3 준비 요청이 많습니다. 잠시 후 다시 시도해주세요.', 429)
        _jobs[key] = {'state': 'preparing', 'updated': time.time()}
        try:
            _pool.submit(_convert, video_id, path, ffmpeg, key)
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
