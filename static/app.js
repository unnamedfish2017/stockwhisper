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

const VIEW_FALLBACKS = {
  backtest: "rank",
  register: "feed",
  auth: "feed",
  detail: "feed",
};

function isLoggedIn() {
  return !!state.user && !state.user.is_guest;
}

function promptLogin(message = "登录后才能添加自选股。") {
  $("#authMsg").textContent = message;
  $("#regInvite").value = state.inviteCodeFromUrl || $("#regInvite").value || "";
  setRegMode("login");
  $("#authDialog").showModal();
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
  const normalized = VIEW_FALLBACKS[view] || view || "feed";
  const target = $(`#${normalized}View`);
  if (!target) {
    console.warn(`Unknown view: ${view}`);
    return switchView("feed");
  }
  state.view = normalized;
  $$(".view").forEach((v) => v.classList.remove("active"));
  $$(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === normalized));
  target.classList.add("active");
  if (normalized === "watch") loadWatchPage();
  if (normalized === "rank") {
    loadSourceUpgradeCenter();
    loadLeaderboard();
  }
}

function runSearch() {
  state.watchOnly = false;
  state.followedOnly = false;
  state.selectedTier = $("#tierFilter").value;
  switchView("feed");
  return loadRumors(true);
}

function clearSearch() {
  $("#searchInput").value = "";
  $("#tierFilter").value = "";
  state.selectedTier = "";
  state.watchOnly = false;
  state.followedOnly = false;
  switchView("feed");
  return loadRumors(true);
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
    const rawQ = $("#searchInput").value.trim();
    const searching = !!rawQ;
    const q = encodeURIComponent(rawQ);
    const tier = encodeURIComponent(state.selectedTier || $("#tierFilter").value);
    const date = encodeURIComponent(searching ? "" : state.feedDate || "");
    const watch = state.watchOnly ? 1 : 0;
    const followed = state.followedOnly ? 1 : 0;
    const section = searching ? "" : "today";
    const data = await api(`/api/rumors?q=${q}&tier=${tier}&date=${date}&watch=${watch}&followed=${followed}&section=${section}&offset=${state.feedOffset}&limit=24`);
    state.rightsEnvelope = data.rights_envelope || state.rightsEnvelope;
    grid.insertAdjacentHTML("beforeend", data.items.map(renderCard).join(""));
    state.feedOffset += data.items.length;
    state.feedHasMore = data.has_more;
    if (sentinel) {
      if (grid.children.length === 0) sentinel.textContent = searching ? "暂无搜索结果" : "暂无情报";
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
  renderRecentBacktestShowcase(data.recent_backtest_showcase || {});
}

function renderValueProof(items) {
  // panel removed
}


async function loadCommunityRooms() {
  // panel removed
}


async function loadActivityFeed() {
  // panel removed
}


async function loadExchangeDesk() {
  // panel removed
}


async function loadModerationSummary() {
  // panel removed
}


async function loadReferralCenter() {
  // panel removed
}


async function loadActivationCenter() {
  // panel removed
}


function renderExchangePanel() {
  // panel removed
}


function renderTrustCenterPanel() {
  // panel removed
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
  // panel removed
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
  // panel removed
}


function renderDailyWorkflow(workflow) {
  // panel removed
}


function renderOpportunityDeck(opportunity) {
  // panel removed
}


function renderBountyBoard(board) {
  // panel removed
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
  // panel removed
}


function renderTopicRadar(radar) {
  // panel removed
}


function renderCommunityRooms() {
  // panel removed
}


async function loadValueFramework() {
  // panel removed
}


async function loadGrowthCenter() {
  // panel removed
}


async function loadSourceUpgradeCenter() {
  const data = await api("/api/source-upgrade-center");
  state.sourceUpgrade = data;
  state.user = data.user || state.user;
  renderProfile();
  renderSourceUpgradePanel();
}

async function loadWatchlist() {
  if (!isLoggedIn()) {
    state.watchlist = null;
    if (state.view === "watch") await loadWatchPage();
    return;
  }
  if (state.view === "watch") await loadWatchPage();
}


async function loadProviderFollows() {
  // panel removed
}


function renderProviderFollowPanel() {
  // panel removed
}


function renderWatchPanel() {
  // panel removed
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

async function loadWatchPage() {
  const root = $("#watchPage");
  if (!root) return;
  if (!isLoggedIn()) {
    state.watchlist = null;
    root.innerHTML = `
      <section class="watch-login">
        <h3>登录后启用自选股</h3>
        <p>自选股会保存到账号，并汇总该股票的所有历史情报。</p>
        <button type="button" id="watchLoginBtn">登录/注册</button>
      </section>
    `;
    $("#watchLoginBtn")?.addEventListener("click", () => promptLogin("登录后才能查看自选股。"));
    return;
  }
  root.innerHTML = `<p class="empty">自选股加载中…</p>`;
  try {
    const data = await api("/api/watchlist/history");
    state.watchlist = data;
    renderWatchPage(data);
  } catch (err) {
    root.innerHTML = `<p class="empty">${esc(err.message || "自选股加载失败")}</p>`;
  }
}

function renderWatchPage(data) {
  const items = data.items || [];
  $("#watchPage").innerHTML = `
    <div class="watch-page-summary">
      <div><span>自选股</span><strong>${items.length}</strong></div>
      <div><span>历史情报</span><strong>${data.signal_total || 0}</strong></div>
    </div>
    ${items.map(renderWatchStock).join("") || `<p class="empty">暂无自选股。在首页信息流点击股票代码右侧的加号添加。</p>`}
  `;
  $$("#watchPage .watch-remove").forEach((btn) => btn.addEventListener("click", () => removeWatch(btn.dataset.code)));
  $$("#watchPage .watch-signal").forEach((btn) => btn.addEventListener("click", () => openRumor(btn.dataset.id).catch((err) => alert(err.message || "详情加载失败"))));
}

function renderWatchStock(item) {
  const signals = item.signals || [];
  return `
    <section class="watch-stock">
      <header>
        <div>
          <h3>${esc(item.name || item.code)} <span>${esc(item.code || "")}</span></h3>
          <p>${signals.length} 条历史情报${item.latest_date ? ` · 最新 ${esc(item.latest_date)}` : ""}</p>
        </div>
        <button type="button" class="ghost watch-remove" data-code="${esc(item.code || "")}">移除</button>
      </header>
      <div class="watch-signal-list">
        ${signals.map(renderWatchSignal).join("") || `<p class="empty">暂无历史情报。</p>`}
      </div>
    </section>
  `;
}

function renderWatchSignal(item) {
  const codes = (item.stock_codes || []).map((stock) => stock.code).filter(Boolean).join(" / ");
  return `
    <button type="button" class="watch-signal" data-id="${item.id}">
      <span class="badge">${esc(item.ai_tier || "")}</span>
      <div>
        <strong>${esc(item.target || "情报")}${codes ? `<em>${esc(codes)}</em>` : ""}</strong>
        <p>${esc(item.logic || "")}</p>
        <small>${esc(item.recommendation_date || "")} · ${item.unlocked ? "可读" : "锁定"}</small>
      </div>
      <b>${Number(item.ai_score || 0)}</b>
    </button>
  `;
}

async function addWatch(item) {
  if (!isLoggedIn()) {
    promptLogin("登录后才能添加自选股。");
    return;
  }
  try {
    await api("/api/watchlist", { method: "POST", body: JSON.stringify(item) });
    if (state.view === "watch") await loadWatchPage();
    await loadCommunityInsight();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

async function toggleWatchFromButton(btn) {
  const code = btn.dataset.code || "";
  const name = btn.dataset.name || code;
  if (btn.dataset.watchAction === "remove") {
    await removeWatch(code);
    return;
  }
  await addWatch({ code, name });
}

async function removeWatch(code) {
  if (!isLoggedIn()) {
    promptLogin("登录后才能管理自选股。");
    return;
  }
  try {
    await api(`/api/watchlist/${encodeURIComponent(code)}`, { method: "DELETE" });
    if (state.view === "watch") await loadWatchPage();
    await loadCommunityInsight();
    await loadRumors(true);
  } catch (err) {
    alert(err.message);
  }
}

function renderWatchToggle(stock, watched, extraClass = "") {
  if (!stock?.code) return "";
  const active = !!watched;
  return `
    <button type="button" class="watch-add-btn ${active ? "active-filter" : ""} ${esc(extraClass)}" data-watch-action="${active ? "remove" : "add"}" data-code="${esc(stock.code)}" data-name="${esc(stock.name || stock.code)}" title="${active ? "移出自选股" : "加入自选股"}">
      ${active ? "-" : "+"}
    </button>
  `;
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
  // panel removed
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
  // panel removed
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
  // panel removed
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
  const items = (showcase.items || []).slice(0, 10);
  root.innerHTML = `
    ${(showcase.dates || []).length ? `<div class="showcase-dates">${(showcase.dates || []).map(esc).join(" / ")}</div>` : ""}
    ${items.map((entry) => {
      const item = entry.rumor || {};
      const ret = entry.ret_t1_1;
      const retStr = ret != null ? `${ret >= 0 ? "+" : ""}${(ret * 100).toFixed(1)}%` : "-";
      const retClass = ret != null ? (ret >= 0 ? "pos" : "neg") : "";
      const codes = item.unlocked ? (item.stock_codes || []).map((stock) => stock.code).filter(Boolean).join(" / ") : "";
      const firstStock = item.unlocked ? (item.stock_codes || []).find((stock) => stock.code) : null;
      const watchAdd = renderWatchToggle(firstStock, item.watched, "showcase-watch-add");
      return `
        <article class="showcase-item" data-id="${item.id}">
          <span class="badge">${esc(item.ai_tier || "")}</span>
          <div class="showcase-item-body">
            <strong>${esc(item.target || "历史信号")}${codes ? `<em class="stock-code">${esc(codes)}</em>` : ""}${watchAdd}</strong>
            <span>${esc(item.logic || "")}</span>
          </div>
          <span class="showcase-ret ${retClass}">${retStr}</span>
          <span class="showcase-date">${esc(item.recommendation_date || "")}</span>
        </article>
      `;
    }).join("") || `<p class="empty">暂无近3个交易日回测样本。</p>`}
  `;
  $$("#recentBacktestShowcase .showcase-item").forEach((item) => item.addEventListener("click", () => openRumor(item.dataset.id).catch((err) => alert(err.message || "详情加载失败"))));
  $$("#recentBacktestShowcase .showcase-watch-add").forEach((btn) => btn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    toggleWatchFromButton(btn);
  }));
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
        <p>${esc(board.summary || "普通信号用于看方向，高价值线索用于重点核验。")}</p>
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
        <span class="badge">${esc(item.ai_tier)}</span>
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
    ? (bountyState === "locked" ? "解锁后反馈" : topBounty.action || topBounty.label || "参与反馈")
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
        <span>反馈任务</span>
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
  const next = steps[0] || "进入详情反馈";
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
  const rights = item.rights || {};
  const outcome = item.outcome || {};
  const hasPending = !outcome.state || outcome.state === "pending" || outcome.state === "skipped" || outcome.state === "no_code";
  const ret1 = outcome.avg_ret != null ? outcome.avg_ret : null;
  const retLabel = ret1 != null
    ? `<span class="card-ret ${ret1 >= 0 ? "pos" : "neg"}">${ret1 >= 0 ? "+" : ""}${(ret1 * 100).toFixed(1)}%</span>`
    : "";
  const actionBtn = item.unlocked
    ? `<button class="open-btn card-row-btn" data-id="${item.id}">详情</button>`
    : `<button class="unlock-btn card-row-btn ghost" data-id="${item.id}">解锁</button>`;
  const codes = item.unlocked ? (item.stock_codes || []).map((stock) => stock.code).filter(Boolean) : [];
  const firstStock = item.unlocked ? (item.stock_codes || []).find((stock) => stock.code) : null;
  const codeLabel = codes.length ? codes.join(" / ") : "";
  const watchAdd = renderWatchToggle(firstStock, item.watched);
  return `
    <article class="card ${locked}" data-rights-fp="${esc(rights.fingerprint || "")}" data-rights-scope="${esc(rights.scope || "rumor-content")}">
      <span class="rights-mark" aria-hidden="true">${esc(rights.mark || "")}:${esc(rights.fingerprint || "")}</span>
      <div class="card-head">
        <div class="badge">${esc(item.ai_tier)}</div>
        <div class="target">${esc(item.target)}</div>
        ${codeLabel ? `<span class="stock-code">${esc(codeLabel)}</span>` : ""}
        ${watchAdd}
        ${retLabel}
      </div>
      <p class="logic">${esc(item.logic)}</p>
      <div class="card-row-meta">
        <span>${esc(item.recommendation_date)}</span>
        ${!hasPending && outcome.label ? `<span class="chip outcome-chip ${esc(outcome.state || "")}">${esc(outcome.label)}</span>` : ""}
      </div>
      ${actionBtn}
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
  const dialog = $("#rumorDialog");
  if (!dialog.open) dialog.showModal();
  $("#detailTarget").textContent = "加载中...";
  $("#detailMeta").innerHTML = "";
  $("#detailBacktest").innerHTML = "";
  $("#detailLogic").textContent = "";
  $("#detailWatchTargets").innerHTML = "";
  $("#detailPoints").innerHTML = "";
  try {
    const data = await api(`/api/rumors/${id}`);
    const bt = data.backtest;
    const item = data.item;
    const rights = item.rights || {};
    const codes = (item.stock_codes || []).map((stock) => stock.code).filter(Boolean).join(" / ");
    state.activeRumorId = item.id;
    $("#detailRightsMark").textContent = rights.mark || "";
    $("#detailTier").textContent = item.ai_tier || "";
    $("#detailTarget").textContent = item.target;
    $("#detailMeta").innerHTML = `
      <span>${esc(item.recommendation_date)}</span>
      ${codes ? `<span>${esc(codes)}</span>` : ""}
      ${item.institution ? `<span>${esc(item.institution)}</span>` : ""}
      <span>${esc(item.submitter_name || "社区信息源")}</span>
    `;
    $("#detailBacktest").innerHTML = bt && bt.ret_t1_1 != null ? `
      <span class="outcome-pill ${esc(bt.outcome?.state || "pending")}">${esc(bt.outcome?.label || "待验证")}</span>
      <span>T+1持1日 <strong class="${bt.ret_t1_1 >= 0 ? "pos" : "neg"}">${pct(bt.ret_t1_1)}</strong></span>
      ${bt.ret_t1_5 != null ? `<span>T+1持5日 <strong>${pct(bt.ret_t1_5)}</strong></span>` : ""}
      ${bt.ret_t1_20 != null ? `<span>T+1持20日 <strong>${pct(bt.ret_t1_20)}</strong></span>` : ""}
      <small>${esc(bt.outcome?.summary || "")}</small>
    ` : "";
    $("#detailLogic").textContent = item.logic || "";
    renderWatchTargets(item.stock_codes || [], item.watched);
    $("#detailPoints").innerHTML = (item.key_points || []).map((p) => `<span>${esc(p)}</span>`).join("");
    renderDetailDiscussion(item.discussion || {}, data.comments || []);
  } catch (err) {
    $("#detailTarget").textContent = "详情加载失败";
    $("#detailLogic").textContent = err.message || "请求失败";
  }
}

function renderDecisionBrief(brief) {
  const root = $("#detailDecisionBrief");
  if (!root) return;
  root.innerHTML = `
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
      <div><strong>待验证</strong>${(brief.watch_points || []).map((item) => `<span>${esc(item)}</span>`).join("") || `<span>等待社区反馈</span>`}</div>
      <div><strong>风险</strong>${(brief.risks || []).map((item) => `<span>${esc(item)}</span>`).join("") || `<span>仍需独立复核</span>`}</div>
    </div>
    <footer>
      <strong>${esc(brief.next_action || "继续观察")}</strong>
      <small>${esc(brief.disclaimer || "不构成投资建议。")}</small>
    </footer>
  `;
}

function renderConsensusSnapshot(snapshot) {
  const root = $("#detailConsensus");
  if (!root) return;
  const metrics = snapshot.metrics || {};
  const peers = snapshot.top_peers || [];
  root.innerHTML = `
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
    <footer>${esc(snapshot.next_action || "等待社区进一步反馈。")}</footer>
  `;
  $$("#detailConsensus .consensus-peer").forEach((btn) => btn.addEventListener("click", () => openRumor(btn.dataset.id)));
}

function renderVerificationLedger(items) {
  const root = $("#detailLedger");
  if (!root) return;
  root.innerHTML = `
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
  const root = $("#detailTasks");
  if (!root) return;
  root.innerHTML = `
    <div class="block-head">
      <div>
        <span class="eyebrow">FEEDBACK TASKS</span>
        <h3>社区反馈任务</h3>
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
    if (action === "有用") reactToActiveRumor("useful");
    else if (action === "存疑") reactToActiveRumor("doubt");
    else if (action === "解锁") return;
    $("#commentForm input[name='content']")?.focus();
  }));
  $$("#detailTasks .bounty-action").forEach((btn) => btn.addEventListener("click", () => {
    const action = btn.dataset.action || "讨论";
    if (action === "有用") reactToActiveRumor("useful");
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
            <span>${esc(item.label || "反馈任务")}</span>
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
    <button type="button" class="ghost detail-watch-btn ${watched ? "active-filter" : ""}" data-watch-action="${watched ? "remove" : "add"}" data-code="${esc(item.code)}" data-name="${esc(item.name || item.code)}">
      ${watched ? "移出自选" : "关注"} ${esc(item.name || item.code)}
    </button>
  `).join("");
}

function renderDetailDiscussion(discussion, comments) {
  const reward = discussion.participation_reward;
  $("#detailReactions").innerHTML = [
    ["useful", "有用", discussion.useful || 0],
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
        <strong>${esc(reward.message || "反馈贡献已记录")}</strong>
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
  const root = $("#detailModeration");
  if (!root) return;
  const reports = Number(moderation.reports || 0);
  const myReports = moderation.my_reports || [];
  const labels = moderation.report_labels || [];
  root.innerHTML = `
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
    const currentLogic = String(form.elements.logic.value || "").trim();
    if (!currentLogic || currentLogic.length < 12 || currentLogic === payload.raw_content?.trim().slice(0, currentLogic.length)) {
      form.elements.logic.value = data.logic || currentLogic;
    }
    form.elements.institution.value = form.elements.institution.value || data.institution || "";
    form.elements.recommender.value = form.elements.recommender.value || data.recommender || "";
    result.classList.add("show");
    result.innerHTML = `
      <h3>${data.summary_source === "llm" ? "AI 已提炼" : "规则已提炼"}</h3>
      ${data.logic ? `<p><strong>核心逻辑：</strong>${esc(data.logic)}</p>` : ""}
      <p>${(data.key_points || []).join("；")}</p>
      ${data.summary_warning ? `<p class="muted">${esc(data.summary_warning)}</p>` : ""}
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
        <div><strong>${esc(item.target)}</strong><span class="badge">${esc(item.ai_tier)}</span></div>
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
      <p>${esc(step === "reg" ? registeredCopy : activation.headline || "你的贡献、反馈和邀请都会进入成长账本。")}</p>
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

function jumpToView(view) {
  return switchView(view);
}

// ── 事件绑定 ───────────────────────────────────────────────
function wire() {
  $$(".nav button").forEach((b) => b.addEventListener("click", () => jumpToView(b.dataset.view)));
  $$(".back-feed-btn").forEach((b) => b.addEventListener("click", () => jumpToView("feed")));
  document.body.addEventListener("click", async (e) => {
    const jump = e.target.closest("[data-view-jump]");
    if (jump) {
      jumpToView(jump.dataset.viewJump);
      return;
    }
    const invite = e.target.closest(".copy-invite, .copy-invite-copy, .invite-momentum-action");
    const provider = e.target.closest(".provider-snapshot.provider-open");
    if (provider) return openProviderProfile(provider.dataset.id);
    if (!invite || invite.disabled) return;
    if (invite.dataset.key === "review_rank") {
      jumpToView("rank");
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
    if (open) return openRumor(open.dataset.id).catch((err) => alert(err.message || "详情加载失败"));
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
  $("#searchInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runSearch();
    }
  });
  $("#searchBtn").addEventListener("click", runSearch);
  $("#clearSearchBtn").addEventListener("click", clearSearch);
  $("#tierFilter").addEventListener("change", () => { state.selectedTier = $("#tierFilter").value; runSearch(); });
  $("#refreshFeed").addEventListener("click", async () => { await loadCommunityInsight(); await loadDailyStats(); });
  $("#refreshWatch")?.addEventListener("click", loadWatchPage);
  $("#rumorGrid").addEventListener("click", (e) => {
    const btn = e.target.closest(".watch-add-btn");
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      toggleWatchFromButton(btn);
    }
  });
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
  $("#detailWatchTargets")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".detail-watch-btn");
    if (btn) toggleWatchFromButton(btn);
  });
  $("#detailModeration")?.addEventListener("click", (e) => {
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
  // 并行加载社区数据和情报流
  await Promise.all([loadCommunityInsight(), loadDailyStats()]);
});
