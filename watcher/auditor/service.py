"""
AuditorService: runs both checks over a sample and writes AuditSample
rows. Knows nothing about scheduling, locking or reporting.
"""

import logging
import os

from watcher.knowledge_base.generation.ollama_generation_service import (
    GenerationServiceError,
    OllamaGenerationService,
)
from watcher.models import AuditSample

from . import checks
from .prompts import AUDIT_PROMPT_VERSION


logger = logging.getLogger(__name__)


class AuditorService:

    def __init__(self, *, generator=None, check_summaries=True):
        # A DIFFERENT model from the Interpreter's by default. The same
        # model with the same prompt at temperature 0 returns the same
        # answer, which would make the agreement rate a tautology.
        self.generator = generator or OllamaGenerationService(
            model_name=os.getenv("AUDITOR_MODEL") or None,
        )
        self.check_summaries = check_summaries
        self._model_label = None

    @property
    def model_label(self):
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

    @property
    def prompt_version(self):
        return AUDIT_PROMPT_VERSION

    def audit(self, classification, *, run, is_sealed=False, save=True):
        """
        Audit one classification and return the AuditSample.

        Note what is passed to the check: classification.filing, not
        the classification. The Interpreter's answer is used only
        afterwards, to compare.
        """
        filing = classification.filing

        data, code, sha = checks.audit_classification(
            filing, generator=self.generator
        )

        sample = AuditSample(
            run=run,
            classification=classification,
            is_sealed=is_sealed,
            input_sha256=sha,
            failure_code=code or "",
        )

        if data is not None:
            sample.auditor_category = data["category"]
            sample.auditor_is_material = data["is_material"]
            sample.auditor_confidence = data["confidence"]
            sample.category_agreed = (
                data["category"] == (classification.category or "")
            )
            if classification.is_material is not None:
                sample.material_agreed = (
                    data["is_material"] == classification.is_material
                )

        if self.check_summaries:
            self._add_grounding(sample, filing)

        if save:
            sample.save()
        return sample

    def _add_grounding(self, sample, filing):
        cache = getattr(filing, "summary_cache", None)
        summary = getattr(cache, "summary", "") if cache else ""
        if not summary:
            return

        try:
            result, code = checks.audit_summary(
                filing, summary, generator=self.generator
            )
        except GenerationServiceError:
            logger.exception("Grounding check failed for filing %s", filing.pk)
            return

        if result is None:
            if code and not sample.failure_code:
                sample.failure_code = code
            return

        sample.summary_claims_total = result["total"]
        sample.summary_claims_grounded = result["grounded"]
        sample.ungrounded_claims = result["ungrounded"]

        # Claims the extractor invented are the Auditor's own fault,
        # not the summary's. Kept visible, kept out of the rate.
        if result.get("invented"):
            sample.ungrounded_claims = (
                result["ungrounded"]
                + [dict(c, invented_by_extractor=True) for c in result["invented"]]
            )
