import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Control de Asistencia Guayamuri"
    DATABASE_URL: str = "sqlite:///./attendance.db"
    ALLOWED_ORIGINS: list = ["*"]

    # Carpeta donde se guardan las fotos de rostro de los empleados
    MEDIA_DIR: str = "media/employee_photos"

    # --- Cámara Hikvision (enrolamiento por ISAPI). Vacío = aún no configurada ---
    HIKVISION_USER: str = "admin"
    HIKVISION_PASS: str = "Hikvision12"  # Reemplázalo por la clave real de tu equipo
    HIKVISION_PASSWORD: str = "Hikvision12"
    HIKVISION_IP: str = "192.168.0.51"
    HIKVISION_HOST: str = ""          # Ej: "192.168.1.64" (sin http://)
    HIKVISION_PORT: int = 80
    HIKVISION_USE_HTTPS: bool = False

    # --- Polling en vivo (detecciones en tiempo real cada N segundos) ---
    LIVE_POLL_ENABLED: bool = True            # activa el polling rápido de la cámara
    LIVE_POLL_INTERVAL_SECONDS: int = 10      # cada cuántos segundos consulta la cámara

    # --- Reconciliación automática (recupera marcaciones guardadas en la cámara) ---
    RECONCILE_ENABLED: bool = True            # job de fondo activado
    RECONCILE_INTERVAL_MINUTES: int = 10      # cada cuánto consulta la cámara
    RECONCILE_LOOKBACK_DAYS: int = 2          # ventana hacia atrás que revisa cada ciclo

    class Config:
        env_file = ".env"

    @property
    def hikvision_configured(self) -> bool:
        # Acepta HIKVISION_HOST o HIKVISION_IP indistintamente
        host = self.HIKVISION_HOST or self.HIKVISION_IP
        return bool(host and self.HIKVISION_USER and self.HIKVISION_PASSWORD)

    @property
    def hikvision_ip_configured(self) -> bool:
        """True cuando la IP de la cámara está definida (suficiente para polling vía ISAPI)."""
        return bool(self.HIKVISION_IP and self.HIKVISION_USER and self.HIKVISION_PASS)

    @property
    def hikvision_base_url(self) -> str:
        scheme = "https" if self.HIKVISION_USE_HTTPS else "http"
        # Usa HIKVISION_HOST si está definido, si no cae en HIKVISION_IP
        host = self.HIKVISION_HOST or self.HIKVISION_IP
        return f"{scheme}://{host}:{self.HIKVISION_PORT}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()