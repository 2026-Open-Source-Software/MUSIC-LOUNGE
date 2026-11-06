import os
import shutil
import tempfile
from flask import Flask, request, jsonify, send_file
import yt_dlp

app = Flask(__name__)

@app.get("/")
def index():
    return jsonify({
        "service": "YT-DLP API Server",
        "endpoints": {
            "health": "/health",
            "search": "/api/search?q=<query>",
            "stream": "/api/stream?url=<youtube_url_or_id> (오디오 파일(mp3) 직접 반환)"
        }
    })

@app.get("/health")
def health():
    return jsonify({"status": "healthy"})

@app.get("/api/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "검색어를 입력해주세요."}), 400
    
    try:
        ydl_opts = {
            'default_search': 'ytsearch10',
            'extract_flat': True,
            'skip_download': True,
            'quiet': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web']
                }
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = query if query.startswith("ytsearch") else f"ytsearch10:{query}"
            result = ydl.extract_info(search_query, download=False)
            entries = result.get('entries', [])
            
            tracks = []
            for entry in entries:
                vid = entry.get('id')
                title = entry.get('title')
                duration = entry.get('duration')
                uploader = entry.get('uploader') or entry.get('channel')
                thumbnail = entry.get('thumbnail')
                if not thumbnail and vid:
                    thumbnail = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                
                tracks.append({
                    "id": vid,
                    "title": title,
                    "duration": duration,
                    "uploader": uploader,
                    "thumbnail": thumbnail,
                    "url": f"https://www.youtube.com/watch?v={vid}" if vid else entry.get('url')
                })
            return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/stream")
def stream_info():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "YouTube URL 또는 ID를 입력해주세요."}), 400
    
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={url}"

    tmp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'noprogress': True,
            'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web']
                }
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        audio_path = os.path.join(tmp_dir, f"{info['id']}.mp3")
        if not os.path.exists(audio_path):
            raise FileNotFoundError("오디오 추출에 실패했습니다.")

        response = send_file(
            audio_path,
            mimetype="audio/mpeg",
            download_name=f"{info.get('title') or info['id']}.mp3",
            conditional=True,
        )

        @response.call_on_close
        def _cleanup():
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return response
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
