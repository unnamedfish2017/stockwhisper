import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]

from test_growth_mechanics import make_client, seed_rumor

INDEX_HTML = ROOT / "static" / "index.html"
APP_JS = ROOT / "static" / "app.js"


class ButtonParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.buttons: list[dict] = []
        self._current: Optional[dict] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "button":
            self._current = {"attrs": {key: value or "" for key, value in attrs}, "text": ""}

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._current is not None:
            self.buttons.append(self._current)
            self._current = None


def html_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def buttons() -> list[dict]:
    parser = ButtonParser()
    parser.feed(html_text())
    return parser.buttons


def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def styles_css() -> str:
    return (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def test_search_controls_are_clickable_and_wired():
    html = html_text()
    js = app_js()

    assert 'id="searchInput"' in html
    assert re.search(r"<button\b(?=[^>]*\bid=\"searchBtn\")(?=[^>]*\btype=\"button\")", html)
    assert re.search(r"<button\b(?=[^>]*\bid=\"clearSearchBtn\")(?=[^>]*\btype=\"button\")", html)
    assert '("#searchBtn").addEventListener("click", runSearch)' in js
    assert '("#clearSearchBtn").addEventListener("click", clearSearch)' in js
    assert 'e.key === "Enter"' in js
    assert "function runSearch()" in js
    assert "function clearSearch()" in js


def test_static_buttons_have_handlers_or_form_semantics():
    js = app_js()
    delegated = {
        "eye-btn",
        "back-feed-btn",
    }
    submit_buttons = {
        "提交并解锁",
        "关闭",
        "发布",
    }

    missing = []
    for button in buttons():
        attrs = button["attrs"]
        button_id = attrs.get("id")
        classes = set((attrs.get("class") or "").split())
        text = button["text"].strip()
        if button_id and f'("#{button_id}").addEventListener' in js:
            continue
        if button_id and f'("#{button_id}")?.addEventListener' in js:
            continue
        if attrs.get("data-view") or attrs.get("data-view-jump"):
            continue
        if classes & delegated:
            continue
        if attrs.get("type") == "submit" or text in submit_buttons:
            continue
        missing.append(button_id or text or str(button))

    assert missing == []


def test_views_have_valid_navigation_and_return_paths():
    html = html_text()
    js = app_js()

    view_ids = set(re.findall(r'<section\b[^>]*class="[^"]*\bview\b[^"]*"[^>]*id="([^"]+)View"', html))
    nav_targets = set(re.findall(r'data-view="([^"]+)"', html))
    assert nav_targets <= view_ids
    assert "backtest" not in nav_targets

    non_feed_views = view_ids - {"feed"}
    for view in non_feed_views:
        start = html.index(f'id="{view}View"')
        next_view = html.find('<section class="view"', start + 1)
        section = html[start:] if next_view == -1 else html[start:next_view]
        assert "back-feed-btn" in section, view

    assert "const VIEW_FALLBACKS" in js
    assert "backtest: \"rank\"" in js
    assert 'jumpToView("feed")' in js


def test_dynamic_view_jump_targets_are_valid():
    js = app_js()
    valid_targets = {"feed", "submit", "watch", "rank", "backtest", "register", "auth", "detail"}

    literal_targets = set(re.findall(r'(?<![A-Za-z0-9_])view:\s*"([^"]+)"', js))
    data_attr_targets = set(re.findall(r'data-view(?:-jump)?="([^"]+)"', INDEX_HTML.read_text(encoding="utf-8")))
    targets = literal_targets | data_attr_targets

    invalid = sorted(target for target in targets if target not in valid_targets)
    assert invalid == []
    assert "document.body.addEventListener(\"click\"" in js
    assert "closest(\"[data-view-jump]\")" in js


def test_detail_watch_buttons_have_container_and_render_call():
    html = html_text()
    js = app_js()

    assert 'id="detailWatchTargets"' in html
    assert "function renderWatchTargets" in js
    assert "renderWatchTargets(item.stock_codes || [], item.watched)" in js
    assert '("#detailWatchTargets")?.addEventListener("click"' in js


def test_watchlist_buttons_toggle_between_add_and_remove():
    js = app_js()

    assert "function renderWatchToggle(stock, watched" in js
    assert 'data-watch-action="${active ? "remove" : "add"}"' in js
    assert '${active ? "-" : "+"}' in js
    assert "toggleWatchFromButton(btn)" in js
    assert 'if (btn.dataset.watchAction === "remove")' in js
    assert "await removeWatch(code)" in js
    assert "await addWatch({ code, name })" in js


def test_watchlist_updates_refresh_recent_signals_and_use_auto_height():
    js = app_js()
    css = styles_css()

    assert re.search(r"async function addWatch[\s\S]+await loadCommunityInsight\(\);[\s\S]+await loadRumors\(true\);", js)
    assert re.search(r"async function removeWatch[\s\S]+await loadCommunityInsight\(\);[\s\S]+await loadRumors\(true\);", js)
    assert re.search(r"\.watch-signal\s*\{[\s\S]*height:\s*auto;", css)
    assert re.search(r"\.watch-signal\s*\{[\s\S]*min-height:\s*72px;", css)
    assert re.search(r"\.watch-signal\s*\{[\s\S]*grid-template-columns:\s*30px minmax\(0, 1fr\) 44px;", css)
    assert re.search(r"\.watch-signal p\s*\{[\s\S]*overflow-wrap:\s*anywhere;", css)


def test_forgot_password_form_is_hidden_and_wired():
    html = html_text()
    js = app_js()

    assert 'placeholder="用户名 / 注册邮箱"' in html
    assert 'id="forgotForm" style="display:none"' in html
    assert 'id="resetMaskedEmail"' in html
    assert 'id="forgotBtn"' in html
    assert 'id="sendResetCodeBtn"' in html
    assert 'id="doResetBtn"' in html
    forgot_start = html.index('id="forgotForm"')
    forgot_end = html.index('<div class="dialog-actions">', forgot_start)
    assert 'id="resetEmail"' not in html[forgot_start:forgot_end]
    assert '("#forgotBtn").addEventListener("click", startForgotPassword)' in js
    assert '("#sendResetCodeBtn").addEventListener("click", sendResetCode)' in js
    assert '("#doResetBtn").addEventListener("click", doResetPassword)' in js
    assert "function startForgotPassword()" in js
    assert "请先填写用户名或注册邮箱" in js
    assert '"/api/password-reset/lookup"' in js
    assert '"/api/password-reset/send-code"' in js
    assert '"/api/password-reset"' in js


def test_invite_landing_and_copy_flow_are_wired():
    js = app_js()

    assert "function normalizeInviteCode(value)" in js
    assert 'text.match(/\\bSW[A-Z0-9]{8,22}\\b/)' in js
    assert "function hydrateInviteFromUrl()" in js
    assert 'new URLSearchParams(location.search).get("invite")' in js
    assert "state.inviteCodeFromUrl = normalizeInviteCode(invite);" in js
    assert '("#regInvite")) $("#regInvite").value = state.inviteCodeFromUrl' in js
    assert 'const invite_code = normalizeInviteCode($("#regInvite").value);' in js
    assert "function loadInvitePreviewFromUrl()" in js
    assert "loadInvitePreviewFromUrl();" in js
    assert "/api/invite-preview/" in js
    assert 'id="acceptInviteBtn"' in js
    assert 'data-invite="${esc(inviteUrl)}"' in js
    assert "navigator.clipboard.writeText(text)" in js


def test_api_errors_and_register_success_are_user_readable():
    js = app_js()

    assert "function apiErrorMessage(detail)" in js
    assert "Array.isArray(detail)" in js
    assert "JSON.stringify(detail)" in js
    assert "throw new Error(apiErrorMessage(data.detail));" in js
    assert '$("#authMsg").textContent = "注册成功";' in js
    assert 'refreshIdentitySurfaces().catch((err) => console.warn("身份信息刷新失败", err));' in js


def test_verification_code_buttons_show_sent_state():
    js = app_js()

    assert re.search(r"async function sendCode[\s\S]+btn\.textContent = \"已发送\";[\s\S]+btn\.textContent = \"重新发送\";", js)
    assert re.search(r"async function sendResetCode[\s\S]+btn\.textContent = \"已发送\";[\s\S]+btn\.textContent = \"重新发送\";", js)


def test_search_api_is_not_limited_to_active_day(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        old_id = seed_rumor(target="远期搜索股份", code="600123.sh", date="2026-05-20")
        seed_rumor(target="当日股份", code="600124.sh", date="2026-06-06")

        today = client.get("/api/rumors", params={"section": "today", "limit": 20}).json()["items"]
        assert old_id not in {item["id"] for item in today}

        searched = client.get(
            "/api/rumors",
            params={"q": "远期搜索股份", "section": "", "date": "", "limit": 20},
        ).json()["items"]
        assert [item["id"] for item in searched] == [old_id]
