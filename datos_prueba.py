import sqlite3

def inyectar_datos():
    conexion = sqlite3.connect("asistencia_colegio.db")
    cursor = conexion.cursor()

    # Insertar empleados falsos (Cédula, Nombre, Apellido, Departamento)
    cursor.execute("INSERT OR IGNORE INTO empleados (cedula, nombre, apellido, departamento) VALUES ('V-12345678', 'Carlos', 'Pérez', 'Docente')")
    cursor.execute("INSERT OR IGNORE INTO empleados (cedula, nombre, apellido, departamento) VALUES ('V-87654321', 'María', 'Gómez', 'Administrativo')")

    # Insertar asistencias falsas (empleado_id, fecha, hora_entrada, hora_salida)
    # Suponiendo que Carlos es el ID 1 y María el ID 2
    cursor.execute("INSERT INTO asistencia (empleado_id, fecha, hora_entrada, hora_salida) VALUES (1, '2026-05-26', '07:15:00', '14:30:00')")
    cursor.execute("INSERT INTO asistencia (empleado_id, fecha, hora_entrada, hora_salida) VALUES (2, '2026-05-26', '07:45:00', '16:00:00')")

    conexion.commit()
    conexion.close()
    print("¡Datos de prueba inyectados correctamente!")

if __name__ == "__main__":
    inyectar_datos()