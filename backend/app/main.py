import logging
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.models import Employee, AttendanceLog
from app.services.excel import generate_attendance_excel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)
app = FastAPI(title=settings.PROJECT_NAME)

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
        self.active_connections.remove(websocket)
        logger.info(f"Conexión WebSocket cerrada. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error al enviar mensaje por WebSocket: {e}")

manager = ConnectionManager()

@app.on_event("startup")
def seed_database():
    db = next(get_db())
    if db.query(Employee).count() == 0:
        logger.info("Inicializando base de datos con empleados semilla...")
        seed_employees = [
            Employee(id="5473790", name="JULIA LUNA", department="GUAYAMURI/MANTENIMIENTO"),
            Employee(id="5478620", name="EMILIO CARABALLO", department="GUAYAMURI/MANTENIMIENTO"),
            Employee(id="5478900", name="MARIA GOMEZ", department="GUAYAMURI/ADMINISTRACION"),
            Employee(id="5479001", name="PEDRO PEREZ", department="GUAYAMURI/DOCENTE"),
            Employee(id="5481022", name="CARLOS MENDOZA", department="GUAYAMURI/DOCENTE"),
        ]
        db.add_all(seed_employees)
        db.commit()
        logger.info("Semillado completo.")

@app.get("/api/employees")
def get_employees(db: Session = Depends(get_db)):
    return db.query(Employee).all()

@app.post("/api/employees")
def create_employee(id: str, name: str, department: str, db: Session = Depends(get_db)):
    db_emp = db.query(Employee).filter(Employee.id == id).first()
    if db_emp:
        raise HTTPException(status_code=400, detail="El ID del empleado ya está registrado.")
    new_emp = Employee(id=id, name=name, department=department)
    db.add(new_emp)
    db.commit()
    db.refresh(new_emp)
    return new_emp

@app.get("/api/attendance")
def get_attendance(db: Session = Depends(get_db)):
    logs = db.query(AttendanceLog).order_by(AttendanceLog.timestamp.desc()).all()
    return [
        {
            "id": log.log_id,
            "person_id": log.person_id,
            "name": log.employee.name if log.employee else "Desconocido",
            "department": log.employee.department if log.employee else "Sin departamento",
            "timestamp": log.timestamp.isoformat(),
            "time_formatted": log.timestamp.strftime("%d/%m/%Y %H:%M"),
            "device_info": "Cámara Hikvision"
        }
        for log in logs
    ]

@app.delete("/api/attendance/clear")
def clear_attendance(db: Session = Depends(get_db)):
    db.query(AttendanceLog).delete()
    db.commit()
    return {"message": "Logs de asistencia vaciados correctamente."}

@app.post("/api/hikvision/event")
async def receive_hikvision_event(payload: dict, db: Session = Depends(get_db)):
    person_id = payload.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Se requiere el person_id en el payload.")
        
    employee = db.query(Employee).filter(Employee.id == person_id).first()
    if not employee:
        employee = Employee(id=person_id, name="Empleado Desconocido", department="Por Asignar")
        db.add(employee)
        db.commit()
        db.refresh(employee)

    log = AttendanceLog(person_id=person_id, timestamp=datetime.now())
    db.add(log)
    db.commit()
    db.refresh(log)

    websocket_payload = {
        "id": log.log_id,
        "person_id": log.person_id,
        "name": employee.name,
        "department": employee.department,
        "timestamp": log.timestamp.isoformat(),
        "time_formatted": log.timestamp.strftime("%d/%m/%Y %H:%M"),
        "device_info": "Cámara Hikvision"
    }

    await manager.broadcast(websocket_payload)
    return {"status": "success", "recorded_attendance": websocket_payload}

@app.get("/api/attendance/export")
def export_attendance_excel(
    start_date: Optional[str] = Query(None, description="Formato YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="Formato YYYY-MM-DD"),
    db: Session = Depends(get_db)
):
    query = db.query(AttendanceLog)
    if start_date:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        query = query.filter(AttendanceLog.timestamp >= start_dt)
    if end_date:
        end_dt = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
        query = query.filter(AttendanceLog.timestamp <= end_dt)
        
    logs = query.order_by(AttendanceLog.timestamp.asc()).all()
    excel_bytes = generate_attendance_excel(logs, sheet_name="Mazro- Abril")
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=reporte_asistencia_guayamuri.xlsx"}
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