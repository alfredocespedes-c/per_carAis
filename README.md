# per_carAis · V1

MVP de control de acceso mediante reconocimiento facial.

## Qué permite esta V1

- Registrar personas autorizadas.
- Subir una foto de referencia por persona.
- Subir una foto de prueba.
- Detectar el rostro y compararlo contra las personas registradas.
- Mostrar nombre, similitud estimada y decisión de acceso.
- Guardar una bitácora de pruebas.
- Cambiar el umbral de aceptación mediante variable de entorno.
- Base preparada para incorporar cámara en vivo y controlador de torniquete.

## Arquitectura

- Frontend: HTML/CSS/JavaScript.
- Backend: FastAPI.
- Reconocimiento: `face_recognition`.
- Persistencia: SQLite.
- Fotos: almacenamiento local en `data/faces/`.

## Importante

Esta versión es para pruebas controladas con personas previamente enroladas.
No está pensada para identificar personas desconocidas contra bases externas.

## Ejecución

Recomendado: Python 3.11 o 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Luego abre:

http://localhost:8000

## Cómo probar efectividad

1. Registra una persona con una foto frontal y bien iluminada.
2. Usa otra foto distinta de la misma persona.
3. Prueba fotos con:
   - distinta iluminación;
   - lentes;
   - pequeñas rotaciones;
   - distinta distancia;
   - fondo distinto.
4. Prueba también personas no registradas.
5. Revisa la bitácora y compara similitud y resultado.

Para una evaluación útil, recomendamos varias imágenes por persona y guardar:
- verdaderos positivos;
- falsos positivos;
- verdaderos negativos;
- falsos negativos.

## Próxima versión

- Captura desde webcam.
- Reconocimiento continuo por frames.
- Confirmación por múltiples frames.
- Liveness detection.
- API segura para controlador de torniquete.
- Gestión de vigencia, horarios y permisos por persona.
