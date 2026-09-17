# Sistema de Control de Asistencias con Reconocimiento Facial

Plataforma web desarrollada para una institución educativa con el objetivo de **sustituir el sistema de asistencia por huella dactilar** por un sistema de **reconocimiento facial**, integrando y gestionando de forma centralizada toda la información de asistencia del personal.

El sistema se conecta con el hardware y software de reconocimiento facial **hikVision**, permitiendo administrar empleados, turnos, marcaciones y reportes desde una única plataforma.

# Descripción del proyecto

La institución ya contaba con el hardware de reconocimiento facial de eVision, pero necesitaba una herramienta propia para:

- Gestionar el registro de empleados y sincronizarlos directamente con el hardware.
- Visualizar y controlar entradas, salidas y horas trabajadas.
- Calcular automáticamente las horas extras según el turno asignado.
- Generar reportes exportables en Excel para uso administrativo.

# Funcionalidades principales

El sistema está organizado en **cuatro módulos principales**:

# 1. Monitoreo
- Indicador de conexión en tiempo real con la cámara y el hardware de reconocimiento facial.
- Visualización de la última detección realizada por la cámara.
- Registro de reconocimientos con ID de la persona, nombre y hora de la marcación.
- Listado de registros del día, separados por **entradas** y **salidas**.

# 2. Gestión de empleados
- Alta, edición y eliminación de empleados.
- Carga de fotografía del empleado con **sincronización automática** hacia el hardware de reconocimiento facial para el enrolamiento (la imagen se transfiere directamente al software de eVision).
- Registro de empleados mediante los siguientes campos:
  - Person ID
  - Nombre completo
  - Departamento
  - Turno
  - Número de tarjeta (opcional)
  - Imagen facial
- Listado de empleados registrados con filtros de búsqueda por nombre, apellido, ID, departamento y turno.

# 3. Configuración de turnos
- Creación y gestión de turnos institucionales.
- Definición de hora de entrada, hora de salida y horas estándar del turno.
- Cálculo automático de horas extra: todo tiempo trabajado por encima de las horas estándar del turno se contabiliza como hora extra.
- Asignación de turnos a empleados desde el módulo de gestión de empleados.
- Listado de todos los turnos configurados.

# 4. Reportes
- Filtro por rango de fechas (fecha de inicio y fecha de fin).
- Filtro adicional por nombre, ID personal, departamento y turno.
- Visualización de:
  - Horas trabajadas
  - Horas extra
  - Bitácora de marcaciones
  - Resumen general de horas y marcaciones
- Exportación del reporte completo en formato **Excel**.

# Tecnologías utilizadas

- **Backend:** Python
- **Automatización / Scripts del sistema:** PowerShell
- **Base de datos:** SQLite
- **Frontend:** HTML, CSS
- **Integración de hardware:** API/SDK del sistema de reconocimiento facial eVision

# Mi rol en el proyecto

Desarrollo full stack del sistema: diseño e implementación de la base de datos, lógica de backend para la gestión de empleados, turnos, cálculo de horas extra y generación de reportes, así como la integración con el hardware de reconocimiento facial para el enrolamiento de empleados y la recepción de marcaciones en tiempo real.

# Capturas de pantalla

<img width="1920" height="1080" alt="19" src="https://github.com/user-attachments/assets/80123c0e-bbe9-40a4-a611-f5c65a974dcb" />
<img width="1920" height="1080" alt="20" src="https://github.com/user-attachments/assets/c1775070-bf3d-4679-9ba7-72d96eb53179" />
<img width="1920" height="1080" alt="21" src="https://github.com/user-attachments/assets/e8bbbe09-e7c0-4d5c-8553-74214368784d" />
<img width="1920" height="1080" alt="22" src="https://github.com/user-attachments/assets/80452103-3115-4362-ace1-89ddcb278477" />
<img width="1920" height="1080" alt="23" src="https://github.com/user-attachments/assets/b690ac50-8743-48b3-b89a-d815b036a375" />
<img width="1920" height="1080" alt="24" src="https://github.com/user-attachments/assets/cb6b9bed-c317-4660-801c-a4757508d5ea" />
<img width="1920" height="1080" alt="25" src="https://github.com/user-attachments/assets/d8f23ed2-3131-485c-a2d1-2ace7ceb1510" />
<img width="1920" height="1080" alt="26" src="https://github.com/user-attachments/assets/f7c79d97-68ec-4c36-8408-7e418c50ba01" />
<img width="1920" height="1080" alt="27" src="https://github.com/user-attachments/assets/8731415a-0fe3-4a71-a489-0c757c562aef" />

bash
# Clonar el repositorio
git clone https://github.com/mariaR544/control-de-asistencias-por-reconocimiento-facial.git
cd CONTROLDEASISTENCIAGUAYAMURI

# Crear entorno virtual e instalar dependencias
python -m venv venv
  # Activar el entorno virtual en PowerShell
  .venv\Scripts\Activate.ps1 (Si PowerShell muestra restricción de scripts, ejecuta antes: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass) # En Windows
  # En CMD (Símbolo del sistema)
  .venv\Scripts\activate.bat
# Instalar las dependencias
  python -m pip install --upgrade pip
  pip install -r requirements.txt


# Iniciar la aplicación
python run.py

# Autoría

Proyecto desarrollado por:
- Maria Rojas

# Licencia

*Todos los derechos reservados © 2026 Maria Rojas*

Este proyecto se comparte con fines de portafolio y demostración de habilidades técnicas. Queda prohibida su copia, modificación, distribución o uso comercial, total o parcial, sin autorización expresa y por escrito la autora.

Este proyecto se encuentra en evaluación para una futura implementación y comercialización con una institución educativa, por lo que no se autoriza su reutilización bajo ninguna licencia de código abierto.

Ver el archivo [`LICENSE`](./LICENSE) para más detalles.
