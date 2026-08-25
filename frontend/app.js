const $ = (q) => document.querySelector(q);
const STORAGE_KEY = "carais_api_url";

function getApiBase() {
  return (localStorage.getItem(STORAGE_KEY) || "").replace(/\/$/, "");
}

function fmtDate(s) {
  if (!s) return "-";
  return new Date(s).toLocaleString();
}

async function api(path, options = {}) {
  const base = getApiBase();
  if (!base) throw new Error("Primero configura la URL del backend Render.");
  const r = await fetch(`${base}${path}`, options);
  let body = {};
  try { body = await r.json(); } catch {}
  if (!r.ok) throw new Error(body.detail || "Error de servidor");
  return body;
}

async function checkHealth() {
  const health = $("#health");
  const base = getApiBase();
  if (!base) {
    health.textContent = "API sin configurar";
    return;
  }
  health.textContent = "Conectando...";
  try {
    const h = await api("/api/health");
    health.textContent = `API activa · v${h.version}`;
  } catch (err) {
    health.textContent = "API no disponible";
  }
}

$("#apiUrl").value = getApiBase();
$("#apiForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("#apiUrl").value.trim().replace(/\/$/, "");
  localStorage.setItem(STORAGE_KEY, value);
  const box = $("#apiResult");
  box.classList.remove("hidden");
  box.textContent = "Probando conexión...";
  try {
    const h = await api("/api/health");
    box.textContent = `Conexión correcta con per_carAis v${h.version}.`;
    await Promise.all([loadPeople(), loadLogs()]);
  } catch (err) {
    box.textContent = `No se pudo conectar: ${err.message}`;
  }
  await checkHealth();
});

$("#enrollForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = $("#enrollResult");
  box.classList.remove("hidden");
  box.textContent = "Procesando foto...";
  try {
    const res = await api("/api/people", { method: "POST", body: new FormData(e.target) });
    box.textContent = `${res.name} registrada correctamente.`;
    e.target.reset();
    await loadPeople();
  } catch (err) {
    box.textContent = err.message;
  }
});

$("#recognizeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = $("#recognitionResult");
  box.className = "recognition empty";
  box.innerHTML = '<div class="big-icon">⌁</div><strong>Analizando...</strong><span>Si Render estaba dormido, la primera consulta puede demorar.</span>';
  try {
    const res = await api("/api/recognize", { method: "POST", body: new FormData(e.target) });
    if (res.authorized) {
      box.className = "recognition allowed";
      box.innerHTML = `<div class="big-icon">✓</div><strong>ACCESO AUTORIZADO</strong><span>${res.person.name}</span><span>Similitud orientativa: ${res.similarity}% · distancia: ${res.distance}</span>`;
    } else {
      box.className = "recognition denied";
      box.innerHTML = `<div class="big-icon">×</div><strong>ACCESO DENEGADO</strong><span>Persona no reconocida sobre el umbral configurado.</span><span>Mejor candidato: ${res.best_candidate} · similitud orientativa: ${res.similarity}%</span>`;
    }
    await loadLogs();
  } catch (err) {
    box.className = "recognition denied";
    box.innerHTML = `<div class="big-icon">!</div><strong>No se pudo analizar</strong><span>${err.message}</span>`;
  }
});

async function loadPeople() {
  const target = $("#peopleList");
  try {
    const rows = await api("/api/people");
    target.innerHTML = rows.length ? `<table><thead><tr><th>Nombre</th><th>ID / RUT</th><th>Empresa</th><th>Estado</th><th>Registro</th></tr></thead><tbody>${rows.map(r => `<tr><td><strong>${r.name}</strong></td><td>${r.external_id || "-"}</td><td>${r.company || "-"}</td><td><span class="badge ${r.active ? "ok" : "no"}">${r.active ? "Activo" : "Inactivo"}</span></td><td>${fmtDate(r.created_at)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">Todavía no hay personas registradas.</p>';
  } catch (err) {
    target.innerHTML = `<p class="muted">${err.message}</p>`;
  }
}

async function loadLogs() {
  const target = $("#logsList");
  try {
    const rows = await api("/api/logs");
    target.innerHTML = rows.length ? `<table><thead><tr><th>Fecha</th><th>Resultado</th><th>Persona</th><th>Similitud</th><th>Distancia</th></tr></thead><tbody>${rows.map(r => `<tr><td>${fmtDate(r.tested_at)}</td><td><span class="badge ${r.authorized ? "ok" : "no"}">${r.authorized ? "Autorizado" : "Denegado"}</span></td><td>${r.matched_name || "Desconocido"}</td><td>${Number(r.similarity).toFixed(1)}%</td><td>${Number(r.distance).toFixed(4)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">Todavía no hay pruebas realizadas.</p>';
  } catch (err) {
    target.innerHTML = `<p class="muted">${err.message}</p>`;
  }
}

checkHealth();
if (getApiBase()) {
  loadPeople();
  loadLogs();
}
