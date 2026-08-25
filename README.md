# per_carAis · V1.3

MVP de control de acceso mediante reconocimiento facial con frontend web, backend FastAPI y despliegue en servidor local.

## Funciones actuales

- Registrar personas autorizadas.
- Guardar múltiples fotografías de referencia por persona.
- Identificar personas desde fotografía.
- Identificar personas desde video corto.
- Captura desde cámara del teléfono/navegador.
- Listado de personas registradas.
- Historial de reconocimientos.
- Formatos JPG, JPEG, JFIF, PNG y WEBP.
- Videos MP4, MOV, M4V, WEBM y 3GP.
- Persistencia SQLite y muestras faciales en almacenamiento local.

## Arquitectura de servidor

- Repositorio: `/home/acespedes/dev/per_carAis`
- Entorno virtual: `/home/acespedes/dev/per_carAis/.venv`
- Backend FastAPI: `127.0.0.1:8301`
- Servicio systemd: `per-carais.service`
- Frontend Nginx: `/var/www/per_carAis`
- Dominio: `dev-prueba1.conaf.cl`
- Configuración Nginx versionada: `deploy/dev-prueba1.conaf.cl.conf`
- Servicio systemd versionado: `deploy/per-carais.service`

## Actualizar servidor

Después de la instalación inicial, las siguientes actualizaciones se hacen con:

```bash
cd /home/acespedes/dev/per_carAis
git pull origin main
chmod +x deploy/update-server.sh
./deploy/update-server.sh
```

El script:

1. Actualiza el repositorio.
2. Crea el `.venv` si no existe.
3. Instala/actualiza dependencias.
4. Instala y reinicia `per-carais.service`.
5. Copia el frontend a `/var/www/per_carAis`.
6. Instala la configuración Nginx correcta.
7. Valida y recarga Nginx.
8. Comprueba `/api/health` por backend y por Nginx.

## Instalación inicial en un servidor nuevo

```bash
cd /home/acespedes/dev
git clone https://github.com/alfredocespedes-c/per_carAis.git
cd per_carAis
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
chmod +x deploy/update-server.sh
./deploy/update-server.sh
```

## Verificaciones

Backend directo:

```bash
curl http://127.0.0.1:8301/api/health
```

Nginx local:

```bash
curl -H "Host: dev-prueba1.conaf.cl" http://127.0.0.1/api/health
```

Servicio:

```bash
sudo systemctl status per-carais.service --no-pager
```

## Datos persistentes

La base y las muestras faciales viven en `data/`. No deben eliminarse durante una actualización normal.

## Nota

Esta aplicación está pensada para reconocer personas previamente enroladas en una base local. No busca ni identifica personas contra bases externas.
