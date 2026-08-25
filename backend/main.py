from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import face_recognition
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
FACE_DIR = DATA_DIR / "faces"
DB_PATH = DATA_DIR / "carais.db"
FRONTEND_DIR = BASE_DIR / "frontend"

FACE_DIR.mkdir(parents=True, exist_ok=True)
MATCH_THRESHOLD = float(os.getenv("CARAIS_MATCH_THRESHOLD", "0.50"))

app = FastAPI(title="per_carAis API", version="1.0.0")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


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
                encoding_json TEXT NOT NULL,
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


def extract_single_face_encoding(image_path: Path) -> np.ndarray:
    image = face_recognition.load_image_file(str(image_path))
    locations = face_recognition.face_locations(image)
    if len(locations) == 0:
        raise HTTPException(status_code=400, detail="No se detectó ningún rostro en la imagen.")
    if len(locations) > 1:
        raise HTTPException(status_code=400, detail="Se detectó más de un rostro. Para enrolar, usa una foto con una sola persona.")
    encodings = face_recognition.face_encodings(image, known_face_locations=locations)
    if not encodings:
        raise HTTPException(status_code=400, detail="No fue posible generar la representación facial.")
    return encodings[0]


def save_upload(upload: UploadFile, folder: Path) -> Path:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa JPG, PNG o WEBP.")
    target = folder / f"{uuid.uuid4().hex}{ext}"
    with target.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return target


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "per_carAis", "version": "1.0.0", "match_threshold": MATCH_THRESHOLD}


@app.get("/api/people")
def list_people():
    with db() as conn:
        rows = conn.execute("SELECT id, name, external_id, company, active, created_at FROM people ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/people")
async def create_person(name: str = Form(...), external_id: str = Form(""), company: str = Form(""), photo: UploadFile = File(...)):
    person_id = str(uuid.uuid4())
    person_folder = FACE_DIR / person_id
    person_folder.mkdir(parents=True, exist_ok=True)
    image_path = save_upload(photo, person_folder)
    try:
        encoding = extract_single_face_encoding(image_path)
    except Exception:
        shutil.rmtree(person_folder, ignore_errors=True)
        raise
    with db() as conn:
        conn.execute("INSERT INTO people (id, name, external_id, company, active, image_path, encoding_json, created_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)", (person_id, name.strip(), external_id.strip(), company.strip(), str(image_path), json.dumps(encoding.tolist()), now_iso()))
        conn.commit()
    return {"id": person_id, "name": name.strip(), "message": "Persona registrada correctamente."}


@app.post("/api/recognize")
async def recognize(photo: UploadFile = File(...)):
    temp_dir = DATA_DIR / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    image_path = save_upload(photo, temp_dir)
    try:
        image = face_recognition.load_image_file(str(image_path))
        locations = face_recognition.face_locations(image)
        if len(locations) == 0:
            raise HTTPException(status_code=400, detail="No se detectó ningún rostro.")
        encodings = face_recognition.face_encodings(image, known_face_locations=locations)
        if not encodings:
            raise HTTPException(status_code=400, detail="No fue posible analizar el rostro.")
        probe = encodings[0]
        with db() as conn:
            people = conn.execute("SELECT id, name, external_id, company, encoding_json FROM people WHERE active = 1").fetchall()
        if not people:
            raise HTTPException(status_code=400, detail="No hay personas registradas para comparar.")
        known = np.array([json.loads(p["encoding_json"]) for p in people])
        distances = face_recognition.face_distance(known, probe)
        best_index = int(np.argmin(distances))
        best_distance = float(distances[best_index])
        best_person = people[best_index]
        similarity = max(0.0, min(100.0, (1.0 - best_distance) * 100.0))
        authorized = best_distance <= MATCH_THRESHOLD
        with db() as conn:
            conn.execute("INSERT INTO access_log (id, tested_at, matched_person_id, matched_name, distance, similarity, authorized, threshold) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), now_iso(), best_person["id"] if authorized else None, best_person["name"] if authorized else None, best_distance, similarity, 1 if authorized else 0, MATCH_THRESHOLD))
            conn.commit()
        return {
            "authorized": authorized,
            "person": {"id": best_person["id"], "name": best_person["name"], "external_id": best_person["external_id"], "company": best_person["company"]} if authorized else None,
            "best_candidate": best_person["name"],
            "distance": round(best_distance, 4),
            "similarity": round(similarity, 1),
            "threshold": MATCH_THRESHOLD,
            "faces_detected": len(locations),
        }
    finally:
        image_path.unlink(missing_ok=True)


@app.get("/api/logs")
def logs():
    with db() as conn:
        rows = conn.execute("SELECT id, tested_at, matched_name, distance, similarity, authorized, threshold FROM access_log ORDER BY tested_at DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]
