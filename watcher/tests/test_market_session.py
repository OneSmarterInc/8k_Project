from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from watcher.services.market_session import (
    MarketSessionService,
)


class MarketSessionServiceTests(SimpleTestCase):

    EASTERN = ZoneInfo("America/New_York")

    def setUp(self):
        self.service = MarketSessionService()

    def _accepted_at(
        self,
        year,
        month,
        day,
        hour,
        minute=0,
    ):
        return datetime(
            year,
            month,
            day,
            hour,
            minute,
            tzinfo=self.EASTERN,
        )

    def _assert_entry_session(
        self,
        *,
        rule,
        accepted_at,
        expected,
    ):
        with override_settings(
            ENTRY_RULE=rule
        ):
            result = self.service.entry_session(
                accepted_at
            )

        self.assertEqual(
            result,
            expected,
        )

    # ---------------------------------------------------------
    # T_PLUS_1
    # ---------------------------------------------------------

    def test_t_plus_1_morning_filing_moves_to_next_session(self):
        """
        Critical rule-distinguishing case.

        Monday morning:
        T_PLUS_1 -> Tuesday
        SAME_SESSION -> Monday
        """

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                9,
                14,
                9,
                45,
            ),
            expected=date(
                2026,
                9,
                15,
            ),
        )

    def test_t_plus_1_after_market_close_moves_to_next_session(self):

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                9,
                14,
                16,
                45,
            ),
            expected=date(
                2026,
                9,
                15,
            ),
        )

    def test_t_plus_1_friday_after_close_skips_weekend(self):

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                9,
                18,
                17,
                0,
            ),
            expected=date(
                2026,
                9,
                21,
            ),
        )

    def test_t_plus_1_saturday_rolls_to_monday(self):

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                9,
                19,
                10,
                0,
            ),
            expected=date(
                2026,
                9,
                21,
            ),
        )

    def test_t_plus_1_day_before_thanksgiving_skips_holiday(self):

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                11,
                25,
                11,
                0,
            ),
            expected=date(
                2026,
                11,
                27,
            ),
        )

    def test_t_plus_1_thanksgiving_rolls_to_friday(self):

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                11,
                26,
                11,
                0,
            ),
            expected=date(
                2026,
                11,
                27,
            ),
        )

    def test_t_plus_1_half_day_before_close_still_moves_to_next_session(self):
        """
        Friday after Thanksgiving is an NYSE early-close session.
        T_PLUS_1 must still move to the following trading session.
        """

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                11,
                27,
                12,
                0,
            ),
            expected=date(
                2026,
                11,
                30,
            ),
        )

    def test_t_plus_1_year_end_weekend_rolls_to_next_session(self):

        self._assert_entry_session(
            rule="T_PLUS_1",
            accepted_at=self._accepted_at(
                2026,
                12,
                26,
                11,
                0,
            ),
            expected=date(
                2026,
                12,
                28,
            ),
        )

    # ---------------------------------------------------------
    # SAME_SESSION
    # ---------------------------------------------------------

    def test_same_session_morning_filing_stays_same_day(self):
        """
        Critical mirror of the T_PLUS_1 morning test.
        """

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                9,
                14,
                9,
                45,
            ),
            expected=date(
                2026,
                9,
                14,
            ),
        )

    def test_same_session_after_close_moves_to_next_session(self):

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                9,
                14,
                16,
                45,
            ),
            expected=date(
                2026,
                9,
                15,
            ),
        )

    def test_same_session_friday_after_close_skips_weekend(self):

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                9,
                18,
                17,
                0,
            ),
            expected=date(
                2026,
                9,
                21,
            ),
        )

    def test_same_session_saturday_rolls_to_monday(self):

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                9,
                19,
                10,
                0,
            ),
            expected=date(
                2026,
                9,
                21,
            ),
        )

    def test_same_session_day_before_thanksgiving_stays_same_day(self):

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                11,
                25,
                11,
                0,
            ),
            expected=date(
                2026,
                11,
                25,
            ),
        )

    def test_same_session_thanksgiving_rolls_to_friday(self):

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                11,
                26,
                11,
                0,
            ),
            expected=date(
                2026,
                11,
                27,
            ),
        )

    def test_same_session_half_day_before_close_stays_same_session(self):
        """
        Friday after Thanksgiving closes early.
        12:00 ET is before the early close.
        """

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                11,
                27,
                12,
                0,
            ),
            expected=date(
                2026,
                11,
                27,
            ),
        )

    def test_same_session_half_day_after_close_moves_to_monday(self):
        """
        Verify actual NYSE early-close time is respected rather
        than assuming a normal 4 PM close.
        """

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                11,
                27,
                13,
                1,
            ),
            expected=date(
                2026,
                11,
                30,
            ),
        )

    def test_same_session_year_end_weekend_rolls_to_next_session(self):

        self._assert_entry_session(
            rule="SAME_SESSION",
            accepted_at=self._accepted_at(
                2026,
                12,
                26,
                11,
                0,
            ),
            expected=date(
                2026,
                12,
                28,
            ),
        )

    # ---------------------------------------------------------
    # DST / configuration / validation
    # ---------------------------------------------------------

    def test_dst_transition_weekend_rolls_to_monday(self):
        """
        March 8, 2026 is the U.S. spring DST transition.
        A Friday-after-close filing must still resolve correctly
        across the DST weekend.
        """

        accepted_at = self._accepted_at(
            2026,
            3,
            6,
            17,
            0,
        )

        for rule in (
            "T_PLUS_1",
            "SAME_SESSION",
        ):
            with self.subTest(
                rule=rule
            ):
                self._assert_entry_session(
                    rule=rule,
                    accepted_at=accepted_at,
                    expected=date(
                        2026,
                        3,
                        9,
                    ),
                )

    def test_override_settings_changes_rule_on_same_service_instance(self):
        """
        Permanent regression test for W-016.
        """

        accepted_at = self._accepted_at(
            2026,
            9,
            14,
            9,
            45,
        )

        with override_settings(
            ENTRY_RULE="T_PLUS_1"
        ):
            self.assertEqual(
                self.service.entry_rule,
                "T_PLUS_1",
            )

            self.assertEqual(
                self.service.entry_session(
                    accepted_at
                ),
                date(
                    2026,
                    9,
                    15,
                ),
            )

        with override_settings(
            ENTRY_RULE="SAME_SESSION"
        ):
            self.assertEqual(
                self.service.entry_rule,
                "SAME_SESSION",
            )

            self.assertEqual(
                self.service.entry_session(
                    accepted_at
                ),
                date(
                    2026,
                    9,
                    14,
                ),
            )

    def test_unsupported_entry_rule_raises_error(self):

        accepted_at = self._accepted_at(
            2026,
            9,
            14,
            9,
            45,
        )

        with override_settings(
            ENTRY_RULE="INVALID_RULE"
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Unsupported ENTRY_RULE",
            ):
                self.service.entry_session(
                    accepted_at
                )

    def test_naive_datetime_is_rejected(self):

        accepted_at = datetime(
            2026,
            9,
            14,
            9,
            45,
        )

        with self.assertRaisesRegex(
            ValueError,
            "timezone information",
        ):
            self.service.entry_session(
                accepted_at
            )