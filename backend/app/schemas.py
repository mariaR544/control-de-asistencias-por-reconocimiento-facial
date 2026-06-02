from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


# ---------- Turnos ----------
class ShiftBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    start_time: Optional[str] = Field(None, description="Horario referencial de entrada, formato HH:MM")
    end_time: Optional[str] = Field(None, description="Horario referencial de salida, formato HH:MM")
    standard_hours: float = Field(8.0, gt=0, le=24, description="Horas estándar antes de horas extra")


class ShiftCreate(ShiftBase):
    pass


class ShiftUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    standard_hours: Optional[float] = Field(None, gt=0, le=24)


class ShiftOut(ShiftBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Empleados ----------
class EmployeeBase(BaseModel):
    id: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    department: str = Field(..., min_length=1, max_length=100)
    shift_id: Optional[int] = None
    card_number: Optional[str] = Field(None, max_length=50)


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    department: Optional[str] = Field(None, min_length=1, max_length=100)
    shift_id: Optional[int] = None
    card_number: Optional[str] = Field(None, max_length=50)


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    department: str
    shift_id: Optional[int] = None
    card_number: Optional[str] = None
    shift: Optional[ShiftOut] = None
    has_photo: bool = False
    camera_status: str = "pendiente"


# ---------- Evento de cámara ----------
class HikvisionEvent(BaseModel):
    person_id: str = Field(..., min_length=1, max_length=20)
    device_info: Optional[str] = "Cámara Hikvision"
    # Opcional: si la cámara reporta dirección, se respeta; si no, se alterna automáticamente.
    event_type: Optional[str] = Field(None, description="'entrada' o 'salida' (opcional)")
