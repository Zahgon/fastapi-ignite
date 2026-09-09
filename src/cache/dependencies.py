"""
Cache dependency providers
"""
import logging
from typing import AsyncGenerator

from src.cache.backends.base import CacheBackend
from src.cache.backends.factory import get_cache_backend

logger = logging.getLogger(__name__)


async def get_cache() -> AsyncGenerator[CacheBackend, None]:
    """
    Get cache client instance as a request-scoped dependency
    
    This function is a dependency provider that yields a cache backend
    instance for use in view functions.
    
    Usage:
        @router.get("/items/<item_id>")
        def get_item(item_id):
            generator = get_cache()
            cache = run_async(generator.__anext__())
            # Use the cache instance
            value = run_async(cache.get(f"item:{item_id}"))
            ...
    
    Returns:
        AsyncGenerator[CacheBackend, None]: A cache backend instance
    """
    cache_backend = get_cache_backend()
    
    try:
        yield cache_backend
    except Exception as e:
        logger.error(f"Cache error: {str(e)}")
        raise
