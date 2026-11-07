import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch, Mock


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get('MUSIC_LOUNGE_DB')
        os.environ['MUSIC_LOUNGE_DB'] = str(Path(self.temp.name) / 'test.sqlite3')
        from storage import init_db
        init_db()
        from app import app
        self.owner, self.other, self.guest = [app.test_client() for _ in range(3)]
        for client, user in ((self.owner, 'owner'), (self.other, 'other')):
            client.post('/login', data={'nickname': user, 'user_id': user, 'password': 'test-pass'}, headers={'Accept': 'application/json'})
        self.metadata = {'title': 'Collection', 'entries': [{'id': 'M7lc1UVf-VE', 'title': 'First track', 'channel': 'Artist A', 'duration': 15}, {'id': 'dQw4w9WgXcQ', 'title': 'Second track', 'channel': 'Artist B'}, None, {'id': 'M7lc1UVf-VE', 'title': 'Duplicate'}]}
        self.payload = {'room': 'indie', 'title': 'My music', 'songs': ['First track | https://youtu.be/M7lc1UVf-VE']}
        self.playlist = self.owner.post('/api/me/playlists', json=self.payload).json
        self.endpoint = f'/api/playlists/{self.playlist["id"]}/audio/M7lc1UVf-VE'

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop('MUSIC_LOUNGE_DB', None)
        else:
            os.environ['MUSIC_LOUNGE_DB'] = self.old_db
        self.temp.cleanup()

    def wait_ready(self):
        for _ in range(100):
            r = self.owner.get(self.endpoint)
            if r.json['state'] != 'preparing':
                return r.json
            time.sleep(.02)
        self.fail('Background audio did not finish')

    def test_metadata_import_save_and_limits(self):
        self.assertEqual(self.guest.post('/api/import', json={'url': 'https://youtu.be/M7lc1UVf-VE'}).status_code, 401)
        with patch('youtube_import.subprocess.run', return_value=Mock(returncode=0, stdout=json.dumps(self.metadata))) as run:
            data = self.owner.post('/api/import', json={'url': 'https://youtube.com/playlist?list=PLabcdefghij'}).json
            self.assertEqual(len(data['tracks']), 2)
            self.assertEqual(data['skipped'], 2)
            self.assertIn('--skip-download', run.call_args.args[0])
        p = self.owner.post('/api/me/playlists', json={'room': 'indie', 'title': data['title'], 'songs': data['tracks']})
        self.assertEqual(p.status_code, 201)
        self.assertEqual(p.json['songs'][0]['artist'], 'Artist A')
        self.assertEqual(p.json['songs'][0]['duration'], 15)
        songs = [{'url': f'https://youtu.be/{i:011d}'} for i in range(100)]
        self.assertEqual(self.owner.post('/api/me/playlists', json={**self.payload, 'songs': songs}).status_code, 201)
        self.assertEqual(self.owner.post('/api/me/playlists', json={**self.payload, 'songs': songs + songs[:1]}).status_code, 400)

    def test_import_invalid_network_and_timeout(self):
        with patch('youtube_import.subprocess.run') as run:
            for url in ('http://youtube.com/watch?v=M7lc1UVf-VE', 'https://youtube.com.evil.com/watch?v=M7lc1UVf-VE', 'file:///etc/passwd', 'https://youtube.com/@channel'):
                self.assertEqual(self.owner.post('/api/import', json={'url': url}).status_code, 400)
            run.assert_not_called()
        for output in ('null', 'broken json'):
            with patch('youtube_import.subprocess.run', return_value=Mock(returncode=1, stdout=output)):
                self.assertEqual(self.owner.post('/api/import', json={'url': 'https://youtu.be/M7lc1UVf-VE'}).status_code, 502)
        with patch('youtube_import.subprocess.run', side_effect=subprocess.TimeoutExpired('yt_dlp', 65)):
            self.assertEqual(self.owner.post('/api/import', json={'url': 'https://youtu.be/M7lc1UVf-VE'}).status_code, 504)

    def test_audio_conversion_range_cache_and_permissions(self):
        import audio_service
        frames = (bytes.fromhex('fffb9064') + bytes(413)) * 40
        calls = []
        def convert(command, **kwargs):
            calls.append(command)
            template = Path(command[command.index('--output') + 1])
            template.with_name('track.mp3').write_bytes(frames)
            return Mock(returncode=0)
        self.assertEqual(self.guest.post(self.endpoint).status_code, 401)
        self.assertEqual(self.other.post(self.endpoint).status_code, 404)
        self.assertEqual(self.owner.post(self.endpoint.replace('M7lc1UVf-VE', 'abcdefghijk')).status_code, 404)
        with patch('audio_service.ffmpeg_dir', return_value=self.temp.name), patch('audio_service.subprocess.run', side_effect=convert):
            self.assertEqual(self.owner.post(self.endpoint).status_code, 202)
            info = self.wait_ready()
            self.assertEqual(info['state'], 'ready', info)
            self.assertEqual(self.owner.post(self.endpoint).json['state'], 'ready')
        self.assertEqual(len(calls), 1)
        self.assertIn('--extract-audio', calls[0])
        self.assertEqual(calls[0][calls[0].index('--audio-format') + 1], 'mp3')
        self.assertEqual(self.other.get(info['url']).status_code, 404)
        with self.owner.get(info['url']) as response:
            self.assertEqual(response.mimetype, 'audio/mpeg')
            self.assertEqual(response.data, frames)
        with self.owner.get(info['url'], headers={'Range': 'bytes=0-31'}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(len(response.data), 32)
        self.owner.put(f'/api/me/playlists/{self.playlist["id"]}/share', json={'room': 'indie', 'enabled': True})
        with self.other.get(info['url']) as response:
            self.assertEqual(response.status_code, 200)
        self.owner.put(f'/api/me/playlists/{self.playlist["id"]}/share', json={'room': 'indie', 'enabled': False})
        self.assertEqual(self.other.get(info['url']).status_code, 404)
        self.owner.delete(f'/api/me/playlists/{self.playlist["id"]}')
        self.assertEqual(self.owner.get(info['url']).status_code, 404)

    def test_missing_ffmpeg_and_failed_conversion(self):
        with patch.dict(os.environ, {'FFMPEG_LOCATION': ''}), patch('audio_service.shutil.which', return_value=None):
            result = self.owner.post(self.endpoint)
            self.assertEqual(result.status_code, 503)
            self.assertIn('FFmpeg', result.json['error'])
        with patch('audio_service.ffmpeg_dir', return_value=self.temp.name), patch('audio_service.subprocess.run', side_effect=subprocess.TimeoutExpired('yt_dlp', 180)):
            self.owner.post(self.endpoint)
            result = self.wait_ready()
            self.assertEqual(result['state'], 'error')
            self.assertTrue(result['error'])
        self.assertEqual(self.owner.get(self.endpoint + '/file').status_code, 409)


if __name__ == '__main__':
    unittest.main()
