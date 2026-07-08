import os
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, date, time, timedelta
from typing import List, Optional

import httpx
from fastapi import HTTPException

import requests
import requests.auth
import base64
from fastapi import HTTPException, Depends
from pydantic import BaseModel
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Employee, AttendanceLog

import re
import json


from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from fastapi import Request
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import Base, engine, get_db, SessionLocal
from app.models import Employee, AttendanceLog, Shift
from app.schemas import (
    ShiftCreate, ShiftUpdate, ShiftOut,
    EmployeeCreate, EmployeeUpdate, EmployeeOut,
    HikvisionEvent,
)
from app.services.excel import generate_attendance_excel
from app.services.hours import build_daily_summaries, determine_next_event_type
from app.services.hikvision_client import (
    enroll_employee, fetch_events, CameraNotConfigured, CameraEnrollmentError,
)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB

# Carpeta única para fotos capturadas en tiempo real (definida aquí para que esté
# disponible tanto en el live poll loop como en los endpoints del webhook)
CARPETA_FOTOS = os.path.join("backend", "static", "captured_faces")
os.makedirs(CARPETA_FOTOS, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Usamos settings.MEDIA_DIR que apunta a "media/employee_photos" en tu config.py
EMPLEADOS_FOTOS_DIR = settings.MEDIA_DIR
os.makedirs(EMPLEADOS_FOTOS_DIR, exist_ok=True)  # Crea la carpeta automáticamente si no existe

def run_light_migrations():
    """Migración ligera para bases SQLite existentes: agrega columnas nuevas
    sin perder datos. Para PostgreSQL en producción se recomienda Alembic."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "employees" in tables:
        cols = {c["name"] for c in inspector.get_columns("employees")}
        if "shift_id" not in cols:
            logger.info("Migración: agregando employees.shift_id")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE employees ADD COLUMN shift_id INTEGER"))

    if "employees" in tables:
        cols = {c["name"] for c in inspector.get_columns("employees")}
        new_emp_cols = {
            "photo_path": "ALTER TABLE employees ADD COLUMN photo_path VARCHAR(255)",
            "card_number": "ALTER TABLE employees ADD COLUMN card_number VARCHAR(50)",
            "camera_status": "ALTER TABLE employees ADD COLUMN camera_status VARCHAR(20) NOT NULL DEFAULT 'pendiente'",
            "camera_synced_at": "ALTER TABLE employees ADD COLUMN camera_synced_at DATETIME",
        }
        for col, ddl in new_emp_cols.items():
            if col not in cols:
                logger.info("Migración: agregando employees.%s", col)
                with engine.begin() as conn:
                    conn.execute(text(ddl))

    if "attendance_logs" in tables:
        cols = {c["name"] for c in inspector.get_columns("attendance_logs")}
        if "event_type" not in cols:
            logger.info("Migración: agregando attendance_logs.event_type")
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE attendance_logs ADD COLUMN event_type VARCHAR(10) "
                    "NOT NULL DEFAULT 'entrada'"
                ))
        if "captured_photo_url" not in cols:
            logger.info("Migración: agregando attendance_logs.captured_photo_url")
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE attendance_logs ADD COLUMN captured_photo_url VARCHAR(255)"
                ))


def seed_database():
    db = SessionLocal()
    try:
        if db.query(Shift).count() == 0:
            logger.info("Creando turnos por defecto...")
            db.add_all([
                Shift(name="Turno Mañana", start_time="07:00", end_time="15:00", standard_hours=8.0),
                Shift(name="Turno Tarde", start_time="13:00", end_time="21:00", standard_hours=8.0),
                Shift(name="Medio Tiempo", start_time="08:00", end_time="12:00", standard_hours=4.0),
            ])
            db.commit()

        if db.query(Employee).count() == 0:
            logger.info("Inicializando empleados semilla...")
            manana = db.query(Shift).filter(Shift.name == "Turno Mañana").first()
            shift_id = manana.id if manana else None
            db.add_all([
                Employee(id="5473790", name="JULIA LUNA", department="GUAYAMURI/MANTENIMIENTO", shift_id=shift_id),
                Employee(id="5478620", name="EMILIO CARABALLO", department="GUAYAMURI/MANTENIMIENTO", shift_id=shift_id),
                Employee(id="5478900", name="MARIA GOMEZ", department="GUAYAMURI/ADMINISTRACION", shift_id=shift_id),
                Employee(id="5479001", name="PEDRO PEREZ", department="GUAYAMURI/DOCENTE", shift_id=shift_id),
                Employee(id="5481022", name="CARLOS MENDOZA", department="GUAYAMURI/DOCENTE", shift_id=shift_id),
            ])
            db.commit()
            logger.info("Semillado completo.")
    finally:
        db.close()


def _run_reconcile_once():
    """Ejecuta una pasada de reconciliación (bloqueante: usa requests + sesión sync)."""
    db = SessionLocal()
    try:
        now = datetime.now()
        start_dt = now - timedelta(days=settings.RECONCILE_LOOKBACK_DAYS)
        result = reconcile_events(db, start_dt, now)
        logger.info(result)
        if result.get("reason") == "no_configurada":
            return  # cámara aún no configurada: nada que hacer
        if result.get("imported"):
            logger.info("Reconciliación automática: %s", result["message"])
    except CameraEnrollmentError as e:
        logger.warning("Reconciliación automática: no se pudo contactar la cámara: %s", e)
    except Exception as e:  # nunca dejar caer el loop por un error puntual
        logger.error("Reconciliación automática: error inesperado: %s", e)
    finally:
        db.close()


async def _reconciliation_loop():
    """Job de fondo: reconcilia periódicamente las marcaciones guardadas en la cámara."""
    interval = max(1, settings.RECONCILE_INTERVAL_MINUTES) * 60
    logger.info("Job de reconciliación activo (cada %d min).", settings.RECONCILE_INTERVAL_MINUTES)
    while True:
        if settings.hikvision_configured:
            await asyncio.to_thread(_run_reconcile_once)
        await asyncio.sleep(interval)


# Timestamp global de la última consulta en vivo a la cámara
_last_live_poll_time: datetime = datetime.now() - timedelta(seconds=60)


def _do_live_camera_poll() -> list:
    """
    Consulta sincrónica a la cámara (se ejecuta en hilo aparte).
    Devuelve lista de eventos nuevos: [{person_id, name, timestamp, device_name}, ...].
    """
    global _last_live_poll_time
    try:
        import requests
        from requests.auth import HTTPDigestAuth
        import warnings
        warnings.filterwarnings("ignore")  # suprimir advertencias SSL en cámaras autofirmadas

        protocol = "https" if settings.HIKVISION_USE_HTTPS else "http"
        base = f"{protocol}://{settings.HIKVISION_IP}:{settings.HIKVISION_PORT}"
        auth = HTTPDigestAuth(settings.HIKVISION_USER, settings.HIKVISION_PASS)
        url = f"{base}/ISAPI/AccessControl/AcsEvent?format=json"

        now = datetime.now()
        body = {
            "AcsEventCond": {
                "searchID": "guayamuri-live",
                "searchResultPosition": 0,
                "maxResults": 30,
                "major": 5,
                "minor": 0,
                "startTime": _last_live_poll_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "endTime": now.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        }

        response = requests.post(url, auth=auth, json=body, timeout=8, verify=False)
        _last_live_poll_time = now

        if response.status_code != 200:
            return []

        data = response.json().get("AcsEvent", {})
        info_list = data.get("InfoList", []) or []
        results = []
        for ev in info_list:
            person_id = ev.get("employeeNoString") or ev.get("employeeNo")
            ts_raw = ev.get("time")
            if not person_id or not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ts = ts.replace(tzinfo=None)
            except ValueError:
                continue
            results.append({
                "person_id": str(person_id),
                "name": ev.get("name", ""),
                "timestamp": ts,
                "device_name": ev.get("deviceName", "Cámara Hikvision"),
                "face_image": ev.get("faceImageString") or ev.get("JPEGPicWithData"),
            })
        return results
    except Exception as e:
        logger.debug("Live poll: no se pudo conectar con la cámara: %s", e)
        return []


async def _live_camera_poll_loop():
    """
    Loop de polling rápido: consulta la cámara Hikvision cada LIVE_POLL_INTERVAL_SECONDS.
    Por cada detección nueva registra la asistencia en la BD y la transmite en
    tiempo real por WebSocket a la pantalla de monitoreo.
    """
    interval = max(5, settings.LIVE_POLL_INTERVAL_SECONDS)
    logger.info("📹 Live poll activo: consultando cámara cada %d s (IP: %s)", interval, settings.HIKVISION_IP)
    # Pequeña espera inicial para que el servidor arranque del todo
    await asyncio.sleep(3)

    while True:
        try:
            events = await asyncio.to_thread(_do_live_camera_poll)
            if events:
                db = SessionLocal()
                try:
                    for ev in events:
                        pid = ev["person_id"]
                        ts  = ev["timestamp"]

                        # Deduplicación: ignorar si ya tenemos esa marca exacta (mismo segundo)
                        ts_second = ts.replace(microsecond=0)
                        existing = db.query(AttendanceLog).filter(
                            AttendanceLog.person_id == pid,
                            AttendanceLog.timestamp >= ts_second,
                            AttendanceLog.timestamp < ts_second + timedelta(seconds=1),
                        ).first()
                        if existing:
                            continue

                        # Obtener o crear el empleado (usando el nombre que mandó la cámara si es nuevo)
                        employee = db.query(Employee).filter(Employee.id == pid).first()
                        if not employee:
                            emp_name = ev["name"] or f"Desconocido {pid}"
                            employee = Employee(
                                id=pid,
                                name=emp_name,
                                department="Por Asignar",
                                camera_status="pendiente",
                            )
                            db.add(employee)
                            db.commit()
                            db.refresh(employee)

                        # Tipo de evento: alternancia entrada/salida por día
                        today_start = datetime.combine(ts.date(), time.min)
                        marks_today = db.query(AttendanceLog).filter(
                            AttendanceLog.person_id == pid,
                            AttendanceLog.timestamp >= today_start,
                            AttendanceLog.timestamp <  ts,
                        ).count()
                        event_type = determine_next_event_type(marks_today)

                        # Guardar foto Base64 si vino en el evento
                        captured_photo = None
                        if ev.get("face_image"):
                            try:
                                img_bytes = base64.b64decode(ev["face_image"])
                                filename = f"rostro_{pid}_{uuid.uuid4().hex[:6]}.jpg"
                                filepath = os.path.join(CARPETA_FOTOS, filename)
                                with open(filepath, "wb") as f:
                                    f.write(img_bytes)
                                captured_photo = filename
                            except Exception as e_img:
                                logger.debug("No se pudo guardar imagen de la cámara: %s", e_img)

                        # Guardar en BD
                        new_log = AttendanceLog(
                            person_id=pid,
                            timestamp=ts,
                            event_type=event_type,
                            captured_photo_url=captured_photo,
                        )
                        db.add(new_log)
                        db.commit()
                        db.refresh(new_log)

                        # Transmitir en tiempo real por WebSocket
                        payload = _serialize_log(new_log, device_info=ev["device_name"])
                        await manager.broadcast(payload)
                        logger.info(
                            "📹 Live: %s (%s) → %s",
                            employee.name, pid, event_type.upper()
                        )
                finally:
                    db.close()
        except Exception as e:
            logger.warning("Live poll loop error: %s", e)

        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.MEDIA_DIR, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    run_light_migrations()
    seed_database()

    reconcile_task = None
    live_poll_task = None

    if settings.RECONCILE_ENABLED:
        if settings.hikvision_configured:
            await asyncio.to_thread(_run_reconcile_once)
        reconcile_task = asyncio.create_task(_reconciliation_loop())

    if settings.LIVE_POLL_ENABLED and settings.hikvision_ip_configured:
        live_poll_task = asyncio.create_task(_live_camera_poll_loop())

    yield

    for task in (reconcile_task, live_poll_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def _photo_file_path(employee_id: str) -> str:
    safe_id = "".join(c for c in employee_id if c.isalnum() or c in ("-", "_"))
    return os.path.join(settings.MEDIA_DIR, f"{safe_id}.jpg")


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Nueva conexión WebSocket activa. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"Conexión WebSocket cerrada. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error al enviar mensaje por WebSocket: {e}")
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)


manager = ConnectionManager()


def _parse_date_range(start_date: Optional[str], end_date: Optional[str]):
    start_dt = end_dt = None
    try:
        if start_date:
            start_dt = datetime.combine(date.fromisoformat(start_date), time.min)
        if end_date:
            end_dt = datetime.combine(date.fromisoformat(end_date), time.max)
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Usa YYYY-MM-DD.")
    return start_dt, end_dt

@app.get("/api/hikvision/status")
async def get_camera_status():
    """
    Realiza un ping rápido a la cámara mediante ISAPI para comprobar si está en línea.
    """
    protocolo = "https" if settings.HIKVISION_USE_HTTPS else "http"
    url = f"{protocolo}://{settings.HIKVISION_IP}:{settings.HIKVISION_PORT}/ISAPI/System/deviceInfo"
    
    try:
        auth = httpx.DigestAuth(settings.HIKVISION_USER, settings.HIKVISION_PASS)
        
        async with httpx.AsyncClient(auth=auth) as client:
            # Un timeout corto de 2 segundos es suficiente para saber si responde
            response = await client.get(url, timeout=2.0)
            logger.info(response)
            if response.status_code == 200:
                return {"camera_online": True, "detail": "Cámara Hikvision conectada y respondiendo."}
                
            return {"camera_online": False, "detail": f"Código de respuesta inesperado: {response.status_code}"}
    except Exception as e:
        return {"camera_online": False, "detail": f"No se pudo conectar con el hardware: {str(e)}"}

# ============================ TURNOS ============================
@app.get("/api/shifts", response_model=List[ShiftOut])
def get_shifts(db: Session = Depends(get_db)):
    return db.query(Shift).order_by(Shift.name).all()


@app.post("/api/shifts", response_model=ShiftOut, status_code=201)
def create_shift(payload: ShiftCreate, db: Session = Depends(get_db)):
    if db.query(Shift).filter(Shift.name == payload.name).first():
        raise HTTPException(status_code=400, detail="Ya existe un turno con ese nombre.")
    shift = Shift(**payload.model_dump())
    db.add(shift)
    db.commit()
    db.refresh(shift)
    return shift


@app.put("/api/shifts/{shift_id}", response_model=ShiftOut)
def update_shift(shift_id: int, payload: ShiftUpdate, db: Session = Depends(get_db)):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != shift.name:
        if db.query(Shift).filter(Shift.name == data["name"]).first():
            raise HTTPException(status_code=400, detail="Ya existe un turno con ese nombre.")
    for k, v in data.items():
        setattr(shift, k, v)
    db.commit()
    db.refresh(shift)
    return shift


@app.delete("/api/shifts/{shift_id}")
def delete_shift(shift_id: int, db: Session = Depends(get_db)):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")
    db.delete(shift)  # empleados quedan con shift_id = NULL (ondelete SET NULL)
    db.commit()
    return {"message": "Turno eliminado correctamente."}


# ============================ EMPLEADOS ============================
@app.get("/api/employees", response_model=List[EmployeeOut])
def get_employees(db: Session = Depends(get_db)):
    """Trae la lista completa para rellenar la tabla HTML (Tu función original)"""
    return db.query(Employee).options(joinedload(Employee.shift)).all()

@app.post("/api/employees", response_model=EmployeeOut, status_code=201)
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db)):
    """
    REGISTRO DE EMPLEADOS UNIFICADO (CORRECCIÓN DE BUCLE).
    Mantiene la compatibilidad exacta con tu interfaz HTML y procesa la foto del hardware.
    """
    try:
        # 1. Validar si el ID ya existe
        if db.query(Employee).filter(Employee.id == payload.id).first():
            raise HTTPException(status_code=400, detail="El ID del empleado ya está registrado.")
            
        # 2. Validar que el turno exista (Seguridad original)
        if payload.shift_id is not None:
            if not db.query(Shift).filter(Shift.id == payload.shift_id).first():
                raise HTTPException(status_code=400, detail="El turno indicado no existe.")

        nombre_archivo_perfil = None

        # 3. Extraer y guardar la foto Base64 si viene de la cámara
        if hasattr(payload, 'photo_base64') and payload.photo_base64 and "base64," in payload.photo_base64:
            try:
                header, data_base64 = payload.photo_base64.split("base64,")
                imagen_binaria = base64.b64decode(data_base64)
                
                # Nombre de archivo único usando su ID
                nombre_archivo_perfil = f"perfil_{payload.id}.jpg"
                
                os.makedirs(settings.MEDIA_DIR, exist_ok=True)
                ruta_guardado = os.path.join(settings.MEDIA_DIR, nombre_archivo_perfil)
                
                with open(ruta_guardado, "wb") as f:
                    f.write(imagen_binaria)
                logger.info(f"Foto de enrolamiento guardada en disco: {ruta_guardado}")
            except Exception as e_img:
                logger.error(f"Error al procesar la imagen del hardware: {str(e_img)}")

        # 4. Crear el objeto usando el volcado de datos original compatible con tu frontend
        datos_empleado = payload.model_dump()
        
        # Eliminamos el campo temporal de base64 si existe para que no choque con las columnas de SQLite
        if "photo_base64" in datos_empleado:
            datos_empleado.pop("photo_base64")
            
        # Creamos la entidad mapeando el path de la foto física y el estado por defecto
        emp = Employee(**datos_empleado)
        emp.photo_path = nombre_archivo_perfil
        emp.camera_status = "pendiente"

        db.add(emp)
        db.commit()
        db.refresh(emp)
        
        logger.info(f"¡Empleado {emp.id} registrado exitosamente desde la interfaz!")
        return emp

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error crítico en el guardado de personal: {str(e)}")
        raise HTTPException(status_code=500, detail="Error interno al procesar el registro.")
@app.post("/api/employees/register-with-face", status_code=201)
async def register_employee_with_face(
    employee_id: str = Form(...),
    name: str = Form(...),
    department: str = Form(...),
    shift_id: Optional[str] = Form(None),
    card_number: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """
    Registro unificado: guarda el empleado en la BD local, almacena su foto
    en disco y lo enrola automáticamente en el biométrico Hikvision en un solo paso.
    Devuelve el resultado de cada etapa para que el frontend muestre el progreso.
    """
    logger.info(f"[REGISTRO UNIFICADO] Iniciando para {name} (ID: {employee_id})")

    # ── PASO 1: Verificar duplicado ──────────────────────────────────────────
    if db.query(Employee).filter(Employee.id == employee_id).first():
        raise HTTPException(status_code=400, detail="El ID de empleado ya está registrado.")

    shift_id_int = int(shift_id) if shift_id and shift_id.strip() else None
    if shift_id_int is not None:
        if not db.query(Shift).filter(Shift.id == shift_id_int).first():
            raise HTTPException(status_code=400, detail="El turno indicado no existe.")

    # ── PASO 2: Guardar foto en disco (si viene) ─────────────────────────────
    photo_path_saved = None
    photo_bytes_saved = None

    if file and file.filename:
        content_type = file.content_type or "image/jpeg"
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="La foto debe ser JPG o PNG.")
        photo_bytes_saved = await file.read()
        if len(photo_bytes_saved) > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=400, detail="La foto supera 5 MB.")
        photo_path_saved = _photo_file_path(employee_id)
        os.makedirs(settings.MEDIA_DIR, exist_ok=True)
        with open(photo_path_saved, "wb") as fout:
            fout.write(photo_bytes_saved)
        logger.info(f"[REGISTRO UNIFICADO] Foto guardada en disco: {photo_path_saved}")

    # ── PASO 3: Crear el empleado en la BD local ─────────────────────────────
    try:
        emp = Employee(
            id=employee_id,
            name=name,
            department=department,
            shift_id=shift_id_int,
            card_number=card_number.strip() if card_number and card_number.strip() else None,
            photo_path=photo_path_saved,
            camera_status="pendiente",
        )
        db.add(emp)
        db.commit()
        db.refresh(emp)
        logger.info(f"[REGISTRO UNIFICADO] Empleado {employee_id} guardado en BD local.")
    except Exception as db_err:
        db.rollback()
        # Limpiar foto si ya se guardó
        if photo_path_saved and os.path.exists(photo_path_saved):
            try:
                os.remove(photo_path_saved)
            except OSError:
                pass
        logger.error(f"[REGISTRO UNIFICADO] Error en BD: {db_err}")
        raise HTTPException(status_code=500, detail=f"Error al guardar en base de datos: {str(db_err)}")

    # ── PASO 4: Enrolamiento en el biométrico Hikvision ──────────────────────
    camera_result = {
        "ok": False,
        "status": "sin_foto",
        "message": "No se subió foto; el empleado quedó guardado localmente.",
    }

    if photo_bytes_saved:
        try:
            hik_message = enroll_employee(emp, photo_bytes_saved)
            emp.camera_status = "sincronizado"
            emp.camera_synced_at = datetime.now()
            db.commit()
            camera_result = {
                "ok": True,
                "status": "sincronizado",
                "message": hik_message,
            }
            logger.info(f"[REGISTRO UNIFICADO] {employee_id} enrolado en Hikvision correctamente.")
        except CameraNotConfigured as e:
            camera_result = {
                "ok": False,
                "status": "pendiente",
                "message": str(e),
                "reason": "no_configurada",
            }
            logger.warning(f"[REGISTRO UNIFICADO] Cámara no configurada: {e}")
        except CameraEnrollmentError as e:
            emp.camera_status = "error"
            db.commit()
            camera_result = {
                "ok": False,
                "status": "error",
                "message": str(e),
            }
            logger.error(f"[REGISTRO UNIFICADO] Error al enrolar en Hikvision: {e}")

    return {
        "employee_id": employee_id,
        "name": name,
        "db_saved": True,
        "photo_saved": photo_path_saved is not None,
        "camera": camera_result,
        "message": (
            f"Empleado registrado. "
            f"{'Foto subida. ' if photo_path_saved else ''}"
            f"{camera_result['message']}"
        ),
    }


@app.put("/api/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: str, payload: EmployeeUpdate, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Empleado no encontrado.")
    if payload.shift_id is not None:
        if not db.query(Shift).filter(Shift.id == payload.shift_id).first():
            raise HTTPException(status_code=400, detail="El turno indicado no existe.")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(emp, k, v)
    db.commit()
    db.refresh(emp)
    return emp


@app.delete("/api/employees/{employee_id}")
def delete_employee(employee_id: str, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Empleado no encontrado.")
    # Eliminar la foto del disco si existe
    if emp.photo_path and os.path.exists(emp.photo_path):
        try:
            os.remove(emp.photo_path)
        except OSError as e:
            logger.warning("No se pudo borrar la foto de %s: %s", employee_id, e)
    db.delete(emp)
    db.commit()
    return {"message": "Empleado eliminado correctamente."}


# ---------- Foto del empleado (para enrolamiento en la cámara) ----------
@app.post("/api/employees/{employee_id}/photo", response_model=EmployeeOut)
async def upload_employee_photo(
    employee_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Empleado no encontrado.")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="La foto debe ser JPG o PNG.")

    content = await file.read()
    if len(content) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="La foto supera el tamaño máximo (5 MB).")

    path = _photo_file_path(employee_id)
    with open(path, "wb") as f:
        f.write(content)

    emp.photo_path = path
    emp.camera_status = "pendiente"  # foto nueva -> requiere (re)sincronizar
    emp.camera_synced_at = None
    db.commit()
    db.refresh(emp)
    return emp


@app.get("/api/employees/{employee_id}/photo")
def get_employee_photo(employee_id: str, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp or not emp.photo_path or not os.path.exists(emp.photo_path):
        raise HTTPException(status_code=404, detail="Este empleado no tiene foto.")
    return FileResponse(emp.photo_path, media_type="image/jpeg")


# ---------- Sincronización con la cámara Hikvision ----------
@app.post("/api/employees/{employee_id}/camera-sync")
def sync_employee_to_camera(employee_id: str, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Empleado no encontrado.")
    if not emp.photo_path or not os.path.exists(emp.photo_path):
        raise HTTPException(status_code=400, detail="El empleado necesita una foto antes de enrolarlo.")

    with open(emp.photo_path, "rb") as f:
        photo_bytes = f.read()

    try:
        message = enroll_employee(emp, photo_bytes)
    except CameraNotConfigured as e:
        # No es un error: la cámara aún no existe. Los datos quedan listos.
        return {
            "ok": False,
            "camera_status": emp.camera_status,
            "message": str(e),
            "reason": "no_configurada",
        }
    except CameraEnrollmentError as e:
        emp.camera_status = "error"
        db.commit()
        return {"ok": False, "camera_status": "error", "message": str(e)}

    emp.camera_status = "sincronizado"
    emp.camera_synced_at = datetime.now()
    db.commit()
    return {"ok": True, "camera_status": "sincronizado", "message": message}


# ============================ ASISTENCIA ============================
def _serialize_log(log: AttendanceLog, device_info: str = "Cámara Hikvision") -> dict:
    foto_url = None
    if hasattr(log, "captured_photo_url") and log.captured_photo_url:
        foto_url = f"/static/captured_faces/{log.captured_photo_url}"
    return {
        "id": log.log_id,
        "person_id": log.person_id,
        "name": log.employee.name if log.employee else "Desconocido",
        "department": log.employee.department if log.employee else "Sin departamento",
        "shift_name": log.employee.shift.name if (log.employee and log.employee.shift) else "Sin turno",
        "event_type": log.event_type or "entrada",
        "timestamp": log.timestamp.isoformat(),
        "time_formatted": log.timestamp.strftime("%d/%m/%Y %H:%M"),
        "device_info": device_info,
        "foto_url": foto_url,
    }

@app.get("/api/departments")
def get_departments(db: Session = Depends(get_db)):
    # Busca todos los departamentos diferentes asignados a los empleados
    departments = db.query(Employee.department).distinct().all()
    # Limpia el resultado para devolver una lista simple de textos
    return [d[0] for d in departments if d[0]]


@app.get("/api/attendance")
def get_attendance(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    name: Optional[str] = Query(None),
    person_id: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    shift: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    start_dt, end_dt = _parse_date_range(start_date, end_date)
    query = db.query(AttendanceLog).options(
        joinedload(AttendanceLog.employee).joinedload(Employee.shift)
    )
    if start_dt:
        query = query.filter(AttendanceLog.timestamp >= start_dt)
    if end_dt:
        query = query.filter(AttendanceLog.timestamp <= end_dt)
    logs = query.order_by(AttendanceLog.timestamp.desc()).all()

    filtered_logs = []
    for log in logs:
        emp = log.employee
        if person_id and person_id.strip() not in (log.person_id or ""):
            continue
        emp_name = emp.name if emp else "Desconocido"
        if name and name.strip().lower() not in emp_name.lower():
            continue
        emp_dept = emp.department if emp else "Sin departamento"
        if department and department.strip().lower() not in emp_dept.lower():
            continue
        emp_shift = emp.shift.name if (emp and emp.shift) else "Sin turno"
        if shift and shift.strip().lower() not in emp_shift.lower():
            continue
        filtered_logs.append(log)

    return [_serialize_log(log) for log in filtered_logs]


@app.get("/api/attendance/summary")
def get_attendance_summary(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    name: Optional[str] = Query(None),
    person_id: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    shift: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    start_dt, end_dt = _parse_date_range(start_date, end_date)
    query = db.query(AttendanceLog).options(
        joinedload(AttendanceLog.employee).joinedload(Employee.shift)
    )
    if start_dt:
        query = query.filter(AttendanceLog.timestamp >= start_dt)
    if end_dt:
        query = query.filter(AttendanceLog.timestamp <= end_dt)
    logs = query.order_by(AttendanceLog.timestamp.asc()).all()

    filtered_logs = []
    for log in logs:
        emp = log.employee
        if person_id and person_id.strip() not in (log.person_id or ""):
            continue
        emp_name = emp.name if emp else "Desconocido"
        if name and name.strip().lower() not in emp_name.lower():
            continue
        emp_dept = emp.department if emp else "Sin departamento"
        if department and department.strip().lower() not in emp_dept.lower():
            continue
        emp_shift = emp.shift.name if (emp and emp.shift) else "Sin turno"
        if shift and shift.strip().lower() not in emp_shift.lower():
            continue
        filtered_logs.append(log)

    return build_daily_summaries(filtered_logs)

# =====================================================================
#   RECEPTOR PRO DE EVENTOS EN TIEMPO REAL - CÁMARA HIKVISION
# =====================================================================

# =====================================================================
#     RECEPTOR ÚNICO Y ROBUSTO DE EVENTOS HIKVISION (TIEMPO REAL)
# =====================================================================

def reconcile_events(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """Trae del equipo los eventos del rango e inserta SOLO los que falten (sin duplicar).

    Lanza CameraEnrollmentError si la cámara responde con error. Si la cámara no está
    configurada, devuelve un resultado con reason='no_configurada' (no lanza)."""
    try:
        events = fetch_events(start_dt, end_dt)
        logger.info("Eventos: ")
        logger.info(events)
    except CameraNotConfigured as e:
        return {"ok": False, "imported": 0, "fetched": 0, "message": str(e), "reason": "no_configurada"}

    # Conjunto de marcas ya existentes en el rango (dedup por persona + segundo exacto)
    existing = db.query(AttendanceLog).filter(
        AttendanceLog.timestamp >= start_dt, AttendanceLog.timestamp <= end_dt
    ).all()
    seen = {(l.person_id, l.timestamp.replace(microsecond=0)) for l in existing}

    imported = 0
    for ev in sorted(events, key=lambda e: e["timestamp"]):
        key = (ev["person_id"], ev["timestamp"].replace(microsecond=0))
        if key in seen:
            continue
        _get_or_create_employee(db, ev["person_id"])
        # Alternancia: contar marcas previas de esa persona ese mismo día
        day_start = datetime.combine(ev["timestamp"].date(), time.min)
        marks_before = db.query(AttendanceLog).filter(
            AttendanceLog.person_id == ev["person_id"],
            AttendanceLog.timestamp >= day_start,
            AttendanceLog.timestamp < ev["timestamp"],
        ).count()
        db.add(AttendanceLog(
            person_id=ev["person_id"],
            timestamp=ev["timestamp"],
            event_type=determine_next_event_type(marks_before),
        ))
        db.flush()
        seen.add(key)
        imported += 1

    db.commit()
    return {
        "ok": True,
        "imported": imported,
        "fetched": len(events),
        "message": f"Se importaron {imported} marcaciones nuevas desde la cámara.",
    }


@app.post("/api/hikvision/pull-events")
def pull_events_from_camera(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD (por defecto: últimos 7 días)"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD (por defecto: hoy)"),
    db: Session = Depends(get_db),
):
    """Reconciliación manual: trae del equipo los eventos guardados localmente (p. ej.
    mientras el servidor estuvo caído) e inserta SOLO los que falten, sin duplicar."""
    now = datetime.now()
    start_dt = (datetime.combine(date.fromisoformat(start_date), time.min)
                if start_date else now - timedelta(days=7))
    end_dt = (datetime.combine(date.fromisoformat(end_date), time.max)
              if end_date else datetime.combine(now.date(), time.max))
    try:
        return reconcile_events(db, start_dt, end_dt)
    except CameraEnrollmentError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/attendance/export")
def export_attendance_excel(
    start_date: Optional[str] = Query(None, description="Formato YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="Formato YYYY-MM-DD"),
    name: Optional[str] = Query(None),
    person_id: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    shift: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    start_dt, end_dt = _parse_date_range(start_date, end_date)
    query = db.query(AttendanceLog).options(
        joinedload(AttendanceLog.employee).joinedload(Employee.shift)
    )
    if start_dt:
        query = query.filter(AttendanceLog.timestamp >= start_dt)
    if end_dt:
        query = query.filter(AttendanceLog.timestamp <= end_dt)

    logs = query.order_by(AttendanceLog.timestamp.asc()).all()

    filtered_logs = []
    for log in logs:
        emp = log.employee
        if person_id and person_id.strip() not in (log.person_id or ""):
            continue
        emp_name = emp.name if emp else "Desconocido"
        if name and name.strip().lower() not in emp_name.lower():
            continue
        emp_dept = emp.department if emp else "Sin departamento"
        if department and department.strip().lower() not in emp_dept.lower():
            continue
        emp_shift = emp.shift.name if (emp and emp.shift) else "Sin turno"
        if shift and shift.strip().lower() not in emp_shift.lower():
            continue
        filtered_logs.append(log)

    excel_bytes = generate_attendance_excel(filtered_logs)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=reporte_asistencia_guayamuri.xlsx"},
    )


@app.websocket("/ws/attendance")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Error en WebSocket: {e}")
        manager.disconnect(websocket)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")

# =====================================================================
#          MÓDULO DE CAPTURA MULTI-RUTA (SOPORTE PARA EL FRONTEND)
# =====================================================================

@app.post("/api/employees/capture-live-hardware")
@app.post("/api/capture-live-hardware")
@app.get("/api/employees/capture-live-hardware")
async def capture_live_hardware_multi_route(request: Request):
    """
    Endpoint comodín: Escucha tanto GET como POST en las rutas habituales
    para evitar que el frontend se quede colgado en 'Procesando...'.
    """
    logger.info(f"¡Petición de captura detectada! Método: {request.method} -> Ruta: {request.url.path}")
    
    protocolo = "https" if settings.HIKVISION_USE_HTTPS else "http"
    url = f"{protocolo}://{settings.HIKVISION_IP}:{settings.HIKVISION_PORT}/ISAPI/Streaming/channels/101/picture"

    
    try:
        auth = httpx.DigestAuth(settings.HIKVISION_USER, settings.HIKVISION_PASS)
        print(url, settings.HIKVISION_USER, settings.HIKVISION_PASS)
        
        async with httpx.AsyncClient(auth=auth) as client:
            logger.info(f"Conectando directamente con la cámara en: {url}")
            response = await client.get(url, timeout=5.0)
            
            if response.status_code == 200:
                logger.info("¡Imagen del rostro obtenida exitosamente desde el hardware!")
                encoded_image = base64.b64encode(response.content).decode("utf-8")
                return {
                    "status": "success", 
                    "image_base64": f"data:image/jpeg;base64,{encoded_image}"
                }
            else:
                logger.error(f"La cámara devolvió un código de error ISAPI: {response.status_code}")
                raise HTTPException(status_code=500, detail="La cámara no pudo procesar la captura.")
                
    except Exception as e:
        logger.error(f"Error de comunicación física con el hardware biométrico: {str(e)}")
        raise HTTPException(status_code=500, detail="No se pudo establecer conexión con la cámara Hikvision.")
    
    # =====================================================================
#         MÓDULO DE AUTENTICACIÓN (LOGIN) PARA EL COLEGIO GUAYAMURI
# =====================================================================

class LoginRequest(BaseModel):
        username: str
        password: str

@app.post("/api/auth/login")
def login_usuario(payload: LoginRequest):
        """
        Endpoint para autenticar a los usuarios del sistema de asistencia.
        Envía las credenciales y genera los datos que el login.html necesita.
        """
        logger.info(f"Intento de inicio de sesión para el usuario: {payload.username}")
        
        # NOTA: Aquí puedes definir las credenciales de administrador para tu sistema.
        # Puedes cambiarlas por el usuario y contraseña que prefieras usar.
        USUARIO_CORRECTO = "admin"
        CONTRASENA_CORRECTA = "guayamuri2026"

        if payload.username == USUARIO_CORRECTO and payload.password == CONTRASENA_CORRECTA:
            logger.info(f"¡Inicio de sesión exitoso para: {payload.username}!")
            return {
                "access_token": "token_secreto_guayamuri_asistencia_2026",
                "username": payload.username,
                "name": "Administrador de Sistemas",
                "role": "admin"
            }
        else:
            logger.warning(f"Credenciales inválidas para el usuario: {payload.username}")
            raise HTTPException(
                status_code=401, 
                detail="Usuario o contraseña incorrectos. Por favor, verifica."
            )