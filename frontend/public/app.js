/* Юридический ассистент — фронтенд без сборки.
 * Запросы к веб-API используют префикс /api/v1.
 */
(function () {
  "use strict";

  const CFG = Object.assign({
    apiBase: "/api", mock: "auto", requestTimeoutMs: 120000,
    auth: true, demoLogin: "demo", demoPassword: "demo",
  }, window.APP_CONFIG || {});
  const apiUrl = (path) => CFG.apiBase.replace(/\/$/, "") + "/v1" + path;
  const SESSION_KEY = "psb-legal-session-v1";
  const DIALOGS_KEY_PREFIX = "psb-legal-dialogs-v2:";
  const MAX_DIALOGS = 100;
  const STATUS_LABELS = {
    confirmed: "Подтверждено базой знаний",
    partial: "Подтверждено частично",
    not_found: "Нет данных в базе знаний",
  };
  const EXAMPLES = [
    "Какой срок оплаты по договору поставки П-17/2024?",
    "Куда доставлять товар по договору с «Альфа-Трейд», если Спецификация подписана 15.10.2024?",
    "С кем нужно согласовывать договоры по регламенту?",
  ];
  // Категории документов для фильтра — определяются по типу и названию источника.
  const DOC_CATEGORIES = [
    { id: "contract", label: "Договоры", test: (t) => /договор/.test(t) && !/доп/.test(t) },
    { id: "supp", label: "Доп. соглашения", test: (t) => /доп\.?\s*соглаш|дополнительн/.test(t) },
    { id: "internal", label: "Регламенты", test: (t) => /регламент|положени|политик|приказ|инструкц/.test(t) },
    { id: "letters", label: "Письма и записки", test: (t) => /письм|записк/.test(t) },
    { id: "law", label: "Законы и кодексы", test: (t) => /закон|кодекс|коап|нк рф|тк рф|\bфз\b|федеральн/.test(t) },
  ];
  const PERIODS = [
    { id: "all", label: "Все", days: null },
    { id: "today", label: "Сегодня", days: 0 },
    { id: "week", label: "7 дней", days: 7 },
    { id: "month", label: "30 дней", days: 30 },
  ];

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const el = {
    app: $("app"),
    authScreen: $("authScreen"), authForm: $("authForm"), authLogin: $("authLogin"), authPassword: $("authPassword"),
    authRemember: $("authRemember"), authSubmit: $("authSubmit"), authError: $("authError"), authHint: $("authHint"),
    togglePassword: $("togglePassword"),
    list: $("dialogList"), btnNew: $("btnNew"), listTitle: $("listTitle"),
    search: $("chatSearch"), btnFilter: $("btnFilter"), filterCount: $("filterCount"), filterPanel: $("filterPanel"),
    periodChips: $("periodChips"), docChips: $("docChips"), favChip: $("favChip"), btnResetFilters: $("btnResetFilters"),
    userName: $("userName"), userRole: $("userRole"), userAvatar: $("userAvatar"), btnLogout: $("btnLogout"),
    messages: $("messages"), composer: $("composer"), input: $("input"), btnSend: $("btnSend"), modeBadge: $("modeBadge"),
    srcListView: $("sourcesListView"), srcList: $("sourcesList"),
    docView: $("docView"), docTitle: $("docTitle"), docMeta: $("docMeta"), docBody: $("docBody"), btnBack: $("btnBack"),
    toast: $("toast"),
  };

  // ---------- Состояние ----------
  const state = {
    session: null,         // { token, user: { login, name, role } }
    dialogs: [],
    currentId: null,
    selectedMsg: null,     // индекс выбранного ответа бота в текущем диалоге
    activeSource: null,    // индекс открытого источника
    pending: false,
    mode: "backend",       // "backend" | "mock"
    docCache: {},
    query: "",
    filters: { period: "all", docs: new Set(), favorites: false },
  };

  // ---------- Сессия ----------
  function loadSession() {
    for (const store of [sessionStorage, localStorage]) {
      try {
        const raw = store.getItem(SESSION_KEY);
        if (raw) { const s = JSON.parse(raw); if (s && s.user) return s; }
      } catch (e) { /* хранилище недоступно */ }
    }
    return null;
  }
  function saveSession(s, remember) {
    try {
      sessionStorage.removeItem(SESSION_KEY); localStorage.removeItem(SESSION_KEY);
      (remember ? localStorage : sessionStorage).setItem(SESSION_KEY, JSON.stringify(s));
    } catch (e) { /* сессия живёт до перезагрузки */ }
  }
  function clearSession() {
    try { sessionStorage.removeItem(SESSION_KEY); localStorage.removeItem(SESSION_KEY); } catch (e) { /* ignore */ }
  }

  // ---------- Хранилище диалогов (отдельно для каждого пользователя) ----------
  const dialogsKey = () => DIALOGS_KEY_PREFIX + (state.session ? state.session.user.login : "guest");
  function loadDialogs() {
    try {
      const raw = localStorage.getItem(dialogsKey());
      const data = raw ? JSON.parse(raw) : [];
      return Array.isArray(data) ? data : [];
    } catch (e) { return []; }
  }
  async function loadBackendDialogs() {
    const r = await api("/conversations", {}, 15000);
    if (!r.ok) throw await responseError(r);
    const conversations = await r.json();
    const stored = new Map(loadDialogs().map((d) => [d.serverId || d.id, d]));
    return Promise.all(conversations.map(async (conversation) => {
      const messagesResponse = await api(
        "/conversations/" + encodeURIComponent(conversation.id) + "/messages?limit=500",
        {},
        15000,
      );
      if (!messagesResponse.ok) throw await responseError(messagesResponse);
      const messages = (await messagesResponse.json()).map((message) => {
        if (message.role !== "assistant" || !message.answer) {
          return { role: message.role, content: message.content };
        }
        const answer = normalizeAnswer(message.answer);
        return {
          role: "assistant",
          content: answer.answer || message.content,
          answerId: answer.answerId,
          sources: answer.sources,
          status: answer.status,
        };
      });
      const previous = stored.get(conversation.id);
      return {
        id: conversation.id,
        serverId: conversation.id,
        title: conversation.title,
        messages,
        updatedAt: Date.parse(conversation.updated_at) || Date.now(),
        favorite: previous ? previous.favorite : false,
      };
    }));
  }
  function saveDialogs() {
    try {
      state.dialogs.sort((a, b) => b.updatedAt - a.updatedAt);
      localStorage.setItem(dialogsKey(), JSON.stringify(state.dialogs.filter((d) => d.messages.length).slice(0, MAX_DIALOGS)));
    } catch (e) { /* хранилище недоступно — история живёт до перезагрузки */ }
  }
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  const current = () => state.dialogs.find((d) => d.id === state.currentId) || null;

  // ---------- API ----------
  async function fetchWithTimeout(url, opts, ms) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), ms);
    try { return await fetch(url, Object.assign({}, opts, { signal: ctrl.signal })); }
    finally { clearTimeout(t); }
  }

  // Запрос к API с токеном. 401 — сессия истекла: возвращаемся на экран входа.
  async function api(path, opts, ms) {
    opts = Object.assign({}, opts);
    opts.headers = Object.assign({}, opts.headers);
    if (state.session && state.session.token) opts.headers.Authorization = "Bearer " + state.session.token;
    const r = await fetchWithTimeout(apiUrl(path), opts, ms);
    if (r.status === 401 && CFG.auth) {
      logout("Сессия истекла. Войдите снова.");
      const err = new Error("требуется вход"); err.silent = true; throw err;
    }
    return r;
  }

  async function responseError(r) {
    let data = null;
    try { data = await r.clone().json(); } catch (e) { /* response is not JSON */ }
    return new Error(data && (data.message || data.detail) || "сервер ответил " + r.status);
  }

  async function detectMode() {
    if (CFG.mock === true) return "mock";
    if (CFG.mock === false) return "backend";
    try {
      const r = await fetchWithTimeout(apiUrl("/auth/me"), {}, 3000);
      return r.ok || r.status === 401 || r.status === 403 ? "backend" : "mock";
    } catch (e) { return "mock"; }
  }

  async function loginRequest(login, password) {
    if (state.mode === "mock") {
      await new Promise((res) => setTimeout(res, 400));
      if (login === CFG.demoLogin && password === CFG.demoPassword) {
        return { token: "demo-token", user: { login, name: "Демо-юрист", role: "Юридический отдел" } };
      }
      throw new Error("Неверный логин или пароль");
    }
    let r;
    try {
      r = await fetchWithTimeout(apiUrl("/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login, password }),
      }, 15000);
    } catch (e) { throw new Error("Сервер недоступен. Попробуйте позже."); }
    if (r.status === 401 || r.status === 403) throw new Error("Неверный логин или пароль");
    if (!r.ok) throw new Error("Не удалось войти: сервер ответил " + r.status);
    const data = await r.json();
    const user = Object.assign({ login, name: login, role: "" }, data.user || {});
    return { token: data.token || data.access_token || "", user };
  }

  function normalizeAnswer(r) {
    const answer = typeof r.answer === "string" ? r.answer :
      (r.blocks || []).map((block) => (block.segments || []).map((segment) => {
        const citations = (segment.citations || []).map((citation) => `[${citation.number}]`).join(" ");
        return segment.text + (citations ? " " + citations : "");
      }).join(" ")).filter(Boolean).join("\n\n") || r.content || r.text || "";
    let status = r.status || null;
    if (!status) {
      if (r.legal_status === "answered" && r.verification_status === "passed") status = "confirmed";
      else if (r.legal_status === "partial" || r.legal_status === "clarification_required" || r.verification_status === "partial") status = "partial";
      else if ((r.retrieval_audit?.passes || []).includes("conversation_history")) status = "confirmed";
      else if (r.legal_status === "insufficient_evidence" || r.legal_status === "out_of_scope") status = "not_found";
    }
    const sources = (r.sources || r.citations || []).map((s, i) => ({
      id: s.id ?? s.number ?? i + 1,
      answer_id: r.answer_id || null,
      answer_source_id: s.answer_source_id || null,
      doc_id: s.doc_id ?? s.document_id ?? null,
      type: s.type || "",
      title: s.title || s.document_title || s.document || "Документ",
      number: s.document_number || (s.answer_source_id ? "" : s.number) || "",
      date: s.date || "",
      version: s.version_label || "",
      pinpoint: s.pinpoint || s.section_number || s.clause || "",
      fragment: s.fragment || s.quote || "",
    }));
    return { answer: String(answer), answerId: r.answer_id || null, sources, status };
  }

  async function ensureBackendConversation(d) {
    if (d.serverId) return d.serverId;
    const r = await api("/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: d.title || "Новый диалог" }),
    }, 15000);
    if (!r.ok) throw await responseError(r);
    const conversation = await r.json();
    const oldId = d.id;
    d.id = conversation.id;
    d.serverId = conversation.id;
    d.updatedAt = Date.parse(conversation.updated_at) || Date.now();
    if (state.currentId === oldId) state.currentId = conversation.id;
    return conversation.id;
  }

  async function askBackend(history) {
    const d = current();
    if (!d) throw new Error("Сначала создайте диалог.");
    const conversationId = await ensureBackendConversation(d);
    const lastMessage = history[history.length - 1];
    const r = await api("/conversations/" + encodeURIComponent(conversationId) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: lastMessage.content }),
    }, CFG.requestTimeoutMs);
    if (!r.ok) throw await responseError(r);
    return normalizeAnswer(await r.json());
  }

  async function askMock(history) {
    await new Promise((res) => setTimeout(res, 700 + Math.random() * 600));
    const M = window.MOCK_DATA;
    const q = (history[history.length - 1].content || "").toLowerCase();
    const sc = M.scenarios.find((s) => new RegExp(s.match, "i").test(q)) || M.fallback;
    return normalizeAnswer(JSON.parse(JSON.stringify(sc)));
  }

  async function getDocument(source) {
    const cacheKey = state.mode === "mock" ? source.doc_id : source.answer_id + ":" + source.answer_source_id;
    if (state.docCache[cacheKey]) return state.docCache[cacheKey];
    let doc;
    if (state.mode === "mock") {
      doc = window.MOCK_DATA.documents[source.doc_id];
      if (!doc) throw new Error("документ не найден");
    } else {
      const r = await api(
        "/answers/" + encodeURIComponent(source.answer_id) + "/sources/" + encodeURIComponent(source.answer_source_id),
        {},
        15000,
      );
      if (!r.ok) throw await responseError(r);
      doc = await r.json();
      doc.text = (doc.before || "") + (doc.highlight || "") + (doc.after || "");
    }
    state.docCache[cacheKey] = doc;
    return doc;
  }

  // ---------- Форматирование ответа ----------
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function inline(text, nSources) {
    let h = esc(text);
    h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    h = h.replace(/\[(\d{1,2})\]/g, (m, n) => {
      const i = Number(n);
      return i >= 1 && i <= nSources ? `<button type="button" class="cite" data-src="${i - 1}" title="Открыть источник ${i}">${i}</button>` : m;
    });
    return h;
  }

  function renderMarkdown(text, nSources) {
    const lines = String(text).replace(/\r/g, "").split("\n");
    let html = "", i = 0;
    const isTable = (l) => /^\s*\|.*\|\s*$/.test(l);
    const listKind = (l) => (/^\s*[-*•]\s+/.test(l) ? "ul" : /^\s*\d+[.)]\s+/.test(l) ? "ol" : null);
    while (i < lines.length) {
      const l = lines[i];
      if (!l.trim()) { i++; continue; }
      if (isTable(l)) {
        const rows = [];
        while (i < lines.length && isTable(lines[i])) rows.push(lines[i++]);
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
        html += "<table>" + body.map((r, ri) => "<tr>" + cells(r).map((c) => (ri === 0 ? `<th>${inline(c, nSources)}</th>` : `<td>${inline(c, nSources)}</td>`)).join("") + "</tr>").join("") + "</table>";
        continue;
      }
      const kind = listKind(l);
      if (kind) {
        const items = [];
        while (i < lines.length && listKind(lines[i]) === kind) items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
        html += `<${kind}>` + items.map((it) => `<li>${inline(it, nSources)}</li>`).join("") + `</${kind}>`;
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !isTable(lines[i]) && !listKind(lines[i])) para.push(lines[i++]);
      html += "<p>" + para.map((p) => inline(p, nSources)).join("<br>") + "</p>";
    }
    return html;
  }

  const stripMd = (t) => String(t).replace(/\*\*(.+?)\*\*/g, "$1").replace(/\[(\d{1,2})\]/g, "");
  function plainWithSources(msg) {
    const text = msg.content.replace(/\*\*(.+?)\*\*/g, "$1");
    if (!msg.sources || !msg.sources.length) return text;
    const src = msg.sources.map((s, i) => `[${i + 1}] ${sourceLine(s)}`).join("\n");
    return text + "\n\nИсточники:\n" + src;
  }
  function sourceLine(s) {
    const version = s.version ? "ред. " + s.version : s.date && "от " + s.date;
    return [s.title, s.number && "№ " + s.number, version, s.pinpoint].filter(Boolean).join(", ");
  }

  // ---------- Поиск и фильтры ----------
  const norm = (s) => String(s || "").toLowerCase().replace(/ё/g, "е");

  function docCategoriesOf(d) {
    const cats = new Set();
    d.messages.forEach((m) => (m.sources || []).forEach((s) => {
      const t = norm(s.type + " " + s.title);
      DOC_CATEGORIES.forEach((c) => { if (c.test(t)) cats.add(c.id); });
    }));
    return cats;
  }

  function inPeriod(d) {
    const p = PERIODS.find((x) => x.id === state.filters.period);
    if (!p || p.days === null) return true;
    const start = new Date(); start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - p.days);
    return d.updatedAt >= start.getTime();
  }

  // Возвращает null, если диалог не подходит; иначе — { snippet } (сниппет, если совпадение не в названии).
  function matchDialog(d) {
    if (state.filters.favorites && !d.favorite) return null;
    if (!inPeriod(d)) return null;
    if (state.filters.docs.size) {
      const cats = docCategoriesOf(d);
      if (![...state.filters.docs].some((c) => cats.has(c))) return null;
    }
    const q = norm(state.query.trim());
    if (!q) return { snippet: null };
    if (norm(d.title).includes(q)) return { snippet: null };
    for (const m of d.messages) {
      if (m.error) continue;
      const text = stripMd(m.content).replace(/\s+/g, " ");
      const pos = norm(text).indexOf(q);
      if (pos >= 0) {
        const from = Math.max(0, pos - 25);
        return { snippet: (from > 0 ? "…" : "") + text.slice(from, pos + q.length + 45) + (pos + q.length + 45 < text.length ? "…" : "") };
      }
    }
    return null;
  }

  function highlight(text) {
    const q = state.query.trim();
    if (!q) return esc(text);
    const n = norm(text), nq = norm(q);
    let out = "", i = 0, pos;
    while ((pos = n.indexOf(nq, i)) >= 0) {
      out += esc(text.slice(i, pos)) + "<mark>" + esc(text.slice(pos, pos + nq.length)) + "</mark>";
      i = pos + nq.length;
    }
    return out + esc(text.slice(i));
  }

  const activeFilterCount = () =>
    (state.filters.period !== "all" ? 1 : 0) + state.filters.docs.size + (state.filters.favorites ? 1 : 0);

  function renderFilterPanel() {
    el.periodChips.innerHTML = PERIODS.map((p) =>
      `<button type="button" class="chip${state.filters.period === p.id ? " on" : ""}" data-period="${p.id}">${p.label}</button>`).join("");
    el.docChips.innerHTML = DOC_CATEGORIES.map((c) =>
      `<button type="button" class="chip${state.filters.docs.has(c.id) ? " on" : ""}" data-doc="${c.id}">${c.label}</button>`).join("");
    el.favChip.classList.toggle("on", state.filters.favorites);
    const n = activeFilterCount();
    el.filterCount.textContent = n;
    el.filterCount.hidden = n === 0;
    el.btnFilter.classList.toggle("on", n > 0);
  }

  // ---------- Рендер ----------
  function renderDialogList() {
    el.list.innerHTML = "";
    const searching = state.query.trim() || activeFilterCount();
    const rows = state.dialogs.filter((d) => d.messages.length)
      .map((d) => ({ d, m: matchDialog(d) })).filter((x) => x.m);
    el.listTitle.textContent = searching ? `Найдено: ${rows.length}` : "Недавние диалоги";
    if (!rows.length) {
      el.list.innerHTML = searching
        ? `<div class="list-empty">Ничего не найдено.<br><button type="button" class="link-light" id="resetAll">Сбросить поиск и фильтры</button></div>`
        : `<div class="list-empty">Здесь появятся ваши диалоги.</div>`;
      const r = $("resetAll");
      if (r) r.addEventListener("click", resetSearchAndFilters);
      return;
    }
    rows.forEach(({ d, m }) => {
      const item = document.createElement("div");
      item.className = "dialog-item" + (d.id === state.currentId ? " active" : "");
      item.setAttribute("role", "button");
      item.tabIndex = 0;
      item.title = d.title || "";
      item.innerHTML = `<div class="dialog-title">${highlight(d.title || "Без названия")}</div>` +
        (m.snippet ? `<div class="dialog-snippet">${highlight(m.snippet)}</div>` : "") +
        `<span class="dialog-actions">
           <button type="button" class="dialog-fav${d.favorite ? " on" : ""}" aria-label="${d.favorite ? "Убрать из избранного" : "В избранное"}" title="${d.favorite ? "Убрать из избранного" : "В избранное"}">${d.favorite ? "★" : "☆"}</button>
           <button type="button" class="dialog-del" aria-label="Удалить диалог" title="Удалить диалог">×</button>
         </span>`;
      item.addEventListener("click", () => openDialog(d.id));
      item.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDialog(d.id); } });
      item.querySelector(".dialog-fav").addEventListener("click", (e) => { e.stopPropagation(); toggleFavorite(d.id); });
      item.querySelector(".dialog-del").addEventListener("click", (e) => { e.stopPropagation(); deleteDialog(d.id); });
      el.list.appendChild(item);
    });
  }

  function renderMessages() {
    const d = current();
    el.messages.innerHTML = "";
    if (!d || !d.messages.length) {
      el.messages.innerHTML = `<div class="empty"><h2>Задайте вопрос по документам базы знаний</h2>
        <div>Ассистент отвечает только по договорам, внутренним документам и нормам из базы знаний и указывает источник для каждого вывода.</div>
        <div class="examples">${EXAMPLES.map((e) => `<button type="button" class="example">${esc(e)}</button>`).join("")}</div></div>`;
      el.messages.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
      return;
    }
    d.messages.forEach((m, idx) => {
      const div = document.createElement("div");
      if (m.role === "user") {
        div.className = "msg user";
        div.textContent = m.content;
      } else {
        div.className = "msg bot" + (m.error ? " error" : "") + (idx === state.selectedMsg ? " selected" : "");
        div.innerHTML = m.error ? `<p>${esc(m.content)}</p>` : renderMarkdown(m.content, (m.sources || []).length);
        const foot = document.createElement("div");
        foot.className = "msg-footer";
        if (m.error) {
          foot.innerHTML = `<button type="button" class="msg-action retry">Повторить запрос</button>`;
          foot.querySelector(".retry").addEventListener("click", (e) => { e.stopPropagation(); retry(); });
        } else {
          if (m.status && STATUS_LABELS[m.status]) foot.innerHTML += `<span class="status ${m.status}">${STATUS_LABELS[m.status]}</span>`;
          foot.innerHTML += `<button type="button" class="msg-action copy">Скопировать с источниками</button>`;
          foot.querySelector(".copy").addEventListener("click", (e) => { e.stopPropagation(); copyText(plainWithSources(m)); });
        }
        div.appendChild(foot);
        div.addEventListener("click", (e) => {
          const c = e.target.closest(".cite");
          selectMessage(idx);
          if (c) openSource(idx, Number(c.dataset.src));
        });
      }
      el.messages.appendChild(div);
    });
    const last = d.messages[d.messages.length - 1];
    if (!state.pending && last.role === "user") {
      // Ответ не был получен (например, сессия истекла во время запроса).
      const div = document.createElement("div");
      div.className = "msg bot error";
      div.innerHTML = `<p>Ответ на этот вопрос не был получен.</p><div class="msg-footer"><button type="button" class="msg-action retry">Повторить запрос</button></div>`;
      div.querySelector(".retry").addEventListener("click", (e) => { e.stopPropagation(); retry(); });
      el.messages.appendChild(div);
    }
    if (state.pending) {
      const t = document.createElement("div");
      t.className = "msg bot typing";
      t.innerHTML = `<span class="dots"><span></span><span></span><span></span></span> Ищу ответ в базе знаний…`;
      el.messages.appendChild(t);
    }
    const hit = markSearchHits();
    if (hit) hit.scrollIntoView({ block: "center" });
    else el.messages.scrollTop = el.messages.scrollHeight;
  }

  // Подсвечивает совпадения поиска в открытом диалоге; возвращает первое совпадение.
  function markSearchHits() {
    const q = norm(state.query.trim());
    if (!q) return null;
    let first = null;
    el.messages.querySelectorAll(".msg:not(.typing)").forEach((node) => {
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, {
        acceptNode: (t) => (t.parentElement.closest(".msg-footer, .cite") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
      });
      const texts = [];
      while (walker.nextNode()) texts.push(walker.currentNode);
      texts.forEach((t) => {
        const n = norm(t.nodeValue);
        if (!n.includes(q)) return;
        const frag = document.createDocumentFragment();
        let i = 0, pos;
        while ((pos = n.indexOf(q, i)) >= 0) {
          frag.appendChild(document.createTextNode(t.nodeValue.slice(i, pos)));
          const mk = document.createElement("mark");
          mk.className = "hit";
          mk.textContent = t.nodeValue.slice(pos, pos + q.length);
          frag.appendChild(mk);
          if (!first) first = mk;
          i = pos + q.length;
        }
        frag.appendChild(document.createTextNode(t.nodeValue.slice(i)));
        t.parentNode.replaceChild(frag, t);
      });
    });
    return first;
  }

  function renderSources() {
    showSourceList();
    const d = current();
    const m = d && state.selectedMsg != null ? d.messages[state.selectedMsg] : null;
    el.srcList.innerHTML = "";
    if (!m) {
      el.srcList.innerHTML = `<div class="sources-empty">Здесь появятся документы, на которые опирается ответ. Нажмите на карточку, чтобы открыть документ с выделенным фрагментом.</div>`;
      return;
    }
    if (!m.sources || !m.sources.length) {
      el.srcList.innerHTML = `<div class="sources-empty">Этот ответ не опирается на документы базы знаний.</div>`;
      return;
    }
    m.sources.forEach((s, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "src-card" + (i === state.activeSource ? " active" : "");
      const version = s.version ? "ред. " + s.version : s.date && "от " + s.date;
      b.innerHTML = `<span class="src-bar"></span><span>
        <span class="src-num">[${i + 1}]</span><br>
        ${esc(s.title)}${s.number ? `<br>№ ${esc(s.number)}` : ""}${version ? `<br>${esc(version)}` : ""}
        ${s.pinpoint ? `<div class="src-pin">${esc(s.pinpoint)}</div>` : ""}</span>`;
      b.addEventListener("click", () => openSource(state.selectedMsg, i));
      el.srcList.appendChild(b);
    });
  }

  function render() { renderDialogList(); renderMessages(); renderSources(); updateSendState(); }

  // ---------- Просмотр документа ----------
  function showSourceList() { el.docView.hidden = true; el.srcListView.hidden = false; el.srcListView.parentElement.scrollTop = 0; }

  function findFragment(text, fragment) {
    if (!fragment) return null;
    const exact = text.indexOf(fragment);
    if (exact >= 0) return [exact, exact + fragment.length];
    const words = fragment.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!words.length) return null;
    const m = new RegExp(words.join("\\s+"), "i").exec(text);
    return m ? [m.index, m.index + m[0].length] : null;
  }

  async function openSource(msgIdx, srcIdx) {
    const d = current();
    const s = d && d.messages[msgIdx] && d.messages[msgIdx].sources[srcIdx];
    if (!s) return;
    state.activeSource = srcIdx;
    el.srcListView.hidden = true;
    el.docView.hidden = false;
    el.docView.parentElement.scrollTop = 0;
    el.docTitle.textContent = s.title;
    const version = s.version ? "ред. " + s.version : s.date && "от " + s.date;
    el.docMeta.textContent = [s.number && "№ " + s.number, version, s.pinpoint].filter(Boolean).join(" · ");
    el.docBody.innerHTML = `<div class="sources-empty">Загружаю документ…</div>`;
    let text = null, fragment = s.fragment || "", note = "";
    const canLoadDocument = state.mode === "mock"
      ? Boolean(s.doc_id)
      : Boolean(s.answer_id && s.answer_source_id);
    if (canLoadDocument) {
      try {
        const document = await getDocument(s);
        text = document.text || null;
        fragment = fragment || document.highlight || "";
      }
      catch (e) {
        if (e.silent) return;
        note = "Не удалось загрузить полный текст документа (" + e.message + "). Показан фрагмент из ответа.";
      }
    } else {
      note = "Полный текст документа недоступен. Показан фрагмент из ответа.";
    }
    if (state.activeSource !== srcIdx || el.docView.hidden) return; // пользователь уже ушёл
    if (!text) {
      el.docBody.innerHTML = (note ? `<div class="doc-note">${esc(note)}</div>` : "") + (fragment ? `<mark>${esc(fragment)}</mark>` : "");
      return;
    }
    const pos = findFragment(text, fragment);
    if (!pos) {
      el.docBody.innerHTML = (fragment ? `<div class="doc-note">Фрагмент не найден в тексте дословно: «${esc(fragment)}»</div>` : "") + esc(text);
      return;
    }
    el.docBody.innerHTML = esc(text.slice(0, pos[0])) + `<mark id="hl">${esc(text.slice(pos[0], pos[1]))}</mark>` + esc(text.slice(pos[1]));
    const mark = document.getElementById("hl");
    if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  // ---------- Действия с диалогами ----------
  function newDialog() {
    const d = current();
    if (d && !d.messages.length) { el.input.focus(); return; }
    const nd = { id: uid(), serverId: null, title: "", messages: [], updatedAt: Date.now(), favorite: false };
    state.dialogs.unshift(nd);
    state.currentId = nd.id;
    state.selectedMsg = null;
    state.activeSource = null;
    render();
    el.input.focus();
  }

  function openDialog(id) {
    if (state.pending) return;
    // Пустой черновик, из которого ушли, не храним.
    state.dialogs = state.dialogs.filter((d) => d.messages.length || d.id === id);
    state.currentId = id;
    state.selectedMsg = lastBotIndex(current());
    state.activeSource = null;
    render();
  }

  async function deleteDialog(id) {
    if (state.pending && id === state.currentId) return;
    const dialog = state.dialogs.find((d) => d.id === id);
    if (state.mode === "backend" && dialog && dialog.serverId) {
      try {
        const r = await api("/conversations/" + encodeURIComponent(dialog.serverId), { method: "DELETE" }, 15000);
        if (!r.ok) throw await responseError(r);
      } catch (e) {
        if (e.silent) return;
        toast("Не удалось удалить диалог: " + e.message);
        return;
      }
    }
    state.dialogs = state.dialogs.filter((d) => d.id !== id);
    saveDialogs();
    if (id === state.currentId) {
      const next = state.dialogs.find((d) => d.messages.length);
      if (next) openDialog(next.id); else { state.currentId = null; newDialog(); }
    } else renderDialogList();
  }

  function toggleFavorite(id) {
    const d = state.dialogs.find((x) => x.id === id);
    if (!d) return;
    d.favorite = !d.favorite;
    saveDialogs();
    renderDialogList();
  }

  function selectMessage(idx) {
    if (state.selectedMsg === idx) return;
    state.selectedMsg = idx;
    state.activeSource = null;
    el.messages.querySelectorAll(".msg.bot").forEach((n) => n.classList.remove("selected"));
    const nodes = el.messages.children;
    if (nodes[idx]) nodes[idx].classList.add("selected");
    renderSources();
  }

  function lastBotIndex(d) {
    if (!d) return null;
    for (let i = d.messages.length - 1; i >= 0; i--) if (d.messages[i].role === "assistant" && !d.messages[i].error) return i;
    return null;
  }

  // История для бэкенда: только реплики пользователя и успешные ответы ассистента.
  const historyOf = (d) => d.messages.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content }));

  async function send(text) {
    text = (text ?? el.input.value).trim();
    if (!text || state.pending) return;
    let d = current();
    if (!d) { newDialog(); d = current(); }
    d.messages.push({ role: "user", content: text });
    if (!d.title) d.title = text.length > 60 ? text.slice(0, 57) + "…" : text;
    d.updatedAt = Date.now();
    el.input.value = "";
    autoGrow();
    await requestAnswer(d);
  }

  async function requestAnswer(d) {
    state.pending = true;
    saveDialogs();
    render();
    try {
      const ask = state.mode === "mock" ? askMock : askBackend;
      const res = await ask(historyOf(d));
      d.messages.push({
        role: "assistant",
        content: res.answer,
        answerId: res.answerId,
        sources: res.sources,
        status: res.status,
      });
    } catch (e) {
      if (e.silent) { state.pending = false; return; }
      const reason = e.name === "AbortError" ? "превышено время ожидания" : e.message;
      d.messages.push({ role: "assistant", error: true, content: "Не удалось получить ответ: " + reason + "." });
    } finally {
      if (state.session) {
        state.pending = false;
        d.updatedAt = Date.now();
        state.selectedMsg = lastBotIndex(d);
        state.activeSource = null;
        saveDialogs();
        if (d.id === state.currentId) render(); else renderDialogList();
        el.input.focus();
      }
    }
  }

  function retry() {
    const d = current();
    if (!d || state.pending) return;
    const last = d.messages[d.messages.length - 1];
    if (last && last.error) d.messages.pop();
    requestAnswer(d);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (err) { /* ignore */ }
      ta.remove();
    }
    toast("Ответ скопирован вместе с источниками");
  }

  let toastTimer;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.toast.hidden = true; }, 2200);
  }

  function autoGrow() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 160) + "px";
    updateSendState();
  }
  function updateSendState() { el.btnSend.disabled = state.pending || !el.input.value.trim(); }

  function resetSearchAndFilters() {
    state.query = "";
    el.search.value = "";
    state.filters = { period: "all", docs: new Set(), favorites: false };
    renderFilterPanel();
    render();
  }

  // ---------- Вход и выход ----------
  function showAuth(message) {
    el.app.hidden = true;
    el.authScreen.hidden = false;
    el.authError.textContent = message || "";
    el.authError.hidden = !message;
    el.authHint.hidden = state.mode !== "mock";
    el.authHint.innerHTML = `Демо-режим: бэкенд не подключён. Вход — <b>${esc(CFG.demoLogin)}</b> / <b>${esc(CFG.demoPassword)}</b>`;
    el.authPassword.value = "";
    updateAuthSubmit();
    (el.authLogin.value ? el.authPassword : el.authLogin).focus();
  }

  async function startApp() {
    state.dialogs = state.mode === "backend" ? await loadBackendDialogs() : loadDialogs();
    el.authScreen.hidden = true;
    el.app.hidden = false;
    const u = state.session.user;
    el.userName.textContent = u.name || u.login;
    el.userRole.textContent = u.role || "";
    el.userRole.hidden = !u.role;
    el.userAvatar.textContent = (u.name || u.login).split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    el.userAvatar.title = u.name || u.login;
    el.btnLogout.hidden = !CFG.auth;
    state.currentId = null;
    state.docCache = {};
    resetSearchAndFilters();
    const first = state.dialogs.find((d) => d.messages.length);
    if (first) openDialog(first.id); else newDialog();
  }

  function logout(message) {
    const token = state.session && state.session.token;
    if (token && state.mode === "backend") {
      fetchWithTimeout(apiUrl("/auth/logout"), { method: "POST", headers: { Authorization: "Bearer " + token } }, 5000).catch(() => {});
    }
    clearSession();
    state.session = null;
    state.pending = false;
    state.dialogs = [];
    state.currentId = null;
    showAuth(message);
  }

  function updateAuthSubmit() {
    el.authSubmit.disabled = !el.authLogin.value.trim() || !el.authPassword.value;
  }

  async function onLogin(e) {
    e.preventDefault();
    const login = el.authLogin.value.trim();
    const password = el.authPassword.value;
    if (!login || !password) return;
    el.authSubmit.disabled = true;
    el.authSubmit.textContent = "Входим…";
    el.authError.hidden = true;
    try {
      const s = await loginRequest(login, password);
      state.session = s;
      await startApp();
      saveSession(s, el.authRemember.checked);
    } catch (err) {
      state.session = null;
      el.authError.textContent = err.message;
      el.authError.hidden = false;
      el.authPassword.select();
    } finally {
      el.authSubmit.textContent = "Войти";
      updateAuthSubmit();
    }
  }

  // ---------- События ----------
  el.authForm.addEventListener("submit", onLogin);
  el.authLogin.addEventListener("input", updateAuthSubmit);
  el.authPassword.addEventListener("input", updateAuthSubmit);
  el.togglePassword.addEventListener("click", () => {
    const show = el.authPassword.type === "password";
    el.authPassword.type = show ? "text" : "password";
    el.togglePassword.textContent = show ? "Скрыть" : "Показать";
    el.togglePassword.setAttribute("aria-pressed", String(show));
  });
  el.btnLogout.addEventListener("click", () => logout());

  el.btnNew.addEventListener("click", newDialog);
  el.btnBack.addEventListener("click", () => { state.activeSource = null; renderSources(); });
  el.composer.addEventListener("submit", (e) => { e.preventDefault(); send(); });
  el.input.addEventListener("input", autoGrow);
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });

  let searchTimer;
  el.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = el.search.value; renderDialogList(); renderMessages(); }, 150);
  });
  el.search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { el.search.value = ""; state.query = ""; renderDialogList(); renderMessages(); }
  });
  el.btnFilter.addEventListener("click", () => {
    const open = el.filterPanel.hidden;
    el.filterPanel.hidden = !open;
    el.btnFilter.setAttribute("aria-expanded", String(open));
  });
  el.periodChips.addEventListener("click", (e) => {
    const b = e.target.closest("[data-period]");
    if (!b) return;
    state.filters.period = b.dataset.period;
    renderFilterPanel(); renderDialogList();
  });
  el.docChips.addEventListener("click", (e) => {
    const b = e.target.closest("[data-doc]");
    if (!b) return;
    const id = b.dataset.doc;
    if (state.filters.docs.has(id)) state.filters.docs.delete(id); else state.filters.docs.add(id);
    renderFilterPanel(); renderDialogList();
  });
  el.favChip.addEventListener("click", () => {
    state.filters.favorites = !state.filters.favorites;
    renderFilterPanel(); renderDialogList();
  });
  el.btnResetFilters.addEventListener("click", resetSearchAndFilters);

  // ---------- Старт ----------
  (async function init() {
    state.mode = await detectMode();
    el.modeBadge.hidden = state.mode !== "mock";
    if (!CFG.auth) {
      state.session = { token: "", user: { login: "guest", name: "Гость", role: "" } };
      await startApp();
      return;
    }
    state.session = loadSession();
    if (state.session) {
      try { await startApp(); }
      catch (err) {
        if (!err.silent) showAuth("Не удалось загрузить диалоги: " + err.message);
      }
    } else showAuth();
  })();
})();
