import io
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def generate_attendance_excel(logs, sheet_name="Asistencia"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.views.sheetView[0].showGridLines = True

    # Cabeceras exactas
    headers = ["Person ID", "Name", "Department", "Time"]
    ws.append(headers)

    # Colores y fuentes
    header_fill = PatternFill(start_color="375623", end_color="375623", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    even_row_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    data_font = Font(name="Calibri", size=11, bold=False, color="000000")
    
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    # Estilo de fila 1
    for col_idx in range(1, 5):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = thin_border

    # Agregar filas de datos
    for r_idx, log in enumerate(logs, start=2):
        person_id_val = f"'{log.person_id}" # Con comilla simple al inicio
        name_val = log.employee.name.upper() if log.employee else "DESCONOCIDO"
        dept_val = log.employee.department.upper() if log.employee else "SIN DEPARTAMENTO"
        time_val = log.timestamp

        ws.append([person_id_val, name_val, dept_val, time_val])
        current_fill = white_fill if r_idx % 2 == 0 else even_row_fill

        for col_idx in range(1, 5):
            cell = ws.cell(row=r_idx, column=col_idx)
            cell.fill = current_fill
            cell.font = data_font
            cell.border = thin_border
            if col_idx == 4:
                cell.number_format = "d/m/yyyy h:mm"
            cell.alignment = Alignment(horizontal="left", vertical="center")

    last_row = len(logs) + 1
    if last_row > 1:
        ws.auto_filter.ref = f"A1:D{last_row}"

    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if isinstance(cell.value, datetime):
                cell_len = 16
            elif cell.value:
                cell_len = len(str(cell.value))
            else:
                cell_len = 0
            if cell_len > max_len:
                max_len = cell_len
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)
    return file_stream.getvalue()