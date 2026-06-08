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
import sys
import time
from datetime import date, datetime, timezone
from email.message import EmailMessage
from functools import lru_cache
from pathlib import Path
from typing import Any

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
STOCK_LOOKUP_CACHE = DATA_DIR / "stock_lookup.json"
LLM_PROVIDER_PATH = ROOT.parent / "llm_provider"
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
LLM_DISABLED_UNTIL = 0.0
SMTP_NOT_CONFIGURED_MESSAGE = "SMTP 未配置，请设置 SMTP_USER 和 SMTP_PASS 环境变量"
NEW_USER_DIRECT_QUOTA = 10
INVITE_REWARD_DIRECT_QUOTA = 10
HIGH_TIER_SUBMISSION_DIRECT_QUOTA = 3


class AuthPayload(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=4, max_length=128)


class SendCodePayload(BaseModel):
    email: str = Field(min_length=4, max_length=120)


class PasswordResetLookupPayload(BaseModel):
    account: str = Field(min_length=2, max_length=120)


class PasswordResetSendPayload(BaseModel):
    account: str = Field(min_length=2, max_length=120)


class PasswordResetPayload(BaseModel):
    account: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=6, max_length=128)


class RegisterPayload(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    email: str = Field(min_length=4, max_length=120)
    code: str = Field(min_length=6, max_length=6)
    invite_code: str = Field(default="", max_length=500)


class RumorPayload(BaseModel):
    target: str = Field(default="", max_length=120)
    stock_codes: str = Field(default="", max_length=2000)
    logic: str = Field(default="", max_length=240)
    raw_content: str = Field(min_length=10, max_length=8000)
    institution: str = Field(default="", max_length=80)
    recommender: str = Field(default="", max_length=80)
    recommendation_date: str | None = None


class CommentPayload(BaseModel):
    content: str = Field(min_length=2, max_length=500)


class ReactionPayload(BaseModel):
    reaction: str = Field(pattern="^(useful|doubt)$")


class ReportPayload(BaseModel):
    reason: str = Field(pattern="^(false_info|promotion|duplicate|stale|abuse)$")
    details: str = Field(default="", max_length=240)


class WatchPayload(BaseModel):
    code: str = Field(default="", max_length=16)
    name: str = Field(default="", max_length=40)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def hidden_copyright_mark() -> str:
    return "StockWhisper::CC-BY-NC-SA-4.0::openclaw-community-intel"


def zero_width_encode(text: str) -> str:
    bits = "".join(f"{byte:08b}" for byte in text.encode("utf-8"))
    return "".join("\u200b" if bit == "0" else "\u200c" for bit in bits)


def rumor_forensic_signature(rumor_id: int, viewer_id: int | None, unlocked: bool, content: str = "") -> dict[str, Any]:
    content_hash = hashlib.sha256(str(content or "").encode()).hexdigest()
    seed = f"sw-forensic:{rumor_id}:{viewer_id or 0}:{int(unlocked)}:{content_hash}:{hidden_copyright_mark()}"
    digest = hashlib.blake2s(seed.encode(), digest_size=16).hexdigest()
    payload = f"SW:{rumor_id}:{viewer_id or 0}:{digest}:{content_hash[:16]}"
    return {
        "signature": digest,
        "content_hash": content_hash,
        "short": digest[:12],
        "payload": payload,
        "zero_width": zero_width_encode(payload),
        "algorithm": "blake2s-128+sha256-content",
    }


def rumor_rights_fingerprint(rumor_id: int, viewer_id: int | None = None, unlocked: bool = False) -> dict[str, Any]:
    seed = f"rumor:{rumor_id}:viewer:{viewer_id or 0}:unlocked:{int(unlocked)}:{hidden_copyright_mark()}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return {
        "fingerprint": digest[:20],
        "payload_hash": digest,
        "mark": hidden_copyright_mark(),
        "scope": "rumor-content",
        "license": "CC-BY-NC-SA-4.0",
    }


def rights_envelope(scope: str, viewer_id: int | None = None, payload_key: str = "") -> dict[str, Any]:
    issued_at = now_iso()
    payload = f"SW-API:{scope}:{viewer_id or 0}:{payload_key}:{issued_at}"
    digest = hashlib.blake2s(f"{payload}:{hidden_copyright_mark()}".encode(), digest_size=16).hexdigest()
    return {
        "scope": scope,
        "mark": hidden_copyright_mark(),
        "license": "CC-BY-NC-SA-4.0",
        "fingerprint": digest,
        "payload": payload,
        "zero_width": zero_width_encode(payload),
        "issued_at": issued_at,
    }


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


def send_verification_email(email: str, code: str, subject: str = "股情报 注册验证码", body: str | None = None) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("SMTP_FROM", user)
    if not user or not password:
        raise RuntimeError(SMTP_NOT_CONFIGURED_MESSAGE)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(body or f"您的验证码是：{code}\n5 分钟内有效，请勿泄露。")
    if port == 465:
        with smtplib.SMTP_SSL(host, port) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)


def registration_verification_email_body(code: str) -> str:
    return (
        "欢迎加入股情报 StockWhisper 社区。\n\n"
        "您正在创建股情报账号。这里汇集 A 股情报线索、社区反馈、回测表现和信息源成长记录，"
        "期待您在社区中发现更有价值的信号，也分享可验证的高质量信息。\n\n"
        f"本次注册验证码：{code}\n\n"
        "验证码 5 分钟内有效，请勿转发或泄露给他人。\n"
        "如果这不是您本人发起的注册请求，请忽略本邮件。\n\n"
        "股情报 StockWhisper"
    )


def password_reset_verification_email_body(code: str) -> str:
    return (
        "您正在为股情报 StockWhisper 账号重置密码。\n\n"
        "为保护账号安全，请在页面中输入以下验证码完成验证：\n\n"
        f"密码重置验证码：{code}\n\n"
        "验证码 5 分钟内有效，请勿转发或泄露给他人。\n"
        "如果这不是您本人发起的操作，请忽略本邮件；您的原密码不会因此被修改。\n\n"
        "股情报 StockWhisper"
    )


def can_fallback_to_logged_email_code(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "smtp 未配置",
            "unreachable",
            "connect",
            "timed out",
        )
    )


def deliver_email_code(email: str, code: str, subject: str, body: str) -> str:
    try:
        send_verification_email(email, code, subject=subject, body=body)
        return "smtp"
    except Exception as exc:
        print(f"[send-code] {email} => {code}", file=sys.stderr, flush=True)
        if not can_fallback_to_logged_email_code(exc):
            raise HTTPException(500, f"邮件发送失败：{exc}") from exc
        return "log"


def mask_email(email: str) -> str:
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    if len(local) <= 3:
        masked_local = f"{local[:1]}***"
    elif len(local) <= 6:
        masked_local = f"{local[:3]}***"
    else:
        masked_local = f"{local[:3]}***{local[-2:]}"
    domain_parts = domain.split(".", 1)
    domain_name = domain_parts[0]
    domain_suffix = f".{domain_parts[1]}" if len(domain_parts) > 1 else ""
    if len(domain_name) <= 2:
        masked_domain = f"{domain_name[:1]}***"
    else:
        masked_domain = f"{domain_name[:2]}***{domain_name[-1:]}"
    return f"{masked_local}@{masked_domain}{domain_suffix}"


def password_reset_user(account: str) -> sqlite3.Row:
    value = account.strip()
    rows = query(
        "select * from users where is_guest = 0 and (username = ? or lower(email) = ?)",
        (value, value.lower()),
    )
    if not rows or not rows[0]["email"]:
        raise HTTPException(404, "账号不存在或未绑定邮箱")
    return rows[0]


def score_text(payload: RumorPayload | dict[str, Any]) -> dict[str, Any]:
    def value(key: str) -> Any:
        return payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")

    text = " ".join(
        str(value(k))
        for k in ("target", "stock_codes", "logic", "raw_content", "institution", "recommender")
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
    dimensions = {
        "specificity": specificity,
        "evidence": evidence,
        "freshness": freshness,
        "source": 10 if value("institution") or value("recommender") else 0,
        "verifiability": min(20, tickers * 4 + catalysts * 3),
    }
    return {"score": score, "tier": tier, "reasons": reasons, "dimensions": dimensions}


def tier_for_score(score: int | float) -> str:
    score = int(score)
    return "S" if score >= 86 else "A" if score >= 72 else "B" if score >= 55 else "C"


def parse_reason_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def reason_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    data = parse_reason_payload(raw)
    if data:
        return [str(item) for item in data.get("reasons", []) if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(data, list):
            return [str(item) for item in data if str(item).strip()]
    return []


def score_with_llm_logic(scored: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    logic_score = summary.get("logic_score")
    try:
        logic_score = int(float(logic_score))
    except (TypeError, ValueError):
        return scored
    logic_score = max(1, min(100, logic_score))
    blended_score = int(round(logic_score * 0.7 + int(scored["score"]) * 0.3))
    blended = dict(scored)
    blended["score"] = max(1, min(100, blended_score))
    blended["tier"] = tier_for_score(blended["score"])
    reasons = list(scored.get("reasons") or [])
    reason = str(summary.get("logic_reason") or "").strip()
    reasons.insert(0, f"大模型逻辑评分 {logic_score}" + (f"：{reason}" if reason else ""))
    blended["reasons"] = reasons
    blended["llm_logic_score"] = logic_score
    blended["llm_logic_reason"] = reason
    return blended


RISK_PATTERNS: tuple[dict[str, Any], ...] = (
    {
        "key": "guarantee",
        "label": "保收益/稳赚承诺",
        "severity": "high",
        "terms": ("保收益", "保本", "稳赚", "稳了", "必赚", "包赚", "无风险", "稳赚不赔"),
    },
    {
        "key": "absolute_prediction",
        "label": "绝对化涨幅判断",
        "severity": "watch",
        "terms": ("必涨", "必板", "翻倍", "连板", "涨停", "马上拉升", "起飞"),
    },
    {
        "key": "insider",
        "label": "内幕/未公开暗示",
        "severity": "high",
        "terms": ("内幕", "内线", "一手消息", "庄家", "坐庄", "私募通道", "主力控盘"),
    },
    {
        "key": "solicitation",
        "label": "喊单/带单引导",
        "severity": "high",
        "terms": ("带单", "喊单", "老师喊单", "跟买", "跟上", "进群", "荐股收费", "收费群"),
    },
    {
        "key": "position_pressure",
        "label": "仓位压迫",
        "severity": "high",
        "terms": ("满仓", "梭哈", "全仓", "融资加仓", "借钱买"),
    },
    {
        "key": "weak_source",
        "label": "传闻来源较弱",
        "severity": "watch",
        "terms": ("听说", "据传", "群里说", "朋友说", "未确认", "小道消息"),
    },
)


def risk_analysis(payload: RumorPayload | dict[str, Any]) -> dict[str, Any]:
    def value(key: str) -> Any:
        return payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")

    text = " ".join(
        str(value(k) or "")
        for k in ("target", "stock_codes", "logic", "raw_content", "institution", "recommender")
    )
    compact = re.sub(r"\s+", "", text)
    flags = []
    for pattern in RISK_PATTERNS:
        matched = [term for term in pattern["terms"] if term in compact]
        if matched:
            flags.append(
                {
                    "key": pattern["key"],
                    "label": pattern["label"],
                    "severity": pattern["severity"],
                    "matched": matched[:5],
                }
            )
    high_count = sum(1 for flag in flags if flag["severity"] == "high")
    if high_count >= 2 or any(flag["key"] in {"guarantee", "solicitation", "position_pressure"} for flag in flags):
        level, label, penalty = "high", "高风险话术", 6
        summary = "存在保收益、喊单、内幕或仓位压迫类表达，建议改写为事实、来源和风险条件。"
    elif flags:
        level, label, penalty = "watch", "需核验话术", 3
        summary = "存在绝对化判断或弱来源表达，建议补充可验证事实和反向风险。"
    else:
        level, label, penalty = "clear", "风控清洁", 0
        summary = "未发现明显保收益、喊单或内幕类高风险措辞。"
    return {
        "level": level,
        "label": label,
        "score_penalty": penalty,
        "flags": flags,
        "summary": summary,
        "suggestion": "移除保收益/喊单/满仓等引导，改成可验证节点、来源边界和风险提示。" if flags else "",
    }


def apply_risk_to_score(scored: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    penalty = int(risk.get("score_penalty") or 0)
    if penalty <= 0:
        return scored
    adjusted = dict(scored)
    adjusted_score = max(1, min(100, int(scored["score"]) - penalty))
    adjusted["score"] = adjusted_score
    adjusted["tier"] = "S" if adjusted_score >= 86 else "A" if adjusted_score >= 72 else "B" if adjusted_score >= 55 else "C"
    adjusted["dimensions"] = dict(scored.get("dimensions") or {})
    adjusted["reasons"] = list(scored.get("reasons") or [])
    adjusted["reasons"].append(f"风控提示：{risk.get('label', '需核验话术')}")
    return adjusted


def evidence_ladder(
    payload: RumorPayload | dict[str, Any],
    dimensions: dict[str, Any],
    risk: dict[str, Any],
    novelty: dict[str, Any],
) -> dict[str, Any]:
    def value(key: str) -> str:
        raw = payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")
        return str(raw or "").strip()

    text = " ".join(value(k) for k in ("target", "stock_codes", "logic", "raw_content", "institution", "recommender"))
    source_ok = dimensions.get("source", 0) > 0
    stock_ok = bool(stock_items_from_payload(value("target"), value("stock_codes")) or re.search(r"\b[036]\d{5}\b", text))
    catalyst_hits = [word for word in ("订单", "中标", "客户", "合作", "产能", "业绩", "政策", "涨价", "并购") if word in text]
    numeric_hits = re.findall(r"\d+(?:\.\d+)?\s*(?:亿|万|%|元|吨|台|GW|GWh|MW|个月|年|天)?", text, flags=re.I)
    date_ok = bool(value("recommendation_date"))
    risk_ok = risk.get("level") == "clear"
    levels = [
        {
            "key": "source",
            "label": "来源链路",
            "state": "strong" if source_ok else "weak",
            "detail": "已填写机构或推荐人，可追踪来源。" if source_ok else "缺少机构、推荐人或渠道来源。",
            "score": 20 if source_ok else 0,
        },
        {
            "key": "target",
            "label": "标的归档",
            "state": "strong" if stock_ok else "weak",
            "detail": "标的或代码明确，可进入自选和回测链路。" if stock_ok else "缺少明确股票名称或 6 位代码。",
            "score": 20 if stock_ok else 0,
        },
        {
            "key": "catalyst",
            "label": "催化事实",
            "state": "strong" if len(catalyst_hits) >= 2 else "watch" if catalyst_hits else "weak",
            "detail": f"命中 {len(catalyst_hits)} 个催化词：{'、'.join(catalyst_hits[:4])}" if catalyst_hits else "缺少订单、客户、业绩、政策等事实催化。",
            "score": 20 if len(catalyst_hits) >= 2 else 10 if catalyst_hits else 0,
        },
        {
            "key": "verification",
            "label": "验证节点",
            "state": "strong" if numeric_hits and date_ok else "watch" if numeric_hits or date_ok else "weak",
            "detail": f"含 {len(numeric_hits)} 个数字/规模信号，{'已给日期' if date_ok else '缺少日期'}。" if numeric_hits or date_ok else "缺少数字、时间点或可复盘节点。",
            "score": 20 if numeric_hits and date_ok else 10 if numeric_hits or date_ok else 0,
        },
        {
            "key": "risk_boundary",
            "label": "风险边界",
            "state": "strong" if risk_ok and novelty.get("penalty", 0) < 8 else "watch" if risk.get("level") != "high" else "weak",
            "detail": "未发现明显喊单/保收益表达，且重复度可控。" if risk_ok and novelty.get("penalty", 0) < 8 else risk.get("summary") or "需补充差异化验证和风险边界。",
            "score": 20 if risk_ok and novelty.get("penalty", 0) < 8 else 10 if risk.get("level") != "high" else 0,
        },
    ]
    total = sum(int(item["score"]) for item in levels)
    if total >= 80:
        grade, label, summary = "A", "证据链较完整", "可以进入交换池，重点等待社区反馈和回测。"
    elif total >= 55:
        grade, label, summary = "B", "有核心线索但需补强", "建议补来源、数字或风险边界后再提高权重。"
    else:
        grade, label, summary = "C", "证据链偏弱", "先补齐来源、标的、催化和验证节点，避免低价值重复投稿。"
    next_steps = [item["label"] for item in levels if item["state"] != "strong"][:3]
    if not next_steps:
        next_steps = ["等待社区交叉验证", "跟踪回测表现", "补充反向风险"]
    return {
        "grade": grade,
        "label": label,
        "score": total,
        "summary": summary,
        "levels": levels,
        "next_steps": next_steps,
    }


def verification_tasks(
    payload: RumorPayload | dict[str, Any],
    dimensions: dict[str, Any] | None = None,
    risk: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    def value(key: str) -> str:
        raw = payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")
        return str(raw or "").strip()

    text = " ".join(value(k) for k in ("target", "stock_codes", "logic", "raw_content", "institution", "recommender"))
    target = value("target") or "该标的"
    stock_items = stock_items_from_payload(value("target"), value("stock_codes"))
    dims = dimensions or score_text(payload)["dimensions"]
    risk = risk or risk_analysis(payload)
    outcome = outcome or backtest_outcome(None)
    tasks: list[dict[str, Any]] = []
    if any(word in text for word in ("订单", "中标", "客户", "合作")):
        tasks.append(
            {
                "key": "order_check",
                "label": "核验订单/客户",
                "priority": "high",
                "status": "open",
                "detail": f"确认{target}涉及的订单金额、客户名称、交付时间和公告或调研来源。",
                "action": "有用",
            }
        )
    if any(word in text for word in ("产能", "交付", "爬坡", "扩产")):
        tasks.append(
            {
                "key": "capacity_check",
                "label": "核验产能兑现",
                "priority": "medium",
                "status": "open",
                "detail": "跟踪产能释放节奏、良率、交付瓶颈和是否存在延期。",
                "action": "有用",
            }
        )
    if any(word in text for word in ("业绩", "利润", "收入", "毛利", "订单")):
        tasks.append(
            {
                "key": "earnings_bridge",
                "label": "拆解业绩弹性",
                "priority": "medium",
                "status": "open",
                "detail": "估算收入确认周期、毛利率和对单季利润的实际影响。",
                "action": "讨论",
            }
        )
    if any(word in text for word in ("政策", "国产替代", "补贴", "监管")):
        tasks.append(
            {
                "key": "policy_check",
                "label": "核验政策催化",
                "priority": "medium",
                "status": "open",
                "detail": "确认政策原文、适用范围、落地时间和是否已有市场预期。",
                "action": "有用",
            }
        )
    if risk.get("level") in {"watch", "high"}:
        tasks.append(
            {
                "key": "risk_language",
                "label": "剔除高风险话术",
                "priority": "high",
                "status": "open",
                "detail": risk.get("suggestion") or risk.get("summary") or "把结论改写为事实、来源和风险条件。",
                "action": "存疑",
            }
        )
    if not value("institution") and not value("recommender"):
        tasks.append(
            {
                "key": "source_trace",
                "label": "补来源链路",
                "priority": "high",
                "status": "open",
                "detail": "补充机构、推荐人、调研纪要、公告或产业链来源，便于后续追踪。",
                "action": "有用",
            }
        )
    if not value("recommendation_date"):
        tasks.append(
            {
                "key": "date_trace",
                "label": "补时间戳",
                "priority": "medium",
                "status": "open",
                "detail": "补充推荐日期和最早传播时间，决定时效评分与回测起点。",
                "action": "讨论",
            }
        )
    if dims.get("verifiability", 0) < 8:
        tasks.append(
            {
                "key": "verifiable_node",
                "label": "补可验证节点",
                "priority": "medium",
                "status": "open",
                "detail": "补充公告、订单编号、客户、价格、产能或业绩指标中的至少一项。",
                "action": "有用",
            }
        )
    if outcome.get("state") in {"pending", "no_code", "skipped"} and stock_items:
        tasks.append(
            {
                "key": "market_followup",
                "label": "等待行情复盘",
                "priority": "low",
                "status": "pending",
                "detail": f"跟踪 {stock_items[0].get('code') or stock_items[0].get('name')} 的 T+1/T+5/T+20 表现。",
                "action": "讨论",
            }
        )
    if not tasks:
        tasks.append(
            {
                "key": "counter_evidence",
                "label": "补反向证据",
                "priority": "medium",
                "status": "open",
                "detail": "补充不成立条件、竞争格局、估值压力或订单兑现风险。",
                "action": "存疑",
            }
        )
    priority_order = {"high": 0, "medium": 1, "low": 2}
    unique: dict[str, dict[str, Any]] = {}
    for item in tasks:
        unique.setdefault(item["key"], item)
    return sorted(unique.values(), key=lambda item: priority_order.get(item["priority"], 9))[:5]


def verification_bounties(tasks: list[dict[str, Any]], discussion: dict[str, Any], unlocked: bool) -> list[dict[str, Any]]:
    if not unlocked:
        return [
            {
                "key": "unlock_to_verify",
                "label": "解锁后参与社区反馈",
                "reward_xp": 0,
                "reputation_delta": 0,
                "action": "解锁",
                "state": "locked",
                "detail": "完整内容解锁后，可通过标记有用、存疑或评论获得成长奖励。",
            }
        ]
    done_useful = int(discussion.get("useful") or 0)
    done_doubt = int(discussion.get("doubt") or 0)
    comments = int(discussion.get("comments") or 0)
    bounties = []
    for task in tasks[:4]:
        priority = task.get("priority")
        action = task.get("action") or "讨论"
        reward_xp = 4 if priority == "high" else 3 if priority == "medium" else 2
        reputation_delta = 0.2 if priority == "high" else 0.1
        if action == "存疑":
            state = "active" if done_doubt == 0 else "claimed"
            cta = "提交存疑"
        elif action == "有用":
            state = "active" if done_useful == 0 else "claimed"
            cta = "标记有用"
        else:
            state = "active" if comments == 0 else "claimed"
            cta = "补充讨论"
        bounties.append(
            {
                "key": task.get("key"),
                "label": task.get("label"),
                "reward_xp": reward_xp,
                "reputation_delta": reputation_delta,
                "action": action,
                "cta": cta,
                "state": state,
                "detail": task.get("detail") or "补充可验证事实、来源或风险点。",
            }
        )
    return bounties


def text_fingerprint_tokens(*parts: str) -> set[str]:
    text = re.sub(r"\s+", "", " ".join(str(part or "") for part in parts))
    cn = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    tokens: set[str] = set()
    for block in cn:
        tokens.update(block[i : i + 2] for i in range(max(0, len(block) - 1)))
    tokens.update(re.findall(r"[A-Za-z0-9]{3,}", text.lower()))
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def novelty_analysis(payload: RumorPayload | dict[str, Any], exclude_id: int | None = None) -> dict[str, Any]:
    def value(key: str) -> str:
        raw = payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")
        return str(raw or "").strip()

    target = value("target")
    raw_stock_codes = value("stock_codes")
    logic = value("logic")
    raw_content = value("raw_content")
    recommendation_date = normalize_date(value("recommendation_date")) if value("recommendation_date") else None
    stock_items = stock_items_from_payload(target, raw_stock_codes)
    search_terms = [item["code"] for item in stock_items if item.get("code")] + [item["name"] for item in stock_items if item.get("name")]
    if target:
        search_terms.extend([part for part in re.split(r"[、,，/;；\s]+", target) if part])
    search_terms = [term for term in dict.fromkeys(search_terms) if term]
    if not search_terms:
        return {"state": "unknown", "score": 8, "penalty": 0, "related_count": 0, "max_overlap": 0.0, "matches": [], "label": "待判断"}

    clauses = []
    args: list[Any] = []
    for term in search_terms[:10]:
        clauses.append("(target like ? or stock_codes like ? or logic like ? or raw_content like ?)")
        args.extend([f"%{term}%"] * 4)
    sql = "select id, target, logic, raw_content, ai_score, ai_tier, recommendation_date from rumors where (" + " or ".join(clauses) + ")"
    if exclude_id is not None:
        sql += " and id != ?"
        args.append(exclude_id)
    sql += " order by recommendation_date desc, ai_score desc limit 20"
    rows = query(sql, tuple(args))
    input_tokens = text_fingerprint_tokens(target, logic, raw_content)
    matches = []
    max_overlap = 0.0
    same_day_overlap = 0.0
    recent_overlap = 0.0
    for row in rows:
        overlap = jaccard(input_tokens, text_fingerprint_tokens(row["target"], row["logic"], row["raw_content"]))
        max_overlap = max(max_overlap, overlap)
        row_date = row["recommendation_date"]
        if recommendation_date and row_date == recommendation_date:
            same_day_overlap = max(same_day_overlap, overlap)
        if recommendation_date and row_date:
            try:
                delta_days = abs((date.fromisoformat(recommendation_date) - date.fromisoformat(row_date)).days)
            except ValueError:
                delta_days = 999
            if delta_days <= 3:
                recent_overlap = max(recent_overlap, overlap)
        matches.append(
            {
                "id": row["id"],
                "target": row["target"],
                "tier": row["ai_tier"],
                "score": row["ai_score"],
                "date": row["recommendation_date"],
                "overlap": round(overlap, 2),
            }
        )
    related_count = len(rows)
    if same_day_overlap >= 0.58:
        state, score, penalty, label, penalty_reason = "duplicate", 0, 16, "当日同标的逻辑高度重复", "当日同标的文本逻辑非常相似，稀缺性极低。"
    elif recent_overlap >= 0.58:
        state, score, penalty, label, penalty_reason = "recent_duplicate", 3, 10, "近3日逻辑重复", "近3日同标的文本逻辑高度相似，适当降低稀缺性。"
    elif max_overlap >= 0.58:
        state, score, penalty, label, penalty_reason = "duplicate", 0, 14, "高度重复", "同标的历史文本逻辑高度相似。"
    elif related_count >= 6 or max_overlap >= 0.32:
        state, score, penalty, label, penalty_reason = "crowded", 5, 8, "同标的拥挤", "同标的已有多条相似线索。"
    elif related_count >= 2:
        state, score, penalty, label, penalty_reason = "related", 10, 3, "已有相关线索", "存在相关线索但逻辑重复度可控。"
    else:
        state, score, penalty, label, penalty_reason = "unique", 15, 0, "稀缺线索", "同标的近似逻辑较少。"
    return {
        "state": state,
        "score": score,
        "penalty": penalty,
        "penalty_reason": penalty_reason,
        "related_count": related_count,
        "max_overlap": round(max_overlap, 2),
        "same_day_overlap": round(same_day_overlap, 2),
        "recent_overlap": round(recent_overlap, 2),
        "matches": matches[:5],
        "label": label,
    }


def apply_novelty_to_score(scored: dict[str, Any], novelty: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(scored)
    penalty = int(novelty.get("penalty") or 0)
    adjusted_score = max(1, min(100, int(scored["score"]) - penalty))
    adjusted["score"] = adjusted_score
    adjusted["tier"] = "S" if adjusted_score >= 86 else "A" if adjusted_score >= 72 else "B" if adjusted_score >= 55 else "C"
    adjusted["dimensions"] = {**(scored.get("dimensions") or {}), "novelty": int(novelty.get("score") or 0)}
    adjusted["reasons"] = list(scored.get("reasons") or [])
    if penalty >= 8:
        adjusted["reasons"].append(f"稀缺性扣分：{novelty.get('label', '重复线索')}")
    elif novelty.get("state") == "unique":
        adjusted["reasons"].append("稀缺性较好")
    return adjusted


def rumor_dimension_scores(
    row: sqlite3.Row | dict[str, Any],
    provider: dict[str, Any] | None,
    outcome: dict[str, Any] | None,
) -> dict[str, Any]:
    row_dict = dict(row)
    base = score_text(row_dict)
    reason_payload = parse_reason_payload(row_dict.get("ai_reasons"))
    llm_logic_score = reason_payload.get("logic_score")
    try:
        llm_logic_score = int(float(llm_logic_score))
    except (TypeError, ValueError):
        llm_logic_score = None
    logic_score = llm_logic_score if llm_logic_score is not None else int(row_dict.get("ai_score") or base["score"])
    logic_detail = str(reason_payload.get("logic_reason") or "").strip()
    novelty = novelty_analysis(row_dict, row_dict.get("id"))
    provider_score = float((provider or {}).get("grade", {}).get("score") or 0)
    backtest_score = outcome.get("score") if outcome else None
    items = [
        {
            "key": "logic",
            "label": "推荐逻辑",
            "score": int(logic_score),
            "detail": logic_detail or "录入时优先根据大模型对逻辑扎实度评分，无模型结果时用结构化规则兜底。",
        },
        {
            "key": "novelty",
            "label": "稀缺性",
            "score": int(novelty.get("score") or 0),
            "detail": novelty.get("label") or "待判断",
        },
        {
            "key": "backtest",
            "label": "回测分位",
            "score": round(float(backtest_score), 1) if backtest_score is not None else None,
            "detail": "综合 T+1/T+5/T+20 收益和回撤后的全样本分位。",
        },
        {
            "key": "observer",
            "label": "观察者",
            "score": round(provider_score, 1),
            "detail": (provider or {}).get("grade", {}).get("name", "新晋观察员"),
        },
    ]
    available = [float(item["score"]) for item in items if item["score"] is not None]
    composite = round(sum(available) / len(available), 1) if available else 0.0
    return {"composite": composite, "items": items}


def preview_participation_reward(scored: dict[str, Any], readiness: dict[str, Any], novelty: dict[str, Any]) -> dict[str, Any]:
    score = int(scored.get("score") or 0)
    tier = str(scored.get("tier") or "C")
    readiness_state = str(readiness.get("state") or "draft")
    base_xp = max(5, int(score / 5))
    tier_bonus = {"S": 12, "A": 8, "B": 4, "C": 0}.get(tier, 0)
    novelty_bonus = 3 if novelty.get("state") == "unique" else 0
    penalty = 6 if novelty.get("penalty", 0) >= 8 else 0
    estimated_xp = max(0, base_xp + tier_bonus + novelty_bonus - penalty)
    source_delta = round(min(8.0, score / 20 + tier_bonus / 4 + novelty_bonus / 2 - penalty / 3), 1)
    if readiness_state == "ready":
        exchange_power, exchange_label, next_action = tier, f"可优先匹配{tier}级交换池", "提交后尝试解锁同级高价值情报"
    elif readiness_state == "exchangeable":
        exchange_power, exchange_label, next_action = tier, f"可进入{tier}级交换池", "可提交，也可先补强缺口提高匹配质量"
    else:
        exchange_power, exchange_label, next_action = "C", "暂不建议进入交换池", "先按补强建议完善来源、日期、标的和催化"
        estimated_xp = min(estimated_xp, 8)
        source_delta = min(source_delta, 1.5)
    return {
        "estimated_xp": estimated_xp,
        "source_score_delta": max(0.0, source_delta),
        "quota_delta": HIGH_TIER_SUBMISSION_DIRECT_QUOTA if tier in {"A", "S"} else 0,
        "exchange_power": exchange_power,
        "exchange_label": exchange_label,
        "next_action": next_action,
        "perks": [
            f"预计 +{estimated_xp} XP",
            f"直看额度 +{HIGH_TIER_SUBMISSION_DIRECT_QUOTA}" if tier in {"A", "S"} else "直看额度 +0",
            f"信息源分约 +{max(0.0, source_delta):.1f}",
            exchange_label,
        ],
    }


def score_preview(payload: RumorPayload | dict[str, Any]) -> dict[str, Any]:
    summary = heuristic_summary(payload)

    def value(key: str) -> str:
        raw = payload.get(key, "") if isinstance(payload, dict) else getattr(payload, key, "")
        return str(raw or "").strip()

    clean = {
        "target": value("target") or summary["target"],
        "stock_codes": value("stock_codes"),
        "logic": value("logic") or summary["logic"],
        "raw_content": value("raw_content"),
        "institution": value("institution") or summary["institution"],
        "recommender": value("recommender") or summary["recommender"],
        "recommendation_date": value("recommendation_date"),
    }
    novelty = novelty_analysis(clean)
    risk = risk_analysis(clean)
    scored = apply_risk_to_score(apply_novelty_to_score(score_text(clean), novelty), risk)
    scored = score_with_llm_logic(scored, summary)
    dimensions = scored["dimensions"]
    ladder = evidence_ladder(clean, dimensions, risk, novelty)
    checklist = [
        {"key": "target", "label": "明确标的/代码", "ok": bool(stock_items_from_payload(clean["target"], clean["stock_codes"]) or re.search(r"\b[036]\d{5}\b", clean["raw_content"]))},
        {"key": "logic", "label": "一句话核心逻辑", "ok": len(clean["logic"]) >= 12},
        {"key": "catalyst", "label": "订单/业绩/政策/客户等催化", "ok": dimensions["verifiability"] >= 6},
        {"key": "source", "label": "来源或推荐人", "ok": dimensions["source"] > 0},
        {"key": "date", "label": "推荐日期", "ok": bool(clean["recommendation_date"])},
        {"key": "density", "label": "原文信息密度", "ok": dimensions["evidence"] >= 3},
        {"key": "novelty", "label": "稀缺/非重复", "ok": novelty["penalty"] < 8},
        {"key": "risk", "label": "无保收益/喊单话术", "ok": risk["level"] != "high"},
    ]
    suggestions = []
    for item in checklist:
        if not item["ok"]:
            suggestions.append(f"补充：{item['label']}")
    if scored["score"] < 72:
        suggestions.append("要冲 A 级，优先补全标的代码、催化事实、来源和日期")
    if scored["score"] >= 72 and scored["score"] < 86:
        suggestions.append("距离 S 级主要看信息密度和可验证节点")
    if novelty["penalty"] >= 8:
        suggestions.append("这条线索与既有内容接近，建议补充独家渠道、时间点或差异化验证")
    elif novelty["state"] == "unique":
        suggestions.append("稀缺性较好，保留清晰来源和验证节点有利于交换解锁")
    if risk["level"] == "high":
        suggestions.append("移除保收益、喊单、满仓或内幕类措辞，改成事实依据和风险边界")
    elif risk["level"] == "watch":
        suggestions.append("减少绝对化判断，补充可核验来源和反向风险")
    if not suggestions:
        suggestions.append("结构完整，可直接提交进入交换解锁")
    next_tier = "S" if scored["score"] >= 86 else "A" if scored["score"] >= 72 else "B" if scored["score"] >= 55 else "C"
    ok_count = sum(1 for item in checklist if item["ok"])
    blockers = [item["label"] for item in checklist if not item["ok"]]
    improvement_plan: list[dict[str, Any]] = []
    submitted_stock_items = stock_items_from_payload(value("target"), clean["stock_codes"])
    stock_items = submitted_stock_items or stock_items_from_payload(clean["target"], clean["stock_codes"])
    if not stock_items:
        stock_items = [{"name": name, "code": code} for code, name in find_codes(clean["raw_content"], code_lookup())]
    if not stock_items:
        stock_items = stock_items_from_payload(summary["target"], "")
    if stock_items and not submitted_stock_items:
        improvement_plan.append(
            {
                "field": "stock_codes",
                "label": "补齐推荐标的",
                "action": "apply_stock_rows",
                "value": stock_items[:8],
                "reason": "从原文或摘要中识别到标的，补齐后更容易匹配行情和交换池。",
            }
        )
    elif not checklist[0]["ok"]:
        improvement_plan.append(
            {
                "field": "target",
                "label": "明确标的或代码",
                "action": "manual",
                "value": "",
                "reason": "至少写出股票名称或 6 位代码，避免线索无法归档。",
            }
        )
    if len(clean["logic"]) < 12 and summary["logic"]:
        improvement_plan.append(
            {
                "field": "logic",
                "label": "补强核心逻辑",
                "action": "fill_if_weak",
                "value": summary["logic"][:160],
                "reason": "把上涨/下跌驱动压缩成一句可验证逻辑。",
            }
        )
    if not checklist[2]["ok"]:
        catalyst_hint = next((point for point in summary.get("key_points", []) if len(point) >= 12), "")
        improvement_plan.append(
            {
                "field": "raw_content",
                "label": "补充可验证催化",
                "action": "manual",
                "value": catalyst_hint,
                "reason": "写清订单、客户、业绩、政策、产能等事实节点。",
            }
        )
    if not checklist[3]["ok"] and summary["institution"]:
        improvement_plan.append(
            {
                "field": "institution",
                "label": "补齐来源",
                "action": "fill_if_empty",
                "value": summary["institution"],
                "reason": "来源能提升信息源维度和后续追踪可信度。",
            }
        )
    elif not checklist[3]["ok"]:
        improvement_plan.append(
            {
                "field": "institution",
                "label": "补齐来源",
                "action": "manual",
                "value": "",
                "reason": "填写机构、群聊、调研、产业链或推荐人等来源。",
            }
        )
    if not checklist[4]["ok"]:
        improvement_plan.append(
            {
                "field": "recommendation_date",
                "label": "补齐日期",
                "action": "fill_if_empty",
                "value": date.today().isoformat(),
                "reason": "日期决定时效评分和后续回测起点。",
            }
        )
    if not checklist[5]["ok"]:
        improvement_plan.append(
            {
                "field": "raw_content",
                "label": "提高原文密度",
                "action": "manual",
                "value": "",
                "reason": "补充关键数字、时间点、验证节点和风险点，避免只有结论。",
            }
        )
    if novelty["penalty"] >= 8:
        improvement_plan.append(
            {
                "field": "raw_content",
                "label": "补充差异化验证",
                "action": "manual",
                "value": "",
                "reason": "说明与既有线索不同的渠道、时间点、订单进度或反向风险。",
            }
        )
    if risk["level"] != "clear":
        improvement_plan.append(
            {
                "field": "raw_content",
                "label": "改写高风险话术",
                "action": "manual",
                "value": "",
                "reason": risk["suggestion"] or "把结论改写为可验证事实，并补充不成立条件。",
            }
        )
    risk_readiness_penalty = 18 if risk["level"] == "high" else 8 if risk["level"] == "watch" else 0
    readiness_score = round(ok_count / max(1, len(checklist)) * 100 - min(25, int(novelty.get("penalty") or 0) * 1.5) - risk_readiness_penalty, 1)
    readiness_score = max(0.0, min(100.0, readiness_score))
    if scored["score"] >= 72 and readiness_score >= 75:
        readiness_state, readiness_label, action = "ready", "可进入高价值交换", "提交后优先尝试解锁同级情报"
    elif scored["score"] >= 55 and readiness_score >= 55:
        readiness_state, readiness_label, action = "exchangeable", "可交换但建议补强", "可提交，也可先补齐缺口冲 A 级"
    else:
        readiness_state, readiness_label, action = "draft", "建议补充后再提交", "先补齐阻塞项，避免低价值重复投稿"
    exchange_readiness = {
        "state": readiness_state,
        "label": readiness_label,
        "score": readiness_score,
        "ok_count": ok_count,
        "total": len(checklist),
        "blockers": blockers[:4],
        "action": action,
    }
    participation_reward = preview_participation_reward(scored, exchange_readiness, novelty)
    return {
        "score": scored,
        "summary": summary,
        "checklist": checklist,
        "suggestions": suggestions[:4],
        "next_tier": next_tier,
        "quality_floor": "可交换" if scored["score"] >= 55 else "建议补充后提交",
        "novelty": novelty,
        "risk_analysis": risk,
        "evidence_ladder": ladder,
        "exchange_readiness": exchange_readiness,
        "improvement_plan": improvement_plan[:6],
        "participation_reward": participation_reward,
    }


def provider_grade_components(xp: int, reputation: float, contribution: float, invite_count: int = 0, feedback_score: float = 0.0) -> dict[str, float]:
    return {
        "xp": min(24.0, max(0.0, float(xp or 0)) / 20),
        "contribution": min(28.0, (max(0.0, float(contribution or 0)) ** 0.5) * 2.2),
        "reputation": min(18.0, max(0.0, float(reputation or 0) - 50.0) / 2),
        "invite": min(10.0, max(0, int(invite_count or 0)) * 1.5),
        "feedback": min(20.0, max(0.0, float(feedback_score or 0))),
    }


def provider_grade(xp: int, reputation: float, contribution: float, invite_count: int = 0, feedback_score: float = 0.0) -> dict[str, Any]:
    parts = provider_grade_components(xp, reputation, contribution, invite_count, feedback_score)
    score = min(100.0, sum(parts.values()))
    if score >= 82:
        name, perks = "王牌信息源", "S级优先展示、每日30直看、社区共建席位"
    elif score >= 62:
        name, perks = "核心信息源", "A级优先展示、每日12直看、邀请奖励加成"
    elif score >= 38:
        name, perks = "可信线索员", "B级直看、投稿交换池优先匹配"
    else:
        name, perks = "新晋观察员", "C级开放、通过高质量投稿快速升级"
    return {"name": name, "score": round(score, 1), "perks": perks, "components": {k: round(v, 1) for k, v in parts.items()}}


def provider_upgrade_plan(user: sqlite3.Row) -> dict[str, Any]:
    contribution = contribution_for_user(user["id"])
    feedback_score = provider_feedback_score(user["id"])
    invite_count = int(user["invite_count"] or 0)
    participation = participation_totals(user["id"])
    total_xp = int(user["xp"] or 0) + int(participation["xp"] or 0)
    reputation = max(1.0, min(99.0, float(user["reputation"] or 0) + float(participation["reputation"] or 0)))
    grade = provider_grade(total_xp, reputation, contribution, invite_count, feedback_score)
    components_map = grade.get("components") or provider_grade_components(total_xp, reputation, contribution, invite_count, feedback_score)
    thresholds = [
        (38, "可信线索员"),
        (62, "核心信息源"),
        (82, "王牌信息源"),
    ]
    next_item = next(((target, name) for target, name in thresholds if grade["score"] < target), None)
    components = [
        {"name": "XP", "value": round(float(components_map.get("xp") or 0), 1), "hint": "投稿、邀请、评论和反馈参与"},
        {"name": "贡献度", "value": round(float(components_map.get("contribution") or 0), 1), "hint": "同日同标的只计最高分，按30天半衰期保留"},
        {"name": "信誉", "value": round(float(components_map.get("reputation") or 0), 1), "hint": "只计算高于基础信誉的部分"},
        {"name": "邀请", "value": round(float(components_map.get("invite") or 0), 1), "hint": "每位有效新用户+1.5源分，封顶10"},
        {"name": "社区反馈", "value": round(float(components_map.get("feedback") or 0), 1), "hint": "有用/存疑反馈"},
    ]
    actions = [
        "提交一条含明确标的、催化、来源和日期的线索",
        "在已解锁线索下标记有用或补充风险点",
        "复制邀请链接给同圈层用户，获得XP和直看额度",
    ]
    priority_actions = [
        {
            "key": "submit",
            "title": "补一条可验证线索",
            "impact": "贡献度/XP",
            "detail": "标的、催化、来源、日期完整时，最容易拉动源分。",
            "view": "submit",
            "weight": max(0.0, 28 - float(components_map.get("contribution") or 0)),
        },
        {
            "key": "discussion",
            "title": "参与社区反馈",
            "impact": "社区反馈",
            "detail": "有用会提高信息源可信度，存疑会形成约束。",
            "view": "feed",
            "weight": max(0.0, 8 - feedback_score),
        },
        {
            "key": "invite",
            "title": "邀请同圈层成员",
            "impact": "邀请/额度",
            "detail": "每位有效注册成员增加源分，并带来直看额度。",
            "view": "rank",
            "weight": max(0.0, 10 - float(components_map.get("invite") or 0)),
        },
        {
            "key": "track_record",
            "title": "沉淀回测记录",
            "impact": "信誉",
            "detail": "持续提供可复盘线索，回测表现会进入信誉分。",
            "view": "rank",
            "weight": max(0.0, 18 - float(components_map.get("reputation") or 0)),
        },
    ]
    priority_actions = sorted(priority_actions, key=lambda item: item["weight"], reverse=True)
    for index, item in enumerate(priority_actions, start=1):
        item["rank"] = index
        item["weight"] = round(item["weight"], 1)
    roadmap_templates = [
        ("xp", "XP积累", components_map.get("xp", 0), 18, "提交线索、邀请、评论和反馈都会增加 XP。", "submit"),
        ("contribution", "高质量贡献", components_map.get("contribution", 0), 24, "优先提交不同标的、可验证线索；同日同标的只计最高分。", "submit"),
        ("reputation", "信誉沉淀", components_map.get("reputation", 0), 16, "让线索经得起回测和社区复核，减少高风险话术。", "rank"),
        ("feedback", "社区反馈", components_map.get("feedback", 0), 12, "在详情页标记有用、存疑或补充有效评论。", "feed"),
        ("invite", "同圈层邀请", components_map.get("invite", 0), 6, "邀请有效成员注册，获得 XP、直看额度和源分加成。", "rank"),
    ]
    roadmap = []
    for key, label, value, target_value, detail, view in roadmap_templates:
        gap = max(0.0, float(target_value) - float(value or 0))
        roadmap.append(
            {
                "key": key,
                "label": label,
                "value": round(float(value or 0), 1),
                "target": target_value,
                "gap": round(gap, 1),
                "progress": round(min(100.0, float(value or 0) / target_value * 100), 1) if target_value else 100,
                "detail": detail,
                "view": view,
                "state": "done" if gap <= 0 else "focus" if gap >= 6 else "near",
            }
        )
    roadmap = sorted(roadmap, key=lambda item: (item["state"] == "done", -item["gap"]))
    if next_item is None:
        return {
            "current": grade,
            "next": None,
            "needed": 0,
            "progress": 100,
            "components": components,
            "actions": actions,
            "roadmap": roadmap,
            "priority_actions": priority_actions[:3],
        }
    target, name = next_item
    return {
        "current": grade,
        "next": {"name": name, "target_score": target},
        "needed": round(target - grade["score"], 1),
        "progress": round(min(100, grade["score"] / target * 100), 1),
        "components": components,
        "actions": actions,
        "roadmap": roadmap,
        "priority_actions": priority_actions[:3],
    }


def source_credibility_passport(
    grade: dict[str, Any],
    contribution: float,
    feedback_score: float,
    track_record: dict[str, Any],
    report_count: int,
    follower_count: int,
    upgrade_plan: dict[str, Any],
) -> dict[str, Any]:
    score = float(grade.get("score") or 0)
    samples = int(track_record.get("samples") or 0)
    hit_rate = track_record.get("hit_rate")
    risk_state = "strong" if report_count == 0 else "watch" if report_count <= 2 else "weak"
    if score >= 82 and samples >= 3 and risk_state == "strong":
        label, state = "高可信信息源", "prime"
    elif score >= 62 or (samples >= 2 and risk_state != "weak"):
        label, state = "可重点关注", "track"
    elif score >= 38:
        label, state = "可观察来源", "watch"
    else:
        label, state = "新来源待验证", "new"
    metrics = [
        {"key": "grade", "label": "源分", "value": round(score, 1), "state": "strong" if score >= 62 else "watch" if score >= 38 else "weak"},
        {"key": "samples", "label": "回测样本", "value": samples, "state": "strong" if samples >= 5 else "watch" if samples >= 1 else "weak"},
        {"key": "hit_rate", "label": "命中率", "value": None if hit_rate is None else round(float(hit_rate), 1), "state": "strong" if hit_rate is not None and hit_rate >= 55 else "watch" if hit_rate is not None else "weak"},
        {"key": "feedback", "label": "社区反馈", "value": round(float(feedback_score or 0), 1), "state": "strong" if feedback_score >= 3 else "watch" if feedback_score > 0 else "weak"},
        {"key": "risk", "label": "风险记录", "value": report_count, "state": risk_state},
    ]
    next_action = None
    for item in upgrade_plan.get("priority_actions", []):
        next_action = {"key": item["key"], "label": item["title"], "view": item["view"], "detail": item["detail"]}
        break
    summary = f"{grade.get('name', '信息源')} · {samples} 个回测样本 · {follower_count} 人关注 · 举报 {report_count} 次。"
    if hit_rate is not None:
        summary += f" 命中率 {float(hit_rate):.1f}%。"
    return {
        "state": state,
        "label": label,
        "summary": summary,
        "metrics": metrics,
        "next_action": next_action or {"key": "submit", "label": "继续沉淀样本", "view": "submit", "detail": "提交可验证线索，扩大信息源可信样本。"},
        "follow_hint": "建议关注并持续观察其回测、社区反馈和风险记录。" if state in {"prime", "track"} else "建议先观察其样本数和社区反馈变化。",
    }


def growth_missions(user: sqlite3.Row) -> list[dict[str, Any]]:
    user_id = user["id"]
    submitted = query("select count(*) n, max(ai_score) max_score from rumors where submitter_id = ?", (user_id,))[0]
    comments = query("select count(*) n from rumor_comments where user_id = ?", (user_id,))[0]["n"]
    reactions = query("select count(*) n from rumor_reactions where user_id = ?", (user_id,))[0]["n"]
    unlocks = query("select count(*) n from unlocks where user_id = ?", (user_id,))[0]["n"]
    watched = query("select count(*) n from watchlist where user_id = ?", (user_id,))[0]["n"]
    follows = query("select count(*) n from provider_follows where follower_id = ?", (user_id,))[0]["n"]
    invite_count = int(user["invite_count"] or 0)
    return [
        {
            "key": "register",
            "title": "注册成为正式成员",
            "reward": "保留邀请码和成长记录",
            "completed": not bool(user["is_guest"]),
            "cta_view": "feed",
        },
        {
            "key": "first_submit",
            "title": "贡献第一条可复盘线索",
            "reward": "按评分解锁同等价值情报",
            "completed": int(submitted["n"] or 0) >= 1,
            "cta_view": "submit",
        },
        {
            "key": "high_value_submit",
            "title": "冲一次 A 级以上线索",
            "reward": "显著提升贡献度和信息源分",
            "completed": int(submitted["max_score"] or 0) >= 72,
            "cta_view": "submit",
        },
        {
            "key": "discuss",
            "title": "参与一次社区反馈",
            "reward": "让社区识别高质量参与者",
            "completed": int(comments or 0) + int(reactions or 0) >= 1,
            "cta_view": "feed",
        },
        {
            "key": "unlock",
            "title": "解锁一条高价值情报",
            "reward": "建立自己的复盘样本池",
            "completed": int(unlocks or 0) >= 1,
            "cta_view": "feed",
        },
        {
            "key": "watch",
            "title": "关注一个自选标的",
            "reward": "把社区情报变成你的个人信号流",
            "completed": int(watched or 0) >= 1,
            "cta_view": "feed",
        },
        {
            "key": "follow_provider",
            "title": "关注一个可信信息源",
            "reward": "沉淀自己的高质量消息源",
            "completed": int(follows or 0) >= 1,
            "cta_view": "rank",
        },
        {
            "key": "invite",
            "title": "邀请一位新成员",
            "reward": "30 XP + 10 次直看额度",
            "completed": invite_count >= 1,
            "cta_view": "rank",
        },
    ]


def growth_ledger(user: sqlite3.Row) -> dict[str, Any]:
    user_id = user["id"]
    participation = participation_totals(user_id)
    referral = query(
        "select coalesce(sum(reward_xp), 0) xp, coalesce(sum(reward_quota), 0) quota, count(*) n from referral_events where inviter_id = ?",
        (user_id,),
    )[0]
    submission_rows = query(
        """
        select id, target, ai_score, ai_tier, created_at
        from rumors
        where submitter_id = ?
        order by id desc
        limit 6
        """,
        (user_id,),
    )
    participation_rows = query(
        """
        select pe.event_key, pe.reward_xp, pe.reputation_delta, pe.created_at, r.target
        from participation_events pe
        join rumors r on r.id = pe.rumor_id
        where pe.user_id = ?
        order by pe.id desc
        limit 6
        """,
        (user_id,),
    )
    referral_rows = query(
        """
        select e.reward_xp, e.reward_quota, e.created_at, u.display_name invitee_name
        from referral_events e
        join users u on u.id = e.invitee_id
        where e.inviter_id = ?
        order by e.id desc
        limit 6
        """,
        (user_id,),
    )
    base_xp = int(user["xp"] or 0)
    participation_xp = int(participation["xp"] or 0)
    referral_xp = int(referral["xp"] or 0)
    entries: list[dict[str, Any]] = []
    for row in submission_rows:
        entries.append(
            {
                "kind": "submission",
                "label": "投稿成长",
                "title": row["target"],
                "value": max(5, int((row["ai_score"] or 0) / 5)),
                "unit": "XP估算",
                "detail": f"{row['ai_tier']}{row['ai_score']} · 进入贡献度和源分",
                "created_at": row["created_at"],
            }
        )
    for row in participation_rows:
        entries.append(
            {
                "kind": "participation",
                "label": "反馈参与",
                "title": row["target"],
                "value": int(row["reward_xp"] or 0),
                "unit": "XP",
                "detail": f"{row['event_key']} · 信誉 +{float(row['reputation_delta'] or 0):.1f}",
                "created_at": row["created_at"],
            }
        )
    for row in referral_rows:
        entries.append(
            {
                "kind": "invite",
                "label": "邀请奖励",
                "title": row["invitee_name"],
                "value": int(row["reward_xp"] or 0),
                "unit": "XP",
                "detail": f"直看额度 +{int(row['reward_quota'] or 0)}",
                "created_at": row["created_at"],
            }
        )
    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return {
        "totals": {
            "base_xp": base_xp,
            "participation_xp": participation_xp,
            "referral_xp": referral_xp,
            "total_xp": base_xp + participation_xp,
            "reputation_delta": participation["reputation"],
            "invite_count": int(referral["n"] or 0),
            "reward_quota": int(referral["quota"] or 0),
        },
        "sources": [
            {"key": "submission", "label": "投稿/回测", "value": base_xp, "detail": "由投稿评分和回测表现重算"},
            {"key": "participation", "label": "社区反馈", "value": participation_xp, "detail": "由有用、存疑和评论累计"},
            {"key": "invite", "label": "邀请拉新", "value": referral_xp, "detail": f"{int(referral['n'] or 0)} 位新用户"},
        ],
        "entries": entries[:8],
        "rights_fingerprint": hashlib.sha256(f"growth-ledger:{user_id}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }


def source_privileges(score: float) -> list[dict[str, Any]]:
    tiers = [
        {
            "name": "新晋观察员",
            "threshold": 0,
            "rights": ["C级开放阅读", "质量预评分", "基础讨论与关注"],
        },
        {
            "name": "可信线索员",
            "threshold": 38,
            "rights": ["B级直看", "投稿交换池优先匹配", "信息源档案展示"],
        },
        {
            "name": "核心信息源",
            "threshold": 62,
            "rights": ["A级优先展示", "每日12直看", "邀请奖励加成"],
        },
        {
            "name": "王牌信息源",
            "threshold": 82,
            "rights": ["S级优先展示", "每日30直看", "社区共建席位"],
        },
    ]
    return [
        {
            **tier,
            "unlocked": score >= tier["threshold"],
            "needed": round(max(0.0, tier["threshold"] - score), 1),
        }
        for tier in tiers
    ]


def provider_rank_context(user_id: int) -> dict[str, Any]:
    rows = query(
        """
        select id, display_name, xp, reputation, invite_count
        from users
        where is_guest = 0 or xp > 0
        """
    )
    ranked = []
    for row in rows:
        contribution = contribution_for_user(row["id"])
        feedback_score = provider_feedback_score(row["id"])
        grade = provider_grade(row["xp"], row["reputation"], contribution, row["invite_count"] or 0, feedback_score)
        ranked.append(
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "score": grade["score"],
                "grade_name": grade["name"],
                "xp": row["xp"],
                "reputation": row["reputation"],
            }
        )
    ranked.sort(key=lambda item: (-item["score"], -item["xp"], -item["reputation"], item["id"]))
    current_index = next((i for i, item in enumerate(ranked) if item["id"] == user_id), None)
    total = len(ranked)
    if current_index is None:
        return {
            "rank": None,
            "total": total,
            "percentile": None,
            "ahead": None,
            "behind": None,
            "nearest_above": None,
            "nearest_below": None,
        }
    rank = current_index + 1
    current = ranked[current_index]
    nearest_above = ranked[current_index - 1] if current_index > 0 else None
    nearest_below = ranked[current_index + 1] if current_index + 1 < total else None
    return {
        "rank": rank,
        "total": total,
        "percentile": round((total - rank + 1) / total * 100, 1) if total else None,
        "ahead": current_index,
        "behind": max(0, total - rank),
        "score": current["score"],
        "nearest_above": nearest_above,
        "nearest_below": nearest_below,
    }


def source_recent_performance(user_id: int) -> dict[str, Any]:
    rumor_stats = query(
        """
        select count(*) n, avg(ai_score) avg_score, max(ai_score) best_score
        from rumors
        where submitter_id = ?
        """,
        (user_id,),
    )[0]
    tier_rows = query(
        """
        select ai_tier, count(*) n
        from rumors
        where submitter_id = ?
        group by ai_tier
        """,
        (user_id,),
    )
    bt_rows = query(
        """
        select b.*
        from rumors r join backtests b on b.rumor_id = r.id
        where r.submitter_id = ?
        order by r.recommendation_date desc, r.id desc
        limit 20
        """,
        (user_id,),
    )
    outcomes: dict[str, int] = {}
    for row in bt_rows:
        state = backtest_outcome(row)["state"]
        outcomes[state] = outcomes.get(state, 0) + 1
    samples = sum(outcomes.values())
    hits = outcomes.get("hit", 0)
    return {
        "rumor_count": int(rumor_stats["n"] or 0),
        "avg_score": round(float(rumor_stats["avg_score"] or 0), 1) if rumor_stats["avg_score"] is not None else None,
        "best_score": int(rumor_stats["best_score"] or 0) if rumor_stats["best_score"] is not None else None,
        "tier_mix": {row["ai_tier"]: int(row["n"]) for row in tier_rows},
        "backtest_samples": samples,
        "outcomes": outcomes,
        "hit_rate": round(hits / samples * 100, 1) if samples else None,
    }


def source_upgrade_center(user: sqlite3.Row) -> dict[str, Any]:
    plan = provider_upgrade_plan(user)
    score = float(plan["current"]["score"])
    components = []
    for item in plan["components"]:
        value = float(item["value"] or 0)
        components.append({**item, "progress": round(max(0.0, min(100.0, value / 24 * 100)), 1)})
    missions = []
    weights = {
        "register": "保留邀请码和等级权益",
        "first_submit": "建立信息源基础样本",
        "high_value_submit": "最快拉升贡献度",
        "discuss": "提高社区验证权重",
        "follow_provider": "校准自己的信息源网络",
        "invite": "获得邀请加成",
    }
    for item in growth_missions(user):
        if item["key"] in weights:
            missions.append({**item, "upgrade_hint": weights[item["key"]]})
    recent_performance = source_recent_performance(user["id"])
    passport_track = {
        "samples": sum(int(v or 0) for v in (recent_performance.get("outcomes") or {}).values()),
        "hit_rate": recent_performance.get("hit_rate"),
    }
    follower_count = int(query("select count(*) n from provider_follows where provider_id = ?", (user["id"],))[0]["n"] or 0)
    report_count = int(query("select count(*) n from rumor_reports rr join rumors r on r.id = rr.rumor_id where r.submitter_id = ?", (user["id"],))[0]["n"] or 0)
    return {
        "user": user_out(user),
        "grade": plan["current"],
        "upgrade": {**plan, "components": components},
        "credibility_passport": source_credibility_passport(plan["current"], contribution_for_user(user["id"]), provider_feedback_score(user["id"]), passport_track, report_count, follower_count, plan),
        "privileges": source_privileges(score),
        "rank_context": provider_rank_context(user["id"]),
        "recent_performance": recent_performance,
        "missions": missions,
        "registered": not bool(user["is_guest"]),
    }


def make_invite_code(username: str, user_id: int | None = None) -> str:
    seed = f"{username}:{user_id or secrets.token_hex(4)}:{hidden_copyright_mark()}"
    digest = hashlib.blake2s(seed.encode(), digest_size=5).hexdigest().upper()
    return f"SW{digest}"


def normalize_invite_code(value: str | None) -> str:
    text = str(value or "").strip().upper()
    match = re.search(r"\bSW[A-Z0-9]{8,22}\b", text)
    return match.group(0)[:24] if match else ""


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
            target = "、".join(dict.fromkeys(tags[:3]))
    if not target:
        matched = find_codes(text, code_lookup())
        target = "、".join(name for _, name in matched[:8]) or "待确认标的"
    logic = value("logic")
    institution = value("institution")
    if not institution:
        m = re.search(r"【([^】]{2,24})】", text)
        institution = m.group(1) if m else ""
    recommender = value("recommender")
    key_points = []
    catalyst_words = ("订单", "合作", "中标", "产能", "AI", "国产替代", "业绩", "并购", "政策", "客户", "验证", "毛利", "涨价", "扩产", "出海", "替代", "需求", "份额", "交付", "放量")
    for sentence in re.split(r"[。！？\n]+", text):
        sentence = re.sub(r"^【[^】]+】", "", sentence).strip(" \t#[]【】")
        if len(sentence) >= 12 and any(w in sentence for w in catalyst_words):
            key_points.append(sentence[:90])
        if len(key_points) >= 4:
            break
    if not logic:
        logic = heuristic_logic_from_points(target, key_points, text)
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


def heuristic_logic_from_points(target: str, key_points: list[str], text: str) -> str:
    cleaned_target = re.split(r"[、,，/]", target or "")[0].strip()
    points = [re.sub(r"\s+", "", item).strip("，,；;。") for item in key_points if item]
    if points:
        drivers: list[str] = []
        for point in points[:3]:
            point = re.sub(r"^(公司|其|该公司)", "", point)
            point = re.sub(r"风险是.*$", "", point)
            if len(point) >= 8:
                drivers.append(point[:34])
        if drivers:
            prefix = f"{cleaned_target}：" if cleaned_target and cleaned_target != "待确认标的" else ""
            return (prefix + "；".join(drivers))[:120]
    compact = re.sub(r"\s+", "", text)
    compact = re.sub(r"^【[^】]+】", "", compact)
    if cleaned_target:
        compact = re.sub(rf"^{re.escape(cleaned_target)}[：:，,]?", "", compact)
    return (f"{cleaned_target}：" if cleaned_target and cleaned_target != "待确认标的" else "") + (compact[:90] or "待补充核心逻辑")


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


@lru_cache(maxsize=1)
def llm_provider_call():
    if not LLM_PROVIDER_PATH.exists():
        return None
    provider_path = str(LLM_PROVIDER_PATH)
    if provider_path not in sys.path:
        sys.path.insert(0, provider_path)
    try:
        from call_llm import call_llm as provider_call
    except Exception:
        return None
    return provider_call


def llm_summary(payload: RumorPayload | dict[str, Any]) -> dict[str, Any] | None:
    global LLM_DISABLED_UNTIL
    if time.time() < LLM_DISABLED_UNTIL:
        return None
    provider_call = llm_provider_call()
    if provider_call is None:
        return None
    timeout = float(os.getenv("AGUWHISPER_LLM_TIMEOUT", "20"))
    raw = payload.get("raw_content", "") if isinstance(payload, dict) else payload.raw_content
    prompt = (
        "你是A股私域投研信息整理助手。请从原始消息中抽取结构化关键信息，"
        "只返回JSON，不要解释。字段：target, logic, institution, recommender, key_points, logic_score, logic_reason。"
        "target为股票或产业链标的，logic为不超过80字的核心投资逻辑，"
        "key_points为2到5条事实/催化/验证节点。logic_score为1到100分，判断推荐逻辑是否扎实，"
        "重点看标的清晰度、催化链条、可验证事实、反向风险和是否只是情绪喊单。"
        "logic_reason为不超过40字的评分理由。原始消息：\n"
        f"{raw[:6000]}"
    )
    try:
        result = provider_call(prompt, timeout=timeout, max_tokens=900)
        content = str(result.text or "").strip()
    except Exception:
        LLM_DISABLED_UNTIL = time.time() + 300
        return None
    parsed = parse_llm_json(content)
    if not parsed:
        return None
    key_points = parsed.get("key_points") or []
    if isinstance(key_points, str):
        key_points = [key_points]
    logic_score = parsed.get("logic_score")
    try:
        logic_score = int(float(logic_score))
    except (TypeError, ValueError):
        logic_score = None
    if logic_score is not None:
        logic_score = max(1, min(100, logic_score))
    return {
        "target": str(parsed.get("target") or "")[:120],
        "logic": str(parsed.get("logic") or "")[:240],
        "institution": str(parsed.get("institution") or "")[:80],
        "recommender": str(parsed.get("recommender") or "")[:80],
        "key_points": [str(p)[:120] for p in key_points if str(p).strip()][:5],
        "logic_score": logic_score,
        "logic_reason": str(parsed.get("logic_reason") or "")[:80],
        "summary_source": "llm",
        "llm_provider": getattr(getattr(result, "choice", None), "provider_id", ""),
        "llm_model": getattr(getattr(result, "choice", None), "model", ""),
        "llm_latency_ms": getattr(result, "latency_ms", None),
    }


def summarize_payload(payload: RumorPayload | dict[str, Any]) -> dict[str, Any]:
    base = heuristic_summary(payload)
    ai = llm_summary(payload)
    if not ai:
        base["stock_codes"] = stock_items_from_target(base["target"])
        base["summary_warning"] = "LLM未返回结果，已使用规则提炼。"
        return base
    merged = {**base, **{k: v for k, v in ai.items() if v}}
    if not merged.get("key_points"):
        merged["key_points"] = base["key_points"]
    merged["stock_codes"] = stock_items_from_target(merged["target"])
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
            stock_codes text not null default '[]',
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
        create table if not exists password_reset_verifications (
            email text not null,
            code text not null,
            expires_at integer not null,
            primary key (email)
        );
        create table if not exists referral_events (
            id integer primary key autoincrement,
            inviter_id integer not null references users(id),
            invitee_id integer not null references users(id),
            reward_xp integer not null,
            reward_quota integer not null,
            created_at text not null,
            unique(inviter_id, invitee_id)
        );
        create table if not exists participation_events (
            id integer primary key autoincrement,
            user_id integer not null references users(id),
            rumor_id integer not null references rumors(id),
            event_key text not null,
            reward_xp integer not null,
            reputation_delta real not null default 0,
            created_at text not null,
            unique(user_id, rumor_id, event_key)
        );
        create table if not exists rumor_comments (
            id integer primary key autoincrement,
            rumor_id integer not null references rumors(id),
            user_id integer not null references users(id),
            display_name text not null,
            content text not null,
            created_at text not null
        );
        create table if not exists rumor_reactions (
            user_id integer not null references users(id),
            rumor_id integer not null references rumors(id),
            reaction text not null,
            created_at text not null,
            primary key (user_id, rumor_id, reaction)
        );
        create table if not exists rumor_reports (
            user_id integer not null references users(id),
            rumor_id integer not null references rumors(id),
            reason text not null,
            details text not null default '',
            created_at text not null,
            primary key (user_id, rumor_id, reason)
        );
        create table if not exists watchlist (
            user_id integer not null references users(id),
            code text not null,
            name text not null,
            created_at text not null,
            primary key (user_id, code)
        );
        create table if not exists provider_follows (
            follower_id integer not null references users(id),
            provider_id integer not null references users(id),
            created_at text not null,
            primary key (follower_id, provider_id)
        );
        create index if not exists idx_rumor_reports_rumor on rumor_reports(rumor_id);
        create index if not exists idx_rumor_comments_rumor on rumor_comments(rumor_id);
        create index if not exists idx_rumor_reactions_rumor on rumor_reactions(rumor_id);
        """
    )
    con.commit()
    existing_cols = {row["name"] for row in con.execute("pragma table_info(rumors)").fetchall()}
    if "key_points" not in existing_cols:
        con.execute("alter table rumors add column key_points text not null default '[]'")
        con.commit()
    if "stock_codes" not in existing_cols:
        con.execute("alter table rumors add column stock_codes text not null default '[]'")
        con.commit()
    backfill_rumor_stock_codes()
    user_cols = {row["name"] for row in con.execute("pragma table_info(users)").fetchall()}
    if "email" not in user_cols:
        con.execute("alter table users add column email text")
        con.commit()
    if "invite_code" not in user_cols:
        con.execute("alter table users add column invite_code text")
        con.commit()
    if "invited_by" not in user_cols:
        con.execute("alter table users add column invited_by integer")
        con.commit()
    if "invite_count" not in user_cols:
        con.execute("alter table users add column invite_count integer not null default 0")
        con.commit()
    con.execute("create unique index if not exists idx_users_invite_code on users(invite_code)")
    con.execute(
        """
        create unique index if not exists idx_users_registered_email
        on users(lower(email))
        where is_guest = 0 and email is not null and email != ''
        """
    )
    missing_codes = con.execute("select id, username from users where invite_code is null or invite_code = ''").fetchall()
    for row in missing_codes:
        code = make_invite_code(row["username"], row["id"])
        suffix = 1
        candidate = code
        while con.execute("select 1 from users where invite_code = ? and id != ?", (candidate, row["id"])).fetchone():
            suffix += 1
            candidate = f"{code}{suffix}"
        con.execute("update users set invite_code = ? where id = ?", (candidate, row["id"]))
    con.commit()
    bt_cols = {row["name"] for row in con.execute("pragma table_info(backtests)").fetchall()}
    for col, defn in [("ret_t1_1", "real"), ("ret_t1_5", "real"), ("ret_t1_20", "real"), ("signal_value", "real"), ("max_drawdown_20", "real")]:
        if col not in bt_cols:
            con.execute(f"alter table backtests add column {col} {defn}")
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
            (submitter_name, recommendation_date, recommender, target, stock_codes, logic, raw_content, institution,
             key_points, ai_score, ai_tier, ai_reasons, created_at, source)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reference')
            """,
            (
                payload["submitter"],
                payload["recommendation_date"],
                payload["recommender"],
                payload["target"],
                json.dumps(stock_items_from_target(payload["target"]), ensure_ascii=False),
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
    user = query("select * from users where username = ?", (name,))[0]
    execute("update users set invite_code = ? where id = ?", (make_invite_code(name, user["id"]), user["id"]))
    return query("select * from users where id = ?", (user["id"],))[0]


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


def require_member(user: sqlite3.Row) -> None:
    if user["is_guest"]:
        raise HTTPException(401, "请先注册或登录后参与讨论")


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


def participation_totals(user_id: int) -> dict[str, Any]:
    row = query(
        "select coalesce(sum(reward_xp), 0) xp, coalesce(sum(reputation_delta), 0) reputation from participation_events where user_id = ?",
        (user_id,),
    )[0]
    return {"xp": int(row["xp"] or 0), "reputation": round(float(row["reputation"] or 0), 1)}


def effective_user_xp(user: sqlite3.Row) -> int:
    return int(user["xp"] or 0) + participation_totals(user["id"])["xp"]


def award_participation(user_id: int, rumor_id: int, event_key: str, reward_xp: int, reputation_delta: float = 0.0) -> dict[str, Any]:
    before = participation_totals(user_id)
    execute(
        """
        insert or ignore into participation_events(user_id, rumor_id, event_key, reward_xp, reputation_delta, created_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (user_id, rumor_id, event_key, reward_xp, reputation_delta, now_iso()),
    )
    after = participation_totals(user_id)
    xp_delta = int(after["xp"] or 0) - int(before["xp"] or 0)
    rep_delta = round(float(after["reputation"] or 0) - float(before["reputation"] or 0), 1)
    user = query("select * from users where id = ?", (user_id,))[0]
    total_xp = int(user["xp"] or 0) + int(after["xp"] or 0)
    return {
        "awarded": xp_delta > 0 or rep_delta != 0,
        "xp_delta": xp_delta,
        "reputation_delta": rep_delta,
        "participation_xp": int(after["xp"] or 0),
        "level": level_for(total_xp),
        "message": f"反馈贡献 +{xp_delta} XP" if xp_delta > 0 else "该反馈贡献已记录",
    }


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
        select target, ai_score, created_at from rumors
        where submitter_id = ?
        """,
        (user_id,),
    )
    now = datetime.now(timezone.utc)
    grouped: dict[tuple[str, str], float] = {}
    for row in rows:
        day = str(row["created_at"] or "")[:10]
        target = str(row["target"] or "").strip()
        age_days = max(0.0, (now - parse_dt(row["created_at"])).total_seconds() / 86400)
        decay = 0.5 ** (age_days / CONTRIBUTION_HALF_LIFE_DAYS)
        key = (day, target)
        grouped[key] = max(grouped.get(key, 0.0), float(row["ai_score"]) * decay)
    total = sum(grouped.values())
    return round(total, 1)


def provider_feedback_score(user_id: int) -> float:
    rows = query(
        """
        select rr.reaction, count(*) n
        from rumors r join rumor_reactions rr on rr.rumor_id = r.id
        where r.submitter_id = ?
        group by rr.reaction
        """,
        (user_id,),
    )
    counts = {row["reaction"]: int(row["n"]) for row in rows}
    report_rows = query(
        """
        select rr.reason, count(*) n
        from rumors r join rumor_reports rr on rr.rumor_id = r.id
        where r.submitter_id = ?
        group by rr.reason
        """,
        (user_id,),
    )
    report_counts = {row["reason"]: int(row["n"]) for row in report_rows}
    report_penalty = (
        report_counts.get("false_info", 0) * 2.2
        + report_counts.get("promotion", 0) * 1.5
        + report_counts.get("abuse", 0) * 2.0
        + report_counts.get("duplicate", 0) * 0.8
        + report_counts.get("stale", 0) * 0.8
    )
    bt_rows = query(
        """
        select b.*
        from rumors r join backtests b on b.rumor_id = r.id
        where r.submitter_id = ?
        """,
        (user_id,),
    )
    outcome_bonus = 0.0
    for row in bt_rows:
        outcome = backtest_outcome(row)
        if outcome["state"] == "hit":
            outcome_bonus += 2.0
        elif outcome["state"] == "valid":
            outcome_bonus += 1.0
        elif outcome["state"] == "weak":
            outcome_bonus -= 1.5
    raw = counts.get("useful", 0) * 1.5 - counts.get("doubt", 0) * 1.2 - report_penalty + outcome_bonus
    return round(max(-10.0, min(12.0, raw)), 1)


def provider_proof_scorecard(
    grade: dict[str, Any],
    contribution: float,
    feedback: dict[str, int],
    track_record: dict[str, Any],
    report_count: int,
    tier_mix: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    hit_rate = float(track_record.get("hit_rate") or 0)
    avg_signal = track_record.get("avg_signal")
    samples = int(track_record.get("samples") or 0)
    positive_feedback = int(feedback.get("useful", 0))
    doubt = int(feedback.get("doubt", 0))
    high_tier_count = sum(int((tier_mix.get(tier) or {}).get("count") or 0) for tier in ("S", "A"))
    return [
        {
            "key": "source_grade",
            "label": "源等级",
            "state": "strong" if grade["score"] >= 62 else "watch" if grade["score"] >= 38 else "weak",
            "value": grade["score"],
            "detail": f"{grade['name']} · {grade['perks']}",
        },
        {
            "key": "track_record",
            "label": "回测证明",
            "state": "strong" if samples and hit_rate >= 60 else "watch" if samples else "unknown",
            "value": f"{hit_rate:.1f}%" if samples else "-",
            "detail": f"{samples} 个样本，平均信号{avg_signal:.1f}" if avg_signal is not None else f"{samples} 个样本，等待更多行情验证",
        },
        {
            "key": "community_feedback",
            "label": "社区反馈",
            "state": "strong" if positive_feedback >= 3 and doubt == 0 else "watch" if positive_feedback else "unknown",
            "value": positive_feedback - doubt,
            "detail": f"有用 {positive_feedback} 次，存疑 {doubt} 次",
        },
        {
            "key": "risk_control",
            "label": "风险约束",
            "state": "strong" if report_count == 0 else "weak" if report_count >= 2 else "watch",
            "value": max(0, 100 - report_count * 12),
            "detail": f"{report_count} 次举报记录" if report_count else "暂无举报记录",
        },
        {
            "key": "contribution",
            "label": "持续贡献",
            "state": "strong" if contribution >= 150 or high_tier_count >= 3 else "watch" if contribution > 0 else "unknown",
            "value": round(contribution, 1),
            "detail": f"S/A 线索 {high_tier_count} 条，30天半衰期贡献 {contribution:.1f}",
        },
    ]


def provider_profile(provider_id: int, viewer: sqlite3.Row) -> dict[str, Any]:
    rows = query("select * from users where id = ?", (provider_id,))
    if not rows:
        raise HTTPException(404, "信息源不存在")
    provider = rows[0]
    followed_ids = followed_provider_ids(viewer["id"])
    follower_count = query("select count(*) n from provider_follows where provider_id = ?", (provider_id,))[0]["n"]
    contribution = contribution_for_user(provider_id)
    feedback_score = provider_feedback_score(provider_id)
    invite_count = int(provider["invite_count"] or 0)
    participation = participation_totals(provider_id)
    effective_xp = int(provider["xp"] or 0) + int(participation["xp"] or 0)
    effective_reputation = max(1.0, min(99.0, float(provider["reputation"] or 0) + float(participation["reputation"] or 0)))
    grade = provider_grade(effective_xp, effective_reputation, contribution, invite_count, feedback_score)
    tier_rows = query(
        """
        select ai_tier, count(*) n, avg(ai_score) avg_score, max(ai_score) top_score
        from rumors
        where submitter_id = ?
        group by ai_tier
        """,
        (provider_id,),
    )
    feedback_rows = query(
        """
        select rr.reaction, count(*) n
        from rumors r join rumor_reactions rr on rr.rumor_id = r.id
        where r.submitter_id = ?
        group by rr.reaction
        """,
        (provider_id,),
    )
    bt = query(
        """
        select count(*) n,
               avg(b.signal_value) avg_signal,
               avg(b.ret_t1_1) avg_ret_1,
               avg(b.ret_t1_5) avg_ret_5,
               avg(b.ret_t1_20) avg_ret_20,
               avg(b.max_drawdown_20) avg_drawdown
        from rumors r join backtests b on b.rumor_id = r.id
        where r.submitter_id = ? and b.ret_t1_1 is not null
        """,
        (provider_id,),
    )[0]
    bt_rows = query(
        """
        select b.*
        from rumors r join backtests b on b.rumor_id = r.id
        where r.submitter_id = ?
        """,
        (provider_id,),
    )
    outcome_counts: dict[str, int] = {}
    for row in bt_rows:
        state = backtest_outcome(row)["state"]
        outcome_counts[state] = outcome_counts.get(state, 0) + 1
    recent_rows = query(
        """
        select *
        from rumors
        where submitter_id = ?
        order by recommendation_date desc, id desc
        limit 6
        """,
        (provider_id,),
    )
    unlocked = user_unlocked_ids(viewer)
    watched_codes = watched_codes_for_user(viewer["id"])
    stats = discussion_stats([row["id"] for row in recent_rows], viewer["id"])
    tier_mix = {
        row["ai_tier"]: {
            "count": int(row["n"]),
            "avg_score": round(float(row["avg_score"] or 0), 1),
            "top_score": int(row["top_score"] or 0),
        }
        for row in tier_rows
    }
    feedback = {row["reaction"]: int(row["n"]) for row in feedback_rows}
    report_count = int(query("select count(*) n from rumor_reports rr join rumors r on r.id = rr.rumor_id where r.submitter_id = ?", (provider_id,))[0]["n"] or 0)
    track_record = {
        "samples": int(bt["n"] or 0),
        "avg_signal": round(float(bt["avg_signal"] or 0), 1) if bt["avg_signal"] is not None else None,
        "avg_ret_1": bt["avg_ret_1"],
        "avg_ret_5": bt["avg_ret_5"],
        "avg_ret_20": bt["avg_ret_20"],
        "avg_drawdown": bt["avg_drawdown"],
        "outcomes": outcome_counts,
        "hit_rate": round((outcome_counts.get("hit", 0) + outcome_counts.get("valid", 0)) / max(1, sum(outcome_counts.values())) * 100, 1),
    }
    upgrade_plan = provider_upgrade_plan(provider)
    return {
        "id": provider_id,
        "display_name": provider["display_name"],
        "level": level_for(effective_xp)["name"],
        "created_at": provider["created_at"],
        "xp": effective_xp,
        "base_xp": provider["xp"],
        "participation_xp": participation["xp"],
        "reputation": effective_reputation,
        "base_reputation": provider["reputation"],
        "contribution": contribution,
        "feedback_score": feedback_score,
        "invite_count": invite_count,
        "provider_grade": grade,
        "followed": provider_id in followed_ids,
        "follower_count": int(follower_count or 0),
        "upgrade_plan": upgrade_plan,
        "credibility_passport": source_credibility_passport(grade, contribution, feedback_score, track_record, report_count, int(follower_count or 0), upgrade_plan),
        "radar": calc_user_radar(provider_id),
        "tier_mix": tier_mix,
        "feedback": {
            "useful": feedback.get("useful", 0),
            "doubt": feedback.get("doubt", 0),
        },
        "track_record": track_record,
        "provider_proof": provider_proof_scorecard(grade, contribution, feedback, track_record, report_count, tier_mix),
        "recent": [
            public_rumor(row, can_view(viewer, row, unlocked), stats.get(row["id"]), watched_codes, viewer["id"], viewer)
            for row in recent_rows
        ],
    }


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
    contribution = contribution_for_user(user["id"])
    invite_count = int(user["invite_count"] or 0)
    feedback_score = provider_feedback_score(user["id"])
    participation = participation_totals(user["id"])
    total_xp = int(user["xp"] or 0) + int(participation["xp"] or 0)
    reputation = max(1.0, min(99.0, float(user["reputation"] or 0) + float(participation["reputation"] or 0)))
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "is_guest": user["is_guest"],
        "xp": total_xp,
        "base_xp": user["xp"],
        "participation_xp": participation["xp"],
        "reputation": reputation,
        "base_reputation": user["reputation"],
        "direct_quota": user["direct_quota"],
        "contribution": contribution,
        "contribution_half_life_days": CONTRIBUTION_HALF_LIFE_DAYS,
        "invite_code": user["invite_code"],
        "invite_count": invite_count,
        "feedback_score": feedback_score,
        "provider_grade": provider_grade(total_xp, reputation, contribution, invite_count, feedback_score),
        "upgrade_plan": provider_upgrade_plan(user),
        "created_at": user["created_at"],
        "radar": calc_user_radar(user["id"]) if not user["is_guest"] else None,
    }


def provider_snapshot(submitter_id: int | None) -> dict[str, Any] | None:
    if not submitter_id:
        return None
    rows = query("select id, display_name, xp, reputation, invite_count from users where id = ?", (submitter_id,))
    if not rows:
        return None
    provider = rows[0]
    contribution = contribution_for_user(provider["id"])
    feedback_score = provider_feedback_score(provider["id"])
    grade = provider_grade(provider["xp"], provider["reputation"], contribution, provider["invite_count"] or 0, feedback_score)
    rumor_stats = query(
        """
        select count(*) n, avg(ai_score) avg_score, max(ai_score) top_score
        from rumors
        where submitter_id = ?
        """,
        (provider["id"],),
    )[0]
    follower_count = query("select count(*) n from provider_follows where provider_id = ?", (provider["id"],))[0]["n"]
    return {
        "id": provider["id"],
        "display_name": provider["display_name"],
        "grade": grade,
        "contribution": contribution,
        "feedback_score": feedback_score,
        "rumor_count": int(rumor_stats["n"] or 0),
        "avg_score": round(float(rumor_stats["avg_score"] or 0), 1) if rumor_stats["avg_score"] is not None else None,
        "top_score": int(rumor_stats["top_score"] or 0) if rumor_stats["top_score"] is not None else None,
        "follower_count": int(follower_count or 0),
    }


def rumor_value_verdict(
    ai_score: int,
    provider: dict[str, Any] | None,
    discussion: dict[str, Any],
    moderation: dict[str, Any],
    outcome: dict[str, Any] | None,
    watched: bool,
) -> dict[str, Any]:
    source_score = float((provider or {}).get("grade", {}).get("score", 45.0))
    useful = int(discussion.get("useful") or 0)
    doubt = int(discussion.get("doubt") or 0)
    comments = int(discussion.get("comments") or 0)
    community_bonus = max(-8.0, min(14.0, useful * 1.8 + comments * 0.45 - doubt * 1.4))
    outcome_state = (outcome or {}).get("state", "pending")
    outcome_bonus = {
        "hit": 10.0,
        "valid": 6.0,
        "neutral": 1.5,
        "pending": 0.0,
        "no_code": -1.0,
        "skipped": -1.0,
        "weak": -9.0,
    }.get(outcome_state, 0.0)
    trust_penalty = float(moderation.get("penalty") or 0)
    watch_bonus = 3.0 if watched else 0.0
    value_index = max(
        0.0,
        min(100.0, ai_score * 0.64 + source_score * 0.18 + community_bonus + outcome_bonus + watch_bonus - trust_penalty),
    )
    if value_index >= 86:
        state, label = "prime", "强价值"
    elif value_index >= 72:
        state, label = "track", "值得跟踪"
    elif value_index >= 58:
        state, label = "watch", "可观察"
    else:
        state, label = "uncertain", "低确定性"
    evidence_points = 1
    evidence_points += 1 if provider else 0
    evidence_points += 1 if useful + comments > 0 else 0
    evidence_points += 1 if outcome_state not in {"pending", "no_code", "skipped"} else 0
    evidence_points += 1 if not moderation.get("reports") else 0
    confidence = round(min(100.0, evidence_points / 5 * 100), 1)
    drivers: list[str] = []
    if ai_score >= 86:
        drivers.append("内容评分进入S级")
    elif ai_score >= 72:
        drivers.append("内容评分进入A级")
    if provider:
        drivers.append(f"{provider['display_name']} · {provider['grade']['name']}")
    if useful:
        drivers.append(f"社区有用反馈 {useful} 次")
    if outcome_state in {"hit", "valid", "weak"}:
        drivers.append(f"回测{(outcome or {}).get('label', '')}")
    if moderation.get("reports"):
        drivers.append(f"可信度{moderation.get('trust_state', 'watch')}，已扣 {int(trust_penalty)}")
    if watched:
        drivers.append("命中自选标的")
    return {
        "index": round(value_index, 1),
        "label": label,
        "state": state,
        "confidence": confidence,
        "drivers": drivers[:4],
    }


def rumor_verification_ledger(
    ai_score: int,
    dimensions: dict[str, Any],
    provider: dict[str, Any] | None,
    discussion: dict[str, Any],
    moderation: dict[str, Any],
    outcome: dict[str, Any] | None,
    verdict: dict[str, Any],
    risk: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    useful = int(discussion.get("useful") or 0)
    doubt = int(discussion.get("doubt") or 0)
    comments = int(discussion.get("comments") or 0)
    outcome = outcome or backtest_outcome(None)
    risk = risk or {"level": "clear", "label": "风控清洁", "flags": [], "score_penalty": 0}
    ledger = [
        {
            "key": "content",
            "label": "内容结构",
            "state": "strong" if ai_score >= 72 else "watch" if ai_score >= 55 else "weak",
            "value": ai_score,
            "detail": f"明确度{dimensions.get('specificity', 0)} · 密度{dimensions.get('evidence', 0)} · 可验证{dimensions.get('verifiability', 0)}",
        },
        {
            "key": "source",
            "label": "信息源",
            "state": "strong" if provider and provider["grade"]["score"] >= 62 else "watch" if provider else "unknown",
            "value": provider["grade"]["score"] if provider else None,
            "detail": f"{provider['display_name']} · {provider['grade']['name']}" if provider else "历史样本或匿名来源",
        },
        {
            "key": "community",
            "label": "社区反馈",
            "state": "strong" if useful >= 2 else "weak" if doubt > useful else "watch",
            "value": useful - doubt,
            "detail": f"有用{useful} · 存疑{doubt} · 讨论{comments}",
        },
        {
            "key": "backtest",
            "label": "行情复盘",
            "state": "strong" if outcome["state"] in {"hit", "valid"} else "weak" if outcome["state"] == "weak" else "watch",
            "value": outcome.get("score"),
            "detail": f"{outcome.get('label', '待验证')} · {outcome.get('summary', '')}",
        },
        {
            "key": "trust",
            "label": "可信度",
            "state": "weak" if moderation.get("reports") else "strong",
            "value": max(0, 100 - int(moderation.get("penalty") or 0)),
            "detail": f"{moderation.get('reports', 0)}次举报 · {moderation.get('trust_state', 'clear')}",
        },
        {
            "key": "risk",
            "label": "风控话术",
            "state": "weak" if risk.get("level") == "high" else "watch" if risk.get("level") == "watch" else "strong",
            "value": max(0, 100 - int(risk.get("score_penalty") or 0) * 10),
            "detail": risk.get("summary") or risk.get("label") or "未发现明显高风险措辞",
        },
        {
            "key": "verdict",
            "label": "综合结论",
            "state": verdict.get("state", "watch"),
            "value": verdict.get("index"),
            "detail": f"{verdict.get('label', '可观察')} · 证据完整度{verdict.get('confidence', 0)}%",
        },
    ]
    return ledger


def rumor_decision_brief(
    row: sqlite3.Row,
    verdict: dict[str, Any],
    tasks: list[dict[str, Any]],
    risk: dict[str, Any],
    outcome: dict[str, Any] | None,
    moderation: dict[str, Any],
) -> dict[str, Any]:
    tier = row["ai_tier"]
    score = int(row["ai_score"] or 0)
    outcome = outcome or backtest_outcome(None)
    positives = list(verdict.get("drivers") or [])
    if not positives:
        positives.append(f"内容评分 {tier}{score}，证据完整度 {verdict.get('confidence', 0)}%")
    watch_points = [task["label"] for task in tasks if task.get("status") != "locked"][:3]
    if not watch_points:
        watch_points = ["补充来源链路", "等待社区反馈", "观察行情复盘"]
    risks = []
    if risk.get("level") != "clear":
        risks.append(risk.get("label") or "话术风险")
    if moderation.get("reports"):
        risks.append(f"社区举报 {moderation.get('reports')} 次")
    if outcome.get("state") == "weak":
        risks.append("回测表现偏弱")
    if not risks:
        risks.append("仍需自行核验来源、价格和兑现节奏")
    if verdict.get("state") in {"prime", "track"}:
        action = "进入详情核验关键节点，并加入自选持续跟踪"
    elif risk.get("level") == "high" or moderation.get("reports"):
        action = "先处理风险和来源核验，再决定是否跟踪"
    else:
        action = "补充可验证事实后再提高权重"
    return {
        "label": verdict.get("label", "可观察"),
        "state": verdict.get("state", "watch"),
        "confidence": verdict.get("confidence", 0),
        "summary": f"{row['target']} 当前为{tier}{score}，社区价值指数 {verdict.get('index', 0)}。",
        "positives": positives[:3],
        "watch_points": watch_points,
        "risks": risks[:3],
        "next_action": action,
        "disclaimer": "仅用于社区信息复盘和反馈，不构成投资建议。",
    }


def rumor_score_explanation(
    ai_score: int,
    dimensions: dict[str, Any],
    provider: dict[str, Any] | None,
    discussion: dict[str, Any],
    moderation: dict[str, Any],
    risk: dict[str, Any],
    outcome: dict[str, Any] | None,
    verdict: dict[str, Any],
) -> dict[str, Any]:
    dimension_labels = {
        "specificity": "标的/逻辑明确",
        "evidence": "事实密度",
        "freshness": "时效",
        "source": "来源链路",
        "verifiability": "可验证节点",
        "novelty": "稀缺性",
    }
    normalized = []
    for key, label in dimension_labels.items():
        value = float(dimensions.get(key) or 0)
        max_value = 30.0 if key in {"specificity", "evidence", "verifiability"} else 10.0
        normalized.append((key, label, value, round(min(100.0, value / max_value * 100), 1)))
    strengths = [
        {"key": key, "label": label, "value": value, "detail": f"{score:.0f}%"}
        for key, label, value, score in sorted(normalized, key=lambda item: item[3], reverse=True)
        if score >= 60
    ][:3]
    gaps = [
        {"key": key, "label": label, "value": value, "detail": f"{score:.0f}%"}
        for key, label, value, score in sorted(normalized, key=lambda item: item[3])
        if score < 55
    ][:3]
    useful = int(discussion.get("useful") or 0)
    doubt = int(discussion.get("doubt") or 0)
    outcome = outcome or backtest_outcome(None)
    source_score = float((provider or {}).get("grade", {}).get("score") or 0)
    factors = [
        {"key": "content", "label": "原始内容", "value": ai_score, "state": "strong" if ai_score >= 72 else "watch" if ai_score >= 55 else "weak"},
        {"key": "source", "label": "信息源", "value": round(source_score, 1) if provider else None, "state": "strong" if source_score >= 62 else "watch" if provider else "unknown"},
        {"key": "community", "label": "社区反馈", "value": useful - doubt, "state": "strong" if useful >= 2 else "weak" if doubt > useful else "watch"},
        {"key": "backtest", "label": "回测", "value": outcome.get("score"), "state": "strong" if outcome.get("state") in {"hit", "valid"} else "weak" if outcome.get("state") == "weak" else "watch"},
        {"key": "risk", "label": "风控", "value": max(0, 100 - int(risk.get("score_penalty") or 0) * 10), "state": "weak" if risk.get("level") == "high" else "watch" if risk.get("level") == "watch" else "strong"},
    ]
    next_steps = [item["label"] for item in gaps]
    if risk.get("level") != "clear":
        next_steps.insert(0, "先核验风险话术")
    if not provider:
        next_steps.append("补充来源身份")
    if outcome.get("state") in {"pending", "no_code", "skipped"}:
        next_steps.append("等待行情复盘")
    if not next_steps:
        next_steps = ["进入详情补充反馈", "加入自选跟踪兑现节奏"]
    return {
        "headline": f"{verdict.get('label', '可观察')} · 价值指数 {verdict.get('index', 0)}",
        "summary": f"内容分 {ai_score}，证据完整度 {verdict.get('confidence', 0)}%，有效排序分已叠加来源、社区、回测和风控。",
        "strengths": strengths,
        "gaps": gaps,
        "factors": factors,
        "next_steps": next_steps[:3],
    }


def rumor_consensus_snapshot(row: sqlite3.Row, risk: dict[str, Any], outcome: dict[str, Any] | None) -> dict[str, Any]:
    stocks = rumor_stock_items(row)
    terms = [item["code"] for item in stocks if item.get("code")] + [item["name"] for item in stocks if item.get("name")]
    if row["target"]:
        terms.extend([part for part in re.split(r"[、,，/;；\s]+", row["target"]) if part])
    terms = [term for term in dict.fromkeys(terms) if term]
    if not terms:
        return {
            "state": "isolated",
            "label": "暂无共识",
            "summary": "缺少可聚合标的，暂不能形成社区共识快照。",
            "metrics": {"related": 0, "sources": 0, "high_value": 0, "risk": 0},
            "top_peers": [],
            "next_action": "先补充股票名称或代码。",
        }

    clauses = []
    args: list[Any] = []
    for term in terms[:8]:
        clauses.append("(target like ? or stock_codes like ? or logic like ? or raw_content like ?)")
        args.extend([f"%{term}%"] * 4)
    rows = query(
        f"""
        select id, target, submitter_name, submitter_id, ai_score, ai_tier, logic, raw_content, stock_codes, recommendation_date,
               institution, recommender, key_points, created_at, source
        from rumors
        where id != ? and ({" or ".join(clauses)})
        order by recommendation_date desc, ai_score desc, id desc
        limit 40
        """,
        (row["id"], *args),
    )
    source_ids = {r["submitter_id"] for r in rows if r["submitter_id"]}
    high_rows = [r for r in rows if r["ai_tier"] in {"S", "A"}]
    risk_rows = [r for r in rows if risk_analysis(dict(r))["level"] != "clear"]
    top_peers = [
        {
            "id": r["id"],
            "target": r["target"],
            "tier": r["ai_tier"],
            "score": r["ai_score"],
            "date": r["recommendation_date"],
            "source": r["submitter_name"] or "社区",
        }
        for r in sorted(rows, key=lambda r: (int(r["ai_score"] or 0), r["recommendation_date"] or ""), reverse=True)[:3]
    ]
    related = len(rows)
    high_ratio = round(len(high_rows) / max(1, related) * 100, 1) if related else 0.0
    risk_count = len(risk_rows) + (1 if risk.get("level") != "clear" else 0)
    if related >= 3 and high_ratio >= 45 and risk_count == 0:
        state, label = "confirmed", "多源确认"
        next_action = "优先查看高分同标的线索，并加入自选持续跟踪。"
    elif related >= 2 and risk_count:
        state, label = "divergent", "存在分歧"
        next_action = "先比较同标的线索来源，重点复核风险话术和反向证据。"
    elif related >= 1:
        state, label = "forming", "共识形成中"
        next_action = "进入情报房间补充反馈，观察是否出现更多独立来源。"
    else:
        state, label = "isolated", "孤立线索"
        next_action = "等待更多社区线索，或补充更强来源和验证节点。"
    outcome = outcome or backtest_outcome(None)
    summary = f"相关线索 {related} 条，独立信息源 {len(source_ids)} 位，S/A占比 {high_ratio:.1f}%，风险分歧 {risk_count} 条。"
    if outcome.get("state") in {"hit", "valid", "weak"}:
        summary += f" 当前回测状态：{outcome.get('label')}。"
    return {
        "state": state,
        "label": label,
        "summary": summary,
        "metrics": {
            "related": related,
            "sources": len(source_ids),
            "high_value": len(high_rows),
            "risk": risk_count,
            "high_ratio": high_ratio,
        },
        "top_peers": top_peers,
        "next_action": next_action,
    }


def public_rumor(
    row: sqlite3.Row,
    unlocked: bool,
    discussion: dict[str, Any] | None = None,
    watched_codes: set[str] | None = None,
    viewer_id: int | None = None,
    viewer: sqlite3.Row | None = None,
    slim: bool = False,
) -> dict[str, Any]:
    hidden = not unlocked
    stocks = rumor_stock_items(row)
    watch_hits = [item for item in stocks if item["code"] and watched_codes and item["code"] in watched_codes]
    watched = bool(watch_hits)
    # slim mode: skip all heavy computations, only return feed-list fields
    if slim:
        ret1 = row["ret_t1_1"] if "ret_t1_1" in row.keys() else None
        avg_ret: float | None = None
        if ret1 is not None:
            valid = [float(v) for v in [ret1, row["ret_t1_5"], row["ret_t1_20"]] if v is not None]  # type: ignore[union-attr]
            avg_ret = sum(valid) / len(valid) if valid else None
        if ret1 is None:
            outcome: dict[str, Any] = {"state": "pending", "label": "待回测", "score": None, "avg_ret": None, "best_ret": None, "max_drawdown": None, "summary": ""}
        else:
            best_ret = max(float(v) for v in [ret1, row["ret_t1_5"], row["ret_t1_20"]] if v is not None)  # type: ignore[union-attr]
            sig = row["signal_value"] if "signal_value" in row.keys() else None
            score_val = float(sig) if sig is not None else None
            if (score_val and score_val >= 80) or (avg_ret and avg_ret >= 0.08) or best_ret >= 0.15:
                state, label = "hit", "强命中"
            elif (score_val and score_val >= 60) or (avg_ret and avg_ret >= 0.02):
                state, label = "valid", "有效"
            elif (score_val and score_val <= 25) or (avg_ret and avg_ret <= -0.04):
                state, label = "weak", "偏弱"
            else:
                state, label = "neutral", "待观察"
            outcome = {"state": state, "label": label, "score": score_val, "avg_ret": avg_ret, "best_ret": best_ret, "max_drawdown": None, "summary": ""}
        return {
            "id": row["id"],
            "submitter_name": row["submitter_name"],
            "target": row["target"] if unlocked else mask_target(row["target"]),
            "stock_codes": stocks if unlocked else [],
            "logic": row["logic"] if unlocked else "已锁定。分享同等价值消息或提升等级后查看。",
            "recommendation_date": row["recommendation_date"],
            "ai_score": row["ai_score"],
            "ai_tier": row["ai_tier"],
            "outcome": outcome,
            "watched": watched,
            "unlocked": unlocked,
            "hidden": hidden,
            "rights": {},
        }
    discussion = discussion or {
        "comments": 0,
        "useful": 0,
        "doubt": 0,
        "heat": 0,
        "my_reactions": [],
        "moderation": {
            "reports": 0,
            "report_reasons": {},
            "report_labels": [],
            "my_reports": [],
            "trust_state": "clear",
            "penalty": 0,
        },
    }
    moderation = discussion.get("moderation") or {}
    provider = provider_snapshot(row["submitter_id"])
    outcome = backtest_outcomes_for([row["id"]]).get(row["id"])
    watched = bool(watch_hits)
    verdict = rumor_value_verdict(int(row["ai_score"] or 0), provider, discussion, moderation, outcome, watched)
    dimension_scores = rumor_dimension_scores(row, provider, outcome)
    dimensions = score_text(dict(row))["dimensions"]
    risk = risk_analysis(dict(row)) if unlocked else {"level": "clear", "label": "解锁后核验", "flags": [], "score_penalty": 0, "summary": "解锁后展示完整话术风控。"}
    ledger = rumor_verification_ledger(int(row["ai_score"] or 0), dimensions, provider, discussion, moderation, outcome, verdict, risk)
    tasks = (
        verification_tasks(dict(row), dimensions, risk, outcome)
        if unlocked
        else [
            {
                "key": "unlock_to_verify",
                "label": "解锁后参与反馈",
                "priority": "medium",
                "status": "locked",
                "detail": "解锁完整内容后可查看订单、来源、风控和行情复盘任务。",
                "action": "解锁",
            }
        ]
    )
    bounties = verification_bounties(tasks, discussion, unlocked)
    decision = rumor_decision_brief(row, verdict, tasks, risk, outcome, moderation)
    score_explanation = rumor_score_explanation(int(row["ai_score"] or 0), dimensions, provider, discussion, moderation, risk, outcome, verdict)
    consensus: dict[str, Any] = {}  # skipped — not shown in simplified detail view
    access = tier_access(viewer, row["ai_tier"]) if viewer is not None else None
    if unlocked:
        unlock_path = {
            "state": "open",
            "label": "已开放",
            "summary": "你已可查看完整内容。",
            "actions": [{"key": "detail", "label": "查看详情", "view": "detail"}],
        }
    elif viewer is not None and bool(viewer["is_guest"]):
        unlock_path = {
            "state": "guest",
            "label": "注册后解锁",
            "summary": "注册后获得成长记录、直看额度，并可通过投稿交换高价值情报。",
            "actions": [
                {"key": "register", "label": "注册账号", "view": "register"},
                {"key": "submit", "label": "贡献线索", "view": "submit"},
            ],
        }
    elif viewer is not None and int(viewer["direct_quota"] or 0) > 0:
        unlock_path = {
            "state": "direct",
            "label": "可直看",
            "summary": f"你还有 {int(viewer['direct_quota'] or 0)} 次直看额度，可直接打开这条{row['ai_tier']}级情报。",
            "actions": [
                {"key": "direct_unlock", "label": "使用额度直看", "view": "unlock"},
                {"key": "submit", "label": "投稿补额度", "view": "submit"},
            ],
        }
    else:
        gap = unlock_gap(viewer, row["ai_tier"]) if viewer is not None else {"xp_gap": 0, "contribution_gap": 0, "hint": ""}
        unlock_path = {
            "state": "earn",
            "label": "需提升后解锁",
            "summary": f"还差 {gap['xp_gap']} XP 或 {gap['contribution_gap']} 贡献度；也可邀请或投稿获得直看额度。",
            "actions": [
                {"key": "submit", "label": "投稿交换", "view": "submit"},
                {"key": "invite", "label": "邀请拿额度", "view": "rank"},
            ],
        }
    forensic_content = "\n".join(
        str(part or "")
        for part in (
            row["target"] if unlocked else mask_target(row["target"]),
            row["logic"] if unlocked else "",
            row["raw_content"] if unlocked else "",
            row["recommendation_date"],
            row["ai_tier"],
            row["ai_score"],
        )
    )
    rights = rumor_rights_fingerprint(int(row["id"]), viewer_id, unlocked)
    rights["forensic_signature"] = rumor_forensic_signature(int(row["id"]), viewer_id, unlocked, forensic_content)
    return {
        "id": row["id"],
        "submitter_name": row["submitter_name"],
        "target": row["target"] if unlocked else mask_target(row["target"]),
        "stock_codes": stocks if unlocked else [],
        "logic": row["logic"] if unlocked else "已锁定。分享同等价值消息或提升等级后查看。",
        "raw_content": row["raw_content"] if unlocked else "",
        "institution": row["institution"] if unlocked else "",
        "recommender": row["recommender"] if unlocked else "",
        "key_points": json.loads(row["key_points"] or "[]") if unlocked else [],
        "recommendation_date": row["recommendation_date"],
        "ai_score": row["ai_score"],
        "ai_tier": row["ai_tier"],
        "ai_reasons": reason_list(row["ai_reasons"]),
        "score_dimensions": dimensions,
        "risk_analysis": risk,
        "created_at": row["created_at"],
        "source": row["source"],
        "provider": provider,
        "discussion": discussion,
        "moderation": moderation,
        "effective_score": max(0, int(row["ai_score"] or 0) - int(moderation.get("penalty") or 0)),
        "value_verdict": verdict,
        "dimension_scores": dimension_scores,
        "score_explanation": score_explanation,
        "verification_ledger": ledger,
        "verification_tasks": tasks,
        "verification_bounties": bounties,
        "decision_brief": decision,
        "consensus_snapshot": consensus,
        "outcome": outcome,
        "watched": watched,
        "watch_hits": watch_hits if unlocked else [],
        "unlocked": unlocked,
        "hidden": hidden,
        "unlock_path": unlock_path,
        "rights": rights,
    }


def mask_target(target: str) -> str:
    parts = re.split(r"([、,，/ ])", target)
    return "".join(p if re.match(r"[、,，/ ]", p) else (p[:1] + "**") for p in parts)


def user_unlocked_ids(user: sqlite3.Row) -> set[int]:
    rows = query("select rumor_id from unlocks where user_id = ?", (user["id"],))
    return {r["rumor_id"] for r in rows}


def is_historic_rumor(row: sqlite3.Row) -> bool:
    return str(row["recommendation_date"] or "")[:10] < active_feed_date()


def watchlist_for_user(user_id: int) -> list[dict[str, Any]]:
    rows = query("select code, name, created_at from watchlist where user_id = ? order by created_at desc", (user_id,))
    return [dict(row) for row in rows]


def watched_codes_for_user(user_id: int) -> set[str]:
    return {row["code"] for row in query("select code from watchlist where user_id = ?", (user_id,))}


def starter_watchlist_suggestions(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    watched = watched_codes_for_user(user_id)
    suggestion_map: dict[str, dict[str, Any]] = {}
    suggestion_rows = query(
        """
        select *
        from rumors
        order by ai_score desc, recommendation_date desc, id desc
        limit 80
        """
    )
    for row in suggestion_rows:
        for stock in rumor_stock_items(row):
            code = stock["code"]
            if not code or code in watched:
                continue
            item = suggestion_map.setdefault(
                code,
                {
                    "code": code,
                    "name": stock["name"] or code,
                    "rumor_count": 0,
                    "top_score": 0,
                    "tier_mix": {},
                    "latest_date": row["recommendation_date"],
                    "reason": "社区热议标的",
                },
            )
            item["rumor_count"] += 1
            item["top_score"] = max(int(item["top_score"] or 0), int(row["ai_score"] or 0))
            item["latest_date"] = max(str(item["latest_date"] or ""), str(row["recommendation_date"] or ""))
            item["tier_mix"][row["ai_tier"]] = item["tier_mix"].get(row["ai_tier"], 0) + 1
            if row["ai_tier"] in {"S", "A"}:
                item["reason"] = "高价值线索命中"
    return sorted(suggestion_map.values(), key=lambda item: (item["top_score"], item["rumor_count"], item["latest_date"]), reverse=True)[:limit]


def followed_provider_ids(user_id: int) -> set[int]:
    return {int(row["provider_id"]) for row in query("select provider_id from provider_follows where follower_id = ?", (user_id,))}


def followed_provider_summary(user_id: int) -> dict[str, Any]:
    rows = query(
        """
        select u.id, u.display_name, u.xp, u.reputation, u.invite_count, f.created_at,
               count(r.id) rumor_count, max(r.recommendation_date) latest_date, max(r.ai_score) top_score
        from provider_follows f
        join users u on u.id = f.provider_id
        left join rumors r on r.submitter_id = u.id
        where f.follower_id = ?
        group by u.id
        order by f.created_at desc
        """,
        (user_id,),
    )
    items = []
    for row in rows:
        contribution = contribution_for_user(row["id"])
        feedback_score = provider_feedback_score(row["id"])
        items.append(
            {
                **dict(row),
                "contribution": contribution,
                "feedback_score": feedback_score,
                "provider_grade": provider_grade(row["xp"], row["reputation"], contribution, row["invite_count"] or 0, feedback_score),
            }
        )
    followed_ids = {int(item["id"]) for item in items}
    suggestion_rows = query(
        """
        select u.id, u.display_name, u.xp, u.reputation, u.invite_count,
               count(r.id) rumor_count, max(r.recommendation_date) latest_date,
               max(r.ai_score) top_score, avg(r.ai_score) avg_score
        from users u
        join rumors r on r.submitter_id = u.id
        where u.is_guest = 0 and u.id != ?
        group by u.id
        having count(r.id) > 0
        order by max(r.ai_score) desc, avg(r.ai_score) desc, count(r.id) desc
        limit 12
        """,
        (user_id,),
    )
    suggestions = []
    for row in suggestion_rows:
        if int(row["id"]) in followed_ids:
            continue
        contribution = contribution_for_user(row["id"])
        feedback_score = provider_feedback_score(row["id"])
        grade = provider_grade(row["xp"], row["reputation"], contribution, row["invite_count"] or 0, feedback_score)
        reason = "近期高分线索"
        if grade["score"] >= 82:
            reason = "王牌信息源"
        elif feedback_score > 0:
            reason = "社区正反馈"
        suggestions.append(
            {
                **dict(row),
                "contribution": contribution,
                "feedback_score": feedback_score,
                "provider_grade": grade,
                "reason": reason,
            }
        )
        if len(suggestions) >= 5:
            break
    candidates = items + suggestions
    top_source = max(candidates, key=lambda item: float(item.get("provider_grade", {}).get("score") or 0), default=None)
    trusted_count = sum(1 for item in candidates if float(item.get("provider_grade", {}).get("score") or 0) >= 38)
    source_board = {
        "headline": "先关注可信信息源，再看个人来源流",
        "trusted_count": trusted_count,
        "followed_count": len(items),
        "suggested_count": len(suggestions),
        "top_source": {
            "id": top_source["id"],
            "display_name": top_source["display_name"],
            "score": top_source["provider_grade"]["score"],
            "grade": top_source["provider_grade"]["name"],
            "reason": top_source.get("reason") or "已关注来源",
        }
        if top_source
        else None,
        "upgrade_action": {
            "key": "source_upgrade",
            "label": "提升我的源分",
            "view": "rank",
            "detail": "投稿、反馈、邀请和社区正反馈都会进入信息源等级。",
        },
    }
    return {
        "items": items,
        "suggestions": suggestions,
        "total": len(items),
        "suggestion_total": len(suggestions),
        "rumor_hits": sum(int(item["rumor_count"] or 0) for item in items),
        "source_board": source_board,
    }


def personalized_activity_feed(user: sqlite3.Row, limit: int = 12) -> dict[str, Any]:
    limit = max(1, min(30, limit))
    user_id = user["id"]
    unlocked = user_unlocked_ids(user)
    watched_codes = watched_codes_for_user(user_id)
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def add_rumor_signal(kind: str, row: sqlite3.Row, title: str, body: str, priority: float, action: dict[str, Any] | None = None) -> None:
        key = (kind, int(row["id"]))
        if key in seen:
            return
        seen.add(key)
        items.append(
            {
                "kind": kind,
                "title": title,
                "body": body,
                "created_at": row["created_at"],
                "priority": priority,
                "rumor_id": row["id"],
                "rumor": public_rumor(row, can_view(user, row, unlocked), watched_codes=watched_codes, viewer_id=user["id"], viewer=user),
                "action": action or {"label": "查看线索", "view": "feed"},
            }
        )

    for watch in watchlist_for_user(user_id)[:8]:
        rows = query(
            """
            select *
            from rumors
            where stock_codes like ? or target like ?
            order by recommendation_date desc, ai_score desc, id desc
            limit 2
            """,
            (f"%{watch['code']}%", f"%{watch['name']}%"),
        )
        for row in rows:
            add_rumor_signal(
                "watch_hit",
                row,
                f"{watch['name']} 出现新线索",
                f"{row['ai_tier']}级 {row['ai_score']}分 · {row['submitter_name']}",
                90 + float(row["ai_score"] or 0) / 10,
                {"label": "看自选", "view": "feed", "query": watch["code"] or watch["name"], "watch": True},
            )

    followed_ids = sorted(followed_provider_ids(user_id))
    if followed_ids:
        placeholders = ",".join("?" for _ in followed_ids)
        rows = query(
            f"""
            select r.*
            from rumors r
            where r.submitter_id in ({placeholders})
            order by r.recommendation_date desc, r.id desc
            limit ?
            """,
            (*followed_ids, min(10, limit)),
        )
        for row in rows:
            add_rumor_signal(
                "followed_source",
                row,
                f"{row['submitter_name']} 更新了情报",
                f"{row['ai_tier']}级 {row['ai_score']}分 · {row['recommendation_date']}",
                80 + float(row["ai_score"] or 0) / 10,
                {"label": "只看关注源", "view": "feed", "followed": True},
            )

    comment_rows = query(
        """
        select c.created_at activity_at, c.display_name actor, c.content comment_content, r.*
        from rumor_comments c
        join rumors r on r.id = c.rumor_id
        where r.submitter_id = ? and c.user_id != ?
        order by c.id desc
        limit 5
        """,
        (user_id, user_id),
    )
    for row in comment_rows:
        add_rumor_signal(
            "discussion",
            row,
            f"{row['actor']} 补充了反馈",
            str(row["comment_content"] or "")[:80],
            75,
            {"label": "看讨论", "view": "detail", "rumor_id": row["id"]},
        )
        items[-1]["created_at"] = row["activity_at"]

    if not bool(user["is_guest"]):
        plan = provider_upgrade_plan(user)
        next_mission = next((item for item in growth_missions(user) if not item["completed"]), None)
        if next_mission:
            items.append(
                {
                    "kind": "growth_tip",
                    "title": next_mission["title"],
                    "body": f"{next_mission['reward']} · 当前{plan['current']['name']} {plan['current']['score']}源分",
                    "created_at": now_iso(),
                    "priority": 45,
                    "action": {"label": "去完成", "view": next_mission.get("cta_view", "feed")},
                }
            )

    if len(items) < 4:
        rows = query(
            """
            select *
            from rumors
            order by ai_score desc, recommendation_date desc, id desc
            limit ?
            """,
            (4 - len(items),),
        )
        for row in rows:
            add_rumor_signal(
                "community_pick",
                row,
                "社区高分样本",
                f"{row['ai_tier']}级 {row['ai_score']}分 · 用来校准什么是高价值线索",
                35 + float(row["ai_score"] or 0) / 10,
                {"label": "查看样本", "view": "feed"},
            )

    items.sort(key=lambda item: (item.get("priority", 0), item.get("created_at", "")), reverse=True)
    for item in items:
        item.pop("priority", None)
    return {
        "items": items[:limit],
        "total": len(items[:limit]),
        "personalized": bool(watched_codes or followed_ids),
        "watch_hits": sum(1 for item in items if item["kind"] == "watch_hit"),
        "followed_hits": sum(1 for item in items if item["kind"] == "followed_source"),
    }


def rumor_stock_items(row: sqlite3.Row | dict[str, Any]) -> list[dict[str, str]]:
    raw = row["stock_codes"] if isinstance(row, sqlite3.Row) else row.get("stock_codes", "[]")
    try:
        items = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        items = []
    if not isinstance(items, list):
        return []
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        code = normalize_stock_code(str(item.get("code") or ""))
        if name or code:
            cleaned.append({"name": name or code, "code": code})
    return cleaned


def watch_summary(user_id: int) -> dict[str, Any]:
    user = query("select * from users where id = ?", (user_id,))[0]
    privilege = watchlist_view_privilege(user)
    items = watchlist_for_user(user_id)
    watched = {item["code"] for item in items if item["code"]}
    total_hits = 0
    enriched = []
    for item in items:
        code = item["code"]
        name = item["name"]
        aggregate = query(
            """
            select count(*) n, max(recommendation_date) latest_date, max(ai_score) top_score
            from rumors
            where stock_codes like ? or target like ?
            """,
            (f"%{code}%", f"%{name}%"),
        )[0]
        recent_rows = query(
            """
            select *
            from rumors
            where stock_codes like ? or target like ?
            order by recommendation_date desc, ai_score desc, id desc
            limit 5
            """,
            (f"%{code}%", f"%{name}%"),
        )
        tier_mix: dict[str, int] = {}
        for row in recent_rows:
            tier_mix[row["ai_tier"]] = tier_mix.get(row["ai_tier"], 0) + 1
        outcomes = backtest_outcomes_for([row["id"] for row in recent_rows])
        reports = report_stats([row["id"] for row in recent_rows])
        outcome_mix: dict[str, int] = {}
        risk_count = 0
        for row in recent_rows:
            outcome = outcomes.get(row["id"], backtest_outcome(None))
            outcome_mix[outcome["state"]] = outcome_mix.get(outcome["state"], 0) + 1
            if reports.get(row["id"], {}).get("reports", 0):
                risk_count += 1
        latest = recent_rows[0] if recent_rows else None
        latest_signal = None
        if latest:
            latest_report = reports.get(latest["id"], {})
            latest_signal = {
                "id": latest["id"],
                "date": latest["recommendation_date"],
                "tier": latest["ai_tier"],
                "score": latest["ai_score"],
                "submitter_name": latest["submitter_name"],
                "outcome": outcomes.get(latest["id"], backtest_outcome(None)),
                "trust_state": latest_report.get("trust_state", "clear"),
                "reports": latest_report.get("reports", 0),
            }
        hit = aggregate
        count = int(hit["n"] or 0)
        total_hits += count
        top_score = int(hit["top_score"] or 0)
        if risk_count:
            alert_level = "risk"
        elif top_score >= 86 or tier_mix.get("S", 0):
            alert_level = "hot"
        elif count:
            alert_level = "active"
        else:
            alert_level = "quiet"
        enriched.append(
            {
                **item,
                "rumor_count": count,
                "latest_date": hit["latest_date"],
                "top_score": top_score,
                "tier_mix": tier_mix,
                "outcome_mix": outcome_mix,
                "risk_count": risk_count,
                "alert_level": alert_level,
                "latest_signal": latest_signal,
            }
        )
    suggestions = starter_watchlist_suggestions(user_id, 5)
    hot_items = [item for item in enriched if item["alert_level"] == "hot"]
    risk_items = [item for item in enriched if item["alert_level"] == "risk"]
    active_items = [item for item in enriched if item["alert_level"] == "active"]
    top_item = sorted(enriched, key=lambda item: (item["top_score"], item["rumor_count"], item.get("latest_date") or ""), reverse=True)[0] if enriched else None
    if not enriched:
        headline = "尚未建立自选情报流"
        action = {"key": "add_suggested", "label": "加入推荐自选", "query": suggestions[0]["code"] if suggestions else "", "detail": "先关注一个社区热议标的，首页会自动聚合命中线索。"}
    elif risk_items:
        headline = f"{risk_items[0]['name']} 出现待复核风险"
        action = {"key": "review_risk", "label": "查看风险线索", "query": risk_items[0]["code"] or risk_items[0]["name"], "detail": "优先查看被举报或风险升高的自选线索。"}
    elif hot_items:
        headline = f"{hot_items[0]['name']} 命中高价值线索"
        action = {"key": "open_hot", "label": "查看高价值命中", "query": hot_items[0]["code"] or hot_items[0]["name"], "detail": f"最高 {hot_items[0]['top_score']} 分，适合进入详情核验。"}
    elif active_items:
        headline = f"{active_items[0]['name']} 有新线索流入"
        action = {"key": "open_active", "label": "查看自选动态", "query": active_items[0]["code"] or active_items[0]["name"], "detail": "按自选过滤情报流，集中处理最新变化。"}
    else:
        headline = "自选暂未命中社区线索"
        action = {"key": "browse_suggestions", "label": "看看推荐自选", "query": suggestions[0]["code"] if suggestions else "", "detail": "可先加入社区高分标的，建立个人信号流。"}
    digest = {
        "headline": headline,
        "state": "risk" if risk_items else "hot" if hot_items else "active" if active_items else "quiet",
        "total_watches": len(enriched),
        "rumor_hits": total_hits,
        "hot_count": len(hot_items),
        "risk_count": len(risk_items),
        "active_count": len(active_items),
        "top_item": top_item,
        "action": action,
        "rights_fingerprint": hashlib.sha256(f"watch:{user_id}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }
    return {
        "items": enriched,
        "suggestions": suggestions,
        "total": len(enriched),
        "suggestion_total": len(suggestions),
        "rumor_hits": total_hits,
        "digest": digest,
        "view_privilege": privilege,
    }


def watchlist_history(user: sqlite3.Row) -> dict[str, Any]:
    if bool(user["is_guest"]):
        raise HTTPException(401, "请先登录后使用自选股")
    watches = watchlist_for_user(user["id"])
    unlocked = user_unlocked_ids(user)
    watched_codes = watched_codes_for_user(user["id"])
    items: list[dict[str, Any]] = []
    total_signals = 0
    for watch in watches:
        code = str(watch["code"] or "")
        name = str(watch["name"] or code)
        rows = query(
            """
            select *
            from rumors
            where stock_codes like ? or target like ?
            order by recommendation_date desc, created_at desc, id desc
            limit 300
            """,
            (f"%{code}%", f"%{name}%"),
        )
        stats = discussion_stats([row["id"] for row in rows], user["id"])
        signals = [
            public_rumor(row, can_view(user, row, unlocked), stats.get(row["id"]), watched_codes, user["id"], user, slim=True)
            for row in rows
        ]
        total_signals += len(signals)
        latest = signals[0] if signals else None
        items.append(
            {
                "code": code,
                "name": name,
                "created_at": watch["created_at"],
                "signal_count": len(signals),
                "latest_date": latest.get("recommendation_date") if latest else None,
                "top_score": max((int(signal.get("ai_score") or 0) for signal in signals), default=0),
                "signals": signals,
            }
        )
    items.sort(key=lambda item: (item.get("latest_date") or "", item.get("created_at") or ""), reverse=True)
    return {
        "items": items,
        "total": len(items),
        "signal_total": total_signals,
        "rights_envelope": rights_envelope("watchlist-history", user["id"], f"{len(items)}:{total_signals}"),
    }


def topic_radar(day: str) -> dict[str, Any]:
    rows = query(
        """
        select target, stock_codes, logic, raw_content, institution, submitter_name, ai_score, ai_tier
        from rumors
        where recommendation_date = ?
        """,
        (day,),
    )
    theme_words = ("订单", "涨价", "合作", "中标", "产能", "AI", "国产替代", "光模块", "业绩", "并购", "政策", "客户", "验证")
    themes: dict[str, dict[str, Any]] = {}
    stocks: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for row in rows:
        text = " ".join(str(row[k] or "") for k in ("target", "logic", "raw_content", "institution"))
        for word in theme_words:
            if word not in text:
                continue
            item = themes.setdefault(word, {"name": word, "count": 0, "top_score": 0, "tiers": {}})
            item["count"] += 1
            item["top_score"] = max(item["top_score"], int(row["ai_score"] or 0))
            item["tiers"][row["ai_tier"]] = item["tiers"].get(row["ai_tier"], 0) + 1
        for stock in rumor_stock_items(row):
            key = stock["code"] or stock["name"]
            if not key:
                continue
            item = stocks.setdefault(key, {"code": stock["code"], "name": stock["name"], "count": 0, "top_score": 0})
            item["count"] += 1
            item["top_score"] = max(item["top_score"], int(row["ai_score"] or 0))
        source = row["submitter_name"] or "社区"
        src = sources.setdefault(source, {"name": source, "count": 0, "avg_score_total": 0})
        src["count"] += 1
        src["avg_score_total"] += int(row["ai_score"] or 0)
    hot_sources = []
    for item in sources.values():
        hot_sources.append({"name": item["name"], "count": item["count"], "avg_score": round(item["avg_score_total"] / item["count"], 1)})
    return {
        "themes": sorted(themes.values(), key=lambda x: (x["count"], x["top_score"]), reverse=True)[:8],
        "stocks": sorted(stocks.values(), key=lambda x: (x["count"], x["top_score"]), reverse=True)[:8],
        "sources": sorted(hot_sources, key=lambda x: (x["count"], x["avg_score"]), reverse=True)[:6],
    }


def daily_brief(day: str, user_id: int) -> dict[str, Any]:
    tier_rows = query(
        """
        select ai_tier, count(*) n, max(ai_score) top_score
        from rumors
        where recommendation_date = ?
        group by ai_tier
        """,
        (day,),
    )
    tier_mix = {row["ai_tier"]: {"count": int(row["n"]), "top_score": int(row["top_score"] or 0)} for row in tier_rows}
    radar = topic_radar(day)
    watch = watch_summary(user_id)
    top_theme = radar["themes"][0] if radar["themes"] else None
    top_stock = radar["stocks"][0] if radar["stocks"] else None
    s_count = tier_mix.get("S", {}).get("count", 0)
    a_count = tier_mix.get("A", {}).get("count", 0)
    headline_bits = []
    if top_theme:
        headline_bits.append(f"{top_theme['name']}最热")
    if top_stock:
        headline_bits.append(f"{top_stock['name']}被反复提及")
    headline = "，".join(headline_bits) or "暂无集中热点"
    bullets = [
        f"今日 S/A 级线索 {s_count + a_count} 条，其中 S 级 {s_count} 条。",
        f"主题雷达覆盖 {len(radar['themes'])} 个催化词、{len(radar['stocks'])} 个热议标的。",
        f"自选命中 {watch['rumor_hits']} 条，关注标的 {watch['total']} 个。",
    ]
    actions = [
        {"label": "看S级", "query": "", "tier": "S", "view": "feed"},
        {"label": "看热点", "query": top_theme["name"] if top_theme else "", "tier": "", "view": "feed"},
        {"label": "补线索", "query": "", "tier": "", "view": "submit"},
    ]
    risk_notes = []
    if s_count + a_count == 0:
        risk_notes.append("今日高分线索偏少，优先等待更多社区验证。")
    if watch["total"] == 0:
        risk_notes.append("尚未关注自选标的，建议先建立个人信号流。")
    if not risk_notes:
        risk_notes.append("高分线索仍需结合回测、讨论和后续验证，不构成投资建议。")
    return {
        "headline": headline,
        "bullets": bullets,
        "actions": actions,
        "risk_notes": risk_notes,
    }


def daily_workflow(day: str, user: sqlite3.Row) -> dict[str, Any]:
    radar = topic_radar(day)
    watch = watch_summary(user["id"])
    top_theme = radar["themes"][0] if radar["themes"] else None
    top_stock = radar["stocks"][0] if radar["stocks"] else None
    high = query(
        """
        select *
        from rumors
        where recommendation_date = ? and ai_tier in ('S', 'A')
        order by ai_score desc, id desc
        limit 1
        """,
        (day,),
    )
    focus_target = high[0]["target"] if high else (top_stock or {}).get("name") or (top_theme or {}).get("name") or "高分线索"
    focus_query = high[0]["target"] if high else (top_stock or top_theme or {}).get("name", "")
    steps = [
        {
            "key": "screen",
            "label": "先筛选",
            "title": f"看 {focus_target}",
            "detail": "从 S/A 级、热点主题和信息源评分开始，先判断是否值得花时间。",
            "metric": f"{len(high)}条高价值" if high else f"{len(radar['themes'])}个热点",
            "view": "feed",
            "query": focus_query,
            "tier": high[0]["ai_tier"] if high else "",
            "action": "打开筛选",
        },
        {
            "key": "verify",
            "label": "再反馈",
            "title": "查来源、证据和风险",
            "detail": "进入详情看评分解释、共识、悬赏和风控，优先补可验证事实。",
            "metric": "评分+共识+悬赏",
            "view": "feed",
            "query": focus_query,
            "tier": "",
            "action": "去反馈",
        },
        {
            "key": "track",
            "label": "后跟踪",
            "title": "加入自选或投稿交换",
            "detail": "把关心的标的加入自选，或提交线索换取更高价值情报。",
            "metric": f"{watch['total']}个自选",
            "view": "submit" if watch["total"] else "feed",
            "query": "",
            "tier": "",
            "action": "建立闭环",
        },
    ]
    return {
        "headline": "三步处理今天的情报：筛选、反馈、跟踪",
        "summary": "把小道消息拆成可复盘工作流，避免只看标题或分数做判断。",
        "steps": steps,
        "rights_fingerprint": hashlib.sha256(f"workflow:{day}:{user['id']}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }


def community_value_proof(day: str, user: sqlite3.Row) -> list[dict[str, Any]]:
    high_value = query(
        """
        select count(*) n, max(ai_score) top_score
        from rumors
        where recommendation_date = ? and ai_tier in ('S', 'A')
        """,
        (day,),
    )[0]
    high_count = int(high_value["n"] or 0)
    top_score = int(high_value["top_score"] or 0)

    backtest_rows = query(
        """
        select b.*
        from backtests b join rumors r on r.id = b.rumor_id
        where b.ret_t1_1 is not null
        order by r.recommendation_date desc, r.ai_score desc
        limit 80
        """
    )
    verified_count = 0
    best_signal = None
    for row in backtest_rows:
        outcome = backtest_outcome(row)
        if outcome["state"] in {"hit", "valid"}:
            verified_count += 1
            if outcome["score"] is not None:
                best_signal = outcome["score"] if best_signal is None else max(best_signal, outcome["score"])

    provider_rows = query("select id, xp, reputation, invite_count from users where is_guest = 0")
    trusted_providers = 0
    top_provider_score = 0.0
    for row in provider_rows:
        grade = provider_grade(
            row["xp"],
            row["reputation"],
            contribution_for_user(row["id"]),
            row["invite_count"] or 0,
            provider_feedback_score(row["id"]),
        )
        score = float(grade["score"])
        if score >= 38:
            trusted_providers += 1
        top_provider_score = max(top_provider_score, score)

    unlocked = user_unlocked_ids(user)
    locked_rows = query(
        """
        select *
        from rumors
        where ai_tier in ('S', 'A')
        order by ai_score desc, recommendation_date desc, id desc
        limit 20
        """
    )
    locked_high_value = [row for row in locked_rows if not can_view(user, row, unlocked)]
    best_locked = locked_high_value[0] if locked_high_value else None

    return [
        {
            "key": "high_value",
            "label": "高价值线索",
            "value": f"{high_count}条",
            "detail": f"今日S/A级，最高{top_score}分" if high_count else "等待社区产出S/A级线索",
            "state": "prime" if high_count else "quiet",
        },
        {
            "key": "verified",
            "label": "回测有效样本",
            "value": f"{verified_count}条",
            "detail": f"最佳信号价值{best_signal:.1f}" if best_signal is not None else "等待本地行情验证",
            "state": "verified" if verified_count else "quiet",
        },
        {
            "key": "sources",
            "label": "可信信息源",
            "value": f"{trusted_providers}位",
            "detail": f"最高源分{top_provider_score:.1f}" if trusted_providers else "注册投稿后进入信息源体系",
            "state": "source" if trusted_providers else "quiet",
        },
        {
            "key": "exchange",
            "label": "可交换机会",
            "value": f"{len(locked_high_value)}条",
            "detail": f"{best_locked['target']} · {best_locked['ai_tier']}{best_locked['ai_score']}" if best_locked else "当前高价值线索已可查看或待刷新",
            "state": "locked" if locked_high_value else "open",
        },
    ]


def detail_rumor(row: sqlite3.Row, stats: dict[str, Any] | None, watched_codes: set[str], user: sqlite3.Row) -> dict[str, Any]:
    stocks = rumor_stock_items(row)
    watched = any(item["code"] and item["code"] in watched_codes for item in stocks)
    return {
        "id": row["id"],
        "submitter_name": row["submitter_name"],
        "target": row["target"],
        "stock_codes": stocks,
        "logic": row["logic"],
        "raw_content": row["raw_content"],
        "institution": row["institution"],
        "recommender": row["recommender"],
        "key_points": json.loads(row["key_points"] or "[]"),
        "recommendation_date": row["recommendation_date"],
        "ai_score": row["ai_score"],
        "ai_tier": row["ai_tier"],
        "created_at": row["created_at"],
        "source": row["source"],
        "discussion": stats
        or {
            "comments": 0,
            "useful": 0,
            "doubt": 0,
            "heat": 0,
            "my_reactions": [],
            "moderation": {"reports": 0, "report_reasons": {}, "report_labels": [], "my_reports": [], "trust_state": "clear", "penalty": 0},
        },
        "watched": watched,
        "unlocked": True,
        "hidden": False,
        "rights": rumor_rights_fingerprint(int(row["id"]), int(user["id"]), True),
    }


def recent_backtest_showcase(day: str, user: sqlite3.Row, limit: int = 10) -> dict[str, Any]:
    rows = query(
        """
        select r.*, b.ret_t1_1, b.ret_t1_5, b.ret_t1_20, b.signal_value, b.max_drawdown_20, b.status
        from rumors r join backtests b on b.rumor_id = r.id
        where r.recommendation_date < ?
          and b.ret_t1_1 is not null
        order by b.ret_t1_1 desc, b.signal_value desc, r.ai_score desc, r.id desc
        limit ?
        """,
        (day, limit),
    )
    if not rows:
        return {"headline": "近3个交易日高分信号", "items": [], "summary": "等待回测样本沉淀"}
    items = []
    watched_codes = watched_codes_for_user(user["id"])
    for row in rows:
        item = public_rumor(row, True, None, watched_codes, user["id"], user, slim=True)
        items.append(
            {
                "rumor": item,
                "signal_value": round(float(row["signal_value"] or 0), 1),
                "ret_t1_1": row["ret_t1_1"],
                "ret_t1_5": row["ret_t1_5"],
                "ret_t1_20": row["ret_t1_20"],
                "max_drawdown_20": row["max_drawdown_20"],
                "outcome": item.get("outcome") or {},
                "return_label": "信号后涨幅",
            }
        )
    return {
        "headline": "近3个交易日高回测信号",
        "summary": "按推荐逻辑、稀缺性、回测分位和观察者评分综合排序；用于证明历史信号质量，实时仍看当日信号。",
        "dates": sorted({item["rumor"]["recommendation_date"] for item in items}, reverse=True),
        "items": items,
    }


def today_signal_board(day: str, user: sqlite3.Row) -> dict[str, Any]:
    unlocked = user_unlocked_ids(user)
    watched_codes = watched_codes_for_user(user["id"])
    ordinary_rows = query(
        """
        select *
        from rumors
        where recommendation_date = ? and ai_tier in ('B', 'C')
        order by ai_score desc, id desc
        limit 4
        """,
        (day,),
    )
    high_rows = query(
        """
        select *
        from rumors
        where recommendation_date = ? and ai_tier in ('S', 'A')
        order by ai_score desc, id desc
        limit 6
        """,
        (day,),
    )
    rows = ordinary_rows + high_rows
    stats = discussion_stats([row["id"] for row in rows], user["id"])
    ordinary = [
        public_rumor(row, can_view(user, row, unlocked), stats.get(row["id"]), watched_codes, user["id"], user)
        for row in ordinary_rows
    ]
    high_value = [
        public_rumor(row, can_view(user, row, unlocked), stats.get(row["id"]), watched_codes, user["id"], user)
        for row in high_rows
    ]
    locked_high = [item for item in high_value if item.get("hidden")]
    return {
        "headline": "当日普通信号与高价值解锁",
        "summary": "先用普通信号了解当日方向，再解锁 S/A 高价值线索做重点核验。",
        "ordinary": ordinary,
        "high_value": high_value,
        "unlock_prompt": {
            "locked_count": len(locked_high),
            "target": locked_high[0]["target"] if locked_high else high_value[0]["target"] if high_value else "",
            "action": {"key": "unlock_today_high", "label": "解锁当日高价值", "view": "feed", "tier": "S"},
        },
    }


def best_pick_verification_plan(
    row: sqlite3.Row,
    risk: dict[str, Any],
    reports: dict[str, Any],
    consensus: dict[str, Any],
    outcome: dict[str, Any],
    adjusted_score: float,
) -> dict[str, Any]:
    row_dict = dict(row)
    tasks = verification_tasks(row_dict, score_text(row_dict)["dimensions"], risk, outcome)
    top_tasks = tasks[:3]
    if adjusted_score >= 82 and risk.get("level") == "clear":
        stage = "priority"
        headline = "优先核验：高分且风控干净"
    elif risk.get("level") != "clear" or reports.get("trust_state") in {"watch", "review"}:
        stage = "risk_first"
        headline = "先排雷：确认风险和举报点"
    elif consensus.get("state") in {"confirmed", "forming"}:
        stage = "consensus"
        headline = "交叉验证：已有同标的信号"
    else:
        stage = "build_evidence"
        headline = "补证据：把线索变成可复盘情报"

    evidence_gap = [
        task["label"]
        for task in top_tasks
        if task.get("status") != "pending" and task.get("key") not in {"market_followup"}
    ][:2]
    if not evidence_gap:
        evidence_gap = ["等待社区反馈", "补充反向风险"]

    steps = [
        {
            "key": task["key"],
            "label": task["label"],
            "priority": task.get("priority", "medium"),
            "detail": task.get("detail", ""),
            "action": task.get("action", "讨论"),
        }
        for task in top_tasks
    ]
    return {
        "stage": stage,
        "headline": headline,
        "summary": f"{consensus.get('label') or '暂无共识'} · {outcome.get('label') or '待验证'} · {risk.get('label') or '风控清洁'}",
        "evidence_gap": evidence_gap,
        "steps": steps,
        "cta": {
            "key": "verify_best",
            "label": "进入反馈",
            "view": "feed",
            "query": row["target"],
            "tier": row["ai_tier"],
        },
    }


def opportunity_summary(day: str, user: sqlite3.Row) -> dict[str, Any]:
    contribution = contribution_for_user(user["id"])
    unlocked = user_unlocked_ids(user)
    watched_codes = watched_codes_for_user(user["id"])
    top_locked_rows = query(
        """
        select *
        from rumors
        where ai_tier in ('S', 'A')
        order by ai_score desc, recommendation_date desc, id desc
        limit 24
        """
    )
    report_map = report_stats([row["id"] for row in top_locked_rows], user["id"])
    outcomes = backtest_outcomes_for([row["id"] for row in top_locked_rows])
    adjusted_candidates = []
    for row in top_locked_rows:
        risk = risk_analysis(dict(row))
        reports = report_map.get(row["id"], {})
        outcome = outcomes.get(row["id"], backtest_outcome(None))
        consensus = rumor_consensus_snapshot(row, risk, outcome)
        adjusted_score = float(row["ai_score"] or 0)
        adjusted_score -= int(risk.get("score_penalty") or 0) * 4
        adjusted_score -= int(reports.get("penalty") or 0)
        adjusted_score += min(6, int(consensus.get("metrics", {}).get("high_value") or 0) * 2)
        if outcome.get("state") in {"hit", "valid"}:
            adjusted_score += 4
        elif outcome.get("state") == "weak":
            adjusted_score -= 5
        adjusted_candidates.append((adjusted_score, row, risk, reports, consensus, outcome))
    adjusted_candidates.sort(key=lambda item: (item[0], int(item[1]["ai_score"] or 0)), reverse=True)
    best = adjusted_candidates[0] if adjusted_candidates else None
    best_pick = None
    if best:
        adjusted_score, row, risk, reports, consensus, outcome = best
        best_pick = {
            "rumor_id": row["id"],
            "target": mask_target(row["target"]) if not can_view(user, row, unlocked) else row["target"],
            "tier": row["ai_tier"],
            "score": int(row["ai_score"] or 0),
            "adjusted_score": round(max(0.0, adjusted_score), 1),
            "risk_label": risk.get("label") or "风控清洁",
            "trust_state": reports.get("trust_state", "clear"),
            "consensus_label": consensus.get("label") or "暂无共识",
            "outcome_label": outcome.get("label") or "待验证",
            "summary": "按原始分、风控扣分、社区举报、共识和回测综合排序。",
            "action": {"key": "open_best", "label": "查看首选", "view": "feed", "query": row["target"], "tier": row["ai_tier"]},
            "verification_plan": best_pick_verification_plan(row, risk, reports, consensus, outcome, adjusted_score),
        }
    locked = [row for row in top_locked_rows if not can_view(user, row, unlocked)]
    direct_unlockable = [row for row in locked if not user["is_guest"] and int(user["direct_quota"] or 0) > 0]
    exchangeable = [
        row
        for row in locked
        if not tier_access(user, row["ai_tier"], contribution)["allowed"] and int(row["ai_score"] or 0) >= 55
    ]
    hot = topic_radar(day)
    top_theme = hot["themes"][0] if hot["themes"] else None
    top_stock = hot["stocks"][0] if hot["stocks"] else None
    watch = watch_summary(user["id"])
    if user["is_guest"]:
        primary = {
            "key": "register",
            "label": "注册保留权益",
            "view": "register",
            "detail": "注册后获得成长记录、邀请奖励和解锁能力。",
        }
    elif direct_unlockable:
        primary = {
            "key": "direct_unlock",
            "label": "直看最高价值",
            "view": "feed",
            "tier": direct_unlockable[0]["ai_tier"],
            "detail": f"可用直看额度解锁 {direct_unlockable[0]['target']} · {direct_unlockable[0]['ai_tier']}{direct_unlockable[0]['ai_score']}",
        }
    elif exchangeable:
        primary = {
            "key": "submit_for_exchange",
            "label": "投稿换取解锁",
            "view": "submit",
            "detail": f"还有 {len(exchangeable)} 条 S/A 线索可通过同级贡献解锁。",
        }
    else:
        primary = {
            "key": "track_hotspot",
            "label": "追踪热点房间",
            "view": "feed",
            "query": (top_theme or top_stock or {}).get("name", ""),
            "detail": "当前高价值线索可见度较好，建议进入热点房间继续核验。",
        }
    cards = [
        {
            "key": "locked",
            "label": "待解高价值",
            "value": len(locked),
            "detail": f"最高 {locked[0]['target']} · {locked[0]['ai_tier']}{locked[0]['ai_score']}" if locked else "当前没有待解 S/A 线索",
            "state": "hot" if locked else "clear",
        },
        {
            "key": "direct",
            "label": "直看机会",
            "value": len(direct_unlockable) if not user["is_guest"] else 0,
            "detail": f"剩余额度 {int(user['direct_quota'] or 0)}" if not user["is_guest"] else "注册后启用直看权益",
            "state": "ready" if direct_unlockable else "quiet",
        },
        {
            "key": "watch",
            "label": "自选命中",
            "value": watch["rumor_hits"],
            "detail": f"已关注 {watch['total']} 个标的",
            "state": "ready" if watch["rumor_hits"] else "quiet",
        },
        {
            "key": "hotspot",
            "label": "最热入口",
            "value": (top_theme or top_stock or {}).get("name", "等待热点"),
            "detail": f"{(top_theme or top_stock or {}).get('count', 0)} 条线索聚合",
            "state": "hot" if (top_theme or top_stock) else "quiet",
        },
    ]
    actions = [
        primary,
        {
            "key": "hotspot",
            "label": "进入热点",
            "view": "feed",
            "query": (top_theme or top_stock or {}).get("name", ""),
            "detail": "按主题或标的筛选情报流。",
        },
        {
            "key": "submit",
            "label": "贡献线索",
            "view": "submit",
            "detail": "提交可验证线索，提升信息源等级并进入交换池。",
        },
    ]
    if not user["is_guest"]:
        actions.append(
            {
                "key": "invite",
                "label": "邀请同行",
                "view": "rank",
                "invite_code": user["invite_code"],
                "detail": "邀请注册可获得 XP 和直看额度。",
            }
        )
    return {
        "headline": primary["detail"],
        "primary_action": primary,
        "best_pick": best_pick,
        "cards": cards,
        "actions": actions,
        "rights_fingerprint": hashlib.sha256(f"opportunity:{user['id']}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }


def frontpage_action_queue(day: str, user: sqlite3.Row) -> list[dict[str, Any]]:
    unlocked = user_unlocked_ids(user)
    watched_codes = watched_codes_for_user(user["id"])
    high_rows = query(
        """
        select *
        from rumors
        where recommendation_date = ? and ai_tier in ('S', 'A')
        order by ai_score desc, id desc
        limit 12
        """,
        (day,),
    )
    locked_high = [row for row in high_rows if not can_view(user, row, unlocked)]
    open_high = [row for row in high_rows if can_view(user, row, unlocked)]
    risk_rows = []
    for row in high_rows:
        risk = risk_analysis(dict(row))
        if risk["level"] != "clear":
            risk_rows.append((row, risk))
    watch = watch_summary(user["id"])
    radar = topic_radar(day)
    top_theme = radar["themes"][0] if radar["themes"] else None

    queue: list[dict[str, Any]] = []
    if locked_high:
        top = locked_high[0]
        queue.append(
            {
                "key": "unlock_high",
                "priority": 1,
                "label": "先解锁最高价值",
                "target": f"{top['target']} · {top['ai_tier']}{top['ai_score']}",
                "detail": "S/A 线索已进入高价值池，优先决定直看、投稿交换或邀请拿额度。",
                "state": "hot",
                "view": "feed",
                "tier": top["ai_tier"],
                "query": "",
                "action": "看高价值",
            }
        )
    elif open_high:
        top = open_high[0]
        queue.append(
            {
                "key": "review_high",
                "priority": 1,
                "label": "复盘已开放高分线索",
                "target": f"{top['target']} · {top['ai_tier']}{top['ai_score']}",
                "detail": "高分内容已可见，进入详情查看决策简报、反馈任务和回测账本。",
                "state": "ready",
                "view": "feed",
                "tier": top["ai_tier"],
                "query": top["target"],
                "action": "打开线索",
            }
        )
    if risk_rows:
        row, risk = risk_rows[0]
        queue.append(
            {
                "key": "risk_review",
                "priority": 2,
                "label": "复核风险话术",
                "target": f"{row['target']} · {risk['label']}",
                "detail": risk["summary"],
                "state": "risk",
                "view": "feed",
                "tier": "",
                "query": row["target"],
                "action": "查风险",
            }
        )
    if watch["rumor_hits"]:
        top_item = watch.get("digest", {}).get("top_item") or {}
        queue.append(
            {
                "key": "watch_hits",
                "priority": 3,
                "label": "处理自选命中",
                "target": top_item.get("target") or f"{watch['rumor_hits']} 条命中",
                "detail": f"{watch['total']} 个自选标的中有 {watch['rumor_hits']} 条社区线索命中。",
                "state": "watch",
                "view": "feed",
                "watch": True,
                "query": "",
                "tier": "",
                "action": "看自选",
            }
        )
    if top_theme:
        queue.append(
            {
                "key": "hot_theme",
                "priority": 4,
                "label": "进入热点房间",
                "target": top_theme["name"],
                "detail": f"{top_theme['count']} 条线索聚合，最高 {top_theme['top_score']} 分。",
                "state": "theme",
                "view": "feed",
                "query": top_theme["name"],
                "tier": "",
                "action": "看热点",
            }
        )
    if user["is_guest"]:
        queue.append(
            {
                "key": "register",
                "priority": 5,
                "label": "注册保留权益",
                "target": "成长记录 + 直看额度 + 邀请奖励",
                "detail": "注册后可累计信息源等级、参与交换池，并复制邀请链接拉新。",
                "state": "member",
                "view": "register",
                "query": "",
                "tier": "",
                "action": "注册",
            }
        )
    else:
        queue.append(
            {
                "key": "submit_gap",
                "priority": 5,
                "label": "补一条可交换线索",
                "target": "提升信息源等级",
                "detail": "提交带来源、时间、标的和验证节点的线索，可获得 XP、源分和交换能力。",
                "state": "contribute",
                "view": "submit",
                "query": "",
                "tier": "",
                "action": "去投稿",
            }
        )
    return sorted(queue, key=lambda item: item["priority"])[:5]


def frontpage_bounty_board(day: str, user: sqlite3.Row, limit: int = 4) -> dict[str, Any]:
    unlocked = user_unlocked_ids(user)
    rows = query(
        """
        select *
        from rumors
        where recommendation_date = ? or ai_tier in ('S', 'A')
        order by case when recommendation_date = ? then 0 else 1 end, ai_score desc, id desc
        limit 24
        """,
        (day, day),
    )
    stats = discussion_stats([row["id"] for row in rows], user["id"])
    items: list[dict[str, Any]] = []
    total_reward = 0
    active_count = 0
    locked_count = 0
    seen: set[int] = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        row_dict = dict(row)
        risk = risk_analysis(row_dict)
        tasks = verification_tasks(row_dict, score_text(row_dict)["dimensions"], risk, backtest_outcome(None))
        bounties = verification_bounties(tasks, stats.get(row["id"], {}), can_view(user, row, unlocked))
        top_bounty = next((item for item in bounties if item.get("state") == "active"), bounties[0] if bounties else None)
        if not top_bounty:
            continue
        reward = int(top_bounty.get("reward_xp") or 0)
        state = str(top_bounty.get("state") or "active")
        if state == "active":
            active_count += 1
        if state == "locked":
            locked_count += 1
        total_reward += reward
        items.append(
            {
                "rumor_id": row["id"],
                "target": row["target"],
                "tier": row["ai_tier"],
                "score": int(row["ai_score"] or 0),
                "task": top_bounty.get("label") or "社区反馈",
                "action": top_bounty.get("cta") or top_bounty.get("action") or "参与",
                "reward_xp": reward,
                "reputation_delta": top_bounty.get("reputation_delta") or 0,
                "state": state,
                "detail": top_bounty.get("detail") or "补充可验证事实、来源或风险点。",
            }
        )
        if len(items) >= limit:
            break
    return {
        "headline": "优先参与高价值线索的社区反馈" if active_count else "解锁高价值线索后参与社区反馈",
        "summary": {
            "active": active_count,
            "locked": locked_count,
            "total_reward_xp": total_reward,
            "items": len(items),
        },
        "items": items,
        "rights_fingerprint": hashlib.sha256(f"bounty:{day}:{user['id']}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }


REPORT_REASON_LABELS = {
    "false_info": "疑似不实",
    "promotion": "软广诱导",
    "duplicate": "重复搬运",
    "stale": "过期失效",
    "abuse": "违规内容",
}


def moderation_penalty(summary: dict[str, Any]) -> int:
    reports = int(summary.get("reports") or 0)
    reasons = summary.get("report_reasons") or {}
    if reports < 2:
        return 0
    penalty = (reports - 1) * 4
    penalty += int(reasons.get("false_info", 0)) * 5
    penalty += int(reasons.get("promotion", 0)) * 3
    penalty += int(reasons.get("abuse", 0)) * 4
    return min(32, penalty)


def moderation_state(summary: dict[str, Any]) -> str:
    reports = int(summary.get("reports") or 0)
    reasons = summary.get("report_reasons") or {}
    if reports >= 5 or int(reasons.get("false_info", 0)) >= 3 or int(reasons.get("abuse", 0)) >= 3:
        return "limited"
    if reports >= 3 or int(reasons.get("false_info", 0)) >= 2:
        return "review"
    if reports >= 1:
        return "watch"
    return "clear"


def report_stats(rumor_ids: list[int], user_id: int | None = None) -> dict[int, dict[str, Any]]:
    if not rumor_ids:
        return {}
    placeholders = ",".join("?" for _ in rumor_ids)
    stats = {
        rumor_id: {
            "reports": 0,
            "report_reasons": {},
            "report_labels": [],
            "my_reports": [],
            "trust_state": "clear",
            "penalty": 0,
        }
        for rumor_id in rumor_ids
    }
    for row in query(
        f"""
        select rumor_id, reason, count(*) n
        from rumor_reports
        where rumor_id in ({placeholders})
        group by rumor_id, reason
        """,
        tuple(rumor_ids),
    ):
        reason = row["reason"]
        count = int(row["n"])
        stats[row["rumor_id"]]["report_reasons"][reason] = count
        stats[row["rumor_id"]]["reports"] += count
    if user_id is not None:
        for row in query(
            f"select rumor_id, reason from rumor_reports where user_id = ? and rumor_id in ({placeholders})",
            (user_id, *rumor_ids),
        ):
            stats[row["rumor_id"]]["my_reports"].append(row["reason"])
    for item in stats.values():
        item["report_labels"] = [
            {"reason": reason, "label": REPORT_REASON_LABELS.get(reason, reason), "count": count}
            for reason, count in sorted(item["report_reasons"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        item["trust_state"] = moderation_state(item)
        item["penalty"] = moderation_penalty(item)
    return stats


def discussion_stats(rumor_ids: list[int], user_id: int | None = None) -> dict[int, dict[str, Any]]:
    if not rumor_ids:
        return {}
    placeholders = ",".join("?" for _ in rumor_ids)
    reports = report_stats(rumor_ids, user_id)
    stats = {
        rumor_id: {
            "comments": 0,
            "useful": 0,
            "doubt": 0,
            "my_reactions": [],
            "moderation": reports.get(rumor_id, {}),
        }
        for rumor_id in rumor_ids
    }
    for row in query(
        f"select rumor_id, count(*) n from rumor_comments where rumor_id in ({placeholders}) group by rumor_id",
        tuple(rumor_ids),
    ):
        stats[row["rumor_id"]]["comments"] = int(row["n"])
    for row in query(
        f"""
        select rumor_id, reaction, count(*) n
        from rumor_reactions
        where rumor_id in ({placeholders})
        group by rumor_id, reaction
        """,
        tuple(rumor_ids),
    ):
        if row["reaction"] == "verify":
            stats[row["rumor_id"]]["useful"] += int(row["n"])
        elif row["reaction"] in ("useful", "doubt"):
            stats[row["rumor_id"]][row["reaction"]] = int(row["n"])
    if user_id is not None:
        for row in query(
            f"select rumor_id, reaction from rumor_reactions where user_id = ? and rumor_id in ({placeholders})",
            (user_id, *rumor_ids),
        ):
            reaction = "useful" if row["reaction"] == "verify" else row["reaction"]
            if reaction in ("useful", "doubt") and reaction not in stats[row["rumor_id"]]["my_reactions"]:
                stats[row["rumor_id"]]["my_reactions"].append(reaction)
    for item in stats.values():
        moderation = item.get("moderation") or {}
        item["heat"] = max(0, int(item["comments"] * 3 + item["useful"] * 2 + item["doubt"] - moderation.get("penalty", 0)))
    return stats


def can_view(user: sqlite3.Row, rumor: sqlite3.Row, unlocked: set[int]) -> bool:
    if rumor["id"] in unlocked or rumor["submitter_id"] == user["id"]:
        return True
    if tier_access(user, rumor["ai_tier"])["allowed"]:
        return True
    privilege = watchlist_view_privilege(user)
    if privilege["limit"] <= 0:
        return False
    watched = watchlist_for_user(user["id"])
    if not watched:
        return False
    terms = []
    args: list[Any] = []
    for item in watched:
        terms.append("(stock_codes like ? or target like ?)")
        args.extend([f"%{item['code']}%", f"%{item['name']}%"])
    if not terms:
        return False
    row = query(
        f"""
        select id
        from rumors
        where ({" or ".join(terms)})
        order by recommendation_date desc, ai_score desc, id desc
        limit ?
        """,
        (*args, privilege["limit"]),
    )
    return int(rumor["id"]) in {int(item["id"]) for item in row}


def watchlist_view_privilege(user: sqlite3.Row) -> dict[str, Any]:
    if bool(user["is_guest"]):
        return {"limit": 0, "label": "注册后启用自选权益", "state": "guest"}
    grade = provider_grade(
        int(user["xp"] or 0),
        float(user["reputation"] or 0),
        contribution_for_user(user["id"]),
        int(user["invite_count"] or 0),
        provider_feedback_score(user["id"]),
    )
    level = level_for(int(user["xp"] or 0))
    level_name = str(level["name"])
    if level_name.startswith(("L4", "L5")) or float(grade["score"]) >= 82:
        return {"limit": 20, "label": "高等级自选全量浏览 20 条", "state": "prime"}
    if level_name.startswith("L3") or float(grade["score"]) >= 62:
        return {"limit": 8, "label": "核心用户自选浏览 8 条", "state": "core"}
    return {"limit": 0, "label": "提升到 L3/核心信息源后开放自选全量浏览", "state": "locked"}


def unlock_similar_match(user_id: int, score: int, tier: str | None = None, stock_codes: str = "", target: str = "") -> dict[str, Any] | None:
    stock_items = stock_items_from_payload(target, stock_codes)
    terms = [item["code"] for item in stock_items if item.get("code")] + [item["name"] for item in stock_items if item.get("name")]
    rows = query(
        """
        select r.*
        from rumors r
        where id not in (select rumor_id from unlocks where user_id = ?)
        and (submitter_id is null or submitter_id != ?)
        and abs(ai_score - ?) <= 12
        order by
            case when ai_tier = ? then 0 else 1 end,
            abs(ai_score - ?) asc,
            ai_score desc,
            recommendation_date desc,
            id desc
        limit 12
        """,
        (user_id, user_id, score, tier or "", score),
    )
    if not rows:
        return None
    reports = report_stats([row["id"] for row in rows], user_id)
    best_row = rows[0]
    best_reason = "分数接近"
    best_rank = (3, abs(int(best_row["ai_score"] or 0) - score), -int(best_row["ai_score"] or 0))
    for row in rows:
        text = " ".join(str(row[key] or "") for key in ("target", "stock_codes", "logic", "raw_content"))
        topic_hit = bool(terms and any(term and term in text for term in terms))
        moderation = reports.get(row["id"], {})
        clean = not moderation.get("reports")
        same_tier = tier and row["ai_tier"] == tier
        rank = (
            0 if same_tier and topic_hit and clean else 1 if same_tier and clean else 2 if clean else 3,
            abs(int(row["ai_score"] or 0) - score),
            -int(row["ai_score"] or 0),
        )
        if rank < best_rank:
            best_row = row
            best_rank = rank
            if same_tier and topic_hit and clean:
                best_reason = "同级同标的且可信度清洁"
            elif same_tier and clean:
                best_reason = "同级且可信度清洁"
            elif clean:
                best_reason = "可信度清洁且分数接近"
            else:
                best_reason = "分数接近"
    execute(
        "insert or ignore into unlocks(user_id, rumor_id, reason, created_at) values (?, ?, 'share_exchange', ?)",
        (user_id, best_row["id"], now_iso()),
    )
    return {
        "item": best_row,
        "reason": best_reason,
        "score_gap": abs(int(best_row["ai_score"] or 0) - score),
        "matched_tier": best_row["ai_tier"],
    }


def unlock_similar(user_id: int, score: int) -> sqlite3.Row | None:
    match = unlock_similar_match(user_id, score)
    return match["item"] if match else None


def unlock_gap(user: sqlite3.Row, tier: str, contribution: float | None = None) -> dict[str, Any]:
    contribution = contribution_for_user(user["id"]) if contribution is None else contribution
    access = tier_access(user, tier, contribution)
    return {
        "tier": tier,
        "allowed": access["allowed"],
        "free": access["free"],
        "min_xp": access["min_xp"],
        "min_contribution": access["min_contribution"],
        "xp_gap": max(0, int(access["min_xp"]) - int(user["xp"] or 0)),
        "contribution_gap": round(max(0.0, float(access["min_contribution"]) - float(contribution)), 1),
        "hint": access["hint"],
    }


def exchange_desk(user: sqlite3.Row, limit: int = 6) -> dict[str, Any]:
    limit = max(1, min(12, limit))
    contribution = contribution_for_user(user["id"])
    unlocked = user_unlocked_ids(user)
    watched_codes = watched_codes_for_user(user["id"])
    rows = query(
        """
        select *
        from rumors
        where submitter_id is null or submitter_id != ?
        order by ai_score desc, recommendation_date desc, id desc
        limit 60
        """,
        (user["id"],),
    )
    locked_rows = [row for row in rows if not can_view(user, row, unlocked)]
    stats = discussion_stats([row["id"] for row in locked_rows[:limit]], user["id"])
    opportunities = []
    for row in locked_rows[:limit]:
        gap = unlock_gap(user, row["ai_tier"], contribution)
        opportunities.append(
            {
                "item": public_rumor(row, False, stats.get(row["id"]), watched_codes, user["id"], user),
                "gap": gap,
                "direct_unlockable": (not bool(user["is_guest"])) and int(user["direct_quota"] or 0) > 0,
                "exchange_score": row["ai_score"],
                "why_locked": "等级未达标，可用直看额度、投稿交换或邀请奖励解锁",
            }
        )

    next_tier = next((unlock_gap(user, tier, contribution) for tier in TIER_ORDER if not unlock_gap(user, tier, contribution)["allowed"]), None)
    actions = []
    if bool(user["is_guest"]):
        actions.append(
            {
                "key": "register",
                "title": "注册保留权益",
                "description": "注册后保存邀请码、成长记录和个人信号流。",
                "view": "auth",
            }
        )
    if int(user["direct_quota"] or 0) > 0 and opportunities:
        actions.append(
            {
                "key": "direct_unlock",
                "title": "使用直看额度",
                "description": f"当前还有 {int(user['direct_quota'] or 0)} 次，可直接打开一条高价值情报。",
                "view": "feed",
            }
        )
    actions.append(
        {
            "key": "submit",
            "title": "投稿交换",
            "description": "提交 B 级以上结构化线索，可匹配解锁同等价值情报。",
            "view": "submit",
        }
    )
    actions.append(
        {
            "key": "invite",
            "title": "邀请拿额度",
            "description": "每邀请 1 位有效新成员，奖励 30 XP 和 10 次直看额度。",
            "view": "rank",
            "invite_code": user["invite_code"] if not bool(user["is_guest"]) else "",
        }
    )
    if next_tier:
        actions.append(
            {
                "key": "upgrade",
                "title": f"冲刺 {next_tier['tier']} 级权限",
                "description": f"还差 {next_tier['xp_gap']} XP 或 {next_tier['contribution_gap']} 贡献度。",
                "view": "submit",
                "gap": next_tier,
            }
        )
    return {
        "resources": {
            "xp": int(user["xp"] or 0),
            "contribution": contribution,
            "direct_quota": int(user["direct_quota"] or 0),
            "level": level_for(user["xp"]),
            "invite_code": user["invite_code"] if not bool(user["is_guest"]) else "",
            "unlocked_count": len(unlocked),
        },
        "tier_gaps": [unlock_gap(user, tier, contribution) for tier in TIER_ORDER],
        "opportunities": opportunities,
        "actions": actions[:5],
        "locked_total_sample": len(locked_rows),
    }


def activation_center(user: sqlite3.Row) -> dict[str, Any]:
    is_guest = bool(user["is_guest"])
    high_value = query("select count(*) n from rumors where ai_tier in ('S', 'A')")[0]["n"]
    provider_count = query("select count(*) n from users where is_guest = 0 and xp > 0")[0]["n"]
    discussion_count = query("select count(*) n from rumor_comments")[0]["n"]
    starter_watchlist = starter_watchlist_suggestions(user["id"], 3)
    locked_sample = query(
        """
        select count(*) n
        from rumors
        where ai_tier in ('S', 'A')
        and id not in (select rumor_id from unlocks where user_id = ?)
        """,
        (user["id"],),
    )[0]["n"]
    next_steps = [
        {
            "key": "register",
            "title": "注册保留个人情报资产",
            "description": "保存邀请码、关注源、自选标的、讨论记录和成长等级。",
            "view": "auth",
            "completed": not is_guest,
        },
        {
            "key": "watch",
            "title": "建立自选信号流",
            "description": "把社区线索过滤到你真正关心的标的。",
            "view": "feed",
            "completed": query("select count(*) n from watchlist where user_id = ?", (user["id"],))[0]["n"] > 0,
        },
        {
            "key": "submit",
            "title": "投稿交换高价值情报",
            "description": "结构化线索会获得评分、贡献度和同等价值解锁机会。",
            "view": "submit",
            "completed": query("select count(*) n from rumors where submitter_id = ?", (user["id"],))[0]["n"] > 0,
        },
        {
            "key": "invite",
            "title": "邀请同圈层成员",
            "description": "每成功邀请 1 人，获得 30 XP、10 次直看额度和源分加成。",
            "view": "rank",
            "completed": int(user["invite_count"] or 0) > 0,
        },
    ]
    completed = sum(1 for item in next_steps if item["completed"])
    first_open = next((item for item in next_steps if not item["completed"]), None)
    if completed == 0:
        stage, stage_label = "visitor", "先建立账户"
    elif completed < len(next_steps):
        stage, stage_label = "activating", "正在激活情报资产"
    else:
        stage, stage_label = "activated", "已形成完整使用闭环"
    playbook = {
        "stage": stage,
        "label": stage_label,
        "progress": round(completed / len(next_steps) * 100, 1) if next_steps else 0,
        "primary_action": {
            "key": first_open["key"] if first_open else "feed",
            "label": first_open["title"] if first_open else "继续复盘情报流",
            "view": first_open["view"] if first_open else "feed",
            "detail": first_open["description"] if first_open else "查看自选、关注源和高价值线索的最新变化。",
        },
        "path": [
            {
                "key": item["key"],
                "label": item["title"],
                "completed": item["completed"],
                "view": item["view"],
            }
            for item in next_steps[:4]
        ],
        "value": [
            f"{int(high_value or 0)} 条 S/A 高价值线索可通过等级、直看或交换解锁",
            f"{int(provider_count or 0)} 位信息源正在沉淀评分、回测和社区反馈",
            "注册、关注、投稿、反馈和邀请都会进入成长账本",
        ],
    }
    return {
        "registered": not is_guest,
        "headline": "注册后把一次浏览变成可积累的情报账户" if is_guest else "你的情报账户正在积累复盘资产",
        "summary": {
            "high_value_rumors": int(high_value or 0),
            "active_sources": int(provider_count or 0),
            "discussion_count": int(discussion_count or 0),
            "locked_high_value": int(locked_sample or 0),
            "direct_quota": int(user["direct_quota"] or 0),
            "xp": int(user["xp"] or 0),
            "completed_steps": completed,
            "total_steps": len(next_steps),
        },
        "starter_rewards": [
            {"label": "无邀请码注册", "value": "10 次直看额度 + 永久成长记录"},
            {"label": "邀请码注册", "value": "20 XP + 10 次直看额度"},
            {"label": "邀请别人", "value": "每人 30 XP + 10 次直看额度"},
        ],
        "starter_watchlist": {
            "headline": "一键建立个人信号流",
            "summary": "先关注社区高分或热议标的，首页会自动聚合命中线索、风险提示和回测状态。",
            "items": starter_watchlist,
            "primary_action": {
                "key": "add_watch",
                "label": "加入第一个自选",
                "code": starter_watchlist[0]["code"] if starter_watchlist else "",
                "name": starter_watchlist[0]["name"] if starter_watchlist else "",
            },
        },
        "next_steps": next_steps,
        "activation_playbook": playbook,
        "invite_code": user["invite_code"] if not is_guest else "",
        "rights_fingerprint": hashlib.sha256(f"activation:{user['id']}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }


def referral_center(user: sqlite3.Row) -> dict[str, Any]:
    milestones = [
        {"target": 1, "title": "首位同圈层成员", "reward": "30 XP + 10 次直看额度"},
        {"target": 3, "title": "小组信息源", "reward": "额外源分加成，交换池优先"},
        {"target": 5, "title": "核心传播者", "reward": "解锁更多 A 级直看机会"},
        {"target": 10, "title": "社区共建席位", "reward": "王牌信息源冲刺加速"},
    ]
    is_guest = bool(user["is_guest"])
    invite_count = int(user["invite_count"] or 0)
    recent: list[dict[str, Any]] = []
    total_xp = 0
    total_quota = 0
    if not is_guest:
        rows = query(
            """
            select e.created_at, e.reward_xp, e.reward_quota, u.display_name invitee_name
            from referral_events e
            join users u on u.id = e.invitee_id
            where e.inviter_id = ?
            order by e.id desc
            limit 8
            """,
            (user["id"],),
        )
        recent = [dict(row) for row in rows]
        totals = query(
            "select coalesce(sum(reward_xp), 0) xp, coalesce(sum(reward_quota), 0) quota from referral_events where inviter_id = ?",
            (user["id"],),
        )[0]
        total_xp = int(totals["xp"] or 0)
        total_quota = int(totals["quota"] or 0)
    leaderboard_rows = query(
        """
        select id, display_name, invite_count, xp, reputation
        from users
        where is_guest = 0 and invite_count > 0
        order by invite_count desc, xp desc, id asc
        limit 6
        """
    )
    next_milestone = next((item for item in milestones if invite_count < item["target"]), None)
    if is_guest:
        invite_plan = {
            "state": "guest",
            "headline": "注册后开启邀请权益",
            "target": "拿到专属邀请码",
            "progress": 0,
            "next_needed": 1,
            "reward": "20 XP + 10 次直看额度",
            "share_copy": "我在股情报看 A 股情报价值评分和信息源等级，注册后可以投稿交换高价值线索。",
            "action": {"key": "register", "label": "注册拿邀请码", "view": "register"},
        }
    elif next_milestone:
        progress = round(min(100, invite_count / max(1, next_milestone["target"]) * 100), 1)
        needed = max(0, next_milestone["target"] - invite_count)
        invite_plan = {
            "state": "active",
            "headline": f"再邀请 {needed} 人到达「{next_milestone['title']}」",
            "target": next_milestone["title"],
            "progress": progress,
            "next_needed": needed,
            "reward": next_milestone["reward"],
            "share_copy": "我在股情报跟踪 A 股小道消息的评分、回测和信息源等级，你用我的邀请码注册可得 20 XP 和 10 次直看额度。",
            "action": {"key": "copy_invite", "label": "复制邀请链接", "view": "rank"},
        }
    else:
        invite_plan = {
            "state": "max",
            "headline": "邀请里程碑已全部达成",
            "target": "社区共建席位",
            "progress": 100,
            "next_needed": 0,
            "reward": "持续获得源分和社区影响力",
            "share_copy": "我在股情报参与情报共建，欢迎一起用评分、回测和信息源等级筛选线索。",
            "action": {"key": "copy_invite", "label": "继续邀请", "view": "rank"},
        }
    projected = {
        "next_reward": next_milestone["reward"] if next_milestone else "持续获得源分和社区影响力",
        "next_needed": max(0, (next_milestone["target"] if next_milestone else invite_count) - invite_count),
        "earned_value": f"{total_xp} XP + {total_quota} 次直看额度",
        "share_url": f"/?invite={user['invite_code']}" if not is_guest else "",
        "actions": [
            {"key": "copy_link", "label": "复制邀请链接", "detail": "适合直接发给同圈层朋友或群聊。"},
            {"key": "copy_pitch", "label": "复制邀请话术", "detail": "带上注册奖励和社区价值说明。"},
            {"key": "review_rank", "label": "查看邀请榜", "detail": "对比同圈层传播进度。"},
        ],
    }
    return {
        "registered": not is_guest,
        "invite_code": user["invite_code"] if not is_guest else "",
        "stats": {
            "invite_count": invite_count,
            "reward_xp": total_xp,
            "reward_quota": total_quota,
            "next_target": next_milestone["target"] if next_milestone else None,
            "next_needed": max(0, (next_milestone["target"] if next_milestone else invite_count) - invite_count),
        },
        "rewards": {
            "invitee": "通过邀请注册：20 XP + 10 次直看额度",
            "inviter": "每成功邀请：30 XP + 10 次直看额度 + 信息源分加成",
        },
        "invite_plan": invite_plan,
        "momentum": projected,
        "milestones": [
            {**item, "completed": invite_count >= item["target"], "progress": round(min(100, invite_count / item["target"] * 100), 1)}
            for item in milestones
        ],
        "recent": recent,
        "leaderboard": [
            {
                **dict(row),
                "provider_grade": provider_grade(
                    row["xp"],
                    row["reputation"],
                    contribution_for_user(row["id"]),
                    row["invite_count"] or 0,
                    provider_feedback_score(row["id"]),
                ),
            }
            for row in leaderboard_rows
        ],
    }


def community_rooms(user: sqlite3.Row, limit: int = 8) -> dict[str, Any]:
    day = active_feed_date()
    radar = topic_radar(day)
    seeds: list[dict[str, Any]] = []
    for item in radar.get("themes", [])[:5]:
        seeds.append({"kind": "theme", "name": item["name"], "query": item["name"], "code": ""})
    for item in radar.get("stocks", [])[:5]:
        name = item.get("name") or item.get("code")
        seeds.append({"kind": "stock", "name": name, "query": name, "code": item.get("code") or ""})
    rooms = []
    unlocked = user_unlocked_ids(user)
    watched_codes = watched_codes_for_user(user["id"])
    for seed in seeds:
        if not seed["query"]:
            continue
        if seed["kind"] == "theme":
            rows = query(
                """
                select *
                from rumors
                where recommendation_date = ?
                and (target like ? or logic like ? or raw_content like ? or institution like ?)
                order by ai_score desc, id desc
                limit 12
                """,
                (day, f"%{seed['query']}%", f"%{seed['query']}%", f"%{seed['query']}%", f"%{seed['query']}%"),
            )
        else:
            terms = [seed["query"]]
            if seed.get("code"):
                terms.append(seed["code"])
            clauses = " or ".join(["target like ? or stock_codes like ?" for _ in terms])
            args: list[Any] = [day]
            for term in terms:
                args.extend([f"%{term}%", f"%{term}%"])
            rows = query(
                f"""
                select *
                from rumors
                where recommendation_date = ?
                and ({clauses})
                order by ai_score desc, id desc
                limit 12
                """,
                tuple(args),
            )
        if not rows:
            continue
        ids = [row["id"] for row in rows]
        stats = discussion_stats(ids, user["id"])
        tier_mix: dict[str, int] = {}
        providers: set[str] = set()
        heat = 0
        for row in rows:
            tier_mix[row["ai_tier"]] = tier_mix.get(row["ai_tier"], 0) + 1
            if row["submitter_name"]:
                providers.add(row["submitter_name"])
            heat += int(stats.get(row["id"], {}).get("heat") or 0)
        top = rows[0]
        rooms.append(
            {
                "key": f"{seed['kind']}:{seed['query']}",
                "kind": seed["kind"],
                "name": seed["name"],
                "query": seed["query"],
                "code": seed.get("code") or "",
                "count": len(rows),
                "top_score": int(top["ai_score"] or 0),
                "tier_mix": tier_mix,
                "provider_count": len(providers),
                "heat": heat,
                "summary": f"{tier_mix.get('S', 0)}条S级 · {tier_mix.get('A', 0)}条A级 · {len(providers)}个信息源",
                "top_rumor": public_rumor(top, can_view(user, top, unlocked), stats.get(top["id"]), watched_codes, user["id"], user),
                "action": {"label": "进入房间", "view": "feed", "query": seed["query"]},
            }
        )
    rooms.sort(key=lambda item: (item["heat"], item["top_score"], item["count"]), reverse=True)
    return {
        "active_date": day,
        "rooms": rooms[: max(1, min(12, limit))],
        "summary": {
            "total": len(rooms),
            "theme_rooms": sum(1 for item in rooms if item["kind"] == "theme"),
            "stock_rooms": sum(1 for item in rooms if item["kind"] == "stock"),
            "heat": sum(int(item["heat"] or 0) for item in rooms),
        },
    }


def invite_preview(code: str) -> dict[str, Any]:
    normalized = normalize_invite_code(code)
    rows = query("select * from users where invite_code = ? and is_guest = 0", (normalized,))
    if not normalized or not rows:
        return {
            "valid": False,
            "invite_code": normalized,
            "message": "邀请码无效或已失效",
            "invitee_reward": "使用有效邀请码注册可获得 20 XP + 10 次直看额度",
            "inviter_reward": "邀请人可获得 30 XP + 10 次直看额度",
            "landing_value": {
                "headline": "使用有效邀请码可获得启动权益",
                "proof_points": [
                    {"label": "注册奖励", "value": "20 XP + 10直看"},
                    {"label": "成长记录", "value": "永久保留"},
                    {"label": "激活路径", "value": "自选/解锁/投稿"},
                ],
                "activation_steps": ["注册账号", "建立自选信号流", "解锁或贡献第一条情报"],
            },
        }
    inviter = rows[0]
    snapshot = provider_snapshot(inviter["id"]) or {
        "id": inviter["id"],
        "display_name": inviter["display_name"],
        "grade": provider_grade(inviter["xp"], inviter["reputation"], contribution_for_user(inviter["id"]), inviter["invite_count"] or 0, provider_feedback_score(inviter["id"])),
    }
    grade = snapshot.get("grade") or {}
    landing_value = {
        "headline": "带奖励进入社区，先建立你的个人情报账户",
        "proof_points": [
            {"label": "注册奖励", "value": "20 XP + 10直看"},
            {"label": "邀请人源分", "value": f"{float(grade.get('score') or 0):.1f}"},
            {"label": "源等级", "value": grade.get("name") or "信息源"},
        ],
        "activation_steps": [
            "注册后保留成长、邀请和解锁记录",
            "先加入 1 个自选标的生成个人信号流",
            "用直看额度或投稿交换第一条高价值情报",
        ],
        "cta": {"key": "accept_invite", "label": "接受邀请注册", "view": "register"},
    }
    return {
        "valid": True,
        "invite_code": normalized,
        "inviter": snapshot,
        "message": f"{inviter['display_name']} 邀请你加入股情报",
        "invitee_reward": "20 XP + 10 次直看额度 + 永久成长记录",
        "inviter_reward": "邀请人获得 30 XP + 10 次直看额度 + 源分加成",
        "activation_steps": ["注册账号", "建立自选信号流", "解锁或贡献第一条情报"],
        "landing_value": landing_value,
        "rights_fingerprint": hashlib.sha256(f"invite:{normalized}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }


@lru_cache(maxsize=1)
def latest_trading_date() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = query("select max(start_date) d from backtests where start_date <= ?", (today,))
    if rows and rows[0]["d"]:
        return rows[0]["d"]
    rows = query("select max(recommendation_date) d from rumors where recommendation_date <= ?", (today,))
    return rows[0]["d"] or today


def active_feed_date() -> str:
    return latest_trading_date()


@lru_cache(maxsize=1)
def code_lookup() -> dict[str, str]:
    if STOCK_LOOKUP_CACHE.exists():
        try:
            cached = json.loads(STOCK_LOOKUP_CACHE.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return {str(k): str(v) for k, v in cached.items()}
        except (OSError, json.JSONDecodeError):
            pass
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
        DATA_DIR.mkdir(exist_ok=True)
        STOCK_LOOKUP_CACHE.write_text(json.dumps(lookup, ensure_ascii=False), encoding="utf-8")
        return lookup
    except Exception:
        return {}


def find_codes(target: str, lookup: dict[str, str]) -> list[tuple[str, str]]:
    matches: list[tuple[int, int, str, str]] = []
    for name in lookup:
        if not name:
            continue
        start = target.find(name)
        while start >= 0:
            matches.append((start, start + len(name), lookup[name], name))
            start = target.find(name, start + 1)

    picked: list[tuple[str, str]] = []
    seen_codes: set[str] = set()
    occupied: list[tuple[int, int]] = []
    for start, end, code, name in sorted(matches, key=lambda x: (x[0], -(x[1] - x[0]))):
        if code in seen_codes or any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        picked.append((code, name))
        seen_codes.add(code)
        occupied.append((start, end))

    for m in re.finditer(r"\b([036]\d{5})\b", target):
        code6 = m.group(1)
        suffix = ".sh" if code6.startswith("6") else ".sz"
        code = code6 + suffix
        if code not in seen_codes:
            picked.append((code, code6))
            seen_codes.add(code)
    return picked


def avg_or_none(values: list[float | None]) -> float | None:
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return float(sum(valid) / len(valid))


def backtest_outcome(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "state": "pending",
            "label": "待回测",
            "score": None,
            "avg_ret": None,
            "best_ret": None,
            "max_drawdown": None,
            "summary": "等待本地行情验证",
        }

    def val(key: str) -> Any:
        return row[key] if isinstance(row, sqlite3.Row) else row.get(key)

    status = val("status") or "pending"
    signal = val("signal_value")
    rets = [val("ret_t1_1"), val("ret_t1_5"), val("ret_t1_20")]
    valid = [float(v) for v in rets if v is not None]
    avg_ret = sum(valid) / len(valid) if valid else None
    best_ret = max(valid) if valid else None
    drawdown = val("max_drawdown_20")
    drawdown = float(drawdown) if drawdown is not None else None
    score = float(signal) if signal is not None else None

    if status == "missing_code":
        state, label, summary = "no_code", "未匹配", "未匹配到本地行情代码"
    elif status == "limit_up_or_down":
        state, label, summary = "skipped", "跳过", "T+1一字板，未纳入可买入信号"
    elif status == "pending" or not valid:
        state, label, summary = "pending", "待验证", "推荐日后行情不足"
    elif (score is not None and score >= 80) or (avg_ret is not None and avg_ret >= 0.08) or (best_ret is not None and best_ret >= 0.15):
        state, label, summary = "hit", "强命中", "收益表现进入高价值区间"
    elif (score is not None and score >= 60) or (avg_ret is not None and avg_ret >= 0.02):
        state, label, summary = "valid", "有效", "收益表现优于多数样本"
    elif (score is not None and score <= 25) or (avg_ret is not None and avg_ret <= -0.04) or (drawdown is not None and drawdown <= -0.12):
        state, label, summary = "weak", "偏弱", "回测收益或回撤表现偏弱"
    else:
        state, label, summary = "neutral", "待观察", "收益表现暂未显著"

    return {
        "state": state,
        "label": label,
        "score": round(score, 1) if score is not None else None,
        "avg_ret": round(avg_ret, 4) if avg_ret is not None else None,
        "best_ret": round(best_ret, 4) if best_ret is not None else None,
        "max_drawdown": round(drawdown, 4) if drawdown is not None else None,
        "summary": summary,
    }


def backtest_outcomes_for(rumor_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not rumor_ids:
        return {}
    placeholders = ",".join("?" for _ in rumor_ids)
    rows = query(f"select * from backtests where rumor_id in ({placeholders})", tuple(rumor_ids))
    outcomes = {row["rumor_id"]: backtest_outcome(row) for row in rows}
    for rumor_id in rumor_ids:
        outcomes.setdefault(rumor_id, backtest_outcome(None))
    return outcomes


def normalize_stock_code(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace(" ", "")
    m = re.match(r"^([036]\d{5})(?:\.(sh|sz))?$", raw)
    if not m:
        return ""
    code6 = m.group(1)
    suffix = ".sh" if code6.startswith("6") else ".sz"
    return code6 + suffix


def stock_items_from_target(target: str, lookup: dict[str, str] | None = None) -> list[dict[str, str]]:
    lookup = lookup if lookup is not None else code_lookup()
    return [{"name": name, "code": code} for code, name in find_codes(target, lookup)]


def stock_items_from_payload(target: str, raw_stock_codes: str | None) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if raw_stock_codes:
        try:
            parsed = json.loads(raw_stock_codes)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            seen: set[tuple[str, str]] = set()
            for item in parsed[:20]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()[:40]
                code = normalize_stock_code(str(item.get("code") or ""))
                if not name and not code:
                    continue
                key = (name, code)
                if key in seen:
                    continue
                seen.add(key)
                items.append({"name": name or code, "code": code})
    if items:
        return items
    return stock_items_from_target(target)


def stock_items_for_backtest(row: sqlite3.Row, lookup: dict[str, str]) -> list[dict[str, str]]:
    try:
        raw_stock_codes = row["stock_codes"]
    except (KeyError, IndexError):
        raw_stock_codes = ""
    items = stock_items_from_payload(row["target"], raw_stock_codes)
    if items:
        return items
    return stock_items_from_target(row["target"], lookup)


def backfill_rumor_stock_codes() -> None:
    try:
        rows = query("select id, target, stock_codes from rumors where stock_codes = '[]' or stock_codes = ''")
    except sqlite3.OperationalError:
        return
    if not rows:
        return
    lookup = code_lookup()
    con = db()
    for row in rows:
        items = stock_items_from_target(row["target"], lookup)
        if items:
            con.execute("update rumors set stock_codes = ? where id = ?", (json.dumps(items, ensure_ascii=False), row["id"]))
    con.commit()
    con.close()


def refresh_backtests(limit: int | None = None, force: bool = False) -> int:
    if not RAW_DAILY.exists():
        return 0
    if force:
        base_sql = "select * from rumors order by recommendation_date desc"
        args: tuple[Any, ...] = ()
    else:
        base_sql = "select * from rumors where id not in (select rumor_id from backtests where ret_t1_1 is not null) order by recommendation_date desc"
        args = ()
    rows = query(base_sql + (" limit ?" if limit else ""), (*args, limit) if limit else args)
    if not rows:
        return 0
    try:
        lookup = code_lookup()
        mapped = {row["id"]: stock_items_for_backtest(row, lookup) for row in rows}
        codes = sorted({item["code"] for stocks in mapped.values() for item in stocks if item["code"]})
        if codes:
            raw = pd.read_parquet(RAW_DAILY, columns=["code", "date", "open", "high", "low", "close"])
            raw = raw[raw["code"].isin(codes)].copy()
            raw["date"] = pd.to_datetime(raw["date"].astype(str))
            raw = raw[raw["date"].le(END_DATE)].sort_values(["code", "date"])
            bars_by_code = {code: g.set_index("date") for code, g in raw.groupby("code", sort=False)}
        else:
            bars_by_code = {}
        source_details = "T+1开盘买入，未复权"
    except Exception:
        return 0
    con = db()
    done = 0
    def calc_stock_metrics(df: pd.DataFrame, rec_date: pd.Timestamp) -> dict[str, Any]:
        future_df = df[df.index > rec_date]
        result: dict[str, Any] = {
            "start_date": None,
            "price_start": None,
            "ret_t1_1": None,
            "ret_t1_5": None,
            "ret_t1_20": None,
            "max_drawdown_20": None,
            "status": "pending",
            "details": "推荐日后暂无足够行情",
        }
        if len(future_df) < 1:
            return result

        t1_row = future_df.iloc[0]
        result["start_date"] = future_df.index[0].strftime("%Y-%m-%d")
        if float(t1_row.get("high", 0)) == float(t1_row.get("low", 1)):
            result["status"] = "limit_up_or_down"
            result["details"] = "T+1一字板，跳过开盘买入信号"
            return result

        buy_price = float(t1_row["open"])
        if buy_price <= 0:
            result["details"] = "T+1开盘价无效"
            return result

        result["price_start"] = buy_price
        open_ser = df["open"].dropna()
        future_open = open_ser[open_ser.index > rec_date]

        def ret_t1_at(n: int) -> float | None:
            # 持有N日后下一交易日开盘卖出，即 future_open.iloc[n]
            if len(future_open) <= n:
                return None
            return float(future_open.iloc[n] / buy_price - 1)

        result["ret_t1_1"] = ret_t1_at(1)
        result["ret_t1_5"] = ret_t1_at(5)
        result["ret_t1_20"] = ret_t1_at(20)
        close_t1_20 = df["close"].dropna()
        close_t1_20 = close_t1_20[close_t1_20.index > rec_date].iloc[:20]
        if len(close_t1_20) > 0:
            result["max_drawdown_20"] = float((close_t1_20.min() - buy_price) / buy_price)
        result["status"] = "ok"
        result["details"] = source_details
        return result

    for row in rows:
        stocks = mapped[row["id"]]
        if not stocks:
            con.execute(
                "insert or replace into backtests (rumor_id,code,name,start_date,price_start,ret_5,ret_20,ret_60,max_ret_60,ret_t1_1,ret_t1_5,ret_t1_20,signal_value,max_drawdown_20,status,details) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], None, None, None, None, None, None, None, None, None, None, None, None, None, "missing_code", "未匹配到本地行情代码"),
            )
            done += 1
            continue

        rec_date = pd.Timestamp(row["recommendation_date"])
        metrics = []
        missing = []
        for stock in stocks:
            code = stock["code"]
            name = stock["name"]
            if not code:
                missing.append(name)
                continue
            df = bars_by_code.get(code)
            if df is None:
                missing.append(name)
                continue
            item = calc_stock_metrics(df, rec_date)
            item["code"] = code
            item["name"] = name
            metrics.append(item)

        signal_value = None
        ok_metrics = [m for m in metrics if m["status"] == "ok"]
        valid_count = len(ok_metrics)
        start_dates = sorted({m["start_date"] for m in ok_metrics if m["start_date"]})
        start_date = start_dates[0] if len(start_dates) == 1 else ",".join(start_dates[:3]) if start_dates else None
        price_start = avg_or_none([m["price_start"] for m in ok_metrics])
        ret_t1_1 = avg_or_none([m["ret_t1_1"] for m in ok_metrics])
        ret_t1_5 = avg_or_none([m["ret_t1_5"] for m in ok_metrics])
        ret_t1_20 = avg_or_none([m["ret_t1_20"] for m in ok_metrics])
        max_drawdown_20 = avg_or_none([m["max_drawdown_20"] for m in ok_metrics])

        if valid_count == len(stocks):
            status = "ok"
        elif valid_count > 0:
            status = "partial"
        elif missing and len(missing) == len(stocks):
            status = "missing_code"
        elif any(m["status"] == "limit_up_or_down" for m in metrics):
            status = "limit_up_or_down"
        else:
            status = "pending"

        skipped_limit = sum(1 for m in metrics if m["status"] == "limit_up_or_down")
        pending_count = sum(1 for m in metrics if m["status"] == "pending")
        details = f"{source_details}；按{len(stocks)}只股票平均；有效{valid_count}只"
        if missing:
            details += f"；缺行情{len(missing)}只"
        if skipped_limit:
            details += f"；一字板跳过{skipped_limit}只"
        if pending_count:
            details += f"；待行情{pending_count}只"

        con.execute(
            "insert or replace into backtests (rumor_id,code,name,start_date,price_start,ret_5,ret_20,ret_60,max_ret_60,ret_t1_1,ret_t1_5,ret_t1_20,signal_value,max_drawdown_20,status,details) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], ",".join(stock["code"] for stock in stocks if stock["code"]), ",".join(stock["name"] for stock in stocks), start_date, price_start,
             None, None, None, None,
             ret_t1_1, ret_t1_5, ret_t1_20, signal_value, max_drawdown_20,
             status, details),
        )
        done += 1

    con.commit()
    con.close()

    # 计算全A百分位排名 signal_value（需要全量数据，在所有行写完后统一算）
    _calc_signal_values()
    recalc_user_scores()
    return done


def _calc_signal_values() -> None:
    """根据 T+1/T+5/T+20 收益和 T+20 最大回撤的综合分位，计算 signal_value (0-100)。"""
    rows = query("select rumor_id, ret_t1_1, ret_t1_5, ret_t1_20, max_drawdown_20 from backtests where ret_t1_1 is not null")
    if not rows:
        return
    scores: list[tuple[int, float]] = []
    for r in rows:
        ret_1 = float(r["ret_t1_1"]) if r["ret_t1_1"] is not None else None
        ret_5 = float(r["ret_t1_5"]) if r["ret_t1_5"] is not None else None
        ret_20 = float(r["ret_t1_20"]) if r["ret_t1_20"] is not None else None
        weighted_parts = []
        if ret_1 is not None:
            weighted_parts.append((ret_1, 0.25))
        if ret_5 is not None:
            weighted_parts.append((ret_5, 0.35))
        if ret_20 is not None:
            weighted_parts.append((ret_20, 0.40))
        if not weighted_parts:
            continue
        weight_total = sum(weight for _, weight in weighted_parts)
        weighted_ret = sum(value * weight for value, weight in weighted_parts) / max(weight_total, 0.01)
        drawdown = float(r["max_drawdown_20"]) if r["max_drawdown_20"] is not None else 0.0
        drawdown_penalty = abs(min(0.0, drawdown)) * 0.45
        raw_score = weighted_ret - drawdown_penalty
        scores.append((r["rumor_id"], raw_score))
    if not scores:
        return
    # 全样本综合分位排名
    all_vals = sorted(v for _, v in scores)
    n = len(all_vals)
    con = db()
    for rumor_id, avg in scores:
        rank = sum(1 for v in all_vals if v <= avg)
        pct = round(rank / n * 100, 1)
        con.execute("update backtests set signal_value = ? where rumor_id = ?", (pct, rumor_id))
    con.commit()
    con.close()


def recalc_user_scores() -> None:
    rows = query(
        """
        select r.submitter_id, r.target, r.ai_score, r.created_at,
               b.ret_t1_1, b.ret_t1_5, b.ret_t1_20, b.signal_value
        from rumors r left join backtests b on b.rumor_id = r.id
        where r.submitter_id is not null
        """
    )
    grouped: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        grouped.setdefault(r["submitter_id"], []).append(r)
    now = datetime.now(timezone.utc)
    for user_id, items in grouped.items():
        xp = 0
        perf = []
        contribution = 0.0
        deduped: dict[tuple[str, str], sqlite3.Row] = {}
        for item in items:
            key = (str(item["created_at"] or "")[:10], str(item["target"] or "").strip())
            current = deduped.get(key)
            if current is None or float(item["ai_score"] or 0) > float(current["ai_score"] or 0):
                deduped[key] = item
        for item in deduped.values():
            # signal_value (0-100 百分位) 有则用，无则退回 ai_score
            sv = item["signal_value"]
            base_score = float(sv) if sv is not None else float(item["ai_score"])
            # XP：基础分 + T+1三期均值奖励
            base_xp = max(5, int(base_score / 5))
            t1_rets = [item["ret_t1_1"], item["ret_t1_5"], item["ret_t1_20"]]
            t1_valid = [v for v in t1_rets if v is not None]
            bonus = 0
            if t1_valid:
                avg_ret = sum(t1_valid) / len(t1_valid)
                bonus = int(avg_ret * 150)
                perf.append(avg_ret)
            xp += max(1, base_xp + bonus)
            # 贡献度：以 base_score 为权重，按30天半衰期时间衰减
            age_days = max(0.0, (now - parse_dt(item["created_at"])).total_seconds() / 86400)
            decay = 0.5 ** (age_days / CONTRIBUTION_HALF_LIFE_DAYS)
            contribution += base_score * decay
        rep = 50 + (sum(perf) / len(perf) * 150 if perf else 0)
        lvl = level_for(xp)
        execute(
            "update users set xp = ?, reputation = ?, direct_quota = max(direct_quota, ?) where id = ?",
            (xp, max(1, min(99, rep)), lvl["quota"], user_id),
        )


def calc_user_radar(user_id: int) -> dict[str, float]:
    """返回四维评分 (0-100)：活跃度、进攻性、防守性、独特性。"""
    now = datetime.now(timezone.utc)
    ninety_days_ago = (now.timestamp() - 90 * 86400)

    # 活跃度：近90天推荐条数 vs 全体用户
    my_count = query(
        "select count(*) n from rumors where submitter_id = ? and created_at >= ?",
        (user_id, datetime.fromtimestamp(ninety_days_ago, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )[0]["n"]
    all_counts = query(
        """select submitter_id, count(*) n from rumors
           where submitter_id is not null and created_at >= ?
           group by submitter_id""",
        (datetime.fromtimestamp(ninety_days_ago, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),),
    )
    activity = _percentile(my_count, [r["n"] for r in all_counts])

    # 进攻性、防守性：T+1三期均值收益 & 20日最大回撤
    my_bt = query(
        """select b.ret_t1_1, b.ret_t1_5, b.ret_t1_20, b.max_drawdown_20
           from rumors r join backtests b on b.rumor_id = r.id
           where r.submitter_id = ? and b.ret_t1_1 is not null""",
        (user_id,),
    )
    all_bt = query(
        """select r.submitter_id, b.ret_t1_1, b.ret_t1_5, b.ret_t1_20, b.max_drawdown_20
           from rumors r join backtests b on b.rumor_id = r.id
           where r.submitter_id is not null and b.ret_t1_1 is not null""",
    )

    def avg_ret(rows: list) -> float | None:
        vals = []
        for r in rows:
            v = [r["ret_t1_1"], r["ret_t1_5"], r["ret_t1_20"]]
            v = [x for x in v if x is not None]
            if v:
                vals.append(sum(v) / len(v))
        return sum(vals) / len(vals) if vals else None

    def avg_dd(rows: list) -> float | None:
        vals = [r["max_drawdown_20"] for r in rows if r["max_drawdown_20"] is not None]
        return sum(vals) / len(vals) if vals else None

    my_avg = avg_ret(my_bt)
    all_avgs_by_user: dict[int, list] = {}
    for r in all_bt:
        all_avgs_by_user.setdefault(r["submitter_id"], []).append(r)
    all_user_avgs = [avg_ret(v) for v in all_avgs_by_user.values() if avg_ret(v) is not None]
    offense = _percentile(my_avg, all_user_avgs) if my_avg is not None else 50.0

    my_dd = avg_dd(my_bt)
    all_user_dds = [avg_dd(v) for v in all_avgs_by_user.values() if avg_dd(v) is not None]
    # 回撤越小（越接近0）越好，取反后百分位
    defense = _percentile(-my_dd if my_dd is not None else None,
                          [-d for d in all_user_dds]) if my_dd is not None else 50.0

    # 独特性：自己推荐过的股票中，仅自己推荐过的比例
    my_targets = query(
        "select distinct target from rumors where submitter_id = ?", (user_id,)
    )
    if not my_targets:
        uniqueness = 50.0
    else:
        target_list = [r["target"] for r in my_targets]
        unique_count = 0
        for t in target_list:
            others = query(
                "select count(*) n from rumors where target = ? and submitter_id != ?", (t, user_id)
            )[0]["n"]
            if others == 0:
                unique_count += 1
        uniqueness = round(unique_count / len(target_list) * 100, 1)

    return {
        "activity":   round(activity, 1),
        "offense":    round(offense, 1),
        "defense":    round(defense, 1),
        "uniqueness": round(uniqueness, 1),
    }


def _percentile(value: float | None, population: list[float]) -> float:
    if value is None or not population:
        return 50.0
    n = len(population)
    rank = sum(1 for v in population if v <= value)
    return round(rank / n * 100, 1)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.middleware("http")
async def add_copyright_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-StockWhisper-Mark"] = hidden_copyright_mark()
    response.headers["X-Content-Policy"] = "non-commercial-learning-only"
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/value-framework")
def value_framework(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    contribution = contribution_for_user(user["id"])
    return {
        "score_dimensions": [
            {"key": "specificity", "name": "标的明确度", "weight": 30, "description": "股票、产业链、客户或催化越明确，基础价值越高"},
            {"key": "evidence", "name": "信息密度", "weight": 30, "description": "事实、时间、订单、产能、业绩线索越完整，密度越高"},
            {"key": "freshness", "name": "时效", "weight": 10, "description": "带推荐日期和短期验证节点的消息优先"},
            {"key": "source", "name": "来源可信度", "weight": 10, "description": "机构、推荐人、可追踪来源会提升可信度"},
            {"key": "verifiability", "name": "可验证性", "weight": 20, "description": "可回测、可复盘、可被后续行情验证的内容权重更高"},
            {"key": "novelty", "name": "稀缺性", "weight": 15, "description": "重复搬运和同质内容会被扣分，独家时间点和差异化验证更有交换价值"},
        ],
        "tiers": [
            {**tier_access(user, tier, contribution), "order": i + 1}
            for i, tier in enumerate(TIER_ORDER)
        ],
        "provider_levels": [
            {"name": "新晋观察员", "rule": "注册或游客起步，贡献 C/B 级线索"},
            {"name": "可信线索员", "rule": "贡献度、XP、信誉分任一持续提升，开始稳定解锁 B/A 情报"},
            {"name": "核心信息源", "rule": "高分投稿+回测表现稳定，A 级内容优先展示"},
            {"name": "王牌信息源", "rule": "长期高质量、低回撤、高独特性、社区验证正反馈，S 级内容和邀请权益优先"},
        ],
        "invite_rewards": {
            "new_user": "通过邀请码注册获得 20 XP 和 10 次直看额度",
            "inviter": "每成功邀请 1 人获得 30 XP、10 次直看额度、邀请计数",
        },
        "value_verdict": {
            "name": "社区价值指数",
            "description": "在原始AI评分之上叠加信息源等级、社区正反馈、回测表现、自选命中和举报扣分，用于排序注意力而非替代原评分。",
            "inputs": ["AI内容评分", "信息源分", "有用/存疑/讨论", "回测状态", "自选命中", "可信度扣分"],
            "labels": ["强价值", "值得跟踪", "可观察", "低确定性"],
        },
        "calibration": {
            "headline": "评分校准规则",
            "summary": "平台先评内容结构，再叠加来源、社区验证和行情复盘；高风险话术、重复搬运和举报会降低有效排序。",
            "positive": [
                {"key": "evidence", "label": "事实链完整", "impact": "加权", "detail": "标的、催化、来源、日期和可验证节点越完整，越容易进入 A/S。"},
                {"key": "source", "label": "高可信来源", "impact": "加权", "detail": "信息源等级、历史回测和社区正反馈会进入价值指数。"},
                {"key": "consensus", "label": "多源共识", "impact": "加权", "detail": "同标的多条独立线索会形成共识快照和验证任务。"},
            ],
            "negative": [
                {"key": "duplicate", "label": "重复搬运", "impact": "扣分", "detail": "同质化文本、缺少差异化时间点或验证节点会降低稀缺性。"},
                {"key": "risk", "label": "高风险话术", "impact": "扣分", "detail": "稳赚、内幕、喊单、满仓等措辞会进入风控提示。"},
                {"key": "reports", "label": "社区举报", "impact": "降权", "detail": "举报会进入可信度账本，影响有效分、排序和信息源反馈。"},
            ],
            "principles": ["排序注意力，不替代独立判断", "优先可验证事实，不奖励喊单结论", "所有高分线索仍需回测和社区反馈"],
        },
    }


@app.get("/api/community-insight")
def community_insight(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    day = active_feed_date()
    return {
        "active_date": day,
        "rights_envelope": rights_envelope("community-insight", user["id"], day),
        "recent_backtest_showcase": recent_backtest_showcase(day, user, limit=10),
    }


@app.get("/api/community-rooms")
def get_community_rooms(response: Response, agu_session: str | None = Cookie(default=None), limit: int = 8) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return community_rooms(user, limit)


@app.get("/api/activity-feed")
def activity_feed(response: Response, agu_session: str | None = Cookie(default=None), limit: int = 12) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return personalized_activity_feed(user, limit)


@app.get("/api/exchange-desk")
def get_exchange_desk(response: Response, agu_session: str | None = Cookie(default=None), limit: int = 6) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return exchange_desk(user, limit)


@app.get("/api/referral-center")
def get_referral_center(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return referral_center(user)


@app.get("/api/invite-preview/{code:path}")
def get_invite_preview(code: str) -> dict[str, Any]:
    return invite_preview(code)


@app.get("/api/activation-center")
def get_activation_center(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return activation_center(user)


@app.get("/api/growth-center")
def growth_center(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    missions = growth_missions(user)
    completed = sum(1 for item in missions if item["completed"])
    return {
        "user": user_out(user),
        "upgrade": provider_upgrade_plan(user),
        "missions": missions,
        "ledger": growth_ledger(user),
        "summary": {
            "completed": completed,
            "total": len(missions),
            "completion_rate": round(completed / len(missions) * 100, 1) if missions else 0,
            "next_action": next((item for item in missions if not item["completed"]), None),
        },
        "rights_fingerprint": hashlib.sha256(f"{user['id']}:{hidden_copyright_mark()}".encode()).hexdigest()[:16],
    }


@app.get("/api/source-upgrade-center")
def get_source_upgrade_center(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return source_upgrade_center(user)


@app.get("/api/watchlist")
def get_watchlist(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    if bool(user["is_guest"]):
        raise HTTPException(401, "请先登录后使用自选股")
    return watch_summary(user["id"])


@app.get("/api/watchlist/history")
def get_watchlist_history(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return watchlist_history(user)


@app.post("/api/watchlist")
def add_watch(payload: WatchPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    code = normalize_stock_code(payload.code)
    name = payload.name.strip()[:40]
    if not code:
        raise HTTPException(422, "需要有效股票代码，例如 600000.sh")
    execute(
        "insert or replace into watchlist(user_id, code, name, created_at) values (?, ?, ?, ?)",
        (user["id"], code, name or code, now_iso()),
    )
    return watch_summary(user["id"])


@app.delete("/api/watchlist/{code}")
def remove_watch(code: str, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    normalized = normalize_stock_code(code)
    if not normalized:
        raise HTTPException(422, "股票代码无效")
    execute("delete from watchlist where user_id = ? and code = ?", (user["id"], normalized))
    return watch_summary(user["id"])


@app.get("/api/provider-follows")
def get_provider_follows(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    return followed_provider_summary(user["id"])


@app.post("/api/providers/{provider_id}/follow")
def follow_provider(provider_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    if provider_id == user["id"]:
        raise HTTPException(400, "不能关注自己")
    if not query("select 1 from users where id = ? and is_guest = 0", (provider_id,)):
        raise HTTPException(404, "信息源不存在")
    execute(
        "insert or ignore into provider_follows(follower_id, provider_id, created_at) values (?, ?, ?)",
        (user["id"], provider_id, now_iso()),
    )
    return {"following": followed_provider_summary(user["id"]), "provider": provider_profile(provider_id, user)}


@app.delete("/api/providers/{provider_id}/follow")
def unfollow_provider(provider_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    execute("delete from provider_follows where follower_id = ? and provider_id = ?", (user["id"], provider_id))
    return {"following": followed_provider_summary(user["id"]), "provider": provider_profile(provider_id, user)}


@app.get("/api/me")
def me(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    lvl = level_for(effective_user_xp(user))
    return {"user": user_out(user), "level": lvl}


@app.post("/api/score-preview")
def preview_score(payload: RumorPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    current_user(response, agu_session)
    return score_preview(payload)


@app.post("/api/send-code")
def send_code(payload: SendCodePayload) -> dict[str, Any]:
    email = payload.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(422, "邮箱格式不正确")
    if query("select 1 from users where lower(email) = ?", (email,)):
        raise HTTPException(409, "该邮箱已注册")
    code = f"{random.randint(0, 999999):06d}"
    expires = int(time.time()) + EMAIL_CODE_TTL
    execute(
        "insert or replace into email_verifications(email, code, expires_at) values (?, ?, ?)",
        (email, code, expires),
    )
    delivery = deliver_email_code(
        email,
        code,
        "股情报 StockWhisper 注册验证码",
        registration_verification_email_body(code),
    )
    return {"ok": True, "delivery": delivery}


DAILY_REG_LIMIT = 100


@app.post("/api/password-reset/lookup")
def lookup_password_reset_account(payload: PasswordResetLookupPayload) -> dict[str, Any]:
    user = password_reset_user(payload.account)
    return {"ok": True, "masked_email": mask_email(user["email"])}


@app.post("/api/password-reset/send-code")
def send_password_reset_code(payload: PasswordResetSendPayload) -> dict[str, Any]:
    user = password_reset_user(payload.account)
    email = user["email"].strip().lower()
    code = f"{random.randint(0, 999999):06d}"
    expires = int(time.time()) + EMAIL_CODE_TTL
    execute(
        "insert or replace into password_reset_verifications(email, code, expires_at) values (?, ?, ?)",
        (email, code, expires),
    )
    delivery = deliver_email_code(
        email,
        code,
        "股情报 StockWhisper 密码重置验证码",
        password_reset_verification_email_body(code),
    )
    return {"ok": True, "delivery": delivery, "masked_email": mask_email(email)}


@app.post("/api/password-reset")
def reset_password(payload: PasswordResetPayload) -> dict[str, Any]:
    user = password_reset_user(payload.account)
    email = user["email"].strip().lower()
    rows = query(
        "select * from password_reset_verifications where email = ? and expires_at > ?",
        (email, int(time.time())),
    )
    if not rows or rows[0]["code"] != payload.code:
        raise HTTPException(400, "验证码错误或已过期")
    salt, digest = hash_password(payload.password)
    execute(
        "update users set password_salt = ?, password_hash = ? where id = ?",
        (salt, digest, user["id"]),
    )
    execute("delete from sessions where user_id = ?", (user["id"],))
    execute("delete from password_reset_verifications where email = ?", (email,))
    return {"ok": True}


@app.post("/api/register")
def register(payload: RegisterPayload, response: Response) -> dict[str, Any]:
    email = payload.email.strip().lower()
    if query("select 1 from users where lower(email) = ?", (email,)):
        raise HTTPException(409, "该邮箱已注册")
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
    invite_code = normalize_invite_code(payload.invite_code)
    inviter = None
    if invite_code:
        inviter_rows = query("select * from users where invite_code = ? and is_guest = 0", (invite_code,))
        if not inviter_rows:
            raise HTTPException(400, "邀请码无效")
        inviter = inviter_rows[0]
    salt, digest = hash_password(payload.password)
    starter_xp = 20 if inviter else 0
    starter_quota = NEW_USER_DIRECT_QUOTA
    execute(
        """
        insert into users(username, display_name, email, password_salt, password_hash, is_guest,
                          xp, direct_quota, invited_by, created_at)
        values (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (payload.username, payload.username, email, salt, digest, starter_xp, starter_quota, inviter["id"] if inviter else None, now_iso()),
    )
    user = query("select * from users where username = ?", (payload.username,))[0]
    execute("update users set invite_code = ? where id = ?", (make_invite_code(user["username"], user["id"]), user["id"]))
    if inviter:
        execute(
            "insert or ignore into referral_events(inviter_id, invitee_id, reward_xp, reward_quota, created_at) values (?, ?, 30, ?, ?)",
            (inviter["id"], user["id"], INVITE_REWARD_DIRECT_QUOTA, now_iso()),
        )
        execute(
            "update users set xp = xp + 30, direct_quota = direct_quota + ?, invite_count = invite_count + 1 where id = ?",
            (INVITE_REWARD_DIRECT_QUOTA, inviter["id"]),
        )
        inviter_level = level_for(inviter["xp"] + 30)
        execute("update users set direct_quota = max(direct_quota, ?) where id = ?", (inviter_level["quota"], inviter["id"]))
    user = query("select * from users where id = ?", (user["id"],))[0]
    create_session(user["id"], response)
    return {"user": user_out(user), "level": level_for(user["xp"])}


@app.post("/api/login")
def login(payload: AuthPayload, response: Response) -> dict[str, Any]:
    login_name = payload.username.strip()
    rows = query(
        "select * from users where is_guest = 0 and (username = ? or lower(email) = ?)",
        (login_name, login_name.lower()),
    )
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
def list_rumors(
    response: Response,
    agu_session: str | None = Cookie(default=None),
    q: str = "",
    tier: str = "",
    date: str = "",
    watch: int = 0,
    followed: int = 0,
    section: str = "",
    offset: int = 0,
    limit: int = 24,
) -> dict[str, Any]:
    user = current_user(response, agu_session)
    limit = max(1, min(60, limit))
    offset = max(0, offset)
    where = []
    args: list[Any] = []
    if q:
        where.append("(target like ? or stock_codes like ? or logic like ? or raw_content like ? or institution like ?)")
        args.extend([f"%{q}%"] * 5)
    if tier:
        where.append("ai_tier = ?")
        args.append(tier)
    if date:
        where.append("recommendation_date = ?")
        args.append(normalize_date(date))
    elif section == "today":
        where.append("recommendation_date >= ?")
        args.append(active_feed_date())
    if section == "today":
        where.append("stock_codes not in ('', '[]') and stock_codes is not null")
    if watch:
        if bool(user["is_guest"]):
            raise HTTPException(401, "请先登录后使用自选股")
        watch_items = watchlist_for_user(user["id"])
        if not watch_items:
            return {"items": [], "offset": offset, "limit": limit, "has_more": False, "rights_envelope": rights_envelope("rumor-feed", user["id"], f"empty-watch:{offset}:{limit}")}
        watch_terms = []
        for item in watch_items:
            watch_terms.append("(stock_codes like ? or target like ?)")
            args.extend([f"%{item['code']}%", f"%{item['name']}%"])
        where.append("(" + " or ".join(watch_terms) + ")")
    if followed:
        followed_ids = followed_provider_ids(user["id"])
        if not followed_ids:
            return {"items": [], "offset": offset, "limit": limit, "has_more": False, "rights_envelope": rights_envelope("rumor-feed", user["id"], f"empty-followed:{offset}:{limit}")}
        where.append("submitter_id in (" + ",".join("?" for _ in followed_ids) + ")")
        args.extend(sorted(followed_ids))
    report_rollup = """
        left join (
            select rumor_id,
                   count(*) report_count,
                   sum(case when reason = 'false_info' then 1 else 0 end) false_count,
                   sum(case when reason = 'promotion' then 1 else 0 end) promotion_count,
                   sum(case when reason = 'abuse' then 1 else 0 end) abuse_count
            from rumor_reports
            group by rumor_id
        ) mr on mr.rumor_id = r.id
    """
    penalty_expr = """
        case when coalesce(mr.report_count, 0) < 2 then 0
             else min(
                 32,
                 (coalesce(mr.report_count, 0) - 1) * 4
                 + coalesce(mr.false_count, 0) * 5
                 + coalesce(mr.promotion_count, 0) * 3
                 + coalesce(mr.abuse_count, 0) * 4
             )
        end
    """
    sql = f"select r.*, b.ret_t1_1, b.ret_t1_5, b.ret_t1_20, b.signal_value from rumors r left join backtests b on b.rumor_id = r.id {report_rollup}"
    if where:
        sql += " where " + " and ".join(where)
    sql += f" order by case when b.ret_t1_1 is not null then 0 else 1 end, coalesce(b.ret_t1_1, 0) desc, (r.ai_score - {penalty_expr}) desc, r.recommendation_date desc limit ? offset ?"
    if section == "today":
        raw_rows = query(sql, (*args, 500, 0))
        rows = raw_rows
    else:
        rows = query(sql, (*args, limit + 1, offset))
        has_more = len(rows) > limit
        rows = rows[:limit]
    unlocked = user_unlocked_ids(user)
    stats = discussion_stats([r["id"] for r in rows], user["id"])
    watched_codes = watched_codes_for_user(user["id"])
    # 历史消息（非当前情报日）按排序结果前 40% 强制免费可见
    historic_ids = [r["id"] for r in rows if is_historic_rumor(r)]
    free_count = max(1, len(historic_ids) * 4 // 10)
    free_ids = set(historic_ids[:free_count])
    items = [public_rumor(r, can_view(user, r, unlocked) or r["id"] in free_ids, stats.get(r["id"]), watched_codes, user["id"], user, slim=True) for r in rows]
    if section == "today":
        items.sort(key=lambda item: (1 if item.get("unlocked") else 0, item.get("recommendation_date") or "", int(item.get("ai_score") or 0)), reverse=True)
        has_more = len(items) > offset + limit
        items = items[offset : offset + limit]
    payload_key = ",".join(str(item["id"]) for item in items) or "empty"
    return {
        "items": items,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "rights_envelope": rights_envelope("rumor-feed", user["id"], f"{payload_key}:{offset}:{limit}"),
    }


@app.get("/api/rumors/{rumor_id}")
def get_rumor(rumor_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    rows = query("select * from rumors where id = ?", (rumor_id,))
    if not rows:
        raise HTTPException(404, "消息不存在")
    unlocked = user_unlocked_ids(user)
    force_open = is_historic_rumor(rows[0])
    if not force_open and not can_view(user, rows[0], unlocked):
        raise HTTPException(403, "需要分享同等价值消息或提升等级")
    bt = query("select * from backtests where rumor_id = ?", (rumor_id,))
    backtest = dict(bt[0]) if bt else None
    if backtest is not None:
        backtest["outcome"] = backtest_outcome(backtest)
    comments = query(
        """
        select id, display_name, content, created_at
        from rumor_comments
        where rumor_id = ?
        order by id desc
        limit 30
        """,
        (rumor_id,),
    )
    stats = discussion_stats([rumor_id], user["id"]).get(rumor_id)
    watched_codes = watched_codes_for_user(user["id"])
    return {
        "item": detail_rumor(rows[0], stats, watched_codes, user),
        "backtest": backtest,
        "comments": [dict(row) for row in comments],
        "rights_envelope": rights_envelope("rumor-detail", user["id"], str(rumor_id)),
    }


@app.post("/api/rumors/{rumor_id}/unlock")
def direct_unlock(rumor_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    rows = query("select * from rumors where id = ?", (rumor_id,))
    if not rows:
        raise HTTPException(404, "消息不存在")
    unlocked = user_unlocked_ids(user)
    if can_view(user, rows[0], unlocked):
        execute("insert or ignore into unlocks(user_id, rumor_id, reason, created_at) values (?, ?, 'already_allowed', ?)", (user["id"], rumor_id, now_iso()))
        return {"item": public_rumor(rows[0], True, viewer_id=user["id"], viewer=user), "quota_left": user["direct_quota"]}
    require_member(user)
    if int(user["direct_quota"] or 0) <= 0:
        access = tier_access(user, rows[0]["ai_tier"])
        raise HTTPException(403, f"直看额度不足，或需要达到{access['hint']}；可通过投稿或邀请新用户获得额度")
    execute("insert or ignore into unlocks(user_id, rumor_id, reason, created_at) values (?, ?, 'direct_quota', ?)", (user["id"], rumor_id, now_iso()))
    execute("update users set direct_quota = max(0, direct_quota - 1) where id = ?", (user["id"],))
    return {"item": public_rumor(rows[0], True, viewer_id=user["id"], viewer=user), "quota_left": max(0, int(user["direct_quota"] or 0) - 1)}


@app.get("/api/rumors/{rumor_id}/comments")
def rumor_comments(rumor_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    rows = query("select * from rumors where id = ?", (rumor_id,))
    if not rows:
        raise HTTPException(404, "消息不存在")
    if not can_view(user, rows[0], user_unlocked_ids(user)):
        raise HTTPException(403, "解锁后可查看完整讨论")
    comments = query(
        """
        select id, display_name, content, created_at
        from rumor_comments
        where rumor_id = ?
        order by id desc
        limit 50
        """,
        (rumor_id,),
    )
    return {
        "items": [dict(row) for row in comments],
        "discussion": discussion_stats([rumor_id], user["id"]).get(rumor_id),
    }


@app.post("/api/rumors/{rumor_id}/comments")
def post_rumor_comment(rumor_id: int, payload: CommentPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    rows = query("select * from rumors where id = ?", (rumor_id,))
    if not rows:
        raise HTTPException(404, "消息不存在")
    if not can_view(user, rows[0], user_unlocked_ids(user)):
        raise HTTPException(403, "解锁后可参与完整讨论")
    content = re.sub(r"\s+", " ", payload.content).strip()
    execute(
        "insert into rumor_comments(rumor_id, user_id, display_name, content, created_at) values (?, ?, ?, ?, ?)",
        (rumor_id, user["id"], user["display_name"], content, now_iso()),
    )
    reward = award_participation(user["id"], rumor_id, f"comment:{hashlib.sha1(content.encode()).hexdigest()[:12]}", 4, 0.2)
    comments = query(
        """
        select id, display_name, content, created_at
        from rumor_comments
        where rumor_id = ?
        order by id desc
        limit 50
        """,
        (rumor_id,),
    )
    return {
        "items": [dict(row) for row in comments],
        "discussion": discussion_stats([rumor_id], user["id"]).get(rumor_id),
        "participation_reward": reward,
    }


@app.post("/api/rumors/{rumor_id}/reactions")
def react_to_rumor(rumor_id: int, payload: ReactionPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    if not query("select 1 from rumors where id = ?", (rumor_id,)):
        raise HTTPException(404, "消息不存在")
    existing = query(
        "select 1 from rumor_reactions where user_id = ? and rumor_id = ? and reaction = ?",
        (user["id"], rumor_id, payload.reaction),
    )
    if existing:
        execute(
            "delete from rumor_reactions where user_id = ? and rumor_id = ? and reaction = ?",
            (user["id"], rumor_id, payload.reaction),
        )
        active = False
    else:
        execute(
            "insert into rumor_reactions(user_id, rumor_id, reaction, created_at) values (?, ?, ?, ?)",
            (user["id"], rumor_id, payload.reaction, now_iso()),
        )
        active = True
    reward = (
        award_participation(
            user["id"],
            rumor_id,
            f"reaction:{payload.reaction}",
            {"useful": 2, "doubt": 3}.get(payload.reaction, 1),
            {"useful": 0.1, "doubt": 0.1}.get(payload.reaction, 0.0),
        )
        if active
        else {"awarded": False, "xp_delta": 0, "reputation_delta": 0.0, "message": "已取消反馈"}
    )
    return {"active": active, "discussion": discussion_stats([rumor_id], user["id"]).get(rumor_id), "participation_reward": reward}


@app.post("/api/rumors/{rumor_id}/reports")
def report_rumor(rumor_id: int, payload: ReportPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    rows = query("select * from rumors where id = ?", (rumor_id,))
    if not rows:
        raise HTTPException(404, "消息不存在")
    rumor = rows[0]
    if rumor["submitter_id"] == user["id"]:
        raise HTTPException(400, "不能举报自己提交的线索")
    if not can_view(user, rumor, user_unlocked_ids(user)):
        raise HTTPException(403, "解锁并查看后才能举报")
    details = re.sub(r"\s+", " ", payload.details).strip()
    execute(
        """
        insert or replace into rumor_reports(user_id, rumor_id, reason, details, created_at)
        values (?, ?, ?, ?, ?)
        """,
        (user["id"], rumor_id, payload.reason, details, now_iso()),
    )
    discussion = discussion_stats([rumor_id], user["id"]).get(rumor_id)
    return {
        "active": True,
        "reason": payload.reason,
        "label": REPORT_REASON_LABELS.get(payload.reason, payload.reason),
        "discussion": discussion,
        "moderation": discussion.get("moderation") if discussion else None,
    }


@app.get("/api/moderation-summary")
def moderation_summary(response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    rows = query(
        """
        select rr.rumor_id, r.target, r.ai_score, r.ai_tier,
               count(*) reports,
               max(rr.created_at) latest_report
        from rumor_reports rr
        join rumors r on r.id = rr.rumor_id
        group by rr.rumor_id
        order by reports desc, latest_report desc
        limit 10
        """
    )
    reason_rows = query(
        """
        select reason, count(*) n
        from rumor_reports
        group by reason
        order by n desc
        """
    )
    stats = report_stats([row["rumor_id"] for row in rows], user["id"])
    total_rumors = int(query("select count(*) n from rumors")[0]["n"] or 0)
    flagged_total = int(query("select count(distinct rumor_id) n from rumor_reports")[0]["n"] or 0)
    reports_total = int(query("select count(*) n from rumor_reports")[0]["n"] or 0)
    review_total = sum(1 for item in stats.values() if item.get("trust_state") in {"review", "limited"})
    clean_total = max(0, total_rumors - flagged_total)
    health_score = round(clean_total / max(1, total_rumors) * 100, 1)
    guardrails = [
        {
            "key": "score_penalty",
            "label": "举报自动降权",
            "value": f"{flagged_total}条",
            "state": "active" if flagged_total else "clear",
            "detail": "被举报线索会降低有效分和排序权重。",
        },
        {
            "key": "dedupe",
            "label": "同用户同原因去重",
            "value": f"{reports_total}次",
            "state": "clear",
            "detail": "同一用户对同一原因重复举报只计一次。",
        },
        {
            "key": "review_queue",
            "label": "待复核队列",
            "value": f"{review_total}条",
            "state": "review" if review_total else "clear",
            "detail": "多原因或高强度举报会进入重点复核。",
        },
        {
            "key": "source_impact",
            "label": "信息源约束",
            "value": f"{health_score:.1f}%",
            "state": "clear" if health_score >= 90 else "watch",
            "detail": "举报和存疑反馈会进入信息源反馈分。",
        },
    ]
    rights_protection = {
        "headline": "原创情报已启用隐形版权保护",
        "summary": "页面、接口、复制内容和单条情报详情都会携带不同层级的版权与取证标记。",
        "license": "CC-BY-NC-SA-4.0",
        "mark": hidden_copyright_mark(),
        "layers": [
            {"key": "headers", "label": "响应头标记", "state": "active", "detail": "所有响应写入 X-StockWhisper-Mark 和内容使用策略。"},
            {"key": "copy", "label": "复制水印", "state": "active", "detail": "复制文本会追加可见来源和不可见零宽标记。"},
            {"key": "fingerprint", "label": "内容指纹", "state": "active", "detail": "每条情报按用户、解锁状态生成独立指纹。"},
            {"key": "forensic", "label": "取证签名", "state": "active", "detail": "详情内容写入稳定签名和内容哈希，便于后续比对。"},
        ],
    }
    return {
        "flagged_total": flagged_total,
        "reports_total": reports_total,
        "total_rumors": total_rumors,
        "clean_total": clean_total,
        "review_total": review_total,
        "health_score": health_score,
        "status": "clean" if flagged_total == 0 else "watch" if review_total == 0 else "review",
        "policy": "举报会进入可信度校验账本，影响有效分、信息源评分和排序权重。",
        "guardrails": guardrails,
        "rights_protection": rights_protection,
        "reasons": [
            {"reason": row["reason"], "label": REPORT_REASON_LABELS.get(row["reason"], row["reason"]), "count": int(row["n"])}
            for row in reason_rows
        ],
        "items": [
            {
                "rumor_id": row["rumor_id"],
                "target": mask_target(row["target"]) if not tier_access(user, row["ai_tier"])["allowed"] else row["target"],
                "ai_tier": row["ai_tier"],
                "ai_score": row["ai_score"],
                "latest_report": row["latest_report"],
                "moderation": stats.get(row["rumor_id"]),
            }
            for row in rows
        ],
    }


@app.post("/api/rumors/summarize")
def summarize(payload: RumorPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    current_user(response, agu_session)
    return summarize_payload(payload)


@app.post("/api/rumors")
def submit_rumor(payload: RumorPayload, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(response, agu_session)
    require_member(user)
    before_user = user_out(user)
    summary = summarize_payload(payload)
    clean = {
        "target": payload.target.strip() or summary["target"],
        "stock_codes": json.dumps(stock_items_from_payload(payload.target.strip() or summary["target"], payload.stock_codes), ensure_ascii=False),
        "logic": payload.logic.strip() or summary["logic"],
        "raw_content": payload.raw_content.strip(),
        "institution": payload.institution.strip() or summary["institution"],
        "recommender": payload.recommender.strip() or summary["recommender"],
        "recommendation_date": payload.recommendation_date,
    }
    if not clean["target"] or not clean["logic"]:
        raise HTTPException(422, "需要可识别的标的和核心逻辑")
    novelty = novelty_analysis(clean)
    risk = risk_analysis(clean)
    scored = apply_risk_to_score(apply_novelty_to_score(score_text(clean), novelty), risk)
    scored = score_with_llm_logic(scored, summary)
    reason_payload = {
        "reasons": scored["reasons"],
        "logic_score": scored.get("llm_logic_score"),
        "logic_reason": scored.get("llm_logic_reason") or "",
        "summary_source": summary.get("summary_source", "heuristic"),
    }
    rec_date = normalize_date(payload.recommendation_date)
    execute(
        """
        insert into rumors
        (submitter_id, submitter_name, recommendation_date, recommender, target, stock_codes, logic, raw_content,
         institution, key_points, ai_score, ai_tier, ai_reasons, created_at, source)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'community')
        """,
        (
            user["id"],
            user["display_name"],
            rec_date,
            clean["recommender"],
            clean["target"],
            clean["stock_codes"],
            clean["logic"],
            clean["raw_content"],
            clean["institution"],
            json.dumps(summary["key_points"], ensure_ascii=False),
            scored["score"],
            scored["tier"],
            json.dumps(reason_payload, ensure_ascii=False),
            now_iso(),
        ),
    )
    rumor = query("select * from rumors order by id desc limit 1")[0]
    execute("insert or ignore into unlocks(user_id, rumor_id, reason, created_at) values (?, ?, 'own_submission', ?)", (user["id"], rumor["id"], now_iso()))
    unlock_match = (
        unlock_similar_match(user["id"], scored["score"], scored["tier"], clean["stock_codes"], clean["target"])
        if scored["score"] >= 55
        else None
    )
    unlocked = unlock_match["item"] if unlock_match else None
    refresh_backtests(limit=20)
    recalc_user_scores()
    refreshed_user = query("select * from users where id = ?", (user["id"],))[0]
    quota_delta = HIGH_TIER_SUBMISSION_DIRECT_QUOTA if scored["tier"] in {"A", "S"} else 0
    if quota_delta:
        execute("update users set direct_quota = direct_quota + ? where id = ?", (quota_delta, user["id"]))
        refreshed_user = query("select * from users where id = ?", (user["id"],))[0]
    after_user = user_out(refreshed_user)
    submission_reward = {
        "xp_delta": int(after_user["xp"] or 0) - int(before_user["xp"] or 0),
        "quota_delta": quota_delta,
        "contribution_delta": round(float(after_user["contribution"] or 0) - float(before_user["contribution"] or 0), 1),
        "source_score_delta": round(float(after_user["provider_grade"]["score"] or 0) - float(before_user["provider_grade"]["score"] or 0), 1),
        "level": level_for(refreshed_user["xp"]),
        "provider_grade": after_user["provider_grade"],
        "exchange_label": f"已进入{scored['tier']}级交换池" if scored["score"] >= 55 else "已记录为草稿级贡献，建议继续补强",
        "unlocked_label": f"已匹配解锁：{unlocked['target']}" if unlocked else "暂无同级可匹配解锁，已进入等待池",
        "unlock_match": {
            "reason": unlock_match["reason"],
            "score_gap": unlock_match["score_gap"],
            "matched_tier": unlock_match["matched_tier"],
        }
        if unlock_match
        else None,
    }
    return {
        "item": public_rumor(rumor, True, viewer_id=user["id"], viewer=user),
        "unlocked": public_rumor(unlocked, True, viewer_id=user["id"], viewer=user) if unlocked else None,
        "score": scored,
        "summary": summary,
        "novelty": novelty,
        "risk_analysis": risk,
        "submission_reward": submission_reward,
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
        item["outcome"] = backtest_outcome(item)
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
        select id, display_name, xp, reputation, direct_quota, invite_count, created_at
        from users where is_guest = 0 or xp > 0
        order by xp desc, reputation desc limit 100
        """
    )
    items = []
    for r in rows:
        contribution = contribution_for_user(r["id"])
        feedback_score = provider_feedback_score(r["id"])
        grade = provider_grade(r["xp"], r["reputation"], contribution, r["invite_count"] or 0, feedback_score)
        items.append(
            {
                **dict(r),
                "level": level_for(r["xp"])["name"],
                "contribution": contribution,
                "feedback_score": feedback_score,
                "provider_grade": grade,
                "radar": calc_user_radar(r["id"]),
            }
        )
    items.sort(
        key=lambda item: (
            -float(item["provider_grade"]["score"]),
            -float(item["contribution"] or 0),
            -float(item["reputation"] or 0),
            -int(item["xp"] or 0),
            int(item["id"]),
        )
    )
    ranked = []
    for index, item in enumerate(items[:50], start=1):
        ranked.append({**item, "source_rank": index})
    return {"items": ranked, "rank_rule": "provider_grade.score desc, contribution desc, reputation desc, xp desc"}


@app.get("/api/providers/{provider_id}")
def get_provider_profile(provider_id: int, response: Response, agu_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    viewer = current_user(response, agu_session)
    return provider_profile(provider_id, viewer)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8289"))
    uvicorn.run("app.main:app", host=host, port=port)
