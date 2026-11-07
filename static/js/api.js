// HTTP and real-time transport live here; screens only consume these interfaces.
export async function api(url, method = "GET", data, signal) {
    const response = await fetch(url, { method, credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: data === undefined ? undefined : JSON.stringify(data), signal });
    const result = await response.json();
    if (response.status === 401) {
        window.location.assign("/login");
        throw new Error("다시 로그인해주세요.");
    }
    if (!response.ok) throw new Error(result.error || "요청을 처리하지 못했습니다.");
    return result;
}

export class RoomConnection {
    constructor(onEvent, onStatus) {
        this.onEvent = onEvent;
        this.onStatus = onStatus;
        this.room = null;
        this.retry = 0;
    }
    join(room) {
        this.leave();
        this.room = room;
        this.retry = 0;
        this.connect();
    }
    connect() {
        const room = this.room;
        if (!room) return;
        const token = Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => n.toString(16).padStart(2, "0")).join("");
        this.token = token;
        this.onStatus("connecting");
        const source = new EventSource(`/api/rooms/${room}/events?connection=${token}`);
        this.source = source;
        for (const kind of ["snapshot", "presence", "message", "playlists_changed", "closed"]) {
            source.addEventListener(kind, (event) => {
                if (source !== this.source) return;
                if (kind === "closed") { this.leave(); window.location.assign("/login"); return; }
                if (kind === "snapshot") { this.retry = 0; this.onStatus("connected"); }
                this.onEvent(kind, JSON.parse(event.data));
            });
        }
        source.onerror = () => {
            if (source !== this.source) return;
            source.close();
            this.release(room, token);
            this.source = null;
            this.onStatus("reconnecting");
            api("/api/me").catch(() => {});
            this.timer = setTimeout(() => this.connect(), Math.min(10000, 1500 * 2 ** this.retry++));
        };
    }
    release(room, token) {
        if (room && token) {
            fetch(`/api/rooms/${room}/leave`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ connection: token }), keepalive: true }).catch(() => {});
        }
    }
    leave() {
        clearTimeout(this.timer);
        this.source?.close();
        this.release(this.room, this.token);
        this.room = this.source = this.token = null;
    }
}

