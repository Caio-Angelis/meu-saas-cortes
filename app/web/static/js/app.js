/**
 * Cortes Virais — web local: formulário, SSE, playlist (pendente/publicado/descartado).
 */

(function () {
  const form = document.getElementById("pipeline-form");
  const submitBtn = document.getElementById("submit-btn");
  const addPlaylistBtn = document.getElementById("add-playlist-btn");
  const processPlaylistBtn = document.getElementById("process-playlist-btn");
  const refreshPlaylistBtn = document.getElementById("refresh-playlist");
  const formError = document.getElementById("form-error");
  const queueHint = document.getElementById("queue-hint");
  const progressBar = document.getElementById("progress-bar");
  const progressBarWrap = document.getElementById("progress-bar-wrap");
  const progressPct = document.getElementById("progress-pct");
  const progressLabel = document.getElementById("progress-label");
  const statusMessage = document.getElementById("status-message");
  const runsTbody = document.getElementById("runs-tbody");
  const playlistTbody = document.getElementById("playlist-tbody");
  const refreshRunsBtn = document.getElementById("refresh-runs");
  const dubEn = document.getElementById("dub-en");
  const dubPt = document.getElementById("dub-pt");

  let eventSource = null;
  let jobRunning = false;
  let playlistPollTimer = null;

  const WORKFLOW_LABEL = {
    pendente: "Pendente",
    publicado: "Publicado",
    descartado: "Descartado",
  };

  const PIPELINE_LABEL = {
    idle: "—",
    queued: "Na fila",
    running: "Rodando",
    done: "Pronto",
    error: "Erro",
  };

  function setProgress(frac, message) {
    const pct = Math.round(Math.max(0, Math.min(1, frac)) * 100);
    progressBar.style.width = pct + "%";
    progressBarWrap.setAttribute("aria-valuenow", String(pct));
    progressPct.textContent = pct + "%";
    if (message) statusMessage.textContent = message;
  }

  function setFormBusy(busy) {
    jobRunning = busy;
    submitBtn.disabled = busy;
    addPlaylistBtn.disabled = busy;
    processPlaylistBtn.disabled = busy;
    progressLabel.textContent = busy ? "Processando…" : "Aguardando…";
    form.querySelectorAll("input, textarea, select").forEach((el) => {
      el.disabled = busy;
    });
  }

  function showError(msg) {
    formError.textContent = msg;
    formError.classList.remove("hidden");
  }

  function clearError() {
    formError.textContent = "";
    formError.classList.add("hidden");
  }

  function showQueueHint(msg, redis) {
    if (!msg) {
      queueHint.classList.add("hidden");
      return;
    }
    const redisNote = redis ? "Fila: Redis/RQ" : "Fila: thread local (defina REDIS_URL + web_worker.py para RQ)";
    queueHint.textContent = msg + " · " + redisNote;
    queueHint.classList.remove("hidden");
  }

  function buildFormData() {
    const urls = document.getElementById("urls").value.trim();
    const files = document.getElementById("files").files;
    const fd = new FormData();
    fd.append("urls", urls);
    fd.append("lang", document.getElementById("lang").value);
    fd.append("position", document.getElementById("position").value);
    fd.append("hook_text", document.getElementById("hook-text").value);
    fd.append("outro_text", document.getElementById("outro-text").value);
    fd.append("clip_start", document.getElementById("clip-start").value);
    fd.append("clip_end", document.getElementById("clip-end").value);
    if (dubEn.checked) fd.append("dub_en", "true");
    if (dubPt.checked) fd.append("dub_pt", "true");
    if (document.getElementById("export-zip").checked) fd.append("export_zip", "true");
    for (const f of files) {
      fd.append("files", f);
    }
    return { fd, urls, files };
  }

  function validateInput(urls, files) {
    if (!urls && (!files || !files.length)) {
      showError("Informe ao menos uma URL ou um arquivo de vídeo.");
      return false;
    }
    return true;
  }

  function handleSseEvent(data) {
    if (!data || !data.type) return;

    switch (data.type) {
      case "progress":
        setProgress(data.frac ?? 0, data.message || "");
        break;
      case "status":
        setProgress(data.frac ?? 0, data.message || "");
        setFormBusy(true);
        break;
      case "log":
        if (data.message) statusMessage.textContent = data.message;
        break;
      case "done":
        if (playlistPollTimer) {
          clearInterval(playlistPollTimer);
          playlistPollTimer = null;
        }
        setProgress(1, data.message || "Concluído.");
        setFormBusy(false);
        loadRuns();
        loadPlaylist();
        break;
      case "error":
        if (playlistPollTimer) {
          clearInterval(playlistPollTimer);
          playlistPollTimer = null;
        }
        showError(data.message || "Erro no processamento.");
        setFormBusy(false);
        progressLabel.textContent = "Erro";
        loadPlaylist();
        break;
      case "idle":
        if (!jobRunning) setProgress(0, "");
        break;
      case "playlist_refresh":
        loadPlaylist();
        break;
      default:
        break;
    }
  }

  function connectProgress() {
    if (eventSource) return;
    eventSource = new EventSource("/api/progress");

    eventSource.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        handleSseEvent(data);
      } catch (_) {
        /* heartbeat */
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      eventSource = null;
      setTimeout(connectProgress, 3000);
    };
  }

  async function loadRuns() {
    try {
      const res = await fetch("/api/runs");
      if (!res.ok) throw new Error("Falha ao carregar execuções.");
      const body = await res.json();
      renderRuns(body.items || []);
    } catch (e) {
      runsTbody.innerHTML =
        '<tr><td colspan="3" class="px-5 py-6 text-red-400 text-center">' +
        escapeHtml(e.message) +
        "</td></tr>";
    }
  }

  async function pollActiveProgress() {
    try {
      const res = await fetch("/api/playlist/active");
      if (!res.ok) return;
      const body = await res.json();
      if (body.active) {
        setFormBusy(true);
        setProgress(body.progress ?? 0, body.message || "Processando playlist…");
        if (!playlistPollTimer) {
          playlistPollTimer = setInterval(() => {
            pollActiveProgress();
            loadPlaylist();
          }, 2000);
        }
      } else if (playlistPollTimer) {
        clearInterval(playlistPollTimer);
        playlistPollTimer = null;
        if (jobRunning) setFormBusy(false);
      }
    } catch (_) {
      /* ignore */
    }
  }

  async function loadPlaylist() {
    try {
      const res = await fetch("/api/playlist");
      if (!res.ok) throw new Error("Falha ao carregar playlist.");
      const body = await res.json();
      renderPlaylist(body.items || [], body.redis);
      await pollActiveProgress();
    } catch (e) {
      playlistTbody.innerHTML =
        '<tr><td colspan="5" class="px-5 py-6 text-red-400 text-center">' +
        escapeHtml(e.message) +
        "</td></tr>";
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function shortSource(src, max = 48) {
    if (!src) return "—";
    if (src.length <= max) return src;
    return src.slice(0, max - 1) + "…";
  }

  function workflowClass(status) {
    if (status === "publicado") return "text-progress";
    if (status === "descartado") return "text-slate-500 line-through";
    return "text-amber-400";
  }

  function renderPlaylist(items, redis) {
    if (!items.length) {
      playlistTbody.innerHTML =
        '<tr><td colspan="5" class="px-5 py-6 text-slate-500 text-center">Playlist vazia — adicione URLs ou arquivos.</td></tr>';
      return;
    }

    playlistTbody.innerHTML = items
      .map((row) => {
        const pct = Math.round((row.progress || 0) * 100);
        const actions = [];
        if (row.can_mark_workflow) {
          actions.push(
            `<button type="button" class="text-progress text-xs hover:underline mark-published" data-id="${row.id}">Publicado</button>`
          );
          actions.push(
            `<button type="button" class="text-slate-400 text-xs hover:underline mark-discarded ml-2" data-id="${row.id}">Descartar</button>`
          );
        }
        if (row.outputs && row.outputs.length) {
          actions.push(
            `<span class="text-slate-500 text-xs ml-2">${row.outputs.length} clipe(s)</span>`
          );
        }
        return `
      <tr class="hover:bg-surface/60" data-id="${row.id}">
        <td class="px-5 py-3 text-xs font-mono text-slate-300" title="${escapeHtml(row.source)}">${escapeHtml(shortSource(row.source))}</td>
        <td class="px-5 py-3 ${workflowClass(row.workflow_status)}">${escapeHtml(WORKFLOW_LABEL[row.workflow_status] || row.workflow_status)}</td>
        <td class="px-5 py-3 text-slate-400">${escapeHtml(PIPELINE_LABEL[row.pipeline_status] || row.pipeline_status)}</td>
        <td class="px-5 py-3 font-mono text-xs">${pct}</td>
        <td class="px-5 py-3 text-right">${actions.join("")}</td>
      </tr>`;
      })
      .join("");

    playlistTbody.querySelectorAll(".mark-published").forEach((btn) => {
      btn.addEventListener("click", () => patchWorkflow(btn.dataset.id, "publicado"));
    });
    playlistTbody.querySelectorAll(".mark-discarded").forEach((btn) => {
      btn.addEventListener("click", () => patchWorkflow(btn.dataset.id, "descartado"));
    });

    if (redis !== undefined) {
      showQueueHint(null);
    }
  }

  async function patchWorkflow(id, status) {
    clearError();
    try {
      const res = await fetch(`/api/playlist/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail || res.statusText);
      }
      await loadPlaylist();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  function renderRuns(items) {
    if (!items.length) {
      runsTbody.innerHTML =
        '<tr><td colspan="3" class="px-5 py-6 text-slate-500 text-center">Nenhum clipe em resultados/ ainda.</td></tr>';
      return;
    }
    runsTbody.innerHTML = items
      .map(
        (row) => `
      <tr class="hover:bg-surface/60">
        <td class="px-5 py-3 font-mono text-xs text-slate-200">${escapeHtml(row.name)}</td>
        <td class="px-5 py-3 text-slate-400">${escapeHtml(row.duration)}</td>
        <td class="px-5 py-3 text-right">
          <button type="button" class="text-accent text-xs hover:underline copy-path" data-path="${escapeHtml(row.path)}">Copiar caminho</button>
        </td>
      </tr>`
      )
      .join("");

    runsTbody.querySelectorAll(".copy-path").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const path = btn.getAttribute("data-path");
        try {
          await navigator.clipboard.writeText(path);
          btn.textContent = "Copiado!";
          setTimeout(() => {
            btn.textContent = "Copiar caminho";
          }, 1500);
        } catch {
          showError("Não foi possível copiar para a área de transferência.");
        }
      });
    });
  }

  async function postPlaylist(addOnly) {
    clearError();
    const { fd, urls, files } = buildFormData();
    if (!validateInput(urls, files)) return;

    const endpoint = addOnly ? "/api/playlist" : "/api/jobs";
    if (!addOnly) setFormBusy(true);
    if (!addOnly) setProgress(0, "Enviando job…");

    try {
      const res = await fetch(endpoint, { method: "POST", body: fd });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = body.detail;
        const msg =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
              : body.message || res.statusText;
        throw new Error(msg);
      }
      statusMessage.textContent = body.message || "OK";
      showQueueHint(body.message, body.redis);
      await loadPlaylist();
      if (!addOnly && body.message) {
        /* progresso via SSE */
      }
    } catch (err) {
      showError(err.message || String(err));
      if (!addOnly) setFormBusy(false);
    }
  }

  async function processPlaylist() {
    clearError();
    setFormBusy(true);
    setProgress(0, "Enfileirando playlist…");
    try {
      const res = await fetch("/api/playlist/process", { method: "POST", body: new FormData() });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          typeof body.detail === "string" ? body.detail : body.message || res.statusText
        );
      }
      showQueueHint(body.message, body.redis);
      statusMessage.textContent = body.message;
      await loadPlaylist();
    } catch (err) {
      showError(err.message || String(err));
      setFormBusy(false);
    }
  }

  dubEn.addEventListener("change", () => {
    if (dubEn.checked) dubPt.checked = false;
  });
  dubPt.addEventListener("change", () => {
    if (dubPt.checked) dubEn.checked = false;
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    postPlaylist(false);
  });

  addPlaylistBtn.addEventListener("click", () => postPlaylist(true));
  processPlaylistBtn.addEventListener("click", processPlaylist);
  refreshPlaylistBtn.addEventListener("click", loadPlaylist);
  refreshRunsBtn.addEventListener("click", loadRuns);

  connectProgress();
  loadRuns();
  loadPlaylist();
})();
