import { api } from "./api.js";
export const el = (tag, className = "", text) => {
    const node = document.createElement(tag); node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};
export const tracksToText = (songs) => songs.map((s) => typeof s === "string" ? s : `${s.title} | ${s.url}`).join("\n");
export const textToTracks = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);
export function trackList(songs, onPlay) {
    const list = el("ol", "track-list");
    songs.forEach((song) => {
        const item = el("li", "track-row");
        if (typeof song === "string") item.append(el("span", "", song), el("small", "muted", "기존 곡 · 유튜브 링크 미등록"));
        else if (/^[\w-]{11}$/.test(song.video_id)) {
            const title = el(onPlay ? "button" : "span", onPlay ? "track-play" : "", `${onPlay ? "▶ " : ""}${song.title}`);
            if (onPlay) { title.type = "button"; title.addEventListener("click", () => onPlay(song)); }
            const link = el("a", "youtube-link", "YouTube ↗");
            link.href = `https://www.youtube.com/watch?v=${song.video_id}`; link.target = "_blank"; link.rel = "noopener";
            item.append(title);
            if (song.artist) item.append(el("small", "track-artist", song.artist));
            item.append(link);
        }
        list.append(item);
    });
    return list;
}
let activeAudio = null;
export function renderPlayer(container, playlist) {
    container.replaceChildren(el("div", "eyebrow", "MP3 PLAYER"), el("h3", "", playlist.title), el("p", "muted", `${playlist.user} · ${playlist.songs.length}곡`));
    const playable = playlist.songs.filter((s) => typeof s === "object" && /^[\w-]{11}$/.test(s.video_id));
    const controls = el("div", "mp3-controls"), now = el("p", "now-playing", "재생할 곡을 선택해주세요."), status = el("p", "feedback audio-status");
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const audio = el("audio", "mp3-audio"); audio.controls = true; audio.preload = "none";
    const prev = el("button", "secondary", "← 이전"), all = el("button", "primary play-all", "▶ 전체 재생"), next = el("button", "secondary", "다음 →");
    [prev, all, next].forEach((b) => b.type = "button");
    controls.append(prev, all, next);
    let index = -1, controller;
    const wait = (signal) => new Promise((resolve, reject) => {
        const onAbort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", onAbort); resolve(); }, 900);
        signal.addEventListener("abort", onAbort, { once: true });
    });
    async function play(song) {
        if (!Number.isInteger(playlist.id)) { status.textContent = "계정에 저장한 뒤 재생해주세요."; return; }
        controller?.abort(); controller = new AbortController();
        const signal = controller.signal;
        activeAudio?.pause(); activeAudio = audio;
        audio.pause(); audio.removeAttribute("src"); audio.load();
        index = playable.findIndex((s) => s.video_id === song.video_id);
        now.textContent = `${index + 1} / ${playable.length} · ${song.title}`;
        status.textContent = "MP3를 준비하고 있어요. 처음 재생할 때는 변환 시간이 필요합니다.";
        const endpoint = `/api/playlists/${playlist.id}/audio/${song.video_id}`;
        try {
            let info = await api(endpoint, "POST", undefined, signal);
            const deadline = Date.now() + 200000;
            while (info.state === "preparing" && Date.now() < deadline) {
                await wait(signal);
                if (!audio.isConnected) return;
                info = await api(endpoint, "GET", undefined, signal);
            }
            if (signal.aborted || !audio.isConnected) return;
            if (info.state !== "ready") throw new Error(info.error || "MP3 준비가 지연되고 있어요. 다시 재생해주세요.");
            audio.preload = "metadata";
            audio.src = info.url;
            audio.load();
            status.textContent = "MP3 준비 완료";
            try { await audio.play(); }
            catch { if (!signal.aborted) status.textContent = "아래 오디오 재생 버튼을 눌러주세요."; }
        } catch (error) { if (error.name !== "AbortError") status.textContent = error.message; }
    }
    prev.addEventListener("click", () => { if (playable.length) play(playable[Math.max(0, index - 1)]); });
    next.addEventListener("click", () => { if (playable.length) play(playable[Math.min(playable.length - 1, index + 1)]); });
    all.addEventListener("click", () => { if (playable.length) play(playable[0]); });
    audio.addEventListener("play", () => { if (activeAudio !== audio) activeAudio?.pause(); activeAudio = audio; });
    audio.addEventListener("ended", () => { if (index + 1 < playable.length) play(playable[index + 1]); else status.textContent = "플레이리스트 재생이 끝났어요."; });
    audio.addEventListener("error", () => { if (audio.getAttribute("src")) status.textContent = "오디오를 재생하지 못했습니다. 다시 시도하거나 YouTube 링크로 열어주세요."; });
    const observer = new MutationObserver(() => {
        if (!audio.isConnected) { controller?.abort(); audio.pause(); audio.removeAttribute("src"); audio.load(); observer.disconnect(); }
    });
    container.append(controls, now, audio, status, trackList(playlist.songs, play), el("p", "muted playback-help", "처음 선택한 곡은 서버에서 MP3로 준비한 후 재생합니다. 준비된 곡은 캐시를 사용합니다."));
    observer.observe(document.body, { childList: true, subtree: true });
    if (!playable.length) { controls.remove(); audio.remove(); status.textContent = "재생 가능한 유튜브 링크가 없습니다."; observer.disconnect(); }
}
