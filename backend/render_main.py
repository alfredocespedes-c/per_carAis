from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import psycopg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
LBPH_THRESHOLD = float(os.getenv("CARAIS_LBPH_THRESHOLD", "65"))
MAX_VIDEO_SECONDS = float(os.getenv("CARAIS_MAX_VIDEO_SECONDS", "2.5"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está configurada. En Render conecta el PostgreSQL del Blueprint.")

app = FastAPI(title="per_carAis Render", version="1.3.0-render")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
FACE_CASCADE = cv2.CascadeClassifier(CASCADE_PATH)
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".webp"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".3gp"}


def conn():
    return psycopg.connect(DATABASE_URL)


def now_iso():
    return datetime.now(timezone.utc)


def init_db():
    with conn() as db:
        with db.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS people (
                    id UUID PRIMARY KEY,
                    name TEXT NOT NULL,
                    external_id TEXT,
                    company TEXT,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS person_samples (
                    id UUID PRIMARY KEY,
                    person_id UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    image_bytes BYTEA NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS access_log (
                    id UUID PRIMARY KEY,
                    tested_at TIMESTAMPTZ NOT NULL,
                    matched_person_id UUID,
                    matched_name TEXT,
                    distance DOUBLE PRECISION,
                    similarity DOUBLE PRECISION,
                    authorized BOOLEAN NOT NULL,
                    threshold DOUBLE PRECISION NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'photo'
                )
            """)
        db.commit()


init_db()


def decode_image_bytes(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="No fue posible leer la imagen.")
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def detect_faces(gray: np.ndarray):
    return list(FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(70, 70)))


def normalize_face(gray: np.ndarray, rect) -> np.ndarray:
    x, y, w, h = [int(v) for v in rect]
    face = gray[y:y+h, x:x+w]
    face = cv2.equalizeHist(face)
    return cv2.resize(face, (200, 200), interpolation=cv2.INTER_AREA)


def face_png_bytes(face: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", face)
    if not ok:
        raise HTTPException(status_code=500, detail="No fue posible preparar la muestra facial.")
    return encoded.tobytes()


def extract_single_face(data: bytes) -> np.ndarray:
    gray = decode_image_bytes(data)
    faces = detect_faces(gray)
    if not faces:
        raise HTTPException(status_code=400, detail="No se detectó ningún rostro en la imagen.")
    if len(faces) > 1:
        raise HTTPException(status_code=400, detail="Se detectó más de un rostro. Usa una foto con una sola persona.")
    return normalize_face(gray, faces[0])


def build_recognizer():
    with conn() as db:
        with db.cursor() as cur:
            cur.execute("SELECT id::text, name, external_id, company FROM people WHERE active = TRUE ORDER BY created_at")
            people = cur.fetchall()
            cur.execute("SELECT person_id::text, image_bytes FROM person_samples ORDER BY created_at")
            samples = cur.fetchall()

    if not people:
        raise HTTPException(status_code=400, detail="No hay personas registradas para comparar.")

    labels_by_person = {row[0]: idx for idx, row in enumerate(people)}
    people_by_label = {
        idx: {"id": row[0], "name": row[1], "external_id": row[2], "company": row[3]}
        for idx, row in enumerate(people)
    }
    train_samples = []
    labels = []
    for person_id, image_bytes in samples:
        label = labels_by_person.get(person_id)
        if label is None:
            continue
        gray = cv2.imdecode(np.frombuffer(bytes(image_bytes), np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        train_samples.append(cv2.resize(gray, (200, 200)))
        labels.append(label)

    if not train_samples:
        raise HTTPException(status_code=400, detail="No hay muestras faciales disponibles para comparar.")

    recognizer = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
    recognizer.train(train_samples, np.array(labels, dtype=np.int32))
    return recognizer, people_by_label


def recognize_face(face, recognizer, people_by_label):
    label, confidence = recognizer.predict(face)
    confidence = float(confidence)
    candidate = people_by_label.get(int(label))
    authorized = bool(candidate) and confidence <= LBPH_THRESHOLD
    similarity = max(0.0, min(100.0, 100.0 - confidence))
    return candidate, authorized, confidence, similarity


def result_payload(candidate, authorized, confidence, similarity, bbox=None):
    return {
        "authorized": authorized,
        "person": candidate if authorized and candidate else None,
        "best_candidate": candidate["name"] if candidate else None,
        "distance": round(confidence, 2),
        "similarity": round(similarity, 1),
        "bbox": [int(v) for v in bbox] if bbox is not None else None,
    }


def log_result(candidate, authorized, confidence, similarity, source_type):
    with conn() as db:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO access_log (id, tested_at, matched_person_id, matched_name, distance, similarity, authorized, threshold, source_type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid4()), now_iso(), candidate["id"] if authorized and candidate else None,
                    candidate["name"] if authorized and candidate else None,
                    confidence, similarity, authorized, LBPH_THRESHOLD, source_type,
                ),
            )
        db.commit()


def summarize(results):
    recognized = [r for r in results if r["authorized"]]
    unique = {}
    for item in recognized:
        pid = item["person"]["id"]
        if pid not in unique or item["distance"] < unique[pid]["distance"]:
            unique[pid] = item
    ordered = sorted(unique.values(), key=lambda x: x["distance"])
    best = ordered[0] if ordered else (min(results, key=lambda x: x["distance"]) if results else None)
    return ordered, best


@app.get("/api/health")
@app.get("/health")
def health():
    with conn() as db:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM people WHERE active = TRUE")
            people = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM person_samples")
            samples = cur.fetchone()[0]
    return {
        "ok": True, "service": "per_carAis", "version": "1.3.0-render",
        "engine": "opencv-lbph", "match_threshold": LBPH_THRESHOLD,
        "people": people, "samples": samples,
        "supported_image_formats": sorted(x.lstrip('.') for x in SUPPORTED_IMAGE_EXTENSIONS),
        "supported_video_formats": sorted(x.lstrip('.') for x in SUPPORTED_VIDEO_EXTENSIONS),
        "max_video_seconds": MAX_VIDEO_SECONDS, "storage": "render-postgresql"
    }


@app.get("/api/people")
def list_people():
    with conn() as db:
        with db.cursor() as cur:
            cur.execute("""
                SELECT p.id::text, p.name, p.external_id, p.company, p.active, p.created_at, COUNT(s.id)
                FROM people p LEFT JOIN person_samples s ON s.person_id = p.id
                GROUP BY p.id ORDER BY p.created_at DESC
            """)
            rows = cur.fetchall()
    return [
        {"id": r[0], "name": r[1], "external_id": r[2], "company": r[3], "active": r[4], "created_at": r[5], "samples": r[6]}
        for r in rows
    ]


@app.post("/api/people")
async def create_person(name: str = Form(...), external_id: str = Form(""), company: str = Form(""),
                        photo: UploadFile = File(...), photo_2: UploadFile | None = File(None), photo_3: UploadFile | None = File(None)):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Ingresa el nombre de la persona.")
    uploads = [u for u in (photo, photo_2, photo_3) if u is not None and u.filename]
    samples = []
    for upload in uploads:
        ext = Path(upload.filename or "").suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Formato no soportado: {ext or 'sin extensión'}.")
        samples.append(face_png_bytes(extract_single_face(await upload.read())))
    if not samples:
        raise HTTPException(status_code=400, detail="Debes agregar al menos una foto.")

    person_id = str(uuid.uuid4())
    with conn() as db:
        with db.cursor() as cur:
            cur.execute("INSERT INTO people (id,name,external_id,company,active,created_at) VALUES (%s,%s,%s,%s,TRUE,%s)",
                        (person_id, name.strip(), external_id.strip(), company.strip(), now_iso()))
            for sample in samples:
                cur.execute("INSERT INTO person_samples (id,person_id,image_bytes,created_at) VALUES (%s,%s,%s,%s)",
                            (str(uuid.uuid4()), person_id, sample, now_iso()))
        db.commit()
    return {"id": person_id, "name": name.strip(), "samples": len(samples), "message": "Persona registrada correctamente."}


@app.post("/api/recognize")
async def recognize(photo: UploadFile = File(...)):
    ext = Path(photo.filename or "").suffix.lower()
    if ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato de imagen no soportado.")
    gray = decode_image_bytes(await photo.read())
    faces = detect_faces(gray)
    if not faces:
        raise HTTPException(status_code=400, detail="No se detectó ningún rostro.")
    recognizer, people_by_label = build_recognizer()
    results = []
    for rect in faces:
        candidate, authorized, confidence, similarity = recognize_face(normalize_face(gray, rect), recognizer, people_by_label)
        log_result(candidate, authorized, confidence, similarity, "photo")
        results.append(result_payload(candidate, authorized, confidence, similarity, rect))
    recognized_people, best = summarize(results)
    return {
        "authorized": bool(recognized_people), "person": best["person"] if best and best["authorized"] else None,
        "best_candidate": best["best_candidate"] if best else None, "distance": best["distance"] if best else None,
        "similarity": best["similarity"] if best else None, "threshold": LBPH_THRESHOLD,
        "faces_detected": len(faces), "recognized_people": recognized_people,
        "unknown_faces": len([r for r in results if not r["authorized"]]), "results": results,
        "engine": "opencv-lbph", "source_type": "photo"
    }


@app.post("/api/recognize-video")
async def recognize_video(video: UploadFile = File(...)):
    ext = Path(video.filename or "").suffix.lower()
    if ext not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato de video no soportado.")
    raw = await video.read()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    cap = cv2.VideoCapture(tmp_path)
    try:
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="No fue posible abrir el video.")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if frame_count else 0
        if duration and duration > MAX_VIDEO_SECONDS + 0.5:
            raise HTTPException(status_code=400, detail=f"El video supera el máximo de {MAX_VIDEO_SECONDS:.1f} segundos.")
        recognizer, people_by_label = build_recognizer()
        results = []
        step = max(1, int(fps / 3))
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step:
                idx += 1
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for rect in detect_faces(gray):
                candidate, authorized, confidence, similarity = recognize_face(normalize_face(gray, rect), recognizer, people_by_label)
                results.append(result_payload(candidate, authorized, confidence, similarity, rect))
            idx += 1
        if not results:
            raise HTTPException(status_code=400, detail="No se detectó ningún rostro en el video.")
        recognized_people, best = summarize(results)
        if best:
            candidate = best["person"] if best["authorized"] else ({"id": None, "name": best["best_candidate"], "external_id": "", "company": ""} if best["best_candidate"] else None)
            log_result(candidate, bool(best["authorized"]), best["distance"], best["similarity"], "video")
        return {
            "authorized": bool(recognized_people), "person": best["person"] if best and best["authorized"] else None,
            "best_candidate": best["best_candidate"] if best else None, "distance": best["distance"] if best else None,
            "similarity": best["similarity"] if best else None, "threshold": LBPH_THRESHOLD,
            "recognized_people": recognized_people, "results": results, "engine": "opencv-lbph",
            "source_type": "video", "duration_sec": round(duration, 2) if duration else None
        }
    finally:
        cap.release()
        Path(tmp_path).unlink(missing_ok=True)


@app.get("/api/logs")
def logs():
    with conn() as db:
        with db.cursor() as cur:
            cur.execute("SELECT id::text, tested_at, matched_name, distance, similarity, authorized, threshold, source_type FROM access_log ORDER BY tested_at DESC LIMIT 100")
            rows = cur.fetchall()
    return [
        {"id": r[0], "tested_at": r[1], "matched_name": r[2], "distance": r[3], "similarity": r[4], "authorized": r[5], "threshold": r[6], "source_type": r[7]}
        for r in rows
    ]


@app.get("/")
def frontend_index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
