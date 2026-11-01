/* Cortes Lab — dashboard local, workspaces e acompanhamento em tempo real. */

(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const state = {
    page: "overview",
    busy: false,
    eventSource: null,
    logs: [],
    files: [],
    youtubeFiles: [],
  };

  const PAGE_META = {
    overview: "Visão geral",
    cortes: "Cortes virais",
    quiz: "Máquina de quizzes",
    batalha: "Batalha 1v1",
    historia: "História",
    tts: "Voz / TTS",
    analytics: "Analytics",
    youtube: "Publicar",
    settings: "Configurações",
  };

  const WORKFLOW_LABEL = { pendente: "Pendente", publicado: "Publicado", descartado: "Descartado" };
  const PIPELINE_LABEL = { idle: "Aguardando", queued: "Na fila", running: "Processando", done: "Pronto", error: "Erro" };

  function escapeHtml(value) {
    const node = document.createElement("span");
    node.textContent = String(value ?? "");
    return node.innerHTML;
  }

  function showToast(message, kind = "success") {
    const region = $("#toast-region");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `toast${kind === "error" ? " is-error" : ""}`;
    toast.innerHTML = `<svg class="icon"><use href="#i-${kind === "error" ? "info" : "check"}"></use></svg><span>${escapeHtml(message)}</span>`;
    region.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4800);
  }

  function setFeedback(id, message, success = false) {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("is-success", Boolean(success));
  }

  function switchPage(page) {
    if (!PAGE_META[page]) page = "overview";
    state.page = page;
    $$('[data-page]').forEach((section) => section.classList.toggle("is-active", section.dataset.page === page));
    $$('[data-page-target]').forEach((item) => {
      if (item.classList.contains("nav-item")) item.classList.toggle("is-active", item.dataset.pageTarget === page);
    });
    const title = $("#breadcrumb-title");
    if (title) title.textContent = PAGE_META[page];
    closeMobileNav();
    if (window.location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setupNavigation() {
    $$('[data-page-target]').forEach((item) => item.addEventListener("click", () => switchPage(item.dataset.pageTarget)));
    const initial = window.location.hash.slice(1);
    switchPage(PAGE_META[initial] ? initial : "overview");
  }

  function closeMobileNav() {
    $("#sidebar")?.classList.remove("is-open");
    $("#mobile-scrim")?.classList.remove("is-visible");
  }

  function setupMobileNav() {
    $("#mobile-menu")?.addEventListener("click", () => {
      $("#sidebar")?.classList.add("is-open");
      $("#mobile-scrim")?.classList.add("is-visible");
    });
    $("#sidebar-close")?.addEventListener("click", closeMobileNav);
    $("#mobile-scrim")?.addEventListener("click", closeMobileNav);
  }

  function setupTheme() {
    const saved = localStorage.getItem("cortes-lab-theme");
    if (saved === "light") document.body.classList.add("light-mode");
    const updateIcon = () => {
      const use = $("#theme-icon use");
      if (use) use.setAttribute("href", document.body.classList.contains("light-mode") ? "#i-sun" : "#i-moon");
    };
    updateIcon();
    $("#theme-toggle")?.addEventListener("click", () => {
      document.body.classList.toggle("light-mode");
      localStorage.setItem("cortes-lab-theme", document.body.classList.contains("light-mode") ? "light" : "dark");
      updateIcon();
    });
  }

  function setActivity({ status = "idle", progress = 0, message = "", stage = "" } = {}) {
    const pct = Math.round(Math.max(0, Math.min(1, Number(progress) || 0)) * 100);
    const progressBar = $("#activity-progress");
    if (progressBar) progressBar.style.width = `${pct}%`;
    if ($("#activity-percent")) $("#activity-percent").textContent = `${pct}%`;
    if ($("#activity-message")) $("#activity-message").textContent = message || (status === "idle" ? "Escolha um workspace para começar." : "Processando…");
    if ($("#activity-stage")) $("#activity-stage").textContent = stage || message || "Pronto quando você estiver.";
    const title = $("#activity-title");
    if (title) title.textContent = status === "running" ? "Seu job está em movimento" : status === "done" ? "Tudo pronto para revisar" : status === "error" ? "Algo interrompeu o job" : "Aguardando um novo job";
    const badge = $("#activity-badge");
    if (badge) {
      badge.textContent = status === "running" ? "RODANDO" : status === "done" ? "CONCLUÍDO" : status === "error" ? "ERRO" : "OCIOSO";
      badge.className = `activity-badge${status === "running" ? " is-running" : status === "done" ? " is-done" : status === "error" ? " is-error" : ""}`;
    }
  }

  function appendLog(message, isError = false) {
    const clean = String(message || "").trim();
    if (!clean) return;
    const lines = clean.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    state.logs.push(...lines.map((line) => ({ text: line, isError })));
    state.logs = state.logs.slice(-80);
    const list = $("#log-list");
    if (!list) return;
    list.innerHTML = state.logs.length ? state.logs.map((line) => `<div class="log-line${line.isError ? " is-error" : ""}">${escapeHtml(line.text)}</div>`).join("") : '<div class="log-empty">As etapas do pipeline aparecerão aqui.</div>';
    list.scrollTop = list.scrollHeight;
  }

  function clearLog() {
    state.logs = [];
    const list = $("#log-list");
    if (list) list.innerHTML = '<div class="log-empty">As etapas do pipeline aparecerão aqui.</div>';
  }

  function renderOutputs(outputs = [], urls = []) {
    const list = $("#output-list");
    const count = $("#output-count");
    if (count) count.textContent = `${outputs.length} arquivo${outputs.length === 1 ? "" : "s"}`;
    if (!list) return;
    if (!outputs.length) {
      list.innerHTML = '<div class="log-empty">Nenhuma saída nesta sessão.</div>';
      return;
    }
    list.innerHTML = outputs.map((path, index) => {
      const href = urls[index] || "";
      const label = String(path).split(/[\\/]/).pop() || path;
      return `<div class="output-row"><svg class="icon icon-xs"><use href="#i-file"></use></svg>${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener" title="Abrir ${escapeHtml(path)}">${escapeHtml(label)}</a>` : `<span title="${escapeHtml(path)}">${escapeHtml(label)}</span>`}<small>${escapeHtml(String(path).toLowerCase().includes(".mp4") ? "MP4" : "AUDIO")}</small></div>`;
    }).join("");
  }

  function setBusy(busy) {
    state.busy = Boolean(busy);
    $$('[data-job-control]').forEach((button) => { button.disabled = state.busy; });
    ["#add-playlist-btn", "#process-playlist-btn"].forEach((selector) => { const button = $(selector); if (button) button.disabled = state.busy; });
    if (state.busy) setActivity({ status: "running", progress: 0, message: "Enviando job…", stage: "Preparando entrada" });
  }

  function handleProgressEvent(data) {
    if (!data || !data.type) return;
    if (data.type === "status") {
      setBusy(true);
      setActivity({ status: "running", progress: data.frac || 0, message: data.message, stage: data.message });
      appendLog(data.message);
    } else if (data.type === "progress") {
      setActivity({ status: "running", progress: data.frac || 0, message: data.message, stage: data.message });
    } else if (data.type === "log") {
      appendLog(data.message);
      setActivity({ status: "running", message: data.message, stage: data.message });
    } else if (data.type === "done") {
      setBusy(false);
      setActivity({ status: "done", progress: 1, message: data.message || "Concluído.", stage: "Saídas disponíveis para revisão" });
      appendLog(data.message || "Processamento concluído.");
      renderOutputs(data.outputs || [], data.output_urls || []);
      showToast(data.message || "Job concluído.");
      loadRuns();
      loadPlaylist();
    } else if (data.type === "error") {
      setBusy(false);
      setActivity({ status: "error", progress: 0, message: data.message || "Erro no processamento.", stage: "Veja o log acima para mais detalhes" });
      appendLog(data.message || "Erro no processamento.", true);
      showToast(data.message || "Erro no processamento.", "error");
    } else if (data.type === "playlist_refresh") {
      loadPlaylist();
    }
  }

  function connectProgress() {
    if (state.eventSource) return;
    const source = new EventSource("/api/progress");
    state.eventSource = source;
    source.onmessage = (event) => {
      try { handleProgressEvent(JSON.parse(event.data)); } catch (_) { /* heartbeat */ }
    };
    source.onerror = () => {
      source.close();
      state.eventSource = null;
      window.setTimeout(connectProgress, 2500);
    };
  }

  async function syncSnapshot() {
    try {
      const response = await fetch("/api/progress/snapshot");
      if (!response.ok) return;
      const data = await response.json();
      if (data.status === "running") {
        setBusy(true);
        setActivity({ status: "running", progress: data.progress, message: data.message, stage: data.message });
      } else if (data.status === "done") {
        setActivity({ status: "done", progress: 1, message: data.message, stage: "Saídas disponíveis para revisão" });
        renderOutputs(data.outputs || [], data.output_urls || []);
      } else if (data.status === "error") {
        setActivity({ status: "error", message: data.error || data.message, stage: "Veja o log acima para mais detalhes" });
      }
    } catch (_) { /* server may still be starting */ }
  }

  function buildCutsFormData() {
    const form = $("#cuts-form");
    const data = new FormData();
    const sourceMode = $(".source-tab.is-active")?.dataset.sourceMode || "url";
    data.append("urls", sourceMode === "url" ? $("#cortes-urls")?.value.trim() || "" : "");
    data.append("lang", $("#cortes-lang")?.value || "pt");
    data.append("position", $("#cortes-position")?.value || "bottom");
    data.append("font", $("#cortes-font")?.value || "Montserrat");
    data.append("color", $("#cortes-color")?.value || "#D7F66E");
    data.append("bg_color", $("#cortes-bg-color")?.value || "#101411");
    data.append("opacity", $("#cortes-opacity")?.value || "75");
    data.append("hook_text", $("#cortes-hook")?.value || "");
    data.append("outro_text", $("#cortes-outro")?.value || "");
    data.append("clip_start", $("#cortes-start")?.value || "");
    data.append("clip_end", $("#cortes-end")?.value || "");
    data.append("export_zip", $("#cortes-export-zip")?.checked ? "true" : "false");
    const dub = $("input[name=dub]:checked", form)?.value || "off";
    data.append("dub_en", dub === "en" ? "true" : "false");
    data.append("dub_pt", dub === "pt" ? "true" : "false");
    data.append("tts_voice", $("#cortes-voice")?.value || "");
    const files = $("#cortes-files")?.files || [];
    if (sourceMode === "file") Array.from(files).forEach((file) => data.append("files", file));
    return { data, sourceMode, files };
  }

  function validateCuts(sourceMode, files) {
    if (sourceMode === "url" && !$("#cortes-urls")?.value.trim()) {
      setFeedback("cuts-feedback", "Cole ao menos uma URL para continuar.");
      return false;
    }
    if (sourceMode === "file" && (!files || !files.length)) {
      setFeedback("cuts-feedback", "Escolha ao menos um vídeo local para continuar.");
      return false;
    }
    return true;
  }

  async function postCuts(addToPlaylist = false) {
    const built = buildCutsFormData();
    if (!validateCuts(built.sourceMode, built.files)) return;
    setFeedback("cuts-feedback", "");
    if (!addToPlaylist) setBusy(true);
    try {
      const endpoint = addToPlaylist ? "/api/playlist" : "/api/jobs";
      const response = await fetch(endpoint, { method: "POST", body: built.data });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      showToast(body.message || (addToPlaylist ? "Adicionado à playlist." : "Job enfileirado."));
      setFeedback("cuts-feedback", body.message || "Pronto.", true);
      if (!addToPlaylist) appendLog(body.message || "Job enfileirado.");
      await loadPlaylist();
    } catch (error) {
      setBusy(false);
      setFeedback("cuts-feedback", error.message || String(error));
      showToast(error.message || "Não foi possível enviar o job.", "error");
    }
  }

  function readApiError(body, fallback) {
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
    return body?.message || fallback || "Erro inesperado.";
  }

  async function submitStudio(form) {
    const jobType = form.dataset.jobType;
    const feedbackId = jobType === "batalha" ? "battle-feedback" : jobType === "historia" ? "story-feedback" : jobType === "tts" ? "tts-feedback" : "quiz-feedback";
    const payload = { job_type: jobType };
    new FormData(form).forEach((value, key) => { if (typeof value === "string") payload[key] = value; });
    if (jobType === "quiz") { payload.count = Number(payload.count || 5); payload.timer_sec = Number(payload.timer_sec || 5); }
    setFeedback(feedbackId, "");
    setBusy(true);
    try {
      const response = await fetch("/api/studio/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      appendLog(body.message || "Job enfileirado.");
      showToast(body.message || "Job enfileirado.");
      setFeedback(feedbackId, body.message || "Job enfileirado.", true);
    } catch (error) {
      setBusy(false);
      setFeedback(feedbackId, error.message || String(error));
      showToast(error.message || "Não foi possível iniciar o fluxo.", "error");
    }
  }

  function renderFileList(files, target) {
    const node = $(target);
    if (!node) return;
    if (!files.length) { node.innerHTML = ""; return; }
    node.innerHTML = files.map((file) => `<div class="file-chip"><svg class="icon icon-xs"><use href="#i-file"></use></svg><span>${escapeHtml(file.name)}</span><small>${formatBytes(file.size)}</small></div>`).join("");
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function setupSources() {
    $$(".source-tab").forEach((button) => button.addEventListener("click", () => {
      const mode = button.dataset.sourceMode;
      $$(".source-tab").forEach((item) => item.classList.toggle("is-active", item === button));
      $$(".source-view").forEach((view) => view.classList.toggle("is-active", view.dataset.sourceView === mode));
    }));
    const input = $("#cortes-files");
    input?.addEventListener("change", () => { state.files = Array.from(input.files || []); renderFileList(state.files, "#cortes-file-list"); });
    $$(".dropzone").forEach((zone) => {
      ["dragenter", "dragover"].forEach((eventName) => zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.add("is-dragging"); }));
      ["dragleave", "drop"].forEach((eventName) => zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.remove("is-dragging"); }));
      zone.addEventListener("drop", (event) => {
        const fileInput = $("input[type=file]", zone);
        if (!fileInput || !event.dataTransfer.files.length) return;
        try { fileInput.files = event.dataTransfer.files; fileInput.dispatchEvent(new Event("change", { bubbles: true })); } catch (_) { /* browser protects FileList */ }
      });
    });
    $("[data-fill-hook]")?.addEventListener("click", (event) => { $("#cortes-hook").value = event.currentTarget.dataset.fillHook; showToast("Sugestão aplicada ao hook."); });
  }

  function setupFormListeners() {
    $("#cuts-form")?.addEventListener("submit", (event) => { event.preventDefault(); postCuts(false); });
    $("#add-playlist-btn")?.addEventListener("click", () => postCuts(true));
    $$('[data-studio-form]').forEach((form) => form.addEventListener("submit", (event) => { event.preventDefault(); submitStudio(form); }));
    $("#process-playlist-btn")?.addEventListener("click", processPlaylist);
    $("#refresh-playlist")?.addEventListener("click", loadPlaylist);
    $("#playlist-filter")?.addEventListener("change", loadPlaylist);
    $("#clear-log")?.addEventListener("click", clearLog);
    $("#refresh-runs")?.addEventListener("click", loadRuns);
    const opacity = $("#cortes-opacity");
    opacity?.addEventListener("input", () => { $("#cortes-opacity-value").textContent = `${opacity.value}%`; });
    const timer = $("#quiz-timer");
    timer?.addEventListener("input", () => { $("#quiz-timer-value").textContent = `${timer.value}s`; });
    bindColorPair("#cortes-color-picker", "#cortes-color");
    bindColorPair("#cortes-bg-picker", "#cortes-bg-color");
    bindColorPair("#quiz-bg-picker", "#quiz-bg");
    bindCharacterCount("#story-text", "#story-count");
    bindCharacterCount("#tts-text", "#tts-count");
    $("#tts-preview-btn")?.addEventListener("click", previewTts);
    $("#performance-form")?.addEventListener("submit", (event) => { event.preventDefault(); analyzePerformance(); });
    $("#retention-form")?.addEventListener("submit", (event) => { event.preventDefault(); analyzeRetention(); });
    $("#performance-file")?.addEventListener("change", () => updateFileLabel("#performance-file", "#performance-file-label"));
    $("#retention-file")?.addEventListener("change", () => updateFileLabel("#retention-file", "#retention-file-label"));
    $("#youtube-files")?.addEventListener("change", updateYoutubeFiles);
    $("#youtube-credentials")?.addEventListener("change", () => updateFileLabel("#youtube-credentials", null));
    $("#youtube-form")?.addEventListener("submit", (event) => { event.preventDefault(); publishYoutube(); });
    $("#youtube-time")?.addEventListener("input", renderSchedulePreview);
    $("#youtube-timezone")?.addEventListener("input", renderSchedulePreview);
    renderSchedulePreview();
    $$('[data-copy-text]').forEach((button) => button.addEventListener("click", () => copyText(button.dataset.copyText, button)));
  }

  function bindColorPair(pickerSelector, textSelector) {
    const picker = $(pickerSelector); const text = $(textSelector);
    if (!picker || !text) return;
    picker.addEventListener("input", () => { text.value = picker.value.toUpperCase(); });
    text.addEventListener("change", () => { if (/^#?[0-9a-f]{6}$/i.test(text.value.trim())) picker.value = text.value.startsWith("#") ? text.value : `#${text.value}`; });
  }

  function bindCharacterCount(inputSelector, outputSelector) {
    const input = $(inputSelector); const output = $(outputSelector);
    if (!input || !output) return;
    const update = () => { output.textContent = String(input.value.length); };
    input.addEventListener("input", update); update();
  }

  function updateFileLabel(inputSelector, labelSelector) {
    const input = $(inputSelector); const label = labelSelector ? $(labelSelector) : null;
    if (!input || !label) return;
    const files = Array.from(input.files || []);
    label.textContent = files.length === 1 ? files[0].name : files.length ? `${files.length} arquivos selecionados` : label.dataset.empty || "Escolher arquivo";
  }

  function populateVoices() {
    const options = [
      ["", "Padrão configurado"],
      ["edge:pt-BR-ThalitaMultilingualNeural", "Thalita · feminina pt-BR"],
      ["edge:pt-BR-FranciscaNeural", "Francisca · feminina pt-BR"],
      ["edge:pt-BR-AntonioNeural", "Antonio · masculino pt-BR"],
      ["edge:pt-BR-DonatoNeural", "Donato · masculino pt-BR"],
      ["edge:en-US-AriaNeural", "Aria · feminina en-US"],
    ];
    $$('[data-voice-select]').forEach((select) => { select.innerHTML = options.map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join(""); });
  }

  async function processPlaylist() {
    if (state.busy) return;
    setBusy(true);
    try {
      const response = await fetch("/api/playlist/process", { method: "POST", body: new FormData() });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      showToast(body.message || "Playlist enfileirada.");
      appendLog(body.message || "Playlist enfileirada.");
      loadPlaylist();
    } catch (error) {
      setBusy(false);
      showToast(error.message || "Não foi possível processar a playlist.", "error");
    }
  }

  async function loadPlaylist() {
    const filter = $("#playlist-filter")?.value || "";
    try {
      const response = await fetch(`/api/playlist?limit=100${filter ? `&workflow=${encodeURIComponent(filter)}` : ""}`);
      if (!response.ok) throw new Error("Falha ao carregar a playlist.");
      const body = await response.json();
      renderPlaylist(body.items || []);
      const all = body.items || [];
      const pending = all.filter((item) => item.workflow_status === "pendente").length;
      const queued = all.filter((item) => ["queued", "running"].includes(item.pipeline_status)).length;
      if ($("#stat-pending")) $("#stat-pending").textContent = String(pending);
      if ($("#stat-queued")) $("#stat-queued").textContent = String(queued);
      if ($("#playlist-summary-count")) $("#playlist-summary-count").textContent = String(pending);
    } catch (error) {
      const tbody = $("#playlist-tbody");
      if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="table-empty">${escapeHtml(error.message || "Erro ao carregar")}</td></tr>`;
    }
  }

  function renderPlaylist(items) {
    const tbody = $("#playlist-tbody");
    if (!tbody) return;
    if (!items.length) { tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Nenhuma fonte nesta visualização.</td></tr>'; return; }
    tbody.innerHTML = items.map((item) => {
      const percent = Math.round((Number(item.progress) || 0) * 100);
      const pipelineClass = item.pipeline_status === "error" ? "error" : item.pipeline_status === "done" ? "" : "muted";
      const workflowClass = item.workflow_status === "publicado" ? "status-text" : item.workflow_status === "descartado" ? "status-text muted" : "status-text";
      const actions = [];
      if (item.can_mark_workflow) {
        actions.push(`<button class="table-action" data-workflow="publicado" data-id="${item.id}">Publicar</button>`);
        actions.push(`<button class="table-action" data-workflow="descartado" data-id="${item.id}">Descartar</button>`);
      }
      if (item.can_process) actions.push(`<button class="table-action" data-process-id="${item.id}">Processar</button>`);
      return `<tr><td><div class="source-cell"><span class="source-type">${escapeHtml(item.source_type)}</span><strong title="${escapeHtml(item.source)}">${escapeHtml(item.title || shortSource(item.source, 54))}</strong></div></td><td><span class="${workflowClass}">${escapeHtml(WORKFLOW_LABEL[item.workflow_status] || item.workflow_status)}</span></td><td><span class="status-text ${pipelineClass}">${escapeHtml(PIPELINE_LABEL[item.pipeline_status] || item.pipeline_status)}</span></td><td><div class="progress-cell"><div class="progress-track"><div class="progress-fill" style="width:${percent}%"></div></div><span>${percent}%</span></div></td><td><div class="row-actions">${actions.join("") || '<span class="muted">—</span>'}</div></td></tr>`;
    }).join("");
    $$('[data-workflow]', tbody).forEach((button) => button.addEventListener("click", () => patchWorkflow(button.dataset.id, button.dataset.workflow)));
    $$('[data-process-id]', tbody).forEach((button) => button.addEventListener("click", () => processPlaylistItem(button.dataset.processId)));
  }

  function shortSource(value, max = 48) { const text = String(value || ""); return text.length > max ? `${text.slice(0, max - 1)}…` : text || "—"; }

  async function patchWorkflow(id, status) {
    try {
      const response = await fetch(`/api/playlist/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      showToast(`Item marcado como ${WORKFLOW_LABEL[status] || status}.`);
      loadPlaylist();
    } catch (error) { showToast(error.message || "Não foi possível atualizar o item.", "error"); }
  }

  async function processPlaylistItem(id) {
    try {
      const form = new FormData(); form.append("item_ids", id);
      const response = await fetch("/api/playlist/process", { method: "POST", body: form });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      setBusy(true); showToast(body.message || "Item enfileirado."); loadPlaylist();
    } catch (error) { showToast(error.message || "Não foi possível processar o item.", "error"); }
  }

  async function loadRuns() {
    try {
      const response = await fetch("/api/runs?limit=30");
      if (!response.ok) throw new Error("Falha ao carregar saídas.");
      const body = await response.json();
      const items = body.items || [];
      if ($("#stat-videos")) $("#stat-videos").textContent = String(items.length);
      renderRecentRuns(items.slice(0, 7));
    } catch (_) { /* empty state is enough */ }
  }

  function renderRecentRuns(items) {
    const list = $("#recent-list");
    if (!list) return;
    if (!items.length) { list.innerHTML = '<div class="empty-state compact-empty"><svg class="icon"><use href="#i-file"></use></svg><p>Nenhuma saída por aqui ainda.</p><small>Seu primeiro vídeo aparecerá nesta lista.</small></div>'; return; }
    list.innerHTML = items.map((item) => `<a class="recent-item" href="${escapeHtml(item.url || "#")}" ${item.url ? 'target="_blank" rel="noopener"' : ""}><span class="recent-thumb"><svg class="icon"><use href="#i-play"></use></svg></span><span class="recent-meta"><strong title="${escapeHtml(item.path)}">${escapeHtml(item.name)}</strong><span>${escapeHtml(item.duration || "—")} · resultados locais</span></span><span class="recent-open"><svg class="icon icon-xs"><use href="#i-external"></use></svg></span></a>`).join("");
  }

  async function previewTts() {
    const text = $("#tts-text")?.value.trim();
    const voice = $("#tts-voice")?.value || "";
    if (!text) { setFeedback("tts-feedback", "Digite algum texto para ouvir a amostra."); return; }
    const button = $("#tts-preview-btn");
    if (button) { button.disabled = true; button.innerHTML = '<svg class="icon"><use href="#i-clock"></use></svg> Gerando amostra…'; }
    try {
      const response = await fetch("/api/studio/tts/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, tts_voice: voice }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      const audio = $("#tts-preview-player");
      audio.src = body.url; audio.hidden = false; audio.play().catch(() => {});
      showToast("Amostra pronta.");
    } catch (error) { setFeedback("tts-feedback", error.message || String(error)); showToast(error.message || "Falha na amostra.", "error"); }
    finally { if (button) { button.disabled = false; button.innerHTML = '<svg class="icon"><use href="#i-play"></use></svg> Ouvir amostra'; } }
  }

  async function analyzePerformance() {
    const input = $("#performance-file");
    if (!input?.files?.length) { setFeedback("performance-feedback", "Escolha um CSV antes de analisar."); return; }
    const data = new FormData(); data.append("file", input.files[0]); data.append("use_ai", $("#performance-use-ai")?.checked ? "true" : "false");
    setFeedback("performance-feedback", "Lendo métricas…");
    try {
      const response = await fetch("/api/analytics/performance", { method: "POST", body: data });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      setFeedback("performance-feedback", `${body.valid_row_count || 0} linha(s) analisada(s).`, true);
      $("#performance-summary").textContent = body.summary || "Análise concluída.";
      renderRecommendations(body.recommendations || []);
      showToast("Recomendações atualizadas.");
    } catch (error) { setFeedback("performance-feedback", error.message || String(error)); showToast(error.message || "Falha na análise.", "error"); }
  }

  function renderRecommendations(items) {
    const node = $("#recommendations");
    if (!node) return;
    if (!items.length) { node.innerHTML = '<div class="empty-state compact-empty"><p>Nenhuma recomendação encontrada.</p></div>'; return; }
    node.innerHTML = items.map((item, index) => `<div class="recommendation"><span class="recommendation-rank">0${index + 1}</span><span class="recommendation-copy"><strong>${escapeHtml(item.theme)}</strong><span>${escapeHtml(item.why || item.next_video || "")}</span><small>${escapeHtml(item.evidence || "")}</small></span><span class="recommendation-score">${Math.round(Number(item.score) || 0)}</span></div>`).join("");
  }

  async function analyzeRetention() {
    const input = $("#retention-file");
    if (!input?.files?.length) { setFeedback("retention-feedback", "Escolha um relatório JSON antes de analisar."); return; }
    const data = new FormData(); data.append("file", input.files[0]);
    setFeedback("retention-feedback", "Calculando sinais…");
    try {
      const response = await fetch("/api/analytics/retention", { method: "POST", body: data });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      setFeedback("retention-feedback", "Perfil aplicado ao pipeline.", true);
      renderRetention(body); showToast("Loop de retenção aplicado.");
    } catch (error) { setFeedback("retention-feedback", error.message || String(error)); showToast(error.message || "Falha no loop de retenção.", "error"); }
  }

  function renderRetention(body) {
    const node = $("#retention-result");
    if (!node) return;
    const duration = body.recommended_clip_duration_sec ? `${body.recommended_clip_duration_sec}s` : "—";
    const windows = (body.post_windows || []).slice(0, 2).map((item) => item.label).join(" · ") || "sem amostra suficiente";
    const magnets = (body.follower_magnets || []).slice(0, 2).map((item) => `#${item.topic}`).join(" · ") || "sem sinal ainda";
    node.innerHTML = `<div class="signal-grid"><div class="signal-card"><span>Duração sugerida</span><strong>${escapeHtml(duration)}</strong></div><div class="signal-card"><span>Melhor janela</span><strong>${escapeHtml(windows)}</strong></div><div class="signal-card"><span>Ímãs de seguidores</span><strong>${escapeHtml(magnets)}</strong></div></div><div class="signal-note">${escapeHtml(body.summary || "Perfil calculado.")}<br /><code>data/growth_profile.json</code> atualizado.</div>`;
  }

  function updateYoutubeFiles() {
    const input = $("#youtube-files");
    state.youtubeFiles = Array.from(input?.files || []);
    const label = $("#youtube-file-label");
    if (label) label.textContent = state.youtubeFiles.length ? `${state.youtubeFiles.length} arquivos selecionados` : "Escolher 5 vídeos + 5 legendas";
    const videos = state.youtubeFiles.filter((file) => file.name.toLowerCase().endsWith(".mp4")).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    const captions = new Map(state.youtubeFiles.filter((file) => file.name.toLowerCase().endsWith(".txt")).map((file) => [file.name.replace(/\.txt$/i, "").toLowerCase(), file]));
    const rows = videos.map((video, index) => { const caption = captions.get(video.name.replace(/\.mp4$/i, "").toLowerCase()); return `<div class="batch-row"><span class="batch-index">0${index + 1}</span><div><strong>${escapeHtml(video.name)}</strong><small>${caption ? escapeHtml(caption.name) : "TXT correspondente não encontrado"}</small></div><span class="${caption ? "status-text" : "status-text error"}"><svg class="icon icon-xs"><use href="#i-${caption ? "check" : "info"}"></use></svg></span></div>`; });
    const node = $("#youtube-batch-list");
    if (node) node.innerHTML = rows.length ? rows.join("") : '<div class="empty-state compact-empty"><svg class="icon"><use href="#i-file"></use></svg><p>Nenhum lote selecionado.</p><small>Adicione os 10 arquivos para visualizar a sequência.</small></div>';
    renderSchedulePreview();
  }

  function renderSchedulePreview() {
    const node = $("#schedule-preview");
    if (!node) return;
    const time = $("#youtube-time")?.value || "07:00";
    const [hour = "07", minute = "00"] = time.split(":");
    const days = ["amanhã", "+2 dias", "+3 dias", "+4 dias", "+5 dias"];
    node.innerHTML = days.map((day, index) => `<div class="schedule-day"><span>${index + 1}. publicação ${day}</span><strong>${escapeHtml(`${hour.padStart(2, "0")}:${(minute || "00").padStart(2, "0")}`)}</strong></div>`).join("");
  }

  async function publishYoutube() {
    const files = state.youtubeFiles;
    if (files.length !== 10) { setFeedback("youtube-feedback", "Selecione exatamente 5 MP4 e 5 TXT."); return; }
    const credentials = $("#youtube-credentials")?.files?.[0];
    if (!credentials) { setFeedback("youtube-feedback", "Selecione a credencial OAuth JSON para continuar."); return; }
    const data = new FormData(); files.forEach((file) => data.append("files", file)); data.append("client_secrets", credentials); data.append("schedule_time", $("#youtube-time")?.value || "07:00"); data.append("timezone", $("#youtube-timezone")?.value || "America/Campo_Grande"); data.append("made_for_kids", $("#youtube-kids")?.checked ? "true" : "false");
    setFeedback("youtube-feedback", "Enviando o lote para a fila…");
    try {
      const response = await fetch("/api/publish/youtube", { method: "POST", body: data });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readApiError(body, response.statusText));
      setBusy(true); appendLog(body.message || "Lote do YouTube enfileirado."); setFeedback("youtube-feedback", body.message || "Lote enfileirado.", true); showToast(body.message || "Lote enfileirado.");
    } catch (error) { setFeedback("youtube-feedback", error.message || String(error)); showToast(error.message || "Não foi possível enfileirar o lote.", "error"); }
  }

  async function copyText(text, button) {
    try { await navigator.clipboard.writeText(text); showToast("Caminho copiado."); if (button) { const before = button.innerHTML; button.textContent = "Copiado"; window.setTimeout(() => { button.innerHTML = before; }, 1300); } } catch (_) { showToast("Não foi possível copiar.", "error"); }
  }

  function boot() {
    setupNavigation();
    setupMobileNav();
    setupTheme();
    setupSources();
    setupFormListeners();
    populateVoices();
    connectProgress();
    syncSnapshot();
    loadRuns();
    loadPlaylist();
  }

  boot();
})();
