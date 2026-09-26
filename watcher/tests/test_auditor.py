"""
The Auditor: both checks, the sampling, the scoring, the firewall.

Ollama is never called - fake generators stand in for it.
"""

import inspect
import json
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from watcher.auditor import checks, grounding, sampling, scoring
from watcher.auditor.prompts import (
    AUDIT_PROMPT_VERSION,
    build_category_prompt,
    build_materiality_prompt,
)
from watcher.auditor.service import AuditorService
from watcher.interpreter.service import InterpreterService
from watcher.models import (
    AuditorRun,
    AuditSample,
    AuditWindow,
    FilingSummaryCache,
    GroundTruthSplit,
)

from .test_interpreter import FakeGenerator, answer, register


FILING_BODY = (
    "On March 1, 2026 the Company entered into an amended credit "
    "agreement with First National Bank, N.A., increasing the facility "
    "to $350,000,000 with maturity in 2031 and a 4.25% margin."
)


def audit_answer(category="FINANCING", confidence=0.9):
    """A stage-2 (category) answer."""
    return json.dumps({"category": category, "confidence": confidence})


def materiality_answer(is_material=True, confidence=0.9):
    return json.dumps({
        "is_material": is_material, "confidence": confidence, "reason": "x",
    })


def claim_answer(claims):
    return json.dumps({"claims": claims})


class RoutingGenerator:
    """
    Answers all three prompts the Auditor sends: the stage-1
    materiality prompt, the stage-2 category prompt, and the claim
    extraction prompt.
    """

    model_name = "fake-auditor:1"

    def __init__(self, *, verdict=None, claims=None, is_material=True):
        self.verdict = verdict or audit_answer()
        self.claims = claims if claims is not None else []
        self.is_material = is_material
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)

        if "Extract every specific factual claim" in prompt:
            return claim_answer(self.claims)

        if "making ONE" in prompt:                      # stage 1
            if self.verdict == "nope":
                return "nope"
            return json.dumps({
                "is_material": self.is_material,
                "confidence": 0.9,
                "reason": "test",
            })

        return self.verdict                              # stage 2

    def model_digest(self):
        return "sha256:def"


# ----------------------------------------------------------------------
# Grounding: normalisation is the whole job
# ----------------------------------------------------------------------

class GroundingTests(TestCase):

    def check(self, claim_type, text):
        return grounding.check_claim(
            {"type": claim_type, "text": text}, FILING_BODY
        )

    def test_amount_written_three_ways_all_match(self):
        for text in ("$350 million", "$350,000,000", "350000000"):
            self.assertTrue(self.check("amount", text), text)

    def test_wrong_amount_is_ungrounded(self):
        self.assertFalse(self.check("amount", "$420 million"))

    def test_date_formats_match(self):
        for text in ("March 1, 2026", "2026-03-01", "1 March 2026", "2031"):
            self.assertTrue(self.check("date", text), text)

    def test_wrong_year_is_ungrounded(self):
        self.assertFalse(self.check("date", "2029"))

    def test_entity_matches_through_corporate_suffix(self):
        self.assertTrue(self.check("counterparty", "First National Bank"))

    def test_invented_entity_is_ungrounded(self):
        self.assertFalse(self.check("counterparty", "Second City Bank"))

    def test_percentage(self):
        self.assertTrue(self.check("percentage", "4.25%"))
        self.assertFalse(self.check("percentage", "6.5%"))

    def test_check_claims_returns_the_ungrounded_ones(self):
        result = grounding.check_claims(
            [
                {"type": "amount", "text": "$350 million"},
                {"type": "counterparty", "text": "Imaginary Bank"},
            ],
            FILING_BODY,
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["grounded"], 1)
        self.assertEqual(result["ungrounded"][0]["text"], "Imaginary Bank")

    def test_empty_claim_list_is_not_a_failure(self):
        result = grounding.check_claims([], FILING_BODY)
        self.assertEqual(
            result,
            {"total": 0, "grounded": 0, "ungrounded": [], "invented": []},
        )


# ----------------------------------------------------------------------
# Parsing the auditor's answer
# ----------------------------------------------------------------------

class ParseTests(TestCase):

    def test_stage1_clean(self):
        data, code = checks.parse_materiality(materiality_answer())
        self.assertEqual(code, "")
        self.assertTrue(data["is_material"])

    def test_stage1_routine(self):
        data, code = checks.parse_materiality(materiality_answer(False))
        self.assertEqual(code, "")
        self.assertFalse(data["is_material"])

    def test_stage1_garbage(self):
        data, code = checks.parse_materiality("sorry, I cannot help")
        self.assertEqual(code, checks.UNPARSEABLE_RESPONSE)

    def test_stage2_clean(self):
        data, code = checks.parse_category(audit_answer())
        self.assertEqual(code, "")
        self.assertEqual(data["category"], "FINANCING")

    def test_stage2_fenced_json(self):
        data, code = checks.parse_category("```json\n" + audit_answer() + "\n```")
        self.assertEqual(code, "")

    def test_stage2_unknown_category_is_flagged_not_accepted(self):
        data, code = checks.parse_category(audit_answer("NONSENSE"))
        self.assertIsNone(data)
        self.assertEqual(code, checks.UNKNOWN_CATEGORY)

    def test_stage2_out_of_range_confidence(self):
        data, code = checks.parse_category(audit_answer(confidence=7))
        self.assertEqual(code, checks.INVALID_CONFIDENCE)


class TwoStageTests(TestCase):
    """
    The category list must not reach the materiality decision. This is
    the fix for the first real run, where a routine Item 7.01 press
    release was called LEGAL_REGULATORY because the categories were
    shown before the materiality question.
    """

    def test_stage1_prompt_contains_no_category_names(self):
        from watcher.auditor.prompts import build_materiality_prompt
        prompt = build_materiality_prompt("body")
        for code in ("FINANCING", "COMMERCIAL", "MA_STRATEGIC",
                     "LEGAL_REGULATORY", "OTHER_MATERIAL"):
            self.assertNotIn(code, prompt)

    def test_stage2_prompt_contains_them(self):
        from watcher.auditor.prompts import build_category_prompt
        self.assertIn("FINANCING", build_category_prompt("body"))


# ----------------------------------------------------------------------
# The service
# ----------------------------------------------------------------------

@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class AuditorServiceTests(TestCase):

    def setUp(self):
        self.filing = register(1)
        self.classification = InterpreterService(
            generator=FakeGenerator(), threshold=0.7
        ).classify(self.filing)
        self.run = AuditorRun.objects.create(
            taxonomy_version="1.0.0",
            auditor_prompt_version=AUDIT_PROMPT_VERSION,
            auditor_model_name="fake-auditor:1",
        )

    def test_agreement_recorded_when_categories_match(self):
        sample = AuditorService(
            generator=RoutingGenerator(verdict=audit_answer("FINANCING")),
            check_summaries=False,
        ).audit(self.classification, run=self.run)

        self.assertTrue(sample.category_agreed)
        self.assertTrue(sample.material_agreed)
        self.assertEqual(sample.auditor_category, "FINANCING")

    def test_disagreement_recorded_without_touching_the_classification(self):
        sample = AuditorService(
            generator=RoutingGenerator(verdict=audit_answer("COMMERCIAL")),
            check_summaries=False,
        ).audit(self.classification, run=self.run)

        self.assertFalse(sample.category_agreed)
        self.classification.refresh_from_db()
        self.assertEqual(self.classification.category, "FINANCING")

    def test_routine_filing_costs_one_call_not_two(self):
        generator = RoutingGenerator(is_material=False)
        sample = AuditorService(
            generator=generator, check_summaries=False
        ).audit(self.classification, run=self.run)

        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(sample.auditor_category, "")
        self.assertFalse(sample.auditor_is_material)

    def test_material_filing_makes_both_calls(self):
        generator = RoutingGenerator()
        AuditorService(generator=generator, check_summaries=False).audit(
            self.classification, run=self.run
        )
        self.assertEqual(len(generator.calls), 2)

    def test_auditor_never_receives_the_classification(self):
        """
        The prompt must not carry the Interpreter's answer, or the
        agreement rate measures nothing.
        """
        generator = RoutingGenerator()
        AuditorService(generator=generator, check_summaries=False).audit(
            self.classification, run=self.run
        )
        for prompt in generator.calls:
            self.assertNotIn("MODEL-REASONING-MARKER", prompt)
            self.assertNotIn(str(self.classification.id), prompt)

    def test_audit_classification_signature_takes_a_filing(self):
        params = list(
            inspect.signature(checks.audit_classification).parameters
        )
        self.assertEqual(params[0], "filing")

    def test_unparseable_answer_is_saved_with_a_failure_code(self):
        sample = AuditorService(
            generator=RoutingGenerator(verdict="nope"),
            check_summaries=False,
        ).audit(self.classification, run=self.run)

        self.assertIsNone(sample.category_agreed)
        self.assertEqual(sample.failure_code, checks.UNPARSEABLE_RESPONSE)

    def test_summary_grounding_is_recorded(self):
        FilingSummaryCache.objects.create(
            filing=self.filing, content_signature="sig",
            model_name="m", prompt_version="1",
            # The claims below must appear here: a claim absent from
            # the summary is now treated as the extractor inventing it.
            summary=(
                "Amended credit agreement of $350 million with "
                "Imaginary Bank."
            ),
        )
        FilingChunkPatch = self.filing.chunks.first()
        FilingChunkPatch.text = FILING_BODY
        FilingChunkPatch.save()

        sample = AuditorService(
            generator=RoutingGenerator(claims=[
                {"type": "amount", "text": "$350 million"},
                {"type": "counterparty", "text": "Imaginary Bank"},
            ]),
        ).audit(self.classification, run=self.run)

        self.assertEqual(sample.summary_claims_total, 2)
        self.assertEqual(sample.summary_claims_grounded, 1)
        self.assertEqual(
            sample.ungrounded_claims[0]["text"], "Imaginary Bank"
        )

    def test_missing_summary_is_not_a_failure(self):
        sample = AuditorService(generator=RoutingGenerator()).audit(
            self.classification, run=self.run
        )
        self.assertEqual(sample.summary_claims_total, 0)


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------

@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class ScoringTests(TestCase):

    def _sample(self, run, n, category, agreed):
        filing = register(n)
        classification = InterpreterService(
            generator=FakeGenerator(answer(category=category)), threshold=0.7
        ).classify(filing)
        return AuditSample.objects.create(
            run=run,
            classification=classification,
            auditor_category=category if agreed else "COMMERCIAL",
            auditor_is_material=True,
            category_agreed=agreed,
            material_agreed=True,
        )

    def setUp(self):
        self.run = AuditorRun.objects.create(
            taxonomy_version="1.0.0", auditor_prompt_version="1.0.0",
            auditor_model_name="m",
        )
        self.samples = [
            self._sample(self.run, i, "FINANCING", i < 8)
            for i in range(10)
        ]

    def test_per_category_rate(self):
        per_category, overall = scoring.summarise(self.samples)
        financing = [r for r in per_category if r["category"] == "FINANCING"][0]
        self.assertAlmostEqual(financing["agreement_rate"], 0.8)
        self.assertAlmostEqual(overall["agreement_rate"], 0.8)

    def test_confusion_names_the_failing_boundary(self):
        matrix = scoring.confusion(self.samples)
        self.assertEqual(matrix[("FINANCING", "COMMERCIAL")], 2)

    def test_small_samples_never_alarm(self):
        windows = scoring.write_windows(
            self.run, self.samples[:3],
            period_start="2026-09-01", period_end="2026-09-30",
        )
        self.assertFalse(any(w.below_bar for w in windows))

    def test_breach_alarms_when_the_sample_is_big_enough(self):
        bad = [
            self._sample(self.run, 100 + i, "FINANCING", False)
            for i in range(25)
        ]
        windows = scoring.write_windows(
            self.run, bad,
            period_start="2026-09-01", period_end="2026-09-30",
        )
        self.assertTrue(any(w.below_bar for w in windows))
        messages = scoring.alarm_messages(windows)
        self.assertTrue(messages)
        self.assertIn("n=25", messages[0])

    def test_monotonic_calibration_check(self):
        self.assertTrue(scoring.is_monotonic([
            {"band": "low", "agreement_rate": 0.5, "sample_size": 10},
            {"band": "mid", "agreement_rate": 0.7, "sample_size": 10},
            {"band": "high", "agreement_rate": 0.9, "sample_size": 10},
        ]))
        self.assertFalse(scoring.is_monotonic([
            {"band": "low", "agreement_rate": 0.9, "sample_size": 10},
            {"band": "mid", "agreement_rate": 0.7, "sample_size": 10},
            {"band": "high", "agreement_rate": 0.5, "sample_size": 10},
        ]))


# ----------------------------------------------------------------------
# The firewall
# ----------------------------------------------------------------------

class FirewallTests(TestCase):
    """
    The Auditor grades against an independent read and the filing text.
    Never against trading outcomes. A system tuned toward whatever
    produced stronger drift stops being a classifier.
    """

    FORBIDDEN = ("price", "pnl", "excess_return", "analyst")

    def test_scoring_module_reads_no_market_data(self):
        source = inspect.getsource(scoring).lower()
        body = source.split('"""', 2)[-1]        # skip the docstring
        for term in self.FORBIDDEN:
            self.assertNotIn(term, body, term)

    def test_sampling_module_reads_no_market_data(self):
        source = inspect.getsource(sampling).lower()
        body = source.split('"""', 2)[-1]
        for term in self.FORBIDDEN:
            self.assertNotIn(term, body, term)


# ----------------------------------------------------------------------
# Sampling
# ----------------------------------------------------------------------

@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class SamplingTests(TestCase):

    def _classify(self, n, category, confidence):
        filing = register(n)
        return InterpreterService(
            generator=FakeGenerator(
                answer(category=category, confidence=confidence)
            ),
            threshold=0.5,
        ).classify(filing)

    def test_least_confident_first(self):
        self._classify(1, "FINANCING", 0.95)
        low = self._classify(2, "FINANCING", 0.55)
        picked = sampling.fresh_sample(size=5)
        self.assertEqual(picked[0].id, low.id)

    def test_already_audited_rows_are_not_redrawn(self):
        row = self._classify(1, "FINANCING", 0.9)
        run = AuditorRun.objects.create(
            taxonomy_version="1", auditor_prompt_version="1",
            auditor_model_name="m",
        )
        AuditSample.objects.create(run=run, classification=row)
        self.assertEqual(sampling.fresh_sample(size=5), [])

    def test_sealed_stream_reads_the_sealed_split(self):
        row = self._classify(1, "FINANCING", 0.9)
        GroundTruthSplit.objects.create(
            filing=row.filing, split=GroundTruthSplit.Split.SEALED
        )
        self.assertEqual(
            [r.id for r in sampling.sealed_sample()], [row.id]
        )

    def test_sealed_stream_empty_is_fine(self):
        self.assertEqual(sampling.sealed_sample(), [])


# ----------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------

@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class AuditCommandTests(TestCase):

    def _classify(self, n, category="FINANCING"):
        filing = register(n)
        return InterpreterService(
            generator=FakeGenerator(answer(category=category)), threshold=0.7
        ).classify(filing)

    def test_dry_run_writes_nothing(self):
        self._classify(1)
        out = StringIO()
        call_command("audit", "--dry-run", "--sample", "5", stdout=out)
        self.assertEqual(AuditorRun.objects.count(), 0)
        self.assertEqual(AuditSample.objects.count(), 0)
        self.assertIn("Dry run", out.getvalue())

    def test_rejects_a_zero_sample(self):
        with self.assertRaises(CommandError):
            call_command("audit", "--sample", "0")

    def test_nothing_to_audit_is_not_an_error(self):
        out = StringIO()
        call_command("audit", "--sample", "5", stdout=out)
        self.assertIn("Nothing to audit", out.getvalue())

    def test_run_records_windows_and_completes(self):
        for i in range(3):
            self._classify(i)

        from unittest import mock
        with mock.patch(
            "watcher.auditor.service.OllamaGenerationService",
            return_value=RoutingGenerator(),
        ):
            call_command("audit", "--sample", "10", stdout=StringIO())

        run = AuditorRun.objects.get()
        self.assertEqual(run.status, AuditorRun.Status.COMPLETED)
        self.assertEqual(AuditSample.objects.count(), 3)
        self.assertTrue(AuditWindow.objects.filter(category="").exists())

    def test_report_prints_windows(self):
        self.test_run_records_windows_and_completes()
        out = StringIO()
        call_command("audit", "--report", "--days", "30", stdout=out)
        self.assertIn("OVERALL", out.getvalue())


class ExtractorGuardTests(TestCase):
    """
    The claim extractor can invent a claim - notably by copying an
    example out of its own prompt. Such a claim must never be scored
    against the summary, or the grounding rate measures the extractor
    instead of the summary.
    """

    SUMMARY = "The company borrowed forty million dollars from Acme Bank."

    def test_claim_absent_from_the_summary_is_marked_invented(self):
        result = grounding.check_claims(
            [{"type": "amount", "text": "$350 million"}],
            FILING_BODY,
            summary=self.SUMMARY,
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["invented"][0]["text"], "$350 million")

    def test_real_claim_still_scores(self):
        result = grounding.check_claims(
            [{"type": "counterparty", "text": "Acme Bank"}],
            "Agreement with Acme Bank dated 2026.",
            summary=self.SUMMARY,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["grounded"], 1)
        self.assertEqual(result["invented"], [])

    def test_guard_is_optional(self):
        result = grounding.check_claims(
            [{"type": "amount", "text": "$350 million"}], FILING_BODY
        )
        self.assertEqual(result["total"], 1)

    def test_prompts_carry_no_copyable_example_values(self):
        from watcher.auditor.prompts import build_claim_prompt
        for text in (
            build_claim_prompt("x"),
            build_materiality_prompt("x"),
            build_category_prompt("x"),
        ):
            for leak in ("350", "First National", "2031"):
                self.assertNotIn(leak, text)


class AuditAPITests(TestCase):
    """The reporting endpoints. Read-only, authenticated."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        self.user = get_user_model().objects.create_user(
            username="viewer", password="pw12345!"
        )
        self.client = APIClient()

    def _seed(self):
        filing = register(900)
        classification = InterpreterService(
            generator=FakeGenerator(), threshold=0.7
        ).classify(filing)
        run = AuditorRun.objects.create(
            taxonomy_version="1.0.0", auditor_prompt_version="2.0.0",
            auditor_model_name="fake", status=AuditorRun.Status.COMPLETED,
        )
        sample = AuditSample.objects.create(
            run=run, classification=classification,
            auditor_category="COMMERCIAL", auditor_is_material=True,
            category_agreed=False, material_agreed=True,
            summary_claims_total=4, summary_claims_grounded=3,
        )
        scoring.write_windows(
            run, [sample],
            period_start="2026-09-01", period_end="2026-09-30",
        )
        return sample

    def test_summary_requires_authentication(self):
        response = self.client.get("/api/audit/summary/")
        self.assertIn(response.status_code, (401, 403))

    def test_summary_shape(self):
        self._seed()
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/audit/summary/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("OVERALL", body["trend"])
        self.assertIsNotNone(body["latest_run"])
        self.assertEqual(body["grounding"]["total_claims"], 4)
        self.assertAlmostEqual(body["grounding"]["rate"], 0.75)
        self.assertEqual(body["bars"]["min_sample_for_alarm"], 20)

    def test_summary_empty_store_does_not_error(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/audit/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["latest_run"])

    def test_disagreements_lists_the_filings_to_read(self):
        self._seed()
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/audit/disagreements/")

        self.assertEqual(response.status_code, 200)
        row = response.json()["results"][0]
        self.assertEqual(row["interpreter_category"], "FINANCING")
        self.assertEqual(row["auditor_category"], "COMMERCIAL")

    def test_disagreements_requires_authentication(self):
        response = self.client.get("/api/audit/disagreements/")
        self.assertIn(response.status_code, (401, 403))
