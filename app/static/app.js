/* Khazana front end — no framework, no build step.
   State is three variables. Everything else is a render function that reads
   them, which keeps the UI honest: there is no second copy of the truth to
   drift out of sync. */

(() => {
  "use strict";

  const state = {
    token: localStorage.getItem("khazana_token") || "",
    user: null,
    collections: [],
    activeCollection: null, // null = all collections
    documents: [],
    stats: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  // ── API ──────────────────────────────────────────────────────────────
  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (options.body && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    const res = await fetch(path, { ...options, headers });

    if (res.status === 401 && state.token) {
      signOut();
      throw new Error("Your session expired — please sign in again.");
    }

    if (res.status === 204) return null;

    const text = await res.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch { data = { detail: text }; }
    }

    if (!res.ok) {
      const detail = data && data.detail;
      const message = Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
        : detail || `Request failed (${res.status})`;
      throw new Error(message);
    }
    return data;
  }

  // ── Toast ────────────────────────────────────────────────────────────
  let toastTimer = null;
  function toast(message, isError = false) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.toggle("is-error", isError);
    node.classList.remove("is-hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.add("is-hidden"), 3600);
  }

  // ── Auth ─────────────────────────────────────────────────────────────
  function showAuth() {
    $("#auth-view").classList.remove("is-hidden");
    $("#app-view").classList.add("is-hidden");
  }

  function showApp() {
    $("#auth-view").classList.add("is-hidden");
    $("#app-view").classList.remove("is-hidden");
    $("#user-name").textContent = state.user ? state.user.display_name : "";
  }

  function signOut() {
    state.token = "";
    state.user = null;
    state.collections = [];
    state.activeCollection = null;
    state.documents = [];
    localStorage.removeItem("khazana_token");
    showAuth();
  }

  function authError(message) {
    const node = $("#auth-error");
    node.textContent = message;
    node.classList.toggle("is-hidden", !message);
  }

  document.querySelectorAll(".tab[data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab[data-tab]").forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      const isLogin = tab.dataset.tab === "login";
      $("#login-form").classList.toggle("is-hidden", !isLogin);
      $("#register-form").classList.toggle("is-hidden", isLogin);
      authError("");
    });
  });

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    authError("");
    const form = new FormData(event.target);
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const data = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      await enter(data);
    } catch (error) {
      authError(error.message);
    } finally {
      button.disabled = false;
    }
  });

  $("#register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    authError("");
    const form = new FormData(event.target);
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const data = await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          display_name: form.get("display_name"),
          password: form.get("password"),
        }),
      });
      await enter(data);
    } catch (error) {
      authError(error.message);
    } finally {
      button.disabled = false;
    }
  });

  async function enter(data) {
    state.token = data.access_token;
    state.user = data.user;
    localStorage.setItem("khazana_token", state.token);
    showApp();
    await refreshAll();
  }

  $("#logout-btn").addEventListener("click", signOut);

  // ── Data loading ─────────────────────────────────────────────────────
  async function refreshAll() {
    await Promise.all([loadCollections(), loadStats()]);
    await loadDocuments();
  }

  async function loadCollections() {
    state.collections = await api("/api/collections");
    if (state.activeCollection) {
      const still = state.collections.find((c) => c.id === state.activeCollection);
      if (!still) state.activeCollection = null;
    }
    renderCollections();
  }

  async function loadStats() {
    state.stats = await api("/api/stats");
    renderStats();
  }

  async function loadDocuments() {
    if (!state.activeCollection) {
      state.documents = [];
      renderDocuments();
      return;
    }
    state.documents = await api(`/api/collections/${state.activeCollection}/documents`);
    renderDocuments();
  }

  // ── Render: collections ──────────────────────────────────────────────
  function renderCollections() {
    const list = $("#collection-list");
    list.innerHTML = "";

    const all = el("li", "collection-item" + (state.activeCollection === null ? " is-active" : ""));
    all.appendChild(el("span", "collection-name", "All collections"));
    const total = state.collections.reduce((sum, c) => sum + c.chunk_count, 0);
    all.appendChild(el("span", "collection-meta", `${total} chunks`));
    all.addEventListener("click", () => selectCollection(null));
    list.appendChild(all);

    state.collections.forEach((collection) => {
      const item = el("li", "collection-item" + (state.activeCollection === collection.id ? " is-active" : ""));
      const main = el("div");
      main.appendChild(el("div", "collection-name", collection.name));
      main.appendChild(el("div", "collection-meta", `${collection.document_count} docs · ${collection.chunk_count} chunks`));
      item.appendChild(main);

      const del = el("button", "collection-del", "×");
      del.title = "Delete collection";
      del.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (!confirm(`Delete "${collection.name}" and all its documents?`)) return;
        try {
          await api(`/api/collections/${collection.id}`, { method: "DELETE" });
          if (state.activeCollection === collection.id) state.activeCollection = null;
          toast("Collection deleted");
          await refreshAll();
        } catch (error) {
          toast(error.message, true);
        }
      });
      item.appendChild(del);

      item.addEventListener("click", () => selectCollection(collection.id));
      list.appendChild(item);
    });

    $("#collections-empty").classList.toggle("is-hidden", state.collections.length > 0);
    $("#scope-label").textContent = state.activeCollection
      ? state.collections.find((c) => c.id === state.activeCollection)?.name || "Collection"
      : "All collections";
    $("#docs-title").textContent = state.activeCollection ? "Documents" : "Documents (select a collection)";
    $("#upload-btn").disabled = !state.activeCollection;
  }

  function selectCollection(id) {
    state.activeCollection = id;
    renderCollections();
    loadDocuments();
    renderSuggestions();
  }

  // ── Render: stats ────────────────────────────────────────────────────
  function renderStats() {
    const grid = $("#stats-grid");
    grid.innerHTML = "";
    if (!state.stats) return;

    const rows = [
      ["Docs", state.stats.documents],
      ["Chunks", state.stats.chunks],
      ["Asked", state.stats.questions_asked],
      ["Answered", `${Math.round(state.stats.answer_rate * 100)}%`],
    ];
    rows.forEach(([label, value]) => {
      const wrap = el("div");
      wrap.appendChild(el("dt", null, label));
      wrap.appendChild(el("dd", null, String(value)));
      grid.appendChild(wrap);
    });
  }

  // ── Render: documents ────────────────────────────────────────────────
  function renderDocuments() {
    const list = $("#document-list");
    list.innerHTML = "";

    state.documents.forEach((doc) => {
      const item = el("li", "document-item");
      const main = el("div", "document-main");
      main.appendChild(el("div", "document-title", doc.title));
      main.appendChild(
        el("div", "document-meta",
          `${doc.source_type.toUpperCase()} · ${doc.chunk_count} chunks · ${doc.char_count.toLocaleString()} chars`)
      );
      item.appendChild(main);

      const del = el("button", "document-del", "×");
      del.title = "Delete document";
      del.addEventListener("click", async () => {
        if (!confirm(`Delete "${doc.title}"?`)) return;
        try {
          await api(`/api/documents/${doc.id}`, { method: "DELETE" });
          toast("Document deleted");
          await refreshAll();
        } catch (error) {
          toast(error.message, true);
        }
      });
      item.appendChild(del);
      list.appendChild(item);
    });

    $("#documents-empty").classList.toggle("is-hidden", state.documents.length > 0);
    if (!state.activeCollection) {
      $("#documents-empty").textContent = "Select a collection to see its documents.";
      $("#documents-empty").classList.remove("is-hidden");
    } else {
      $("#documents-empty").textContent = "No documents in this collection yet.";
    }
  }

  // ── Render: suggestions ──────────────────────────────────────────────
  const SUGGESTIONS = [
    "What is the refund policy?",
    "How long does onboarding take?",
    "Who approves expenses over 50,000?",
    "What are the support hours?",
  ];

  function renderSuggestions() {
    const wrap = $("#suggestions");
    wrap.innerHTML = "";
    SUGGESTIONS.forEach((text) => {
      const chip = el("button", "suggestion", text);
      chip.type = "button";
      chip.addEventListener("click", () => {
        $("#question").value = text;
        $("#ask-form").requestSubmit();
      });
      wrap.appendChild(chip);
    });
  }

  // ── Ask ──────────────────────────────────────────────────────────────
  $("#ask-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = $("#question").value.trim();
    if (!question) return;

    const button = $("#ask-btn");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>';

    try {
      const payload = { question, limit: 8 };
      if (state.activeCollection) payload.collection_id = state.activeCollection;

      const result = await api("/api/query", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      renderAnswer(result);
      await loadStats();

      // Fetch the ranking breakdown in the background; it is a nice-to-have
      // and must never block the answer from appearing.
      api("/api/query/explain", {
        method: "POST",
        body: JSON.stringify(payload),
      }).then(renderExplain).catch(() => {});
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "Ask";
    }
  });

  function renderAnswer(result) {
    const panel = $("#answer-panel");
    panel.classList.remove("is-hidden");

    const badge = $("#confidence-badge");
    badge.className = `badge ${result.confidence_label}`;
    badge.textContent = result.answered
      ? `${result.confidence_label} · ${Math.round(result.confidence * 100)}%`
      : "no answer";

    $("#answer-meta").textContent = result.answered
      ? `${result.citations.length} citation${result.citations.length === 1 ? "" : "s"} · ${result.sources_considered} passages considered`
      : `${result.sources_considered} passages considered`;

    const body = $("#answer-body");
    const refusal = $("#answer-refusal");
    const citations = $("#citations");
    body.innerHTML = "";
    citations.innerHTML = "";

    if (result.answered) {
      refusal.classList.add("is-hidden");
      // Render the answer with the [n] markers turned into styled superscripts.
      const parts = result.answer.split(/(\[\d+\])/g);
      parts.forEach((part) => {
        const match = part.match(/^\[(\d+)\]$/);
        if (match) {
          body.appendChild(el("span", "cite-ref", match[1]));
        } else if (part) {
          body.appendChild(document.createTextNode(part));
        }
      });

      result.citations.forEach((citation) => {
        const card = el("div", "citation");
        card.appendChild(el("div", "citation-marker", String(citation.marker)));
        const inner = el("div", "citation-body");
        inner.appendChild(el("div", "citation-title", citation.document_title));
        inner.appendChild(el("div", "citation-text", citation.sentence));
        card.appendChild(inner);
        citations.appendChild(card);
      });
    } else {
      refusal.classList.remove("is-hidden");
      refusal.innerHTML = "";
      refusal.appendChild(el("strong", null, "Khazana could not answer this. "));
      refusal.appendChild(document.createTextNode(result.reason));
      if (result.missing_terms && result.missing_terms.length) {
        refusal.appendChild(document.createElement("br"));
        refusal.appendChild(
          el("span", null, `Not found in your documents: ${result.missing_terms.join(", ")}`)
        );
      }
    }

    $("#explain-details").open = false;
    $("#explain-body").innerHTML = "";
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderExplain(data) {
    const body = $("#explain-body");
    body.innerHTML = "";
    if (!data || !data.results || !data.results.length) {
      body.appendChild(el("div", null, "No candidates were retrieved."));
      return;
    }
    data.results.forEach((row) => {
      const line = el("div", "explain-row");
      line.appendChild(el("span", row.found_by_both ? "both" : "", row.chunk_id.slice(0, 10)));
      line.appendChild(el("span", null, `bm25 ${row.bm25_rank ?? "—"}`));
      line.appendChild(el("span", "vec", `vec ${row.vector_rank ?? "—"}`));
      body.appendChild(line);
    });
  }

  // ── New collection ───────────────────────────────────────────────────
  $("#new-collection-btn").addEventListener("click", async () => {
    const name = prompt("Collection name (e.g. HR Policies):");
    if (!name || !name.trim()) return;
    try {
      const created = await api("/api/collections", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), description: "" }),
      });
      toast("Collection created");
      await loadCollections();
      selectCollection(created.id);
    } catch (error) {
      toast(error.message, true);
    }
  });

  // ── Upload ───────────────────────────────────────────────────────────
  $("#upload-btn").addEventListener("click", () => {
    $("#upload-form-wrap").classList.toggle("is-hidden");
  });
  $("#upload-cancel").addEventListener("click", () => {
    $("#upload-form-wrap").classList.add("is-hidden");
    $("#upload-status").textContent = "";
  });

  document.querySelectorAll(".tab[data-upload]").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab[data-upload]").forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      const isFile = tab.dataset.upload === "file";
      $("#upload-file-fields").classList.toggle("is-hidden", !isFile);
      $("#upload-text-fields").classList.toggle("is-hidden", isFile);
    });
  });

  $("#upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.activeCollection) {
      toast("Select a collection first", true);
      return;
    }

    const form = new FormData(event.target);
    const status = $("#upload-status");
    const isFileMode = !$("#upload-file-fields").classList.contains("is-hidden");

    status.textContent = "Ingesting…";

    try {
      if (isFileMode) {
        const file = form.get("file");
        if (!file || !file.size) {
          status.textContent = "Choose a file first.";
          return;
        }
        const body = new FormData();
        body.append("file", file);
        await api(`/api/collections/${state.activeCollection}/upload`, {
          method: "POST",
          body,
        });
      } else {
        const title = (form.get("title") || "").toString().trim();
        const text = (form.get("text") || "").toString().trim();
        if (!title || !text) {
          status.textContent = "Both a title and some text are required.";
          return;
        }
        await api(`/api/collections/${state.activeCollection}/documents`, {
          method: "POST",
          body: JSON.stringify({ title, text }),
        });
      }

      status.textContent = "";
      event.target.reset();
      $("#upload-form-wrap").classList.add("is-hidden");
      toast("Document ingested");
      await refreshAll();
    } catch (error) {
      status.textContent = error.message;
      toast(error.message, true);
    }
  });

  // ── Boot ─────────────────────────────────────────────────────────────
  async function boot() {
    renderSuggestions();
    if (!state.token) {
      showAuth();
      return;
    }
    try {
      state.user = await api("/api/auth/me");
      showApp();
      await refreshAll();
    } catch {
      signOut();
    }
  }

  boot();
})();
