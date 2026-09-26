"""
Guide Part 5 (Phase 4): the Interpreter, and the review-queue API.

Ollama is never called: a fake generator stands in for it.
"""

import json
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from watcher.interpreter import parser as P
from watcher.interpreter.prompts import PROMPT_VERSION, build_prompt
from watcher.interpreter.service import (
    InterpreterService,
    ModelUnavailable,
    pending_filings,
)
from watcher.interpreter.taxonomy import TAXONOMY_VERSION
from watcher.knowledge_base.generation.ollama_generation_service import (
    GenerationServiceError,
    OllamaGenerationService,
)
from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.models import (
    AutomationRun,
    ClassificationOverride,
    FilingChunk,
    FilingClassification,
    FilingDocument,
    GroundTruthSplit,
    InterpreterRun,
    LabellingSample,
)
from watcher.knowledge_base.models import FailureEvent


def answer(category="FINANCING", confidence=0.9, is_material=True, **extra):
    body = {
        "is_material": is_material,
        "category": category,
        "confidence": confidence,
        "body_item_numbers": ["1.01", "9.01"],
        "extracted_facts": {"counterparty": "First Bank", "amount_usd": 350000000},
        "reasoning": "MODEL-REASONING-MARKER",
    }
    body.update(extra)
    return json.dumps(body)


class FakeGenerator:
    """Deterministic stand-in for OllamaGenerationService."""

    model_name = "fake-model:1"

    def __init__(self, reply=None, error=None):
        self.reply = reply if reply is not None else answer()
        self.error = error
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error:
            raise GenerationServiceError(self.error)
        return self.reply(prompt) if callable(self.reply) else self.reply

    def model_digest(self):
        return "sha256:abc"


def register(n, form="8-K", with_text=True, ticker="TEST"):
    filing = FilingRegistrationService().register(
        ticker=ticker, cik="1234567890", company_name="Test Co", form=form,
        accession_number=f"0009-26-{n:06d}", sequence=1,
        filing_date="2026-09-01", primary_document=f"d{n}.htm",
        local_path=f"/tmp/{n}", source_url=f"http://test/{n}",
        report_date="2026-09-01",
    )
    if with_text:
        doc = FilingDocument.objects.create(
            filing=filing, sequence="1", document_type=form,
            document_name="d.htm", source_url="http://t", local_path="/t",
            is_primary=True,
        )
        FilingChunk.objects.create(
            filing=filing, document=doc, chunk_index=0,
            text=f"Body of filing {n}. Amended credit agreement.",
            content_sha256=f"h{n}",
        )
    return filing


# ----------------------------------------------------------------------
# Ollama service: existing callers must be unaffected
# ----------------------------------------------------------------------

# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class OllamaServiceTests(TestCase):

    def _service(self):
        session = mock.Mock()
        session.post.return_value.json.return_value = {"response": "ok"}
        return OllamaGenerationService(
            base_url="http://x", model_name="m", session=session
        ), session

    def test_existing_call_sends_identical_payload(self):
        service, session = self._service()
        service.generate("hello", temperature=0.2, max_tokens=100)
        self.assertEqual(
            session.post.call_args.kwargs["json"],
            {
                "model": "m",
                "prompt": "hello",
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": 100,
                    "num_ctx": 16384,
                },
            },
        )
        self.assertEqual(
            list(session.post.call_args.kwargs["json"].keys()),
            ["model", "prompt", "stream", "think", "options"],
        )

    def test_new_options_only_when_asked(self):
        service, session = self._service()
        service.generate("x", seed=42, top_p=1.0, num_ctx=8192, json_mode=True)
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["format"], "json")
        self.assertEqual(payload["options"]["seed"], 42)
        self.assertEqual(payload["options"]["num_ctx"], 8192)

    def test_model_digest(self):
        service, session = self._service()
        session.get.return_value.json.return_value = {
            "models": [{"name": "m:latest", "digest": "sha256:1"}]
        }
        self.assertEqual(service.model_digest(), "sha256:1")
        session.get.side_effect = Exception("down")
        self.assertEqual(service.model_digest(), "")


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------

# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class ParserTests(TestCase):

    def test_clean_answer(self):
        data, code = P.parse_response(answer())
        self.assertIsNone(code)
        self.assertEqual(data["category"], "FINANCING")
        self.assertEqual(data["body_item_numbers"], "1.01;9.01")
        self.assertEqual(data["extracted_facts"]["counterparty"], "First Bank")

    def test_fenced_and_chatty_answers(self):
        self.assertIsNone(P.parse_response("```json\n" + answer() + "\n```")[1])
        self.assertIsNone(P.parse_response("Here you go: " + answer() + " done")[1])

    def test_routine(self):
        data, code = P.parse_response(answer(category=None, is_material=False))
        self.assertIsNone(code)
        self.assertEqual(data["category"], "")

    def test_failures(self):
        cases = {
            "not json at all": P.UNPARSEABLE_RESPONSE,
            "[1, 2]": P.UNPARSEABLE_RESPONSE,
            answer(category="SENTIMENT"): P.UNKNOWN_CATEGORY,
            answer(confidence=1.5): P.INVALID_CONFIDENCE,
            answer(confidence="high"): P.INVALID_CONFIDENCE,
            answer(confidence=True): P.INVALID_CONFIDENCE,
            answer(is_material="yes"): P.INVALID_MATERIALITY,
            answer(category=None): P.MISSING_CATEGORY,
            answer(is_material=False): P.INCONSISTENT_ANSWER,
        }
        for raw, expected in cases.items():
            self.assertEqual(P.parse_response(raw)[1], expected, raw)

    def test_lowercase_category_accepted(self):
        data, code = P.parse_response(answer(category="commercial"))
        self.assertIsNone(code)
        self.assertEqual(data["category"], "COMMERCIAL")


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------

# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class ServiceTests(TestCase):

    def setUp(self):
        self.filing = register(1)

    def test_classification_is_deterministic(self):
        # Guide 5.3: write this test first.
        service = InterpreterService(generator=FakeGenerator(), threshold=0.7)
        a = service.classify(self.filing)
        b = service.classify(self.filing)
        self.assertEqual(a.category, b.category)
        self.assertEqual(a.confidence, b.confidence)
        self.assertEqual(a.input_sha256, b.input_sha256)
        self.assertEqual(FilingClassification.objects.count(), 2)  # append-only

    def test_records_provenance_and_facts(self):
        row = InterpreterService(generator=FakeGenerator(), threshold=0.7).classify(self.filing)
        self.assertEqual(row.taxonomy_version, TAXONOMY_VERSION)
        self.assertEqual(row.prompt_version, PROMPT_VERSION)
        self.assertEqual(row.model_name, "fake-model:1@sha256:abc")
        self.assertEqual(row.model_options["seed"], 42)
        self.assertEqual(row.extracted_facts["amount_usd"], 350000000)
        self.assertFalse(row.needs_human_review)
        self.assertEqual(len(row.input_sha256), 64)

    def test_sends_deterministic_options(self):
        fake = FakeGenerator()
        InterpreterService(generator=fake, threshold=0.7).classify(self.filing)
        kwargs = fake.calls[0][1]
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertEqual(kwargs["seed"], 42)
        self.assertTrue(kwargs["json_mode"])

    def test_low_confidence_goes_to_review(self):
        row = InterpreterService(
            generator=FakeGenerator(answer(confidence=0.42)), threshold=0.7
        ).classify(self.filing)
        self.assertTrue(row.needs_human_review)
        self.assertEqual(row.category, "FINANCING")   # guess kept for the reviewer

    def test_parse_failure_saved_and_flagged(self):
        row = InterpreterService(
            generator=FakeGenerator("garbage"), threshold=0.7
        ).classify(self.filing)
        self.assertTrue(row.needs_human_review)
        self.assertEqual(row.failure_code, P.UNPARSEABLE_RESPONSE)
        self.assertEqual(row.raw_response, "garbage")
        self.assertIsNone(row.confidence)

    def test_model_down_saves_nothing(self):
        service = InterpreterService(generator=FakeGenerator(error="refused"), threshold=0.7)
        with self.assertRaises(ModelUnavailable):
            service.classify(self.filing)
        self.assertEqual(FilingClassification.objects.count(), 0)
        self.assertEqual(FailureEvent.objects.count(), 0)

    def test_no_text_returns_none(self):
        empty = register(2, with_text=False)
        self.assertIsNone(
            InterpreterService(generator=FakeGenerator(), threshold=0.7).classify(empty)
        )

    def test_contract_exhibit_not_sent(self):
        doc = FilingDocument.objects.create(
            filing=self.filing, sequence="3", document_type="EX-10.1",
            document_name="c.htm", source_url="http://t", local_path="/t",
        )
        FilingChunk.objects.create(
            filing=self.filing, document=doc, chunk_index=0,
            text="SECRET CONTRACT TEXT", content_sha256="c",
        )
        fake = FakeGenerator()
        InterpreterService(generator=fake, threshold=0.7).classify(self.filing)
        self.assertNotIn("SECRET CONTRACT TEXT", fake.calls[0][0])

    def test_prompt_contains_taxonomy_and_filing(self):
        prompt = build_prompt(self.filing, "BODY", truncated=True)
        for code in ("FINANCING", "COMMERCIAL", "MA_STRATEGIC",
                     "LEGAL_REGULATORY", "OTHER_MATERIAL"):
            self.assertIn(code, prompt)
        self.assertIn("BODY", prompt)
        self.assertIn("truncated", prompt)

    def test_current_prompt_is_1_0_2(self):
        self.assertEqual(PROMPT_VERSION, "1.0.2")

    def test_prompt_1_0_2_asks_for_currency_amounts_and_dates(self):
        prompt = build_prompt(self.filing, "BODY")
        self.assertIn('"amounts"', prompt)
        self.assertIn("Never convert between", prompt)
        self.assertIn('"event_date"', prompt)
        self.assertNotIn('"amount_usd"', prompt)

    def test_prompt_1_0_1_kept_unchanged(self):
        from watcher.interpreter.prompts import v1_0_1
        self.assertEqual(v1_0_1.PROMPT_VERSION, "1.0.1")
        self.assertIn('"amount_usd"', v1_0_1.system_prompt())

    def test_prompt_demands_real_confidence_for_routine(self):
        prompt = build_prompt(self.filing, "BODY")
        self.assertIn("A routine answer still needs a real confidence", prompt)
        self.assertIn("Never use 0.0 just because the", prompt)
        # The routine example in the prompt itself carries a real score
        # and parses cleanly.
        start = prompt.index("Example of a routine answer:")
        end = prompt.index("=== FILING ===", start)
        example = prompt[start:end].split(":", 1)[1].strip()
        data, code = P.parse_response(example)
        self.assertIsNone(code)
        self.assertFalse(data["is_material"])
        self.assertGreaterEqual(data["confidence"], 0.7)

    def test_old_prompt_kept_unchanged(self):
        from watcher.interpreter.prompts import v1_0_0
        self.assertEqual(v1_0_0.PROMPT_VERSION, "1.0.0")
        self.assertNotIn("real confidence", v1_0_0.system_prompt())

    def test_confident_routine_is_not_sent_to_review(self):
        row = InterpreterService(
            generator=FakeGenerator(
                answer(is_material=False, category=None, confidence=0.9)
            ),
            threshold=0.7,
        ).classify(self.filing)
        self.assertFalse(row.needs_human_review)
        self.assertEqual(row.prompt_version, PROMPT_VERSION)

    def test_new_prompt_version_makes_filings_pending_again(self):
        old = FilingClassification.objects.create(
            filing=self.filing, taxonomy_version=TAXONOMY_VERSION,
            prompt_version="1.0.0", model_name="m", input_sha256="x",
        )
        self.assertIn(self.filing.id, [f.id for f in pending_filings()])
        InterpreterService(generator=FakeGenerator(), threshold=0.7).classify(self.filing)
        self.assertNotIn(self.filing.id, [f.id for f in pending_filings()])
        old.refresh_from_db()
        self.assertEqual(old.prompt_version, "1.0.0")   # old row untouched

    @override_settings(INTERPRETER_REVIEW_THRESHOLD=0.95)
    def test_threshold_from_settings(self):
        row = InterpreterService(generator=FakeGenerator()).classify(self.filing)
        self.assertTrue(row.needs_human_review)   # 0.9 < 0.95


# ----------------------------------------------------------------------
# Command
# ----------------------------------------------------------------------

# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class CommandTests(TestCase):

    def setUp(self):
        self.a = register(1)
        self.b = register(2)
        self.ten_k = register(3, form="10-K")
        self.no_text = register(4, with_text=False)
        self.fake = FakeGenerator()

    def _call(self, *args, fake=None):
        fake = fake or self.fake
        out, err = StringIO(), StringIO()
        with mock.patch(
            "watcher.management.commands.interpret.InterpreterService",
            side_effect=lambda: InterpreterService(generator=fake, threshold=0.7),
        ):
            call_command("interpret", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_only_8k_with_text(self):
        self._call()
        ids = set(FilingClassification.objects.values_list("filing_id", flat=True))
        self.assertEqual(ids, {self.a.id, self.b.id})
        run = InterpreterRun.objects.get()
        self.assertEqual(run.status, InterpreterRun.Status.COMPLETED)
        self.assertEqual(run.classified_count, 2)

    def test_dry_run_calls_nothing(self):
        out, _ = self._call("--dry-run")
        self.assertIn("Dry run", out)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(FilingClassification.objects.count(), 0)
        self.assertEqual(InterpreterRun.objects.count(), 0)

    def test_rerun_skips_done_filings(self):
        self._call()
        self._call()
        self.assertEqual(FilingClassification.objects.count(), 2)

    def test_reclassify_appends(self):
        self._call()
        first = list(FilingClassification.objects.values_list("id", "category"))
        self._call("--reclassify", "--taxonomy-version", TAXONOMY_VERSION)
        self.assertEqual(FilingClassification.objects.count(), 4)
        for pk, category in first:
            self.assertEqual(FilingClassification.objects.get(pk=pk).category, category)

    def test_wrong_taxonomy_version_refused(self):
        with self.assertRaises(CommandError):
            self._call("--reclassify", "--taxonomy-version", "9.9.9")

    def test_sealed_split_not_allowed(self):
        with self.assertRaises(CommandError):
            self._call("--split", "sealed")

    def test_split_filter(self):
        GroundTruthSplit.objects.create(filing=self.a, split="train")
        self._call("--split", "train")
        self.assertEqual(
            list(FilingClassification.objects.values_list("filing_id", flat=True)),
            [self.a.id],
        )

    def test_filing_id_and_limit(self):
        self._call("--filing-id", str(self.b.id))
        self.assertEqual(
            list(FilingClassification.objects.values_list("filing_id", flat=True)),
            [self.b.id],
        )

    def test_ollama_down_stops_and_leaves_watcher_alone(self):
        watcher_run = AutomationRun.objects.create(status=AutomationRun.Status.COMPLETED)
        self._call("--max-errors", "2", fake=FakeGenerator(error="connection refused"))

        run = InterpreterRun.objects.get()
        self.assertEqual(run.status, InterpreterRun.Status.FAILED)
        self.assertEqual(run.error_count, 2)
        self.assertEqual(FilingClassification.objects.count(), 0)
        self.assertEqual(FailureEvent.objects.count(), 0)
        self.assertEqual(AutomationRun.objects.count(), 1)
        watcher_run.refresh_from_db()
        self.assertEqual(watcher_run.status, AutomationRun.Status.COMPLETED)

    def test_stale_interpreter_run_reconciled_watcher_run_untouched(self):
        stale = InterpreterRun.objects.create(
            taxonomy_version="1", prompt_version="1", model_name="m"
        )
        watcher_running = AutomationRun.objects.create(status=AutomationRun.Status.RUNNING)
        self._call("--dry-run")
        stale.refresh_from_db()
        watcher_running.refresh_from_db()
        self.assertEqual(stale.status, InterpreterRun.Status.FAILED)
        self.assertEqual(watcher_running.status, AutomationRun.Status.RUNNING)

    def test_pending_excludes_done(self):
        InterpreterService(generator=self.fake, threshold=0.7).classify(self.a)
        self.assertEqual([f.id for f in pending_filings()], [self.b.id])


# ----------------------------------------------------------------------
# Review queue API
# ----------------------------------------------------------------------

# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class ReviewApiTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user("boss", password="pw-boss-123", is_staff=True)
        self.reader = User.objects.create_user("reader", password="pw-reader-123")

        self.low = register(1)
        self.high = register(2)
        self.fixed_later = register(3)

        svc = lambda reply: InterpreterService(generator=FakeGenerator(reply), threshold=0.7)
        self.low_row = svc(answer(confidence=0.42)).classify(self.low)
        self.high_row = svc(answer(confidence=0.95)).classify(self.high)
        # Old low-confidence row, then a newer confident one: not in queue.
        svc(answer(confidence=0.3)).classify(self.fixed_later)
        svc(answer(confidence=0.95)).classify(self.fixed_later)

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _ids(self, user, status=None):
        params = {"status": status} if status else {}
        response = self._client(user).get(reverse("filings"), params)
        self.assertEqual(response.status_code, 200)
        return {row["id"] for row in response.json()["results"]}

    def _override(self, user, row, **body):
        payload = {"category": "COMMERCIAL", "is_material": True, "note": ""}
        payload.update(body)
        return self._client(user).post(
            reverse("override_classification", args=[row.id]), payload, format="json"
        )

    def test_queue_holds_latest_low_confidence_only(self):
        self.assertEqual(self._ids(self.staff, "classification_review"), {self.low.id})

    def test_queue_is_staff_only(self):
        response = self._client(self.reader).get(
            reverse("filings"), {"status": "classification_review"}
        )
        self.assertEqual(response.status_code, 403)

    def test_existing_views_unchanged(self):
        # Needs-review filings stay on the main page and out of the
        # failures tab: no FailureEvent was written.
        self.assertIn(self.low.id, self._ids(self.staff))
        self.assertEqual(self._ids(self.staff, "review"), set())
        self.assertEqual(self._ids(self.staff, "failed"), set())

    def test_old_classification_field_still_null(self):
        rows = self._client(self.staff).get(reverse("filings")).json()["results"]
        self.assertTrue(all(row["classification"] is None for row in rows))

    def test_interpretation_staff_only(self):
        rows = self._client(self.reader).get(reverse("filings")).json()["results"]
        self.assertTrue(all(row["interpretation"] is None for row in rows))

        rows = self._client(self.staff).get(
            reverse("filings"), {"status": "classification_review"}
        ).json()["results"]
        interp = rows[0]["interpretation"]
        self.assertEqual(interp["id"], self.low_row.id)
        self.assertEqual(interp["category"], "FINANCING")
        self.assertEqual(interp["confidence"], 0.42)
        self.assertIsNone(interp["override"])

    def test_override_creates_row_and_leaves_queue(self):
        response = self._override(self.staff, self.low_row, note="customer deal")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["id"], self.low.id)
        self.assertEqual(body["interpretation"]["override"]["category"], "COMMERCIAL")
        self.assertEqual(body["interpretation"]["override"]["reviewer"], "boss")

        self.low_row.refresh_from_db()
        self.assertEqual(self.low_row.category, "FINANCING")   # model answer untouched
        self.assertEqual(self._ids(self.staff, "classification_review"), set())

    def test_second_override_is_409(self):
        self._override(self.staff, self.low_row)
        response = self._override(self.staff, self.low_row, category="FINANCING")
        self.assertEqual(response.status_code, 409)
        self.assertIn("boss", response.json()["detail"])
        self.assertEqual(ClassificationOverride.objects.count(), 1)

    def test_override_validation(self):
        self.assertEqual(self._override(self.staff, self.low_row, category="XYZ").status_code, 400)
        self.assertEqual(
            self._override(self.staff, self.low_row, is_material=False).status_code, 400
        )
        ok = self._override(self.staff, self.low_row, is_material=False, category="")
        self.assertEqual(ok.status_code, 201)

    def test_override_permissions_and_stale_rows(self):
        self.assertEqual(self._override(self.reader, self.low_row).status_code, 403)
        older = FilingClassification.objects.filter(
            filing=self.fixed_later
        ).order_by("created_at", "id").first()
        self.assertEqual(self._override(self.staff, older).status_code, 409)
        missing = FilingClassification(id=999999)
        self.assertEqual(self._override(self.staff, missing).status_code, 404)

    def test_email_sent_at_exposed_read_only(self):
        from django.utils import timezone
        self.low.email_sent_at = timezone.now()
        self.low.save(update_fields=["email_sent_at"])
        rows = self._client(self.staff).get(reverse("filings")).json()["results"]
        by_id = {row["id"]: row for row in rows}
        self.assertIsNotNone(by_id[self.low.id]["email_sent_at"])
        self.assertIsNone(by_id[self.high.id]["email_sent_at"])
        # The Interpreter row's time is present so View Mail can compare.
        self.assertIsNotNone(by_id[self.low.id]["interpretation"]["created_at"])

    def test_taxonomy_endpoint(self):
        body = self._client(self.reader).get(reverse("taxonomy")).json()
        self.assertEqual(body["version"], TAXONOMY_VERSION)
        self.assertEqual(len(body["categories"]), 5)

    def test_labelling_stays_blind_with_classifications_present(self):
        from django.contrib.auth.models import Group
        self.reader.groups.add(Group.objects.get(name="labeller"))
        LabellingSample.objects.create(
            filing=self.low, position=0, taxonomy_version=TAXONOMY_VERSION
        )
        body = self._client(self.reader).get(reverse("labelling_next")).json()
        text = json.dumps(body)
        self.assertNotIn("MODEL-REASONING-MARKER", text)
        self.assertNotIn("interpretation", text)
        self.assertNotIn("confidence\": 0.42", text)
