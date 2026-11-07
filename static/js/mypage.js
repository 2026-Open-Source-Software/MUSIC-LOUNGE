import { YoutubeImporter } from "./youtube-import.js";
import { api as request } from "./api.js";
import { el as element, tracksToText, trackList, renderPlayer } from "./playlist-ui.js";
const byId = (id) => document.getElementById(id);
const library = byId("my-playlists");
const importer = new YoutubeImporter({ dialog: "edit-dialog", prefix: "edit-import", songs: "edit-songs", title: "edit-title", submit: "editor-submit" });
let editing = null;
let deleting = null;
let rooms = {};
function openEditor(playlist = null) {
    importer.reset(playlist?.songs ?? []);
    editing = playlist?.id ?? null;
    byId("editor-heading").textContent = playlist ? "플레이리스트 수정" : "새 플레이리스트";
    byId("editor-submit").textContent = playlist ? "변경 저장" : "내 계정에 저장";
    byId("edit-title").value = playlist?.title ?? "";
    byId("edit-songs").value = playlist ? tracksToText(playlist.songs) : "";
    byId("edit-room").value = playlist?.room ?? "ballad";
    byId("edit-feedback").textContent = "";
    byId("edit-dialog").showModal();
}
async function loadLibrary() {
    try {
        const playlists = await request("/api/me/playlists");
        library.replaceChildren();
        byId("playlist-count").textContent = playlists.length;
        if (!playlists.length) {
            const empty = element("div", "panel empty-library");
            empty.append(element("div", "avatar", "♫"), element("h3", "", "첫 플레이리스트를 만들어보세요"), element("p", "muted", "유튜브 링크를 모아 내 계정에 저장하고, 라운지에서 공유하세요."));
            const create = element("button", "primary", "+ 플레이리스트 만들기");
            create.type = "button";
            create.addEventListener("click", () => openEditor());
            empty.append(create);
            library.append(empty);
        }
        playlists.forEach((playlist) => {
            const card = element("article", "panel library-card");
            const cover = element("div", "library-cover");
            cover.dataset.room = playlist.room;
            cover.append(element("span", "room-badge", playlist.room_name));
            const content = element("div", "library-content");
            content.append(element("p", "muted library-meta", `${playlist.songs.length}곡 · 좋아요 ${playlist.likes} · ${playlist.shared_rooms.length ? `${playlist.shared_rooms.length}개 라운지에 공유 중` : "내 보관함"}`), element("h3", "", playlist.title));
            const preview = element("div", "library-player hidden");
            const openPlayer = element("button", "primary", "♫ MP3 플레이어 열기");
            openPlayer.type = "button";
            openPlayer.addEventListener("click", () => {
                if (preview.classList.contains("hidden")) { preview.classList.remove("hidden"); renderPlayer(preview, playlist); }
            });
            content.append(trackList(playlist.songs), openPlayer, preview);
            const sharing = element("details", "sharing-settings");
            sharing.append(element("summary", "", "라운지 공유 설정"));
            Object.entries(rooms).forEach(([id, room]) => {
                const label = element("label", "share-toggle");
                const checkbox = element("input");
                checkbox.type = "checkbox";
                checkbox.checked = playlist.shared_rooms.includes(id);
                checkbox.addEventListener("change", async () => {
                    checkbox.disabled = true;
                    try {
                        const result = await request(`/api/me/playlists/${playlist.id}/share`, "PUT", { room: id, enabled: checkbox.checked });
                        playlist.shared_rooms = result.playlist.shared_rooms;
                        content.querySelector(".library-meta").textContent = `${playlist.songs.length}곡 · 좋아요 ${playlist.likes} · ${playlist.shared_rooms.length ? `${playlist.shared_rooms.length}개 라운지에 공유 중` : "내 보관함"}`;
                        byId("library-feedback").textContent = `${room.name} 라운지 공유 설정을 저장했어요.`;
                    } catch (error) { checkbox.checked = !checkbox.checked; byId("library-feedback").textContent = error.message; }
                    finally { checkbox.disabled = false; }
                });
                label.append(checkbox, element("span", "", room.name));
                sharing.append(label);
            });
            content.append(sharing);
            const actions = element("div", "dialog-actions");
            const edit = element("button", "secondary", "수정");
            edit.type = "button";
            edit.addEventListener("click", () => openEditor(playlist));
            const remove = element("button", "secondary delete-button", "삭제");
            remove.type = "button";
            remove.addEventListener("click", () => {
                deleting = playlist.id;
                byId("delete-description").textContent = `‘${playlist.title}’가 보관함과 공유한 모든 라운지에서 삭제됩니다. 삭제 후 복구할 수 없습니다.`;
                byId("delete-feedback").textContent = "";
                byId("delete-dialog").showModal();
            });
            actions.append(edit, remove);
            content.append(actions);
            card.append(cover, content);
            library.append(card);
        });
    } catch (error) { byId("library-feedback").textContent = error.message; }
}
byId("new-playlist").addEventListener("click", () => openEditor());
byId("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const feedback = byId("profile-feedback");
    const password = byId("new-password").value;
    if (password !== byId("confirm-password").value) { feedback.textContent = "새 비밀번호가 일치하지 않습니다."; return; }
    const button = event.submitter || event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
        await request("/api/me", "PATCH", { nickname: byId("profile-nickname").value.trim(), current_password: byId("current-password").value, new_password: password });
        feedback.textContent = "계정 정보를 저장했어요.";
        ["current-password", "new-password", "confirm-password"].forEach((id) => byId(id).value = "");
    } catch (error) { feedback.textContent = error.message; }
    finally { button.disabled = false; }
});
byId("edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
        await request(editing ? `/api/me/playlists/${editing}` : "/api/me/playlists", editing ? "PATCH" : "POST", { title: byId("edit-title").value.trim(), room: byId("edit-room").value, songs: importer.collect() });
        byId("edit-dialog").close();
        await loadLibrary();
        byId("library-feedback").textContent = editing ? "플레이리스트를 수정했어요." : "내 계정에 저장했어요. 공유 설정에서 원하는 라운지를 선택하세요.";
    } catch (error) { byId("edit-feedback").textContent = error.message; }
    finally { button.disabled = false; }
});
byId("delete-confirm").addEventListener("click", async () => {
    byId("delete-confirm").disabled = true;
    try {
        await request(`/api/me/playlists/${deleting}`, "DELETE");
        byId("delete-dialog").close();
        await loadLibrary();
        byId("library-feedback").textContent = "플레이리스트를 삭제했어요.";
    } catch (error) { byId("delete-feedback").textContent = error.message; }
    finally { byId("delete-confirm").disabled = false; }
});
byId("edit-cancel").addEventListener("click", () => byId("edit-dialog").close());
byId("delete-cancel").addEventListener("click", () => byId("delete-dialog").close());
(async () => { try { rooms = await request("/api/rooms"); await loadLibrary(); } catch (error) { byId("library-feedback").textContent = error.message; } })();
