"""Cliente de enrolamiento para cámaras/terminales faciales Hikvision (ISAPI).

La cámara aún no está disponible. Este módulo deja todo listo para que, cuando se
configure (variables HIKVISION_* en .env), el sistema pueda dar de alta a un empleado
y subir su foto de rostro al equipo automáticamente.

Flujo de enrolamiento (ISAPI, formato JSON):
  1) Alta/edición de la persona:  POST /ISAPI/AccessControl/UserInfo/Record?format=json
  2) Carga del rostro:            POST /ISAPI/Intelligent/FDLib/FaceDataRecord?format=json

Notas:
  - La autenticación es HTTP Digest con usuario/clave del equipo.
  - Algunos modelos usan endpoints ligeramente distintos (p. ej. AccessControl/FaceDataRecord).
    Si al conectar la cámara algún modelo difiere, se ajusta aquí en un solo lugar.
"""
import json
import logging
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)

# Validez por defecto de la credencial de la persona en el equipo.
_VALID_BEGIN = "2020-01-01T00:00:00"
_VALID_END = "2037-12-31T23:59:59"


class CameraNotConfigured(Exception):
    """La cámara Hikvision aún no está configurada en .env."""


class CameraEnrollmentError(Exception):
    """Falló una llamada ISAPI durante el enrolamiento."""


def build_user_payload(employee) -> dict:
    """Construye el cuerpo ISAPI para dar de alta a la persona en el equipo."""
    user = {
        "employeeNo": str(employee.id),
        "name": employee.name,
        "userType": "normal",
        "Valid": {
            "enable": True,
            "beginTime": _VALID_BEGIN,
            "endTime": _VALID_END,
            "timeType": "local",
        },
    }
    if employee.card_number:
        # El número de tarjeta se asocia por separado vía CardInfo en la mayoría de
        # modelos; lo incluimos aquí como referencia para el enrolamiento posterior.
        user["numOfCard"] = 1
    return {"UserInfo": user}


def build_face_record_payload(employee) -> dict:
    """Metadatos del rostro a subir (la imagen va como parte binaria aparte)."""
    return {
        "faceLibType": "blackFD",
        "FDID": "1",
        "FPID": str(employee.id),
    }


def enroll_employee(employee, photo_bytes: bytes) -> str:
    """Da de alta al empleado y sube su rostro al equipo Hikvision.

    Devuelve un mensaje de éxito. Lanza CameraNotConfigured si falta configuración
    o CameraEnrollmentError si la cámara responde con error.
    """
    if not settings.hikvision_configured:
        raise CameraNotConfigured(
            "La cámara Hikvision no está configurada (define HIKVISION_HOST/USER/PASSWORD en .env)."
        )
    if not photo_bytes:
        raise CameraEnrollmentError("El empleado no tiene foto de rostro para enrolar.")

    try:
        import requests
        from requests.auth import HTTPDigestAuth
    except ImportError as e:
        raise CameraEnrollmentError(
            "Falta la librería 'requests'. Instálala con: pip install requests"
        ) from e

    base = settings.hikvision_base_url
    auth = HTTPDigestAuth(settings.HIKVISION_USER, settings.HIKVISION_PASSWORD)
    verify = False  # certificados autofirmados típicos en equipos locales

    # 1) Alta de la persona
    user_url = f"{base}/ISAPI/AccessControl/UserInfo/Record?format=json"
    try:
        r = requests.post(
            user_url, auth=auth, json=build_user_payload(employee),
            timeout=10, verify=verify,
        )
    except requests.RequestException as e:
        raise CameraEnrollmentError(f"No se pudo contactar la cámara: {e}") from e

    # Si la persona ya existe, intentar modificarla en vez de crearla.
    if r.status_code >= 400 or '"statusCode":4' in r.text:
        modify_url = f"{base}/ISAPI/AccessControl/UserInfo/Modify?format=json"
        try:
            r = requests.put(
                modify_url, auth=auth, json=build_user_payload(employee),
                timeout=10, verify=verify,
            )
        except requests.RequestException as e:
            raise CameraEnrollmentError(f"No se pudo contactar la cámara: {e}") from e
        if r.status_code >= 400:
            raise CameraEnrollmentError(
                f"Error al registrar persona en la cámara (HTTP {r.status_code}): {r.text[:300]}"
            )

    # 2) Carga del rostro (multipart: metadatos JSON + imagen)
    face_url = f"{base}/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json"
    files = {
        "FaceDataRecord": (None, json.dumps(build_face_record_payload(employee)), "application/json"),
        "img": ("face.jpg", photo_bytes, "image/jpeg"),
    }
    try:
        rf = requests.post(face_url, auth=auth, files=files, timeout=15, verify=verify)
    except requests.RequestException as e:
        raise CameraEnrollmentError(f"No se pudo subir el rostro a la cámara: {e}") from e
    if rf.status_code >= 400:
        raise CameraEnrollmentError(
            f"Error al subir el rostro (HTTP {rf.status_code}): {rf.text[:300]}"
        )

    logger.info("Empleado %s enrolado en la cámara correctamente.", employee.id)
    return f"Empleado {employee.name} enrolado en la cámara correctamente."


def fetch_events(start_time: datetime, end_time: datetime) -> list:
    """Recupera del equipo los eventos de acceso ocurridos entre dos fechas.

    Sirve para RECONCILIAR cuando el servidor estuvo caído: la cámara/terminal
    guarda los registros localmente y aquí los traemos por ISAPI (AcsEvent search)
    para insertar los que falten en la base de datos.

    Devuelve una lista de dicts: {"person_id": str, "timestamp": datetime}.
    Nota: el código `minor` y los nombres de campo pueden variar según el modelo;
    se ajustan aquí en un único lugar al conectar el equipo real.
    """
    if not settings.hikvision_configured:
        raise CameraNotConfigured(
            "La cámara Hikvision no está configurada (define HIKVISION_HOST/USER/PASSWORD en .env)."
        )
    try:
        import requests
        from requests.auth import HTTPDigestAuth
    except ImportError as e:
        raise CameraEnrollmentError("Falta la librería 'requests'. Instálala con: pip install requests") from e

    base = settings.hikvision_base_url
    auth = HTTPDigestAuth(settings.HIKVISION_USER, settings.HIKVISION_PASSWORD)
    url = f"{base}/ISAPI/AccessControl/AcsEvent?format=json"

    results = []
    position = 0
    page_size = 50
    while True:
        body = {
            "AcsEventCond": {
                "searchID": "guayamuri-sync",
                "searchResultPosition": position,
                "maxResults": page_size,
                "major": 5,          # 5 = evento de control de acceso
                "minor": 0,          # 0 = todos los subtipos
                "startTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "endTime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        }
        try:
            r = requests.post(url, auth=auth, json=body, timeout=15, verify=False)
        except requests.RequestException as e:
            raise CameraEnrollmentError(f"No se pudo consultar eventos en la cámara: {e}") from e
        if r.status_code >= 400:
            raise CameraEnrollmentError(
                f"Error al consultar eventos (HTTP {r.status_code}): {r.text[:300]}"
            )

        data = r.json().get("AcsEvent", {})
        info_list = data.get("InfoList", []) or []
        for ev in info_list:
            person_id = ev.get("employeeNoString") or ev.get("employeeNo")
            ts_raw = ev.get("time")
            if not person_id or not ts_raw:
                continue
            try:
                # La cámara devuelve ISO 8601, a veces con offset de zona horaria.
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ts = ts.replace(tzinfo=None)
            except ValueError:
                continue
            results.append({"person_id": str(person_id), "timestamp": ts})

        num_matches = data.get("numOfMatches", 0)
        total = data.get("totalMatches", 0)
        position += num_matches
        if num_matches == 0 or position >= total:
            break

    return results
