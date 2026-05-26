from flask import Flask, render_template, jsonify, send_file, request, redirect, url_for
import sqlite3
import os
import cv2
import face_recognition
import pickle # Nos ayuda a convertir la lista de la IA en formato binario para SQLite
from reportes import generar_reporte_excel
import numpy as np
import time

app = Flask(__name__)

# 📌 DEFINICIÓN DE LA RUTA ABSOLUTA DE LA BASE DE DATOS 
# Esto evita que Flask cree archivos .db vacíos según la carpeta desde donde abras la terminal.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "asistencia_colegio.db")

@app.route('/')
def index():
    return render_template('index.html')

# Nueva Ruta: Muestra el formulario de registro
@app.route('/registro')
def registro():
    return render_template('registro.html')

# Nueva Ruta: Procesa el formulario, abre la webcam y guarda en la BD
@app.route('/procesar-registro', methods=['POST'])
def procesar_registro():
    cedula = request.form['cedula']
    nombre = request.form['nombre']
    apellido = request.form['apellido']
    departamento = request.form['departamento']

    print(f"Iniciando captura de rostro para: {nombre} {apellido}")

    # 1. Encender la cámara web con DirectShow (CAP_DSHOW) para Windows
    camara = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    
    if not camara.isOpened():
        print("ERROR: No se pudo acceder a la cámara en el módulo de Registro.")
        return redirect(url_for('index'))

    print("Calentando cámara de registro...")
    time.sleep(2) # Espera técnica para que el sensor reciba luz
    
    rostro_codificado = None

    while True:
        ret, fotograma = camara.read()
        
        # Filtro protector por si el fotograma viene vacío
        if not ret or fotograma is None:
            continue

        # Mostrar instrucciones en la pantalla de video
        cv2.putText(fotograma, "Mira a la camara y presiona SPACE para tomar foto", (10, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow("Registro Biometrico - Colegio Guayamuri", fotograma)

        # Esperar a que el usuario presione la tecla Espacio (código 32)
        tecla = cv2.waitKey(1) & 0xFF
        if tecla == 32: # Tecla Espacio
            # Convertir la imagen de BGR (OpenCV) a RGB (face_recognition)
            imagen_rgb = cv2.cvtColor(fotograma, cv2.COLOR_BGR2RGB)
            
            # Buscar rostros en la foto
            ubicaciones_rostros = face_recognition.face_locations(imagen_rgb)
            codificaciones_rostros = face_recognition.face_encodings(imagen_rgb, ubicaciones_rostros)

            if len(codificaciones_rostros) > 0:
                # ¡Encontramos el rostro! Guardamos el primero que vea
                rostro_codificado = codificaciones_rostros[0]
                print("¡Rostro capturado con éxito!")
                break
            else:
                print("No se detectó ningún rostro. Inténtalo de nuevo.")
        
        # Por si quiere cancelar presionando la tecla 'Esc'
        elif tecla == 27: 
            break

    # Apagar cámara y cerrar ventanas de video
    camara.release()
    cv2.destroyAllWindows()

    # 2. Si logramos codificar el rostro, guardamos todo en SQLite usando DB_PATH
    if rostro_codificado is not None:
        try:
            # Convertimos el array de la IA en un objeto binario (BLOB)
            rostro_blob = pickle.dumps(rostro_codificado)

            conexion = sqlite3.connect(DB_PATH)
            cursor = conexion.cursor()
            
            cursor.execute("""
                INSERT INTO empleados (cedula, nombre, apellido, departamento, rostro_enrolled)
                VALUES (?, ?, ?, ?, ?)
            """, (cedula, nombre, apellido, departamento, rostro_blob))
            
            conexion.commit()
            conexion.close()
            print("Empleado registrado exitosamente en la base de datos.")
            
        except sqlite3.IntegrityError:
            print("Error: Esa cédula ya se encuentra registrada.")
        except Exception as e:
            print(f"Error inesperado: {e}")

    # Al terminar, nos devuelve automáticamente a la página principal
    return redirect(url_for('index'))


# --- RUTAS DE ASISTENCIA Y EXCEL ---
@app.route('/api/asistencia')
def api_asistencia():
    conexion = sqlite3.connect(DB_PATH)
    cursor = conexion.cursor()
    consulta = """
        SELECT e.cedula, e.nombre || ' ' || e.apellido AS empleado, e.departamento, a.hora_entrada, a.hora_salida
        FROM asistencia a
        JOIN empleados e ON a.empleado_id = e.id
        WHERE a.fecha = date('now', 'localtime') 
        ORDER BY a.id_asistencia DESC
    """
    cursor.execute(consulta)
    registros = cursor.fetchall()
    conexion.close()
    
    datos = []
    for r in registros:
        datos.append({
            "cedula": r[0],
            "empleado": r[1],
            "departamento": r[2],
            "hora": r[4] if r[4] else r[3],
            "estado": "🟢 Salió" if r[4] else "🔵 Entró"
        })
    return jsonify(datos)

@app.route('/descargar-excel')
def descargar_excel():
    generar_reporte_excel()
    nombre_archivo = "Reporte_Asistencia_Guayamuri.xlsx"
    if os.path.exists(nombre_archivo):
        return send_file(nombre_archivo, as_attachment=True)
    return "Error al generar el archivo", 500


# --- CONFIGURACIÓN DE ESCÁNER EN VIVO TOTALMENTE INMUNE A CAÍDAS ---
@app.route('/iniciar-camara')
def iniciar_camara():
    print("Cargando rostros autorizados desde la base de datos...")
    
    # 1. Obtener todos los empleados y sus rostros desde SQLite usando DB_PATH
    conexion = sqlite3.connect(DB_PATH)
    cursor = conexion.cursor()
    cursor.execute("SELECT id, nombre, apellido, rostro_enrolled FROM empleados")
    registros = cursor.fetchall()
    conexion.close()

    rostros_conocidos_codigos = []
    empleados_informacion = []

    for r in registros:
        emp_id, nombre, apellido, rostro_blob = r
        if rostro_blob:
            rostro_codificado = pickle.loads(rostro_blob)
            rostros_conocidos_codigos.append(rostro_codificado)
            empleados_informacion.append({
                "id": emp_id,
                "nombre_completo": f"{nombre} {apellido}"
            })

    if not rostros_conocidos_codigos:
        print("No hay ningún personal registrado con rostro en el sistema.")
        return "No hay usuarios registrados en el sistema. Registra a alguien primero.", 400

    # 2. Encender la cámara usando DirectShow nativo de Windows (CAP_DSHOW)
    camara = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    
    if not camara.isOpened():
        print("ERROR CRÍTICO: No se pudo conectar a la cámara web.")
        return redirect(url_for('index'))

    print("Calentando la cámara del Escáner...")
    time.sleep(2) 
    print("¡Cámara de asistencia activa! Presiona 'ESC' en la ventana de video para cerrarla.")

    while True:
        ret, fotograma = camara.read()
        
        # 🛡️ FILTRO 1: Si no lee el hardware o el objeto viene vacío, saltar de inmediato
        if not ret or fotograma is None:
            continue

        # 🛡️ FILTRO 2: Si las dimensiones son inválidas, saltar
        if len(fotograma.shape) < 2 or fotograma.shape[0] == 0 or fotograma.shape[1] == 0:
            continue

        # 🛡️ BLOQUE ANTI-CRASH COMPLETO: Metemos todo el procesamiento crítico aquí dentro
        try:
            # Reducir tamaño
            fotograma_pequeno = cv2.resize(fotograma, (0, 0), fx=0.5, fy=0.5)
            
            if fotograma_pequeno is None or fotograma_pequeno.shape[0] == 0:
                continue
                
            # Convertir color
            rgb_pequeno = cv2.cvtColor(fotograma_pequeno, cv2.COLOR_BGR2RGB)

            # Detectar rostros en el fotograma actual
            ubicaciones_actuales = face_recognition.face_locations(rgb_pequeno)
            codificaciones_actuales = face_recognition.face_encodings(rgb_pequeno, ubicaciones_actuales)

        except Exception as e:
            # Si el hardware de la cámara falla por un milisegundo, la consola avisará,
            # pero el programa NO SE CAE, continúa procesando el siguiente cuadro de video.
            print(f"Fotograma ignorado por parpadeo o error de cámara: {e}")
            continue

        # Procesar las detecciones del rostro
        for codigo_rostro, ubicacion in zip(codificaciones_actuales, ubicaciones_actuales):
            coincidencias = face_recognition.compare_faces(rostros_conocidos_codigos, codigo_rostro, tolerance=0.5)
            nombre_pantalla = "Desconocido"

            if True in coincidencias:
                indice_coincidencia = coincidencias.index(True)
                empleado = empleados_informacion[indice_coincidencia]
                nombre_pantalla = empleado["nombre_completo"]
                empleado_id = empleado["id"]

                # --- LÓGICA DE ASISTENCIA EN SQLITE USANDO DB_PATH ---
                conexion = sqlite3.connect(DB_PATH)
                cursor = conexion.cursor()
                
                cursor.execute("""
                    SELECT id_asistencia, hora_salida FROM asistencia 
                    WHERE empleado_id = ? AND fecha = date('now', 'localtime')
                """, (empleado_id,))
                resultado_asistencia = cursor.fetchone()

                if resultado_asistencia is None:
                    cursor.execute("""
                        INSERT INTO asistencia (empleado_id, fecha, hora_entrada) 
                        VALUES (?, date('now', 'localtime'), time('now', 'localtime'))
                    """, (empleado_id,))
                    conexion.commit()
                    print(f"-> ENTRADA registrada para: {nombre_pantalla}")
                
                elif resultado_asistencia[1] is None:
                    id_asistencia = resultado_asistencia[0]
                    cursor.execute("""
                        UPDATE asistencia 
                        SET hora_salida = time('now', 'localtime') 
                        WHERE id_asistencia = ? AND hora_entrada < time('now', 'localtime', '-1 minute')
                    """, (id_asistencia,))
                    conexion.commit()
                    print(f"-> SALIDA registrada para: {nombre_pantalla}")
                
                conexion.close()

            top, right, bottom, left = ubicacion
            top *= 2; right *= 2; bottom *= 2; left *= 2
            
            color_cuadro = (0, 255, 0) if nombre_pantalla != "Desconocido" else (0, 0, 255)
            cv2.rectangle(fotograma, (left, top), (right, bottom), color_cuadro, 2)
            cv2.putText(fotograma, nombre_pantalla, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_cuadro, 2)

        # Mostrar la ventana en vivo
        cv2.imshow("Punto de Control Biometrico - Guayamuri", fotograma)

        if cv2.waitKey(1) & 0xFF == 27:
            break

    camara.release()
    cv2.destroyAllWindows()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)