import os
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, date, time, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.MEDIA_DIR, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    run_light_migrations()
    seed_database()

    reconcile_task = None
    if settings.RECONCILE_ENABLED:
        # Pasada inmediata al arrancar (recupera lo ocurrido mientras estuvo caído),
        # luego el loop periódico.
        if settings.hikvision_configured:
            await asyncio.to_thread(_run_reconcile_once)
        reconcile_task = asyncio.create_task(_reconciliation_loop())

    yield

    if reconcile_task:
        reconcile_task.cancel()
        try:
            await reconcile_task
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
    return db.query(Employee).options(joinedload(Employee.shift)).all()


@app.post("/api/employees", response_model=EmployeeOut, status_code=201)
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db)):
    if db.query(Employee).filter(Employee.id == payload.id).first():
        raise HTTPException(status_code=400, detail="El ID del empleado ya está registrado.")
    if payload.shift_id is not None and not db.query(Shift).filter(Shift.id == payload.shift_id).first():
        raise HTTPException(status_code=400, detail="El turno indicado no existe.")
    emp = Employee(**payload.model_dump())
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@app.put("/api/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: str, payload: EmployeeUpdate, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Empleado no encontrado.")
    data = payload.model_dump(exclude_unset=True)
    if data.get("shift_id") is not None and not db.query(Shift).filter(Shift.id == data["shift_id"]).first():
        raise HTTPException(status_code=400, detail="El turno indicado no existe.")
    for k, v in data.items():
        setattr(emp, k, v)
    # Si cambian datos enrolados en la cámara, marcar para re-sincronizar.
    if "name" in data or "card_number" in data:
        emp.camera_status = "pendiente"
        emp.camera_synced_at = None
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
def _serialize_log(log: AttendanceLog) -> dict:
    return {
        "id": log.log_id,
        "person_id": log.person_id,
        "name": log.employee.name if log.employee else "Desconocido",
        "department": log.employee.department if log.employee else "Sin departamento",
        "event_type": log.event_type or "entrada",
        "timestamp": log.timestamp.isoformat(),
        "time_formatted": log.timestamp.strftime("%d/%m/%Y %H:%M"),
        "device_info": "Cámara Hikvision",
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
    db: Session = Depends(get_db),
):
    start_dt, end_dt = _parse_date_range(start_date, end_date)
    query = db.query(AttendanceLog).options(joinedload(AttendanceLog.employee))
    if start_dt:
        query = query.filter(AttendanceLog.timestamp >= start_dt)
    if end_dt:
        query = query.filter(AttendanceLog.timestamp <= end_dt)
    logs = query.order_by(AttendanceLog.timestamp.desc()).all()
    return [_serialize_log(log) for log in logs]


@app.get("/api/attendance/summary")
def get_summary(
    start_date: str = None, 
    end_date: str = None, 
    department: str = Query(None), # <-- Nuevo parámetro de filtro
    db: Session = Depends(get_db)
):
    # Unimos (JOIN) las marcaciones con los empleados para poder filtrar por su departamento
    query = db.query(AttendanceLog).join(Employee)
    
    if start_date:
        query = query.filter(AttendanceLog.timestamp >= f"{start_date} 00:00:00")
    if end_date:
        query = query.filter(AttendanceLog.timestamp <= f"{end_date} 23:59:59")
    if department:
        query = query.filter(Employee.department == department) # <-- Filtrado activo
        
    logs = query.all()
    return build_daily_summaries(logs)


@app.delete("/api/attendance/clear")
def clear_attendance(db: Session = Depends(get_db)):
    db.query(AttendanceLog).delete()
    db.commit()
    return {"message": "Logs de asistencia vaciados correctamente."}


@app.post("/api/hikvision/event")
async def receive_hikvision_event(payload: HikvisionEvent, db: Session = Depends(get_db)):
    person_id = payload.person_id

    employee = db.query(Employee).filter(Employee.id == person_id).first()
    if not employee:
        employee = Employee(id=person_id, name="Empleado Desconocido", department="Por Asignar")
        db.add(employee)
        db.commit()
        db.refresh(employee)

    now = datetime.now()

    # Determinar entrada/salida: respeta el tipo enviado por la cámara si viene;
    # de lo contrario alterna según las marcas del día.
    if payload.event_type in ("entrada", "salida"):
        event_type = payload.event_type
    else:
        today_start = datetime.combine(now.date(), time.min)
        today_end = datetime.combine(now.date(), time.max)
        marks_today = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.person_id == person_id,
                AttendanceLog.timestamp >= today_start,
                AttendanceLog.timestamp <= today_end,
            )
            .count()
        )
        event_type = determine_next_event_type(marks_today)

    log = AttendanceLog(person_id=person_id, timestamp=now, event_type=event_type)
    db.add(log)
    db.commit()
    db.refresh(log)

    websocket_payload = _serialize_log(log)
    await manager.broadcast(websocket_payload)
    return {"status": "success", "recorded_attendance": websocket_payload}


def _get_or_create_employee(db: Session, person_id: str) -> Employee:
    emp = db.query(Employee).filter(Employee.id == person_id).first()
    if not emp:
        emp = Employee(id=person_id, name="Empleado Desconocido", department="Por Asignar")
        db.add(emp)
        db.flush()
    return emp


def reconcile_events(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """Trae del equipo los eventos del rango e inserta SOLO los que falten (sin duplicar).

    Lanza CameraEnrollmentError si la cámara responde con error. Si la cámara no está
    configurada, devuelve un resultado con reason='no_configurada' (no lanza)."""
    try:
        events = fetch_events(start_dt, end_dt)
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
    excel_bytes = generate_attendance_excel(logs)
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
