import { api } from "./api.js";

const LABELS = {
    available: "수집하기",
    collected: "수집함 ✓",
    mine: "내 플레이리스트",
    login: "로그인하고 수집하기",
};

// 플레이리스트 찾기 결과와 라운지 카드에서 함께 쓰는 수집하기 버튼.
// 예시 목록(sample)은 DB에 없어 수집할 수 없으므로 null을 돌려줍니다.
export function collectButton(playlist, { className = "", onMessage = () => {} } = {}) {
    const state = playlist.collect_state ?? "available";
    if (state === "sample") return null;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `collect-button ${className}`.trim();
    const render = (current) => {
        button.textContent = LABELS[current] ?? LABELS.available;
        button.disabled = current === "collected" || current === "mine";
        button.dataset.state = current;
    };
    render(state);
    button.addEventListener("click", async () => {
        if (button.dataset.state === "login") { window.location.assign("/login"); return; }
        button.disabled = true;
        onMessage("수집하는 중…");
        try {
            await api(`/api/discovery/playlists/${playlist.id}/collect`, "POST", {});
            playlist.collect_state = "collected";
            render("collected");
            onMessage("마이페이지의 수집한 플레이리스트에 추가했어요.");
        } catch (error) {
            button.disabled = false;
            onMessage(error.message);
        }
    });
    return button;
}
