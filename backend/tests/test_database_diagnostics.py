from sqlalchemy.exc import OperationalError

from app.db.diagnostics import redact_diagnostic_text, safe_database_error_summary


class SentinelDriverError(Exception):
    sqlstate = "08001"


def test_database_error_summary_keeps_safe_type_and_sqlstate_only() -> None:
    sentinel = "SENTINEL-DO-NOT-PRINT 4821"
    driver_error = SentinelDriverError(f"connection args contain {sentinel}")
    error = OperationalError(
        "SELECT 1",
        {"password": sentinel},
        driver_error,
    )

    summary = safe_database_error_summary(error)

    assert summary == "Database operation failed (SentinelDriverError, SQLSTATE 08001)."
    assert sentinel not in summary


def test_diagnostic_redaction_covers_raw_and_url_encoded_sentinel() -> None:
    sentinel = "SENTINEL-DO-NOT-PRINT 4821"
    output = redact_diagnostic_text(
        f"password={sentinel}; url_password=SENTINEL-DO-NOT-PRINT%204821",
        [sentinel],
    )

    assert sentinel not in output
    assert "%204821" not in output
    assert output.count("[REDACTED]") == 2
