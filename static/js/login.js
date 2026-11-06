const form = document.getElementById("login-form");
const password = document.getElementById("password");
const nicknameInput = document.getElementById("nickname");
const toggle = document.getElementById("toggle-password");
const feedback = document.getElementById("login-feedback");

toggle.addEventListener("click", () => {
    const visible = password.type === "password";
    password.type = visible ? "text" : "password";
    toggle.textContent = visible ? "숨김" : "표시";
    toggle.setAttribute("aria-pressed", String(visible));
});

nicknameInput.addEventListener("input", () => {
    nicknameInput.setCustomValidity(
        nicknameInput.value.trim().length < 2 ? "닉네임을 2자 이상 입력해주세요." : ""
    );
});

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    feedback.textContent = "로그인 요청 중…";
    try {
        const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: { Accept: "application/json" }
        });
        const data = await response.json();
        if (response.ok && data.ok) {
            window.location.assign(data.redirect_url);
            return;
        }
        feedback.textContent = data.error || "로그인 응답을 확인할 수 없습니다.";
    } catch {
        feedback.textContent = "서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.";
    } finally {
        password.value = "";
        password.type = "password";
        toggle.textContent = "표시";
        toggle.setAttribute("aria-pressed", "false");
        submit.disabled = false;
    }
});
