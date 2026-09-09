"""
Custom exceptions and exception handlers for the application
"""
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Union

from flask import Flask, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from src.api.responses import json_response
from src.api.validation import RequestValidationError


class ErrorResponse(BaseModel):
    """
    Standard error response model
    """
    status_code: int
    message: str
    details: Optional[Union[List[Dict[str, Any]], Dict[str, Any], str]] = None


class HTTPException(Exception):
    """
    Exception raised by a view to return a specific HTTP status code
    """
    def __init__(
        self,
        status_code: int,
        detail: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.status_code = status_code
        self.detail = detail if detail is not None else HTTPStatus(status_code).phrase
        self.headers = headers
        super().__init__(self.detail)


class DatabaseError(Exception):
    """Exception raised for database-related errors"""
    def __init__(self, message: str = "Database error occurred"):
        self.message = message
        super().__init__(self.message)


class CacheError(Exception):
    """Exception raised for cache-related errors"""
    def __init__(self, message: str = "Cache error occurred"):
        self.message = message
        super().__init__(self.message)


class TaskQueueError(Exception):
    """Exception raised for task queue related errors"""
    def __init__(self, message: str = "Task queue error occurred"):
        self.message = message
        super().__init__(self.message)


class ResourceNotFoundError(Exception):
    """Exception raised when a resource is not found"""
    def __init__(self, resource_type: str, resource_id: Any):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.message = f"{resource_type} with ID {resource_id} not found"
        super().__init__(self.message)


class BusinessLogicError(Exception):
    """Exception raised for business logic errors"""
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


def register_exception_handlers(app: Flask) -> None:
    """
    Register exception handlers with the Flask application
    """
    # Handle validation errors (from Pydantic)
    app.register_error_handler(RequestValidationError, validation_error_handler)

    # Handle HTTP exceptions
    app.register_error_handler(HTTPException, http_exception_handler)

    # Handle SQLAlchemy errors
    app.register_error_handler(SQLAlchemyError, sqlalchemy_error_handler)

    # Handle custom exceptions
    app.register_error_handler(DatabaseError, database_error_handler)
    app.register_error_handler(CacheError, cache_error_handler)
    app.register_error_handler(TaskQueueError, task_queue_error_handler)
    app.register_error_handler(ResourceNotFoundError, resource_not_found_error_handler)
    app.register_error_handler(BusinessLogicError, business_logic_error_handler)

    # Catch-all for any unhandled exceptions
    app.register_error_handler(Exception, unhandled_exception_handler)


def validation_error_handler(exc: RequestValidationError) -> Response:
    """
    Handler for request validation errors
    """
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            message="Validation error",
            details=exc.errors(),
        ).model_dump(),
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
    )


def http_exception_handler(exc: HTTPException) -> Response:
    """
    Handler for HTTP exceptions
    """
    return json_response(
        ErrorResponse(
            status_code=exc.status_code,
            message=str(exc.detail),
        ).model_dump(),
        status=exc.status_code,
    )


def sqlalchemy_error_handler(exc: SQLAlchemyError) -> Response:
    """
    Handler for SQLAlchemy errors
    """
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            message="Database error",
            details=str(exc),
        ).model_dump(),
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
    )


def database_error_handler(exc: DatabaseError) -> Response:
    """
    Handler for database errors
    """
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            message=exc.message,
        ).model_dump(),
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
    )


def cache_error_handler(exc: CacheError) -> Response:
    """
    Handler for cache errors
    """
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            message=exc.message,
        ).model_dump(),
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
    )


def task_queue_error_handler(exc: TaskQueueError) -> Response:
    """
    Handler for task queue errors
    """
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            message=exc.message,
        ).model_dump(),
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
    )


def resource_not_found_error_handler(exc: ResourceNotFoundError) -> Response:
    """
    Handler for resource not found errors
    """
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.NOT_FOUND,
            message=exc.message,
        ).model_dump(),
        status=HTTPStatus.NOT_FOUND,
    )


def business_logic_error_handler(exc: BusinessLogicError) -> Response:
    """
    Handler for business logic errors
    """
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.BAD_REQUEST,
            message=exc.message,
        ).model_dump(),
        status=HTTPStatus.BAD_REQUEST,
    )


def unhandled_exception_handler(exc: Exception) -> Response:
    """
    Handler for all unhandled exceptions
    """
    # Log the exception here before returning response
    return json_response(
        ErrorResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            message="Internal server error",
        ).model_dump(),
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
    )
