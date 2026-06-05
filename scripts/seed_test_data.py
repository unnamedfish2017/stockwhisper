"""Generate diversified test rumors for waterfall/pagination testing.

Inserts rows directly with source='reference' using the app's real
score_text() so AI score/tier distribution is authentic.
Idempotent-ish: only adds rows tagged submitter_name='测试数据'.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import db, score_text, heuristic_summary, now_iso  # noqa: E402

random.seed(42)

TARGETS = [
    "新相微", "华灿光电", "中际旭创", "天孚通信", "工业富联", "寒武纪", "海光信息",
    "胜宏科技", "沪电股份", "生益科技", "立讯精密", "歌尔股份", "兆易创新", "北方华创",
    "中芯国际", "韦尔股份", "卓胜微", "斯达半导", "阳光电源", "宁德时代", "亿纬锂能",
    "比亚迪", "汇川技术", "金山办公", "科大讯飞", "三六零", "恒生电子", "用友网络",
]
INSTITUTIONS = ["中信证券", "国君电子", "华泰策略", "招商TMT", "中金海外", "私募内部群", "游资席位", ""]
RECOMMENDERS = ["小道哥", "游资王", "研究员A", "群友爆料", "机构朋友", ""]
CATALYSTS = ["订单", "涨价", "合作", "中标", "产能", "AI", "国产替代", "光模块", "业绩", "并购", "政策", "客户", "验证"]


def make_content(target: str, n_catalyst: int, n_ticker: int, length: int) -> str:
    cats = random.sample(CATALYSTS, min(n_catalyst, len(CATALYSTS)))
    tickers = [f"{random.choice('036')}{random.randint(10000, 99999)}" for _ in range(n_ticker)]
    body = (
        f"【{random.choice(INSTITUTIONS) or '匿名'}】{target}："
        + "，".join(cats)
        + "方面出现明显边际变化，" * 3
    )
    body += "据产业链反馈下游需求持续超预期，关注后续催化兑现节奏。" * (length // 30 + 1)
    if tickers:
        body += " 相关代码 " + " ".join(tickers)
    return body[: max(length, 40)]


def build_rows() -> list[dict]:
    rows = []
    today = date.today()
    # spread across 20 days
    for i in range(110):
        target = random.choice(TARGETS)
        # bias parameters to spread tiers
        n_catalyst = random.randint(0, 6)
        n_ticker = random.randint(0, 3)
        length = random.choice([60, 120, 240, 600, 1200, 1800])
        rec_date = (today - timedelta(days=random.randint(0, 19))).strftime("%Y-%m-%d")
        content = make_content(target, n_catalyst, n_ticker, length)
        rows.append(
            {
                "submitter": "测试数据",
                "recommendation_date": rec_date,
                "recommender": random.choice(RECOMMENDERS),
                "target": target,
                "logic": f"{target}核心逻辑：{'/'.join(random.sample(CATALYSTS, 2))}驱动",
                "raw_content": content,
                "institution": random.choice(INSTITUTIONS),
                "created_at": now_iso(),
            }
        )
    return rows


def main() -> None:
    con = db()
    existing = con.execute("select count(*) n from rumors where submitter_name = '测试数据'").fetchone()[0]
    if existing:
        print(f"already have {existing} test rows; skipping insert")
        con.close()
        return
    rows = build_rows()
    tier_count: dict[str, int] = {}
    for payload in rows:
        scored = score_text(payload)
        tier_count[scored["tier"]] = tier_count.get(scored["tier"], 0) + 1
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
                payload["created_at"],
            ),
        )
    con.commit()
    con.close()
    print(f"inserted {len(rows)} test rows; tier distribution: {tier_count}")


if __name__ == "__main__":
    main()
