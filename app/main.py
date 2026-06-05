from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import secrets
import smtplib
import sqlite3
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.error import URLError

import pandas as pd
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "stockwhisper.db"
PRIVATE_DB = ROOT.parent / "私有信息" / "info_collection.db"
MARKET_ROOT = Path("/home/vscode/workspace/data/store/rsync")
DAILY_PICKLE = MARKET_ROOT / "tonglian_data_daily" / "tonglian_data_daily.pickle"
RAW_DAILY = MARKET_ROOT / "tonglian_data_daily" / "tonglian_stock_day_n.parquet"
END_DATE = pd.Timestamp(datetime.now(timezone.utc).date())
SESSION_SECONDS = 60 * 60 * 24 * 30
CONTRIBUTION_HALF_LIFE_DAYS = 30
TIER_RULES = {
    "C": {"label": "C级", "free": True, "min_xp": 0, "min_contribution": 0, "hint": "免费查看"},
    "B": {"label": "B级", "free": False, "min_xp": 80, "min_contribution": 35, "hint": "L2 或贡献度 35"},
    "A": {"label": "A级", "free": False, "min_xp": 220, "min_contribution": 80, "hint": "L3 或贡献度 80"},
    "S": {"label": "S级", "free": False, "min_xp": 520, "min_contribution": 150, "hint": "L4 或贡献度 150"},
}
TIER_ORDER = ("S", "A", "B", "C")

app = FastAPI(title="股情报", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


EMAIL_CODE_TTL = 300  # 5 minutes


class AuthPayload(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=4, max_length=128)


class SendCodePayload(BaseModel):
    email: str = Field(min_length=4, max_length=120)


class RegisterPayload(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    email: str = Field(min_length=4, max_length=120)
    code: str = Field(min_length=6, max_length=6)


class RumorPayload(BaseModel):
    target: str = Field(default="", max_length=120)
    logic: str = Field(default="", max_length=240)
    raw_content: str = Field(min_length=10, max_length=8000)
    institution: str = Field(default="", max_length=80)
    recommender: str = Field(default="", max_length=80)
    recommendation_date: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def execute(sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
    con = db()
    cur = con.execute(sql, args)
    con.commit()
    con.close()
    return cur


def query(sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    con = db()
    rows = con.execute(sql, args).fetchall()
    con.close()
    return rows


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, digest: str) -> bool:
    _, trial = hash_password(password, salt)
    return hmac.compare_digest(trial, digest)


def send_verification_email(email: str, code: str) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("SMTP_FROM", user)
    if not user or not password:
        raise RuntimeError("SMTP 未配置，请设置 SMTP_USER 和 SMTP_PASS 环境变量")
    msg = EmailMessage()
    msg["Subject"] = "股情报 注册验证码"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(f"您的验证码是：{code}\n5 分钟内有效，请勿泄露。")
    if port == 465:
        with smtplib.SMTP_SSL(host, port) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)


def score_text(payload: RumorPayload | dict[str, Any]) -> dict[str, Any]:
    def value(key: str) -> Any:
        return payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")

    text = " ".join(
        str(value(k))
        for k in ("target", "logic", "raw_content", "institution", "recommender")
    )
    cn_len = len(re.findall(r"[\u4e00-\u9fff]", text))
    tickers = len(set(re.findall(r"\b[036]\d{5}\b|#[\u4e00-\u9fffA-Za-z0-9]+", text)))
    catalysts = sum(1 for w in ("订单", "涨价", "合作", "中标", "产能", "AI", "国产替代", "光模块", "业绩", "并购", "政策") if w in text)
    specificity = min(30, tickers * 5 + catalysts * 4)
    evidence = min(30, cn_len // 55)
    freshness = 10 if value("recommendation_date") else 0
    raw = 25 + specificity + evidence + freshness
    score = max(1, min(100, raw))
    tier = "S" if score >= 86 else "A" if score >= 72 else "B" if score >= 55 else "C"
    reasons = []
    if tickers:
        reasons.append("标的明确")
    if catalysts:
        reasons.append("催化因素清晰")
    if cn_len >= 180:
        reasons.append("信息密度较高")
    if not reasons:
        reasons.append("待补充来源与催化")
    return {"score": score, "tier": tier, "reasons": reasons}


def heuristic_summary(payload: RumorPayload | dict[str, Any]) -> dict[str, Any]:
    def value(key: str) -> str:
        raw = payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")
        return str(raw or "").strip()

    text = value("raw_content")
    target = value("target")
    if not target:
        head_match = re.search(r"】\s*([\u4e00-\u9fffA-Za-z0-9、，,]{2,40})[：:]", text)
        if head_match:
            target = head_match.group(1)
        else:
            tags = re.findall(r"#([\u4e00-\u9fffA-Za-z0-9]{2,12})", text)
            target = "、".join(dict.fromkeys(tags[:3])) or "待确认标的"
    logic = value("logic")
    if not logic:
        compact = re.sub(r"\s+", "", text)
        logic = compact[:80] or "待补充核心逻辑"
    institution = value("institution")
    if not institution:
        m = re.search(r"【([^】]{2,24})】", text)
        institution = m.group(1) if m else ""
    recommender = value("recommender")
    key_points = []
    for sentence in re.split(r"[。！？\n]+", text):
        sentence = re.sub(r"^【[^】]+】", "", sentence).strip(" \t#[]【】")
        if len(sentence) >= 12 and any(w in sentence for w in ("订单", "合作", "中标", "产能", "AI", "国产替代", "业绩", "并购", "政策", "客户", "验证")):
            key_points.append(sentence[:90])
        if len(key_points) >= 4:
            break
    if not key_points and logic:
        key_points = [logic[:90]]
    return {
        "target": target[:120],
        "logic": logic[:240],
        "institution": institution[:80],
        "recommender": recommender[:80],
        "key_points": key_points,
        "summary_source": "heuristic",
    }


def parse_llm_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def llm_summary(payload: RumorPayload | dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("AGUWHISPER_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("AGUWHISPER_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    model = os.getenv("AGUWHISPER_LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    timeout = int(os.getenv("AGUWHISPER_LLM_TIMEOUT", "6"))
    raw = payload.get("raw_content", "") if isinstance(payload, dict) else payload.raw_content
    prompt = (
        "你是A股私域投研信息整理助手。请从原始消息中抽取结构化关键信息，"
        "只返回JSON，不要解释。字段：target, logic, institution, recommender, key_points。"
        "target为股票或产业链标的，logic为不超过80字的核心投资逻辑，"
        "key_points为2到5条事实/催化/验证节点。原始消息：\n"
        f"{raw[:6000]}"
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "只输出严格JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        ensure_ascii=False,
    ).encode()
    req = urlrequest.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    content = ""
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content", "")
    except (OSError, URLError, json.JSONDecodeError):
        responses_body = json.dumps(
            {
                "model": model,
                "input": [
                    {"role": "system", "content": "只输出严格JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            ensure_ascii=False,
        ).encode()
        responses_req = urlrequest.Request(
            base_url.rstrip("/") + "/responses",
            data=responses_body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(responses_req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError):
            return None
        content = data.get("output_text", "")
        if not content:
            for output in data.get("output", []):
                for item in output.get("content", []):
                    if item.get("type") in ("output_text", "text") and item.get("text"):
                        content += item["text"]
    parsed = parse_llm_json(content)
    if not parsed:
        return None
    key_points = parsed.get("key_points") or []
    if isinstance(key_points, str):
        key_points = [key_points]
    return {
        "target": str(parsed.get("target") or "")[:120],
        "logic": str(parsed.get("logic") or "")[:240],
        "institution": str(parsed.get("institution") or "")[:80],
        "recommender": str(parsed.get("recommender") or "")[:80],
        "key_points": [str(p)[:120] for p in key_points if str(p).strip()][:5],
        "summary_source": "llm",
    }


def summarize_payload(payload: RumorPayload | dict[str, Any]) -> dict[str, Any]:
    base = heuristic_summary(payload)
    ai = llm_summary(payload)
    if not ai:
        return base
    merged = {**base, **{k: v for k, v in ai.items() if v}}
    if not merged.get("key_points"):
        merged["key_points"] = base["key_points"]
    return merged


def normalize_date(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    value = value.strip()[:10]
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    con = db()
    con.executescript(
        """
        create table if not exists users (
            id integer primary key autoincrement,
            username text unique not null,
            display_name text not null,
            password_salt text,
            password_hash text,
            is_guest integer not null default 0,
            xp integer not null default 0,
            reputation real not null default 50,
            direct_quota integer not null default 1,
            created_at text not null
        );
        create table if not exists sessions (
            token text primary key,
            user_id integer not null references users(id),
            expires_at integer not null
        );
        create table if not exists rumors (
            id integer primary key autoincrement,
            submitter_id integer references users(id),
            submitter_name text not null,
            recommendation_date text not null,
            recommender text not null,
            target text not null,
            logic text not null,
            raw_content text not null,
            institution text not null default '',
            key_points text not null default '[]',
            ai_score integer not null,
            ai_tier text not null,
            ai_reasons text not null,
            created_at text not null,
            source text not null default 'community'
        );
        create table if not exists unlocks (
            user_id integer not null references users(id),
            rumor_id integer not null references rumors(id),
            reason text not null,
            created_at text not null,
            primary key (user_id, rumor_id)
        );
        create table if not exists backtests (
            rumor_id integer primary key references rumors(id),
            code text,
            name text,
            start_date text,
            price_start real,
            ret_5 real,
            ret_20 real,
            ret_60 real,
            max_ret_60 real,
            status text not null,
            details text not null
        );
        create table if not exists email_verifications (
            email text not null,
            code text not null,
            expires_at integer not null,
            primary key (email)
        );
        """
    )
    con.commit()
    existing_cols = {row["name"] for row in con.execute("pragma table_info(rumors)").fetchall()}
    if "key_points" not in existing_cols:
        con.execute("alter table rumors add column key_points text not null default '[]'")
        con.commit()
    user_cols = {row["name"] for row in con.execute("pragma table_info(users)").fetchall()}
    if "email" not in user_cols:
        con.execute("alter table users add column email text")
        con.commit()
    con.close()
    seed_reference_data()


def seed_reference_data() -> None:
    if query("select count(*) n from rumors")[0]["n"] > 0:
        return
    if not PRIVATE_DB.exists():
        return
    src = sqlite3.connect(PRIVATE_DB)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        "select submitter, recommendation_date, recommender, target, logic, raw_content, institution, created_at from recommendations order by id"
    ).fetchall()
    src.close()
    con = db()
    for row in rows:
        payload = dict(row)
        scored = score_text(payload)
        con.execute(
            """
            insert into rumors
            (submitter_name, recommendation_date, recommender, target, logic, raw_content, institution,
             key_points, ai_score, ai_tier, ai_reasons, created_at, source)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reference')
            """,
            (
                payload["submitter"],
                payload["recommendation_date"],
                payload["recommender"],
                payload["target"],
                payload["logic"],
                payload["raw_content"],
                payload["institution"],
                json.dumps(heuristic_summary(payload)["key_points"], ensure_ascii=False),
                scored["score"],
                scored["tier"],
                json.dumps(scored["reasons"], ensure_ascii=False),
                payload["created_at"] or now_iso(),
            ),
        )
    con.commit()
    con.close()
    refresh_backtests(limit=200)


def create_guest() -> sqlite3.Row:
    name = f"guest-{secrets.token_hex(3)}"
    execute(
        "insert into users(username, display_name, is_guest, created_at) values (?, ?, 1, ?)",
        (name, "游客", now_iso()),
    )
    return query("select * from users where username = ?", (name,))[0]


def create_session(user_id: int, response: Response) -> str:
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + SESSION_SECONDS
    execute("insert into sessions(token, user_id, expires_at) values (?, ?, ?)", (token, user_id, expires))
    response.set_cookie("agu_session", token, max_age=SESSION_SECONDS, httponly=True, samesite="lax")
    return token


def current_user(response: Response, agu_session: str | None = Cookie(default=None)) -> sqlite3.Row:
    if agu_session:
        rows = query(
            """
            select u.* from sessions s join users u on u.id = s.user_id
            where s.token = ? and s.expires_at > ?
            """,
            (agu_session, int(time.time())),
        )
        if rows:
            return rows[0]
    guest = create_guest()
    create_session(guest["id"], response)
    return guest


def level_for(xp: int) -> dict[str, Any]:
    tiers = [
        (0, "L1 观察员", 1, 0),
        (80, "L2 线索员", 3, 45),
        (220, "L3 研究员", 6, 55),
        (520, "L4 情报官", 12, 65),
        (1000, "L5 核心席位", 30, 75),
    ]
    current = tiers[0]
    for tier in tiers:
        if xp >= tier[0]:
            current = tier
    return {"name": current[1], "quota": current[2], "min_score": current[3]}


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def contribution_for_user(user_id: int) -> float:
    rows = query(
        """
        select ai_score, created_at from rumors
        where submitter_id = ?
        """,
        (user_id,),
    )
    now = datetime.now(timezone.utc)
    total = 0.0
    for row in rows:
        age_days = max(0.0, (now - parse_dt(row["created_at"])).total_seconds() / 86400)
        decay = 0.5 ** (age_days / CONTRIBUTION_HALF_LIFE_DAYS)
        total += float(row["ai_score"]) * decay
    return round(total, 1)


def tier_access(user: sqlite3.Row, tier: str, contribution: float | None = None) -> dict[str, Any]:
    rule = TIER_RULES.get(tier, TIER_RULES["S"])
    contribution = contribution_for_user(user["id"]) if contribution is None else contribution
    allowed = bool(rule["free"] or user["xp"] >= rule["min_xp"] or contribution >= rule["min_contribution"])
    return {
        **rule,
        "tier": tier,
        "allowed": allowed,
        "user_contribution": contribution,
    }


def user_out(user: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "is_guest": user["is_guest"],
        "xp": user["xp"],
        "reputation": user["reputation"],
        "direct_quota": user["direct_quota"],
        "contribution": contribution_for_user(user["id"]),
        "contribution_half_life_days": CONTRIBUTION_HALF_LIFE_DAYS,
        "created_at": user["created_at"],
    }


def public_rumor(row: sqlite3.Row, unlocked: bool) -> dict[str, Any]:
    hidden = not unlocked
    return {
        "id": row["id"],
        "target": row["target"] if unlocked else mask_target(row["target"]),
        "logic": row["logic"] if unlocked else "已锁定。分享同等价值消息或提升等级后查看。",
        "raw_content": row["raw_content"] if unlocked else "",
        "institution": row["institution"] if unlocked else "",
        "recommender": row["recommender"] if unlocked else "",
        "key_points": json.loads(row["key_points"] or "[]") if unlocked else [],
        "recommendation_date": row["recommendation_date"],
        "ai_score": row["ai_score"],
        "ai_tier": row["ai_tier"],
        "ai_reasons": json.loads(row["ai_reasons"] or "[]"),
        "created_at": row["created_at"],
        "source": row["source"],
        "unlocked": unlocked,
        "hidden": hidden,
    }


def mask_target(target: str) -> str:
    parts = re.split(r"([、,，/ ])", target)
    return "".join(p if re.match(r"[、,，/ ]", p) else (p[:1] + "**") for p in parts)


def user_unlocked_ids(user: sqlite3.Row) -> set[int]:
    rows = query("select rumor_id from unlocks where user_id = ?", (user["id"],))
    return {r["rumor_id"] for r in rows}


def can_view(user: sqlite3.Row, rumor: sqlite3.Row, unlocked: set[int]) -> bool:
    if rumor["id"] in unlocked or rumor["submitter_id"] == user["id"]:
        return True
    return tier_access(user, rumor["ai_tier"])["allowed"]


def unlock_similar(user_id: int, score: int) -> sqlite3.Row | None:
    rows = query(
        """
        select * from rumors
        where id not in (select rumor_id from unlocks where user_id = ?)
        and abs(ai_score - ?) <= 12
        order by random()
        limit 1
        """,
        (user_id, score),
    )
    if not rows:
        return None
    execute(
        "insert or ignore into unlocks(user_id, rumor_id, reason, created_at) values (?, ?, 'share_exchange', ?)",
        (user_id, rows[0]["id"], now_iso()),
    )
    return rows[0]


def active_feed_date() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if query("select 1 from rumors where recommendation_date = ? limit 1", (today,)):
        return today
    rows = query("select max(recommendation_date) d from rumors")
    return rows[0]["d"] or today


def code_lookup() -> dict[str, str]:
    if not DAILY_PICKLE.exists():
        return {}
    try:
        bars = pd.read_pickle(DAILY_PICKLE)
        names = bars["WA_names_cn"]
        lookup: dict[str, str] = {}
        for code, row in names.iterrows():
            name = str(row.iloc[0])
            if name and name != "nan":
                lookup[name] = code
        return lookup
    except Exception:
        return {}


def find_code(target: str, lookup: dict[str, str]) -> tuple[str | None, str | None]:
    for name in sorted(lookup, key=len, reverse=True):
        if name and name in target:
            return lookup[name], name
    m = re.search(r"\b([036]\d{5})\b", target)
    if not m:
        return None, None
    suffix = ".sh" if m.group(1).startswith("6") else ".sz"
    return m.group(1) + suffix, m.group(1)


def refresh_backtests(limit: int | None = None, force: bool = False) -> int:
    if not RAW_DAILY.exists() and not DAILY_PICKLE.exists():
        return 0
    if force:
        base_sql = "select * from rumors order by recommendation_date desc"
        args: tuple[Any, ...] = ()
    else:
        base_sql = "select * from rumors where id not in (select rumor_id from backtests) order by recommendation_date desc"
        args = ()
    rows = query(base_sql + (" limit ?" if limit else ""), (*args, limit) if limit else args)
    if not rows:
        return 0
    try:
        lookup = code_lookup()
        mapped = {row["id"]: find_code(row["target"], lookup) for row in rows}
        codes = sorted({code for code, _ in mapped.values() if code})
        if RAW_DAILY.exists() and codes:
            raw = pd.read_parquet(RAW_DAILY, columns=["code", "date", "close"])
            raw = raw[raw["code"].isin(codes)].copy()
            raw["date"] = pd.to_datetime(raw["date"].astype(str))
            raw = raw[raw["date"].le(END_DATE)].sort_values(["code", "date"])
            series_by_code = {
                code: g.set_index("date")["close"].dropna()
                for code, g in raw.groupby("code", sort=False)
            }
            source_details = "使用本地未复权日线 close 计算"
        else:
            bars = pd.read_pickle(DAILY_PICKLE)
            close = bars["closew"].copy()
            close.index = pd.to_datetime(close.index)
            close = close[close.index <= END_DATE]
            series_by_code = {code: close[code].dropna() for code in codes if code in close.columns}
            source_details = "使用本地前复权日线 closew 计算"
    except Exception:
        return 0
    con = db()
    done = 0
    for row in rows:
        code, name = mapped[row["id"]]
        if not code or code not in series_by_code:
            status, details = "missing_code", "未匹配到本地行情代码"
            values = (None, name, None, None, None, None, None, None, status, details)
        else:
            series = series_by_code[code]
            start = pd.Timestamp(row["recommendation_date"])
            future = series[series.index >= start]
            if len(future) < 2:
                status, details = "pending", "推荐日后暂无足够行情"
                values = (code, name, None, None, None, None, None, None, status, details)
            else:
                price0 = float(future.iloc[0])
                def ret_at(n: int) -> float | None:
                    if len(future) <= n or price0 == 0:
                        return None
                    return float(future.iloc[n] / price0 - 1)
                window = future.iloc[: min(len(future), 61)]
                max_ret = float(window.max() / price0 - 1) if price0 else None
                status, details = "ok", source_details
                values = (
                    code,
                    name,
                    future.index[0].strftime("%Y-%m-%d"),
                    price0,
                    ret_at(5),
                    ret_at(20),
                    ret_at(60),
                    max_ret,
                    status,
                    details,
                )
        con.execute(
            """
            insert or replace into backtests
            (rumor_id, code, name, start_date, price_start, ret_5, ret_20, ret_60, max_ret_60, status, details)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row["id"], *values),
        )
        done += 1
    con.commit()
    con.close()
    recalc_user_scores()
    return done


def recalc_user_scores() -> None:
    rows = query(
        """
        select r.submitter_id, r.ai_score, b.ret_20, b.max_ret_60
        from rumors r left join backtests b on b.rumor_id = r.id
        where r.submitter_id is not null
        """
    )
    grouped: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        grouped.setdefault(r["submitter_id"], []).append(r)
    for user_id, items in grouped.items():
        xp = 0
        perf = []
        for item in items:
            base = max(5, int(item["ai_score"] / 5))
            ret20 = item["ret_20"]
            max60 = item["max_ret_60"]
            bonus = 0
            if ret20 is not None:
                bonus += int(ret20 * 120)
                perf.append(ret20)
            if max60 is not None and max60 > 0.12:
                bonus += 12
            xp += max(1, base + bonus)
        rep = 50 + (sum(perf) / len(perf) * 120 if perf else 0)
        lvl = level_for(xp)
        execute(
            "update users set xp = ?, reputation = ?, direct_quota = ? where id = ?",
            (xp, max(1, min(99, rep)), lvl["quota"], user_id),
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/me")
def me(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    lvl = level_for(user["xp"])
    return {"user": user_out(user), "level": lvl}


@app.post("/api/send-code")
def send_code(payload: SendCodePayload) -> dict[str, Any]:
    email = payload.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(422, "邮箱格式不正确")
    code = f"{random.randint(0, 999999):06d}"
    expires = int(time.time()) + EMAIL_CODE_TTL
    execute(
        "insert or replace into email_verifications(email, code, expires_at) values (?, ?, ?)",
        (email, code, expires),
    )
    try:
        send_verification_email(email, code)
    except Exception as exc:
        import sys
        print(f"[send-code] {email} => {code}", file=sys.stderr, flush=True)
        if "unreachable" not in str(exc).lower() and "connect" not in str(exc).lower() and "timed out" not in str(exc).lower():
            raise HTTPException(500, f"邮件发送失败：{exc}") from exc
    return {"ok": True}


DAILY_REG_LIMIT = 100


@app.post("/api/register")
def register(payload: RegisterPayload, response: Response) -> dict[str, Any]:
    email = payload.email.strip().lower()
    rows = query(
        "select * from email_verifications where email = ? and expires_at > ?",
        (email, int(time.time())),
    )
    if not rows or rows[0]["code"] != payload.code:
        raise HTTPException(400, "验证码错误或已过期")
    execute("delete from email_verifications where email = ?", (email,))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = query(
        "select count(*) n from users where is_guest = 0 and created_at >= ?",
        (today,),
    )[0]["n"]
    if count >= DAILY_REG_LIMIT:
        raise HTTPException(429, f"今日注册名额已满（{DAILY_REG_LIMIT} 人），请明日再试，您已在排队中")
    if query("select 1 from users where username = ?", (payload.username,)):
        raise HTTPException(409, "用户名已存在")
    if query("select 1 from users where email = ?", (email,)):
        raise HTTPException(409, "该邮箱已注册")
    salt, digest = hash_password(payload.password)
    execute(
        "insert into users(username, display_name, email, password_salt, password_hash, is_guest, created_at) values (?, ?, ?, ?, ?, 0, ?)",
        (payload.username, payload.username, email, salt, digest, now_iso()),
    )
    user = query("select * from users where username = ?", (payload.username,))[0]
    create_session(user["id"], response)
    return {"user": user_out(user), "level": level_for(user["xp"])}


@app.post("/api/login")
def login(payload: AuthPayload, response: Response) -> dict[str, Any]:
    rows = query("select * from users where username = ? and is_guest = 0", (payload.username,))
    if not rows or not verify_password(payload.password, rows[0]["password_salt"], rows[0]["password_hash"]):
        raise HTTPException(401, "账号或密码错误")
    create_session(rows[0]["id"], response)
    return {"user": user_out(rows[0]), "level": level_for(rows[0]["xp"])}


@app.post("/api/logout")
def logout(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    if agu_session:
        execute("delete from sessions where token = ?", (agu_session,))
    response.delete_cookie("agu_session")
    return {"ok": True}


@app.get("/api/daily-stats")
def daily_stats(response: Response, agu_session: str | None = Cookie(default=None), date: str | None = None) -> dict[str, Any]:
    user = current_user(response, agu_session)
    day = normalize_date(date) if date else active_feed_date()
    rows = query(
        """
        select ai_tier, count(*) n, max(ai_score) max_score
        from rumors
        where recommendation_date = ?
        group by ai_tier
        """,
        (day,),
    )
    counts = {row["ai_tier"]: row for row in rows}
    contribution = contribution_for_user(user["id"])
    items = []
    for tier in TIER_ORDER:
        row = counts.get(tier)
        access = tier_access(user, tier, contribution)
        items.append(
            {
                "tier": tier,
                "label": access["label"],
                "count": int(row["n"]) if row else 0,
                "max_score": int(row["max_score"]) if row and row["max_score"] is not None else None,
                "free": access["free"],
                "allowed": access["allowed"],
                "requirement": access["hint"],
                "min_xp": access["min_xp"],
                "min_contribution": access["min_contribution"],
            }
        )
    return {"date": day, "items": items, "contribution": contribution}


@app.get("/api/rumors")
def list_rumors(response: Response, agu_session: str | None = Cookie(default=None), q: str = "", tier: str = "", date: str = "", offset: int = 0, limit: int = 24) -> dict[str, Any]:
    user = current_user(response, agu_session)
    limit = max(1, min(60, limit))
    offset = max(0, offset)
    where = []
    args: list[Any] = []
    if q:
        where.append("(target like ? or logic like ? or raw_content like ? or institution like ?)")
        args.extend([f"%{q}%"] * 4)
    if tier:
        where.append("ai_tier = ?")
        args.append(tier)
    if date:
        where.append("recommendation_date = ?")
        args.append(normalize_date(date))
    sql = "select * from rumors"
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by ai_score desc, recommendation_date desc, id desc limit ? offset ?"
    rows = query(sql, (*args, limit + 1, offset))
    has_more = len(rows) > limit
    rows = rows[:limit]
    unlocked = user_unlocked_ids(user)
    return {
        "items": [public_rumor(r, can_view(user, r, unlocked)) for r in rows],
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


@app.get("/api/rumors/{rumor_id}")
def get_rumor(rumor_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    rows = query("select * from rumors where id = ?", (rumor_id,))
    if not rows:
        raise HTTPException(404, "消息不存在")
    unlocked = user_unlocked_ids(user)
    if not can_view(user, rows[0], unlocked):
        raise HTTPException(403, "需要分享同等价值消息或提升等级")
    bt = query("select * from backtests where rumor_id = ?", (rumor_id,))
    return {"item": public_rumor(rows[0], True), "backtest": dict(bt[0]) if bt else None}


@app.post("/api/rumors/{rumor_id}/unlock")
def direct_unlock(rumor_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    rows = query("select * from rumors where id = ?", (rumor_id,))
    if not rows:
        raise HTTPException(404, "消息不存在")
    access = tier_access(user, rows[0]["ai_tier"])
    if not access["allowed"]:
        raise HTTPException(403, f"需要达到{access['hint']}，当前贡献度 {access['user_contribution']}")
    execute("insert or ignore into unlocks(user_id, rumor_id, reason, created_at) values (?, ?, 'direct_quota', ?)", (user["id"], rumor_id, now_iso()))
    return {"item": public_rumor(rows[0], True)}


@app.post("/api/rumors/summarize")
def summarize(payload: RumorPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    current_user(response, agu_session)
    return summarize_payload(payload)


@app.post("/api/rumors")
def submit_rumor(payload: RumorPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    summary = summarize_payload(payload)
    clean = {
        "target": payload.target.strip() or summary["target"],
        "logic": payload.logic.strip() or summary["logic"],
        "raw_content": payload.raw_content.strip(),
        "institution": payload.institution.strip() or summary["institution"],
        "recommender": payload.recommender.strip() or summary["recommender"],
        "recommendation_date": payload.recommendation_date,
    }
    if not clean["target"] or not clean["logic"]:
        raise HTTPException(422, "需要可识别的标的和核心逻辑")
    scored = score_text(clean)
    rec_date = normalize_date(payload.recommendation_date)
    execute(
        """
        insert into rumors
        (submitter_id, submitter_name, recommendation_date, recommender, target, logic, raw_content,
         institution, key_points, ai_score, ai_tier, ai_reasons, created_at, source)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'community')
        """,
        (
            user["id"],
            user["display_name"],
            rec_date,
            clean["recommender"],
            clean["target"],
            clean["logic"],
            clean["raw_content"],
            clean["institution"],
            json.dumps(summary["key_points"], ensure_ascii=False),
            scored["score"],
            scored["tier"],
            json.dumps(scored["reasons"], ensure_ascii=False),
            now_iso(),
        ),
    )
    rumor = query("select * from rumors order by id desc limit 1")[0]
    execute("insert or ignore into unlocks(user_id, rumor_id, reason, created_at) values (?, ?, 'own_submission', ?)", (user["id"], rumor["id"], now_iso()))
    unlocked = unlock_similar(user["id"], scored["score"])
    refresh_backtests(limit=20)
    recalc_user_scores()
    return {
        "item": public_rumor(rumor, True),
        "unlocked": public_rumor(unlocked, True) if unlocked else None,
        "score": scored,
        "summary": summary,
    }


@app.get("/api/backtests")
def backtests(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    unlocked = user_unlocked_ids(user)
    rows = query(
        """
        select r.id, r.submitter_id, r.target, r.logic, r.ai_score, r.ai_tier, b.*
        from rumors r left join backtests b on b.rumor_id = r.id
        order by case when b.status = 'ok' then 0 else 1 end, r.ai_score desc
        limit 160
        """
    )
    items = []
    for row in rows:
        item = dict(row)
        visible = can_view(user, row, unlocked)
        if not visible:
            item["target"] = mask_target(row["target"])
            item["logic"] = "已锁定"
            item["code"] = None
            item["name"] = None
        item["unlocked"] = visible
        items.append(item)
    return {"items": items}


@app.post("/api/backtests/refresh")
def refresh(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    current_user(response, agu_session)
    return {"updated": refresh_backtests(limit=300, force=True)}


@app.get("/api/leaderboard")
def leaderboard(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    current_user(response, agu_session)
    rows = query(
        """
        select id, display_name, xp, reputation, direct_quota, created_at
        from users where is_guest = 0 or xp > 0
        order by xp desc, reputation desc limit 50
        """
    )
    return {"items": [{**dict(r), "level": level_for(r["xp"])["name"]} for r in rows]}


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8289"))
    uvicorn.run("app.main:app", host=host, port=port)
