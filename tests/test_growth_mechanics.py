import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi.testclient import TestClient

from app import main


def make_client(monkeypatch, tmp_path: Path) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "stockwhisper.db")
    monkeypatch.setattr(main, "PRIVATE_DB", tmp_path / "missing.db")
    monkeypatch.setattr(main, "DAILY_PICKLE", tmp_path / "missing.pickle")
    monkeypatch.setattr(main, "RAW_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(main, "STOCK_LOOKUP_CACHE", data_dir / "stock_lookup.json")
    main.code_lookup.cache_clear()
    return TestClient(main.app)


def add_email_code(email: str, code: str = "123456") -> None:
    main.execute(
        "insert or replace into email_verifications(email, code, expires_at) values (?, ?, ?)",
        (email, code, 4_102_444_800),
    )


def register(client: TestClient, username: str, email: str, invite_code: str = "") -> dict:
    add_email_code(email)
    res = client.post(
        "/api/register",
        json={
            "username": username,
            "password": "secret12",
            "email": email,
            "code": "123456",
            "invite_code": invite_code,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["user"]


def test_tier_for_score_uses_shared_thresholds():
    assert main.tier_for_score(86) == "S"
    assert main.tier_for_score(85) == "A"
    assert main.tier_for_score(72) == "A"
    assert main.tier_for_score(71) == "B"
    assert main.tier_for_score(55) == "B"
    assert main.tier_for_score(54) == "C"


def test_send_code_falls_back_to_log_when_smtp_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)

    with make_client(monkeypatch, tmp_path) as client:
        res = client.post("/api/send-code", json={"email": "fish@example.com"})

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "delivery": "log"}
    assert "[send-code] fish@example.com =>" in capsys.readouterr().err
    rows = main.query("select code from email_verifications where email = ?", ("fish@example.com",))
    assert len(rows) == 1
    assert re.match(r"^\d{6}$", rows[0]["code"])


def test_registration_code_email_uses_formal_copy(monkeypatch, tmp_path):
    sent = {}

    def capture_email(email: str, code: str, subject: str, body: str) -> str:
        sent.update({"email": email, "code": code, "subject": subject, "body": body})
        return "smtp"

    monkeypatch.setattr(main, "deliver_email_code", capture_email)
    with make_client(monkeypatch, tmp_path) as client:
        res = client.post("/api/send-code", json={"email": "fish@example.com"})

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "delivery": "smtp"}
    assert sent["email"] == "fish@example.com"
    assert sent["subject"] == "股情报 StockWhisper 注册验证码"
    assert sent["code"] in sent["body"]
    assert "欢迎加入股情报 StockWhisper 社区" in sent["body"]
    assert "期待您在社区中发现更有价值的信号" in sent["body"]
    assert "验证码 5 分钟内有效" in sent["body"]
    assert "如果这不是您本人发起的注册请求" in sent["body"]


def test_registered_email_cannot_receive_new_code(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "alpha", "alpha@example.com")

        res = client.post("/api/send-code", json={"email": "alpha@example.com"})

    assert res.status_code == 409
    assert res.json()["detail"] == "该邮箱已注册"


def test_registered_email_cannot_register_again(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "alpha", "alpha@example.com")
        add_email_code("alpha@example.com")

        res = client.post(
            "/api/register",
            json={
                "username": "beta",
                "password": "secret12",
                "email": "ALPHA@example.com",
                "code": "123456",
            },
        )

    assert res.status_code == 409
    assert res.json()["detail"] == "该邮箱已注册"
    rows = main.query("select code from email_verifications where email = ?", ("alpha@example.com",))
    assert len(rows) == 1


def test_password_reset_updates_password_and_clears_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "deliver_email_code", lambda *args, **kwargs: "smtp")
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "alpha", "alpha@example.com")
        login = client.post("/api/login", json={"username": "alpha", "password": "secret12"})
        assert login.status_code == 200

        lookup = client.post("/api/password-reset/lookup", json={"account": "alpha"})
        assert lookup.status_code == 200, lookup.text
        assert lookup.json() == {"ok": True, "masked_email": "alp***@ex***e.com"}

        send = client.post("/api/password-reset/send-code", json={"account": "alpha"})
        rows = main.query("select code from password_reset_verifications where email = ?", ("alpha@example.com",))
        assert send.status_code == 200, send.text
        assert send.json() == {"ok": True, "delivery": "smtp", "masked_email": "alp***@ex***e.com"}
        assert len(rows) == 1

        reset = client.post(
            "/api/password-reset",
            json={"account": "alpha", "code": rows[0]["code"], "password": "newsecret"},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json() == {"ok": True}
        assert main.query("select 1 from password_reset_verifications where email = ?", ("alpha@example.com",)) == []
        assert main.query("select 1 from sessions") == []

        old_login = client.post("/api/login", json={"username": "alpha", "password": "secret12"})
        assert old_login.status_code == 401
        new_login = client.post("/api/login", json={"username": "alpha", "password": "newsecret"})
        assert new_login.status_code == 200
        email_login = client.post("/api/login", json={"username": "alpha@example.com", "password": "newsecret"})
        assert email_login.status_code == 200


def test_password_reset_code_email_uses_formal_copy(monkeypatch, tmp_path):
    sent = {}

    def capture_email(email: str, code: str, subject: str, body: str) -> str:
        sent.update({"email": email, "code": code, "subject": subject, "body": body})
        return "smtp"

    monkeypatch.setattr(main, "deliver_email_code", capture_email)
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "alpha", "alpha@example.com")

        res = client.post("/api/password-reset/send-code", json={"account": "alpha"})

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "delivery": "smtp", "masked_email": "alp***@ex***e.com"}
    assert sent["email"] == "alpha@example.com"
    assert sent["subject"] == "股情报 StockWhisper 密码重置验证码"
    assert sent["code"] in sent["body"]
    assert "您正在为股情报 StockWhisper 账号重置密码" in sent["body"]
    assert "为保护账号安全" in sent["body"]
    assert "验证码 5 分钟内有效" in sent["body"]
    assert "您的原密码不会因此被修改" in sent["body"]


def test_password_reset_rejects_unregistered_email(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        res = client.post("/api/password-reset/send-code", json={"account": "missing@example.com"})

    assert res.status_code == 404
    assert res.json()["detail"] == "账号不存在或未绑定邮箱"


def seed_rumor(
    submitter_id: Optional[int] = None,
    tier: str = "C",
    score: int = 45,
    target: str = "测试股份",
    code: str = "600000.sh",
    logic: str = "订单落地带来业绩弹性",
    raw: str = "测试股份获得大额订单，客户验证进展明确，产能释放节奏清晰。",
    date: str = "2026-06-06",
) -> int:
    main.execute(
        """
        insert into rumors
        (submitter_id, submitter_name, recommendation_date, recommender, target, stock_codes, logic,
         raw_content, institution, key_points, ai_score, ai_tier, ai_reasons, created_at, source)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            submitter_id,
            "source-a",
            date,
            "analyst",
            target,
            json.dumps([{"name": target, "code": code}], ensure_ascii=False),
            logic,
            raw,
            "测试机构",
            json.dumps(["订单落地", "客户验证"], ensure_ascii=False),
            score,
            tier,
            json.dumps(["标的明确", "催化因素清晰"], ensure_ascii=False),
            "2026-06-06 09:30:00",
            "community",
        ),
    )
    return main.query("select id from rumors order by id desc limit 1")[0]["id"]


def seed_backtest(
    rumor_id: int,
    signal_value: float = 82.0,
    ret_1: float = 0.03,
    ret_5: float = 0.08,
    ret_20: float = 0.12,
    drawdown: float = -0.03,
    status: str = "ok",
) -> None:
    main.execute(
        """
        insert or replace into backtests
        (rumor_id, code, name, start_date, price_start, ret_5, ret_20, ret_60, max_ret_60,
         ret_t1_1, ret_t1_5, ret_t1_20, signal_value, max_drawdown_20, status, details)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rumor_id,
            "600000.sh",
            "测试股份",
            "2026-06-07",
            10.0,
            None,
            None,
            None,
            None,
            ret_1,
            ret_5,
            ret_20,
            signal_value,
            drawdown,
            status,
            "测试回测",
        ),
    )


def test_invite_registration_rewards_both_users(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        inviter = register(client, "alpha", "alpha@example.com")
        preview = client.get(f"/api/invite-preview/{inviter['invite_code']}")
        assert preview.status_code == 200
        preview_data = preview.json()
        assert preview_data["valid"] is True
        assert preview_data["invite_code"] == inviter["invite_code"]
        assert preview_data["inviter"]["display_name"] == "alpha"
        assert "20 XP" in preview_data["invitee_reward"]
        assert "10 次直看额度" in preview_data["invitee_reward"]
        landing = preview_data["landing_value"]
        assert landing["headline"]
        assert len(landing["proof_points"]) == 3
        assert {item["label"] for item in landing["proof_points"]} >= {"注册奖励", "邀请人源分", "源等级"}
        assert len(landing["activation_steps"]) == 3
        assert landing["cta"]["view"] == "register"

        invalid = client.get("/api/invite-preview/BADCODE")
        assert invalid.status_code == 200
        assert invalid.json()["valid"] is False
        assert invalid.json()["landing_value"]["proof_points"]

        guest_center = client.get("/api/referral-center").json()
        assert guest_center["registered"] is True
        assert guest_center["invite_plan"]["state"] == "active"
        assert guest_center["invite_plan"]["next_needed"] == 1

        invited = register(client, "beta", "beta@example.com", inviter["invite_code"])

        inviter_row = main.query("select xp, direct_quota, invite_count from users where username = 'alpha'")[0]
        invited_row = main.query("select xp, direct_quota, invited_by from users where username = 'beta'")[0]
        events = main.query("select reward_xp, reward_quota from referral_events")

        assert inviter_row["xp"] == 30
        assert inviter_row["direct_quota"] == 20
        assert inviter_row["invite_count"] == 1
        assert invited["xp"] == 20
        assert invited_row["direct_quota"] == 10
        assert invited_row["invited_by"] is not None
        assert [dict(e) for e in events] == [{"reward_xp": 30, "reward_quota": 10}]

        client.post("/api/logout")
        client.post("/api/login", json={"username": "alpha", "password": "secret12"})
        center = client.get("/api/referral-center")
        assert center.status_code == 200
        data = center.json()
        assert data["registered"] is True
        assert data["invite_code"] == inviter["invite_code"]
        assert data["stats"]["invite_count"] == 1
        assert data["stats"]["reward_xp"] == 30
        assert data["stats"]["reward_quota"] == 10
        assert data["invite_plan"]["state"] == "active"
        assert data["invite_plan"]["next_needed"] == 2
        assert "邀请" in data["invite_plan"]["headline"]
        assert data["invite_plan"]["share_copy"]
        assert data["momentum"]["earned_value"] == "30 XP + 10 次直看额度"
        assert data["momentum"]["next_needed"] == 2
        assert data["momentum"]["next_reward"]
        assert {item["key"] for item in data["momentum"]["actions"]} == {"copy_link", "copy_pitch", "review_rank"}
        assert data["milestones"][0]["completed"] is True
        assert data["recent"][0]["invitee_name"] == invited["display_name"]
        assert data["leaderboard"][0]["display_name"] == "alpha"


def test_invite_code_accepts_shared_link_or_watermarked_text(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        inviter = register(client, "alpha", "alpha@example.com")
        invite_url = f"https://stockwhisper.example/?invite={inviter['invite_code']}"
        shared = f"{invite_url}\n\n--\n来源：股情报 StockWhisper\n版权标记：{main.hidden_copyright_mark()}"

        preview = client.get(f"/api/invite-preview/{quote(invite_url, safe='')}")
        assert preview.status_code == 200
        assert preview.json()["valid"] is True
        assert preview.json()["invite_code"] == inviter["invite_code"]

        invited = register(client, "beta", "beta@example.com", shared)
        inviter_row = main.query("select xp, direct_quota, invite_count from users where username = 'alpha'")[0]
        invited_row = main.query("select xp, direct_quota, invited_by from users where username = 'beta'")[0]

        assert invited["xp"] == 20
        assert invited_row["direct_quota"] == 10
        assert invited_row["invited_by"] == inviter["id"]
        assert inviter_row["xp"] == 30
        assert inviter_row["direct_quota"] == 20
        assert inviter_row["invite_count"] == 1


def test_activation_center_explains_guest_and_registered_value(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        seed_rumor(tier="S", score=91)
        guest = client.get("/api/activation-center")
        assert guest.status_code == 200
        guest_data = guest.json()
        assert guest_data["registered"] is False
        assert guest_data["summary"]["high_value_rumors"] == 1
        assert guest_data["summary"]["locked_high_value"] == 1
        assert guest_data["starter_rewards"][1]["label"] == "邀请码注册"
        assert guest_data["next_steps"][0]["key"] == "register"
        assert guest_data["next_steps"][0]["completed"] is False
        assert guest_data["activation_playbook"]["stage"] == "visitor"
        assert guest_data["activation_playbook"]["primary_action"]["key"] == "register"
        assert guest_data["activation_playbook"]["progress"] == 0
        assert guest_data["starter_watchlist"]["headline"]
        assert guest_data["starter_watchlist"]["items"]
        assert {"code", "name", "rumor_count", "top_score", "reason"} <= set(guest_data["starter_watchlist"]["items"][0])
        assert guest_data["starter_watchlist"]["primary_action"]["key"] == "add_watch"
        assert guest_data["rights_fingerprint"]

        user = register(client, "activate", "activate@example.com")
        registered = client.get("/api/activation-center").json()
        assert registered["registered"] is True
        assert registered["invite_code"] == user["invite_code"]
        steps = {item["key"]: item for item in registered["next_steps"]}
        assert steps["register"]["completed"] is True
        assert registered["activation_playbook"]["stage"] == "activating"
        assert registered["activation_playbook"]["primary_action"]["key"] in {"watch", "submit", "invite"}
        assert registered["activation_playbook"]["progress"] > 0
        assert registered["summary"]["direct_quota"] == 10
        starter = registered["starter_watchlist"]["items"][0]
        added = client.post("/api/watchlist", json={"code": starter["code"], "name": starter["name"]})
        assert added.status_code == 200
        assert added.json()["items"][0]["code"] == starter["code"]


def test_homepage_and_responses_carry_hidden_rights_marks(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert res.headers["X-StockWhisper-Mark"] == main.hidden_copyright_mark()
        assert res.headers["X-Content-Policy"] == "non-commercial-learning-only"
        assert 'meta name="sw-origin"' in res.text
        assert 'meta name="sw-copy-policy"' in res.text
        assert main.hidden_copyright_mark() in res.text
        assert 'class="rights-mark"' in res.text
        assert 'data-sw-mark="StockWhisper::open-community-intel"' in res.text
        assert 'id="authIncentive"' in res.text

        app_js = client.get("/static/app.js")
        assert app_js.status_code == 200
        assert "installCopyWatermark" in app_js.text
        assert "StockWhisper::CC-BY-NC-SA-4.0::openclaw-community-intel" in app_js.text
        assert "zeroWidthEncode" in app_js.text
        assert "renderAuthIncentive" in app_js.text
        assert "MEMBER ACCESS" in app_js.text
        assert "forensicSignature" in app_js.text
        assert "取证签名" in app_js.text
        assert "页面指纹" in app_js.text
        assert "rights_protection" in app_js.text
        assert "renderRecentBacktestShowcase" in app_js.text
        assert "renderTodaySignalBoard" in app_js.text
        assert "dimension-score-strip" in app_js.text
        assert "watchManualAdd" in app_js.text

        styles = client.get("/static/styles.css")
        assert styles.status_code == 200
        assert "--accent: #d21f2b" in styles.text
        assert ".pos { color: #d21f2b" in styles.text
        assert ".neg { color: #138a52" in styles.text
        assert "watch-manual-add" in styles.text
        assert "today-signal-board" in styles.text

        community = client.get("/api/community-insight").json()
        envelope = community["rights_envelope"]
        assert envelope["scope"] == "community-insight"
        assert envelope["mark"] == main.hidden_copyright_mark()
        assert envelope["license"] == "CC-BY-NC-SA-4.0"
        assert len(envelope["fingerprint"]) == 32
        assert "\u200b" in envelope["zero_width"] or "\u200c" in envelope["zero_width"]

        seed_rumor(tier="C", score=55)
        feed = client.get("/api/rumors?limit=5").json()
        feed_envelope = feed["rights_envelope"]
        assert feed_envelope["scope"] == "rumor-feed"
        assert feed_envelope["mark"] == main.hidden_copyright_mark()
        assert len(feed_envelope["fingerprint"]) == 32
        assert "RIGHTS MARK" in app_js.text


def test_value_framework_exposes_scoring_and_invite_rewards(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        res = client.get("/api/value-framework")
        assert res.status_code == 200
        data = res.json()
        assert [d["key"] for d in data["score_dimensions"]] == [
            "specificity",
            "evidence",
            "freshness",
            "source",
            "verifiability",
            "novelty",
        ]
        assert data["invite_rewards"]["new_user"]
        assert data["tiers"][0]["tier"] == "S"
        assert data["value_verdict"]["name"] == "社区价值指数"
        assert "信息源分" in data["value_verdict"]["inputs"]
        calibration = data["calibration"]
        assert calibration["headline"] == "评分校准规则"
        assert {item["key"] for item in calibration["positive"]} == {"evidence", "source", "consensus"}
        assert {item["key"] for item in calibration["negative"]} == {"duplicate", "risk", "reports"}
        assert calibration["principles"]


def test_score_preview_returns_quality_checklist(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        res = client.post(
            "/api/score-preview",
            json={
                "target": "测试股份",
                "stock_codes": '[{"name":"测试股份","code":"600000.sh"}]',
                "logic": "订单落地带来业绩弹性，客户验证和产能释放同步推进",
                "raw_content": "测试股份获得大额订单，客户验证进展明确，产能释放节奏清晰。机构称订单交付和客户导入已进入验证节点，政策支持和国产替代需求同步催化，后续业绩有望兑现，需要跟踪订单验收、产能爬坡和客户复购。",
                "institution": "测试机构",
                "recommender": "analyst",
                "recommendation_date": "2026-06-06",
            },
        )

        assert res.status_code == 200, res.text
        data = res.json()
        assert data["score"]["score"] >= 55
        assert data["checklist"][0]["key"] == "target"
        assert any(item["ok"] for item in data["checklist"])
        assert data["suggestions"]
        assert data["quality_floor"] in ("可交换", "建议补充后提交")
        assert "novelty" in data
        assert data["exchange_readiness"]["state"] in {"ready", "exchangeable"}
        assert data["exchange_readiness"]["score"] > 50
        assert data["exchange_readiness"]["ok_count"] <= data["exchange_readiness"]["total"]
        assert data["participation_reward"]["estimated_xp"] > 0
        assert data["participation_reward"]["source_score_delta"] > 0
        assert data["participation_reward"]["exchange_power"] in {"S", "A", "B", "C"}
        assert data["participation_reward"]["perks"]
        ladder = data["evidence_ladder"]
        assert ladder["grade"] in {"A", "B", "C"}
        assert ladder["score"] > 0
        assert {item["key"] for item in ladder["levels"]} == {"source", "target", "catalyst", "verification", "risk_boundary"}
        assert ladder["next_steps"]

        weak = client.post(
            "/api/score-preview",
            json={
                "target": "",
                "stock_codes": "[]",
                "logic": "听说会涨",
                "raw_content": "听说会涨，看看，暂无来源和代码。",
                "institution": "",
                "recommender": "",
                "recommendation_date": "",
            },
        ).json()
        assert weak["exchange_readiness"]["state"] == "draft"
        assert weak["exchange_readiness"]["blockers"]
        assert weak["improvement_plan"]
        weak_actions = {item["action"] for item in weak["improvement_plan"]}
        weak_fields = {item["field"] for item in weak["improvement_plan"]}
        assert {"manual", "fill_if_empty"} & weak_actions
        assert {"logic", "institution", "recommendation_date", "target"} & weak_fields
        assert weak["participation_reward"]["exchange_power"] == "C"
        assert weak["participation_reward"]["estimated_xp"] <= 8
        assert weak["evidence_ladder"]["grade"] == "C"
        assert "来源链路" in weak["evidence_ladder"]["next_steps"]

        code_hint = client.post(
            "/api/score-preview",
            json={
                "target": "",
                "stock_codes": "[]",
                "logic": "",
                "raw_content": "600000 获得大额订单，客户验证推进，产能释放节奏明确。",
                "institution": "",
                "recommender": "",
                "recommendation_date": "",
            },
        )
        assert code_hint.status_code == 200, code_hint.text
        stock_plan = code_hint.json()["improvement_plan"]
        assert any(item["field"] == "stock_codes" and item["action"] == "apply_stock_rows" for item in stock_plan)


def test_guest_can_preview_but_must_register_before_submission(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        payload = {
            "target": "测试股份",
            "stock_codes": '[{"name":"测试股份","code":"600000.sh"}]',
            "logic": "订单落地带来业绩弹性，客户验证和产能释放同步推进",
            "raw_content": "测试股份获得大额订单，客户验证进展明确，产能释放节奏清晰。",
            "institution": "测试机构",
            "recommender": "analyst",
            "recommendation_date": "2026-06-06",
        }

        preview = client.post("/api/score-preview", json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["participation_reward"]["estimated_xp"] > 0

        submitted = client.post("/api/rumors", json=payload)
        assert submitted.status_code == 401
        assert "注册" in submitted.json()["detail"]


def test_submission_uses_llm_logic_score_in_dimension_scores(monkeypatch, tmp_path):
    def fake_llm_summary(payload):
        return {
            "target": "逻辑股份",
            "logic": "订单验证清晰，产能兑现节奏明确",
            "institution": "测试机构",
            "recommender": "测试员",
            "key_points": ["订单验证", "产能兑现"],
            "logic_score": 91,
            "logic_reason": "标的、催化和验证节点明确",
            "summary_source": "llm",
        }

    monkeypatch.setattr(main, "llm_summary", fake_llm_summary)
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "llmlogic", "llmlogic@example.com")
        payload = {
            "target": "",
            "logic": "",
            "raw_content": "逻辑股份拿到客户订单，产能释放节奏清晰，有可跟踪的验证节点。",
            "institution": "",
            "recommender": "",
            "recommendation_date": "2026-06-06",
        }
        submitted = client.post("/api/rumors", json=payload)
        assert submitted.status_code == 200, submitted.text
        rumor_id = submitted.json()["item"]["id"]
        row = main.query("select ai_reasons from rumors where id = ?", (rumor_id,))[0]
        stored = json.loads(row["ai_reasons"])
        assert stored["logic_score"] == 91
        detail = client.get(f"/api/rumors/{rumor_id}").json()["item"]
        logic_dim = next(item for item in detail["dimension_scores"]["items"] if item["key"] == "logic")
        assert logic_dim["score"] == 91
        assert "标的、催化" in logic_dim["detail"]
        assert detail["ai_reasons"][0].startswith("大模型逻辑评分 91")


def test_a_tier_submission_rewards_direct_quota(monkeypatch, tmp_path):
    def force_a_score(scored, _summary):
        next_score = dict(scored)
        next_score["score"] = 78
        next_score["tier"] = "A"
        return next_score

    monkeypatch.setattr(main, "score_with_llm_logic", force_a_score)
    with make_client(monkeypatch, tmp_path) as client:
        user = register(client, "quota_writer", "quota_writer@example.com")
        payload = {
            "target": "额度股份",
            "stock_codes": '[{"name":"额度股份","code":"600000.sh"}]',
            "logic": "订单落地带来业绩弹性，客户验证和产能释放同步推进",
            "raw_content": "额度股份获得大额订单，客户验证进展明确，产能释放节奏清晰。订单交付和客户导入已进入验证节点，政策支持和国产替代需求同步催化，后续业绩有望兑现。",
            "institution": "测试机构",
            "recommender": "analyst",
            "recommendation_date": "2026-06-06",
        }

        submitted = client.post("/api/rumors", json=payload)

        assert submitted.status_code == 200, submitted.text
        data = submitted.json()
        assert data["score"]["tier"] == "A"
        assert data["submission_reward"]["quota_delta"] == 3
        row = main.query("select direct_quota from users where id = ?", (user["id"],))[0]
        assert row["direct_quota"] == 13


def test_score_preview_penalizes_duplicate_like_submission(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "novelty", "novelty@example.com")
        seed_rumor(tier="A", score=78, target="重复股份", code="600000.sh")
        payload = {
            "target": "重复股份",
            "stock_codes": '[{"name":"重复股份","code":"600000.sh"}]',
            "logic": "订单落地带来业绩弹性",
            "raw_content": "测试股份获得大额订单，客户验证进展明确，产能释放节奏清晰。",
            "institution": "测试机构",
            "recommender": "analyst",
            "recommendation_date": "2026-06-06",
        }

        preview = client.post("/api/score-preview", json=payload)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["novelty"]["penalty"] >= 8
        assert data["score"]["dimensions"]["novelty"] <= 5
        assert any(item["key"] == "novelty" and item["ok"] is False for item in data["checklist"])

        submitted = client.post("/api/rumors", json=payload)
        assert submitted.status_code == 200, submitted.text
        submitted_data = submitted.json()
        assert submitted_data["novelty"]["penalty"] >= 8
        assert "稀缺性扣分" in "、".join(submitted_data["score"]["reasons"])
        reward = submitted_data["submission_reward"]
        assert {"xp_delta", "contribution_delta", "source_score_delta", "level", "provider_grade", "exchange_label", "unlocked_label"} <= set(reward)
        assert reward["level"]["name"]
        assert reward["provider_grade"]["name"]
        assert "交换" in reward["exchange_label"] or "贡献" in reward["exchange_label"]


def test_novelty_penalty_weights_same_day_and_recent_same_target_logic(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "novelty_time", "novelty_time@example.com")
        seed_rumor(
            tier="A",
            score=80,
            target="重复股份",
            code="600000.sh",
            logic="订单放量客户验证产能释放业绩弹性",
            raw="重复股份订单放量，客户验证清晰，产能释放推动业绩弹性。",
            date="2026-06-06",
        )
        payload = {
            "target": "重复股份",
            "stock_codes": '[{"name":"重复股份","code":"600000.sh"}]',
            "logic": "订单放量客户验证产能释放业绩弹性",
            "raw_content": "重复股份订单放量，客户验证清晰，产能释放推动业绩弹性。",
            "recommendation_date": "2026-06-06",
        }

        same_day = client.post("/api/score-preview", json=payload).json()["novelty"]
        assert same_day["state"] == "duplicate"
        assert same_day["same_day_overlap"] >= 0.58
        assert same_day["penalty"] >= 16

        payload["recommendation_date"] = "2026-06-08"
        recent = client.post("/api/score-preview", json=payload).json()["novelty"]
        assert recent["state"] == "recent_duplicate"
        assert recent["recent_overlap"] >= 0.58
        assert recent["penalty"] >= 10


def test_score_preview_and_submission_flag_high_risk_language(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "riskuser", "risk@example.com")
        payload = {
            "target": "风险股份",
            "stock_codes": '[{"name":"风险股份","code":"600000.sh"}]',
            "logic": "订单落地带来业绩弹性，但文案出现保收益喊单风险",
            "raw_content": "风险股份据传获得大额订单，老师喊单说稳赚不赔，建议满仓跟上，内幕资金马上拉升。客户验证和产能释放仍需跟踪。",
            "institution": "测试群聊",
            "recommender": "risk_teacher",
            "recommendation_date": "2026-06-06",
        }

        preview = client.post("/api/score-preview", json=payload)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        risk = data["risk_analysis"]
        assert risk["level"] == "high"
        assert risk["score_penalty"] > 0
        assert {"guarantee", "solicitation", "position_pressure", "insider"} & {flag["key"] for flag in risk["flags"]}
        assert any(item["key"] == "risk" and item["ok"] is False for item in data["checklist"])
        assert any("保收益" in item or "喊单" in item for item in data["suggestions"])
        assert any(item["label"] == "改写高风险话术" for item in data["improvement_plan"])

        submitted = client.post("/api/rumors", json=payload)
        assert submitted.status_code == 200, submitted.text
        submitted_data = submitted.json()
        assert submitted_data["risk_analysis"]["level"] == "high"
        assert "风控提示" in "、".join(submitted_data["score"]["reasons"])


def test_rumor_feed_and_detail_include_risk_ledger(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "riskfeed", "riskfeed@example.com")
        payload = {
            "target": "风控股份",
            "stock_codes": '[{"name":"风控股份","code":"600000.sh"}]',
            "logic": "订单落地但需要剔除喊单式表达",
            "raw_content": "风控股份获得大额订单，但群里老师喊单称稳赚，建议满仓跟上。实际还需要跟踪客户验收和订单交付。",
            "institution": "测试群聊",
            "recommender": "teacher",
            "recommendation_date": "2026-06-06",
        }
        submitted = client.post("/api/rumors", json=payload)
        assert submitted.status_code == 200, submitted.text
        rumor_id = submitted.json()["item"]["id"]

        feed = client.get("/api/rumors").json()["items"]
        item = next(row for row in feed if row["id"] == rumor_id)
        assert item["risk_analysis"]["level"] == "high"
        assert any(entry["key"] == "risk" and entry["state"] == "weak" for entry in item["verification_ledger"])

        detail = client.get(f"/api/rumors/{rumor_id}").json()["item"]
        assert detail["risk_analysis"]["flags"]
        ledger = {entry["key"]: entry for entry in detail["verification_ledger"]}
        assert ledger["risk"]["label"] == "风控话术"
        assert "保收益" in ledger["risk"]["detail"] or "喊单" in ledger["risk"]["detail"]


def test_rumor_detail_exposes_verification_tasks(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "taskuser", "taskuser@example.com")
        payload = {
            "target": "任务股份",
            "stock_codes": '[{"name":"任务股份","code":"600000.sh"}]',
            "logic": "大额订单和产能释放带来业绩弹性",
            "raw_content": "任务股份获得大额订单，客户验证进展明确，产能释放节奏清晰，业绩弹性需要看订单交付和毛利率变化。",
            "institution": "测试机构",
            "recommender": "analyst",
            "recommendation_date": "2026-06-06",
        }
        submitted = client.post("/api/rumors", json=payload)
        assert submitted.status_code == 200, submitted.text
        rumor_id = submitted.json()["item"]["id"]

        detail = client.get(f"/api/rumors/{rumor_id}").json()["item"]
        assert detail["target"] == "任务股份"
        assert detail["stock_codes"] == [{"name": "任务股份", "code": "600000.sh"}]
        assert detail["discussion"]["useful"] == 0
        assert detail["discussion"]["doubt"] == 0
        assert detail["watched"] is False
        assert detail["unlocked"] is True
        assert detail["rights"]["mark"]

        locked_id = seed_rumor(tier="S", score=93, target="锁定任务", code="000001.sz")
        feed_item = next(row for row in client.get("/api/rumors").json()["items"] if row["id"] == locked_id)
        assert feed_item["hidden"] is True
        assert feed_item["unlocked"] is False
        assert feed_item["stock_codes"] == []
        assert feed_item["logic"] == "已锁定。分享同等价值消息或提升等级后查看。"


def test_submission_exchange_match_explains_and_skips_own_rumors(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        user = register(client, "matcher", "matcher@example.com")
        own_id = seed_rumor(submitter_id=user["id"], tier="B", score=65, target="自有股份", code="600001.sh")
        external_id = seed_rumor(tier="B", score=66, target="匹配股份", code="600000.sh")
        payload = {
            "target": "匹配股份",
            "stock_codes": '[{"name":"匹配股份","code":"600000.sh"}]',
            "logic": "订单落地带来业绩弹性，客户验证和产能释放同步推进",
            "raw_content": "匹配股份获得大额订单，客户验证进展明确，产能释放节奏清晰。订单交付和客户导入已进入验证节点，政策支持和国产替代需求同步催化，后续业绩有望兑现，需要跟踪订单验收、产能爬坡和客户复购。渠道反馈二季度排产环比提升，客户验厂节奏提前，若交付节点兑现将带来利润弹性。",
            "institution": "测试机构",
            "recommender": "analyst",
            "recommendation_date": "2026-06-06",
        }

        submitted = client.post("/api/rumors", json=payload)
        assert submitted.status_code == 200, submitted.text
        data = submitted.json()
        assert data["unlocked"]["id"] == external_id
        assert data["unlocked"]["id"] != own_id
        match = data["submission_reward"]["unlock_match"]
        assert match["matched_tier"] == "B"
        assert match["score_gap"] <= 12
        assert match["reason"] in {"同级同标的且可信度清洁", "同级且可信度清洁", "可信度清洁且分数接近", "分数接近"}


def test_growth_center_exposes_missions_and_upgrade_plan(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        user = register(client, "growth", "growth@example.com")
        rumor_id = seed_rumor(submitter_id=user["id"], tier="C", score=45)
        client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})
        res = client.get("/api/growth-center")

        assert res.status_code == 200
        data = res.json()
        missions = {item["key"]: item for item in data["missions"]}
        assert missions["register"]["completed"] is True
        assert missions["first_submit"]["completed"] is True
        assert missions["watch"]["completed"] is False
        assert missions["invite"]["completed"] is False
        assert data["summary"]["completed"] >= 2
        assert data["summary"]["next_action"]["key"] in missions
        assert data["upgrade"]["current"]["name"]
        ledger = data["ledger"]
        assert ledger["totals"]["base_xp"] >= 0
        assert ledger["totals"]["participation_xp"] >= 2
        assert {item["key"] for item in ledger["sources"]} == {"submission", "participation", "invite"}
        assert {"submission", "participation"} <= {item["kind"] for item in ledger["entries"]}
        assert ledger["rights_fingerprint"]
        assert data["rights_fingerprint"]


def test_community_insight_exposes_topic_radar(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        seed_rumor(tier="C", score=65)
        res = client.get("/api/community-insight")

        assert res.status_code == 200
        radar = res.json()["topic_radar"]
        theme_names = {item["name"] for item in radar["themes"]}
        stock_names = {item["name"] for item in radar["stocks"]}
        assert "订单" in theme_names
        assert "客户" in theme_names
        assert "测试股份" in stock_names


def test_community_rooms_expose_theme_and_stock_entry_points(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        seed_rumor(tier="S", score=91, target="测试股份", code="600000.sh")
        seed_rumor(tier="A", score=78, target="测试股份", code="600000.sh")

        res = client.get("/api/community-rooms")
        assert res.status_code == 200
        data = res.json()
        assert data["summary"]["total"] >= 2
        rooms = data["rooms"]
        names = {room["name"] for room in rooms}
        assert "订单" in names
        assert "测试股份" in names
        stock_room = next(room for room in rooms if room["name"] == "测试股份")
        assert stock_room["kind"] == "stock"
        assert stock_room["count"] == 2
        assert stock_room["tier_mix"]["S"] == 1
        assert stock_room["top_rumor"]["value_verdict"]["index"] > 0
        assert stock_room["action"]["query"] == "测试股份"


def test_community_insight_exposes_daily_brief(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        rumor_id = seed_rumor(tier="S", score=90)
        seed_backtest(rumor_id, signal_value=86, ret_1=0.03, ret_5=0.08, ret_20=0.12)
        res = client.get("/api/community-insight")

        assert res.status_code == 200
        data = res.json()
        brief = data["daily_brief"]
        assert brief["headline"]
        assert len(brief["bullets"]) == 3
        assert {item["label"] for item in brief["actions"]} >= {"看S级", "看热点", "补线索"}
        assert brief["risk_notes"]
        workflow = data["daily_workflow"]
        assert workflow["headline"]
        assert workflow["rights_fingerprint"]
        assert [item["key"] for item in workflow["steps"]] == ["screen", "verify", "track"]
        assert all({"label", "title", "detail", "metric", "view", "query", "tier", "action"} <= set(item) for item in workflow["steps"])
        queue = data["action_queue"]
        assert queue
        assert queue[0]["priority"] == 1
        assert {"key", "label", "target", "detail", "state", "view", "action"} <= set(queue[0])
        assert {item["key"] for item in queue} & {"unlock_high", "review_high"}
        proof = {item["key"]: item for item in data["value_proof"]}
        assert set(proof) == {"high_value", "verified", "sources", "exchange"}
        assert proof["high_value"]["value"] == "1条"
        assert proof["verified"]["value"] == "1条"
        assert proof["exchange"]["state"] == "locked"
        showcase = data["recent_backtest_showcase"]
        assert showcase["headline"]
        assert len(showcase["dates"]) <= 3
        assert showcase["items"]
        assert showcase["items"][0]["signal_value"] == 86
        assert showcase["items"][0]["dimension_composite"] >= 0
        assert {"ret_t1_1", "ret_t1_5", "ret_t1_20", "outcome", "rumor"} <= set(showcase["items"][0])
        dim_scores = showcase["items"][0]["rumor"]["dimension_scores"]
        assert {item["key"] for item in dim_scores["items"]} == {"logic", "novelty", "backtest", "observer"}
        assert dim_scores["composite"] >= 0
        board = data["today_signal_board"]
        assert board["headline"]
        assert {"ordinary", "high_value", "unlock_prompt"} <= set(board)
        assert board["unlock_prompt"]["action"]["tier"] == "S"
        bounty = data["bounty_board"]
        assert bounty["headline"]
        assert bounty["rights_fingerprint"]
        assert bounty["summary"]["items"] >= 1
        assert {"active", "locked", "total_reward_xp", "items"} <= set(bounty["summary"])
        assert {"rumor_id", "target", "tier", "score", "task", "action", "reward_xp", "state", "detail"} <= set(bounty["items"][0])


def test_recent_backtest_showcase_uses_recent_backtested_dates(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        seed_rumor(tier="S", score=93, target="今日无回测", code="600001.sh", date="2026-06-10")
        old_id = seed_rumor(tier="A", score=82, target="历史强信号", code="600000.sh", date="2026-06-06")
        seed_backtest(old_id, signal_value=91, ret_1=0.05, ret_5=0.09, ret_20=0.13)

        data = client.get("/api/community-insight").json()
        showcase = data["recent_backtest_showcase"]
        assert showcase["dates"] == ["2026-06-06"]
        assert showcase["items"]
        assert showcase["items"][0]["rumor"]["target"] in {"历史强信号", "历**"}
        assert showcase["items"][0]["signal_value"] == 91


def test_recent_backtest_showcase_marks_watchlist_matches(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "showcase_watch", "showcase_watch@example.com")
        rumor_id = seed_rumor(tier="A", score=82, target="自选历史强信号", code="600000.sh", date="2026-06-06")
        seed_backtest(rumor_id, signal_value=91, ret_1=0.05, ret_5=0.09, ret_20=0.13)

        before = client.get("/api/community-insight").json()["recent_backtest_showcase"]["items"][0]["rumor"]
        assert before["watched"] is False

        added = client.post("/api/watchlist", json={"code": "600000.sh", "name": "自选历史强信号"})
        assert added.status_code == 200, added.text

        after = client.get("/api/community-insight").json()["recent_backtest_showcase"]["items"][0]["rumor"]
        assert after["id"] == rumor_id
        assert after["watched"] is True


def test_community_insight_exposes_actionable_opportunity_summary(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "opportunity", "opportunity@example.com")
        seed_rumor(tier="S", score=93, target="机会股份", code="600000.sh")
        seed_rumor(tier="A", score=78, target="订单股份", code="000001.sz")

        res = client.get("/api/community-insight")
        assert res.status_code == 200, res.text
        opportunity = res.json()["opportunity_summary"]
        assert opportunity["headline"]
        assert opportunity["rights_fingerprint"]
        cards = {item["key"]: item for item in opportunity["cards"]}
        assert {"locked", "direct", "watch", "hotspot"} <= set(cards)
        assert cards["locked"]["value"] >= 1
        assert opportunity["primary_action"]["key"] in {"direct_unlock", "submit_for_exchange", "track_hotspot", "register"}
        assert {action["key"] for action in opportunity["actions"]} >= {"hotspot", "submit"}
        best_pick = opportunity["best_pick"]
        assert {
            "rumor_id",
            "target",
            "tier",
            "score",
            "adjusted_score",
            "risk_label",
            "trust_state",
            "consensus_label",
            "outcome_label",
            "summary",
            "action",
        } <= set(best_pick)
        assert best_pick["adjusted_score"] >= 0
        assert best_pick["summary"]
        assert best_pick["action"]["view"] == "feed"
        assert best_pick["action"]["query"]
        plan = best_pick["verification_plan"]
        assert plan["stage"] in {"priority", "risk_first", "consensus", "build_evidence"}
        assert plan["headline"]
        assert plan["summary"]
        assert plan["evidence_gap"]
        assert plan["cta"]["view"] == "feed"
        assert plan["steps"]
        assert {"key", "label", "priority", "detail", "action"} <= set(plan["steps"][0])


def test_exchange_desk_exposes_locked_opportunities_and_unlock_paths(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        user = register(client, "exchange", "exchange@example.com")
        seed_rumor(tier="S", score=92)

        res = client.get("/api/exchange-desk")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["resources"]["direct_quota"] == user["direct_quota"]
        assert data["opportunities"][0]["item"]["hidden"] is True
        assert data["opportunities"][0]["gap"]["tier"] == "S"
        assert data["opportunities"][0]["gap"]["xp_gap"] > 0
        assert data["opportunities"][0]["direct_unlockable"] is True
        assert {action["key"] for action in data["actions"]} >= {"direct_unlock", "submit", "invite"}


def test_guest_must_register_before_direct_unlock(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        rumor_id = seed_rumor(tier="S", score=92)

        desk = client.get("/api/exchange-desk")
        assert desk.status_code == 200, desk.text
        data = desk.json()
        assert data["opportunities"][0]["direct_unlockable"] is False
        assert any(action["key"] == "register" for action in data["actions"])

        res = client.post(f"/api/rumors/{rumor_id}/unlock")
        assert res.status_code == 401
        assert "注册" in res.json()["detail"]


def test_locked_rumors_explain_personal_unlock_path(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        seed_rumor(tier="S", score=92)
        guest_item = client.get("/api/rumors?tier=S&limit=1").json()["items"][0]
        assert guest_item["hidden"] is True
        assert guest_item["unlock_path"]["state"] == "guest"
        assert {action["key"] for action in guest_item["unlock_path"]["actions"]} >= {"register", "submit"}

        user = register(client, "pathuser", "pathuser@example.com")
        direct_item = client.get("/api/rumors?tier=S&limit=1").json()["items"][0]
        assert direct_item["unlock_path"]["state"] == "direct"
        assert "直看额度" in direct_item["unlock_path"]["summary"]

        client.post(f"/api/rumors/{direct_item['id']}/unlock")
        seed_rumor(tier="S", score=91, target="新锁定", code="000001.sz")
        earn_item = next(row for row in client.get("/api/rumors?tier=S&limit=5").json()["items"] if row["target"] != "测试股份")
        assert earn_item["unlock_path"]["state"] == "earn"
        assert "XP" in earn_item["unlock_path"]["summary"]
        assert {action["key"] for action in earn_item["unlock_path"]["actions"]} >= {"submit", "invite"}


def test_watchlist_filters_personal_signal_flow(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "watcher", "watcher@example.com")
        rumor_id = seed_rumor(tier="C", score=65)
        seed_backtest(rumor_id, signal_value=72, ret_1=0.02, ret_5=0.04, ret_20=0.05)

        guestless = client.get("/api/watchlist")
        assert guestless.status_code == 200
        assert guestless.json()["total"] == 0
        assert guestless.json()["suggestion_total"] >= 1
        assert guestless.json()["suggestions"][0]["code"] == "600000.sh"
        assert guestless.json()["suggestions"][0]["top_score"] == 65

        added = client.post("/api/watchlist", json={"code": "600000.sh", "name": "测试股份"})
        assert added.status_code == 200, added.text
        assert added.json()["total"] == 1
        assert all(item["code"] != "600000.sh" for item in added.json()["suggestions"])
        assert added.json()["items"][0]["rumor_count"] == 1
        assert added.json()["items"][0]["alert_level"] == "active"
        assert added.json()["items"][0]["latest_signal"]["outcome"]["state"] == "valid"
        assert added.json()["items"][0]["tier_mix"]["C"] == 1
        digest = added.json()["digest"]
        assert digest["headline"]
        assert digest["state"] == "active"
        assert digest["rumor_hits"] == 1
        assert digest["active_count"] == 1
        assert digest["top_item"]["code"] == "600000.sh"
        assert digest["action"]["key"] == "open_active"
        assert added.json()["view_privilege"]["state"] == "locked"
        assert digest["rights_fingerprint"]

        watched_feed = client.get("/api/rumors?watch=1&limit=5").json()["items"]
        assert len(watched_feed) == 1
        assert watched_feed[0]["watched"] is True
        assert watched_feed[0]["watch_hits"][0]["code"] == "600000.sh"

        growth = client.get("/api/growth-center").json()
        missions = {item["key"]: item for item in growth["missions"]}
        assert missions["watch"]["completed"] is True

        removed = client.delete("/api/watchlist/600000.sh")
        assert removed.status_code == 200
        assert removed.json()["total"] == 0


def test_guest_must_register_before_watchlist(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        res = client.post("/api/watchlist", json={"code": "600000.sh", "name": "测试股份"})
        assert res.status_code == 401


def test_high_level_user_can_view_limited_watchlist_locked_rumors(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        user = register(client, "watchprime", "watchprime@example.com")
        main.execute("update users set xp = 220, reputation = 99, invite_count = 10 where id = ?", (user["id"],))
        locked_id = seed_rumor(tier="S", score=94, target="自选高值", code="600000.sh")
        other_id = seed_rumor(tier="S", score=93, target="旁路高值", code="000001.sz")
        added = client.post("/api/watchlist", json={"code": "600000.sh", "name": "自选高值"})
        assert added.status_code == 200
        assert added.json()["view_privilege"]["state"] == "core"
        assert added.json()["view_privilege"]["limit"] >= 8

        items = client.get("/api/rumors?tier=S&limit=5").json()["items"]
        watched = next(item for item in items if item["id"] == locked_id)
        other = next(item for item in items if item["id"] == other_id)
        assert watched["unlocked"] is True
        assert watched["raw_content"]
        assert other["unlocked"] is False


def test_direct_unlock_consumes_quota_for_locked_rumor(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        user = register(client, "unlocker", "unlocker@example.com")
        seed_rumor(tier="S", score=90)

        listed = client.get("/api/rumors?tier=S&limit=1").json()["items"][0]
        assert listed["unlocked"] is False
        assert user["direct_quota"] == 10

        res = client.post(f"/api/rumors/{listed['id']}/unlock")
        assert res.status_code == 200, res.text
        assert res.json()["quota_left"] == 9
        assert client.get("/api/me").json()["user"]["direct_quota"] == 9


def test_backtest_outcome_is_exposed_on_feed_detail_and_backtest_list(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        register(client, "outcomereader", "outcomereader@example.com")
        rumor_id = seed_rumor(tier="C", score=72)
        seed_backtest(rumor_id, signal_value=88, ret_1=0.04, ret_5=0.09, ret_20=0.14)

        feed_item = client.get("/api/rumors?tier=C&limit=1").json()["items"][0]
        assert feed_item["outcome"]["state"] == "hit"
        assert feed_item["outcome"]["label"] == "强命中"
        scores = {item["key"]: item for item in feed_item["dimension_scores"]["items"]}
        assert scores["logic"]["score"] >= 1
        assert scores["backtest"]["score"] == 88
        assert scores["observer"]["score"] >= 0

        detail = client.get(f"/api/rumors/{rumor_id}").json()
        assert detail["backtest"]["outcome"]["state"] == "hit"

        backtest_rows = client.get("/api/backtests").json()["items"]
        assert backtest_rows[0]["outcome"]["state"] == "hit"


def test_backtest_signal_value_penalizes_drawdown_in_percentile(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        low_dd = seed_rumor(tier="C", score=70, target="低回撤", code="600000.sh")
        high_dd = seed_rumor(tier="C", score=70, target="高回撤", code="000001.sz")
        seed_backtest(low_dd, signal_value=None, ret_1=0.03, ret_5=0.08, ret_20=0.12, drawdown=-0.02)
        seed_backtest(high_dd, signal_value=None, ret_1=0.03, ret_5=0.08, ret_20=0.12, drawdown=-0.20)

        main._calc_signal_values()
        rows = {
            row["rumor_id"]: row["signal_value"]
            for row in main.query("select rumor_id, signal_value from backtests where rumor_id in (?, ?)", (low_dd, high_dd))
        }
        assert rows[low_dd] > rows[high_dd]


def test_members_can_discuss_and_react_to_free_rumor(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        user = register(client, "gamma", "gamma@example.com")
        rumor_id = seed_rumor(tier="C", score=45)

        reaction = client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})
        assert reaction.status_code == 200, reaction.text
        assert reaction.json()["discussion"]["useful"] == 1
        assert reaction.json()["discussion"]["my_reactions"] == ["useful"]
        assert reaction.json()["participation_reward"]["xp_delta"] == 2

        comment = client.post(f"/api/rumors/{rumor_id}/comments", json={"content": "已看到渠道验证，仍需跟踪订单兑现。"})
        assert comment.status_code == 200, comment.text
        assert comment.json()["discussion"]["comments"] == 1
        assert comment.json()["items"][0]["display_name"] == user["display_name"]
        assert comment.json()["participation_reward"]["xp_delta"] == 4

        detail = client.get(f"/api/rumors/{rumor_id}")
        assert detail.status_code == 200
        assert detail.json()["comments"][0]["content"] == "已看到渠道验证，仍需跟踪订单兑现。"
        me = client.get("/api/me").json()
        assert me["user"]["participation_xp"] == 6
        assert me["user"]["xp"] == user["xp"] + 6

        repeated = client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})
        assert repeated.json()["participation_reward"]["xp_delta"] == 0


def test_guest_must_register_before_discussion(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        rumor_id = seed_rumor(tier="C", score=45)
        reaction = client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "doubt"})
        comment = client.post(f"/api/rumors/{rumor_id}/comments", json={"content": "想补充验证"})

        assert reaction.status_code == 401
        assert comment.status_code == 401


def test_report_rumor_updates_moderation_and_deduplicates_reason(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "reportauthor", "reportauthor@example.com")
        rumor_id = seed_rumor(submitter_id=author["id"], tier="C", score=45)
        register(client, "reporter", "reporter@example.com")

        first = client.post(f"/api/rumors/{rumor_id}/reports", json={"reason": "false_info", "details": "渠道口径不一致"})
        second = client.post(f"/api/rumors/{rumor_id}/reports", json={"reason": "false_info", "details": "补充说明"})

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        moderation = second.json()["moderation"]
        assert moderation["reports"] == 1
        assert moderation["my_reports"] == ["false_info"]
        assert moderation["trust_state"] == "watch"

        summary = client.get("/api/moderation-summary").json()
        assert summary["total_rumors"] == 1
        assert summary["flagged_total"] == 1
        assert summary["clean_total"] == 0
        assert summary["health_score"] == 0.0
        assert summary["status"] == "watch"
        assert summary["policy"]
        guardrails = {item["key"]: item for item in summary["guardrails"]}
        assert {"score_penalty", "dedupe", "review_queue", "source_impact"} == set(guardrails)
        assert guardrails["score_penalty"]["state"] == "active"
        assert guardrails["dedupe"]["value"] == "1次"
        rights = summary["rights_protection"]
        assert rights["license"] == "CC-BY-NC-SA-4.0"
        assert rights["mark"] == main.hidden_copyright_mark()
        assert {item["key"] for item in rights["layers"]} == {"headers", "copy", "fingerprint", "forensic"}
        assert summary["items"][0]["moderation"]["trust_state"] == "watch"

        detail_res = client.get(f"/api/rumors/{rumor_id}").json()
        detail_envelope = detail_res["rights_envelope"]
        assert detail_envelope["scope"] == "rumor-detail"
        assert len(detail_envelope["fingerprint"]) == 32
        detail = detail_res["item"]
        assert detail["moderation"]["reports"] == 1


def test_reported_rumors_are_demoted_and_source_score_penalized(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "badsource", "badsource@example.com")
        flagged_id = seed_rumor(submitter_id=author["id"], tier="C", score=95, target="被举报股份", code="000002.sz")
        trusted_id = seed_rumor(tier="C", score=90, target="可信股份", code="000003.sz")

        register(client, "reporter1", "reporter1@example.com")
        client.post(f"/api/rumors/{flagged_id}/reports", json={"reason": "false_info"})
        register(client, "reporter2", "reporter2@example.com")
        client.post(f"/api/rumors/{flagged_id}/reports", json={"reason": "promotion"})

        feed = client.get("/api/rumors?tier=C&limit=5").json()["items"]
        assert feed[0]["id"] == trusted_id
        flagged = next(item for item in feed if item["id"] == flagged_id)
        assert flagged["effective_score"] < flagged["ai_score"]
        assert flagged["moderation"]["trust_state"] == "watch"

        profile = client.get(f"/api/providers/{author['id']}").json()
        assert profile["feedback_score"] < 0


def test_guest_must_register_before_reporting(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        rumor_id = seed_rumor(tier="C", score=45)
        res = client.post(f"/api/rumors/{rumor_id}/reports", json={"reason": "promotion"})
        assert res.status_code == 401


def test_provider_feedback_affects_leaderboard_grade(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "author", "author@example.com")
        rumor_id = seed_rumor(submitter_id=author["id"], tier="C", score=45)
        register(client, "reader", "reader@example.com")

        res = client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})
        assert res.status_code == 200

        rows = client.get("/api/leaderboard").json()["items"]
        author_row = next(row for row in rows if row["display_name"] == "author")
        assert author_row["feedback_score"] == 1.5
        assert author_row["provider_grade"]["score"] > 0


def test_leaderboard_orders_by_provider_source_score(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        high_xp = register(client, "highxp", "highxp@example.com")
        quality = register(client, "qualitysrc", "qualitysrc@example.com")
        main.execute("update users set xp = 300 where id = ?", (high_xp["id"],))
        rumor_id = seed_rumor(submitter_id=quality["id"], tier="S", score=96)
        seed_backtest(rumor_id, signal_value=90, ret_1=0.04, ret_5=0.08, ret_20=0.12)
        register(client, "leaderreader", "leaderreader@example.com")
        client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})

        data = client.get("/api/leaderboard").json()
        rows = data["items"]
        quality_row = next(row for row in rows if row["display_name"] == "qualitysrc")
        high_xp_row = next(row for row in rows if row["display_name"] == "highxp")
        assert data["rank_rule"].startswith("provider_grade.score")
        assert quality_row["provider_grade"]["score"] > high_xp_row["provider_grade"]["score"]
        assert quality_row["source_rank"] < high_xp_row["source_rank"]


def test_feed_exposes_provider_quality_snapshot(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "feedsource", "feedsource@example.com")
        rumor_id = seed_rumor(submitter_id=author["id"], tier="A", score=78)
        seed_backtest(rumor_id, signal_value=80, ret_1=0.01, ret_5=0.04, ret_20=0.07)
        register(client, "feedreader", "feedreader@example.com")
        client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})

        feed = client.get("/api/rumors?limit=5").json()["items"]
        item = next(row for row in feed if row["id"] == rumor_id)
        provider = item["provider"]
        assert provider["id"] == author["id"]
        assert provider["display_name"] == "feedsource"
        assert provider["grade"]["score"] > 0
        assert provider["feedback_score"] >= 1.5
        assert provider["rumor_count"] == 1
        assert provider["top_score"] == 78
        verdict = item["value_verdict"]
        assert verdict["index"] > 50
        assert verdict["label"] in {"强价值", "值得跟踪", "可观察"}
        assert any("回测" in driver or "社区正反馈" in driver for driver in verdict["drivers"])
        ledger = {entry["key"]: entry for entry in item["verification_ledger"]}
        assert set(ledger) == {"content", "source", "community", "backtest", "trust", "risk", "verdict"}
        assert ledger["source"]["value"] == provider["grade"]["score"]
        assert ledger["community"]["value"] > 0
        assert ledger["backtest"]["state"] in {"strong", "watch"}
        assert ledger["verdict"]["value"] == verdict["index"]
        explain = item["score_explanation"]
        assert explain["headline"]
        assert "内容分" in explain["summary"]
        assert explain["factors"]
        assert {"content", "source", "community", "backtest", "risk"} == {factor["key"] for factor in explain["factors"]}
        assert explain["next_steps"]
        consensus = item["consensus_snapshot"]
        assert consensus["state"] in {"confirmed", "forming", "divergent", "isolated"}
        assert consensus["label"]
        assert {"related", "sources", "high_value", "risk"} <= set(consensus["metrics"])
        assert consensus["next_action"]
        bounties = item["verification_bounties"]
        assert bounties
        assert {"key", "label", "reward_xp", "reputation_delta", "action", "state", "detail"} <= set(bounties[0])
        assert bounties[0]["reward_xp"] >= 0
        rights = item["rights"]
        assert rights["scope"] == "rumor-content"
        assert rights["license"] == "CC-BY-NC-SA-4.0"
        assert rights["mark"].startswith("StockWhisper::")
        assert len(rights["fingerprint"]) == 20
        forensic = rights["forensic_signature"]
        assert forensic["algorithm"] == "blake2s-128+sha256-content"
        assert len(forensic["signature"]) == 32
        assert len(forensic["content_hash"]) == 64
        assert forensic["payload"].startswith(f"SW:{rumor_id}:")
        assert "\u200b" in forensic["zero_width"] or "\u200c" in forensic["zero_width"]
        again = next(row for row in client.get("/api/rumors?limit=5").json()["items"] if row["id"] == rumor_id)
        assert again["rights"]["fingerprint"] == rights["fingerprint"]
        assert again["rights"]["forensic_signature"]["signature"] == forensic["signature"]


def test_source_upgrade_center_exposes_rank_privileges_and_missions(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "upgrade", "upgrade@example.com")
        rumor_id = seed_rumor(submitter_id=author["id"], tier="A", score=78)
        seed_backtest(rumor_id, signal_value=84, ret_1=0.02, ret_5=0.05, ret_20=0.09)
        register(client, "upgrade_reader", "upgrade_reader@example.com")
        client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})
        client.post("/api/login", json={"username": "upgrade", "password": "secret12"})

        res = client.get("/api/source-upgrade-center")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["registered"] is True
        assert data["grade"]["score"] > 0
        passport = data["credibility_passport"]
        assert passport["state"] in {"prime", "track", "watch", "new"}
        assert passport["label"]
        assert {item["key"] for item in passport["metrics"]} == {"grade", "samples", "hit_rate", "feedback", "risk"}
        assert passport["next_action"]["view"] in {"submit", "feed", "rank"}
        assert data["upgrade"]["components"][0]["progress"] >= 0
        assert data["upgrade"]["priority_actions"]
        assert data["upgrade"]["priority_actions"][0]["rank"] == 1
        assert {item["key"] for item in data["upgrade"]["priority_actions"]} & {"submit", "discussion", "invite", "track_record"}
        assert all(item["view"] in {"submit", "feed", "rank"} for item in data["upgrade"]["priority_actions"])
        roadmap = {item["key"]: item for item in data["upgrade"]["roadmap"]}
        assert {"xp", "contribution", "reputation", "feedback", "invite"} == set(roadmap)
        assert all({"label", "value", "target", "gap", "progress", "detail", "view", "state"} <= set(item) for item in roadmap.values())
        assert all(item["view"] in {"submit", "feed", "rank"} for item in roadmap.values())
        assert roadmap["contribution"]["target"] > 0
        assert data["rank_context"]["rank"] == 1
        assert data["rank_context"]["total"] >= 2
        assert data["privileges"][0]["unlocked"] is True
        assert any(item["threshold"] == 62 for item in data["privileges"])
        assert data["recent_performance"]["rumor_count"] == 1
        assert data["recent_performance"]["tier_mix"]["A"] == 1
        assert data["recent_performance"]["outcomes"]["hit"] == 1
        missions = {item["key"]: item for item in data["missions"]}
        assert missions["first_submit"]["completed"] is True
        assert missions["high_value_submit"]["upgrade_hint"]


def test_provider_profile_exposes_track_record_and_recent_rumors(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "profile", "profile@example.com")
        rumor_id = seed_rumor(submitter_id=author["id"], tier="A", score=78)
        seed_backtest(rumor_id, signal_value=82, ret_1=0.02, ret_5=0.05, ret_20=0.08)
        register(client, "reader2", "reader2@example.com")
        client.post(f"/api/rumors/{rumor_id}/reactions", json={"reaction": "useful"})

        res = client.get(f"/api/providers/{author['id']}")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["display_name"] == "profile"
        assert data["provider_grade"]["name"]
        passport = data["credibility_passport"]
        assert passport["summary"]
        assert passport["metrics"][0]["key"] == "grade"
        assert passport["follow_hint"]
        assert data["participation_xp"] >= 0
        assert data["tier_mix"]["A"]["count"] == 1
        assert data["feedback"]["useful"] == 1
        assert "track_record" in data
        assert data["track_record"]["outcomes"]["hit"] == 1
        assert data["track_record"]["hit_rate"] == 100.0
        proof = {item["key"]: item for item in data["provider_proof"]}
        assert set(proof) == {"source_grade", "track_record", "community_feedback", "risk_control", "contribution"}
        assert proof["track_record"]["state"] == "strong"
        assert proof["community_feedback"]["value"] >= 1
        assert proof["risk_control"]["state"] == "strong"
        assert data["feedback_score"] > 1.5
        assert data["recent"][0]["ai_tier"] == "A"


def test_follow_provider_creates_personal_source_feed(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "source", "source@example.com")
        seed_rumor(submitter_id=author["id"], tier="A", score=78)
        suggested = register(client, "suggested", "suggested@example.com")
        seed_rumor(submitter_id=suggested["id"], tier="S", score=92)
        register(client, "follower", "follower@example.com")

        before = client.get("/api/provider-follows").json()
        suggestion_names = {item["display_name"] for item in before["suggestions"]}
        assert "source" in suggestion_names
        assert "suggested" in suggestion_names
        assert before["suggestion_total"] >= 2
        board = before["source_board"]
        assert board["headline"]
        assert board["suggested_count"] >= 2
        assert board["top_source"]["display_name"] in {"source", "suggested"}
        assert board["top_source"]["score"] > 0
        assert board["upgrade_action"]["view"] == "rank"

        followed = client.post(f"/api/providers/{author['id']}/follow")
        assert followed.status_code == 200, followed.text
        assert followed.json()["provider"]["followed"] is True
        assert followed.json()["following"]["total"] == 1
        assert all(item["id"] != author["id"] for item in followed.json()["following"]["suggestions"])

        feed = client.get("/api/rumors?followed=1&limit=5").json()["items"]
        assert len(feed) == 1
        assert feed[0]["ai_tier"] == "A"

        missions = {item["key"]: item for item in client.get("/api/growth-center").json()["missions"]}
        assert missions["follow_provider"]["completed"] is True

        removed = client.delete(f"/api/providers/{author['id']}/follow")
        assert removed.status_code == 200
        assert removed.json()["following"]["total"] == 0


def test_activity_feed_combines_watchlist_and_followed_source_signals(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "activitysrc", "activitysrc@example.com")
        seed_rumor(submitter_id=author["id"], tier="B", score=66, target="来源股份", code="000001.sz")
        seed_rumor(tier="A", score=82, target="测试股份", code="600000.sh")
        register(client, "activityreader", "activityreader@example.com")

        client.post("/api/watchlist", json={"code": "600000.sh", "name": "测试股份"})
        client.post(f"/api/providers/{author['id']}/follow")

        res = client.get("/api/activity-feed?limit=10")
        assert res.status_code == 200, res.text
        data = res.json()
        kinds = {item["kind"] for item in data["items"]}
        assert data["personalized"] is True
        assert "watch_hit" in kinds
        assert "followed_source" in kinds
        assert data["watch_hits"] >= 1
        assert data["followed_hits"] >= 1


def test_guest_activity_feed_uses_community_samples(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        seed_rumor(tier="S", score=91)

        res = client.get("/api/activity-feed")
        assert res.status_code == 200
        data = res.json()
        assert data["personalized"] is False
        assert data["items"][0]["kind"] == "community_pick"
        assert data["items"][0]["rumor"]["ai_tier"] == "S"


def test_guest_must_register_before_follow_provider(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as client:
        author = register(client, "source2", "source2@example.com")
        client.post("/api/logout")
        res = client.post(f"/api/providers/{author['id']}/follow")
        assert res.status_code == 401
