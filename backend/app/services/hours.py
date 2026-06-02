"""Emparejamiento de marcas entrada/salida y cálculo de horas trabajadas/extra.

Reglas:
- Por cada empleado y por cada día, las marcas se ordenan cronológicamente.
- Se emparejan en orden: 1ª = entrada, 2ª = salida, 3ª = entrada, 4ª = salida, ...
- Las horas trabajadas del día = suma de (salida - entrada) de cada par.
- Las horas extra = max(0, horas_trabajadas - horas_estándar_del_turno).
- Si queda una entrada sin salida, el día se marca como incompleto.
"""
from collections import defaultdict
from datetime import date
from typing import List

DEFAULT_STANDARD_HOURS = 8.0


def _round(value: float) -> float:
    return round(value + 1e-9, 2)


def build_daily_summaries(logs: List) -> List[dict]:
    """Recibe una lista de AttendanceLog (con .employee cargado) y devuelve
    un resumen por empleado/día ordenado por fecha y nombre.
    """
    # Agrupar por (person_id, fecha)
    grouped = defaultdict(list)
    for log in logs:
        d: date = log.timestamp.date()
        grouped[(log.person_id, d)].append(log)

    summaries = []
    for (person_id, day), day_logs in grouped.items():
        day_logs.sort(key=lambda l: l.timestamp)
        employee = day_logs[0].employee

        standard_hours = DEFAULT_STANDARD_HOURS
        shift_name = "Sin turno"
        if employee and employee.shift:
            standard_hours = employee.shift.standard_hours
            shift_name = employee.shift.name

        worked_seconds = 0.0
        first_in = None
        last_out = None
        incomplete = False

        # Emparejar entrada/salida en orden cronológico
        i = 0
        n = len(day_logs)
        while i < n:
            entrada = day_logs[i]
            if first_in is None:
                first_in = entrada.timestamp
            if i + 1 < n:
                salida = day_logs[i + 1]
                worked_seconds += (salida.timestamp - entrada.timestamp).total_seconds()
                last_out = salida.timestamp
                i += 2
            else:
                # Entrada sin salida correspondiente
                incomplete = True
                i += 1

        worked_hours = _round(worked_seconds / 3600.0)
        overtime_hours = _round(max(0.0, worked_hours - standard_hours))

        summaries.append({
            "person_id": person_id,
            "name": employee.name if employee else "Desconocido",
            "department": employee.department if employee else "Sin departamento",
            "shift_name": shift_name,
            "date": day.isoformat(),
            "first_in": first_in.isoformat() if first_in else None,
            "last_out": last_out.isoformat() if last_out else None,
            "marks": n,
            "standard_hours": _round(standard_hours),
            "worked_hours": worked_hours,
            "overtime_hours": overtime_hours,
            "incomplete": incomplete,
        })

    summaries.sort(key=lambda s: (s["date"], s["name"]))
    return summaries


def determine_next_event_type(day_marks_count: int) -> str:
    """Dado cuántas marcas ya tiene el empleado HOY, devuelve el tipo de la siguiente.
    0 marcas -> entrada, 1 -> salida, 2 -> entrada, ...
    """
    return "entrada" if day_marks_count % 2 == 0 else "salida"
