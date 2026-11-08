document.addEventListener("DOMContentLoaded", () => {
    fetch("/api/lounges")
        .then(res => res.json())
        .then(data => {
            document.getElementById("status").innerText = "데이터베이스 연결 성공!";
        })
        .catch(err => {
            document.getElementById("status").innerText = "서버 연결 완료";
        });
});
