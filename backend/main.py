from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
FACE_DIR = DATA_DIR / "faces"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = DATA_DIR / "carais.db"

FACE_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

LBPH_THRESHOLD = float(os.getenv("CARAIS_LBPH_THRESHOLD", "65"))
MAX_VIDEO_SECONDS = float(os.getenv("CARAIS_MAX_VIDEO_SECONDS", "2.5"))
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CARAIS_ALLOWED_ORIGINS",
        "https://alfredocespedes-c.github.io,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="per_carAis API", version="1.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
FACE_CASCADE = cv2.CascadeClassifier(CASCADE_PATH)
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".webp"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".3gp"}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                external_id TEXT,
                company TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                image_path TEXT NOT NULL,
                encoding_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_samples (
                id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                image_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(person_id) REFERENCES people(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_log (
                id TEXT PRIMARY KEY,
                tested_at TEXT NOT NULL,
                matched_person_id TEXT,
                matched_name TEXT,
                distance REAL,
                similarity REAL,
                authorized INTEGER NOT NULL,
                threshold REAL NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'photo'
            )
        """)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(access_log)").fetchall()}
        if "source_type" not in cols:
            conn.execute("ALTER TABLE access_log ADD COLUMN source_type TEXT NOT NULL DEFAULT 'photo'")

        # Migra automáticamente la foto principal de versiones anteriores como muestra facial.
        rows = conn.execute("SELECT id, image_path, created_at FROM people").fetchall()
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM person_samples WHERE person_id = ? LIMIT 1", (row["id"],)
            ).fetchone()
            if not exists and row["image_path"]:
                conn.execute(
                    "INSERT INTO person_samples (id, person_id, image_path, created_at) VALUES (?, ?, ?, ?)",
                    (str(uuid.uuid4()), row["id"], row["image_path"], row["created_at"]),
                )
        conn.commit()


init_db()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def save_upload(upload: UploadFile, folder: Path, allowed_extensions: set[str]) -> Path:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Formato no soportado: {ext or 'sin extensión'}.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex}{ext}"
    with target.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return target


def load_gray(path: Path) -> np.ndarray:
    image = cv2.imread(str(path))
    if image is None:
        raise HTTPException(status_code=400, detail="No fue posible leer la imagen.")
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def detect_faces(gray: np.ndarray):
    return list(
        FACE_CASCADE.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(70, 70),
        )
    )


def normalize_face(gray: np.ndarray, rect) -> np.ndarray:
    x, y, w, h = [int(v) for v in rect]
    face = gray[y:y + h, x:x + w]
    face = cv2.equalizeHist(face)
    return cv2.resize(face, (200, 200), interpolation=cv2.INTER_AREA)


def extract_single_face(path: Path) -> np.ndarray:
    gray = load_gray(path)
    faces = detect_faces(gray)
    if len(faces) == 0:
        raise HTTPException(status_code=400, detail="No se detectó ningún rostro en la imagen.")
    if len(faces) > 1:
        raise HTTPException(status_code=400, detail="Se detectó más de un rostro. Usa una foto con una sola persona.")
    return normalize_face(gray, faces[0])


def store_face_sample(person_id: str, upload: UploadFile, person_folder: Path, index: int) -> Path:
    raw_path = save_upload(upload, person_folder, SUPPORTED_IMAGE_EXTENSIONS)
    try:
        face = extract_single_face(raw_path)
        face_path = person_folder / f"face_{index}.png"
        cv2.imwrite(str(face_path), face)
        return face_path
    finally:
        raw_path.unlink(missing_ok=True)


def build_recognizer():
    with db() as conn:
        people = conn.execute(
            "SELECT id, name, external_id, company FROM people WHERE active = 1 ORDER BY created_at"
        ).fetchall()
        sample_rows = conn.execute(
            "SELECT person_id, image_path FROM person_samples ORDER BY created_at"
        ).fetchall()

    if not people:
        raise HTTPException(status_code=400, detail="No hay personas registradas para comparar.")

    labels_by_person = {row["id"]: i for i, row in enumerate(people)}
    people_by_label = {i: row for i, row in enumerate(people)}
    samples: list[np.ndarray] = []
    labels: list[int] = []

    for sample in sample_rows:
        label = labels_by_person.get(sample["person_id"])
        if label is None:
            continue
        path = Path(sample["image_path"])
        if not path.exists():
            continue
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        samples.append(cv2.resize(gray, (200, 200)))
        labels.append(label)

    if not samples:
        raise HTTPException(status_code=400, detail="No hay muestras faciales disponibles para comparar.")

    recognizer = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
    recognizer.train(samples, np.array(labels, dtype=np.int32))
    return recognizer, people_by_label


def recognize_face(face: np.ndarray, recognizer, people_by_label):
    label, confidence = recognizer.predict(face)
    confidence = float(confidence)
    candidate = people_by_label.get(int(label))
    authorized = bool(candidate) and confidence <= LBPH_THRESHOLD
    similarity = max(0.0, min(100.0, 100.0 - confidence))
    return candidate, authorized, confidence, similarity


def log_result(candidate, authorized: bool, confidence: float, similarity: float, source_type: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO access_log (id, tested_at, matched_person_id, matched_name, distance, similarity, authorized, threshold, source_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                now_iso(),
                candidate["id"] if authorized and candidate else None,
                candidate["name"] if authorized and candidate else None,
                confidence,
                similarity,
                1 if authorized else 0,
                LBPH_THRESHOLD,
                source_type,
            ),
        )
        conn.commit()


def result_payload(candidate, authorized: bool, confidence: float, similarity: float, bbox=None):
    return {
        "authorized": authorized,
        "person": {
            "id": candidate["id"],
            "name": candidate["name"],
            "external_id": candidate["external_id"],
            "company": candidate["company"],
        } if authorized and candidate else None,
        "best_candidate": candidate["name"] if candidate else None,
        "distance": round(confidence, 2),
        "similarity": round(similarity, 1),
        "bbox": [int(v) for v in bbox] if bbox is not None else None,
    }


def summarize_results(results: Iterable[dict]):
    results = list(results)
    recognized = [r for r in results if r["authorized"]]
    unique = {}
    for item in recognized:
        pid = item["person"]["id"]
        current = unique.get(pid)
        if current is None or item["distance"] < current["distance"]:
            unique[pid] = item
    ordered = sorted(unique.values(), key=lambda x: x["distance"])
    best = ordered[0] if ordered else (min(results, key=lambda x: x["distance"]) if results else None)
    return ordered, best


@app.get("/")
def index():
    return {"ok": True, "service": "per_carAis", "version": "1.3.0", "docs": "/docs"}


@app.get("/health")
@app.get("/api/health")
def health():
    with db() as conn:
        people_count = conn.execute("SELECT COUNT(*) FROM people WHERE active = 1").fetchone()[0]
        samples_count = conn.execute("SELECT COUNT(*) FROM person_samples").fetchone()[0]
    return {
        "ok": True,
        "service": "per_carAis",
        "version": "1.3.0",
        "engine": "opencv-lbph",
        "match_threshold": LBPH_THRESHOLD,
        "people": people_count,
        "samples": samples_count,
        "supported_image_formats": ["jpg", "jpeg", "jfif", "png", "webp"],
        "supported_video_formats": ["mp4", "mov", "m4v", "webm", "3gp"],
        "max_video_seconds": MAX_VIDEO_SECONDS,
        "storage": "local-persistent",
    }


@app.get("/api/people")
def list_people():
    with db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.name, p.external_id, p.company, p.active, p.created_at,
                   COUNT(s.id) AS samples
            FROM people p
            LEFT JOIN person_samples s ON s.person_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/people")
async def create_person(
    name: str = Form(...),
    external_id: str = Form(""),
    company: str = Form(""),
    photo: UploadFile = File(...),
    photo_2: UploadFile | None = File(None),
    photo_3: UploadFile | None = File(None),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Ingresa el nombre de la persona.")

    person_id = str(uuid.uuid4())
    person_folder = FACE_DIR / person_id
    uploads = [u for u in (photo, photo_2, photo_3) if u is not None and u.filename]
    face_paths: list[Path] = []

    try:
        for index, upload in enumerate(uploads, start=1):
            face_paths.append(store_face_sample(person_id, upload, person_folder, index))
    except Exception:
        shutil.rmtree(person_folder, ignore_errors=True)
        raise

    if not face_paths:
        shutil.rmtree(person_folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Debes agregar al menos una foto.")

    with db() as conn:
        conn.execute(
            "INSERT INTO people (id, name, external_id, company, active, image_path, encoding_json, created_at) VALUES (?, ?, ?, ?, 1, ?, '{}', ?)",
            (person_id, name.strip(), external_id.strip(), company.strip(), str(face_paths[0]), now_iso()),
        )
        for face_path in face_paths:
            conn.execute(
                "INSERT INTO person_samples (id, person_id, image_path, created_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), person_id, str(face_path), now_iso()),
            )
        conn.commit()

    return {
        "id": person_id,
        "name": name.strip(),
        "samples": len(face_paths),
        "message": "Persona registrada correctamente.",
    }


@app.post("/api/recognize")
async def recognize(photo: UploadFile = File(...)):
    image_path = save_upload(photo, TMP_DIR, SUPPORTED_IMAGE_EXTENSIONS)
    try:
        gray = load_gray(image_path)
        faces = detect_faces(gray)
        if not faces:
            raise HTTPException(status_code=400, detail="No se detectó ningún rostro.")

        recognizer, people_by_label = build_recognizer()
        results = []
        for rect in faces:
            probe = normalize_face(gray, rect)
            candidate, authorized, confidence, similarity = recognize_face(probe, recognizer, people_by_label)
            log_result(candidate, authorized, confidence, similarity, "photo")
            results.append(result_payload(candidate, authorized, confidence, similarity, rect))

        recognized_people, best = summarize_results(results)
        return {
            "authorized": bool(recognized_people),
            "person": best["person"] if best and best["authorized"] else None,
            "best_candidate": best["best_candidate"] if best else None,
            "distance": best["distance"] if best else None,
            "similarity": best["similarity"] if best else None,
            "threshold": LBPH_THRESHOLD,
            "faces_detected": len(faces),
            "recognized_people": recognized_people,
            "unknown_faces": len([r for r in results if not r["authorized"]]),
            "results": results,
            "engine": "opencv-lbph",
            "source_type": "photo",
        }
    finally:
        image_path.unlink(missing_ok=True)


@app.post("/api/recognize-video")
async def recognize_video(video: UploadFile = File(...)):
    video_path = save_upload(video, TMP_DIR, SUPPORTED_VIDEO_EXTENSIONS)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="No fue posible abrir el video.")

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if frame_count else 0
        if duration and duration > MAX_VIDEO_SECONDS + 0.5:
            raise HTTPException(
                status_code=400,
                detail=f"El video debe durar máximo {MAX_VIDEO_SECONDS:g} segundos.",
            )

        recognizer, people_by_label = build_recognizer()
        step = max(1, int(fps / 3))
        frame_index = 0
        analyzed_frames = 0
        results = []

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % step != 0:
                frame_index += 1
                continue
            analyzed_frames += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detect_faces(gray)
            for rect in faces:
                probe = normalize_face(gray, rect)
                candidate, authorized, confidence, similarity = recognize_face(probe, recognizer, people_by_label)
                results.append(result_payload(candidate, authorized, confidence, similarity, rect))
            frame_index += 1
            if analyzed_frames >= 8:
                break

        if not results:
            raise HTTPException(status_code=400, detail="No se detectaron rostros utilizables en el video.")

        # Se registra una vez por persona reconocida y, si no hubo coincidencias, el mejor desconocido.
        recognized_people, best = summarize_results(results)
        to_log = recognized_people if recognized_people else ([best] if best else [])
        for item in to_log:
            candidate = None
            if item["authorized"]:
                pid = item["person"]["id"]
                candidate = next((p for p in people_by_label.values() if p["id"] == pid), None)
            log_result(candidate, item["authorized"], item["distance"], item["similarity"], "video")

        return {
            "authorized": bool(recognized_people),
            "person": best["person"] if best and best["authorized"] else None,
            "best_candidate": best["best_candidate"] if best else None,
            "distance": best["distance"] if best else None,
            "similarity": best["similarity"] if best else None,
            "threshold": LBPH_THRESHOLD,
            "recognized_people": recognized_people,
            "detections": len(results),
            "analyzed_frames": analyzed_frames,
            "engine": "opencv-lbph",
            "source_type": "video",
        }
    finally:
        cap.release()
        video_path.unlink(missing_ok=True)


@app.get("/api/logs")
def logs():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, tested_at, matched_name, distance, similarity, authorized, threshold, source_type FROM access_log ORDER BY tested_at DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]
