"""
Centralna konfiguracja aplikacji.
Wszystkie ustawienia są czytane ze zmiennych środowiskowych / pliku .env
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # SubiektGT MS SQL
    subiekt_server: str = "10.0.0.10"
    subiekt_database: str = "astra_prod"
    subiekt_username: str = "reader"
    subiekt_password: str = ""
    subiekt_port: int = 1433

    # PostgreSQL analityczny
    analytics_host: str = "localhost"
    analytics_port: int = 5432
    analytics_database: str = "subiekt_analytics"
    analytics_username: str = "subiekt_app"
    analytics_password: str = ""

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    ollama_embed_model: str = "nomic-embed-text"

    # JWT
    secret_key: str = "IrBPI7VOViK0ijDMkLxjPBdyei8OnXcJo92griYwFgV"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    # Aplikacja
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False
    log_level: str = "info"

    # ETL
    etl_cron_hour: int = 2
    etl_history_days: int = 365

    # Magazyn
    default_warehouse_id: int = 1

    @property
    def subiekt_conn_str(self) -> str:
        return (
            f"mssql+pyodbc://{self.subiekt_username}:{self.subiekt_password}"
            f"@{self.subiekt_server}:{self.subiekt_port}/{self.subiekt_database}"
            f"?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
        )

    @property
    def analytics_conn_str(self) -> str:
        return (
            f"postgresql+psycopg2://{self.analytics_username}:{self.analytics_password}"
            f"@{self.analytics_host}:{self.analytics_port}/{self.analytics_database}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
