const $ = (q) => document.querySelector(q);
const STORAGE_KEY = "carais_api_url";

let groupImage = null;
let lastResult = null;

function getApiBase() {
  return (localStorage.getItem(STORAGE_KEY) || "").replace(/\/$/, "");
}

async function api(path, options = {}) {
  const base = getApiBase();
  if (!base) throw new Error("Primero configura la URL de la API.");
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
    health.className = "status";
    return;
  }

  health.textContent = "Conectando...";
  health.className = "status";

  try {
    const h = await api("/health");
    health.textContent = `API activa · v${h.version}`;
    health.className = "status ok";
  } catch {
    health.textContent = "API no disponible";
    health.className = "status no";
  }
}

function drawGroupImage(bbox = null) {
  const canvas = $("#groupCanvas");
  const placeholder = $("#groupPlaceholder");
  if (!groupImage) {
    canvas.style.display = "none";
    placeholder.style.display = "flex";
    return;
  }

  const maxWidth = 1000;
  const scale = Math.min(1, maxWidth / groupImage.naturalWidth);
  canvas.width = Math.round(groupImage.naturalWidth * scale);
  canvas.height = Math.round(groupImage.naturalHeight * scale);
  canvas.style.display = "block";
  placeholder.style.display = "none";

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(groupImage, 0, 0, canvas.width, canvas.height);

  if (bbox) {
    const [x1, y1, x2, y2] = bbox;
    ctx.lineWidth = Math.max(3, Math.round(4 * scale));
    ctx.strokeStyle = "#16a34a";
    ctx.fillStyle = "rgba(22, 163, 74, 0.14)";
    ctx.strokeRect(x1 * scale, y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale);
    ctx.fillRect(x1 * scale, y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale);
  }
}

function loadFileAsImage(file, callback) {
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    callback(img, url);
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    throw new Error("No fue posible cargar la imagen.");
  };
  img.src = url;
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
    const h = await api("/health");
    box.textContent = `Conexión correcta con per_carAis v${h.version}.`;
  } catch (err) {
    box.textContent = `No se pudo conectar: ${err.message}`;
  }

  await checkHealth();
});

$("#groupFile").addEventListener("change", (e) => {
  const file = e.target.files[0];
  lastResult = null;
  if (!file) {
    groupImage = null;
    drawGroupImage();
    return;
  }

  loadFileAsImage(file, (img) => {
    groupImage = img;
    drawGroupImage();
  });
});

$("#referenceFile").addEventListener("change", (e) => {
  const file = e.target.files[0];
  const img = $("#referencePreview");
  const placeholder = $("#referencePlaceholder");

  if (!file) {
    img.removeAttribute("src");
    img.style.display = "none";
    placeholder.style.display = "flex";
    return;
  }

  const url = URL.createObjectURL(file);
  img.onload = () => {
    img.style.display = "block";
    placeholder.style.display = "none";
  };
  img.src = url;
});

$("#analyzeButton").addEventListener("click", async () => {
  const groupFile = $("#groupFile").files[0];
  const referenceFile = $("#referenceFile").files[0];
  const box = $("#recognitionResult");
  const button = $("#analyzeButton");

  if (!groupFile || !referenceFile) {
    box.className = "recognition denied";
    box.innerHTML = '<div class="big-icon">!</div><strong>Faltan fotografías</strong><span>Selecciona una foto grupal y una foto de referencia.</span>';
    return;
  }

  const form = new FormData();
  form.append("group_file", groupFile);
  form.append("reference_file", referenceFile);

  box.className = "recognition empty";
  box.innerHTML = '<div class="spinner"></div><strong>Analizando fotografías...</strong><span>Buscando la mejor coincidencia entre todos los rostros detectados.</span>';
  button.disabled = true;
  $("#detailsCard").classList.add("hidden");
  drawGroupImage();

  try {
    const res = await api("/faces/compare", { method: "POST", body: form });
    lastResult = res;
    drawGroupImage(res.best_match?.bbox || null);

    const similarityPct = (Number(res.best_match.similarity) * 100).toFixed(1);
    const gapPct = res.similarity_gap == null ? "-" : `${(Number(res.similarity_gap) * 100).toFixed(1)}%`;

    if (res.match_candidate) {
      box.className = "recognition allowed";
      box.innerHTML = `<div class="big-icon">✓</div><strong>Posible coincidencia encontrada</strong><span>Rostro ${res.best_match.face} de ${res.faces_group} · similitud ${similarityPct}%</span><span>Separación respecto del segundo candidato: ${gapPct}</span>`;
    } else {
      box.className = "recognition denied";
      box.innerHTML = `<div class="big-icon">×</div><strong>No hay una coincidencia suficientemente clara</strong><span>Mejor similitud: ${similarityPct}%</span><span>Se analizaron ${res.faces_group} rostros.</span>`;
    }

    renderDetails(res);
  } catch (err) {
    box.className = "recognition denied";
    box.innerHTML = `<div class="big-icon">!</div><strong>No se pudo analizar</strong><span>${err.message}</span>`;
  } finally {
    button.disabled = false;
  }
});

function renderDetails(res) {
  const card = $("#detailsCard");
  const metrics = $("#metrics");
  const matches = $("#matchesList");

  const similarityPct = (Number(res.best_match.similarity) * 100).toFixed(1);
  const gapPct = res.similarity_gap == null ? "-" : `${(Number(res.similarity_gap) * 100).toFixed(1)}%`;

  metrics.innerHTML = `
    <div class="metric"><span>Rostros analizados</span><strong>${res.faces_group}</strong></div>
    <div class="metric"><span>Mejor similitud</span><strong>${similarityPct}%</strong></div>
    <div class="metric"><span>Separación candidatos</span><strong>${gapPct}</strong></div>
    <div class="metric"><span>Resultado</span><strong>${res.match_candidate ? "Candidato" : "Sin coincidencia"}</strong></div>
  `;

  matches.innerHTML = `<table>
    <thead><tr><th>Rostro</th><th>Similitud</th><th>Confianza detección</th><th>Coordenadas</th></tr></thead>
    <tbody>${res.matches.map((m) => `
      <tr class="${m.face === res.best_match.face ? "best-row" : ""}">
        <td><strong>${m.face}${m.face === res.best_match.face ? " · mejor" : ""}</strong></td>
        <td>${(Number(m.similarity) * 100).toFixed(1)}%</td>
        <td>${(Number(m.detection_score) * 100).toFixed(1)}%</td>
        <td>${m.bbox.join(", ")}</td>
      </tr>
    `).join("")}</tbody>
  </table>`;

  card.classList.remove("hidden");
}

checkHealth();
