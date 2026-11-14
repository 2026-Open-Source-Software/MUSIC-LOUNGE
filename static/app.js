import { YoutubeImporter } from "./js/youtube-import.js";
import { api, RoomConnection } from "./js/api.js";
import { el, renderPlayer } from "./js/playlist-ui.js";
const $ = (id) => document.getElementById(id);
const importer = new YoutubeImporter({ dialog: "create-dialog", prefix: "create-import", songs: "playlist-songs", title: "playlist-title", submit: "create-submit" });
let currentRoom = null;
let roomPlaylists = [];
let selectedMember = null;
let selectedPlaylist = null;
let self = null;
let refreshId = 0;
let connected = false;
let playerVersion = null;
const messageIds = new Set();
let aiBusy = false;
let aiMode = false;
let aiLastRequest = "";
let aiSongs = [];
let chatDraft = "";
const CHAT_PLACEHOLDER = $("chat-input").placeholder;
const CHAT_MAXLENGTH = $("chat-input").maxLength;
const AI_PLACEHOLDER = "듣고 싶은 분위기 (비워두면 비슷한 곡 추천)";
const AI_MAX_REQUEST = 100;
const AI_TIMEOUT_MS = 300000; // the LLM on a CPU and YouTube lookups can be slow; the server has its own limits

function feedback(error) { $("page-feedback").textContent = error.message; }
function updateStatus(status) {
    connected = status === "connected";
    $("connection-status").textContent = { connecting: "연결 중…", connected: "● 실시간 연결", reconnecting: "재연결 중…", offline: "연결 대기" }[status];
    $("connection-status").dataset.state = status;
    $("chat-input").disabled = !connected || aiBusy;
    $("chat-form").querySelector('[type="submit"]').disabled = !connected || aiBusy;
    if (!aiBusy) $("ai-recommend-btn").disabled = !connected;
    if (!connected) {
        $("participant-list").replaceChildren(el("p", "muted", "실시간 연결을 확인하고 있어요."));
        $("participant-count").textContent = "—";
    }
}
const connection = new RoomConnection((kind, data) => {
    if (data.room && data.room !== currentRoom) return;
    if (kind === "snapshot") {
        self = data.self;
        roomPlaylists = data.playlists;
        $("chat-messages").replaceChildren();
        messageIds.clear();
        data.messages.forEach((message) => appendMessage(message, true));
        if (!data.messages.length) $("chat-messages").append(el("p", "muted chat-empty", "첫 인사를 건네보세요. 이 라운지의 대화가 여기 쌓입니다."));
        renderParticipants(data.participants);
        renderPlaylists();
        scrollChat();
    } else if (kind === "presence") {
        renderParticipants(data.participants);
    } else if (kind === "message") {
        appendMessage(data.message);
    } else if (kind === "playlists_changed") {
        refreshPlaylists();
    }
}, updateStatus);

async function loadRooms() {
    try {
        const rooms = await api("/api/rooms");
        $("room-cards").replaceChildren();
        Object.entries(rooms).forEach(([id, room]) => {
            const card = el("button", "room-card");
            card.type = "button";
            card.dataset.room = id;
            card.append(el("div", "room-icon", room.emoji), el("h3", "", room.name), el("p", "", "함께 듣는 공간으로 →"));
            card.addEventListener("click", () => enterRoom(id, room.name));
            $("room-cards").append(card);
        });
    } catch (error) { feedback(error); }
}
function enterRoom(room, name) {
    if (document.body.dataset.authenticated !== "true") { window.location.assign("/login"); return; }
    currentRoom = room;
    selectedMember = selectedPlaylist = null;
    roomPlaylists = [];
    document.body.dataset.room = room;
    $("selection").classList.add("hidden");
    $("lounge").classList.remove("hidden");
    $("room-title").textContent = `${name} 라운지`;
    $("playlist-heading").textContent = "라운지의 음악";
    $("page-feedback").textContent = "";
    $("chat-feedback").textContent = "";
    setAiMode(false);
    $("chat-input").value = "";
    closeAiResult();
    $("chat-messages").replaceChildren();
    $("player").replaceChildren(el("p", "", "♫ 플레이리스트를 선택해주세요."));
    renderPlaylists();
    connection.join(room);
}
function leaveRoom() {
    closeAiResult();
    setAiMode(false);
    connection.leave();
    currentRoom = null;
    refreshId++;
    roomPlaylists = [];
    selectedMember = selectedPlaylist = null;
    delete document.body.dataset.room;
    $("lounge").classList.add("hidden");
    $("selection").classList.remove("hidden");
    $("player").replaceChildren();
    updateStatus("offline");
}
function renderParticipants(participants) {
    const selected = participants.find((person) => person.member_id === selectedMember);
    if (selected) $("playlist-heading").textContent = `${selected.nickname}님의 음악`;
    $("participant-count").textContent = `${participants.length}명`;
    $("participant-list").replaceChildren();
    participants.forEach((person) => {
        const button = el("button", `participant${person.member_id === selectedMember ? " selected" : ""}`);
        button.type = "button";
        button.dataset.member = person.member_id;
        button.setAttribute("aria-pressed", String(person.member_id === selectedMember));
        button.append(el("span", "participant-avatar", person.nickname.slice(0, 1)), el("span", "participant-name", person.nickname), el("small", "muted", person.member_id === self ? "나" : "●"));
        button.addEventListener("click", () => {
            selectedMember = person.member_id;
            $("playlist-heading").textContent = `${person.nickname}님의 음악`;
            renderParticipants(participants);
            clearPlayer();
            renderPlaylists();
        });
        $("participant-list").append(button);
    });
}
function clearPlayer() { selectedPlaylist = null; $("player").replaceChildren(el("p", "", "♫ 목록에서 플레이리스트를 선택해주세요.")); }
async function refreshPlaylists() {
    const room = currentRoom;
    const version = ++refreshId;
    try {
        const lists = await api(`/api/rooms/${room}/playlists`);
        if (room !== currentRoom || version !== refreshId) return;
        roomPlaylists = lists;
        renderPlaylists();
    } catch (error) { feedback(error); }
}
function showPlaylist(playlist) {
    selectedPlaylist = playlist.id;
    playerVersion = JSON.stringify([playlist.title, playlist.songs, playlist.user]);
    renderPlayer($("player"), playlist);
}
function renderPlaylists() {
    const visible = selectedMember ? roomPlaylists.filter((p) => p.member_id === selectedMember) : roomPlaylists;
    $("playlist-description").textContent = selectedMember ? "이 사용자가 현재 라운지에 공유한 플레이리스트입니다." : "참여자의 닉네임을 클릭하거나 아래 공유 목록을 선택하세요.";
    $("playlist-list").replaceChildren();
    visible.forEach((playlist) => {
        const item = el("div", "playlist-item");
        const info = el("div", "item-text");
        const open = el("button", "user-btn", playlist.title);
        open.type = "button";
        open.addEventListener("click", () => showPlaylist(playlist));
        const author = el("button", "author-button", `${playlist.user} · ${playlist.songs.length}곡${playlist.sample ? " · 예시 목록" : ""}`);
        author.type = "button";
        author.addEventListener("click", () => {
            selectedMember = playlist.member_id;
            $("playlist-heading").textContent = `${playlist.user}님의 음악`;
            clearPlayer();
            renderPlaylists();
            highlightMember();
        });
        info.append(open, author);
        const like = el("button", "heart", `♥ ${playlist.likes}`);
        like.type = "button";
        like.setAttribute("aria-label", `${playlist.title} 좋아요 ${playlist.likes}`);
        like.disabled = Boolean(playlist.sample);
        if (playlist.sample) like.title = "기존 예시 플레이리스트";
        like.addEventListener("click", async () => {
            like.disabled = true;
            try { await api(`/api/rooms/${currentRoom}/playlists/${playlist.id}/like`, "POST"); }
            catch (error) { feedback(error); }
            finally { like.disabled = false; }
        });
        item.append(info, like);
        $("playlist-list").append(item);
    });
    if (!visible.length) $("playlist-list").append(el("p", "empty-state", selectedMember ? "아직 이 라운지에 공유한 플레이리스트가 없어요." : "아직 공유된 음악이 없어요. 첫 플레이리스트를 공유해보세요."));
    $("ranking-list").replaceChildren();
    roomPlaylists.slice(0, 3).forEach((playlist, i) => {
        const button = el("button", "compact-rank");
        button.type = "button";
        button.append(el("span", "rank-num", String(i + 1)), el("span", "rank-title", playlist.title), el("small", "", `♥ ${playlist.likes}`));
        button.addEventListener("click", () => showPlaylist(playlist));
        $("ranking-list").append(button);
    });
    if (selectedPlaylist) {
        const playlist = roomPlaylists.find((p) => p.id === selectedPlaylist);
        if (!playlist) clearPlayer();
        else if (JSON.stringify([playlist.title, playlist.songs, playlist.user]) !== playerVersion) showPlaylist(playlist);
    }
}
function highlightMember() {
    document.querySelectorAll(".participant").forEach((button) => {
        const active = button.dataset.member === selectedMember;
        button.classList.toggle("selected", active);
        button.setAttribute("aria-pressed", String(active));
    });
}
function scrollChat() { $("chat-messages").scrollTop = $("chat-messages").scrollHeight; }
function appendMessage(message, history = false) {
    if (messageIds.has(message.id)) return;
    messageIds.add(message.id);
    const box = $("chat-messages");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 100;
    box.querySelector(".chat-empty")?.remove();
    const item = el("div", `message${message.member_id === self ? " own-message" : ""}`);
    const meta = el("div", "message-meta");
    const time = el("time", "muted", new Date(message.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }));
    time.dateTime = message.created_at;
    meta.append(el("strong", "", message.user), time);
    item.append(meta, el("p", "", message.text));
    box.append(item);
    if (box.children.length > 100) box.firstElementChild.remove();
    if (!history && (nearBottom || message.member_id === self)) scrollChat();
}
$("chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (aiMode) { await requestAiRecommendation($("chat-input").value.trim().slice(0, AI_MAX_REQUEST)); return; }
    const text = $("chat-input").value.trim();
    if (!text || !connected) return;
    const room = currentRoom;
    const button = event.submitter || event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
        const message = await api(`/api/rooms/${room}/messages`, "POST", { text });
        if (room !== currentRoom) return;
        appendMessage(message);
        $("chat-input").value = "";
        $("chat-feedback").textContent = "";
        $("chat-input").focus();
    } catch (error) { $("chat-feedback").textContent = error.message; }
    finally { button.disabled = !connected; }
});
// ---- AI recommendation: button -> the chat input becomes a request box -> Enter ----
function closeAiResult() {
    aiSongs = [];
    $("ai-result").classList.add("hidden");
    $("ai-result-list").replaceChildren();
    $("ai-context-list").replaceChildren();
    $("ai-save-feedback").textContent = "";
}
function setAiMode(on) {
    if (on === aiMode) return;
    aiMode = on;
    const input = $("chat-input");
    const send = $("chat-form").querySelector('[type="submit"]');
    const toggle = $("ai-recommend-btn");
    $("chat-form").classList.toggle("ai-mode", on);
    $("ai-mode-hint").classList.toggle("hidden", !on);
    toggle.setAttribute("aria-pressed", String(on));
    if (on) {
        chatDraft = input.value; // keep a half-written chat message
        input.value = "";
        input.placeholder = AI_PLACEHOLDER;
        input.maxLength = AI_MAX_REQUEST;
        input.required = false; // an empty request means "songs like mine"
        input.setAttribute("aria-label", "AI에게 요청할 내용");
        send.textContent = "✨";
        send.setAttribute("aria-label", "AI에게 추천 요청하기");
        toggle.textContent = "취소";
        input.focus();
    } else {
        input.value = chatDraft;
        chatDraft = "";
        input.placeholder = CHAT_PLACEHOLDER;
        input.maxLength = CHAT_MAXLENGTH;
        input.required = true;
        input.setAttribute("aria-label", "채팅 메시지");
        send.textContent = "↑";
        send.setAttribute("aria-label", "메시지 보내기");
        toggle.textContent = "✨ AI 추천";
    }
}
function formatDuration(seconds) {
    return seconds > 0 ? ` · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "";
}
function showAiResult(result) {
    aiSongs = result.recommendations;
    $("ai-result-list").replaceChildren(...aiSongs.map((song) => {
        const item = el("li");
        const link = el("a", "youtube-link", "YouTube ↗");
        link.href = song.url; link.target = "_blank"; link.rel = "noopener";
        item.append(el("strong", "", song.title), el("span", "muted", `${song.artist ? ` · ${song.artist}` : ""}${formatDuration(song.duration)}`), link);
        return item;
    }));
    const stats = result.stats || {};
    const notes = ["모두 음원 카탈로그(iTunes)에서 실제 발매곡으로 확인하고, 그 곡의 YouTube 영상을 연결했어요. 곡 이름은 카탈로그 표기예요."];
    if (stats.dropped) notes.push(`확인되지 않은 ${stats.dropped}곡은 뺐어요.`);
    if (stats.unavailable) notes.push("음원 확인 횟수 제한에 걸려 일부만 확인했어요. 1분쯤 뒤에 다시 추천받으면 더 나와요.");
    if (result.mode === "related" && stats.mix_ok < stats.mix_total) notes.push(`YouTube 관련곡 ${stats.mix_total - stats.mix_ok}건은 가져오지 못했어요.`);
    $("ai-result-note").textContent = notes.join(" ");
    const context = result.context || {};
    const rows = [
        el("li", "", `요청: ${context.request || "없음"}`),
        el("li", "", (context.seeds || []).length ? "기준 곡:" : "기준 곡: 없음 (내 플레이리스트나 좋아요한 플레이리스트에 곡이 없어요)"),
        ...(context.seeds || []).map((seed) => el("li", "ai-context-seed", `${seed.title} (${seed.why})`)),
    ];
    $("ai-context-list").replaceChildren(...rows);
    $("ai-save-feedback").textContent = "";
    $("ai-save-btn").disabled = false;
    $("ai-result").classList.remove("hidden");
    $("ai-result").scrollTop = 0;
}
async function requestAiRecommendation(request) {
    const room = currentRoom;
    if (!room || !connected || aiBusy) return;
    const input = $("chat-input");
    const send = $("chat-form").querySelector('[type="submit"]');
    const toggle = $("ai-recommend-btn");
    aiBusy = true;
    input.disabled = send.disabled = toggle.disabled = $("ai-again-btn").disabled = true;
    toggle.textContent = "추천 중…";
    input.placeholder = request ? "AI가 노래를 고르는 중이에요…" : "비슷한 곡을 찾는 중이에요…";
    $("chat-feedback").textContent = "";
    try {
        const result = await api(`/api/rooms/${room}/ai-recommend`, "POST", { request }, AbortSignal.timeout(AI_TIMEOUT_MS));
        if (room !== currentRoom) return;
        aiLastRequest = request;
        showAiResult(result);
        setAiMode(false);
    } catch (error) {
        if (room !== currentRoom) return;
        $("chat-feedback").textContent = error.name === "TimeoutError" ? "추천이 너무 오래 걸려요. 잠시 후 다시 시도해주세요." : error.message;
    } finally {
        aiBusy = false;
        input.disabled = send.disabled = toggle.disabled = !connected;
        $("ai-again-btn").disabled = false;
        toggle.textContent = aiMode ? "취소" : "✨ AI 추천";
        input.placeholder = aiMode ? AI_PLACEHOLDER : CHAT_PLACEHOLDER;
        if (aiMode && connected) input.focus(); // on an error the typed request stays so it can be retried
    }
}
$("ai-recommend-btn").addEventListener("click", () => { if (currentRoom && connected && !aiBusy) setAiMode(!aiMode); });
$("chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Escape" && aiMode && !aiBusy) { event.preventDefault(); setAiMode(false); }
});
$("ai-result-close").addEventListener("click", closeAiResult);
$("ai-again-btn").addEventListener("click", () => requestAiRecommendation(aiLastRequest));
$("ai-save-btn").addEventListener("click", async () => {
    if (!aiSongs.length) return;
    const button = $("ai-save-btn");
    button.disabled = true;
    const now = new Date();
    const title = `AI 추천 · ${now.getMonth() + 1}/${now.getDate()} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    try {
        const songs = aiSongs.map(({ title, url, artist, duration }) => ({ title, url, artist, duration }));
        const playlist = await api("/api/me/playlists", "POST", { title, songs, room: currentRoom });
        $("ai-save-feedback").textContent = `‘${playlist.title}’으로 저장했어요. 아래 플레이어에서 바로 들을 수 있어요. 라운지 공유는 ‘내 플레이리스트 공유’에서 할 수 있어요.`;
        selectedPlaylist = null; // not shared to the room, so a room refresh must not clear it
        renderPlayer($("player"), playlist);
    } catch (error) {
        $("ai-save-feedback").textContent = error.message;
        button.disabled = false;
    }
});
$("back-btn").addEventListener("click", leaveRoom);
$("show-all").addEventListener("click", () => { selectedMember = null; $("playlist-heading").textContent = "라운지의 음악"; clearPlayer(); renderPlaylists(); highlightMember(); });
$("create-btn").addEventListener("click", () => { $("create-form").reset(); importer.reset(); $("create-feedback").textContent = ""; $("create-dialog").showModal(); });
$("cancel-btn").addEventListener("click", () => $("create-dialog").close());
$("create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
        await api(`/api/rooms/${currentRoom}/playlists`, "POST", { title: $("playlist-title").value.trim(), songs: importer.collect() });
        $("create-dialog").close();
        $("page-feedback").textContent = "내 계정에 저장하고 라운지에 공유했어요.";
    } catch (error) { $("create-feedback").textContent = error.message; }
    finally { button.disabled = false; }
});
async function renderShareList() {
    const room = currentRoom;
    const playlists = await api("/api/me/playlists");
    if (currentRoom !== room) return;
    $("share-list").replaceChildren();
    if (!playlists.length) $("share-list").append(el("p", "muted", "아직 내 플레이리스트가 없어요. 새 플레이리스트를 만들어보세요."));
    playlists.forEach((playlist) => {
        const item = el("div", "playlist-item");
        const active = playlist.shared_rooms.includes(room);
        const button = el("button", active ? "secondary" : "primary", active ? "공유 해제" : "공유하기");
        button.type = "button";
        button.addEventListener("click", async () => {
            button.disabled = true;
            try { await api(`/api/me/playlists/${playlist.id}/share`, "PUT", { room, enabled: !active }); await renderShareList(); }
            catch (error) { $("share-feedback").textContent = error.message; button.disabled = false; }
        });
        item.append(el("strong", "item-text", playlist.title), button);
        $("share-list").append(item);
    });
}
$("share-btn").addEventListener("click", async () => {
    $("share-feedback").textContent = "";
    $("share-list").replaceChildren(el("p", "muted", "내 목록을 불러오는 중…"));
    $("share-dialog").showModal();
    try { await renderShareList(); } catch (error) { $("share-feedback").textContent = error.message; }
});
$("share-close").addEventListener("click", () => $("share-dialog").close());
window.addEventListener("pagehide", () => connection.leave());
window.addEventListener("pageshow", (event) => { if (event.persisted && currentRoom) connection.join(currentRoom); });
loadRooms();
