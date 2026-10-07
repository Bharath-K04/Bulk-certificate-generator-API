from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via environment variables prefixed with CERTGEN_.

    Example: CERTGEN_DATABASE_URL=postgresql+psycopg://user:pass@localhost/certs
    """

    model_config = SettingsConfigDict(env_prefix="CERTGEN_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/certificates.db"
    storage_dir: Path = Path("./storage/certificates")
    max_recipients_per_job: int = 1000
    worker_threads: int = 2
    # "background": jobs run on a thread pool and the POST returns immediately (default).
    # "inline": jobs run inside the request. Handy for debugging; used by the tests.
    processing_mode: Literal["background", "inline"] = "background"
