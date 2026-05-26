import sqlite3
import pandas as pd

def generar_reporte_excel():
    print("Conectando a la base de datos...")
    # 1. Conectarse a la base de datos
    conexion = sqlite3.connect("asistencia_colegio.db")

    # 2. La consulta SQL con JOIN
    # Cruzamos los datos de 'empleados' con 'asistencia' usando el ID
    consulta_sql = """
        SELECT 
            e.cedula AS "Cédula",
            e.nombre AS "Nombres",
            e.apellido AS "Apellidos",
            e.departamento AS "Departamento",
            a.fecha AS "Fecha",
            a.hora_entrada AS "Hora de Entrada",
            a.hora_salida AS "Hora de Salida"
        FROM empleados e
        JOIN asistencia a ON e.id = a.empleado_id
        ORDER BY a.fecha DESC, a.hora_entrada ASC
    """

    try:
        # 3. Leer los datos directamente a un DataFrame de Pandas
        df = pd.read_sql_query(consulta_sql, conexion)

        # Verificar si hay datos
        if df.empty:
            print("No hay registros de asistencia para exportar.")
        else:
            # 4. Guardar como archivo Excel
            nombre_archivo = "Reporte_Asistencia_Guayamuri.xlsx"
            df.to_excel(nombre_archivo, index=False, engine='openpyxl')
            print(f"¡Éxito! El reporte se ha generado como: {nombre_archivo}")

    except Exception as e:
        print(f"Ocurrió un error al generar el reporte: {e}")
    
    finally:
        # 5. Siempre cerrar la conexión
        conexion.close()

if __name__ == "__main__":
    generar_reporte_excel()