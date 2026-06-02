import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Control de Asistencia Guayamuri"
    DATABASE_URL: str = "sqlite:///./attendance.db"
    ALLOWED_ORIGINS: list = ["*"]

    # Carpeta donde se guardan las fotos de rostro de los empleados
    MEDIA_DIR: str = "media/employee_photos"

    # --- Cámara Hikvision (enrolamiento por ISAPI). Vacío = aún no configurada ---
    HIKVISION_HOST: str = ""          # Ej: "192.168.1.64" (sin http://)
    HIKVISION_PORT: int = 80
    HIKVISION_USER: str = ""
    HIKVISION_PASSWORD: str = ""
    HIKVISION_USE_HTTPS: bool = False

    # --- Reconciliación automática (recupera marcaciones guardadas en la cámara) ---
    RECONCILE_ENABLED: bool = True            # job de fondo activado
    RECONCILE_INTERVAL_MINUTES: int = 10      # cada cuánto consulta la cámara
    RECONCILE_LOOKBACK_DAYS: int = 2          # ventana hacia atrás que revisa cada ciclo

    @property
    def hikvision_configured(self) -> bool:
        return bool(self.HIKVISION_HOST and self.HIKVISION_USER and self.HIKVISION_PASSWORD)

    @property
    def hikvision_base_url(self) -> str:
        scheme = "https" if self.HIKVISION_USE_HTTPS else "http"
        return f"{scheme}://{self.HIKVISION_HOST}:{self.HIKVISION_PORT}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()
