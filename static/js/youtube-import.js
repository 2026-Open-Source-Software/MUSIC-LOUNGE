import { api } from "./api.js";
import { tracksToText, textToTracks } from "./playlist-ui.js";
function videoId(line) {
    try { const u = new URL(line.split(" | ").at(-1)); return u.hostname.endsWith("youtu.be") ? u.pathname.slice(1) : u.searchParams.get("v") || u.pathname.split("/")[2]; } catch { return null; }
}
export class YoutubeImporter {
    constructor({ dialog, prefix, songs, title, submit }) {
        const $ = (id) => document.getElementById(id);
        this.dialog = $(dialog); this.url = $(`${prefix}-url`); this.button = $(`${prefix}-button`);
        this.feedback = $(`${prefix}-feedback`); this.songs = $(songs); this.title = $(title); this.submit = $(submit);
        this.metadata = [];
        this.button.addEventListener("click", () => this.import());
        this.url.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.import(); } });
        this.dialog.addEventListener("close", () => this.reset());
        this.dialog.addEventListener("cancel", () => this.reset());
    }
    reset(existing = []) {
        this.controller?.abort(); this.controller = null;
        this.metadata = existing.filter((s) => typeof s === "object");
        this.url.value = ""; this.feedback.textContent = ""; this.busy(false);
    }
    busy(value) { this.loading = value; this.button.disabled = this.submit.disabled = this.url.disabled = value; this.button.textContent = value ? "가져오는 중…" : "곡 정보 가져오기"; }
    collect() { const known = new Map(this.metadata.map((s) => [tracksToText([s]), s])); return textToTracks(this.songs.value).map((s) => known.get(s) || s); }
    async import() {
        if (this.loading) return;
        const url = this.url.value.trim();
        if (!url) { this.feedback.textContent = "유튜브 영상·재생목록 링크를 입력해주세요."; return; }
        const controller = new AbortController(); this.controller = controller; this.busy(true);
        this.feedback.textContent = "곡 제목과 정보를 확인하고 있어요. 최대 65초 정도 걸릴 수 있습니다.";
        try {
            const data = await api("/api/import", "POST", { url }, controller.signal);
            if (this.controller !== controller || !this.dialog.open) return;
            const lines = textToTracks(this.songs.value), seen = new Set(lines.map(videoId).filter(Boolean)), added = [];
            let duplicate = 0, overflow = 0;
            data.tracks.forEach((song) => {
                if (seen.has(song.video_id)) { duplicate++; return; }
                seen.add(song.video_id);
                if (lines.length + added.length >= 100) { overflow++; return; }
                added.push(song);
            });
            this.metadata.push(...added);
            this.songs.value = [...lines, ...added.map((s) => tracksToText([s]))].join("\n");
            if (!this.title.value.trim()) this.title.value = data.title;
            this.feedback.textContent = `${added.length}곡을 추가했어요.${duplicate ? ` 중복 ${duplicate}곡 제외.` : ""}${data.skipped ? ` 비공개·삭제·중복 ${data.skipped}개 제외.` : ""}${overflow ? ` 100곡 제한으로 ${overflow}곡 제외.` : ""}${data.limit_reached ? " 재생목록의 앞 100개까지만 확인합니다." : ""} 저장 후 MP3로 감상할 수 있어요.`;
            this.url.value = "";
        } catch (error) { if (this.controller === controller && error.name !== "AbortError") this.feedback.textContent = error.message; }
        finally { if (this.controller === controller) { this.controller = null; this.busy(false); } }
    }
}
