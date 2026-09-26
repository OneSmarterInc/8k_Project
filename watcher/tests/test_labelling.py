"""
Guide 4.1 / 4.2 (Phase 1-2): taxonomy, filing text, labelling queue.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from watcher.interpreter import taxonomy
from watcher.interpreter.filing_text import build_filing_text
from watcher.interpreter.labelling import (
    LABELLER_GROUP,
    allocate,
    build_sample,
    primary_stratum,
)
from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.models import (
    FilingChunk,
    FilingDocument,
    FilingSummaryCache,
    GroundTruthLabel,
    LabellingSample,
)


def _register(n, form="8-K", items="1.01;9.01"):
    filing = FilingRegistrationService().register(
        ticker="TEST",
        cik="1234567890",
        company_name="Test Co",
        form=form,
        accession_number=f"0009-26-{n:06d}",
        sequence=1,
        filing_date="2026-09-01",
        primary_document=f"doc{n}.htm",
        local_path=f"/tmp/{n}",
        source_url=f"http://test/{n}",
        report_date="2026-09-01",
    )
    filing.sec_item_codes = items
    filing.save(update_fields=["sec_item_codes"])
    return filing


def _document(filing, doc_type, *, primary=False, seq="1"):
    return FilingDocument.objects.create(
        filing=filing,
        sequence=seq,
        document_type=doc_type,
        document_name=f"{doc_type}.htm",
        source_url="http://test/doc",
        local_path="/tmp/doc",
        is_primary=primary,
    )


def _chunk(filing, document, index, text, start=None, end=None):
    return FilingChunk.objects.create(
        filing=filing,
        document=document,
        chunk_index=index,
        text=text,
        content_sha256=f"{filing.id}-{index}-{document.id if document else 0}",
        char_start=start,
        char_end=end,
    )


class TaxonomyTests(TestCase):

    def test_five_categories_with_required_keys(self):
        self.assertEqual(len(taxonomy.CATEGORIES), 5)
        for spec in taxonomy.CATEGORIES.values():
            for key in ("label", "definition", "includes", "excludes", "examples"):
                self.assertIn(key, spec)

    def test_other_is_a_real_category(self):
        self.assertTrue(taxonomy.is_valid_category(taxonomy.OTHER_CATEGORY))
        self.assertFalse(taxonomy.is_valid_category("ROUTINE"))

    def test_payload_carries_version(self):
        self.assertEqual(
            taxonomy.taxonomy_payload()["version"], taxonomy.TAXONOMY_VERSION
        )


class FilingTextTests(TestCase):

    def setUp(self):
        self.filing = _register(1)
        self.primary = _document(self.filing, "8-K", primary=True, seq="1")
        self.press = _document(self.filing, "EX-99.1", seq="2")
        self.contract = _document(self.filing, "EX-10.1", seq="3")

    def test_primary_first_then_press_release_and_contract_excluded(self):
        _chunk(self.filing, self.press, 0, "PRESS RELEASE TEXT")
        _chunk(self.filing, self.primary, 0, "BODY TEXT")
        _chunk(self.filing, self.contract, 0, "CREDIT AGREEMENT")

        result = build_filing_text(self.filing)

        self.assertLess(
            result.text.index("BODY TEXT"),
            result.text.index("PRESS RELEASE TEXT"),
        )
        self.assertNotIn("CREDIT AGREEMENT", result.text)
        self.assertEqual(result.documents, ["PRIMARY DOCUMENT", "EX-99.1"])

    def test_chunker_overlap_is_trimmed(self):
        # Chunker overlap: second chunk repeats the last 5 chars ("World").
        _chunk(self.filing, self.primary, 0, "Hello World", 0, 11)
        _chunk(self.filing, self.primary, 1, "World again", 6, 17)

        text = build_filing_text(self.filing).text
        self.assertEqual(text.count("World"), 1)
        self.assertIn("again", text)

    def test_truncation_is_visible(self):
        _chunk(self.filing, self.primary, 0, "x" * 500)
        result = build_filing_text(self.filing, max_chars=100)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.text), 100)

    def test_legacy_chunks_without_document_count_as_primary(self):
        _chunk(self.filing, None, 0, "LEGACY BODY")
        self.assertIn("LEGACY BODY", build_filing_text(self.filing).text)


class SamplingTests(TestCase):

    def test_primary_stratum_skips_exhibit_item(self):
        self.assertEqual(primary_stratum("9.01;1.01"), "1.01")
        self.assertEqual(primary_stratum("9.01"), "9.01")
        self.assertEqual(primary_stratum(""), "")

    def test_stratum_falls_back_to_parsed_codes(self):
        self.assertEqual(primary_stratum("", "8.01;9.01"), "8.01")

    def test_double_share_kept_when_pool_is_small(self):
        for n in range(1, 11):
            self._eligible(n, "1.01")
        rows = build_sample(size=500, double=100, seed=1)
        self.assertEqual(len(rows), 10)
        self.assertEqual(sum(1 for r in rows if r.required_labels == 2), 2)

    def test_allocate_floor_and_total(self):
        alloc = allocate({"1.01": 100, "8.01": 100, "4.02": 2}, 50, 5)
        self.assertEqual(sum(alloc.values()), 50)
        self.assertEqual(alloc["4.02"], 2)   # rare item fully represented

    def test_allocate_more_strata_than_budget(self):
        alloc = allocate({str(i): 3 for i in range(10)}, 4, 5)
        self.assertEqual(sum(alloc.values()), 4)

    def _eligible(self, n, items):
        filing = _register(n, items=items)
        _chunk(filing, _document(filing, "8-K", primary=True), 0, "body")
        return filing

    def test_build_sample_only_8k_with_text_and_marks_doubles(self):
        for n in range(1, 11):
            self._eligible(n, "1.01;9.01" if n % 2 else "8.01")

        no_text = _register(50)                      # no chunks
        ten_k = _register(51, form="10-K")
        _chunk(ten_k, _document(ten_k, "10-K", primary=True), 0, "body")

        rows = build_sample(size=8, double=3, seed=1)

        ids = {r.filing_id for r in rows}
        self.assertEqual(len(rows), 8)
        self.assertNotIn(no_text.id, ids)
        self.assertNotIn(ten_k.id, ids)
        self.assertEqual(sum(1 for r in rows if r.required_labels == 2), 3)

    def test_rerun_appends_and_never_resamples(self):
        for n in range(1, 6):
            self._eligible(n, "1.01")
        first = build_sample(size=3, double=0, seed=1)
        second = build_sample(size=10, double=0, seed=1)

        self.assertEqual(len(second), 2)
        self.assertFalse(
            {r.filing_id for r in first} & {r.filing_id for r in second}
        )
        self.assertGreater(
            min(r.position for r in second), max(r.position for r in first)
        )

    def test_command_dry_run_writes_nothing(self):
        self._eligible(1, "1.01")
        call_command(
            "build_labelling_sample", "--size", "1", "--double", "0",
            "--dry-run", stdout=StringIO(),
        )
        self.assertEqual(LabellingSample.objects.count(), 0)


class LabellingApiTests(TestCase):

    def setUp(self):
        User = get_user_model()
        group = Group.objects.get(name=LABELLER_GROUP)   # from migration 0027

        self.alice = User.objects.create_user("alice", password="pw-alice-123")
        self.bob = User.objects.create_user("bob", password="pw-bob-123")
        self.outsider = User.objects.create_user("eve", password="pw-eve-123")
        self.alice.groups.add(group)
        self.bob.groups.add(group)

        self.single = _register(1)
        self.double = _register(2)
        for f in (self.single, self.double):
            _chunk(f, _document(f, "8-K", primary=True), 0, f"body {f.id}")
            # A summary exists; labellers must never receive it.
            FilingSummaryCache.objects.create(
                filing=f, content_signature="s", model_name="m",
                prompt_version="v", summary="MODEL SUMMARY",
            )

        LabellingSample.objects.create(
            filing=self.double, required_labels=2, position=0,
            taxonomy_version=taxonomy.TAXONOMY_VERSION,
        )
        LabellingSample.objects.create(
            filing=self.single, required_labels=1, position=1,
            taxonomy_version=taxonomy.TAXONOMY_VERSION,
        )

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _next(self, user):
        response = self._client(user).get(reverse("labelling_next"))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _label(self, user, filing, **overrides):
        body = {
            "filing_id": filing.id,
            "is_material": True,
            "category": "FINANCING",
            "confidence": 4,
            "duration_seconds": 30,
        }
        body.update(overrides)
        return self._client(user).post(
            reverse("labelling_labels"), body, format="json"
        )

    def test_non_labeller_is_refused(self):
        client = self._client(self.outsider)
        self.assertEqual(client.get(reverse("labelling_next")).status_code, 403)
        self.assertEqual(self._label(self.outsider, self.single).status_code, 403)

    def test_staff_can_label_without_group(self):
        self.outsider.is_staff = True
        self.outsider.save()
        self.assertEqual(
            self._client(self.outsider).get(reverse("labelling_next")).status_code,
            200,
        )

    def test_payload_is_blind(self):
        body = self._next(self.alice)
        flat = str(body)
        self.assertNotIn("MODEL SUMMARY", flat)
        for forbidden in ("summary", "flag", "classification", "labels"):
            self.assertNotIn(forbidden, body["sample"]["filing"])
        self.assertIn("body", body["sample"]["text"])

    def test_served_in_position_order_and_claim_survives_refresh(self):
        first = self._next(self.alice)["sample"]["filing"]["id"]
        again = self._next(self.alice)["sample"]["filing"]["id"]
        self.assertEqual(first, self.double.id)
        self.assertEqual(again, first)

    def test_labeller_never_gets_same_filing_twice(self):
        self.assertEqual(self._label(self.alice, self.double).status_code, 201)
        self.assertEqual(
            self._next(self.alice)["sample"]["filing"]["id"], self.single.id
        )

    def test_double_labelled_goes_to_second_person(self):
        self._label(self.alice, self.double)
        self.assertEqual(
            self._next(self.bob)["sample"]["filing"]["id"], self.double.id
        )
        self.assertEqual(self._label(self.bob, self.double).status_code, 201)

    def test_single_slot_claimed_by_other_is_skipped(self):
        self._label(self.alice, self.double)
        self._label(self.bob, self.double)
        self.assertEqual(
            self._next(self.alice)["sample"]["filing"]["id"], self.single.id
        )
        self.assertTrue(self._next(self.bob)["done"])

    def test_full_filing_rejects_extra_label(self):
        self._label(self.alice, self.single)
        self.assertEqual(self._label(self.bob, self.single).status_code, 409)

    def test_duplicate_label_rejected(self):
        self._label(self.alice, self.single)
        self.assertEqual(self._label(self.alice, self.single).status_code, 409)

    def test_routine_must_have_no_category(self):
        response = self._label(
            self.alice, self.single, is_material=False, category="FINANCING"
        )
        self.assertEqual(response.status_code, 400)
        ok = self._label(self.alice, self.single, is_material=False, category="")
        self.assertEqual(ok.status_code, 201)

    def test_invalid_category_and_confidence(self):
        self.assertEqual(
            self._label(self.alice, self.single, category="XYZ").status_code, 400
        )
        self.assertEqual(
            self._label(self.alice, self.single, confidence=9).status_code, 400
        )

    def test_filing_outside_sample_is_404(self):
        outside = _register(99)
        self.assertEqual(self._label(self.alice, outside).status_code, 404)

    def test_correction_is_new_row_not_edit(self):
        first = self._label(self.alice, self.single).json()
        fix = self._label(
            self.alice, self.single, category="COMMERCIAL",
            supersedes=first["id"],
        )
        self.assertEqual(fix.status_code, 201)
        self.assertEqual(GroundTruthLabel.objects.count(), 2)
        original = GroundTruthLabel.objects.get(pk=first["id"])
        self.assertEqual(original.category, "FINANCING")   # untouched

        again = self._label(
            self.alice, self.single, category="MA_STRATEGIC",
            supersedes=first["id"],
        )
        self.assertEqual(again.status_code, 409)

    def test_cannot_correct_someone_elses_label(self):
        first = self._label(self.alice, self.double).json()
        response = self._label(self.bob, self.double, supersedes=first["id"])
        self.assertEqual(response.status_code, 404)

    def test_progress_counts(self):
        self._label(self.alice, self.double)
        progress = self._next(self.alice)["progress"]
        self.assertEqual(progress["slots_total"], 3)
        self.assertEqual(progress["slots_filled"], 1)
        self.assertEqual(progress["labelled_by_me"], 1)

    def test_labelling_does_not_change_filings_list(self):
        before = self._client(self.alice).get(reverse("filings")).json()["count"]
        self._label(self.alice, self.single)
        after = self._client(self.alice).get(reverse("filings")).json()["count"]
        self.assertEqual(before, after)
