from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    go2rtc_url: str = "http://127.0.0.1:1984"
    go2rtc_user: str = "admin"
    go2rtc_password: str
    miio_tokens_file: str = "/config/miio_tokens.json"
    cookie_secure: bool = False
    session_ttl_hours: int = 168
    monitor_interval_s: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
