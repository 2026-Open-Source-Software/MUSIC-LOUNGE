import { YoutubeImporter } from "./js/youtube-import.js";
import { api, RoomConnection } from "./js/api.js";
import { el, renderPlayer, trackList } from "./js/playlist-ui.js";
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
        catch (error) { if (room === currentRoom && token === connection.token) $("listening-count").textContent = "청취 인원을 확인하지 못했어요."; }
    });
}
function updateListeningPanel() {
    const audio = $("player").querySelector("audio");
    $("listening-title").textContent = listeningPlaylist?.title || "음악을 골라보세요";
    $("listening-state").textContent = !listeningPlaylist ? "플레이리스트를 선택하면 여기에 표시됩니다." : playbackActive ? "지금 재생 중 · " + listeningPlaylist.user : "재생 대기 · 곡을 선택해 재생하세요.";
    document.querySelector(".mini-record").classList.toggle("is-playing", playbackActive);
    $("listening-mute").disabled = !audio;
    $("listening-mute").setAttribute("aria-pressed", String(Boolean(audio?.muted)));
    $("listening-mute").textContent = audio?.muted ? "음소거 해제" : "음소거";
    $("listening-tracks-toggle").disabled = !listeningPlaylist;
    $("listening-tracks-toggle").textContent = listeningPlaylist ? `곡 목록 · ${listeningPlaylist.songs.length}` : "곡 목록";
    const count = listeningParticipants.filter((person) => person.listening?.includes(selectedPlaylist)).length;
    $("listening-count").textContent = connected ? `함께 듣는 사람 ${count}명` : "청취 인원 · 연결 대기";
}
function resetListening() {
    listeningPlaylist = null;
    playbackActive = false;
    $("listening-tracks").replaceChildren();
    $("listening-tracks").classList.add("hidden");
    $("listening-tracks-toggle").setAttribute("aria-expanded", "false");
    updateListeningPanel();
    reportListening();
}

function feedback(error) { $("page-feedback").textContent = error.message; }
function updateStatus(status) {
    connected = status === "connected";
    $("connection-status").textContent = { connecting: "연결 중…", connected: "● 실시간 연결", reconnecting: "재연결 중…", offline: "연결 대기" }[status];
    $("connection-status").dataset.state = status;
    $("chat-input").disabled = !connected;
    $("chat-form").querySelector("button").disabled = !connected;
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
    $("chat-input").value = "";
    $("chat-messages").replaceChildren();
    $("player").replaceChildren(el("p", "", "♫ 플레이리스트를 선택해주세요."));
    renderPlaylists();
    connection.join(room);
}
function leaveRoom() {
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
    $("listening-tracks").replaceChildren(trackList(playlist.songs));
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
$("listening-mute").addEventListener("click", () => {
    const audio = $("player").querySelector("audio");
    if (audio) { audio.muted = !audio.muted; updateListeningPanel(); }
});
$("listening-tracks-toggle").addEventListener("click", () => {
    const open = $("listening-tracks").classList.toggle("hidden") === false;
    $("listening-tracks-toggle").setAttribute("aria-expanded", String(open));
});
window.addEventListener("pagehide", () => connection.leave());
window.addEventListener("pageshow", (event) => { if (event.persisted && currentRoom) connection.join(currentRoom); });
loadRooms();
