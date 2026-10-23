from urllib.parse import quote, quote_plus

from sqlalchemy.exc import SQLAlchemyError


def redact_diagnostic_text(text: str, secrets: tuple[str, ...] | list[str]) -> str:
    """Remove configured secret spellings before diagnostics reach test output."""
    variants: set[str] = set()
    for secret in secrets:
        if not secret:
            continue
        encoded = quote(secret, safe="")
        variants.update((secret, encoded, quote_plus(secret, safe=""), quote(encoded, safe="")))
    for secret in sorted(variants, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def safe_database_error_summary(error: SQLAlchemyError) -> str:
    """Describe a database failure without rendering driver args or connection data."""
    driver_error = getattr(error, "orig", error)
    error_name = type(driver_error).__name__
    sqlstate = getattr(driver_error, "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate.isalnum() and len(sqlstate) <= 10:
        return f"Database operation failed ({error_name}, SQLSTATE {sqlstate})."
    return f"Database operation failed ({error_name})."
