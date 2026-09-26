"""
Optional Interpreter step inside the Watcher's per-filing pipeline.

    download -> registration -> indexing -> item verification -> summary
        -> INTERPRETER (this) -> email

Only for the filing just detected. Switched on by INTERPRETER_IN_PIPELINE
(settings or .env); OFF by default, and when off the pipeline, console
output and email are exactly what they were before.

Failure handling, matching the other stages:
    - Ollama down / timeout / crash  -> FailureEvent(stage=interpreter),
      so the filing appears in Review Queue -> Failures. The email still
      goes out, and the run's COMPLETED/PARTIAL status is unaffected
      (that status depends only on download and index failures).
    - The model answered but badly   -> saved with needs_human_review,
      Review Queue -> Classification. Not a process failure.
    - A later successful `interpret` resolves the FailureEvent.
"""

import os

from django.conf import settings

from .facts import event_date, format_amounts
from .service import INTERPRETABLE_FORMS, InterpreterService


_TRUE = {"1", "true", "yes", "on"}


def pipeline_enabled():
    value = getattr(settings, "INTERPRETER_IN_PIPELINE", None)
    if value is None:
        value = os.getenv("INTERPRETER_IN_PIPELINE", "")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def email_block(row):
    """Plain dict for the email. None-safe, no model objects."""
    facts = row.extracted_facts or {}
    if row.is_material is False:
        label = "ROUTINE"
    else:
        label = row.category or "UNKNOWN"

    return {
        "material": (
            "Yes" if row.is_material
            else "No" if row.is_material is False
            else "Unknown"
        ),
        "category": label,
        "confidence": (
            f"{row.confidence:.2f}" if row.confidence is not None else "N/A"
        ),
        "counterparty": facts.get("counterparty"),
        # Pre-formatted text, e.g. "EUR 625,000,000 (2031 Notes)".
        # Works for both the 1.0.2 "amounts" list and older amount_usd.
        "amount_usd": format_amounts(facts),
        "effective_date": event_date(facts),
        "status": (
            "Sent to Review Queue (Classification)"
            if row.needs_human_review
            else "Confident"
        ),
    }


FAILED_EMAIL_BLOCK = {
    "status": "Failed - filing sent to Review Queue (Failures)",
}


class PipelineInterpreterStep:
    """Thin wrapper so post_processing stays readable and testable."""

    def __init__(self, *, service=None):
        self._service = service

    @property
    def service(self):
        if self._service is None:
            self._service = InterpreterService()
        return self._service

    @staticmethod
    def applies_to(form):
        return str(form or "").strip().upper() in INTERPRETABLE_FORMS

    def classify(self, filing):
        """Returns the saved row or None (no text). Raises on failure."""
        return self.service.classify(filing)


def default_step():
    return PipelineInterpreterStep() if pipeline_enabled() else None
