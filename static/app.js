import { YoutubeImporter } from "./js/youtube-import.js";
import { api, RoomConnection } from "./js/api.js";
import { el, renderPlayer } from "./js/playlist-ui.js";
import { collectButton } from "./js/collect.js";
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
// 메인·개별 라운지 입력창. AI 추천은 지금 보고 있는 탭의 입력창에서 요청합니다.
const COMPOSERS = {
    "main-chat-tab": { form: "chat-form", input: "chat-input", toggle: "ai-recommend-btn" },
    "playlist-chat-tab": { form: "playlist-chat-form", input: "playlist-chat-input", toggle: "playlist-ai-btn" },
};
let activeChatTab = "main-chat-tab";
let aiParts = null; // AI 모드가 켜진 입력창
// 라운지에 처음 들어왔을 때 AI 버튼 위에 사용법 말풍선을 한 번 보여줍니다.
const AI_TIP_KEY = "musicLounge.aiTipSeen";
function showAiTip() {
    let seen = false;
    try { seen = localStorage.getItem(AI_TIP_KEY) === "1"; } catch { /* 저장할 수 없으면 매번 보여줍니다 */ }
    $("ai-tip").hidden = seen;
}
function hideAiTip() {
    $("ai-tip").hidden = true;
    $("ai-tip").classList.remove("peek");
    try { localStorage.setItem(AI_TIP_KEY, "1"); } catch { /* ignore */ }
}
function composerParts(tabId = activeChatTab) {
    const ids = COMPOSERS[tabId];
    const form = $(ids.form);
    return { form, input: $(ids.input), send: form.querySelector('[type="submit"]'), toggle: $(ids.toggle) };
}
const AI_PLACEHOLDER = "분위기 입력 / 비우면 내 플리 기반 추천";
const AI_MAX_REQUEST = 100;
const AI_TIMEOUT_MS = 300000; // the LLM and YouTube lookups can be slow; the server has its own limits
let listeningParticipants = [];
let listeningPlaylist = null;
let playbackActive = false;
let listeningRequest = Promise.resolve();

function reportListening() {
    if (!connected || !currentRoom) return;
    const room = currentRoom, token = connection.token;
    const playlist_id = playbackActive ? selectedPlaylist : null;
    listeningRequest = listeningRequest.catch(() => {}).then(async () => {
        if (room !== currentRoom || token !== connection.token || !connected) return;
        try { await api(`/api/rooms/${room}/listening`, "POST", { connection: token, playlist_id }); }
        catch (error) { /* Retry presence reporting on the next playback update. */ }
    });
}
function updateListeningPanel() {
    const audio = $("player").querySelector("audio");
    $("listening-title").textContent = listeningPlaylist?.title || "음악을 골라보세요";
    $("listening-state").textContent = !listeningPlaylist ? "플레이리스트를 선택하면 여기에 표시됩니다." : playbackActive ? "지금 재생 중 · " + listeningPlaylist.user : "재생 대기 · 곡을 선택해 재생하세요.";
    document.querySelector(".mini-record").classList.toggle("is-playing", playbackActive);
    updateListeningControls();
    $("playlist-chat-title").textContent = listeningPlaylist?.title || "플레이리스트를 선택해주세요";
    $("playlist-chat-description").textContent = listeningPlaylist ? "함께 음악을 듣는 사람들을 위한 채팅창입니다." : "플레이리스트를 선택해 채팅에 참여하세요.";
}
function resetListening() {
    selectChatTab("main-chat-tab");
    listeningPlaylist = null;
    playbackActive = false;
    updateListeningPanel();
    reportListening();
}

function feedback(error) { $("page-feedback").textContent = error.message; }
function selectChatTab(id) {
    if (aiMode && !aiBusy && id !== activeChatTab) setAiMode(false);
    activeChatTab = id;
    $(COMPOSERS[id].form).before($("ai-area")); // AI 결과·안내는 두 탭이 함께 씁니다
    ["main-chat-tab", "playlist-chat-tab"].forEach((tabId) => {
        const tab = $(tabId);
        const active = tabId === id;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        $(tab.getAttribute("aria-controls")).hidden = !active;
    });
}
const chatTabs = [$("main-chat-tab"), $("playlist-chat-tab")];
chatTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectChatTab(tab.id));
    tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
        selectChatTab(chatTabs[next].id);
        chatTabs[next].focus();
    });
});
function updateStatus(status) {
    connected = status === "connected";
    $("connection-status").textContent = { connecting: "연결 중…", connected: "● 실시간 연결", reconnecting: "재연결 중…", offline: "연결 대기" }[status];
    $("connection-status").dataset.state = status;
    $("chat-input").disabled = !connected || aiBusy;
    $("chat-form").querySelector('[type="submit"]').disabled = !connected || aiBusy;
    if (!aiBusy) $("ai-recommend-btn").disabled = $("playlist-ai-btn").disabled = !connected;
    if (!connected) {
        listeningParticipants = [];
        updateListeningPanel();
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
        reportListening();
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
            card.append( el("h3", "", room.name), el("p", "", "함께 듣는 공간으로 →"));
            card.addEventListener("click", () => enterRoom(id, room.name));
            $("room-cards").append(card);
        });
    } catch (error) { feedback(error); }
}
function enterRoom(room, name) {
    if (document.body.dataset.authenticated !== "true") { window.location.assign("/login"); return; }
    currentRoom = room;
    selectedMember = selectedPlaylist = null;
    listeningParticipants = [];
    resetListening();
    roomPlaylists = [];
    document.body.dataset.room = room;
    $("selection").classList.add("hidden");
    $("lounge").classList.remove("hidden");
    $("room-title").textContent = `${name} 라운지`;
    $("playlist-heading").textContent = "라운지의 음악";
    $("page-feedback").textContent = "";
    $("chat-feedback").textContent = "";
    $("ai-feedback").textContent = "";
    setAiMode(false);
    showAiTip();
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
    listeningParticipants = [];
    resetListening();
    delete document.body.dataset.room;
    $("lounge").classList.add("hidden");
    $("selection").classList.remove("hidden");
    $("player").replaceChildren();
    updateStatus("offline");
}
function renderParticipants(participants) {
    listeningParticipants = participants;
    updateListeningPanel();
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
function clearPlayer() { selectedPlaylist = null; $("player").replaceChildren(el("p", "", "♫ 목록에서 플레이리스트를 선택해주세요.")); resetListening(); }
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
    playbackActive = false;
    selectedPlaylist = playlist.id;
    listeningPlaylist = playlist;
    playerVersion = JSON.stringify([playlist.title, playlist.songs, playlist.user]);
    renderPlayer($("player"), playlist);
    updateListeningPanel();
    reportListening();
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
        const collect = collectButton(playlist, { className: "lounge-collect", onMessage: (text) => { $("collect-feedback").textContent = text; } });
        const together = playlist.sample ? null : el("button", "together-btn", "함께 듣기");
        if (together) {
            together.type = "button";
            together.dataset.playlistId = playlist.id;
            together.setAttribute("aria-label", `${playlist.title} 함께 듣기`);
            together.addEventListener("click", () => joinListenTogether(playlist));
        }
        const buttons = el("div", "playlist-buttons");
        buttons.append(like, ...(together ? [together] : []), ...(collect ? [collect] : []));
        item.append(info, buttons);
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
// 함께 듣기 참여 버튼. 방 만들기·입장·재생 동기화는 함께 듣기 API(/api/playlist-rooms) 연동 때
// document의 "listen-together" 이벤트를 받아 처리하고 event.preventDefault()를 호출하면 됩니다.
function joinListenTogether(playlist) {
    const event = new CustomEvent("listen-together", { detail: { room: currentRoom, playlist }, cancelable: true });
    if (!document.dispatchEvent(event)) return;
    $("collect-feedback").textContent = `‘${playlist.title}’ 함께 듣기는 곧 연결될 예정이에요.`;
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
    const sentAt = new Date(message.created_at);
    const day = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(sentAt);
    const previousMessage = box.querySelector(".message:last-of-type");
    if (previousMessage?.dataset.day !== day) {
        const divider = el("div", "chat-date-divider");
        const date = el("time", "", sentAt.toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "long", day: "numeric", weekday: "long" }));
        date.dateTime = day;
        divider.append(date);
        box.append(divider);
    }
    const item = el("div", `message${message.member_id === self ? " own-message" : ""}`);
    item.dataset.day = day;
    const meta = el("div", "message-meta");
    const time = el("time", "muted", sentAt.toLocaleTimeString("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" }));
    time.dateTime = message.created_at;
    meta.append(el("strong", "", message.user), time);
    item.append(meta, el("p", "", message.text));
    box.append(item);
    const messages = box.querySelectorAll(".message");
    if (messages.length > 100) {
        const oldest = messages[0];
        const divider = oldest.previousElementSibling;
        const next = oldest.nextElementSibling;
        oldest.remove();
        if (divider?.classList.contains("chat-date-divider") && (!next || next.classList.contains("chat-date-divider"))) divider.remove();
    }
    if (!history && (nearBottom || message.member_id === self)) scrollChat();
}
$("chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (aiMode && aiParts?.form === event.currentTarget) { await requestAiRecommendation(aiParts.input.value.trim().slice(0, AI_MAX_REQUEST)); return; }
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
$("playlist-chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (aiMode && aiParts?.form === event.currentTarget) { await requestAiRecommendation(aiParts.input.value.trim().slice(0, AI_MAX_REQUEST)); return; }
    // 개별 라운지 메시지 전송은 함께 듣기(/api/playlist-rooms) 연동 때 연결합니다.
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
    const parts = on ? composerParts() : aiParts;
    aiParts = on ? parts : null;
    if (!parts) return;
    const { form, input, send, toggle } = parts;
    form.classList.toggle("ai-mode", on);
    toggle.setAttribute("aria-pressed", String(on));
    if (on) {
        // keep what was in the input (a half-written message) and restore it when AI mode ends
        Object.assign(input.dataset, { draft: input.value, placeholder: input.placeholder, label: input.getAttribute("aria-label") || "", maxlength: input.maxLength, required: input.required });
        Object.assign(send.dataset, { text: send.textContent, label: send.getAttribute("aria-label") || "" });
        input.value = "";
        input.placeholder = AI_PLACEHOLDER;
        input.maxLength = AI_MAX_REQUEST;
        input.required = false; // an empty request means "songs like mine"
        input.setAttribute("aria-label", "AI에게 요청할 내용");
        send.textContent = "✨";
        send.setAttribute("aria-label", "AI에게 추천 요청하기");
        toggle.textContent = "✕";
        toggle.setAttribute("aria-label", "AI 추천 취소");
        input.focus();
    } else {
        input.value = input.dataset.draft || "";
        input.placeholder = input.dataset.placeholder;
        input.maxLength = Number(input.dataset.maxlength);
        input.required = input.dataset.required === "true";
        input.setAttribute("aria-label", input.dataset.label);
        send.textContent = send.dataset.text;
        send.setAttribute("aria-label", send.dataset.label);
        toggle.textContent = "✨";
        toggle.setAttribute("aria-label", "AI 추천");
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
    const { input, send, toggle } = aiParts || composerParts();
    aiBusy = true;
    input.disabled = send.disabled = toggle.disabled = $("ai-again-btn").disabled = true;
    toggle.textContent = "…";
    input.placeholder = request ? "AI가 노래를 고르는 중이에요…" : "내 플리 기반으로 찾는 중이에요…";
    $("ai-feedback").textContent = "";
    try {
        const result = await api(`/api/rooms/${room}/ai-recommend`, "POST", { request }, AbortSignal.timeout(AI_TIMEOUT_MS));
        if (room !== currentRoom) return;
        aiLastRequest = request;
        showAiResult(result);
        setAiMode(false);
    } catch (error) {
        if (room !== currentRoom) return;
        $("ai-feedback").textContent = error.name === "TimeoutError" ? "추천이 너무 오래 걸려요. 잠시 후 다시 시도해주세요." : error.message;
    } finally {
        aiBusy = false;
        input.disabled = send.disabled = toggle.disabled = !connected;
        $("ai-again-btn").disabled = false;
        toggle.textContent = aiMode ? "✕" : "✨";
        if (aiMode) input.placeholder = AI_PLACEHOLDER;
        if (aiMode && connected) input.focus(); // on an error the typed request stays so it can be retried
    }
}
[$("ai-recommend-btn"), $("playlist-ai-btn")].forEach((button) => button.addEventListener("click", () => { hideAiTip(); if (currentRoom && connected && !aiBusy) setAiMode(!aiMode); }));
$("ai-tip-close").addEventListener("click", hideAiTip);
// ✨ 버튼에 마우스를 올리거나 키보드로 이동하면 사용법 말풍선을 잠깐 보여줍니다(처음 안내를 닫은 뒤에도).
function peekAiTip(on) {
    const tip = $("ai-tip");
    if (on) {
        if (!tip.hidden || aiMode) return;
        tip.classList.add("peek");
        tip.hidden = false;
    } else if (tip.classList.contains("peek")) {
        tip.classList.remove("peek");
        tip.hidden = true;
    }
}
[$("ai-recommend-btn"), $("playlist-ai-btn")].forEach((button) => {
    button.addEventListener("mouseenter", () => peekAiTip(true));
    button.addEventListener("mouseleave", () => peekAiTip(false));
    button.addEventListener("focus", () => peekAiTip(true));
    button.addEventListener("blur", () => peekAiTip(false));
});
[$("chat-input"), $("playlist-chat-input")].forEach((input) => input.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && aiMode && !aiBusy) { event.preventDefault(); setAiMode(false); }
}));
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
        resetListening(); // a private playlist is not counted as "listening together" in the lounge
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
$("player").addEventListener("playback-state", (event) => {
    if (!currentRoom || event.detail.playlist.id !== selectedPlaylist) return;
    const changed = playbackActive !== event.detail.playing;
    playbackActive = event.detail.playing;
    updateListeningPanel();
    if (changed) reportListening();
});
function formatPlaybackTime(value) {
    const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
function updateListeningControls() {
    const audio = $("player").querySelector("audio");
    const ready = Boolean(audio?.getAttribute("src"));
    const duration = Number.isFinite(audio?.duration) ? audio.duration : 0;
    $("listening-play").disabled = !ready;
    $("listening-play").textContent = audio && !audio.paused ? "\u275a\u275a" : "\u25b6";
    $("listening-play").setAttribute("aria-label", audio && !audio.paused ? "\uc77c\uc2dc \uc815\uc9c0" : "\uc7ac\uc0dd");
    $("listening-time").textContent = formatPlaybackTime(audio?.currentTime);
    $("listening-duration").textContent = formatPlaybackTime(duration);
    $("listening-seek").max = duration;
    $("listening-seek").value = audio?.currentTime || 0;
    $("listening-seek").disabled = !ready || !duration;
    $("listening-volume").disabled = !audio;
    $("listening-volume").value = audio?.muted ? 0 : audio?.volume ?? 1;
}
["timeupdate", "durationchange", "loadedmetadata", "emptied", "play", "pause", "ended", "volumechange"].forEach((type) => {
    $("player").addEventListener(type, updateListeningControls, true);
});
$("listening-play").addEventListener("click", async () => {
    const audio = $("player").querySelector("audio");
    if (!audio) return;
    if (audio.paused) { try { await audio.play(); } catch (error) { feedback(error); } }
    else audio.pause();
});
$("listening-seek").addEventListener("input", (event) => {
    const audio = $("player").querySelector("audio");
    if (audio && Number.isFinite(audio.duration)) audio.currentTime = Number(event.target.value);
});
$("listening-volume").addEventListener("input", (event) => {
    const audio = $("player").querySelector("audio");
    if (audio) { audio.muted = false; audio.volume = Number(event.target.value); }
});
window.addEventListener("pagehide", () => connection.leave());
window.addEventListener("pageshow", (event) => { if (event.persisted && currentRoom) connection.join(currentRoom); });
loadRooms();
