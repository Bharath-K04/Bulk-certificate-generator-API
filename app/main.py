import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import Settings
from app.database import Database
from app.generator import Renderer, render_certificate_pdf
from app.processing import JobProcessor
from app.services import unfinished_job_ids
from app.worker import Dispatcher, InlineDispatcher, ThreadPoolDispatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, renderer: Renderer | None = None) -> FastAPI:
    """App factory. Tests pass their own settings (temp DB/storage) and renderer."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(settings.database_url)
        database.create_all()
        settings.storage_dir.mkdir(parents=True, exist_ok=True)

        processor = JobProcessor(
            database.session_factory, settings.storage_dir, renderer or render_certificate_pdf
        )
        dispatcher: Dispatcher
        if settings.processing_mode == "inline":
            dispatcher = InlineDispatcher(processor.process)
        else:
            dispatcher = ThreadPoolDispatcher(processor.process, settings.worker_threads)

        app.state.settings = settings
        app.state.database = database
        app.state.processor = processor
        app.state.dispatcher = dispatcher

        # Crash recovery: anything left PENDING/PROCESSING by a previous run is resumed.
        # Only PENDING certificates are generated, so finished work is never redone.
        with database.session_factory() as session:
            for job_id in unfinished_job_ids(session):
                logger.info("Resuming unfinished job %s", job_id)
                dispatcher.submit(job_id)

        yield

        processor.request_stop()
        dispatcher.shutdown()
        database.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description="Submit a list of recipients, track generation progress, "
        "and download the generated certificates (PDF / ZIP).",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
