"""Read-only Django admin for the Interpreter and ground-truth tables."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from watcher.interpreter.service import InterpreterService
from watcher.interpreter.taxonomy import TAXONOMY_VERSION
from watcher.models import (
    ClassificationOverride,
    GroundTruthLabel,
    GroundTruthSplit,
    InterpreterRun,
    LabellingSample,
)

from .test_interpreter import FakeGenerator, register

MODELS = (
    "filingclassification",
    "classificationoverride",
    "interpreterrun",
    "groundtruthlabel",
    "labellingsample",
    "groundtruthsplit",
)


# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class InterpreterAdminTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("root", "r@x.com", "pw-root-12345")
        self.client.force_login(self.admin)

        filing = register(1)
        self.row = InterpreterService(
            generator=FakeGenerator(), threshold=0.7
        ).classify(filing)
        ClassificationOverride.objects.create(
            classification=self.row, reviewer=self.admin,
            taxonomy_version=TAXONOMY_VERSION, is_material=True,
            category="COMMERCIAL",
        )
        InterpreterRun.objects.create(
            taxonomy_version="1", prompt_version="1", model_name="m"
        )
        LabellingSample.objects.create(
            filing=filing, position=0, taxonomy_version=TAXONOMY_VERSION
        )
        GroundTruthLabel.objects.create(
            filing=filing, labeller=self.admin, taxonomy_version=TAXONOMY_VERSION,
            is_material=True, category="FINANCING", confidence=4,
        )
        GroundTruthSplit.objects.create(filing=filing, split="train")

    def test_every_list_page_loads(self):
        for model in MODELS:
            url = reverse(f"admin:watcher_{model}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, model)

    def test_list_shows_data(self):
        page = self.client.get(
            reverse("admin:watcher_filingclassification_changelist")
        ).content.decode()
        self.assertIn("FINANCING", page)
        self.assertIn("TEST", page)

    def test_filters_and_search_work(self):
        url = reverse("admin:watcher_filingclassification_changelist")
        self.assertEqual(self.client.get(url, {"needs_human_review__exact": "0"}).status_code, 200)
        self.assertEqual(self.client.get(url, {"q": "TEST"}).status_code, 200)

    def test_cannot_add_change_or_delete(self):
        for model in MODELS:
            add = reverse(f"admin:watcher_{model}_add")
            self.assertEqual(self.client.get(add).status_code, 403, model)

        change = reverse("admin:watcher_filingclassification_change", args=[self.row.id])
        response = self.client.post(change, {"category": "LEGAL_REGULATORY"})
        self.assertIn(response.status_code, (200, 403))
        self.row.refresh_from_db()
        self.assertEqual(self.row.category, "FINANCING")

        delete = reverse("admin:watcher_filingclassification_delete", args=[self.row.id])
        self.assertEqual(self.client.post(delete, {"post": "yes"}).status_code, 403)
        self.row.refresh_from_db()

    def test_non_staff_cannot_open_admin(self):
        user = get_user_model().objects.create_user("x", password="pw-x-123456")
        self.client.force_login(user)
        url = reverse("admin:watcher_filingclassification_changelist")
        self.assertEqual(self.client.get(url).status_code, 302)   # to admin login
