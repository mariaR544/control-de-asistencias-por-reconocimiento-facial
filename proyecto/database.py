import sqlite3

def inicializar_base_de_datos():
    # Conecta a la base de datos (se creará el archivo si no existe)
    conexion = sqlite3.connect("asistencia_colegio.db")
    cursor = conexion.cursor()

    # Habilitar el soporte de llaves foráneas en SQLite
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Crear tabla Empleados
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS empleados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cedula TEXT UNIQUE NOT NULL,
        nombre TEXT NOT NULL,
        apellido TEXT NOT NULL,
        departamento TEXT,
        rostro_enrolled BLOB
    );
    """)

    # 2. Crear tabla Asistencia
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS asistencia (
        id_asistencia INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        fecha TEXT NOT NULL,
        hora_entrada TEXT,
        hora_salida TEXT,
        FOREIGN KEY (empleado_id) REFERENCES empleados (id) ON DELETE CASCADE
    );
    """)

    # Guardar cambios y cerrar
    conexion.commit()
    conexion.close()
    print("¡Base de datos e inicializada con éxito!")

if __name__ == "__main__":
    inicializar_base_de_datos()