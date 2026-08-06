from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    redis_url: str | None = None
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    redis_timeout: float = 2.0

    idempotency_ttl: int = 86400
    idempotency_lock_ttl: int = 300

    circuit_breaker_fail_max: int = 5
    circuit_breaker_timeout: int = 60

settings = Settings()
