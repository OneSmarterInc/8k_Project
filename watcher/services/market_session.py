from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
import pandas_market_calendars as mcal


class MarketSessionService:
    """
    Maps an EDGAR acceptance datetime to the correct
    tradeable U.S. equity market entry session.

    Entry rule:
        T_PLUS_1:
            Any filing accepted on a given day maps to
            the next NYSE trading session.

        SAME_SESSION:
            Filing before market close maps to the same
            session. Filing at/after market close maps
            to the next session.

    Weekend:
        Next NYSE trading session.

    NYSE holiday:
        Next NYSE trading session.

    Early-close session:
        Uses actual NYSE market close from the exchange
        calendar.

    This service is deterministic.

    It does NOT:
        - download SEC filings
        - modify download registry
        - write to PostgreSQL
        - generate summaries
        - send emails
    """

    UTC = ZoneInfo(
        "UTC"
    )

    EASTERN = ZoneInfo(
        "America/New_York"
    )

    CALENDAR_NAME = "NYSE"

    ENTRY_RULE = getattr(
        settings,
        "ENTRY_RULE",
        "T_PLUS_1",
    )

    def __init__(
        self,
        calendar=None,
    ):
        self.calendar = (
            calendar
            or mcal.get_calendar(
                self.CALENDAR_NAME
            )
        )

    @classmethod
    def _normalize_datetime(
        cls,
        value,
    ):
        """
        Require timezone-aware datetime and normalize to UTC.
        """

        if not isinstance(
            value,
            datetime,
        ):
            raise TypeError(
                "accepted_at must be a datetime instance."
            )

        if value.tzinfo is None:
            raise ValueError(
                "accepted_at must include timezone information."
            )

        return value.astimezone(
            cls.UTC
        )

    def _schedule_from(
        self,
        start_date,
    ):
        """
        Get NYSE sessions starting from supplied date.
        """

        end_date = (
            start_date
            + timedelta(
                days=14,
            )
        )

        schedule = self.calendar.schedule(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

        if schedule.empty:
            raise RuntimeError(
                "NYSE calendar returned no trading sessions "
                f"between {start_date} and {end_date}."
            )

        return schedule

    def entry_session(
        self,
        accepted_at,
    ):
        """
        Return entry trading session date.

        Examples:

            T_PLUS_1:

            Monday 3:30 PM ET
                -> Tuesday


            Monday 4:45 PM ET
                -> Tuesday


            Saturday
                -> Monday
        """

        accepted_utc = (
            self._normalize_datetime(
                accepted_at
            )
        )

        accepted_eastern = (
            accepted_utc.astimezone(
                self.EASTERN
            )
        )

        local_date = (
            accepted_eastern.date()
        )

        schedule = self._schedule_from(
            local_date
        )

        sessions = list(
            schedule.iterrows()
        )

        for index, (
            session_label,
            row,
        ) in enumerate(
            sessions
        ):

            session_date = (
                session_label.date()
            )

            # Weekend / holiday:
            # first available session after
            # acceptance date becomes entry session
            if session_date > local_date:
                return session_date

            if session_date != local_date:
                continue

            market_close = (
                row["market_close"]
                .to_pydatetime()
            )

            market_close_utc = (
                market_close.astimezone(
                    self.UTC
                )
            )

            # --------------------------------------------------
            # Roadmap rule:
            #
            # EDGAR acceptance -> next trading session
            # --------------------------------------------------
            if self.ENTRY_RULE == "T_PLUS_1":

                if index + 1 < len(sessions):

                    next_label = (
                        sessions[index + 1][0]
                    )

                    return next_label.date()


            # --------------------------------------------------
            # Optional legacy behaviour
            # --------------------------------------------------
            elif self.ENTRY_RULE == "SAME_SESSION":

                if accepted_utc < market_close_utc:

                    return session_date

                if index + 1 < len(sessions):

                    next_label = (
                        sessions[index + 1][0]
                    )

                    return next_label.date()


            else:
                raise ValueError(
                    f"Unsupported ENTRY_RULE: {self.ENTRY_RULE}"
                )

        raise RuntimeError(
            "Unable to determine an NYSE entry session "
            f"for acceptance datetime {accepted_at!r}."
        )