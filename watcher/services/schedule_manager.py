import logging
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
from watcher.models import ScheduleConfig
from django.conf import settings

logger = logging.getLogger(__name__)
tz = ZoneInfo(
    settings.TIME_ZONE
)

# ----------------------------------------------------------------------
# W-037 (R-08 / R-09)
#
# INTERVAL_FLOOR_MINUTES: the advisory lock already prevents overlapping
# runs, but a 1-minute setting would make every run abort on that lock.
#
# INTERVAL_CEILING_MINUTES: cron "*/N" is only meaningful inside one
# hour. Anything above 60 silently degrades to hourly, so it is clamped.
#
# ALLOWED_INTERVALS: "*/N" is only evenly spaced when N divides 60.
# "*/25" fires at :00, :25, :50 and then :00 - a ragged 10-minute gap at
# every hour boundary. Snapping to a divisor keeps the polling cadence
# reproducible, which D-01's five-day audit depends on.
# ----------------------------------------------------------------------
INTERVAL_FLOOR_MINUTES = 5
INTERVAL_CEILING_MINUTES = 60
ALLOWED_INTERVALS = (5, 10, 15, 20, 30, 60)

DEFAULT_INTERVAL_MINUTES = 20

SWEEP_HOUR = 22
SWEEP_MINUTE = 30


def normalize_interval_minutes(minutes):
    """
    W-037 step 3. Clamp to [5, 60] and snap up to the nearest divisor
    of 60 so the cron minute expression is evenly spaced.
    """
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_INTERVAL_MINUTES

    minutes = max(
        INTERVAL_FLOOR_MINUTES,
        min(INTERVAL_CEILING_MINUTES, minutes),
    )

    for allowed in ALLOWED_INTERVALS:
        if minutes <= allowed:
            return allowed

    return INTERVAL_CEILING_MINUTES


def build_hour_expression(config):
    """
    Build the cron hour field for the active window.

    The range is INCLUSIVE of the end hour: 06:00-21:00 polls through
    21:59 ET. A window that wraps midnight is not expressible as a
    single cron range, so it falls back to all hours rather than
    building a trigger that never fires.
    """
    start = getattr(config, "active_window_start", None)
    end = getattr(config, "active_window_end", None)

    if not start or not end:
        return "*"

    if start.hour > end.hour:
        logger.warning(
            "W-037: active window %s-%s wraps midnight and cannot be "
            "expressed as one cron range; falling back to all hours.",
            start,
            end,
        )
        return "*"

    return f"{start.hour}-{end.hour}"


def build_interval_jobs(config: ScheduleConfig):
    """
    W-037. Return [(trigger, job_kwargs), ...] for the "interval"
    frequency only.

    A CronTrigger is used instead of IntervalTrigger so the polling
    pattern stays aligned to clock minutes across daemon restarts.

    The nightly sweep is deliberately scoped to this frequency. Adding
    it to every active schedule would change the behaviour of existing
    daily / weekly / monthly configs, which W-037 does not ask for.
    """
    minutes = normalize_interval_minutes(
        getattr(config, "interval_minutes", DEFAULT_INTERVAL_MINUTES)
    )

    jobs = [
        (
            CronTrigger(
                day_of_week="mon-fri",
                hour=build_hour_expression(config),
                minute=f"*/{minutes}",
                timezone=tz,
            ),
            {"sweep": False},
        )
    ]

    if getattr(config, "nightly_sweep", True):
        # R-09: runs after EDGAR's dissemination day has settled, every
        # day including weekends, so late Friday filings are picked up.
        jobs.append(
            (
                CronTrigger(
                    hour=SWEEP_HOUR,
                    minute=SWEEP_MINUTE,
                    timezone=tz,
                ),
                {"sweep": True},
            )
        )

    return jobs


def build_jobs_for_config(config: ScheduleConfig):
    """
    W-037. Preferred entry point for run_scheduler.

    Returns [(trigger, job_kwargs), ...].

    Only the "interval" frequency produces non-empty kwargs. Every other
    frequency is delegated unchanged to build_triggers_for_config, so
    its trigger list, order and count are byte-identical to before.
    """
    if not config or not config.is_active:
        return []

    if str(config.frequency or "").lower() == "interval":
        try:
            return build_interval_jobs(config)
        except Exception as exc:
            logger.error(
                f"Error building interval jobs from config: {exc}"
            )
            return []

    return [
        (trigger, {})
        for trigger in build_triggers_for_config(config)
    ]


def build_triggers_for_config(config: ScheduleConfig):
    """
    Given a ScheduleConfig, return a list of APScheduler triggers
    that represent all the times the task should run.
    """
    triggers = []
    
    if not config.is_active:
        return triggers
        
    try:
        run_times = config.run_times or []
        if not run_times:
            if config.start_time:
                run_times = [config.start_time.strftime("%H:%M")]
            else:
                run_times = ["00:00"]
                
        freq = config.frequency.lower()
        
        if freq == "onetime":
            if config.start_date and config.start_time:
                dt = datetime.combine(config.start_date, config.start_time).replace(tzinfo=tz)
                triggers.append(DateTrigger(run_date=dt, timezone=tz))
                
        elif freq == "daily":
            for t_str in run_times:
                h, m = map(int, t_str.split(":"))
                if config.daily_recur == 1:
                    triggers.append(CronTrigger(hour=h, minute=m, timezone=tz))
                else:
                    from apscheduler.triggers.interval import IntervalTrigger
                    import datetime as dt_module
                    start = datetime.combine(config.start_date or datetime.today().date(), dt_module.time(h, m)).replace(tzinfo=tz)
                    triggers.append(IntervalTrigger(days=config.daily_recur, start_date=start, timezone=tz))
                    
        elif freq == "weekly":
            day_map = {'mon': 'mon', 'tue': 'tue', 'wed': 'wed', 'thu': 'thu', 'fri': 'fri', 'sat': 'sat', 'sun': 'sun'}
            selected_days = [day_map[k] for k, v in config.weekly_days.items() if v and k in day_map]
            day_cron = ",".join(selected_days) if selected_days else "mon"
            
            for t_str in run_times:
                h, m = map(int, t_str.split(":"))
                triggers.append(CronTrigger(day_of_week=day_cron, hour=h, minute=m, timezone=tz))
                
        elif freq == "monthly":
            for t_str in run_times:
                h, m = map(int, t_str.split(":"))
                
                months = "*"
                if config.monthly_months and config.monthly_months.lower() != "all months":
                    months = config.monthly_months
                    
                if config.monthly_type == "days":
                    days = config.monthly_days.replace(" ", "") if config.monthly_days else "1"
                    triggers.append(CronTrigger(month=months, day=days, hour=h, minute=m, timezone=tz))
                else:
                    week_map = {"First": "1st", "Second": "2nd", "Third": "3rd", "Fourth": "4th", "Last": "last"}
                    day_map = {"Sunday": "sun", "Monday": "mon", "Tuesday": "tue", "Wednesday": "wed", "Thursday": "thu", "Friday": "fri", "Saturday": "sat"}
                    
                    nth = week_map.get(config.monthly_on_week, "1st")
                    dow = day_map.get(config.monthly_on_day, "sun")
                    
                    if nth == "last":
                        day_expr = f"{dow}l"
                    else:
                        num = nth.replace('st','').replace('nd','').replace('rd','').replace('th','')
                        day_expr = f"{dow}#{num}"
                        
                    triggers.append(CronTrigger(month=months, day_of_week=day_expr, hour=h, minute=m, timezone=tz))

        elif freq == "interval":
            # W-037. Kept here so any caller still using the legacy
            # function sees the interval triggers too. run_scheduler
            # uses build_jobs_for_config instead, because only that
            # entry point carries the sweep kwarg.
            triggers.extend(
                trigger
                for trigger, _kwargs in build_interval_jobs(config)
            )

    except Exception as e:
        logger.error(f"Error building triggers from config: {e}")
        
    return triggers