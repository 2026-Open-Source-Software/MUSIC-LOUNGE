
/* ========================================
   MUSIC LOUNGE - JavaScript
======================================== */

// 현재 선택된 라운지
let currentRoom = null;

// 사용자 닉네임
let nickname = "";

// 채팅 자동 갱신
let chatInterval = null;


/* ========================================
   HTML 요소 가져오기
======================================== */

const roomCards = document.getElementById("room-cards");
const selection = document.getElementById("selection");
const lounge = document.getElementById("lounge");
const roomTitle = document.getElementById("room-title");

const rankingList = document.getElementById("ranking-list");
const playlistList = document.getElementById("playlist-list");
const player = document.getElementById("player");

const chatMessages = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");

const createDialog = document.getElementById("create-dialog");
const createForm = document.getElementById("create-form");


/* ========================================
   1. 사용자 닉네임 설정
======================================== */

function setNickname() {

    const name = prompt("사용할 닉네임을 입력해주세요!");

    if (name && name.trim() !== "") {
        nickname = name.trim().slice(0, 20);
    } else {
        nickname = "익명사용자";
    }

}


/* ========================================
   2. 서버 API 요청
======================================== */

async function apiRequest(url, options = {}) {

    const response = await fetch(url, options);

    const data = await response.json();

    if (!response.ok) {
        throw new Error(
            data.error || "서버 요청에 실패했습니다."
        );
    }

    return data;

}


/* ========================================
   3. 장르별 라운지 목록
======================================== */

async function loadRooms() {

    try {

        const rooms = await apiRequest("/api/rooms");

        roomCards.innerHTML = "";

        Object.entries(rooms).forEach(([id, room]) => {

            const card = document.createElement("button");

            card.className = "room-card";

            const icon = document.createElement("div");
            icon.className = "room-icon";
            icon.textContent = room.emoji;

            const title = document.createElement("h3");
            title.textContent = room.name;

            const description = document.createElement("p");
            description.textContent = "라운지 입장하기 →";

            card.appendChild(icon);
            card.appendChild(title);
            card.appendChild(description);

            card.addEventListener("click", () => {
                enterRoom(id, room.name);
            });

            roomCards.appendChild(card);

        });

    } catch (error) {

        console.error(error);
        alert("라운지 목록을 불러오지 못했습니다.");

    }

}


/* ========================================
   4. 라운지 입장
======================================== */

async function enterRoom(roomId, name) {

    currentRoom = roomId;

    selection.classList.add("hidden");
    lounge.classList.remove("hidden");

    roomTitle.textContent = name;

    player.textContent = "♫ 플레이리스트를 선택해주세요.";

    chatMessages.innerHTML = "";

    await loadPlaylists();
    await loadMessages();

    // 기존 채팅 갱신 중지
    if (chatInterval) {
        clearInterval(chatInterval);
    }

    // 2.5초마다 채팅 새로고침
    chatInterval = setInterval(() => {

        if (currentRoom === roomId) {
            loadMessages();
        }

    }, 2500);

}


/* ========================================
   5. 라운지 나가기
======================================== */

function leaveRoom() {

    currentRoom = null;

    lounge.classList.add("hidden");
    selection.classList.remove("hidden");

    if (chatInterval) {
        clearInterval(chatInterval);
        chatInterval = null;
    }

}

document.getElementById("back-btn")
    .addEventListener("click", leaveRoom);


/* ========================================
   6. 플레이리스트 불러오기
======================================== */

async function loadPlaylists() {

    if (!currentRoom) {
        return;
    }

    const roomId = currentRoom;

    try {

        const playlists = await apiRequest(
            `/api/rooms/${roomId}/playlists`
        );

        if (currentRoom !== roomId) {
            return;
        }

        renderRanking(playlists);
        renderPlaylists(playlists);

    } catch (error) {

        console.error(error);

    }

}


/* ========================================
   7. 플레이리스트 랭킹
======================================== */

function renderRanking(playlists) {

    rankingList.innerHTML = "";

    // 좋아요 기준 상위 5개
    const topFive = playlists.slice(0, 5);

    topFive.forEach((playlist, index) => {

        const item = document.createElement("div");
        item.className = "rank-item";

        const rank = document.createElement("span");
        rank.className = "rank-num";
        rank.textContent = index + 1;

        const info = document.createElement("div");
        info.className = "item-text";

        const title = document.createElement("strong");
        title.textContent = playlist.title;

        const user = document.createElement("small");
        user.textContent = playlist.user;

        info.appendChild(title);
        info.appendChild(user);

        const likes = document.createElement("span");
        likes.className = "like-count";
        likes.textContent = `♥ ${playlist.likes}`;

        item.appendChild(rank);
        item.appendChild(info);
        item.appendChild(likes);

        rankingList.appendChild(item);

    });

}


/* ========================================
   8. 플레이리스트 목록 표시
======================================== */

function renderPlaylists(playlists) {

    playlistList.innerHTML = "";

    playlists.forEach((playlist) => {

        const item = document.createElement("div");
        item.className = "playlist-item";

        const info = document.createElement("div");
        info.className = "item-text";

        // 닉네임 클릭 버튼
        const userButton = document.createElement("button");

        userButton.className = "user-btn";
        userButton.textContent = playlist.user;

        userButton.addEventListener("click", () => {
            showPlaylist(playlist);
        });

        const title = document.createElement("small");
        title.textContent = playlist.title;

        info.appendChild(userButton);
        info.appendChild(title);

        // 좋아요 버튼
        const likeButton = document.createElement("button");

        likeButton.className = "heart";
        likeButton.textContent = `♥ ${playlist.likes}`;

        likeButton.addEventListener("click", () => {
            likePlaylist(playlist.id);
        });

        item.appendChild(info);
        item.appendChild(likeButton);

        playlistList.appendChild(item);

    });

}


/* ========================================
   9. 플레이리스트 선택
======================================== */

function showPlaylist(playlist) {

    player.innerHTML = "";

    const title = document.createElement("strong");

    title.textContent =
        `${playlist.user}님의 ${playlist.title}`;

    const songList = document.createElement("ol");

    playlist.songs.forEach((song) => {

        const songItem = document.createElement("li");
        songItem.textContent = song;

        songList.appendChild(songItem);

    });

    player.appendChild(title);
    player.appendChild(songList);

}


/* ========================================
   10. 플레이리스트 좋아요
======================================== */

async function likePlaylist(playlistId) {

    if (!currentRoom) {
        return;
    }

    const roomId = currentRoom;

    try {

        await apiRequest(
            `/api/rooms/${roomId}/playlists/${playlistId}/like`,
            {
                method: "POST"
            }
        );

        // 좋아요 이후 랭킹 갱신
        if (currentRoom === roomId) {
            await loadPlaylists();
        }

    } catch (error) {

        alert(error.message);

    }

}


/* ========================================
   11. 채팅 메시지 불러오기
======================================== */

async function loadMessages() {

    if (!currentRoom) {
        return;
    }

    const roomId = currentRoom;

    try {

        const messages = await apiRequest(
            `/api/rooms/${roomId}/messages`
        );

        if (currentRoom !== roomId) {
            return;
        }

        chatMessages.innerHTML = "";

        messages.forEach((message) => {

            const messageBox = document.createElement("div");
            messageBox.className = "message";

            const user = document.createElement("strong");
            user.textContent = message.user;

            const text = document.createElement("p");
            text.textContent = message.text;

            messageBox.appendChild(user);
            messageBox.appendChild(text);

            chatMessages.appendChild(messageBox);

        });

        // 가장 최근 채팅으로 스크롤
        chatMessages.scrollTop =
            chatMessages.scrollHeight;

    } catch (error) {

        console.error(error);

    }

}


/* ========================================
   12. 채팅 메시지 전송
======================================== */

chatForm.addEventListener("submit", async (event) => {

    event.preventDefault();

    if (!currentRoom) {
        return;
    }

    const roomId = currentRoom;
    const message = chatInput.value.trim();

    if (message === "") {
        return;
    }

    try {

        await apiRequest(
            `/api/rooms/${roomId}/messages`,
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    user: nickname,
                    text: message
                })
            }
        );

        chatInput.value = "";

        if (currentRoom === roomId) {
            await loadMessages();
        }

    } catch (error) {

        alert(error.message);

    }

});


/* ========================================
   13. 플레이리스트 생성 팝업
======================================== */

document.getElementById("create-btn")
    .addEventListener("click", () => {

        createDialog.showModal();

    });


/* ========================================
   14. 팝업 취소
======================================== */

document.getElementById("cancel-btn")
    .addEventListener("click", () => {

        createDialog.close();

    });


/* ========================================
   15. 플레이리스트 공유하기
======================================== */

createForm.addEventListener("submit", async (event) => {

    event.preventDefault();

    if (!currentRoom) {
        return;
    }

    const roomId = currentRoom;

    const title = document
        .getElementById("playlist-title")
        .value.trim();

    const songsText = document
        .getElementById("playlist-songs")
        .value;

    // 줄바꿈 기준으로 노래 분리
    const songs = songsText
        .split("\n")
        .map((song) => song.trim())
        .filter((song) => song !== "");

    if (songs.length === 0 || songs.length > 20) {

        alert("노래는 1곡부터 20곡까지 입력해주세요.");
        return;

    }

    try {

        await apiRequest(
            `/api/rooms/${roomId}/playlists`,
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    user: nickname,
                    title: title,
                    songs: songs
                })
            }
        );

        createDialog.close();
        createForm.reset();

        if (currentRoom === roomId) {
            await loadPlaylists();
        }

        alert("플레이리스트가 공유되었습니다!");

    } catch (error) {

        alert(error.message);

    }

});


/* ========================================
   16. 웹사이트 초기 실행
======================================== */

async function initialize() {

    setNickname();

    await loadRooms();

}

initialize();
