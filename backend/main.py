from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
FACE_DIR = DATA_DIR / "faces"
DB_PATH = DATA_DIR / "carais.db"

FACE_DIR.mkdir(parents=True, exist_ok=True)
LBPH_THRESHOLD = float(os.getenv("CARAIS_LBPH_THRESHOLD", "65"))
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CARAIS_ALLOWED_ORIGINS",
        "https://alfredocespedes-c.github.io,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="per_carAis API", version="1.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
FACE_CASCADE = cv2.CascadeClassifier(CASCADE_PATH)


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
            CREATE TABLE IF NOT EXISTS access_log (
                id TEXT PRIMARY KEY,
                tested_at TEXT NOT NULL,
                matched_person_id TEXT,
                matched_name TEXT,
                distance REAL,
                similarity REAL,
                authorized INTEGER NOT NULL,
                threshold REAL NOT NULL
            )
        """)
        conn.commit()


init_db()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def save_upload(upload: UploadFile, folder: Path) -> Path:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa JPG, PNG o WEBP.")
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
    faces = FACE_CASCADE.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(80, 80),
    )
    return list(faces)


def normalize_face(gray: np.ndarray, rect) -> np.ndarray:
    x, y, w, h = rect
    face = gray[y:y+h, x:x+w]
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


def build_recognizer():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, external_id, company, image_path FROM people WHERE active = 1 ORDER BY created_at"
        ).fetchall()

    if not rows:
        raise HTTPException(status_code=400, detail="No hay personas registradas para comparar.")

    samples = []
    labels = []
    people_by_label = {}

    for label, row in enumerate(rows):
        path = Path(row["image_path"])
        if not path.exists():
            continue
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        samples.append(cv2.resize(gray, (200, 200)))
        labels.append(label)
        people_by_label[label] = row

    if not samples:
        raise HTTPException(status_code=400, detail="No hay muestras faciales disponibles para comparar.")

    recognizer = cv2.face.LBPHFaceRecognizer_create(
        radius=1,
        neighbors=8,
        grid_x=8,
        grid_y=8,
    )
    recognizer.train(samples, np.array(labels, dtype=np.int32))
    return recognizer, people_by_label


@app.get("/")
def index():
    return {"ok": True, "service": "per_carAis", "version": "1.2.0", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "per_carAis",
        "version": "1.2.0",
        "engine": "opencv-lbph",
        "match_threshold": LBPH_THRESHOLD,
        "storage": "ephemeral-local",
    }


@app.get("/api/people")
def list_people():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, external_id, company, active, created_at FROM people ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/people")
async def create_person(
    name: str = Form(...),
    external_id: str = Form(""),
    company: str = Form(""),
    photo: UploadFile = File(...),
):
    person_id = str(uuid.uuid4())
    person_folder = FACE_DIR / person_id
    raw_path = save_upload(photo, person_folder)

    try:
        face = extract_single_face(raw_path)
        face_path = person_folder / "face.png"
        cv2.imwrite(str(face_path), face)
        raw_path.unlink(missing_ok=True)
    except Exception:
        shutil.rmtree(person_folder, ignore_errors=True)
        raise

    with db() as conn:
        conn.execute(
            "INSERT INTO people (id, name, external_id, company, active, image_path, encoding_json, created_at) VALUES (?, ?, ?, ?, 1, ?, '{}', ?)",
            (person_id, name.strip(), external_id.strip(), company.strip(), str(face_path), now_iso()),
        )
        conn.commit()

    return {"id": person_id, "name": name.strip(), "message": "Persona registrada correctamente."}


@app.post("/api/recognize")
async def recognize(photo: UploadFile = File(...)):
    temp_dir = DATA_DIR / "tmp"
    image_path = save_upload(photo, temp_dir)

    try:
        gray = load_gray(image_path)
        faces = detect_faces(gray)
        if len(faces) == 0:
            raise HTTPException(status_code=400, detail="No se detectó ningún rostro.")

        probe = normalize_face(gray, max(faces, key=lambda r: r[2] * r[3]))
        recognizer, people_by_label = build_recognizer()
        label, confidence = recognizer.predict(probe)
        confidence = float(confidence)
        candidate = people_by_label.get(int(label))

        authorized = bool(candidate) and confidence <= LBPH_THRESHOLD
        similarity = max(0.0, min(100.0, 100.0 - confidence))

        with db() as conn:
            conn.execute(
                "INSERT INTO access_log (id, tested_at, matched_person_id, matched_name, distance, similarity, authorized, threshold) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    now_iso(),
                    candidate["id"] if authorized else None,
                    candidate["name"] if authorized else None,
                    confidence,
                    similarity,
                    1 if authorized else 0,
                    LBPH_THRESHOLD,
                ),
            )
            conn.commit()

        return {
            "authorized": authorized,
            "person": {
                "id": candidate["id"],
                "name": candidate["name"],
                "external_id": candidate["external_id"],
                "company": candidate["company"],
            } if authorized else None,
            "best_candidate": candidate["name"] if candidate else None,
            "distance": round(confidence, 2),
            "similarity": round(similarity, 1),
            "threshold": LBPH_THRESHOLD,
            "faces_detected": len(faces),
            "engine": "opencv-lbph",
        }
    finally:
        image_path.unlink(missing_ok=True)


@app.get("/api/logs")
def logs():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, tested_at, matched_name, distance, similarity, authorized, threshold FROM access_log ORDER BY tested_at DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]
