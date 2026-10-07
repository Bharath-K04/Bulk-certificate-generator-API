from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base


class Database:
    """Owns the engine and session factory. One instance per app (and per test)."""

    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        self.is_sqlite = parsed.get_backend_name() == "sqlite"
        connect_args: dict = {}

        if self.is_sqlite:
            # The worker threads and request threads share the engine.
            connect_args["check_same_thread"] = False
            if parsed.database and parsed.database != ":memory:":
                Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

        self.engine: Engine = create_engine(url, connect_args=connect_args)
        if self.is_sqlite:
            event.listen(self.engine, "connect", _configure_sqlite)

        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, expire_on_commit=False
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    # WAL lets API reads proceed while the worker is writing progress updates.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
