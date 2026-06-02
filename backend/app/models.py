from sqlalchemy import Column, String, DateTime, Integer, Float, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class Shift(Base):
    """Turno de trabajo configurable por el administrador.

    - standard_hours: horas estándar antes de que empiecen a contar las horas extra.
    - start_time / end_time: horario referencial del turno (formato "HH:MM"), informativo.
    """
    __tablename__ = "shifts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    start_time = Column(String(5), nullable=True)   # "HH:MM"
    end_time = Column(String(5), nullable=True)     # "HH:MM"
    standard_hours = Column(Float, nullable=False, default=8.0)

    employees = relationship("Employee", back_populates="shift")


class Employee(Base):
    __tablename__ = "employees"

    id = Column(String(20), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    department = Column(String(100), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True)

    # --- Datos para enrolamiento en la cámara Hikvision (uso futuro) ---
    # Nombre del archivo de la foto del rostro almacenada en media/employee_photos/
    photo_path = Column(String(255), nullable=True)
    # Número de tarjeta/credencial opcional (Hikvision lo soporta junto al rostro)
    card_number = Column(String(50), nullable=True)
    # Estado de sincronización con la cámara: "pendiente" | "sincronizado" | "error"
    camera_status = Column(String(20), nullable=False, default="pendiente")
    camera_synced_at = Column(DateTime, nullable=True)

    shift = relationship("Shift", back_populates="employees")
    attendances = relationship(
        "AttendanceLog", back_populates="employee", cascade="all, delete-orphan"
    )

    @property
    def has_photo(self) -> bool:
        return bool(self.photo_path)


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    log_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    person_id = Column(
        String(20), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    timestamp = Column(DateTime, nullable=False, default=func.now())
    # "entrada" | "salida" — determinado por alternancia diaria al momento de marcar.
    event_type = Column(String(10), nullable=False, default="entrada")

    employee = relationship("Employee", back_populates="attendances")
