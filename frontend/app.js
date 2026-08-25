const $ = (q) => document.querySelector(q);
const $$ = (q) => [...document.querySelectorAll(q)];
const STORAGE_KEY = "carais_api_url";

let recordedVideoFile = null;
let recordStream = null;

function defaultApiBase() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored) return stored.replace(/\/$/, "");
  if (!location.hostname.includes("github.io")) return location.origin;
  return "https://dev-prueba1.conaf.cl";
}

function getApiBase() {
  return defaultApiBase().replace(/\/$/, "");
}

async function api(path, options = {}) {
  const base = getApiBase();
  const response = await fetch(`${base}${path}`, options);
  let body = {};
  try { body = await response.json(); } catch {}
  if (!response.ok) throw new Error(body.detail || `Error ${response.status}`);
  return body;
}

function setBusy(button, busy, text = "Procesando…") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = text;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("es-CL", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(new Date(value));
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function checkHealth() {
  const health = $("#health");
  health.textContent = "Conectando…";
  health.className = "status";
  try {
    const data = await api("/api/health");
    health.textContent = `Servidor activo · ${data.people} personas`;
    health.className = "status ok";
    $("#peopleCount strong").textContent = data.people;
    return data;
  } catch (error) {
    health.textContent = "Servidor sin conexión";
    health.className = "status no";
    $("#peopleCount strong").textContent = "—";
    return null;
  }
}

function showView(name) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === name));
  $$(".view").forEach((view) => view.classList.remove("active"));
  $(`#view-${name}`).classList.add("active");
  if (name === "people") loadPeople();
  if (name === "history") loadHistory();
}

$$(".tab").forEach((tab) => tab.addEventListener("click", () => showView(tab.dataset.view)));
$("#health").addEventListener("click", checkHealth);

$("#apiUrl").value = getApiBase();
$("#apiForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const box = $("#apiResult");
  localStorage.setItem(STORAGE_KEY, $("#apiUrl").value.trim().replace(/\/$/, ""));
  box.classList.remove("hidden");
  box.textContent = "Probando conexión…";
  const health = await checkHealth();
  box.textContent = health
    ? `Conexión correcta con CarAis v${health.version}.`
    : "No fue posible conectar con el servidor. Revisa URL, HTTPS y acceso de red.";
});

function previewImageInput(input, image, wrapper) {
  const file = input.files?.[0];
  if (!file) {
    wrapper.classList.add("hidden");
    return null;
  }
  image.src = URL.createObjectURL(file);
  wrapper.classList.remove("hidden");
  return file;
}

$("#identifyPhoto").addEventListener("change", () => {
  const file = previewImageInput($("#identifyPhoto"), $("#photoPreview"), $("#photoPreviewWrap"));
  $("#identifyPhotoButton").classList.toggle("hidden", !file);
});

$("#identifyVideo").addEventListener("change", async () => {
  const file = $("#identifyVideo").files?.[0];
  recordedVideoFile = null;
  if (!file) return;
  try {
    await setVideoPreview(file);
  } catch (error) {
    showRecognitionError(error.message);
  }
});

async function videoDuration(file) {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      resolve(video.duration || 0);
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("No fue posible leer el video."));
    };
    video.src = url;
  });
}

async function setVideoPreview(file) {
  const duration = await videoDuration(file);
  if (duration > 2.8) {
    $("#identifyVideo").value = "";
    throw new Error(`El video dura ${duration.toFixed(1)} s. Usa un clip de máximo 2 segundos.`);
  }
  const video = $("#videoPreview");
  video.src = URL.createObjectURL(file);
  $("#videoPreviewWrap").classList.remove("hidden");
  $("#identifyVideoButton").classList.remove("hidden");
}

function renderRecognition(data) {
  const box = $("#recognitionResult");
  const listCard = $("#recognizedListCard");
  const list = $("#recognizedList");
  const recognized = data.recognized_people || (data.person ? [{ person: data.person, similarity: data.similarity }] : []);

  if (recognized.length) {
    box.className = "result-panel success";
    const first = recognized[0];
    box.innerHTML = `
      <div class="result-symbol">✓</div>
      <div><strong>${recognized.length === 1 ? escapeHtml(first.person.name) : `${recognized.length} personas identificadas`}</strong>
      <span>${recognized.length === 1 ? `Coincidencia ${Number(first.similarity).toFixed(1)}%` : "Coincidencias encontradas en la base registrada"}</span></div>`;
    list.innerHTML = recognized.map((item) => `
      <div class="person-result">
        <div class="avatar">${escapeHtml(item.person.name).charAt(0).toUpperCase()}</div>
        <div><strong>${escapeHtml(item.person.name)}</strong><span>${escapeHtml(item.person.external_id || item.person.company || "Sin identificador")}</span></div>
        <b>${Number(item.similarity).toFixed(1)}%</b>
      </div>`).join("");
    listCard.classList.remove("hidden");
  } else {
    box.className = "result-panel warning";
    box.innerHTML = `
      <div class="result-symbol">?</div>
      <div><strong>Persona no identificada</strong><span>Se detectó rostro, pero no hubo una coincidencia suficientemente clara.</span></div>`;
    listCard.classList.add("hidden");
  }
}

function showRecognitionLoading(text) {
  const box = $("#recognitionResult");
  box.className = "result-panel loading";
  box.innerHTML = `<div class="spinner"></div><div><strong>${text}</strong><span>Comparando con la base registrada…</span></div>`;
  $("#recognizedListCard").classList.add("hidden");
}

function showRecognitionError(message) {
  const box = $("#recognitionResult");
  box.className = "result-panel error";
  box.innerHTML = `<div class="result-symbol">!</div><div><strong>No se pudo identificar</strong><span>${escapeHtml(message)}</span></div>`;
  $("#recognizedListCard").classList.add("hidden");
}

$("#identifyPhotoButton").addEventListener("click", async () => {
  const file = $("#identifyPhoto").files?.[0];
  if (!file) return;
  const button = $("#identifyPhotoButton");
  const form = new FormData();
  form.append("photo", file);
  showRecognitionLoading("Analizando foto");
  setBusy(button, true);
  try {
    renderRecognition(await api("/api/recognize", { method: "POST", body: form }));
    await loadHistory(false);
  } catch (error) {
    showRecognitionError(error.message);
  } finally {
    setBusy(button, false);
  }
});

$("#identifyVideoButton").addEventListener("click", async () => {
  const file = recordedVideoFile || $("#identifyVideo").files?.[0];
  if (!file) return;
  const button = $("#identifyVideoButton");
  const form = new FormData();
  form.append("video", file, file.name || "capture.webm");
  showRecognitionLoading("Analizando video");
  setBusy(button, true);
  try {
    renderRecognition(await api("/api/recognize-video", { method: "POST", body: form }));
    await loadHistory(false);
  } catch (error) {
    showRecognitionError(error.message);
  } finally {
    setBusy(button, false);
  }
});

$("#recordVideoButton").addEventListener("click", async () => {
  const button = $("#recordVideoButton");
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showRecognitionError("Este navegador no permite grabación directa. Usa ‘elegir un video’.");
    return;
  }

  try {
    setBusy(button, true, "Grabando… 2 s");
    recordStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 720 }, height: { ideal: 720 } },
      audio: false
    });

    const chunks = [];
    const preferredType = MediaRecorder.isTypeSupported("video/webm;codecs=vp8") ? "video/webm;codecs=vp8" : "video/webm";
    const recorder = new MediaRecorder(recordStream, { mimeType: preferredType });
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.start();

    await new Promise((resolve) => setTimeout(resolve, 2000));
    const stopped = new Promise((resolve) => recorder.addEventListener("stop", resolve, { once: true }));
    recorder.stop();
    await stopped;
    recordStream.getTracks().forEach((track) => track.stop());

    recordedVideoFile = new File([new Blob(chunks, { type: preferredType })], `carais-${Date.now()}.webm`, { type: preferredType });
    $("#identifyVideo").value = "";
    await setVideoPreview(recordedVideoFile);
  } catch (error) {
    recordStream?.getTracks().forEach((track) => track.stop());
    showRecognitionError(error.name === "NotAllowedError" ? "No se autorizó el acceso a la cámara." : error.message);
  } finally {
    setBusy(button, false);
  }
});

function renderRegisterPreviews() {
  const container = $("#registerPreview");
  const files = [$("#registerPhoto1"), $("#registerPhoto2"), $("#registerPhoto3")]
    .map((input) => input.files?.[0])
    .filter(Boolean);
  container.innerHTML = files.map((file) => `<img src="${URL.createObjectURL(file)}" alt="Muestra facial" />`).join("");
}

[$("#registerPhoto1"), $("#registerPhoto2"), $("#registerPhoto3")].forEach((input) => input.addEventListener("change", renderRegisterPreviews));

$("#registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#registerButton");
  const result = $("#registerResult");
  const photo1 = $("#registerPhoto1").files?.[0];
  if (!photo1) return;

  const form = new FormData();
  form.append("name", $("#personName").value.trim());
  form.append("external_id", $("#personExternalId").value.trim());
  form.append("company", $("#personCompany").value.trim());
  form.append("photo", photo1);
  const photo2 = $("#registerPhoto2").files?.[0];
  const photo3 = $("#registerPhoto3").files?.[0];
  if (photo2) form.append("photo_2", photo2);
  if (photo3) form.append("photo_3", photo3);

  result.classList.remove("hidden");
  result.className = "inline-result";
  result.textContent = "Guardando y preparando muestras faciales…";
  setBusy(button, true, "Guardando…");
  try {
    const data = await api("/api/people", { method: "POST", body: form });
    result.className = "inline-result success-inline";
    result.textContent = `${data.name} fue registrado correctamente con ${data.samples} muestra(s) facial(es).`;
    event.target.reset();
    $("#registerPreview").innerHTML = "";
    await checkHealth();
    await loadPeople(false);
  } catch (error) {
    result.className = "inline-result error-inline";
    result.textContent = error.message;
  } finally {
    setBusy(button, false);
  }
});

async function loadPeople(showLoading = true) {
  const container = $("#peopleList");
  const empty = $("#peopleEmpty");
  if (showLoading) container.innerHTML = '<div class="empty-state">Cargando personas…</div>';
  try {
    const people = await api("/api/people");
    empty.classList.toggle("hidden", people.length > 0);
    container.innerHTML = people.map((person) => `
      <article class="person-card">
        <div class="avatar large">${escapeHtml(person.name).charAt(0).toUpperCase()}</div>
        <div class="person-card-main"><strong>${escapeHtml(person.name)}</strong><span>${escapeHtml(person.external_id || "Sin identificador")}</span><small>${escapeHtml(person.company || "Sin área / empresa")}</small></div>
        <div class="sample-count"><b>${person.samples}</b><span>muestra${person.samples === 1 ? "" : "s"}</span></div>
      </article>`).join("");
  } catch (error) {
    container.innerHTML = `<div class="empty-state error-text">${escapeHtml(error.message)}</div>`;
  }
}

async function loadHistory(showLoading = true) {
  const container = $("#historyList");
  if (showLoading) container.innerHTML = "Cargando historial…";
  try {
    const logs = await api("/api/logs");
    if (!logs.length) {
      container.innerHTML = '<div class="empty-state">Todavía no hay identificaciones registradas.</div>';
      return;
    }
    container.innerHTML = `<table>
      <thead><tr><th>Fecha</th><th>Resultado</th><th>Persona</th><th>Similitud</th><th>Origen</th></tr></thead>
      <tbody>${logs.map((log) => `
        <tr>
          <td>${formatDate(log.tested_at)}</td>
          <td><span class="pill ${log.authorized ? "ok-pill" : "no-pill"}">${log.authorized ? "Identificado" : "Sin coincidencia"}</span></td>
          <td>${escapeHtml(log.matched_name || "—")}</td>
          <td>${Number(log.similarity || 0).toFixed(1)}%</td>
          <td>${log.source_type === "video" ? "Video" : "Foto"}</td>
        </tr>`).join("")}</tbody>
    </table>`;
  } catch (error) {
    container.innerHTML = `<div class="empty-state error-text">${escapeHtml(error.message)}</div>`;
  }
}

$("#refreshPeople").addEventListener("click", () => loadPeople());
$("#refreshHistory").addEventListener("click", () => loadHistory());

checkHealth();
loadPeople(false);
