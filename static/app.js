const state = {
  user: null,
  level: null,
  view: "feed",
  feedDate: "",
  selectedTier: "",
  feedOffset: 0,
  feedHasMore: true,
  feedLoading: false,
  community: null,
  communityRooms: null,
  activityFeed: null,
  exchangeDesk: null,
  moderationSummary: null,
  referral: null,
  activation: null,
  invitePreview: null,
  opportunity: null,
  inviteCodeFromUrl: "",
  framework: null,
  growth: null,
  sourceUpgrade: null,
  watchlist: null,
  watchOnly: false,
  followedProviders: null,
  followedOnly: false,
  activeRumorId: null,
  lastQualityPreview: null,
  rightsEnvelope: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const REPORT_OPTIONS = [
  ["false_info", "疑似不实"],
  ["promotion", "软广诱导"],
  ["duplicate", "重复搬运"],
  ["stale", "过期失效"],
  ["abuse", "违规内容"],
];

const COPY_RIGHTS_MARK = "StockWhisper::CC-BY-NC-SA-4.0::openclaw-community-intel";

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    credentials: "same-origin",
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "请求失败");
  return data;
}

function pct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v) * 100;
  return `<span class="${n >= 0 ? "pos" : "neg"}">${n.toFixed(1)}%</span>`;
}

function todayInputValue() {
  const d = new Date();
  const tzOffset = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - tzOffset).toISOString().slice(0, 10);
}

function setDefaultRecommendationDate() {
  const input = $("#submitForm")?.elements.recommendation_date;
  if (input && !input.value) input.value = todayInputValue();
}

function renderAuthButtons() {
  const loggedIn = !!state.user && !state.user.is_guest;
  $("#loginOpen").style.display = loggedIn ? "none" : "";
  $("#logoutBtn").style.display = loggedIn ? "" : "none";
}

function renderRadar(radar) {
  if (!radar) return "";
  const labels = ["活跃度", "进攻性", "防守性", "独特性"];
  const vals = [radar.activity, radar.offense, radar.defense, radar.uniqueness];
  const cx = 60, cy = 60, r = 46;
  const angles = labels.map((_, i) => (i * 2 * Math.PI / 4) - Math.PI / 2);
  const pt = (v, i) => {
    const ratio = v / 100;
    return [cx + r * ratio * Math.cos(angles[i]), cy + r * ratio * Math.sin(angles[i])];
  };
  const grid = [0.25, 0.5, 0.75, 1].map(s =>
    `<polygon points="${angles.map((_, i) => { const x = cx + r * s * Math.cos(angles[i]); const y = cy + r * s * Math.sin(angles[i]); return `${x},${y}`; }).join(" ")}" fill="none" stroke="var(--line)" stroke-width="0.8"/>`
  ).join("");
  const axes = angles.map((a, i) => `<line x1="${cx}" y1="${cy}" x2="${cx + r * Math.cos(a)}" y2="${cy + r * Math.sin(a)}" stroke="var(--line)" stroke-width="0.8"/>`).join("");
  const shape = `<polygon points="${vals.map((v, i) => pt(v, i).join(",")).join(" ")}" fill="var(--accent)" fill-opacity="0.25" stroke="var(--accent)" stroke-width="1.5"/>`;
  const labelEls = labels.map((l, i) => {
    const [x, y] = pt(115, i);
    return `<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="middle" font-size="9" fill="var(--muted)">${l}</text>`;
  }).join("");
  const dots = vals.map((v, i) => {
    const [x, y] = pt(v, i);
    return `<circle cx="${x}" cy="${y}" r="2.5" fill="var(--accent)"><title>${labels[i]}: ${v}</title></circle>`;
  }).join("");
  return `<svg viewBox="0 0 120 120" width="120" height="120" style="display:block;margin:8px auto 0">${grid}${axes}${shape}${dots}${labelEls}</svg>`;
}

function renderProfile() {
  const u = state.user;
  if (!u) return;
  renderAuthButtons();
  const inviteUrl = !u.is_guest && u.invite_code ? `${location.origin}/?invite=${encodeURIComponent(u.invite_code)}` : "";
  const grade = u.provider_grade || {};
  const upgrade = state.growth?.upgrade || u.upgrade_plan || {};
  $("#profile").innerHTML = `
    <h3>${esc(u.display_name || u.username)}</h3>
    <p>${u.is_guest ? "默认游客会话" : "已登录账号"}</p>
    <div class="provider-grade">
      <strong>${esc(grade.name || "新晋观察员")}</strong>
      <span>${Number(grade.score || 0).toFixed(1)} / 100</span>
    </div>
    ${upgrade.next ? `
      <div class="upgrade-mini">
        <span>距 ${esc(upgrade.next.name)} 还差 ${Number(upgrade.needed || 0).toFixed(1)}</span>
        <i><b style="width:${Math.min(100, Number(upgrade.progress || 0))}%"></b></i>
      </div>
    ` : `<div class="upgrade-mini"><span>已达最高信息源等级</span><i><b style="width:100%"></b></i></div>`}
    <div class="metric"><span>等级</span><strong>${state.level.name}</strong></div>
    <div class="metric"><span>经验值</span><strong>${u.xp}</strong></div>
    <div class="metric"><span>贡献度</span><strong>${Number(u.contribution || 0).toFixed(1)}</strong></div>
    <div class="metric"><span>信誉分</span><strong>${Number(u.reputation).toFixed(1)}</strong></div>
    <div class="metric"><span>直看额度</span><strong>${u.direct_quota}</strong></div>
    <div class="metric"><span>邀请</span><strong>${u.invite_count || 0}</strong></div>
    ${inviteUrl ? `<button class="copy-invite ghost" data-invite="${esc(inviteUrl)}">复制邀请链接</button>` : ""}
    ${renderRadar(u.radar)}
  `;
}

function switchView(view) {
  state.view = view;
  $$(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#${view}View`).classList.add("active");
  if (view === "backtest") loadBacktests();
  if (view === "rank") {
    loadSourceUpgradeCenter();
    loadLeaderboard();
  }
}

async function loadMe() {
  const data = await api("/api/me");
  state.user = data.user;
  state.level = data.level;
  renderProfile();
}

async function loadRumors(reset = true) {
  if (state.feedLoading) return;
  if (!reset && !state.feedHasMore) return;
  state.feedLoading = true;
  const grid = $("#rumorGrid");
  if (reset) {
    state.feedOffset = 0;
    state.feedHasMore = true;
    grid.innerHTML = "";
  }
  const sentinel = $("#feedSentinel");
  if (sentinel) sentinel.textContent = "加载中…";
  try {
    const q = encodeURIComponent($("#searchInput").value.trim());
    const tier = encodeURIComponent(state.selectedTier || $("#tierFilter").value);
    const date = encodeURIComponent(state.feedDate || "");
    const watch = state.watchOnly ? 1 : 0;
    const followed = state.followedOnly ? 1 : 0;
    const data = await api(`/api/rumors?q=${q}&tier=${tier}&date=${date}&watch=${watch}&followed=${followed}&offset=${state.feedOffset}&limit=24`);
    state.rightsEnvelope = data.rights_envelope || state.rightsEnvelope;
    grid.insertAdjacentHTML("beforeend", data.items.map(renderCard).join(""));
    state.feedOffset += data.items.length;
    state.feedHasMore = data.has_more;
    if (sentinel) {
      if (grid.children.length === 0) sentinel.textContent = "暂无情报";
      else sentinel.textContent = state.feedHasMore ? "下拉加载更多" : "已经到底了";
    }
  } catch (err) {
    if (sentinel) sentinel.textContent = err.message;
  } finally {
    state.feedLoading = false;
  }
}

async function loadDailyStats() {
  const data = await api("/api/daily-stats");
  state.feedDate = data.date;
  $("#dailyStats").innerHTML = data.items.map(renderTierStat).join("");
  $("#tierPanel").innerHTML = `
    <strong>${data.date} 等级总览</strong>
    <span>当前贡献度 ${Number(data.contribution || 0).toFixed(1)}，贡献度按 30 天半衰期衰减。</span>
  `;
  $$(".tier-card").forEach((card) => card.addEventListener("click", () => selectTier(card.dataset.tier, card.dataset.allowed === "true")));
  await loadRumors(true);
}

async function loadCommunityInsight() {
  const data = await api("/api/community-insight");
  state.community = data;
  state.rightsEnvelope = data.rights_envelope || state.rightsEnvelope;
  const stats = data.stats || {};
  $("#pulseCopy").textContent = `${data.active_date}：社区累计 ${stats.total_rumors || 0} 条线索，社区投稿 ${stats.community_total || 0} 条，最高评分 ${stats.top_score || 0}。`;
  $("#pulseMetrics").innerHTML = [
    ["情报总量", stats.total_rumors || 0],
    ["社区投稿", stats.community_total || 0],
    ["平均评分", Number(stats.avg_score || 0).toFixed(1)],
    ["已解锁", stats.unlocked_count || 0],
  ].map(([label, value]) => `<div class="pulse-card"><span>${label}</span><strong>${value}</strong></div>`).join("");
  renderValueProof(data.value_proof || []);
  renderDailyBrief(data.daily_brief || {});
  renderDailyWorkflow(data.daily_workflow || {});
  state.opportunity = data.opportunity_summary || {};
  renderOpportunityDeck(state.opportunity);
  renderBountyBoard(data.bounty_board || {});
  renderActionQueue(data.action_queue || []);
  renderRecentBacktestShowcase(data.recent_backtest_showcase || {});
  renderTodaySignalBoard(data.today_signal_board || {});
  $("#highlightList").innerHTML = (data.highlights || []).map(renderHighlight).join("") || `<p class="empty">暂无高分线索</p>`;
  renderTopicRadar(data.topic_radar || {});
  renderInvitePanel();
  refreshAuthIncentiveIfOpen();
}

function renderValueProof(items) {
  $("#valueProof").innerHTML = (items || []).map((item) => `
    <div class="proof-chip ${esc(item.state || "quiet")}">
      <span>${esc(item.label)}</span>
      <strong>${esc(item.value)}</strong>
      <small>${esc(item.detail)}</small>
    </div>
  `).join("");
}

async function loadCommunityRooms() {
  const data = await api("/api/community-rooms");
  state.communityRooms = data;
  renderCommunityRooms();
}

async function loadActivityFeed() {
  const data = await api("/api/activity-feed");
  state.activityFeed = data;
  renderActivityPanel();
}

async function loadExchangeDesk() {
  const data = await api("/api/exchange-desk");
  state.exchangeDesk = data;
  renderExchangePanel();
}

async function loadModerationSummary() {
  const data = await api("/api/moderation-summary");
  state.moderationSummary = data;
  renderTrustCenterPanel();
}

async function loadReferralCenter() {
  const data = await api("/api/referral-center");
  state.referral = data;
  renderInvitePanel();
  refreshAuthIncentiveIfOpen();
}

async function loadActivationCenter() {
  const data = await api("/api/activation-center");
  state.activation = data;
  renderActivationPanel();
  refreshAuthIncentiveIfOpen();
}

function renderExchangePanel() {
  const data = state.exchangeDesk || {};
  const resources = data.resources || {};
  const opportunities = data.opportunities || [];
  const actions = data.actions || [];
  const directCopy = state.user?.is_guest ? "注册后直看" : `${resources.direct_quota || 0} 直看`;
  $("#exchangePanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">EXCHANGE DESK</span>
        <h3>情报交换台</h3>
      </div>
      <span>${directCopy} · ${Number(resources.contribution || 0).toFixed(1)} 贡献</span>
    </div>
    <div class="exchange-layout">
      <div class="exchange-opportunities">
        ${opportunities.slice(0, 3).map((entry) => {
          const item = entry.item || {};
          const gap = entry.gap || {};
          return `
            <article class="exchange-card">
              <div>
                <span class="badge">${esc(item.ai_tier)}${item.ai_score}</span>
                <strong>${esc(item.target)}</strong>
              </div>
              <p>${esc(item.logic)}</p>
              <footer>
                <span>差 ${gap.xp_gap || 0} XP</span>
                <span>差 ${Number(gap.contribution_gap || 0).toFixed(1)} 贡献</span>
                ${entry.direct_unlockable ? `<button class="ghost exchange-unlock" data-id="${item.id}">直看</button>` : ""}
              </footer>
            </article>
          `;
        }).join("") || `<p class="empty">当前等级已覆盖样本池，继续投稿可刷新交换池。</p>`}
      </div>
      <div class="exchange-actions">
        ${actions.slice(0, 4).map((action) => `
          <button class="ghost exchange-action" data-key="${esc(action.key)}" data-view="${esc(action.view || "feed")}" data-invite="${esc(action.invite_code || "")}">
            <strong>${esc(action.title)}</strong>
            <span>${esc(action.description)}</span>
          </button>
        `).join("")}
      </div>
    </div>
  `;
  $$("#exchangePanel .exchange-unlock").forEach((btn) => btn.addEventListener("click", () => unlockRumor(btn.dataset.id)));
  $$("#exchangePanel .exchange-action").forEach((btn) => btn.addEventListener("click", () => runExchangeAction(btn)));
}

function renderTrustCenterPanel() {
  const data = state.moderationSummary || {};
  const reasons = data.reasons || [];
  const items = data.items || [];
  const guardrails = data.guardrails || [];
  const rights = data.rights_protection || {};
  $("#trustCenterPanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">TRUST CENTER</span>
        <h3>社区可信度中枢</h3>
      </div>
      <span>${Number(data.health_score || 0).toFixed(1)}% 清洁</span>
    </div>
    <div class="trust-layout">
      <div class="trust-metrics">
        <div><span>线索总量</span><strong>${data.total_rumors || 0}</strong></div>
        <div><span>清洁样本</span><strong>${data.clean_total || 0}</strong></div>
        <div><span>举报线索</span><strong>${data.flagged_total || 0}</strong></div>
        <div><span>待复核</span><strong>${data.review_total || 0}</strong></div>
      </div>
      <div class="trust-policy">
        <strong>${esc(data.status === "review" ? "存在待复核风险" : data.status === "watch" ? "有举报，已降权观察" : "当前社区较清洁")}</strong>
        <p>${esc(data.policy || "举报会影响有效分和排序权重。")}</p>
        <div>${reasons.slice(0, 4).map((item) => `<span>${esc(item.label)} ${item.count}</span>`).join("") || `<span>暂无举报</span>`}</div>
      </div>
      <div class="rights-protection">
        <div>
          <span class="eyebrow">RIGHTS MARK</span>
          <strong>${esc(rights.headline || "原创情报版权保护")}</strong>
          <p>${esc(rights.summary || "复制与详情会携带平台版权标记。")}</p>
        </div>
        <div class="rights-layers">
          ${(rights.layers || []).slice(0, 4).map((item) => `
            <span class="${esc(item.state || "active")}">${esc(item.label || "")}</span>
          `).join("")}
        </div>
      </div>
      <div class="trust-guardrails">
        ${guardrails.slice(0, 4).map((item) => `
          <div class="${esc(item.state || "clear")}">
            <span>${esc(item.label || "")}</span>
            <strong>${esc(item.value || "")}</strong>
            <p>${esc(item.detail || "")}</p>
          </div>
        `).join("")}
      </div>
      <div class="trust-watchlist">
        ${items.slice(0, 3).map((item) => `
          <button class="ghost trust-item" data-id="${item.rumor_id}">
            <span>${esc(item.ai_tier)}${item.ai_score}</span>
            <strong>${esc(item.target)}</strong>
            <em>${trustLabel(item.moderation?.trust_state)} · ${item.moderation?.reports || 0}</em>
          </button>
        `).join("") || `<p class="empty">暂无被举报线索。</p>`}
      </div>
    </div>
  `;
  $$("#trustCenterPanel .trust-item").forEach((btn) => btn.addEventListener("click", () => openRumor(btn.dataset.id).catch((err) => alert(err.message))));
}

async function runExchangeAction(btn) {
  const key = btn.dataset.key;
  if (key === "register") {
    setRegMode("reg");
    $("#authDialog").showModal();
    return;
  }
  if (key === "invite" && btn.dataset.invite) {
    const url = `${location.origin}/?invite=${encodeURIComponent(btn.dataset.invite)}`;
    try {
      await navigator.clipboard.writeText(url);
      btn.querySelector("strong").textContent = "已复制邀请链接";
    } catch {
      switchView(btn.dataset.view || "rank");
    }
    return;
  }
  switchView(btn.dataset.view || "feed");
}

function renderActivityPanel() {
  const data = state.activityFeed || {};
  const items = data.items || [];
  const labels = {
    watch_hit: "自选",
    followed_source: "关注源",
    discussion: "求证",
    growth_tip: "成长",
    community_pick: "样本",
  };
  $("#activityPanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">SIGNAL TAPE</span>
        <h3>${data.personalized ? "你的实时信号" : "社区价值样本"}</h3>
      </div>
      <span>${data.watch_hits || 0} 自选 · ${data.followed_hits || 0} 关注源</span>
    </div>
    <div class="activity-strip">
      ${items.slice(0, 8).map((item, idx) => {
        const rumor = item.rumor || {};
        return `
          <article class="activity-item ${esc(item.kind)}">
            <span class="activity-kind">${esc(labels[item.kind] || item.kind)}</span>
            <div>
              <strong>${esc(item.title)}</strong>
              <p>${esc(item.body)}</p>
              ${rumor.id ? `<small>${esc(rumor.target)} · ${esc(rumor.ai_tier)}${rumor.ai_score}</small>` : ""}
            </div>
            <button class="ghost activity-action" data-index="${idx}">${esc(item.action?.label || "查看")}</button>
          </article>
        `;
      }).join("") || `<p class="empty">暂无信号</p>`}
    </div>
  `;
  $$("#activityPanel .activity-action").forEach((btn) => btn.addEventListener("click", () => runActivityAction(items[Number(btn.dataset.index)]?.action || {})));
}

function runActivityAction(action) {
  if (action.view === "detail" && action.rumor_id) {
    openRumor(action.rumor_id).catch((err) => alert(err.message));
    return;
  }
  const view = action.view || "feed";
  switchView(view);
  if (view !== "feed") return;
  if (Object.prototype.hasOwnProperty.call(action, "query")) $("#searchInput").value = action.query || "";
  if (action.watch) {
    state.watchOnly = true;
    state.followedOnly = false;
  }
  if (action.followed) {
    state.followedOnly = true;
    state.watchOnly = false;
  }
  renderWatchPanel();
  renderProviderFollowPanel();
  loadRumors(true);
}

function renderDailyBrief(brief) {
  $("#dailyBrief").innerHTML = `
    <strong>${esc(brief.headline || "等待社区线索聚合")}</strong>
    <ul>
      ${(brief.bullets || []).slice(0, 3).map((item) => `<li>${esc(item)}</li>`).join("")}
    </ul>
    <div class="brief-actions">
      ${(brief.actions || []).map((item) => `<button class="ghost brief-action" data-view="${esc(item.view || "feed")}" data-query="${esc(item.query || "")}" data-tier="${esc(item.tier || "")}">${esc(item.label)}</button>`).join("")}
    </div>
    <p>${esc((brief.risk_notes || [])[0] || "")}</p>
  `;
  $$("#dailyBrief .brief-action").forEach((btn) => btn.addEventListener("click", () => {
    const view = btn.dataset.view || "feed";
    switchView(view);
    if (view === "feed") {
      $("#searchInput").value = btn.dataset.query || "";
      $("#tierFilter").value = btn.dataset.tier || "";
      state.selectedTier = btn.dataset.tier || "";
      loadRumors(true);
    }
  }));
}

function renderDailyWorkflow(workflow) {
  const root = $("#dailyWorkflow");
  if (!root) return;
  const steps = workflow.steps || [];
  root.innerHTML = `
    <div class="workflow-head">
      <div>
        <span class="eyebrow">RETAIL WORKFLOW</span>
        <strong>${esc(workflow.headline || "今日情报处理路径")}</strong>
      </div>
      <small>${esc(workflow.summary || "按筛选、求证、跟踪处理线索。")}</small>
    </div>
    <div class="workflow-steps">
      ${steps.slice(0, 3).map((item, idx) => `
        <button type="button" class="workflow-step ${esc(item.key || "")}"
          data-view="${esc(item.view || "feed")}" data-query="${esc(item.query || "")}" data-tier="${esc(item.tier || "")}">
          <span>${idx + 1}</span>
          <div>
            <small>${esc(item.label || "")} · ${esc(item.metric || "")}</small>
            <strong>${esc(item.title || "")}</strong>
            <p>${esc(item.detail || "")}</p>
          </div>
          <em>${esc(item.action || "查看")}</em>
        </button>
      `).join("")}
    </div>
  `;
  $$("#dailyWorkflow .workflow-step").forEach((btn) => btn.addEventListener("click", async () => {
    const view = btn.dataset.view || "feed";
    switchView(view);
    if (view !== "feed") return;
    $("#searchInput").value = btn.dataset.query || "";
    $("#tierFilter").value = btn.dataset.tier || "";
    state.selectedTier = btn.dataset.tier || "";
    await loadRumors(true);
  }));
}

function renderOpportunityDeck(opportunity) {
  const root = $("#opportunityDeck");
  if (!root) return;
  const cards = opportunity.cards || [];
  const actions = opportunity.actions || [];
  const bestPick = opportunity.best_pick || null;
  const plan = bestPick?.verification_plan || null;
  root.innerHTML = `
    <div class="opportunity-head">
      <div>
        <span class="eyebrow">ACTIONABLE EDGE</span>
        <strong>今日机会台</strong>
      </div>
      <small>${esc(opportunity.headline || "根据高价值线索、直看额度和热点房间生成下一步。")}</small>
    </div>
    <div class="opportunity-cards">
      ${cards.slice(0, 4).map((item) => `
        <div class="opportunity-card ${esc(item.state || "quiet")}">
          <span>${esc(item.label)}</span>
          <strong>${esc(item.value)}</strong>
          <small>${esc(item.detail || "")}</small>
        </div>
      `).join("")}
    </div>
    ${bestPick ? `
      <button type="button" class="opportunity-best-pick opportunity-action"
        data-key="${esc(bestPick.action?.key || "open_best")}" data-view="${esc(bestPick.action?.view || "feed")}"
        data-query="${esc(bestPick.action?.query || bestPick.target || "")}" data-tier="${esc(bestPick.action?.tier || bestPick.tier || "")}">
        <span class="best-pick-badge">${esc(bestPick.tier || "")}${esc(bestPick.score ?? "")}</span>
        <div>
          <strong>${esc(bestPick.target || "首选线索")} · 调整分 ${esc(bestPick.adjusted_score ?? "-")}</strong>
          <small>${esc(bestPick.risk_label || "风控清洁")} / ${esc(bestPick.trust_state || "clear")} / ${esc(bestPick.consensus_label || "暂无共识")} / ${esc(bestPick.outcome_label || "待验证")}</small>
          <em>${esc(bestPick.summary || "按价值、风险、共识和回测综合排序。")}</em>
          ${plan ? `
            <section class="best-pick-plan">
              <header>
                <span>${esc(plan.headline || "求证计划")}</span>
                <small>${esc(plan.summary || "")}</small>
              </header>
              <div>
                ${(plan.steps || []).slice(0, 3).map((step) => `
                  <i class="${esc(step.priority || "medium")}">${esc(step.label || "")}</i>
                `).join("")}
              </div>
            </section>
          ` : ""}
        </div>
      </button>
    ` : ""}
    <div class="opportunity-actions">
      ${actions.slice(0, 4).map((action) => `
        <button type="button" class="${action.key === opportunity.primary_action?.key ? "" : "ghost"} opportunity-action"
          data-key="${esc(action.key || "")}" data-view="${esc(action.view || "feed")}" data-query="${esc(action.query || "")}" data-tier="${esc(action.tier || "")}" data-invite="${esc(action.invite_code || "")}">
          ${esc(action.label || "查看")}
        </button>
      `).join("")}
    </div>
  `;
  $$("#opportunityDeck .opportunity-action").forEach((btn) => btn.addEventListener("click", () => runOpportunityAction(btn)));
}

function renderBountyBoard(board) {
  const root = $("#bountyBoard");
  if (!root) return;
  const summary = board.summary || {};
  const items = board.items || [];
  root.innerHTML = `
    <div class="bounty-board-head">
      <div>
        <span class="eyebrow">VERIFY BOUNTY</span>
        <strong>求证悬赏榜</strong>
      </div>
      <small>${esc(board.headline || "参与求证、存疑和补充讨论，积累信息源声誉。")}</small>
    </div>
    <div class="bounty-board-stats">
      <div><span>可领取</span><strong>${summary.active || 0}</strong></div>
      <div><span>待解锁</span><strong>${summary.locked || 0}</strong></div>
      <div><span>总XP</span><strong>${summary.total_reward_xp || 0}</strong></div>
    </div>
    <div class="bounty-board-list">
      ${items.slice(0, 4).map((item) => `
        <button type="button" class="bounty-board-item ${esc(item.state || "active")}" data-id="${item.rumor_id}">
          <span>${esc(item.tier)}${item.score}</span>
          <div>
            <strong>${esc(item.target)} · ${esc(item.task)}</strong>
            <small>${esc(item.detail || "")}</small>
          </div>
          <em>${item.reward_xp ? `+${Number(item.reward_xp)}XP` : esc(item.action || "解锁")}</em>
        </button>
      `).join("") || `<p class="empty">暂无求证悬赏，提交一条可验证线索可生成任务。</p>`}
    </div>
  `;
  $$("#bountyBoard .bounty-board-item").forEach((btn) => btn.addEventListener("click", () => openRumor(btn.dataset.id)));
}

async function runOpportunityAction(btn) {
  const key = btn.dataset.key;
  if (key === "register" || btn.dataset.view === "register") {
    setRegMode("reg");
    $("#authDialog").showModal();
    return;
  }
  if (key === "invite" && btn.dataset.invite) {
    const url = `${location.origin}/?invite=${encodeURIComponent(btn.dataset.invite)}`;
    try {
      await navigator.clipboard.writeText(url);
      btn.textContent = "已复制邀请链接";
      setTimeout(() => { btn.textContent = "邀请同行"; }, 1200);
    } catch {
      switchView(btn.dataset.view || "rank");
    }
    return;
  }
  const view = btn.dataset.view || "feed";
  switchView(view);
  if (view !== "feed") return;
  $("#searchInput").value = btn.dataset.query || "";
  $("#tierFilter").value = btn.dataset.tier || "";
  state.selectedTier = btn.dataset.tier || "";
  await loadRumors(true);
}

function renderActionQueue(items) {
  const root = $("#actionQueue");
  if (!root) return;
  root.innerHTML = `
    <div class="action-queue-head">
      <div>
        <span class="eyebrow">NEXT BEST ACTIONS</span>
        <strong>今日优先队列</strong>
      </div>
      <small>${items.length ? "按价值、风险、自选和成长收益排序" : "等待社区产生可行动线索"}</small>
    </div>
    <div class="action-queue-list">
      ${(items || []).map((item, idx) => `
        <button type="button" class="action-queue-item ${esc(item.state || "quiet")}"
          data-view="${esc(item.view || "feed")}" data-query="${esc(item.query || "")}" data-tier="${esc(item.tier || "")}" data-watch="${item.watch ? "1" : ""}">
          <span>${String(idx + 1).padStart(2, "0")}</span>
          <div>
            <strong>${esc(item.label || "下一步")}</strong>
            <b>${esc(item.target || "")}</b>
            <small>${esc(item.detail || "")}</small>
          </div>
          <em>${esc(item.action || "查看")}</em>
        </button>
      `).join("") || `<p class="empty">暂无优先事项。</p>`}
    </div>
  `;
  $$("#actionQueue .action-queue-item").forEach((btn) => btn.addEventListener("click", async () => {
    const view = btn.dataset.view || "feed";
    if (view === "register") {
      setRegMode("reg");
      $("#authDialog").showModal();
      return;
    }
    switchView(view);
    if (view !== "feed") return;
    $("#searchInput").value = btn.dataset.query || "";
    $("#tierFilter").value = btn.dataset.tier || "";
    state.selectedTier = btn.dataset.tier || "";
    state.watchOnly = btn.dataset.watch === "1";
    if (state.watchOnly) state.followedOnly = false;
    renderWatchPanel();
    renderProviderFollowPanel();
    await loadRumors(true);
  }));
}

function renderTopicRadar(radar) {
  const themes = radar.themes || [];
  const stocks = radar.stocks || [];
  const sources = radar.sources || [];
  $("#topicRadar").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">RADAR</span>
        <h3>热点主题雷达</h3>
      </div>
      <span>${themes.length + stocks.length} 个信号</span>
    </div>
    <div class="radar-grid">
      <div>
        <h4>催化主题</h4>
        <div class="radar-tags">
          ${themes.map((item) => `<button class="ghost radar-filter" data-query="${esc(item.name)}">${esc(item.name)}<strong>${item.count}</strong></button>`).join("") || `<p class="empty">暂无主题</p>`}
        </div>
      </div>
      <div>
        <h4>热议标的</h4>
        <div class="radar-list">
          ${stocks.map((item) => `<button class="ghost radar-filter" data-query="${esc(item.name || item.code)}"><span>${esc(item.name || item.code)}</span><strong>${item.count}条</strong></button>`).join("") || `<p class="empty">暂无标的</p>`}
        </div>
      </div>
      <div>
        <h4>活跃信息源</h4>
        <div class="radar-list">
          ${sources.map((item) => `<div><span>${esc(item.name)}</span><strong>${item.count}条 · ${Number(item.avg_score || 0).toFixed(1)}</strong></div>`).join("") || `<p class="empty">暂无信息源</p>`}
        </div>
      </div>
    </div>
  `;
  $$("#topicRadar .radar-filter").forEach((btn) => btn.addEventListener("click", () => {
    $("#searchInput").value = btn.dataset.query || "";
    state.selectedTier = "";
    $("#tierFilter").value = "";
    loadRumors(true);
  }));
}

function renderCommunityRooms() {
  const data = state.communityRooms || {};
  const rooms = data.rooms || [];
  const summary = data.summary || {};
  $("#communityRooms").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">ROOMS</span>
        <h3>社区情报房间</h3>
      </div>
      <span>${summary.total || 0} 房间 · 热度 ${summary.heat || 0}</span>
    </div>
    <div class="room-grid">
      ${rooms.slice(0, 6).map((room) => {
        const top = room.top_rumor || {};
        const verdict = top.value_verdict || {};
        return `
          <article class="room-card ${esc(room.kind)}">
            <header>
              <span>${room.kind === "stock" ? "标的" : "主题"}</span>
              <strong>${esc(room.name)}</strong>
            </header>
            <div class="room-stats">
              <div><span>线索</span><strong>${room.count || 0}</strong></div>
              <div><span>高分</span><strong>${room.top_score || 0}</strong></div>
              <div><span>信息源</span><strong>${room.provider_count || 0}</strong></div>
            </div>
            <p>${esc(room.summary || "")}</p>
            <div class="room-top">
              <span class="badge">${esc(top.ai_tier || "-")}${top.ai_score || ""}</span>
              <div>
                <strong>${esc(top.target || "等待线索")}</strong>
                <small>${esc(verdict.label || "可观察")} · ${Number(verdict.index || 0).toFixed(1)} 指数</small>
              </div>
            </div>
            <button class="ghost room-enter" data-query="${esc(room.query || room.name)}">进入房间</button>
          </article>
        `;
      }).join("") || `<p class="empty">暂无可进入房间，等待社区产生更多主题信号。</p>`}
    </div>
  `;
  $$("#communityRooms .room-enter").forEach((btn) => btn.addEventListener("click", () => {
    $("#searchInput").value = btn.dataset.query || "";
    $("#tierFilter").value = "";
    state.selectedTier = "";
    switchView("feed");
    loadRumors(true);
  }));
}

async function loadValueFramework() {
  const data = await api("/api/value-framework");
  state.framework = data;
  const verdict = data.value_verdict;
  const calibration = data.calibration || {};
  $("#valueFramework").innerHTML = [
    ...(data.score_dimensions || []).map((item) => `
    <div class="framework-row">
      <div>
        <strong>${esc(item.name)}</strong>
        <p>${esc(item.description)}</p>
      </div>
      <span>${item.weight}</span>
    </div>
  `),
    verdict ? `
    <div class="framework-row verdict-row">
      <div>
        <strong>${esc(verdict.name)}</strong>
        <p>${esc(verdict.description)}</p>
      </div>
      <span>指数</span>
    </div>
  ` : "",
    calibration.headline ? `
    <div class="framework-calibration">
      <div>
        <span class="eyebrow">CALIBRATION</span>
        <strong>${esc(calibration.headline)}</strong>
        <p>${esc(calibration.summary || "")}</p>
      </div>
      <div class="calibration-lanes">
        <section>
          <span>加权因素</span>
          ${(calibration.positive || []).slice(0, 3).map((item) => `
            <p><strong>${esc(item.label)}</strong><small>${esc(item.impact)} · ${esc(item.detail)}</small></p>
          `).join("")}
        </section>
        <section>
          <span>扣分/降权</span>
          ${(calibration.negative || []).slice(0, 3).map((item) => `
            <p><strong>${esc(item.label)}</strong><small>${esc(item.impact)} · ${esc(item.detail)}</small></p>
          `).join("")}
        </section>
      </div>
      <footer>${(calibration.principles || []).map(esc).join(" · ")}</footer>
    </div>
  ` : "",
  ].join("");
}

async function loadGrowthCenter() {
  const data = await api("/api/growth-center");
  state.growth = data;
  state.user = data.user || state.user;
  renderProfile();
  renderGrowthPanel();
  refreshAuthIncentiveIfOpen();
  await loadSourceUpgradeCenter();
}

async function loadSourceUpgradeCenter() {
  const data = await api("/api/source-upgrade-center");
  state.sourceUpgrade = data;
  state.user = data.user || state.user;
  renderProfile();
  renderSourceUpgradePanel();
}

async function loadWatchlist() {
  const data = await api("/api/watchlist");
  state.watchlist = data;
  renderWatchPanel();
}

async function loadProviderFollows() {
  const data = await api("/api/provider-follows");
  state.followedProviders = data;
  renderProviderFollowPanel();
}

function renderProviderFollowPanel() {
  const data = state.followedProviders || {};
  const items = data.items || [];
  const suggestions = data.suggestions || [];
  const board = data.source_board || {};
  const topSource = board.top_source || null;
  $("#providerFollowPanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">SOURCES</span>
        <h3>关注信息源</h3>
      </div>
      <strong>${items.length}</strong>
    </div>
    <div class="watch-actions">
      <button class="ghost ${state.followedOnly ? "active-filter" : ""}" id="followedOnlyBtn">${state.followedOnly ? "查看全部" : "只看关注源"}</button>
    </div>
    <div class="source-board">
      <div>
        <strong>${esc(board.headline || "关注可信信息源，建立个人来源流")}</strong>
        <p>${board.followed_count || 0} 已关注 · ${board.suggested_count || 0} 可推荐 · ${board.trusted_count || 0} 可信源</p>
      </div>
      ${topSource ? `
        <button class="source-board-top provider-open" data-id="${topSource.id}">
          <span>${esc(topSource.reason || "最高源分")}</span>
          <strong>${esc(topSource.display_name)} · ${Number(topSource.score || 0).toFixed(1)}</strong>
          <small>${esc(topSource.grade || "信息源")}</small>
        </button>
      ` : `<p class="empty">暂无可评估信息源，等待社区投稿沉淀。</p>`}
      <button class="ghost source-upgrade-jump">${esc(board.upgrade_action?.label || "提升我的源分")}</button>
    </div>
    <div class="watch-list">
      ${items.slice(0, 5).map((item) => `
        <div class="watch-row">
          <div><strong>${esc(item.display_name)}</strong><p>${Number(item.provider_grade?.score || 0).toFixed(1)}源分 · ${item.rumor_count || 0}条</p></div>
          <button class="ghost provider-open" data-id="${item.id}">档案</button>
        </div>
      `).join("") || `<p class="empty">先关注几个可信信息源，这里会变成你的个人来源动态。</p>`}
    </div>
    <div class="source-suggestions">
      <strong>推荐关注</strong>
      ${suggestions.slice(0, 3).map((item) => `
        <div class="source-suggestion">
          <div>
            <span>${esc(item.reason || "推荐信息源")}</span>
            <strong>${esc(item.display_name)}</strong>
            <p>${Number(item.provider_grade?.score || 0).toFixed(1)}源分 · ${item.rumor_count || 0}条 · 峰值${item.top_score || 0}</p>
          </div>
          <div>
            <button class="ghost provider-open" data-id="${item.id}">档案</button>
            <button class="ghost provider-follow-now" data-id="${item.id}">关注</button>
          </div>
        </div>
      `).join("") || `<p class="empty">暂无推荐来源，等待更多社区投稿。</p>`}
    </div>
  `;
  $("#followedOnlyBtn")?.addEventListener("click", () => {
    state.followedOnly = !state.followedOnly;
    renderProviderFollowPanel();
    loadRumors(true);
  });
  $("#providerFollowPanel .source-upgrade-jump")?.addEventListener("click", () => switchView("rank"));
  $$("#providerFollowPanel .provider-open").forEach((btn) => btn.addEventListener("click", () => openProviderProfile(btn.dataset.id)));
  $$("#providerFollowPanel .provider-follow-now").forEach((btn) => btn.addEventListener("click", () => setProviderFollow(btn.dataset.id, true)));
}

function renderWatchPanel() {
  const data = state.watchlist || {};
  const items = data.items || [];
  const suggestions = data.suggestions || [];
  const digest = data.digest || {};
  const privilege = data.view_privilege || {};
  const alertLabels = { hot: "热", active: "动", risk: "险", quiet: "静" };
  $("#watchPanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">WATCHLIST</span>
        <h3>自选情报</h3>
      </div>
      <strong>${items.length}</strong>
    </div>
    <div class="watch-actions">
      <button class="ghost ${state.watchOnly ? "active-filter" : ""}" id="watchOnlyBtn">${state.watchOnly ? "查看全部" : "只看自选"}</button>
    </div>
    <form class="watch-manual-add" id="watchManualAdd">
      <input name="code" placeholder="代码，如 600000.sh" />
      <input name="name" placeholder="名称，如 浦发银行" />
      <button type="submit">加入自选</button>
    </form>
    <div class="watch-privilege ${esc(privilege.state || "locked")}">
      <strong>${esc(privilege.label || "提升等级后开放自选全量浏览")}</strong>
      <span>${Number(privilege.limit || 0)} 条自选命中可浏览</span>
    </div>
    <div class="watch-digest ${esc(digest.state || "quiet")}">
      <strong>${esc(digest.headline || "建立自选后生成个人信号摘要")}</strong>
      <p>${digest.total_watches || 0} 个自选 · ${digest.rumor_hits || 0} 条命中 · 高热 ${digest.hot_count || 0} · 风险 ${digest.risk_count || 0}</p>
      ${digest.action ? `<button class="ghost watch-digest-action" data-key="${esc(digest.action.key || "")}" data-query="${esc(digest.action.query || "")}">${esc(digest.action.label || "查看")}</button>` : ""}
    </div>
    <div class="watch-list">
      ${items.slice(0, 5).map((item) => `
        <div class="watch-row ${esc(item.alert_level || "quiet")}">
          <div>
            <strong><span>${esc(item.name)}</span><em>${esc(alertLabels[item.alert_level] || "静")}</em></strong>
            <p>${esc(item.code)} · ${item.rumor_count || 0}条 · 最高 ${item.top_score || 0}</p>
            ${item.latest_signal ? `<small>${esc(item.latest_signal.date)} ${esc(item.latest_signal.tier)}${item.latest_signal.score} · ${esc(item.latest_signal.outcome?.label || "待验证")}</small>` : `<small>暂无社区线索</small>`}
          </div>
          <div class="watch-row-actions">
            <button class="ghost watch-focus" data-query="${esc(item.code || item.name)}">查看</button>
            <button class="ghost watch-remove" data-code="${esc(item.code)}">移除</button>
          </div>
        </div>
      `).join("") || `<p class="empty">在详情页关注股票后，这里会变成你的个人信号流。</p>`}
    </div>
    <div class="watch-suggestions">
      <strong>推荐自选</strong>
      ${suggestions.slice(0, 3).map((item) => `
        <div class="watch-suggestion">
          <div>
            <span>${esc(item.reason || "推荐标的")}</span>
            <strong>${esc(item.name || item.code)}</strong>
            <p>${esc(item.code)} · ${item.rumor_count || 0}条 · 峰值${item.top_score || 0}</p>
          </div>
          <button class="ghost watch-add-suggested" data-code="${esc(item.code)}" data-name="${esc(item.name || item.code)}">加入</button>
        </div>
      `).join("") || `<p class="empty">暂无推荐自选，等待社区产生更多高分标的。</p>`}
    </div>
  `;
  $("#watchOnlyBtn")?.addEventListener("click", () => {
    state.watchOnly = !state.watchOnly;
    renderWatchPanel();
    loadRumors(true);
  });
  $("#watchManualAdd")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const code = form.elements.code.value.trim();
    const name = form.elements.name.value.trim();
    if (!code && !name) return;
    addWatch({ code, name });
    form.reset();
  });
  $$("#watchPanel .watch-focus").forEach((btn) => btn.addEventListener("click", () => {
    state.watchOnly = false;
    $("#searchInput").value = btn.dataset.query || "";
    switchView("feed");
    renderWatchPanel();
    loadRumors(true);
  }));
  $$("#watchPanel .watch-remove").forEach((btn) => btn.addEventListener("click", () => removeWatch(btn.dataset.code)));
  $$("#watchPanel .watch-add-suggested").forEach((btn) => btn.addEventListener("click", () => addWatch({ code: btn.dataset.code, name: btn.dataset.name })));
  $("#watchPanel .watch-digest-action")?.addEventListener("click", () => runWatchDigestAction(digest.action || {}, suggestions));
}

function runWatchDigestAction(action, suggestions = []) {
  if (action.key === "add_suggested") {
    const first = suggestions[0];
    if (first) addWatch({ code: first.code, name: first.name || first.code });
    return;
  }
  state.watchOnly = false;
  $("#searchInput").value = action.query || "";
  switchView("feed");
  renderWatchPanel();
  loadRumors(true);
}

async function addWatch(item) {
  if (state.user?.is_guest) {
    $("#regInvite").value = state.inviteCodeFromUrl || $("#regInvite").value || "";
    setRegMode("reg");
    $("#authDialog").showModal();
    return;
  }
  try {
    await api("/api/watchlist", { method: "POST", body: JSON.stringify(item) });
    await loadWatchlist();
    await loadGrowthCenter();
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

async function removeWatch(code) {
  try {
    await api(`/api/watchlist/${encodeURIComponent(code)}`, { method: "DELETE" });
    await loadWatchlist();
    await loadGrowthCenter();
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

async function setProviderFollow(providerId, follow) {
  try {
    const data = await api(`/api/providers/${providerId}/follow`, { method: follow ? "POST" : "DELETE" });
    state.followedProviders = data.following;
    renderProviderFollowPanel();
    renderProviderProfile(data.provider);
    await loadGrowthCenter();
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

function renderGrowthPanel() {
  const data = state.growth || {};
  const summary = data.summary || {};
  const ledger = data.ledger || {};
  const totals = ledger.totals || {};
  const missions = (data.missions || []).slice(0, 4);
  const next = summary.next_action;
  $("#growthPanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">NEXT STEP</span>
        <h3>成长任务</h3>
      </div>
      <strong>${summary.completed || 0}/${summary.total || 0}</strong>
    </div>
    <div class="mission-progress"><b style="width:${Number(summary.completion_rate || 0)}%"></b></div>
    <div class="growth-ledger-mini">
      <div><span>总XP</span><strong>${totals.total_xp || 0}</strong></div>
      <div><span>求证</span><strong>${totals.participation_xp || 0}</strong></div>
      <div><span>邀请</span><strong>${totals.referral_xp || 0}</strong></div>
    </div>
    <div class="growth-ledger-sources">
      ${(ledger.sources || []).map((item) => `<span>${esc(item.label)} ${item.value || 0}</span>`).join("")}
    </div>
    <div class="growth-ledger-recent">
      ${(ledger.entries || []).slice(0, 3).map((item) => `
        <div class="${esc(item.kind || "")}">
          <span>${esc(item.label || "")}</span>
          <strong>${esc(item.title || "")}</strong>
          <em>+${Number(item.value || 0)} ${esc(item.unit || "XP")}</em>
        </div>
      `).join("") || `<p class="empty">完成投稿、求证或邀请后，这里会沉淀成长账本。</p>`}
    </div>
    <div class="mission-list">
      ${missions.map(renderMission).join("")}
    </div>
    ${next ? `<button class="ghost mission-jump" data-view-jump="${esc(next.cta_view || "feed")}">${esc(next.title)}</button>` : `<button class="ghost mission-jump" data-view-jump="rank">查看信息源榜</button>`}
  `;
  $$("#growthPanel [data-view-jump]").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.viewJump)));
}

function renderSourceUpgradePanel() {
  const root = $("#sourceUpgradePanel");
  if (!root) return;
  const data = state.sourceUpgrade || {};
  const grade = data.grade || data.user?.provider_grade || {};
  const upgrade = data.upgrade || {};
  const rank = data.rank_context || {};
  const perf = data.recent_performance || {};
  const next = upgrade.next;
  const components = upgrade.components || [];
  const roadmap = upgrade.roadmap || [];
  const priorityActions = upgrade.priority_actions || [];
  const privileges = data.privileges || [];
  const missions = (data.missions || []).slice(0, 5);
  const progress = upgrade.progress == null ? 100 : Number(upgrade.progress || 0);
  root.innerHTML = `
    <div class="source-upgrade-head">
      <div>
        <span class="eyebrow">SOURCE OPS</span>
        <h3>信息源升级中心</h3>
        <p>源分由 XP、贡献度、信誉、邀请和社区验证共同构成。</p>
      </div>
      <div class="source-grade-card">
        <span>${esc(grade.name || "新晋观察员")}</span>
        <strong>${Number(grade.score || 0).toFixed(1)}</strong>
        <small>${rank.rank ? `第 ${rank.rank}/${rank.total} 名` : "暂未入榜"}</small>
      </div>
    </div>
    <div class="source-upgrade-grid">
      <section>
        <div class="upgrade-target">
          <div>
            <span>下一等级</span>
            <strong>${next ? esc(next.name) : "已达最高等级"}</strong>
          </div>
          <span>${next ? `还差 ${Number(upgrade.needed || 0).toFixed(1)}` : "100%"}</span>
        </div>
        <div class="mission-progress wide"><b style="width:${progress}%"></b></div>
        <div class="source-roadmap">
          <div class="source-roadmap-head">
            <span class="eyebrow">UPGRADE ROADMAP</span>
            <strong>升级差距路线图</strong>
          </div>
          ${roadmap.slice(0, 5).map((item) => `
            <button class="source-roadmap-item ${esc(item.state || "focus")}" data-view-jump="${esc(item.view || "feed")}">
              <div>
                <strong>${esc(item.label)}</strong>
                <span>${Number(item.value || 0).toFixed(1)} / ${Number(item.target || 0).toFixed(1)}</span>
              </div>
              <i><b style="width:${Number(item.progress || 0)}%"></b></i>
              <p>${item.gap > 0 ? `还差 ${Number(item.gap || 0).toFixed(1)} · ` : "已达标 · "}${esc(item.detail || "")}</p>
            </button>
          `).join("")}
        </div>
        ${renderCredibilityPassport(data.credibility_passport || {})}
        <div class="source-components">
          ${components.map((item) => `
            <div class="source-component">
              <div><strong>${esc(item.name)}</strong><span>${Number(item.value || 0).toFixed(1)}</span></div>
              <i><b style="width:${Number(item.progress || 0)}%"></b></i>
              <p>${esc(item.hint)}</p>
            </div>
          `).join("")}
        </div>
        <div class="source-priority-actions">
          ${priorityActions.map((item) => `
            <button class="source-priority-action" data-view-jump="${esc(item.view || "feed")}">
              <span>${item.rank || ""}</span>
              <div>
                <strong>${esc(item.title)}</strong>
                <p>${esc(item.impact || "")} · ${esc(item.detail || "")}</p>
              </div>
            </button>
          `).join("")}
        </div>
      </section>
      <section>
        <div class="source-stats">
          <div><span>投稿</span><strong>${perf.rumor_count || 0}</strong></div>
          <div><span>均分</span><strong>${perf.avg_score == null ? "-" : Number(perf.avg_score).toFixed(1)}</strong></div>
          <div><span>最佳</span><strong>${perf.best_score == null ? "-" : perf.best_score}</strong></div>
          <div><span>命中率</span><strong>${perf.hit_rate == null ? "-" : `${Number(perf.hit_rate).toFixed(1)}%`}</strong></div>
        </div>
        <div class="privilege-list">
          ${privileges.map((item) => `
            <article class="${item.unlocked ? "unlocked" : ""}">
              <div><strong>${esc(item.name)}</strong><span>${item.unlocked ? "已解锁" : `差 ${Number(item.needed || 0).toFixed(1)}`}</span></div>
              <p>${(item.rights || []).map(esc).join(" · ")}</p>
            </article>
          `).join("")}
        </div>
      </section>
    </div>
    <div class="source-missions">
      ${missions.map((item) => `
        <button class="source-mission ${item.completed ? "done" : ""}" data-view-jump="${esc(item.cta_view || "feed")}">
          <span>${item.completed ? "✓" : ""}</span>
          <div><strong>${esc(item.title)}</strong><p>${esc(item.upgrade_hint || item.reward)}</p></div>
        </button>
      `).join("")}
    </div>
  `;
  $$("#sourceUpgradePanel [data-view-jump]").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.viewJump)));
}

function renderCredibilityPassport(passport) {
  const metrics = passport.metrics || [];
  if (!metrics.length) return "";
  return `
    <div class="cred-passport ${esc(passport.state || "new")}">
      <div class="cred-passport-head">
        <div>
          <span class="eyebrow">SOURCE PASSPORT</span>
          <strong>${esc(passport.label || "信息源信用护照")}</strong>
          <p>${esc(passport.summary || "")}</p>
        </div>
        <em>${esc(passport.state || "new")}</em>
      </div>
      <div class="cred-metrics">
        ${metrics.map((item) => `
          <div class="${esc(item.state || "watch")}">
            <span>${esc(item.label)}</span>
            <strong>${item.value == null ? "-" : esc(item.value)}</strong>
          </div>
        `).join("")}
      </div>
      <footer>
        <span>${esc(passport.follow_hint || "")}</span>
        ${passport.next_action ? `<button type="button" class="ghost" data-view-jump="${esc(passport.next_action.view || "feed")}">${esc(passport.next_action.label || "下一步")}</button>` : ""}
      </footer>
    </div>
  `;
}

function renderMission(item) {
  return `
    <div class="mission ${item.completed ? "done" : ""}">
      <span>${item.completed ? "✓" : ""}</span>
      <div>
        <strong>${esc(item.title)}</strong>
        <p>${esc(item.reward)}</p>
      </div>
    </div>
  `;
}

async function loadInvitePreviewFromUrl() {
  const code = state.inviteCodeFromUrl;
  if (!code) {
    renderInviteLanding();
    return;
  }
  try {
    const data = await api(`/api/invite-preview/${encodeURIComponent(code)}`);
    state.invitePreview = data;
    renderInviteLanding();
  } catch {
    state.invitePreview = { valid: false, invite_code: code, message: "邀请码预览加载失败" };
    renderInviteLanding();
  }
}

function renderInviteLanding() {
  const root = $("#inviteLanding");
  if (!root) return;
  const data = state.invitePreview;
  if (!state.inviteCodeFromUrl || !data) {
    root.innerHTML = "";
    root.classList.remove("show");
    return;
  }
  root.classList.add("show");
  const inviter = data.inviter || {};
  const grade = inviter.grade || {};
  const landing = data.landing_value || {};
  const proofPoints = landing.proof_points || [];
  const steps = landing.activation_steps || data.activation_steps || [];
  root.innerHTML = `
    <div>
      <span class="eyebrow">INVITE ACCESS</span>
      <h3>${esc(data.valid ? data.message : "邀请码待确认")}</h3>
      <p>${esc(landing.headline || (data.valid ? data.invitee_reward : data.message || "使用有效邀请码注册可获得额外启动权益。"))}</p>
    </div>
    <div class="invite-landing-proof">
      ${(proofPoints.length ? proofPoints : [
        { label: "邀请人", value: inviter.display_name || "待确认" },
        { label: "源等级", value: grade.name || "未验证" },
        { label: "邀请码", value: data.invite_code || state.inviteCodeFromUrl },
      ]).slice(0, 3).map((item) => `
        <div><span>${esc(item.label || "")}</span><strong>${esc(item.value || "")}</strong></div>
      `).join("")}
    </div>
    <div class="invite-landing-steps">
      ${steps.slice(0, 3).map((step, idx) => `<span><b>${idx + 1}</b>${esc(step)}</span>`).join("")}
    </div>
    <div class="invite-landing-actions">
      <button id="acceptInviteBtn" ${data.valid ? "" : "disabled"}>接受邀请注册</button>
      <button id="inviteDismissBtn" class="ghost">继续浏览</button>
    </div>
  `;
  $("#acceptInviteBtn")?.addEventListener("click", () => {
    setRegMode("reg");
    if ($("#regInvite")) $("#regInvite").value = data.invite_code || state.inviteCodeFromUrl;
    $("#authDialog").showModal();
  });
  $("#inviteDismissBtn")?.addEventListener("click", () => {
    root.classList.remove("show");
    root.innerHTML = "";
  });
}

function renderActivationPanel() {
  const data = state.activation || {};
  const summary = data.summary || {};
  const rewards = data.starter_rewards || [];
  const steps = data.next_steps || [];
  const playbook = data.activation_playbook || {};
  const primary = playbook.primary_action || {};
  const starterWatch = data.starter_watchlist || {};
  const starterItems = starterWatch.items || [];
  $("#activationPanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">START HERE</span>
        <h3>${data.registered ? "情报账户" : "新用户激活"}</h3>
      </div>
      <strong>${summary.completed_steps || 0}/${summary.total_steps || 0}</strong>
    </div>
    <p>${esc(data.headline || "注册后保留成长、关注和解锁记录。")}</p>
    <div class="activation-metrics">
      <div><span>S/A线索</span><strong>${summary.high_value_rumors || 0}</strong></div>
      <div><span>高价值待解</span><strong>${summary.locked_high_value || 0}</strong></div>
      <div><span>直看额度</span><strong>${summary.direct_quota || 0}</strong></div>
    </div>
    <div class="activation-playbook ${esc(playbook.stage || "visitor")}">
      <div>
        <span>当前阶段</span>
        <strong>${esc(playbook.label || "建立使用闭环")}</strong>
        <p>${(playbook.value || []).slice(0, 2).map(esc).join(" · ")}</p>
      </div>
      <i><b style="width:${Number(playbook.progress || 0)}%"></b></i>
      <button type="button" class="ghost activation-primary" data-key="${esc(primary.key || "feed")}" data-view="${esc(primary.view || "feed")}">${esc(primary.label || "继续使用")}</button>
    </div>
    <div class="activation-rewards">
      ${rewards.slice(0, 3).map((item) => `
        <div><span>${esc(item.label)}</span><strong>${esc(item.value)}</strong></div>
      `).join("")}
    </div>
    <div class="activation-watch">
      <div>
        <strong>${esc(starterWatch.headline || "建立个人信号流")}</strong>
        <p>${esc(starterWatch.summary || "关注标的后，首页会聚合你的自选线索。")}</p>
      </div>
      <div class="activation-watch-list">
        ${starterItems.slice(0, 3).map((item) => `
          <button type="button" class="activation-watch-item" data-code="${esc(item.code)}" data-name="${esc(item.name || item.code)}">
            <span>${esc(item.reason || "推荐")}</span>
            <strong>${esc(item.name || item.code)}</strong>
            <small>${esc(item.code)} · ${item.rumor_count || 0}条 · 峰值${item.top_score || 0}</small>
          </button>
        `).join("") || `<p class="empty">暂无可推荐标的，先浏览高分线索。</p>`}
      </div>
    </div>
    <div class="activation-steps">
      ${steps.slice(0, 4).map((item) => `
        <button class="activation-step ${item.completed ? "done" : ""}" data-key="${esc(item.key)}" data-view="${esc(item.view || "feed")}">
          <span>${item.completed ? "✓" : ""}</span>
          <div><strong>${esc(item.title)}</strong><p>${esc(item.description)}</p></div>
        </button>
      `).join("")}
    </div>
  `;
  $$("#activationPanel .activation-step, #activationPanel .activation-primary").forEach((btn) => btn.addEventListener("click", () => runActivationAction(btn)));
  $$("#activationPanel .activation-watch-item").forEach((btn) => btn.addEventListener("click", () => addWatch({ code: btn.dataset.code, name: btn.dataset.name })));
}

function runActivationAction(btn) {
  const key = btn.dataset.key;
  if (key === "register") {
    setRegMode("reg");
    $("#authDialog").showModal();
    return;
  }
  switchView(btn.dataset.view || "feed");
}

function renderInvitePanel() {
  const u = state.user;
  const referral = state.referral || {};
  const rewards = referral.rewards || state.framework?.invite_rewards || {};
  const stats = referral.stats || {};
  const milestones = referral.milestones || [];
  const recent = referral.recent || [];
  const leaders = referral.leaderboard || [];
  const plan = referral.invite_plan || {};
  const momentum = referral.momentum || {};
  const code = referral.invite_code || (u && !u.is_guest ? u.invite_code || "" : "");
  $("#invitePanel").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">INVITE LOOP</span>
        <h3>邀请中心</h3>
      </div>
      <strong>${stats.invite_count || 0}</strong>
    </div>
    <p>${esc(rewards.inviter || "成功邀请可获得 XP 和直看额度。")}</p>
    <div class="invite-plan ${esc(plan.state || "guest")}">
      <div>
        <span>当前目标</span>
        <strong>${esc(plan.headline || "建立你的邀请增长回路")}</strong>
        <p>${esc(plan.reward || "邀请新用户可获得 XP、直看额度和源分加成。")}</p>
      </div>
      <i><b style="width:${Math.min(100, Number(plan.progress || 0))}%"></b></i>
      <small>${plan.next_needed == null ? "" : `还差 ${plan.next_needed} 人 · ${esc(plan.target || "")}`}</small>
    </div>
    <div class="invite-code">${code ? esc(code) : "登录后生成"}</div>
    <button class="copy-invite" data-invite="${esc(`${location.origin}/?invite=${code}`)}" ${code ? "" : "disabled"}>复制邀请链接</button>
    <button class="ghost copy-invite-copy" data-copy="${esc(`${plan.share_copy || ""}${code ? ` ${location.origin}/?invite=${code}` : ""}`)}" ${plan.share_copy ? "" : "disabled"}>复制邀请话术</button>
    <div class="invite-momentum">
      <div>
        <span>已获得</span>
        <strong>${esc(momentum.earned_value || `${stats.reward_xp || 0} XP + ${stats.reward_quota || 0} 次直看`)}</strong>
      </div>
      <div>
        <span>下一档奖励</span>
        <strong>${esc(momentum.next_reward || plan.reward || "邀请越多，源分越高")}</strong>
      </div>
      <div>
        <span>下一步</span>
        <strong>${momentum.next_needed == null ? "继续邀请" : `还差 ${momentum.next_needed} 人`}</strong>
      </div>
    </div>
    <div class="invite-action-strip">
      ${(momentum.actions || []).slice(0, 3).map((item) => `
        <button type="button" class="ghost invite-momentum-action" data-key="${esc(item.key || "")}" data-copy="${esc(item.key === "copy_pitch" ? `${plan.share_copy || ""}${code ? ` ${location.origin}/?invite=${code}` : ""}` : `${location.origin}/?invite=${code}`)}">
          <strong>${esc(item.label || "邀请")}</strong>
          <span>${esc(item.detail || "")}</span>
        </button>
      `).join("")}
    </div>
    <div class="invite-stats">
      <div><span>奖励XP</span><strong>${stats.reward_xp || 0}</strong></div>
      <div><span>直看额度</span><strong>${stats.reward_quota || 0}</strong></div>
      <div><span>下一档</span><strong>${stats.next_needed == null ? "满级" : `还差${stats.next_needed}`}</strong></div>
    </div>
    <div class="invite-milestones">
      ${milestones.slice(0, 4).map((item) => `
        <div class="${item.completed ? "done" : ""}">
          <span>${item.target}</span>
          <p>${esc(item.title)}</p>
        </div>
      `).join("")}
    </div>
    <div class="invite-mini-list">
      ${(recent.length ? recent : leaders).slice(0, 3).map((item) => `
        <div>
          <span>${esc(item.invitee_name || item.display_name || "社区成员")}</span>
          <strong>${item.reward_xp ? `+${item.reward_xp}XP` : `${item.invite_count || 0}邀`}</strong>
        </div>
      `).join("") || `<p class="empty">邀请记录会在这里沉淀。</p>`}
    </div>
  `;
}

function renderDimensionScores(scores) {
  const items = scores?.items || [];
  if (!items.length) return "";
  return `
    <div class="dimension-score-strip">
      ${items.slice(0, 4).map((item) => `
        <span title="${esc(item.detail || "")}">
          <b>${esc(item.label || "")}</b>
          <strong>${item.score == null ? "-" : esc(item.score)}</strong>
        </span>
      `).join("")}
    </div>
  `;
}

function renderRecentBacktestShowcase(showcase) {
  const root = $("#recentBacktestShowcase");
  if (!root) return;
  const items = showcase.items || [];
  root.innerHTML = `
    <div class="showcase-head">
      <div>
        <strong>${esc(showcase.headline || "近3个交易日高回测信号")}</strong>
        <p>${esc(showcase.summary || "历史表现用于展示社区筛选能力，实时仍需看当日高价值信号。")}</p>
      </div>
      <small>${(showcase.dates || []).map(esc).join(" / ")}</small>
    </div>
    <div class="showcase-list">
      ${items.slice(0, 4).map((entry) => {
        const item = entry.rumor || {};
        return `
          <button type="button" class="showcase-item" data-id="${item.id}">
            <span class="badge">${esc(item.ai_tier || "")}${item.ai_score || ""}</span>
            <div>
              <strong>${esc(item.target || "历史信号")}</strong>
              <small>综合 ${entry.dimension_composite ?? "-"} · 回测分位 ${entry.signal_value ?? "-"} · T+1 ${pct(entry.ret_t1_1)} · T+5 ${pct(entry.ret_t1_5)} · T+20 ${pct(entry.ret_t1_20)}</small>
            </div>
          </button>
        `;
      }).join("") || `<p class="empty">暂无近3个交易日回测样本。</p>`}
    </div>
  `;
  $$("#recentBacktestShowcase .showcase-item").forEach((btn) => btn.addEventListener("click", () => openRumor(btn.dataset.id)));
}

function renderTodaySignalBoard(board) {
  const root = $("#todaySignalBoard");
  if (!root) return;
  const ordinary = board.ordinary || [];
  const high = board.high_value || [];
  const prompt = board.unlock_prompt || {};
  root.innerHTML = `
    <div class="today-signal-head">
      <div>
        <strong>${esc(board.headline || "当日普通信号与高价值解锁")}</strong>
        <p>${esc(board.summary || "普通信号用于看方向，高价值线索用于重点求证。")}</p>
      </div>
      <button type="button" class="ghost today-unlock-jump" data-tier="${esc(prompt.action?.tier || "S")}">${esc(prompt.action?.label || "解锁高价值")}</button>
    </div>
    <div class="today-signal-grid">
      <section>
        <span>当日普通信号</span>
        ${ordinary.slice(0, 3).map((item) => `
          <button type="button" class="today-signal-item" data-id="${item.id}">
            <strong>${esc(item.target)}</strong>
            <small>${esc(item.ai_tier)}${item.ai_score} · 综合 ${esc(item.dimension_scores?.composite ?? "-")}</small>
          </button>
        `).join("") || `<p class="empty">暂无当日普通信号。</p>`}
      </section>
      <section>
        <span>高价值待解锁</span>
        ${high.slice(0, 3).map((item) => `
          <button type="button" class="today-signal-item ${item.hidden ? "locked" : "open"}" data-id="${item.id}">
            <strong>${esc(item.target)}</strong>
            <small>${esc(item.ai_tier)}${item.ai_score} · ${item.hidden ? "待解锁" : "已开放"} · 综合 ${esc(item.dimension_scores?.composite ?? "-")}</small>
          </button>
        `).join("") || `<p class="empty">暂无当日 S/A 高价值信号。</p>`}
      </section>
    </div>
  `;
  $$("#todaySignalBoard .today-signal-item").forEach((btn) => btn.addEventListener("click", () => openRumor(btn.dataset.id).catch(() => {
    $("#tierFilter").value = "S";
    state.selectedTier = "S";
    loadRumors(true);
  })));
  $("#todaySignalBoard .today-unlock-jump")?.addEventListener("click", () => {
    $("#tierFilter").value = "S";
    state.selectedTier = "S";
    loadRumors(true);
  });
}

function renderHighlight(item) {
  const reasons = (item.ai_reasons || []).slice(0, 2).map((r) => `<span class="chip">${esc(r)}</span>`).join("");
  const discussion = item.discussion || {};
  return `
    <article class="highlight-card ${item.hidden ? "locked" : ""}">
      <div>
        <strong>${esc(item.target)}</strong>
        <span class="badge">${esc(item.ai_tier)}${item.ai_score}</span>
      </div>
      <p>${esc(item.logic)}</p>
      ${renderDimensionScores(item.dimension_scores)}
      ${renderValueVerdict(item.value_verdict, "compact")}
      ${renderProviderSnapshot(item.provider, "compact")}
      <footer>
        <span>${esc(item.submitter_name || "社区")}</span>
        <span>热度 ${discussion.heat || 0}</span>
        ${reasons}
      </footer>
    </article>
  `;
}

function renderValueVerdict(verdict, mode = "card") {
  if (!verdict) return "";
  return `
    <div class="value-verdict ${esc(verdict.state || "watch")} ${esc(mode)}">
      <div>
        <span>价值指数</span>
        <strong>${Number(verdict.index || 0).toFixed(1)}</strong>
      </div>
      <div>
        <b>${esc(verdict.label || "可观察")}</b>
        <p>${(verdict.drivers || []).slice(0, mode === "detail" ? 4 : 2).map(esc).join(" · ") || `证据完整度 ${Number(verdict.confidence || 0).toFixed(0)}%`}</p>
      </div>
    </div>
  `;
}

function renderProviderSnapshot(provider, mode = "card") {
  if (!provider) return "";
  const grade = provider.grade || {};
  const avg = provider.avg_score == null ? "-" : Number(provider.avg_score).toFixed(1);
  const feedback = Number(provider.feedback_score || 0).toFixed(1);
  const followers = provider.follower_count || 0;
  return `
    <button type="button" class="provider-snapshot ${esc(mode)} provider-open" data-id="${provider.id}">
      <span>
        <strong>${esc(provider.display_name)}</strong>
        <small>${esc(grade.name || "新晋观察员")} · ${Number(grade.score || 0).toFixed(1)}源分</small>
      </span>
      <em>${provider.rumor_count || 0}条 · ${provider.top_score == null ? "-" : provider.top_score}峰值</em>
      <div class="provider-proof">
        <small>均分 ${avg}</small>
        <small>反馈 ${feedback}</small>
        <small>关注 ${followers}</small>
      </div>
    </button>
  `;
}

function renderTierStat(item) {
  const status = item.allowed ? "可查看" : `需 ${item.requirement}`;
  return `
    <button class="tier-card ${item.allowed ? "" : "locked-tier"}" data-tier="${item.tier}" data-allowed="${item.allowed}">
      <span class="tier-name">${item.label}</span>
      <strong>${item.count}</strong>
      <span>${status}</span>
    </button>
  `;
}

function renderCardIntelStrip(item) {
  const consensus = item.consensus_snapshot || {};
  const metrics = consensus.metrics || {};
  const bounties = item.verification_bounties || [];
  const topBounty = bounties.find((bounty) => bounty.state !== "locked") || bounties[0] || null;
  const state = consensus.state || "isolated";
  const bountyState = topBounty?.state || "none";
  const bountyLabel = topBounty
    ? (bountyState === "locked" ? "解锁后求证" : topBounty.action || topBounty.label || "参与求证")
    : "等待悬赏";
  const bountyReward = topBounty?.reward_xp ? `+${Number(topBounty.reward_xp)}XP` : "声誉";
  return `
    <div class="card-intel-strip">
      <div class="consensus-mini ${esc(state)}">
        <span>社区共识</span>
        <strong>${esc(consensus.label || "等待交叉验证")}</strong>
        <small>${metrics.related || 0}相关 · ${metrics.sources || 0}源 · ${metrics.high_value || 0}高值</small>
      </div>
      <div class="bounty-mini ${esc(bountyState)}">
        <span>验证悬赏</span>
        <strong>${esc(bountyLabel)}</strong>
        <small>${esc(bountyReward)} · ${bounties.length || 0}项任务</small>
      </div>
    </div>
  `;
}

function renderScoreExplain(explain, mode = "card") {
  if (!explain) return "";
  const strengths = explain.strengths || [];
  const gaps = explain.gaps || [];
  const factors = explain.factors || [];
  const steps = explain.next_steps || [];
  if (mode === "detail") {
    return `
      <section class="score-explain detail">
        <div class="score-explain-head">
          <div>
            <span class="eyebrow">SCORE TRANSPARENCY</span>
            <strong>${esc(explain.headline || "评分解释")}</strong>
          </div>
          <small>${esc(explain.summary || "")}</small>
        </div>
        <div class="score-factor-grid">
          ${factors.map((item) => `
            <div class="${esc(item.state || "watch")}">
              <span>${esc(item.label)}</span>
              <strong>${item.value == null ? "-" : esc(item.value)}</strong>
            </div>
          `).join("")}
        </div>
        <div class="score-explain-lists">
          <div><span>强项</span>${strengths.map((item) => `<b>${esc(item.label)} ${esc(item.detail || "")}</b>`).join("") || `<b>等待更多强证据</b>`}</div>
          <div><span>短板</span>${gaps.map((item) => `<b>${esc(item.label)} ${esc(item.detail || "")}</b>`).join("") || `<b>暂无明显短板</b>`}</div>
          <div><span>下一步</span>${steps.map((item) => `<b>${esc(item)}</b>`).join("") || `<b>继续跟踪验证</b>`}</div>
        </div>
      </section>
    `;
  }
  const topStrength = strengths[0]?.label || "等待强证据";
  const topGap = gaps[0]?.label || "短板较少";
  const next = steps[0] || "进入详情求证";
  return `
    <div class="score-explain compact">
      <div><span>强项</span><strong>${esc(topStrength)}</strong></div>
      <div><span>短板</span><strong>${esc(topGap)}</strong></div>
      <div><span>下一步</span><strong>${esc(next)}</strong></div>
    </div>
  `;
}

async function selectTier(tier, allowed) {
  state.selectedTier = tier;
  $("#tierFilter").value = tier;
  $("#tierPanel").innerHTML = `<strong>${tier}级情报</strong><span>${allowed ? "已开放，下方展示该等级明细。" : "未开放，仅展示脱敏信息，可通过提升等级或贡献度解锁。"}</span>`;
  await loadRumors();
}

function renderCard(item) {
  const locked = item.hidden ? "locked" : "";
  const reasons = item.ai_reasons.map((r) => `<span class="chip">${esc(r)}</span>`).join("");
  const dims = item.score_dimensions || {};
  const discussion = item.discussion || {};
  const moderation = item.moderation || discussion.moderation || {};
  const outcome = item.outcome || {};
  const rights = item.rights || {};
  const risk = item.risk_analysis || {};
  const tasks = item.verification_tasks || [];
  const unlockPath = item.unlock_path || {};
  const dimBars = Object.entries({
    specificity: "明确",
    evidence: "密度",
    freshness: "时效",
    verifiability: "验证",
  }).map(([key, label]) => {
    const value = Math.max(0, Math.min(30, Number(dims[key] || 0)));
    return `<span title="${label} ${value}"><i style="width:${Math.min(100, value / 30 * 100)}%"></i></span>`;
  }).join("");
  return `
    <article class="card ${locked}" data-rights-fp="${esc(rights.fingerprint || "")}" data-rights-scope="${esc(rights.scope || "rumor-content")}">
      <span class="rights-mark" aria-hidden="true">${esc(rights.mark || "")}:${esc(rights.fingerprint || "")}</span>
      <div class="card-head">
        <div>
          <div class="target">${esc(item.target)}</div>
          <small>${esc(item.submitter_name || "社区信息源")}</small>
        </div>
        <div class="badge">${esc(item.ai_tier)}${item.ai_score}</div>
      </div>
      <p class="logic">${esc(item.logic)}</p>
      ${renderDimensionScores(item.dimension_scores)}
      ${renderValueVerdict(item.value_verdict)}
      ${renderScoreExplain(item.score_explanation)}
      ${renderProviderSnapshot(item.provider)}
      ${renderCardIntelStrip(item)}
      <div class="score-bars">${dimBars}</div>
      <div class="meta">
        <span class="chip">${esc(item.recommendation_date)}</span>
        <span class="chip">${item.source === "reference" ? "历史样本" : "社区分享"}</span>
        ${item.watched ? `<span class="chip watch-chip">自选命中</span>` : ""}
        ${outcome.label ? `<span class="chip outcome-chip ${esc(outcome.state || "pending")}">${esc(outcome.label)}</span>` : ""}
        ${tasks.length ? `<span class="chip verify-chip">求证 ${tasks.length}</span>` : ""}
        ${risk.level && risk.level !== "clear" ? `<span class="chip risk-chip">${esc(risk.label || "风控提示")}</span>` : ""}
        ${moderation.reports ? `<span class="chip risk-chip">${trustLabel(moderation.trust_state)} ${moderation.reports}</span>` : ""}
        ${item.effective_score < item.ai_score ? `<span class="chip risk-chip">有效分 ${item.effective_score}</span>` : ""}
        ${reasons}
      </div>
      <div class="discussion-strip">
        <span>有用 ${discussion.useful || 0}</span>
        <span>求证 ${discussion.verify || 0}</span>
        <span>存疑 ${discussion.doubt || 0}</span>
        <span>讨论 ${discussion.comments || 0}</span>
      </div>
      ${item.unlocked ? "" : renderUnlockPath(unlockPath, item.id)}
      <div class="card-actions">
        ${item.unlocked ? `<button class="open-btn" data-id="${item.id}">查看详情</button>` : `<button class="unlock-btn" data-id="${item.id}">使用额度直看</button>`}
      </div>
    </article>
  `;
}

function renderUnlockPath(path, rumorId) {
  if (!path || !path.state) return "";
  return `
    <div class="unlock-path ${esc(path.state)}">
      <strong>${esc(path.label || "解锁路径")}</strong>
      <p>${esc(path.summary || "")}</p>
      <div>
        ${(path.actions || []).slice(0, 2).map((action) => `
          <button type="button" class="ghost unlock-path-action" data-id="${rumorId}" data-key="${esc(action.key || "")}" data-view="${esc(action.view || "feed")}">${esc(action.label || "行动")}</button>
        `).join("")}
      </div>
    </div>
  `;
}

function runUnlockPathAction(btn) {
  const key = btn.dataset.key;
  if (key === "register") {
    setRegMode("reg");
    $("#authDialog").showModal();
    return;
  }
  if (key === "direct_unlock") {
    unlockRumor(btn.dataset.id);
    return;
  }
  switchView(btn.dataset.view || "feed");
}

async function openRumor(id) {
  const data = await api(`/api/rumors/${id}`);
  const bt = data.backtest;
  const item = data.item;
  const rights = item.rights || {};
  const envelope = data.rights_envelope || state.rightsEnvelope || {};
  const forensic = rights.forensic_signature || {};
  state.activeRumorId = item.id;
  $("#rumorDialog").dataset.rightsFp = rights.fingerprint || "";
  $("#rumorDialog").dataset.envelopeFp = envelope.fingerprint || "";
  $("#rumorDialog").dataset.forensicSignature = forensic.signature || "";
  $("#detailTier").textContent = `${item.ai_tier}${item.ai_score}`;
  $("#detailTarget").textContent = item.target;
  $("#detailMeta").innerHTML = `
    <span>${esc(item.recommendation_date)}</span>
    <span>${esc(item.submitter_name || "社区信息源")}</span>
    <span>${item.source === "reference" ? "历史样本" : "社区分享"}</span>
    ${item.institution ? `<span>${esc(item.institution)}</span>` : ""}
  `;
  $("#detailProvider").innerHTML = renderProviderSnapshot(item.provider, "detail");
  $("#detailVerdict").innerHTML = renderValueVerdict(item.value_verdict, "detail");
  $("#detailVerdict").innerHTML += renderRiskAnalysis(item.risk_analysis || {});
  $("#detailVerdict").innerHTML += renderScoreExplain(item.score_explanation, "detail");
  $("#detailRightsMark").textContent = `${rights.mark || ""}:${rights.fingerprint || ""}:${forensic.zero_width || ""}`;
  renderWatchTargets(item.stock_codes || [], item.watched);
  renderDecisionBrief(item.decision_brief || {});
  renderConsensusSnapshot(item.consensus_snapshot || {});
  $("#detailLogic").textContent = item.logic || "";
  $("#detailPoints").innerHTML = (item.key_points || []).map((p) => `<span>${esc(p)}</span>`).join("");
  renderVerificationTasks(item.verification_tasks || [], item.verification_bounties || []);
  renderVerificationLedger(item.verification_ledger || []);
  $("#detailScores").innerHTML = renderScoreDimensions(item.score_dimensions || {});
  $("#detailRaw").textContent = `${item.raw_content || ""}${forensic.zero_width || ""}`;
  $("#detailBacktest").innerHTML = bt ? `
    <strong>回测</strong>
    <span class="outcome-pill ${esc(bt.outcome?.state || "pending")}">${esc(bt.outcome?.label || "待验证")}</span>
    <span>T+1持1日 ${pct(bt.ret_t1_1)}</span>
    <span>T+1持5日 ${pct(bt.ret_t1_5)}</span>
    <span>T+1持20日 ${pct(bt.ret_t1_20)}</span>
    <span>信号价值 ${bt.signal_value != null ? bt.signal_value.toFixed(1) : "-"}</span>
    <small>${esc(bt.outcome?.summary || "")}</small>
    <small>${esc(bt.details || bt.status || "")}</small>
  ` : `<strong>回测</strong><span>暂无</span>`;
  renderModerationPanel(item.moderation || item.discussion?.moderation || {});
  renderDetailDiscussion(item.discussion || {}, data.comments || []);
  $("#rumorDialog").showModal();
}

function renderDecisionBrief(brief) {
  $("#detailDecisionBrief").innerHTML = `
    <div class="decision-head">
      <div>
        <span class="eyebrow">DECISION BRIEF</span>
        <h3>${esc(brief.label || "可观察")} · ${Number(brief.confidence || 0).toFixed(0)}% 证据</h3>
      </div>
      <span class="decision-state ${esc(brief.state || "watch")}">${esc(brief.state || "watch")}</span>
    </div>
    <p>${esc(brief.summary || "")}</p>
    <div class="decision-grid">
      <div><strong>看点</strong>${(brief.positives || []).map((item) => `<span>${esc(item)}</span>`).join("") || `<span>等待更多正向证据</span>`}</div>
      <div><strong>待验证</strong>${(brief.watch_points || []).map((item) => `<span>${esc(item)}</span>`).join("") || `<span>等待社区求证</span>`}</div>
      <div><strong>风险</strong>${(brief.risks || []).map((item) => `<span>${esc(item)}</span>`).join("") || `<span>仍需独立复核</span>`}</div>
    </div>
    <footer>
      <strong>${esc(brief.next_action || "继续观察")}</strong>
      <small>${esc(brief.disclaimer || "不构成投资建议。")}</small>
    </footer>
  `;
}

function renderConsensusSnapshot(snapshot) {
  const metrics = snapshot.metrics || {};
  const peers = snapshot.top_peers || [];
  $("#detailConsensus").innerHTML = `
    <div class="consensus-head">
      <div>
        <span class="eyebrow">COMMUNITY CONSENSUS</span>
        <h3>${esc(snapshot.label || "暂无共识")}</h3>
      </div>
      <span class="consensus-state ${esc(snapshot.state || "isolated")}">${esc(snapshot.state || "isolated")}</span>
    </div>
    <p>${esc(snapshot.summary || "")}</p>
    <div class="consensus-metrics">
      <div><span>相关线索</span><strong>${metrics.related || 0}</strong></div>
      <div><span>信息源</span><strong>${metrics.sources || 0}</strong></div>
      <div><span>高价值</span><strong>${metrics.high_value || 0}</strong></div>
      <div><span>风险分歧</span><strong>${metrics.risk || 0}</strong></div>
    </div>
    <div class="consensus-peers">
      ${peers.map((item) => `
        <button type="button" class="ghost consensus-peer" data-id="${item.id}">
          <span>${esc(item.target)}</span>
          <strong>${esc(item.tier)}${item.score}</strong>
          <small>${esc(item.source)} · ${esc(item.date || "")}</small>
        </button>
      `).join("") || `<span class="empty">暂无同标的高分样本。</span>`}
    </div>
    <footer>${esc(snapshot.next_action || "等待社区进一步求证。")}</footer>
  `;
  $$("#detailConsensus .consensus-peer").forEach((btn) => btn.addEventListener("click", () => openRumor(btn.dataset.id)));
}

function renderVerificationLedger(items) {
  $("#detailLedger").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">VALUE LEDGER</span>
        <h3>价值校验账本</h3>
      </div>
      <span>${items.length} 项</span>
    </div>
    <div class="ledger-grid">
      ${(items || []).map((item) => `
        <article class="ledger-item ${esc(item.state || "watch")}">
          <div>
            <span>${esc(item.label)}</span>
            <strong>${item.value == null ? "-" : esc(item.value)}</strong>
          </div>
          <p>${esc(item.detail || "")}</p>
        </article>
      `).join("")}
    </div>
  `;
}

function renderVerificationTasks(items, bounties = []) {
  $("#detailTasks").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">VERIFY TASKS</span>
        <h3>社区求证任务</h3>
      </div>
      <span>${items.length} 项</span>
    </div>
    ${renderVerificationBounties(bounties)}
    <div class="task-grid">
      ${(items || []).map((item) => `
        <article class="task-item ${esc(item.priority || "medium")} ${esc(item.status || "open")}">
          <div>
            <span>${esc(item.priority === "high" ? "高优先" : item.priority === "low" ? "低优先" : "中优先")}</span>
            <strong>${esc(item.label || "")}</strong>
          </div>
          <p>${esc(item.detail || "")}</p>
          <button type="button" class="ghost task-action" data-action="${esc(item.action || "讨论")}">${esc(item.action || "讨论")}</button>
        </article>
      `).join("")}
    </div>
  `;
  $$("#detailTasks .task-action").forEach((btn) => btn.addEventListener("click", () => {
    const action = btn.dataset.action || "讨论";
    if (action === "求证") reactToActiveRumor("verify");
    else if (action === "存疑") reactToActiveRumor("doubt");
    else if (action === "解锁") return;
    $("#commentInput")?.focus();
  }));
  $$("#detailTasks .bounty-action").forEach((btn) => btn.addEventListener("click", () => {
    const action = btn.dataset.action || "讨论";
    if (action === "求证") reactToActiveRumor("verify");
    else if (action === "存疑") reactToActiveRumor("doubt");
    else $("#commentForm input[name='content']")?.focus();
  }));
}

function renderVerificationBounties(items) {
  if (!items.length) return "";
  return `
    <div class="verification-bounties">
      ${(items || []).slice(0, 4).map((item) => `
        <article class="${esc(item.state || "active")}">
          <div>
            <span>${esc(item.label || "求证悬赏")}</span>
            <strong>+${Number(item.reward_xp || 0)} XP${Number(item.reputation_delta || 0) ? ` · 信誉 +${Number(item.reputation_delta || 0).toFixed(1)}` : ""}</strong>
          </div>
          <p>${esc(item.detail || "")}</p>
          <button type="button" class="ghost bounty-action" data-action="${esc(item.action || "讨论")}" ${item.state === "locked" ? "disabled" : ""}>${esc(item.cta || item.action || "参与")}</button>
        </article>
      `).join("")}
    </div>
  `;
}

function strip(html) { return html.replace(/<[^>]*>/g, ""); }

function zeroWidthEncode(text) {
  return [...String(text || "")].map((ch) => ch.charCodeAt(0).toString(2).padStart(16, "0").replace(/0/g, "\u200b").replace(/1/g, "\u200c")).join("\u200d");
}

function copyRightsPayload(sourceEl = null) {
  const dialogFp = $("#rumorDialog")?.dataset.rightsFp || "";
  const envelopeFp = $("#rumorDialog")?.dataset.envelopeFp || state.rightsEnvelope?.fingerprint || "";
  const forensic = $("#rumorDialog")?.dataset.forensicSignature || "";
  const card = sourceEl?.closest?.("[data-rights-fp]");
  const fp = dialogFp || card?.dataset.rightsFp || document.querySelector("[data-rights-fp]")?.dataset.rightsFp || envelopeFp || "page";
  const visible = `\n\n--\n来源：股情报 StockWhisper\n版权标记：${COPY_RIGHTS_MARK}\n内容指纹：${fp}${envelopeFp ? `\n页面指纹：${envelopeFp}` : ""}${forensic ? `\n取证签名：${forensic}` : ""}`;
  return { visible, hidden: zeroWidthEncode(`${COPY_RIGHTS_MARK}:${fp}:${envelopeFp || "page"}:${forensic || "page"}`) };
}

function installCopyWatermark() {
  document.addEventListener("copy", (event) => {
    const selection = window.getSelection();
    const selected = selection ? selection.toString() : "";
    if (!selected.trim()) return;
    const sourceEl = selection?.anchorNode?.nodeType === Node.ELEMENT_NODE ? selection.anchorNode : selection?.anchorNode?.parentElement;
    if (sourceEl?.closest?.(".copy-invite")) return;
    const payload = copyRightsPayload(sourceEl);
    event.clipboardData?.setData("text/plain", `${selected}${payload.visible}${payload.hidden}`);
    event.preventDefault();
  });
}

function renderWatchTargets(stocks, watched) {
  const target = $("#detailWatchTargets");
  if (!target) return;
  const usable = (stocks || []).filter((item) => item.code);
  target.innerHTML = usable.map((item) => `
    <button type="button" class="ghost detail-watch-btn ${watched ? "active-filter" : ""}" data-code="${esc(item.code)}" data-name="${esc(item.name || item.code)}">
      ${watched ? "已关注" : "关注"} ${esc(item.name || item.code)}
    </button>
  `).join("");
}

function renderDetailDiscussion(discussion, comments) {
  const reward = discussion.participation_reward;
  $("#detailReactions").innerHTML = [
    ["useful", "有用", discussion.useful || 0],
    ["verify", "求证", discussion.verify || 0],
    ["doubt", "存疑", discussion.doubt || 0],
  ].map(([reaction, label, count]) => `
    <button type="button" class="reaction-btn ${(discussion.my_reactions || []).includes(reaction) ? "active" : ""}" data-reaction="${reaction}">
      ${label} <strong>${count}</strong>
    </button>
  `).join("");
  $("#commentList").innerHTML = (comments || []).map((comment) => `
    <article class="comment">
      <strong>${esc(comment.display_name)}</strong>
      <time>${esc(comment.created_at)}</time>
      <p>${esc(comment.content)}</p>
    </article>
  `).join("") || `<p class="empty">暂无讨论，解锁后可以补充验证或风险点。</p>`;
  if (reward) {
    $("#commentList").insertAdjacentHTML("afterbegin", `
      <div class="participation-toast ${reward.awarded ? "awarded" : ""}">
        <strong>${esc(reward.message || "求证贡献已记录")}</strong>
        <span>${Number(reward.xp_delta || 0) ? `+${Number(reward.xp_delta || 0)} XP` : "已记录"}${Number(reward.reputation_delta || 0) ? ` · 信誉 +${Number(reward.reputation_delta || 0).toFixed(1)}` : ""}</span>
      </div>
    `);
  }
}

function trustLabel(state) {
  return { clear: "清洁", watch: "观察", review: "待复核", limited: "已降权" }[state || "clear"] || "观察";
}

function outcomeLabel(outcome) {
  if (!outcome) return "-";
  const score = outcome.score == null ? "" : ` ${Number(outcome.score).toFixed(1)}`;
  return `${outcome.label || "-"}${score}`;
}

function renderModerationPanel(moderation = {}) {
  const reports = Number(moderation.reports || 0);
  const myReports = moderation.my_reports || [];
  const labels = moderation.report_labels || [];
  $("#detailModeration").innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">TRUST CHECK</span>
        <h3>可信度校验</h3>
      </div>
      <span class="trust-state ${esc(moderation.trust_state || "clear")}">${trustLabel(moderation.trust_state)}${reports ? ` · ${reports}` : ""}</span>
    </div>
    <div class="moderation-body">
      <p>${reports ? "该线索存在社区举报，已纳入有效分和信息源评分。" : "暂无举报。若发现不实、推广或重复搬运，可提交结构化反馈。"}</p>
      <div class="report-labels">
        ${labels.map((item) => `<span>${esc(item.label)} ${item.count}</span>`).join("")}
      </div>
      <div class="report-actions">
        ${REPORT_OPTIONS.map(([reason, label]) => `
          <button type="button" class="ghost report-btn ${myReports.includes(reason) ? "active-filter" : ""}" data-reason="${reason}">
            ${esc(label)}
          </button>
        `).join("")}
      </div>
    </div>
  `;
}

async function reportActiveRumor(reason) {
  if (!state.activeRumorId) return;
  try {
    const data = await api(`/api/rumors/${state.activeRumorId}/reports`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    renderModerationPanel(data.moderation || {});
    if (data.discussion) {
      const comments = await api(`/api/rumors/${state.activeRumorId}/comments`);
      renderDetailDiscussion(data.discussion || comments.discussion || {}, comments.items || []);
    }
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

async function reactToActiveRumor(reaction) {
  if (!state.activeRumorId) return;
  try {
    const data = await api(`/api/rumors/${state.activeRumorId}/reactions`, {
      method: "POST",
      body: JSON.stringify({ reaction }),
    });
    const comments = await api(`/api/rumors/${state.activeRumorId}/comments`);
    const discussion = data.discussion || comments.discussion || {};
    discussion.participation_reward = data.participation_reward;
    renderDetailDiscussion(discussion, comments.items || []);
    await loadMe();
    await loadGrowthCenter();
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

async function submitComment(event) {
  event.preventDefault();
  if (!state.activeRumorId) return;
  const input = event.target.elements.content;
  const content = input.value.trim();
  if (!content) return;
  try {
    const data = await api(`/api/rumors/${state.activeRumorId}/comments`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    input.value = "";
    const discussion = data.discussion || {};
    discussion.participation_reward = data.participation_reward;
    renderDetailDiscussion(discussion, data.items || []);
    await loadMe();
    await loadGrowthCenter();
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

function renderScoreDimensions(dimensions) {
  const labels = { specificity: "标的明确", evidence: "信息密度", freshness: "时效", source: "来源", verifiability: "可验证" };
  return Object.entries(labels).map(([key, label]) => {
    const value = Number(dimensions[key] || 0);
    const cap = key === "freshness" || key === "source" ? 10 : key === "verifiability" ? 20 : 30;
    return `
      <div class="score-row">
        <span>${label}</span>
        <strong>${value}</strong>
        <i><b style="width:${Math.min(100, value / cap * 100)}%"></b></i>
      </div>
    `;
  }).join("");
}

function esc(value) {
  return String(value || "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function splitTargetNames(target) {
  return [...new Set(String(target || "")
    .split(/[、,，/;；\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 20))];
}

function renderStockRows(items) {
  const rows = (items || []).filter((item) => item && (item.name || item.code));
  const wrap = $("#stockRows");
  if (!rows.length) {
    wrap.innerHTML = `<div class="stock-empty">暂无识别标的，可点击 AI 提炼或手动新增。</div>`;
    syncStockCodesField();
    return;
  }
  wrap.innerHTML = rows.map((item, idx) => `
    <div class="stock-row">
      <input class="stock-name-input" value="${esc(item.name || "")}" placeholder="中文名称" />
      <input class="stock-code-input" value="${esc(item.code || "")}" placeholder="例如 600000.sh，可留空" />
      <div class="stock-row-actions">
        <button type="button" class="ghost stock-add" title="新增标的">+</button>
        <button type="button" class="ghost stock-remove" title="删除标的" ${rows.length === 1 && idx === 0 ? "disabled" : ""}>-</button>
      </div>
    </div>
  `).join("");
  syncStockCodesField();
}

function addStockRow(afterRow = null) {
  const rows = stockRowItems(true);
  if (!rows.length || !afterRow) {
    rows.push({ name: "", code: "" });
  } else {
    const idx = $$("#stockRows .stock-row").indexOf(afterRow);
    rows.splice(idx + 1, 0, { name: "", code: "" });
  }
  renderStockRows(rows);
  const inputs = $$("#stockRows .stock-name-input");
  inputs[Math.min(inputs.length - 1, rows.length - 1)]?.focus();
}

function stockRowItems(keepEmpty = false) {
  const items = $$("#stockRows .stock-row").map((row) => ({
    name: row.querySelector(".stock-name-input").value.trim(),
    code: row.querySelector(".stock-code-input").value.trim(),
  }));
  return keepEmpty ? items : items.filter((item) => item.name || item.code);
}

function currentStockItems() {
  return stockRowItems(false);
}

function syncStockCodesField() {
  const form = $("#submitForm");
  if (!form || !form.elements.stock_codes) return;
  const items = currentStockItems();
  form.elements.stock_codes.value = JSON.stringify(items);
  form.elements.target.value = items.map((item) => item.name || item.code).filter(Boolean).join("、");
}

async function unlockRumor(id) {
  try {
    await api(`/api/rumors/${id}/unlock`, { method: "POST" });
    await loadMe();
    await loadGrowthCenter();
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors();
  } catch (err) {
    alert(err.message);
  }
}

async function submitRumor(event) {
  event.preventDefault();
  syncStockCodesField();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  const result = $("#submitResult");
  if (!payload.target) {
    result.classList.add("show");
    result.innerHTML = `<p class="neg">请至少填写一个推荐标的。</p>`;
    return;
  }
  try {
    const data = await api("/api/rumors", { method: "POST", body: JSON.stringify(payload) });
    const reward = data.submission_reward || {};
    const risk = data.risk_analysis || {};
    result.classList.add("show");
    result.innerHTML = `
      <div class="submit-success-head">
        <div>
          <span class="eyebrow">SUBMITTED</span>
          <h3>评分 ${esc(data.score.tier)}${data.score.score}</h3>
        </div>
        <strong>${esc(reward.provider_grade?.name || "信息源成长")}</strong>
      </div>
      <div class="submit-reward-grid">
        <div><span>XP变化</span><strong>${Number(reward.xp_delta || 0) >= 0 ? "+" : ""}${reward.xp_delta || 0}</strong></div>
        <div><span>贡献变化</span><strong>${Number(reward.contribution_delta || 0) >= 0 ? "+" : ""}${Number(reward.contribution_delta || 0).toFixed(1)}</strong></div>
        <div><span>源分变化</span><strong>${Number(reward.source_score_delta || 0) >= 0 ? "+" : ""}${Number(reward.source_score_delta || 0).toFixed(1)}</strong></div>
      </div>
      <p>${esc((data.score.reasons || []).join("、"))}</p>
      ${renderRiskAnalysis(risk)}
      <p>稀缺性：${esc(data.novelty?.label || "待判断")}，相关线索 ${data.novelty?.related_count || 0} 条</p>
      <p>${esc(reward.exchange_label || "已进入社区交换池")}</p>
      <p>${esc(reward.unlocked_label || (data.unlocked ? `已解锁：${data.unlocked.target}` : "暂无可匹配解锁消息"))}</p>
      ${reward.unlock_match ? `<p>匹配原因：${esc(reward.unlock_match.reason)} · 分差 ${reward.unlock_match.score_gap}</p>` : ""}
      <p>${esc((data.summary.key_points || []).join("；"))}</p>
    `;
    event.target.reset();
    setDefaultRecommendationDate();
    renderStockRows([]);
    $("#qualityPreview").classList.remove("show");
    $("#qualityPreview").innerHTML = "";
    await loadMe();
    await loadGrowthCenter();
    await loadCommunityInsight();
    await loadCommunityRooms();
    await loadActivityFeed();
    await loadExchangeDesk();
    await loadModerationSummary();
    await loadRumors();
  } catch (err) {
    result.classList.add("show");
    result.innerHTML = `
      <p class="neg">${esc(err.message)}</p>
      ${/注册|登录/.test(err.message) ? `<button type="button" class="ghost submit-register-now">注册后提交</button>` : ""}
    `;
  }
}

async function previewScore(silent = false) {
  const form = $("#submitForm");
  syncStockCodesField();
  const payload = Object.fromEntries(new FormData(form).entries());
  const panel = $("#qualityPreview");
  if (!payload.raw_content || payload.raw_content.trim().length < 10) {
    if (!silent) {
      panel.classList.add("show");
      panel.innerHTML = `<p class="neg">请先粘贴至少 10 个字的原始内容。</p>`;
    }
    return;
  }
  try {
    const data = await api("/api/score-preview", { method: "POST", body: JSON.stringify(payload) });
    renderQualityPreview(data);
  } catch (err) {
    if (!silent) {
      panel.classList.add("show");
      panel.innerHTML = `<p class="neg">${err.message}</p>`;
    }
  }
}

function renderQualityPreview(data) {
  state.lastQualityPreview = data;
  const score = data.score || {};
  const dims = score.dimensions || {};
  const labels = { specificity: "明确", evidence: "密度", freshness: "时效", source: "来源", verifiability: "验证", novelty: "稀缺" };
  const novelty = data.novelty || {};
  const risk = data.risk_analysis || {};
  const readiness = data.exchange_readiness || {};
  const reward = data.participation_reward || {};
  $("#qualityPreview").classList.add("show");
  $("#qualityPreview").innerHTML = `
    <div class="quality-head">
      <div>
        <span class="eyebrow">QUALITY PREVIEW</span>
        <h3>${esc(score.tier || "C")}${score.score || 0} · ${esc(data.quality_floor || "")}</h3>
      </div>
      <strong>${esc(data.next_tier || "C")}级</strong>
    </div>
    <div class="readiness-gate ${esc(readiness.state || "draft")}">
      <div>
        <span>交换就绪度</span>
        <strong>${Number(readiness.score || 0).toFixed(1)}%</strong>
      </div>
      <div>
        <b>${esc(readiness.label || "建议补充后再提交")}</b>
        <p>${esc(readiness.action || "")}</p>
        ${(readiness.blockers || []).length ? `<small>缺口：${(readiness.blockers || []).map(esc).join("、")}</small>` : `<small>${readiness.ok_count || 0}/${readiness.total || 0} 项已达标</small>`}
      </div>
    </div>
    <div class="participation-reward">
      <div>
        <span>预计成长</span>
        <strong>+${Number(reward.estimated_xp || 0)} XP</strong>
      </div>
      <div>
        <span>源分影响</span>
        <strong>+${Number(reward.source_score_delta || 0).toFixed(1)}</strong>
      </div>
      <div>
        <span>交换权益</span>
        <strong>${esc(reward.exchange_power || "C")}级</strong>
      </div>
      <p>${esc(reward.next_action || "完善草稿后再提交。")}</p>
    </div>
    ${renderEvidenceLadder(data.evidence_ladder || {})}
    <div class="quality-dims">
      ${Object.entries(labels).map(([key, label]) => {
        const value = Number(dims[key] || 0);
        const cap = key === "freshness" || key === "source" ? 10 : key === "verifiability" ? 20 : key === "novelty" ? 15 : 30;
        return `<div><span>${label}</span><i><b style="width:${Math.min(100, value / cap * 100)}%"></b></i><strong>${value}</strong></div>`;
      }).join("")}
    </div>
    <div class="novelty-card ${esc(novelty.state || "unknown")}">
      <strong>${esc(novelty.label || "稀缺性待判断")}</strong>
      <span>相关 ${novelty.related_count || 0} 条 · 重合 ${Math.round(Number(novelty.max_overlap || 0) * 100)}%</span>
    </div>
    ${renderRiskAnalysis(risk)}
    <div class="quality-checks">
      ${(data.checklist || []).map((item) => `<span class="${item.ok ? "ok" : ""}">${item.ok ? "✓" : ""}${esc(item.label)}</span>`).join("")}
    </div>
    <ul class="quality-suggestions">
      ${(data.suggestions || []).map((item) => `<li>${esc(item)}</li>`).join("")}
    </ul>
    ${renderImprovementPlan(data.improvement_plan || [])}
  `;
}

function renderEvidenceLadder(ladder) {
  const levels = ladder.levels || [];
  if (!levels.length) return "";
  return `
    <div class="evidence-ladder ${esc(ladder.grade || "C")}">
      <div class="evidence-ladder-head">
        <div>
          <span class="eyebrow">EVIDENCE LADDER</span>
          <strong>${esc(ladder.label || "证据链待判断")}</strong>
          <p>${esc(ladder.summary || "")}</p>
        </div>
        <b>${esc(ladder.grade || "C")}${Number(ladder.score || 0)}</b>
      </div>
      <div class="evidence-levels">
        ${levels.map((item) => `
          <div class="${esc(item.state || "weak")}">
            <span>${esc(item.label || "")}</span>
            <strong>${esc(item.state || "weak")}</strong>
            <p>${esc(item.detail || "")}</p>
          </div>
        `).join("")}
      </div>
      <small>下一步：${(ladder.next_steps || []).map(esc).join("、")}</small>
    </div>
  `;
}

function renderRiskAnalysis(risk) {
  if (!risk || !risk.level || risk.level === "clear") return "";
  const flags = Array.isArray(risk.flags) ? risk.flags : [];
  return `
    <div class="risk-analysis-card ${esc(risk.level)}">
      <div>
        <strong>${esc(risk.label || "需核验话术")}</strong>
        <p>${esc(risk.summary || "")}</p>
      </div>
      ${flags.length ? `
        <div class="risk-flags">
          ${flags.map((flag) => `<span>${esc(flag.label || "")}${flag.matched?.length ? `：${flag.matched.map(esc).join("、")}` : ""}</span>`).join("")}
        </div>
      ` : ""}
    </div>
  `;
}

function actionableImprovementItems(plan) {
  return (plan || []).filter((item) => item && item.action && item.action !== "manual" && item.value !== undefined && item.value !== "");
}

function renderImprovementPlan(plan) {
  const items = (plan || []).filter(Boolean);
  if (!items.length) return "";
  const actionable = actionableImprovementItems(items);
  const actionLabels = {
    apply_stock_rows: "自动补标的",
    fill_if_empty: "空项补齐",
    fill_if_weak: "弱项补强",
    manual: "人工补充",
  };
  return `
    <div class="improvement-plan">
      <div class="improvement-plan-head">
        <div>
          <span class="eyebrow">DRAFT UPGRADE</span>
          <strong>草稿补强建议</strong>
        </div>
        ${actionable.length ? `<button type="button" class="ghost apply-improvement-plan">一键补强草稿</button>` : ""}
      </div>
      <div class="improvement-items">
        ${items.map((item) => `
          <div class="improvement-item ${item.action === "manual" ? "manual" : "auto"}">
            <span>${esc(actionLabels[item.action] || "建议")}</span>
            <strong>${esc(item.label || "")}</strong>
            <p>${esc(item.reason || "")}</p>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

async function applyImprovementPlan(plan) {
  const form = $("#submitForm");
  if (!form) return;
  let changed = false;
  for (const item of actionableImprovementItems(plan)) {
    const action = item.action;
    const field = item.field;
    if (action === "apply_stock_rows") {
      const nextRows = Array.isArray(item.value) ? item.value.filter((row) => row && (row.name || row.code)) : [];
      if (!currentStockItems().length && nextRows.length) {
        renderStockRows(nextRows);
        changed = true;
      }
      continue;
    }
    const input = form.elements[field];
    if (!input) continue;
    const current = String(input.value || "").trim();
    const nextValue = String(item.value || "").trim();
    if (!nextValue) continue;
    if (action === "fill_if_empty" && !current) {
      input.value = nextValue;
      changed = true;
    }
    if (action === "fill_if_weak" && current.length < 12 && nextValue.length > current.length) {
      input.value = nextValue;
      changed = true;
    }
  }
  if (changed) {
    syncStockCodesField();
    await previewScore(true);
  }
}

async function summarizeRumor() {
  const form = $("#submitForm");
  const btn = $("#summarizeBtn");
  const payload = Object.fromEntries(new FormData(form).entries());
  const result = $("#submitResult");
  if (!payload.raw_content || payload.raw_content.trim().length < 10) {
    result.classList.add("show");
    result.innerHTML = `<p class="neg">请先粘贴至少 10 个字的原始内容。</p>`;
    return;
  }
  btn.disabled = true;
  btn.textContent = "提炼中...";
  result.classList.add("show");
  result.innerHTML = `<p>正在提炼标的和股票代码...</p>`;
  try {
    const data = await api("/api/rumors/summarize", { method: "POST", body: JSON.stringify(payload) });
    renderStockRows(data.stock_codes && data.stock_codes.length ? data.stock_codes : splitTargetNames(data.target).map((name) => ({ name, code: "" })));
    form.elements.logic.value = form.elements.logic.value || data.logic || "";
    form.elements.institution.value = form.elements.institution.value || data.institution || "";
    form.elements.recommender.value = form.elements.recommender.value || data.recommender || "";
    result.classList.add("show");
    result.innerHTML = `
      <h3>${data.summary_source === "llm" ? "AI 已提炼" : "规则已提炼"}</h3>
      <p>${(data.key_points || []).join("；")}</p>
    `;
    await previewScore(true);
  } catch (err) {
    result.classList.add("show");
    result.innerHTML = `<p class="neg">${err.message}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "AI 提炼";
  }
}

async function loadBacktests() {
  const data = await api("/api/backtests");
  $("#backtestRows").innerHTML = data.items.map((r) => `
    <tr>
      <td>${r.target}</td><td>${r.ai_tier}${r.ai_score}</td><td>${r.code || "-"}</td>
      <td>${pct(r.ret_t1_1)}</td><td>${pct(r.ret_t1_5)}</td><td>${pct(r.ret_t1_20)}</td>
      <td>${r.signal_value != null ? r.signal_value.toFixed(1) : "-"}</td>
      <td><span class="outcome-text ${esc(r.outcome?.state || "pending")}">${esc(outcomeLabel(r.outcome))}</span></td>
    </tr>
  `).join("");
}

async function loadLeaderboard() {
  const data = await api("/api/leaderboard");
  $("#leaderboard").innerHTML = `
    <div class="rank-rule">${esc(data.rank_rule || "按信息源分排序")}</div>
    ${(data.items || []).map((u, i) => `
    <div class="rank-row">
      <strong>#${u.source_rank || i + 1}</strong>
      <div><strong>${esc(u.display_name)}</strong><p>${esc(u.provider_grade?.name || u.level)}</p></div>
      <span>${Number(u.provider_grade?.score || 0).toFixed(1)} 源分</span>
      <span>${Number(u.contribution || 0).toFixed(1)} 贡献</span>
      <span>${Number(u.reputation).toFixed(1)} 分</span>
      <span>${u.invite_count || 0} 邀请</span>
      <button class="ghost provider-open" data-id="${u.id}">档案</button>
      <div>${renderRadar(u.radar)}</div>
    </div>
  `).join("")}
  `;
}

async function openProviderProfile(id) {
  try {
    const data = await api(`/api/providers/${id}`);
    renderProviderProfile(data);
    $("#providerDialog").showModal();
  } catch (err) {
    alert(err.message);
  }
}

function renderProviderProfile(data) {
    $("#providerGrade").textContent = `${Number(data.provider_grade?.score || 0).toFixed(1)}源分`;
    $("#providerName").textContent = data.display_name;
    $("#providerMeta").innerHTML = `
      <span>${esc(data.provider_grade?.name || data.level)}</span>
      <span>${esc(data.level)}</span>
      <span>${Number(data.contribution || 0).toFixed(1)} 贡献</span>
      <span>${Number(data.feedback_score || 0).toFixed(1)} 反馈</span>
      <span>${data.follower_count || 0} 关注</span>
    `;
    $("#providerFollowAction").innerHTML = `<button class="ghost ${data.followed ? "active-filter" : ""}" data-id="${data.id}" data-follow="${data.followed ? "0" : "1"}">${data.followed ? "已关注，点击取消" : "关注信息源"}</button>`;
    $("#providerScores").innerHTML = `
      <div><span>XP</span><strong>${data.xp}</strong></div>
      <div><span>信誉</span><strong>${Number(data.reputation || 0).toFixed(1)}</strong></div>
      <div><span>有用</span><strong>${data.feedback?.useful || 0}</strong></div>
      <div><span>求证</span><strong>${data.feedback?.verify || 0}</strong></div>
      <div><span>存疑</span><strong>${data.feedback?.doubt || 0}</strong></div>
    `;
    renderProviderProof(data.provider_proof || [], data.credibility_passport || {});
    const tr = data.track_record || {};
    $("#providerTrack").innerHTML = `
      <div><span>样本</span><strong>${tr.samples || 0}</strong></div>
      <div><span>命中率</span><strong>${tr.hit_rate == null ? "-" : `${Number(tr.hit_rate).toFixed(1)}%`}</strong></div>
      <div><span>信号价值</span><strong>${tr.avg_signal == null ? "-" : Number(tr.avg_signal).toFixed(1)}</strong></div>
      <div><span>T+1</span><strong>${strip(pct(tr.avg_ret_1))}</strong></div>
      <div><span>T+5</span><strong>${strip(pct(tr.avg_ret_5))}</strong></div>
      <div><span>T+20</span><strong>${strip(pct(tr.avg_ret_20))}</strong></div>
      <div><span>回撤</span><strong>${strip(pct(tr.avg_drawdown))}</strong></div>
    `;
    $("#providerRecent").innerHTML = (data.recent || []).map((item) => `
      <article>
        <div><strong>${esc(item.target)}</strong><span class="badge">${esc(item.ai_tier)}${item.ai_score}</span></div>
        <p>${esc(item.logic)}</p>
        <footer><span>${esc(item.recommendation_date)}</span><span>${esc(item.outcome?.label || "待验证")}</span><span>讨论 ${item.discussion?.comments || 0}</span></footer>
      </article>
    `).join("") || `<p class="empty">暂无近期线索</p>`;
}

function renderProviderProof(items, passport = null) {
  $("#providerProofPanel").innerHTML = `
    ${passport ? renderCredibilityPassport(passport) : ""}
    <div class="block-head">
      <div>
        <span class="eyebrow">SOURCE PROOF</span>
        <h3>信息源证明卡</h3>
      </div>
      <span>${items.length} 项</span>
    </div>
    <div class="provider-proof-grid">
      ${items.map((item) => `
        <article class="provider-proof-item ${esc(item.state || "watch")}">
          <div>
            <span>${esc(item.label || "")}</span>
            <strong>${esc(item.value == null ? "-" : item.value)}</strong>
          </div>
          <p>${esc(item.detail || "")}</p>
        </article>
      `).join("")}
    </div>
  `;
}

// ── 登录/注册弹窗 ──────────────────────────────────────────
let regEmail = "";

function refreshAuthIncentiveIfOpen() {
  const dialog = $("#authDialog");
  if (!dialog?.open) return;
  renderAuthIncentive($("#regForm")?.style.display === "none" ? "login" : "reg");
}

function setRegMode(step) {
  const isLogin = step === "login";
  const isReg   = step === "reg";
  $("#authTitle").textContent = isLogin ? "登录" : "注册";
  renderAuthIncentive(step);
  $("#loginForm").style.display    = isLogin ? "" : "none";
  $("#regForm").style.display      = isReg   ? "" : "none";
  $("#loginBtn").style.display     = isLogin ? "" : "none";
  $("#toRegBtn").style.display     = isLogin ? "" : "none";
  $("#doRegisterBtn").style.display = isReg  ? "" : "none";
  $("#regBackBtn").style.display   = isReg   ? "" : "none";
  $("#authMsg").textContent = "";
}

function renderAuthIncentive(step = "login") {
  const root = $("#authIncentive");
  if (!root) return;
  const activation = state.activation || {};
  const invite = state.invitePreview || {};
  const opportunity = state.opportunity || state.community?.opportunity_summary || {};
  const stats = state.community?.stats || {};
  const registeredCopy = invite.valid
    ? `使用邀请码 ${esc(invite.invite_code || "")} 注册，获得 20 XP 和 2 次直看额度。`
    : "注册后获得永久成长记录、1 次直看额度，并可通过投稿/邀请继续解锁。";
  const headline = step === "reg" ? "注册后把浏览变成可积累的情报账户" : "登录后恢复你的自选、解锁和信息源成长";
  root.innerHTML = `
    <div class="auth-incentive-head">
      <span class="eyebrow">${step === "reg" ? "MEMBER ACCESS" : "ACCOUNT VALUE"}</span>
      <strong>${headline}</strong>
      <p>${esc(step === "reg" ? registeredCopy : activation.headline || "你的贡献、求证和邀请都会进入成长账本。")}</p>
    </div>
    <div class="auth-proof-grid">
      <div><span>高价值待解</span><strong>${opportunity.cards?.find?.((item) => item.key === "locked")?.value ?? activation.summary?.locked_high_value ?? 0}</strong></div>
      <div><span>社区线索</span><strong>${stats.total_rumors || activation.summary?.high_value_rumors || 0}</strong></div>
      <div><span>启动权益</span><strong>${invite.valid ? "20XP" : `${activation.summary?.direct_quota || 1}直看`}</strong></div>
    </div>
  `;
}

function toggleEye(targetId) {
  const input = $(`#${targetId}`);
  input.type = input.type === "password" ? "text" : "password";
}

async function auth(mode) {
  const payload = { username: $("#authUser").value.trim(), password: $("#authPass").value };
  try {
    const data = await api(`/api/${mode}`, { method: "POST", body: JSON.stringify(payload) });
    state.user = data.user; state.level = data.level;
    $("#authDialog").close(); setRegMode("login");
    await refreshIdentitySurfaces();
  } catch (err) { $("#authMsg").textContent = err.message; }
}

async function sendCode() {
  const email = $("#regEmail").value.trim();
  if (!email) { $("#authMsg").textContent = "请填写邮箱"; return; }
  const btn = $("#sendCodeBtn");
  btn.disabled = true; btn.textContent = "发送中…";
  try {
    await api("/api/send-code", { method: "POST", body: JSON.stringify({ email }) });
    regEmail = email;
    $("#authMsg").textContent = `验证码已发至 ${email}，5分钟内有效`;
    setTimeout(() => { btn.disabled = false; btn.textContent = "重新发送"; }, 60000);
  } catch (err) {
    $("#authMsg").textContent = err.message;
    btn.disabled = false; btn.textContent = "发送验证码";
  }
}

async function doRegister() {
  const username = $("#regUser").value.trim();
  const password = $("#regPass").value;
  const confirm  = $("#regPassConfirm").value;
  const code     = $("#regCode").value.trim();
  const email    = $("#regEmail").value.trim();
  const invite_code = $("#regInvite").value.trim();
  if (!username)            { $("#authMsg").textContent = "请填写用户名"; return; }
  if (password.length < 6)  { $("#authMsg").textContent = "密码至少6位"; return; }
  if (password !== confirm)  { $("#authMsg").textContent = "两次密码不一致"; return; }
  if (!email)               { $("#authMsg").textContent = "请填写邮箱"; return; }
  if (code.length !== 6)    { $("#authMsg").textContent = "请填写6位验证码"; return; }
  try {
    const data = await api("/api/register", { method: "POST", body: JSON.stringify({ username, password, email, code, invite_code }) });
    state.user = data.user; state.level = data.level;
    $("#authDialog").close(); setRegMode("login");
    await refreshIdentitySurfaces();
  } catch (err) { $("#authMsg").textContent = err.message; }
}

async function refreshIdentitySurfaces() {
  renderProfile();
  await Promise.all([
    loadActivationCenter(),
    loadReferralCenter(),
    loadGrowthCenter(),
    loadWatchlist(),
    loadProviderFollows(),
    loadCommunityInsight(),
    loadCommunityRooms(),
    loadActivityFeed(),
    loadExchangeDesk(),
    loadModerationSummary(),
    loadDailyStats(),
  ]);
}

function hydrateInviteFromUrl() {
  const invite = new URLSearchParams(location.search).get("invite");
  if (!invite) return;
  state.inviteCodeFromUrl = invite.trim().toUpperCase();
  if ($("#regInvite")) $("#regInvite").value = state.inviteCodeFromUrl;
}

// ── 事件绑定 ───────────────────────────────────────────────
function wire() {
  $$(".nav button").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
  $$("[data-view-jump]").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.viewJump)));
  document.body.addEventListener("click", async (e) => {
    const invite = e.target.closest(".copy-invite, .copy-invite-copy, .invite-momentum-action");
    const provider = e.target.closest(".provider-snapshot.provider-open");
    if (provider) return openProviderProfile(provider.dataset.id);
    if (!invite || invite.disabled) return;
    if (invite.dataset.key === "review_rank") {
      switchView("rank");
      return;
    }
    const text = invite.dataset.invite || invite.dataset.copy || "";
    const originalText = invite.textContent;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const input = document.createElement("input");
      input.value = text;
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    invite.textContent = "已复制";
    setTimeout(() => { invite.textContent = originalText; }, 1200);
  });
  $("#rumorGrid").addEventListener("click", (e) => {
    const pathAction = e.target.closest(".unlock-path-action");
    if (pathAction) return runUnlockPathAction(pathAction);
    const open = e.target.closest(".open-btn");
    if (open) return openRumor(open.dataset.id);
    const unlock = e.target.closest(".unlock-btn");
    if (unlock) return unlockRumor(unlock.dataset.id);
  });
  $("#leaderboard").addEventListener("click", (e) => {
    const btn = e.target.closest(".provider-open");
    if (btn) openProviderProfile(btn.dataset.id);
  });
  const sentinel = $("#feedSentinel");
  if (sentinel && "IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) loadRumors(false);
    }, { rootMargin: "200px" }).observe(sentinel);
  }
  $("#searchInput").addEventListener("input", debounce(() => loadRumors(true), 250));
  $("#tierFilter").addEventListener("change", () => { state.selectedTier = $("#tierFilter").value; loadRumors(true); });
  $("#refreshFeed").addEventListener("click", async () => { await loadCommunityInsight(); await loadCommunityRooms(); await loadActivationCenter(); await loadActivityFeed(); await loadExchangeDesk(); await loadModerationSummary(); await loadReferralCenter(); await loadDailyStats(); });
  $("#loadHighlights").addEventListener("click", loadCommunityInsight);
  $("#submitForm").addEventListener("submit", submitRumor);
  $("#submitResult").addEventListener("click", (e) => {
    if (e.target.closest(".submit-register-now")) {
      setRegMode("reg");
      $("#authDialog").showModal();
    }
  });
  $("#summarizeBtn").addEventListener("click", summarizeRumor);
  $("#previewScoreBtn").addEventListener("click", () => previewScore(false));
  $("#submitForm").addEventListener("input", debounce(() => previewScore(true), 500));
  $("#qualityPreview").addEventListener("click", (e) => {
    if (e.target.closest(".apply-improvement-plan")) applyImprovementPlan(state.lastQualityPreview?.improvement_plan || []);
  });
  $("#addStockRow").addEventListener("click", () => addStockRow());
  $("#stockRows").addEventListener("input", (e) => {
    if (e.target.closest(".stock-code-input") || e.target.closest(".stock-name-input")) syncStockCodesField();
  });
  $("#stockRows").addEventListener("click", (e) => {
    const row = e.target.closest(".stock-row");
    if (e.target.closest(".stock-add")) addStockRow(row);
    if (e.target.closest(".stock-remove") && row) {
      const rows = stockRowItems(true);
      const idx = $$("#stockRows .stock-row").indexOf(row);
      rows.splice(idx, 1);
      renderStockRows(rows);
    }
  });
  $("#refreshBacktest").addEventListener("click", async () => { await api("/api/backtests/refresh", { method: "POST" }); loadBacktests(); });
  $("#detailClose").addEventListener("click", () => $("#rumorDialog").close());
  $("#providerClose").addEventListener("click", () => $("#providerDialog").close());
  $("#providerFollowAction").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (btn) setProviderFollow(btn.dataset.id, btn.dataset.follow === "1");
  });
  $("#rumorDialog").addEventListener("close", () => { state.activeRumorId = null; });
  $("#detailReactions").addEventListener("click", (e) => {
    const btn = e.target.closest(".reaction-btn");
    if (btn) reactToActiveRumor(btn.dataset.reaction);
  });
  $("#detailWatchTargets").addEventListener("click", (e) => {
    const btn = e.target.closest(".detail-watch-btn");
    if (btn) addWatch({ code: btn.dataset.code, name: btn.dataset.name });
  });
  $("#detailModeration").addEventListener("click", (e) => {
    const btn = e.target.closest(".report-btn");
    if (btn) reportActiveRumor(btn.dataset.reason);
  });
  $("#commentForm").addEventListener("submit", submitComment);

  // 登录/注册弹窗
  $("#loginOpen").addEventListener("click", () => { setRegMode("login"); $("#authDialog").showModal(); });
  $("#loginBtn").addEventListener("click", () => auth("login"));
  $("#toRegBtn").addEventListener("click", () => setRegMode("reg"));
  $("#sendCodeBtn").addEventListener("click", sendCode);
  $("#doRegisterBtn").addEventListener("click", doRegister);
  $("#regBackBtn").addEventListener("click", () => setRegMode("login"));
  // 眼睛按钮（事件委托）
  $("#authDialog").addEventListener("click", (e) => {
    const btn = e.target.closest(".eye-btn");
    if (btn) toggleEye(btn.dataset.target);
  });

  $("#logoutBtn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    await loadMe();
    await refreshIdentitySurfaces();
  });
}

function debounce(fn, wait) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
}

wire();
hydrateInviteFromUrl();
installCopyWatermark();
setDefaultRecommendationDate();
renderStockRows([]);
loadMe().then(async () => {
  await Promise.all([loadInvitePreviewFromUrl(), loadValueFramework(), loadCommunityInsight(), loadCommunityRooms(), loadActivationCenter(), loadGrowthCenter(), loadWatchlist(), loadProviderFollows(), loadActivityFeed(), loadExchangeDesk(), loadModerationSummary(), loadReferralCenter()]);
  renderInvitePanel();
  await loadDailyStats();
});
