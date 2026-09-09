"""
Shared dependencies for API routes
"""
from contextlib import contextmanager
from http import HTTPStatus
from typing import Iterator, Optional

from flask import request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.aio import run_async
from src.core.config import settings
from src.core.exceptions import HTTPException
from src.db.session import get_db


def get_api_key(x_api_key: Optional[str] = None) -> str:
    """
    Validate API key from header if required
    Not used in this example but provided as a template for future auth
    """
    if x_api_key is None:
        x_api_key = request.headers.get("X-API-Key")

    if settings.DEBUG:
        # Skip validation in debug mode
        return "debug"
    
    # Example API key validation logic
    if not x_api_key:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="API key header is missing",
        )
        
    # Here you would validate the API key against your database or config
    return x_api_key


# Re-export the database session dependency for convenience
get_db_session = get_db


@contextmanager
def db_session() -> Iterator[AsyncSession]:
    """
    Drive :func:`get_db` from synchronous request-handling code
    """
    generator = get_db_session()
    session = run_async(generator.__anext__())

    try:
        yield session
    except BaseException as exc:
        run_async(generator.athrow(type(exc), exc, exc.__traceback__))
        raise
    else:
        try:
            run_async(generator.__anext__())
        except StopAsyncIteration:
            pass
