"""
Guide 5.1-5.4: the Interpreter.

One filing in, one FilingClassification row out. It never knows whether
it was right; it only produces labels. Grading happens elsewhere
(Phase 5, against human labels).

Reuses:
    filing_text.build_filing_text  - same text builder as the labelling screen
    OllamaGenerationService        - the existing HTTP client
    taxonomy / prompts / parser    - versioned alongside this module
"""

import os
import time

from django.conf import settings
from django.db.models import Exists, OuterRef

from watcher.knowledge_base.generation.ollama_generation_service import (
    GenerationServiceError,
    OllamaGenerationService,
)
from watcher.knowledge_base.models import FailureEvent
from watcher.models import Filing, FilingChunk, FilingClassification
from watcher.services.failure_tracking_service import FailureTrackingService

from .filing_text import build_filing_text
from .parser import parse_response
from .prompts import PROMPT_VERSION, build_prompt
from .taxonomy import TAXONOMY_VERSION


INTERPRETABLE_FORMS = ("8-K", "8-K/A")

# ~24,000 characters fits an 8k-token context with the prompt and answer.
MAX_INPUT_CHARS = 24000
INPUT_EXHIBIT_PREFIXES = ("EX-99",)

# Guide 5.3: the same filing must produce the same answer twice.
OLLAMA_OPTIONS = {
    "temperature": 0.0,
    "top_p": 1.0,
    "seed": 42,
    "num_ctx": 8192,
}
MAX_ANSWER_TOKENS = 512

DEFAULT_REVIEW_THRESHOLD = 0.70


class ModelUnavailable(Exception):
    """Ollama could not answer. No row is saved; the filing is retried."""


def review_threshold():
    value = getattr(settings, "INTERPRETER_REVIEW_THRESHOLD", None)
    if value is None:
        value = os.getenv("INTERPRETER_REVIEW_THRESHOLD")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_THRESHOLD
    return min(max(value, 0.0), 1.0)


def interpretable_filings():
    """8-K / 8-K/A filings that have text (chunks) to classify."""
    has_chunks = FilingChunk.objects.filter(filing_id=OuterRef("pk"))
    return (
        Filing.objects
        .filter(form__in=INTERPRETABLE_FORMS)
        .filter(Exists(has_chunks))
        .select_related("company")
    )


def pending_filings():
    """Interpretable filings with no classification for this taxonomy
    and prompt version. Newest first, so new filings are handled first."""
    done = FilingClassification.objects.filter(
        filing_id=OuterRef("pk"),
        taxonomy_version=TAXONOMY_VERSION,
        prompt_version=PROMPT_VERSION,
    )
    return (
        interpretable_filings()
        .exclude(Exists(done))
        .order_by("-created_at", "-id")
    )


class InterpreterService:

    def __init__(self, *, generator=None, threshold=None):
        self.generator = generator or OllamaGenerationService(
            model_name=os.getenv("INTERPRETER_MODEL") or None,
        )
        self.threshold = (
            review_threshold() if threshold is None else threshold
        )
        self._model_label = None

    @property
    def model_label(self):
        """tag@digest when Ollama reports a digest, else the tag."""
        if self._model_label is None:
            name = self.generator.model_name
            digest = ""
            digest_fn = getattr(self.generator, "model_digest", None)
            if callable(digest_fn):
                try:
                    digest = digest_fn() or ""
                except Exception:
                    digest = ""
            self._model_label = f"{name}@{digest}" if digest else name
        return self._model_label

    def build_input(self, filing):
        return build_filing_text(
            filing,
            max_chars=MAX_INPUT_CHARS,
            exhibit_prefixes=INPUT_EXHIBIT_PREFIXES,
        )

    def classify(self, filing, *, run=None, save=True):
        """
        Returns the FilingClassification (saved unless save=False), or
        None when the filing has no text yet. Raises ModelUnavailable
        when Ollama fails, so the caller can retry later instead of
        filling the review queue with outage rows.
        """
        text = self.build_input(filing)
        if text.is_empty:
            return None

        prompt = build_prompt(filing, text.text, truncated=text.truncated)

        started = time.monotonic()
        try:
            raw = self.generator.generate(
                prompt,
                temperature=OLLAMA_OPTIONS["temperature"],
                max_tokens=MAX_ANSWER_TOKENS,
                seed=OLLAMA_OPTIONS["seed"],
                top_p=OLLAMA_OPTIONS["top_p"],
                num_ctx=OLLAMA_OPTIONS["num_ctx"],
                json_mode=True,
            )
        except GenerationServiceError as exc:
            raise ModelUnavailable(str(exc)) from exc
        duration_ms = int((time.monotonic() - started) * 1000)

        data, failure_code = parse_response(raw)
        data = data or {}

        confidence = data.get("confidence")
        needs_review = bool(failure_code) or (
            confidence is None or confidence < self.threshold
        )

        row = FilingClassification(
            filing=filing,
            run=run,
            taxonomy_version=TAXONOMY_VERSION,
            prompt_version=PROMPT_VERSION,
            model_name=self.model_label,
            model_options={
                **OLLAMA_OPTIONS,
                "max_tokens": MAX_ANSWER_TOKENS,
                "json_mode": True,
                "review_threshold": self.threshold,
            },
            is_material=data.get("is_material"),
            category=data.get("category", ""),
            confidence=confidence,
            reasoning=data.get("reasoning", ""),
            body_item_numbers=data.get("body_item_numbers", ""),
            extracted_facts=data.get("extracted_facts", {}),
            needs_human_review=needs_review,
            failure_code=failure_code or "",
            raw_response=raw,
            input_sha256=text.sha256,
            input_truncated=text.truncated,
            duration_ms=duration_ms,
        )

        if save:
            row.save()
            # The model answered, so any earlier Interpreter failure for
            # this filing (e.g. Ollama was down during the Watcher run) is
            # resolved and the filing returns to the Filings page - the
            # same pattern the summary stage uses.
            FailureTrackingService.resolve(
                filing=filing,
                stage=FailureEvent.Stage.INTERPRETER,
            )
        return row
