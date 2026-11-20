"""Object storage for uploaded documents (feature 024, research D6). `get_storage()` is the single selector."""
from api.storage.registry import get_storage

__all__ = ["get_storage"]
