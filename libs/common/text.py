"""Text utilities (e.g. PostgreSQL-safe sanitization)."""

# PostgreSQL text/JSONB does not allow NUL (\u0000).
NUL = "\u0000"


def sanitize_for_postgres(obj: object) -> object:
    """Recursively remove NUL characters from strings in dicts/lists for PostgreSQL."""
    if isinstance(obj, dict):
        return {k: sanitize_for_postgres(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_postgres(v) for v in obj]
    if isinstance(obj, str) and NUL in obj:
        return obj.replace(NUL, "")
    return obj
