import { api } from './api.js';

const form = document.querySelector('#song-finder-form');
let selected = [];
let searchController;
let searchVersion = 0;
const input = document.querySelector('#finder-songs');
const choices = document.querySelector('#finder-song-choices');
let resultVersion = 0;
const feedback = document.querySelector('#finder-feedback');
const results = document.querySelector('#finder-results');
const submit = document.querySelector('#finder-submit');
const dialog = document.querySelector('#recommendation-detail');
const isAuthenticated = document.body.dataset.authenticated === 'true';
const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
};
function invalidate() {
    resultVersion++;
    results.replaceChildren();
    const complete = selected.length >= 2 && selected.every(Boolean);
    const unique = new Set(selected.filter(Boolean).map(song => song.video_id)).size;
    submit.disabled = !complete || unique !== selected.length;
    feedback.textContent = complete && unique !== selected.length
        ? '서로 다른 곡을 선택해주세요.' : '';
}
async function search() {
    searchController?.abort();
    searchController = new AbortController();
    const version = ++searchVersion;
    selected = [];
    choices.replaceChildren();
    invalidate();
    const titles = input.value.split(',').map(title => title.trim());
    if (titles.length > 3 || titles.some(title => !title || title.length > 120)) {
        feedback.textContent = '곡 제목을 쉼표로 구분해 최대 3곡까지 입력해주세요. 각 제목은 120자 이하여야 합니다.';
        return;
    }
    selected = titles.map(() => null);
    feedback.textContent = '검색 중…';
    await Promise.all(titles.map(async (title, index) => {
        const group = node('section', '', 'song-choice-group');
        const status = node('p', `${index + 1}. ${title} · 검색 중…`, 'finder-status');
        status.setAttribute('role', 'status');
        const list = node('ul', '', 'song-options');
        list.setAttribute('aria-label', `${index + 1}번째 곡 검색 결과`);
        group.append(status, list);
        choices.append(group);
        try {
            const { songs } = await api(`/api/discovery/songs?q=${encodeURIComponent(title)}`, 'GET', undefined, searchController.signal);
            if (version !== searchVersion) return;
            status.textContent = songs.length ? `${index + 1}. ${title} · 원하는 곡을 선택해주세요.` : `${title} · 검색 결과가 없습니다.`;
            for (const song of songs) {
                const item = node('li');
                const button = node('button', song.title);
                button.type = 'button';
                if (song.artist) button.append(node('small', song.artist));
                button.addEventListener('click', () => {
                    selected[index] = song;
                    list.replaceChildren();
                    status.textContent = `선택됨 · ${song.title}`;
                    invalidate();
                    if (selected.length === 1) feedback.textContent = '공통 플레이리스트 추천을 위해 한 곡 이상 더 입력해주세요.';
                });
                item.append(button);
                list.append(item);
            }
        } catch (error) {
            if (error.name !== 'AbortError' && version === searchVersion) status.textContent = error.message;
        }
    }));
    if (version === searchVersion) {
        invalidate();
        if (titles.length === 1) feedback.textContent = '공통 플레이리스트 추천에는 2~3곡이 필요합니다.';
    }
}
document.querySelector('.song-search').addEventListener('click', search);
input.addEventListener('input', () => {
    searchVersion++;
    searchController?.abort();
    selected = [];
    choices.replaceChildren();
    invalidate();
});
input.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); search(); }
});
form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    const version = ++resultVersion;
    submit.disabled = true;
    results.replaceChildren();
    feedback.textContent = '선택한 곡들이 함께 담긴 목록을 찾고 있어요…';
    try {
        const params = new URLSearchParams({ first: selected[0].video_id, second: selected[1].video_id });
        const response = await api(`/api/discovery/playlists?${params}`);
        const playlists = response.playlists.filter(playlist => selected.every(song => playlist.songs.some(track => track.video_id === song.video_id)));
        for (const playlist of playlists) playlist.matched_songs = [...selected];
        if (version !== resultVersion) return;
        feedback.textContent = playlists.length ? `${playlists.length}개의 공통 플레이리스트를 찾았어요.` : '선택한 곡이 모두 포함된 공유 플레이리스트가 없습니다. 다른 곡 조합을 선택해보세요.';
        for (const playlist of playlists) {
            const card = node('article', '', 'recommendation-card');
            const cover = node('div', '♫', 'recommendation-cover');
            const image = node('img');
            image.src = playlist.cover_url;
            image.alt = `${playlist.title} 커버`;
            image.loading = 'lazy';
            image.addEventListener('error', () => image.remove(), { once: true });
            cover.append(image);
            const body = node('div');
            body.append(node('h3', playlist.title), node('small', `${playlist.user} · ${playlist.song_count}곡 · ♥ ${playlist.likes ?? 0}`));
            const matched = node('ul');
            for (const song of playlist.matched_songs) matched.append(node('li', `✓ ${song.title}`));
            const detail = node('button', '상세보기 ↗');
            detail.type = 'button';
            detail.addEventListener('click', () => {
                document.querySelector('#recommendation-detail-title').textContent = playlist.title;
                document.querySelector('#recommendation-detail-meta').textContent = `${playlist.user} · 전체 ${playlist.song_count}곡 · 좋아요 ${playlist.likes ?? 0}개`;
                const songs = document.querySelector('#recommendation-detail-songs');
                songs.replaceChildren();
                for (const song of playlist.songs) {
                    const li = node('li');
                    const link = node('a', song.title);
                    link.href = song.url;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    li.append(link);
                    songs.append(li);
                }
                dialog.showModal();
            });
            const copy = node('button', isAuthenticated ? '내 플레이리스트로 가져오기' : '로그인하고 가져오기', 'recommendation-import');
            copy.type = 'button';
            const copyStatus = node('p', '', 'finder-status');
            copyStatus.setAttribute('role', 'status');
            copy.addEventListener('click', async () => {
                if (!isAuthenticated) { window.location.assign('/login'); return; }
                copy.disabled = true;
                copyStatus.textContent = '가져오는 중…';
                try {
                    await api(`/api/discovery/playlists/${playlist.id}/import`, 'POST', {});
                    copy.textContent = '가져왔어요 ✓';
                    copyStatus.textContent = '마이페이지의 내 플레이리스트에 추가했어요.';
                } catch (error) {
                    copy.disabled = false;
                    copyStatus.textContent = error.message;
                }
            });
            const actions = node('div', '', 'recommendation-actions');
            actions.append(detail, copy);
            body.append(matched, actions, copyStatus);
            card.append(cover, body);
            results.append(card);
        }
    } catch (error) {
        if (version === resultVersion) feedback.textContent = `추천을 불러오지 못했습니다. ${error.message}`;
    } finally {
        if (version === resultVersion) submit.disabled = false;
    }
});
document.querySelector('#recommendation-close').addEventListener('click', () => dialog.close());
