import { YoutubeImporter } from "./js/youtube-import.js";
import { api, RoomConnection, PlaylistRoomConnection } from "./js/api.js";
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
let roomConnected = false;
let playerVersion = null;
const messageIds = new Set();
const roomMessageIds = new Set();

let aiBusy = false;
let aiMode = false;
let aiLastRequest = "";
let aiSongs = [];
let chatDraft = "";
const CHAT_PLACEHOLDER = $("chat-input").placeholder;
const CHAT_MAXLENGTH = $("chat-input").maxLength;
const AI_PLACEHOLDER = "듣고 싶은 분위기 (비워두면 비슷한 곡 추천)";
const AI_MAX_REQUEST = 100;
const AI_TIMEOUT_MS = 300000;

let listeningParticipants = [];
let listeningPlaylist = null;
let playbackActive = false;
let listeningRequest = Promise.resolve();

// 개별 라운지 (함께 듣기 방) 상태
let togetherRooms = [];
let activeTogetherRoom = null; // { room_id, title, playlist_id, is_host, host, participants }
let lastRoomSync = null;
let revertingGuestSeek = false;
let seekingSlider = false;

function isTogetherGuest() {
    return Boolean(activeTogetherRoom && !activeTogetherRoom.is_host);
}

function notifyGuestPlaylistLock() {
    const msg = "개별 라운지 참여 중에는 플레이리스트를 변경할 수 없어요. 개별 라운지에서 '방 나가기'를 누른 뒤 변경해주세요.";
    $("page-feedback").textContent = msg;
    if ($("playlist-chat-feedback")) $("playlist-chat-feedback").textContent = msg;
}

function computeExpectedRoomSec(syncData) {
    if (!syncData) return 0;
    const base = Number(syncData.current_position_sec) || 0;
    if (!syncData.is_playing || !syncData.synced_at) return Math.max(0, base);
    const syncedMs = Date.parse(syncData.synced_at);
    if (!Number.isFinite(syncedMs)) return Math.max(0, base);
    return Math.max(0, base + Math.max(0, (Date.now() - syncedMs) / 1000));
}

function applyTogetherPlayerLock() {
    const locked = isTogetherGuest();
    const player = $("player");
    if (player) {
        player.querySelectorAll(".mp3-controls button, .track-play").forEach((btn) => {
            btn.disabled = locked;
            if (locked) btn.title = "개별 라운지에서는 호스트만 곡을 변경할 수 있어요.";
            else btn.removeAttribute("title");
        });
    }
    updateListeningControls();
}

function enforceGuestTimeLock(audio) {
    if (!isTogetherGuest() || revertingGuestSeek || !lastRoomSync || !audio || audio.readyState < 1) return;
    const expected = computeExpectedRoomSec(lastRoomSync);
    const clamped = Number.isFinite(audio.duration) && audio.duration > 0
        ? Math.min(expected, Math.max(0, audio.duration - 0.5))
        : expected;
    const tolerance = lastRoomSync.is_playing ? 1.5 : 0.4;
    if (Math.abs((audio.currentTime || 0) - clamped) > tolerance) {
        revertingGuestSeek = true;
        audio.currentTime = clamped;
        const msg = "개별 라운지에서는 호스트만 재생 시간을 이동할 수 있어요.";
        if ($("playlist-chat-feedback")) $("playlist-chat-feedback").textContent = msg;
        setTimeout(() => { revertingGuestSeek = false; }, 200);
    }
}

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
    $("listening-title").textContent = listeningPlaylist?.title || "음악을 골라보세요";
    if (activeTogetherRoom) {
        $("listening-state").textContent = `${activeTogetherRoom.is_host ? "호스트 DJ로 진행 중" : "함께 듣기 동기화 중 (호스트 제어)"} · ${activeTogetherRoom.title}`;
    } else {
        $("listening-state").textContent = !listeningPlaylist ? "플레이리스트를 선택하면 여기에 표시됩니다." : playbackActive ? "지금 재생 중 · " + listeningPlaylist.user : "재생 대기 · 곡을 선택해 재생하세요.";
    }
    document.querySelector(".mini-record").classList.toggle("is-playing", playbackActive);
    updateListeningControls();

    const audienceCount = document.querySelector(".listening-audience summary span");
    const audienceList = document.querySelector(".listening-audience-menu ul");
    if (audienceCount && audienceList) {
        const listeners = activeTogetherRoom
            ? (activeTogetherRoom.participants || [])
            : listeningPlaylist
                ? listeningParticipants.filter((p) => Array.isArray(p.listening) && p.listening.includes(listeningPlaylist.id))
                : [];
        audienceCount.textContent = (activeTogetherRoom || listeningPlaylist) ? `${listeners.length}명` : "—";
        audienceList.replaceChildren(
            ...(listeners.length
                ? listeners.map((p) => el("li", "", p.member_id === self ? `${p.nickname} (나)` : p.nickname))
                : [el("li", "muted", activeTogetherRoom || listeningPlaylist ? "아직 함께 듣는 사람이 없어요." : "플레이리스트를 선택해주세요.")])
        );
    }

    renderPlaylistChatPanel();
}

function resetListening() {
    if (!activeTogetherRoom) selectChatTab("main-chat-tab");
    listeningPlaylist = null;
    playbackActive = false;
    updateListeningPanel();
    reportListening();
}

function feedback(error) { $("page-feedback").textContent = error.message || String(error); }

function selectChatTab(id) {
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
    if (!aiBusy) $("ai-recommend-btn").disabled = !connected;
    if (!connected) {
        listeningParticipants = [];
        updateListeningPanel();
        $("participant-list").replaceChildren(el("p", "muted", "실시간 연결을 확인하고 있어요."));
        $("participant-count").textContent = "—";
    }
}

function updateRoomStatus(status) {
    roomConnected = status === "connected";
    const badge = $("room-connection-status");
    if (badge) {
        badge.textContent = {
            connecting: "방 연결 중…",
            connected: `● 참여 중 (${activeTogetherRoom?.participants?.length || 1}명)`,
            reconnecting: "재연결 중…",
            offline: activeTogetherRoom ? "연결 끊김" : "대기 중",
        }[status] || "대기 중";
        badge.dataset.state = status;
    }
    const input = $("playlist-chat-input");
    const sendBtn = $("playlist-chat-form")?.querySelector('[type="submit"]');
    if (input) input.disabled = !roomConnected || !activeTogetherRoom;
    if (sendBtn) sendBtn.disabled = !roomConnected || !activeTogetherRoom;
}

// ---- 개별 라운지(함께 듣기 방) SSE 및 제어 ----
const togetherConnection = new PlaylistRoomConnection((kind, data) => {
    if (!activeTogetherRoom) return;
    if (kind === "room_snapshot") {
        activeTogetherRoom.participants = data.participants || [];
        updateRoomStatus("connected");
        updateListeningPanel();
        if (data.playback) {
            lastRoomSync = data.playback;
            if (!activeTogetherRoom.is_host) {
                $("player").syncPlayback?.(data.playback);
            }
        }
        applyTogetherPlayerLock();
        const box = $("playlist-chat-messages");
        if (box) {
            box.replaceChildren();
            roomMessageIds.clear();
            (data.messages || []).forEach((m) => appendChatMessage(box, roomMessageIds, m, true));
            if (!(data.messages || []).length) {
                box.append(el("p", "muted chat-empty", "첫 인사를 건네보세요. 같은 음악을 듣는 사람들의 대화가 여기 쌓입니다."));
            }
            box.scrollTop = box.scrollHeight;
        }
    } else if (kind === "room_sync") {
        lastRoomSync = data;
        if (!activeTogetherRoom.is_host) {
            $("player").syncPlayback?.(data);
            applyTogetherPlayerLock();
        }
        refreshTogetherRooms();
    } else if (kind === "room_presence") {
        activeTogetherRoom.participants = data.participants || [];
        updateRoomStatus(roomConnected ? "connected" : "connecting");
        updateListeningPanel();
        refreshTogetherRooms();
    } else if (kind === "room_message") {
        const box = $("playlist-chat-messages");
        if (box) appendChatMessage(box, roomMessageIds, data);
    } else if (kind === "room_closed") {
        $("playlist-chat-feedback").textContent = "호스트가 개별 라운지(함께 듣기 방)를 종료했습니다.";
        leaveTogetherRoom(false);
        refreshTogetherRooms();
    }
}, updateRoomStatus);

async function refreshTogetherRooms() {
    if (!currentRoom) return;
    try {
        const res = await api(`/api/rooms/${currentRoom}/playlist-rooms`);
        togetherRooms = res.data || [];
        renderPlaylistChatPanel();
    } catch {
        /* ignore background refresh errors */
    }
}

function renderPlaylistChatPanel() {
    const titleEl = $("playlist-chat-title");
    const actionsEl = $("playlist-room-actions");
    const listEl = $("playlist-room-list");
    const emptyEl = $("playlist-chat-empty");
    const descEl = $("playlist-chat-description");
    const messagesEl = $("playlist-chat-messages");
    if (!titleEl) return;

    actionsEl?.replaceChildren();
    listEl?.replaceChildren();

    if (activeTogetherRoom) {
        titleEl.textContent = `${activeTogetherRoom.title} (${activeTogetherRoom.is_host ? "호스트 DJ" : "참여 중"})`;
        const leaveBtn = el("button", "secondary", activeTogetherRoom.is_host ? "방 종료" : "방 나가기");
        leaveBtn.type = "button";
        leaveBtn.addEventListener("click", () => leaveTogetherRoom(true));
        actionsEl?.append(leaveBtn);

        emptyEl?.classList.add("hidden");
        messagesEl?.classList.remove("hidden");
        return;
    }

    titleEl.textContent = listeningPlaylist?.title || "플레이리스트를 선택해주세요";
    emptyEl?.classList.remove("hidden");
    messagesEl?.classList.add("hidden");

    const canCreate = Boolean(listeningPlaylist && !listeningPlaylist.sample && Number.isInteger(listeningPlaylist.id));
    if (canCreate) {
        const existingForPlaylist = togetherRooms.find((r) => r.playlist_id === listeningPlaylist.id);
        const btn = el(
            "button",
            "primary",
            existingForPlaylist ? "🎧 열린 방 입장하기" : "+ 이 플리로 방 만들기"
        );
        btn.type = "button";
        btn.addEventListener("click", () => {
            if (existingForPlaylist) joinTogetherRoom(existingForPlaylist.room_id);
            else createTogetherRoom();
        });
        actionsEl?.append(btn);
    }

    if (descEl) {
        descEl.textContent = listeningPlaylist
            ? (listeningPlaylist.sample
                ? "예시 목록으로는 개별 라운지를 만들 수 없어요. 유튜브 곡이 담긴 플레이리스트를 선택해주세요."
                : "위 버튼을 눌러 함께 듣기 방을 개설하거나 아래 열린 방에 입장해보세요.")
            : "플레이리스트를 선택하면 개별 라운지를 만들거나 참여할 수 있어요.";
    }

    if (listEl && togetherRooms.length) {
        const wrap = el("div", "playlist-list");
        togetherRooms.forEach((r) => {
            const row = el("div", "playlist-item");
            const info = el("div", "item-text");
            const trackTitle = r.current_track?.title || "재생 대기";
            info.append(
                el("strong", "", `🎧 ${r.title} (${r.participant_count}명)`),
                el("small", "muted", `호스트: ${r.host.nickname} · ♫ ${trackTitle}`)
            );
            const joinBtn = el("button", "secondary", "입장");
            joinBtn.type = "button";
            joinBtn.addEventListener("click", () => joinTogetherRoom(r.room_id));
            row.append(info, joinBtn);
            wrap.append(row);
        });
        listEl.append(wrap);
    }
}

async function createTogetherRoom() {
    if (!currentRoom || !listeningPlaylist || listeningPlaylist.sample) return;
    $("playlist-chat-feedback").textContent = "";
    const audio = $("player").querySelector("audio");
    const position_sec = audio && Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
    const is_playing = Boolean(audio && !audio.paused && !audio.ended && audio.readyState >= 2);
    try {
        const res = await api(`/api/rooms/${currentRoom}/playlist-rooms`, "POST", {
            playlist_id: listeningPlaylist.id,
            title: `${listeningPlaylist.title} · 함께 듣기`,
            position_sec,
            is_playing,
        });
        await joinTogetherRoom(res.data.room_id);
        await refreshTogetherRooms();
    } catch (error) {
        $("playlist-chat-feedback").textContent = error.message;
    }
}

async function joinTogetherRoom(roomId) {
    $("playlist-chat-feedback").textContent = "";
    $("page-feedback").textContent = "";
    try {
        const res = await api(`/api/playlist-rooms/${roomId}`);
        const detail = res.data;
        activeTogetherRoom = {
            room_id: detail.room_id,
            title: detail.title,
            playlist_id: detail.playlist_id,
            is_host: detail.is_host,
            host: detail.host,
            participants: detail.participants || [],
        };
        lastRoomSync = detail.playback || null;
        let targetPl = roomPlaylists.find((p) => p.id === detail.playlist_id);
        if (!targetPl) {
            await refreshPlaylists();
            targetPl = roomPlaylists.find((p) => p.id === detail.playlist_id);
        }
        if (targetPl && selectedPlaylist !== targetPl.id) {
            showPlaylist(targetPl, { force: true });
        }
        selectChatTab("playlist-chat-tab");
        updateListeningPanel();
        applyTogetherPlayerLock();
        renderPlaylists();
        togetherConnection.join(roomId);
    } catch (error) {
        $("playlist-chat-feedback").textContent = error.message;
    }
}

async function leaveTogetherRoom(byUser = true) {
    if (!activeTogetherRoom) return;
    const { room_id, is_host } = activeTogetherRoom;
    togetherConnection.leave();
    activeTogetherRoom = null;
    lastRoomSync = null;
    roomMessageIds.clear();
    $("playlist-chat-messages")?.replaceChildren();
    if (byUser && is_host) {
        try { await api(`/api/playlist-rooms/${room_id}`, "DELETE"); } catch {}
    }
    $("page-feedback").textContent = "";
    updateRoomStatus("offline");
    applyTogetherPlayerLock();
    updateListeningPanel();
    renderPlaylists();
    refreshTogetherRooms();
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
        refreshTogetherRooms();
        scrollChat();
    } else if (kind === "presence") {
        renderParticipants(data.participants);
    } else if (kind === "message") {
        appendMessage(data.message);
    } else if (kind === "playlists_changed") {
        refreshPlaylists();
    } else if (kind === "playlist_room_created") {
        refreshTogetherRooms();
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
            card.append(el("h3", "", room.name), el("p", "", "함께 듣는 공간으로 →"));
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
    togetherRooms = [];
    if (activeTogetherRoom) leaveTogetherRoom(true);
    resetListening();
    roomPlaylists = [];
    document.body.dataset.room = room;
    $("selection").classList.add("hidden");
    $("lounge").classList.remove("hidden");
    $("room-title").textContent = `${name} 라운지`;
    $("playlist-heading").textContent = "라운지의 음악";
    $("page-feedback").textContent = "";
    $("chat-feedback").textContent = "";
    $("playlist-chat-feedback").textContent = "";
    setAiMode(false);
    $("chat-input").value = "";
    if ($("playlist-chat-input")) $("playlist-chat-input").value = "";
    closeAiResult();
    $("chat-messages").replaceChildren();
    $("playlist-chat-messages")?.replaceChildren();
    $("player").replaceChildren(el("p", "", "♫ 플레이리스트를 선택해주세요."));
    renderPlaylists();
    connection.join(room);
}

function leaveRoom() {
    if (activeTogetherRoom) leaveTogetherRoom(true);
    closeAiResult();
    setAiMode(false);
    connection.leave();
    currentRoom = null;
    refreshId++;
    roomPlaylists = [];
    togetherRooms = [];
    selectedMember = selectedPlaylist = null;
    listeningParticipants = [];
    resetListening();
    delete document.body.dataset.room;
    $("lounge").classList.add("hidden");
    $("selection").classList.remove("hidden");
    $("player").replaceChildren();
    updateStatus("offline");
    updateRoomStatus("offline");
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
            if (isTogetherGuest()) {
                notifyGuestPlaylistLock();
                return;
            }
            selectedMember = person.member_id;
            $("playlist-heading").textContent = `${person.nickname}님의 음악`;
            renderParticipants(participants);
            clearPlayer();
            renderPlaylists();
        });
        $("participant-list").append(button);
    });
}

function clearPlayer({ force = false } = {}) {
    if (!force && isTogetherGuest()) {
        notifyGuestPlaylistLock();
        return;
    }
    selectedPlaylist = null;
    $("player").replaceChildren(el("p", "", "♫ 목록에서 플레이리스트를 선택해주세요."));
    resetListening();
}

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

function showPlaylist(playlist, { force = false } = {}) {
    if (!force && isTogetherGuest() && playlist.id !== activeTogetherRoom.playlist_id) {
        notifyGuestPlaylistLock();
        return;
    }
    playbackActive = false;
    selectedPlaylist = playlist.id;
    listeningPlaylist = playlist;
    playerVersion = JSON.stringify([playlist.title, playlist.songs, playlist.user]);
    renderPlayer($("player"), playlist);
    applyTogetherPlayerLock();
    updateListeningPanel();
    reportListening();
}

function renderPlaylists() {
    const guestLocked = isTogetherGuest();
    const visible = selectedMember ? roomPlaylists.filter((p) => p.member_id === selectedMember) : roomPlaylists;
    $("playlist-description").textContent = guestLocked
        ? "개별 라운지 참여 중에는 플레이리스트를 변경할 수 없어요. 변경하려면 개별 라운지에서 '방 나가기'를 눌러주세요."
        : selectedMember
            ? "이 사용자가 현재 라운지에 공유한 플레이리스트입니다."
            : "참여자의 닉네임을 클릭하거나 아래 공유 목록을 선택하세요.";
    $("playlist-list").replaceChildren();
    visible.forEach((playlist) => {
        const item = el("div", "playlist-item");
        const info = el("div", "item-text");
        const open = el("button", "user-btn", playlist.title);
        open.type = "button";
        if (guestLocked && playlist.id !== activeTogetherRoom.playlist_id) {
            open.setAttribute("aria-disabled", "true");
            open.title = "개별 라운지를 나가야 플레이리스트를 변경할 수 있어요.";
        }
        open.addEventListener("click", () => showPlaylist(playlist));
        const author = el("button", "author-button", `${playlist.user} · ${playlist.songs.length}곡${playlist.sample ? " · 예시 목록" : ""}`);
        author.type = "button";
        author.addEventListener("click", () => {
            if (isTogetherGuest()) {
                notifyGuestPlaylistLock();
                return;
            }
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
        const buttons = el("div", "playlist-buttons");
        buttons.append(like, ...(collect ? [collect] : []));
        item.append(info, buttons);
        $("playlist-list").append(item);
    });
    if (!visible.length) $("playlist-list").append(el("p", "empty-state", selectedMember ? "아직 이 라운지에 공유한 플레이리스트가 없어요." : "아직 공유된 음악이 없어요. 첫 플레이리스트를 공유해보세요."));
    $("ranking-list").replaceChildren();
    roomPlaylists.slice(0, 3).forEach((playlist, i) => {
        const button = el("button", "compact-rank");
        button.type = "button";
        if (guestLocked && playlist.id !== activeTogetherRoom.playlist_id) {
            button.setAttribute("aria-disabled", "true");
            button.title = "개별 라운지를 나가야 플레이리스트를 변경할 수 있어요.";
        }
        button.append(el("span", "rank-num", String(i + 1)), el("span", "rank-title", playlist.title), el("small", "", `♥ ${playlist.likes}`));
        button.addEventListener("click", () => showPlaylist(playlist));
        $("ranking-list").append(button);
    });
    if (selectedPlaylist) {
        const playlist = roomPlaylists.find((p) => p.id === selectedPlaylist);
        if (!playlist) clearPlayer({ force: true });
        else if (JSON.stringify([playlist.title, playlist.songs, playlist.user]) !== playerVersion) showPlaylist(playlist, { force: true });
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

function appendChatMessage(box, idSet, message, history = false) {
    if (idSet.has(message.id)) return;
    idSet.add(message.id);
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
    if (!history && (nearBottom || message.member_id === self)) {
        box.scrollTop = box.scrollHeight;
    }
}

function appendMessage(message, history = false) {
    appendChatMessage($("chat-messages"), messageIds, message, history);
}

// 메인 라운지 채팅 전송
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

// 개별 라운지(함께 듣기 방) 전용 채팅 전송
$("playlist-chat-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("playlist-chat-input");
    const text = input?.value.trim();
    if (!text || !roomConnected || !activeTogetherRoom) return;
    const roomId = activeTogetherRoom.room_id;
    const button = event.submitter || event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
        const message = await api(`/api/playlist-rooms/${roomId}/messages`, "POST", { text });
        if (activeTogetherRoom?.room_id !== roomId) return;
        appendChatMessage($("playlist-chat-messages"), roomMessageIds, message);
        input.value = "";
        $("playlist-chat-feedback").textContent = "";
        input.focus();
    } catch (error) {
        $("playlist-chat-feedback").textContent = error.message;
    } finally {
        button.disabled = !roomConnected || !activeTogetherRoom;
    }
});

// ---- AI recommendation ----
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
        chatDraft = input.value;
        input.value = "";
        input.placeholder = AI_PLACEHOLDER;
        input.maxLength = AI_MAX_REQUEST;
        input.required = false;
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
        if (aiMode && connected) input.focus();
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
        if (isTogetherGuest()) {
            $("ai-save-feedback").textContent = `‘${playlist.title}’으로 내 계정에 저장했어요. 개별 라운지를 나간 뒤 재생할 수 있어요.`;
            return;
        }
        $("ai-save-feedback").textContent = `‘${playlist.title}’으로 저장했어요. 아래 플레이어에서 바로 들을 수 있어요. 라운지 공유는 ‘내 플레이리스트 공유’에서 할 수 있어요.`;
        selectedPlaylist = null;
        resetListening();
        renderPlayer($("player"), playlist);
    } catch (error) {
        $("ai-save-feedback").textContent = error.message;
        button.disabled = false;
    }
});

$("back-btn").addEventListener("click", leaveRoom);
$("show-all").addEventListener("click", () => {
    if (isTogetherGuest()) {
        notifyGuestPlaylistLock();
        return;
    }
    selectedMember = null;
    $("playlist-heading").textContent = "라운지의 음악";
    clearPlayer();
    renderPlaylists();
    highlightMember();
});
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
        const isCollected = Boolean(playlist.collected_from);
        const active = playlist.shared_rooms.includes(room);
        const button = el("button", active || isCollected ? "secondary" : "primary", isCollected ? "수집한 목록 (공유 불가)" : active ? "공유 해제" : "공유하기");
        button.type = "button";
        button.disabled = isCollected;
        if (!isCollected) {
            button.addEventListener("click", async () => {
                button.disabled = true;
                try { await api(`/api/me/playlists/${playlist.id}/share`, "PUT", { room, enabled: !active }); await renderShareList(); }
                catch (error) { $("share-feedback").textContent = error.message; button.disabled = false; }
            });
        }
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

// 비호스트 참여자의 임의 재생 시간 이동(seek) 차단
["seeking", "seeked"].forEach((type) => {
    $("player").addEventListener(type, (event) => {
        if (event.target instanceof HTMLAudioElement) {
            enforceGuestTimeLock(event.target);
        }
    }, true);
});

// 호스트가 플레이어를 조작할 때 방 참여자들에게 실시간 동기화 전송
$("player").addEventListener("host-control", async (event) => {
    if (!activeTogetherRoom || !activeTogetherRoom.is_host) return;
    const { action, track_index, position_sec } = event.detail;
    try {
        await api(`/api/playlist-rooms/${activeTogetherRoom.room_id}/control`, "POST", {
            action,
            track_index,
            position_sec,
        });
    } catch (error) {
        $("playlist-chat-feedback").textContent = error.message;
    }
});

function formatPlaybackTime(value) {
    const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
function updateListeningControls() {
    const audio = $("player").querySelector("audio");
    const ready = Boolean(audio?.getAttribute("src"));
    const duration = Number.isFinite(audio?.duration) ? audio.duration : 0;
    const guestLocked = isTogetherGuest();
    $("listening-play").disabled = !ready;
    $("listening-play").textContent = audio && !audio.paused ? "\u275a\u275a" : "\u25b6";
    $("listening-play").setAttribute("aria-label", audio && !audio.paused ? "\uc77c\uc2dc \uc815\uc9c0" : "\uc7ac\uc0dd");
    if (!seekingSlider) {
        $("listening-time").textContent = formatPlaybackTime(audio?.currentTime);
        $("listening-seek").value = audio?.currentTime || 0;
    }
    $("listening-duration").textContent = formatPlaybackTime(duration);
    $("listening-seek").max = duration;
    $("listening-seek").disabled = !ready || !duration || guestLocked;
    $("listening-seek").title = guestLocked ? "개별 라운지에서는 호스트만 재생 시간을 이동할 수 있어요." : "";
    $("listening-volume").disabled = !audio;
    $("listening-volume").value = audio?.muted ? 0 : audio?.volume ?? 1;
}
["timeupdate", "durationchange", "loadedmetadata", "emptied", "play", "pause", "ended", "volumechange"].forEach((type) => {
    $("player").addEventListener(type, updateListeningControls, true);
});
$("listening-play").addEventListener("click", async () => {
    const audio = $("player").querySelector("audio");
    if (!audio) return;
    if (isTogetherGuest()) {
        if (lastRoomSync?.is_playing && audio.paused) {
            $("player").syncPlayback?.(lastRoomSync);
        } else {
            if ($("playlist-chat-feedback")) $("playlist-chat-feedback").textContent = "개별 라운지에서는 호스트가 재생을 제어해요. 소리를 끄려면 음량을 조절해주세요.";
        }
        return;
    }
    if (audio.paused) { try { await audio.play(); } catch (error) { feedback(error); } }
    else audio.pause();
});
["pointerdown", "mousedown", "touchstart"].forEach((type) => {
    $("listening-seek").addEventListener(type, () => {
        if (!isTogetherGuest()) seekingSlider = true;
    });
});
["pointerup", "mouseup", "touchend", "blur"].forEach((type) => {
    $("listening-seek").addEventListener(type, () => { seekingSlider = false; });
});
$("listening-seek").addEventListener("input", (event) => {
    if (isTogetherGuest()) {
        const audio = $("player").querySelector("audio");
        event.target.value = audio?.currentTime || 0;
        return;
    }
    $("listening-time").textContent = formatPlaybackTime(Number(event.target.value));
});
$("listening-seek").addEventListener("change", (event) => {
    seekingSlider = false;
    if (isTogetherGuest()) {
        const audio = $("player").querySelector("audio");
        event.target.value = audio?.currentTime || 0;
        if ($("playlist-chat-feedback")) $("playlist-chat-feedback").textContent = "개별 라운지에서는 호스트만 재생 시간을 이동할 수 있어요.";
        return;
    }
    const audio = $("player").querySelector("audio");
    if (audio && Number.isFinite(audio.duration)) audio.currentTime = Number(event.target.value);
});
$("listening-volume").addEventListener("input", (event) => {
    const audio = $("player").querySelector("audio");
    if (audio) { audio.muted = false; audio.volume = Number(event.target.value); }
});
window.addEventListener("pagehide", () => {
    togetherConnection.leave();
    connection.leave();
});
window.addEventListener("pageshow", (event) => {
    if (event.persisted && currentRoom) {
        connection.join(currentRoom);
        if (activeTogetherRoom) togetherConnection.join(activeTogetherRoom.room_id);
    }
});
loadRooms();