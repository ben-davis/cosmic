class NotFound(Exception):
    """Raised when a repository lookup finds no (visible) row → maps to HTTP 404."""
