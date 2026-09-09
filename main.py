"""
Flask application entry point
"""
import atexit
import logging
import os

from flask import Flask

from src.api.errors import register_error_handlers
from src.api.openapi import build_openapi_schema, render_redoc, render_swagger_ui
from src.api.responses import html_response, json_response, register_reason_phrases
from src.api.v1.router import router as api_v1_router
from src.core.aio import run_async, start_event_loop, stop_event_loop
from src.core.config import settings
from src.core.cors import register_cors
from src.core.events import create_start_app_handler, create_stop_app_handler
from src.core.logging import setup_logging


def startup(app: Flask) -> None:
    """
    Run the application startup handlers
    """
    # Setup logging
    setup_logging()

    # Override scheduler setting from environment variable (set by CLI)
    scheduler_env = os.environ.get("SCHEDULER_ENABLED")
    if scheduler_env is not None:
        from src.core.config import settings
        settings.scheduler.enabled = scheduler_env.lower() == "true"

    # Run startup event handlers
    start_event_loop()
    start_handler = create_start_app_handler()
    run_async(start_handler())


def shutdown(app: Flask) -> None:
    """
    Run the application shutdown handlers
    """
    # Run shutdown event handlers
    stop_handler = create_stop_app_handler()
    run_async(stop_handler())
    stop_event_loop()


def register_documentation(application: Flask) -> None:
    """
    Serve the OpenAPI schema and the interactive documentation pages
    """
    openapi_url = f"{settings.API_PREFIX}/openapi.json"

    @application.get(openapi_url)
    def openapi_schema():
        return json_response(build_openapi_schema())

    @application.get(f"{settings.API_PREFIX}/docs")
    def swagger_ui():
        return html_response(
            render_swagger_ui(openapi_url, f"{settings.PROJECT_NAME} - Swagger UI")
        )

    @application.get(f"{settings.API_PREFIX}/redoc")
    def redoc():
        return html_response(
            render_redoc(openapi_url, f"{settings.PROJECT_NAME} - ReDoc")
        )


def create_application() -> Flask:
    """
    Create and configure the Flask application
    """
    application = Flask(__name__)

    # Set up CORS handling
    register_cors(
        application,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Translate exceptions into responses
    register_error_handlers(application)
    register_reason_phrases(application)

    # Include routers
    application.register_blueprint(api_v1_router, url_prefix=settings.API_PREFIX)
    register_documentation(application)

    # Run startup event handlers
    startup(application)
    atexit.register(shutdown, application)

    return application


app = create_application()


if __name__ == "__main__":
    # The development server serves one request at a time, mirroring the single
    # worker process the production image runs (gunicorn defaults to one
    # synchronous worker).  Flask would otherwise default to threaded=True and
    # give the development entry point concurrency the deployed application
    # does not have.
    app.run(
        host=settings.HOST,
        port=settings.PORT,
        debug=settings.DEBUG,
        threaded=False,
    )
