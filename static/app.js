const state = { user: null, level: null, view: "feed", feedDate: "", selectedTier: "", feedOffset: 0, feedHasMore: true, feedLoading: false };

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

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
  $("#profile").innerHTML = `
    <h3>${u.display_name || u.username}</h3>
    <p>${u.is_guest ? "默认游客会话" : "已登录账号"}</p>
    <div class="metric"><span>等级</span><strong>${state.level.name}</strong></div>
    <div class="metric"><span>经验值</span><strong>${u.xp}</strong></div>
    <div class="metric"><span>贡献度</span><strong>${Number(u.contribution || 0).toFixed(1)}</strong></div>
    <div class="metric"><span>信誉分</span><strong>${Number(u.reputation).toFixed(1)}</strong></div>
    <div class="metric"><span>直看额度</span><strong>${u.direct_quota}</strong></div>
    ${renderRadar(u.radar)}
  `;
}

function switchView(view) {
  state.view = view;
  $$(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#${view}View`).classList.add("active");
  if (view === "backtest") loadBacktests();
  if (view === "rank") loadLeaderboard();
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
    const data = await api(`/api/rumors?q=${q}&tier=${tier}&date=${date}&offset=${state.feedOffset}&limit=24`);
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

async function selectTier(tier, allowed) {
  state.selectedTier = tier;
  $("#tierFilter").value = tier;
  $("#tierPanel").innerHTML = `<strong>${tier}级情报</strong><span>${allowed ? "已开放，下方展示该等级明细。" : "未开放，仅展示脱敏信息，可通过提升等级或贡献度解锁。"}</span>`;
  await loadRumors();
}

function renderCard(item) {
  const locked = item.hidden ? "locked" : "";
  const reasons = item.ai_reasons.map((r) => `<span class="chip">${r}</span>`).join("");
  return `
    <article class="card ${locked}">
      <div class="card-head">
        <div class="target">${item.target}</div>
        <div class="badge">${item.ai_tier}${item.ai_score}</div>
      </div>
      <p class="logic">${item.logic}</p>
      <div class="meta">
        <span class="chip">${item.recommendation_date}</span>
        <span class="chip">${item.source === "reference" ? "历史样本" : "社区分享"}</span>
        ${reasons}
      </div>
      <div class="card-actions">
        ${item.unlocked ? `<button class="open-btn" data-id="${item.id}">查看详情</button>` : `<button class="unlock-btn" data-id="${item.id}">使用额度直看</button>`}
      </div>
    </article>
  `;
}

async function openRumor(id) {
  const data = await api(`/api/rumors/${id}`);
  const bt = data.backtest;
  const msg = [
    data.item.raw_content,
    "",
    bt ? `回测：T+1持1日 ${strip(pct(bt.ret_t1_1))} / 持5日 ${strip(pct(bt.ret_t1_5))} / 持20日 ${strip(pct(bt.ret_t1_20))} / 信号价值 ${bt.signal_value != null ? bt.signal_value.toFixed(1) : "-"}` : "回测：暂无",
  ].join("\n");
  alert(msg);
}

function strip(html) { return html.replace(/<[^>]*>/g, ""); }

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
    result.classList.add("show");
    result.innerHTML = `
      <h3>评分 ${data.score.tier}${data.score.score}</h3>
      <p>${data.score.reasons.join("、")}</p>
      <p>${(data.summary.key_points || []).join("；")}</p>
      <p>${data.unlocked ? `已解锁：${data.unlocked.target}` : "暂无可匹配解锁消息"}</p>
    `;
    event.target.reset();
    setDefaultRecommendationDate();
    renderStockRows([]);
    await loadMe();
    await loadRumors();
  } catch (err) {
    result.classList.add("show");
    result.innerHTML = `<p class="neg">${err.message}</p>`;
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
      <td>${r.status || "pending"}</td>
    </tr>
  `).join("");
}

async function loadLeaderboard() {
  const data = await api("/api/leaderboard");
  $("#leaderboard").innerHTML = data.items.map((u, i) => `
    <div class="rank-row">
      <strong>#${i + 1}</strong>
      <div><strong>${u.display_name}</strong><p>${u.level}</p></div>
      <span>${u.xp} XP</span>
      <span>${Number(u.reputation).toFixed(1)} 分</span>
      <span>${u.direct_quota} 额度</span>
      <div>${renderRadar(u.radar)}</div>
    </div>
  `).join("");
}

// ── 登录/注册弹窗 ──────────────────────────────────────────
let regEmail = "";

function setRegMode(step) {
  const isLogin = step === "login";
  const isReg   = step === "reg";
  $("#authTitle").textContent = isLogin ? "登录" : "注册";
  $("#loginForm").style.display    = isLogin ? "" : "none";
  $("#regForm").style.display      = isReg   ? "" : "none";
  $("#loginBtn").style.display     = isLogin ? "" : "none";
  $("#toRegBtn").style.display     = isLogin ? "" : "none";
  $("#doRegisterBtn").style.display = isReg  ? "" : "none";
  $("#regBackBtn").style.display   = isReg   ? "" : "none";
  $("#authMsg").textContent = "";
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
    renderProfile(); loadRumors();
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
  if (!username)            { $("#authMsg").textContent = "请填写用户名"; return; }
  if (password.length < 6)  { $("#authMsg").textContent = "密码至少6位"; return; }
  if (password !== confirm)  { $("#authMsg").textContent = "两次密码不一致"; return; }
  if (!email)               { $("#authMsg").textContent = "请填写邮箱"; return; }
  if (code.length !== 6)    { $("#authMsg").textContent = "请填写6位验证码"; return; }
  try {
    const data = await api("/api/register", { method: "POST", body: JSON.stringify({ username, password, email, code }) });
    state.user = data.user; state.level = data.level;
    $("#authDialog").close(); setRegMode("login");
    renderProfile(); loadRumors();
  } catch (err) { $("#authMsg").textContent = err.message; }
}

// ── 事件绑定 ───────────────────────────────────────────────
function wire() {
  $$(".nav button").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
  $("#rumorGrid").addEventListener("click", (e) => {
    const open = e.target.closest(".open-btn");
    if (open) return openRumor(open.dataset.id);
    const unlock = e.target.closest(".unlock-btn");
    if (unlock) return unlockRumor(unlock.dataset.id);
  });
  const sentinel = $("#feedSentinel");
  if (sentinel && "IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) loadRumors(false);
    }, { rootMargin: "200px" }).observe(sentinel);
  }
  $("#searchInput").addEventListener("input", debounce(() => loadRumors(true), 250));
  $("#tierFilter").addEventListener("change", () => { state.selectedTier = $("#tierFilter").value; loadRumors(true); });
  $("#refreshFeed").addEventListener("click", loadDailyStats);
  $("#submitForm").addEventListener("submit", submitRumor);
  $("#summarizeBtn").addEventListener("click", summarizeRumor);
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
    await loadMe(); await loadRumors();
  });
}

function debounce(fn, wait) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
}

wire();
setDefaultRecommendationDate();
renderStockRows([]);
loadMe().then(loadDailyStats);
