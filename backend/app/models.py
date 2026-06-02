from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base

class Employee(Base):
    __tablename__ = "employees"

    id = Column(String(20), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    department = Column(String(100), nullable=False)

    attendances = relationship("AttendanceLog", back_populates="employee", cascade="all, delete-orphan")

class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    log_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    person_id = Column(String(20), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=func.now())

    employee = relationship("Employee", back_populates="attendances")
    