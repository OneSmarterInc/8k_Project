from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase

from watcher.services.market_session import (
    MarketSessionService,
)


class MarketSessionServiceTests(TestCase):

    def setUp(self):
        self.service = MarketSessionService()

    def test_after_market_close_moves_to_next_session(self):
        accepted_at = datetime(
            2026,
            9,
            15,
            16,
            11,
            tzinfo=ZoneInfo(
                "America/New_York"
            ),
        )

        result = self.service.entry_session(
            accepted_at
        )

        self.assertEqual(
            str(result),
            "2026-09-16",
        )

    def test_friday_after_close_skips_weekend(self):
        accepted_at = datetime(
            2026,
            9,
            18,
            17,
            0,
            tzinfo=ZoneInfo(
                "America/New_York"
            ),
        )

        result = self.service.entry_session(
            accepted_at
        )

        self.assertEqual(
            str(result),
            "2026-09-21",
        )