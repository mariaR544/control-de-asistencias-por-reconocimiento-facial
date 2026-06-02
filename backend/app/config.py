import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Control de Asistencia Guayamuri"
    DATABASE_URL: str = "sqlite:///./attendance.db"
    ALLOWED_ORIGINS: list = ["*"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()
