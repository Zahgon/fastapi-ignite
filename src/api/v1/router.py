"""
API v1 router
"""
from flask import Blueprint, Response

from src.api.responses import json_response
from src.api.v1.endpoints import items
from src.core.config import settings


router = Blueprint("v1", __name__)

router.register_blueprint(items.router)


@router.get("/health")
def health_check() -> Response:
    """
    Health check endpoint
    """
    return json_response({"status": "ok", "version": "1"})


@router.get("/app-info")
def app_info() -> Response:
    """
    Application information endpoint
    """
    return json_response(
        {
            "name": settings.PROJECT_NAME,
            "description": settings.PROJECT_DESCRIPTION,
            "version": settings.VERSION,
        }
    )
