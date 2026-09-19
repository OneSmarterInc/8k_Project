import logging
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from datetime import datetime
from watcher.models import ScheduleConfig

logger = logging.getLogger(__name__)

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
                dt = datetime.combine(config.start_date, config.start_time)
                triggers.append(DateTrigger(run_date=dt))
                
        elif freq == "daily":
            for t_str in run_times:
                h, m = map(int, t_str.split(":"))
                if config.daily_recur == 1:
                    triggers.append(CronTrigger(hour=h, minute=m))
                else:
                    from apscheduler.triggers.interval import IntervalTrigger
                    import datetime as dt_module
                    start = datetime.combine(config.start_date or datetime.today().date(), dt_module.time(h, m))
                    triggers.append(IntervalTrigger(days=config.daily_recur, start_date=start))
                    
        elif freq == "weekly":
            day_map = {'mon': 'mon', 'tue': 'tue', 'wed': 'wed', 'thu': 'thu', 'fri': 'fri', 'sat': 'sat', 'sun': 'sun'}
            selected_days = [day_map[k] for k, v in config.weekly_days.items() if v and k in day_map]
            day_cron = ",".join(selected_days) if selected_days else "mon"
            
            for t_str in run_times:
                h, m = map(int, t_str.split(":"))
                triggers.append(CronTrigger(day_of_week=day_cron, hour=h, minute=m))
                
        elif freq == "monthly":
            for t_str in run_times:
                h, m = map(int, t_str.split(":"))
                
                months = "*"
                if config.monthly_months and config.monthly_months.lower() != "all months":
                    months = "*"
                    
                if config.monthly_type == "days":
                    days = config.monthly_days.replace(" ", "") if config.monthly_days else "1"
                    triggers.append(CronTrigger(month=months, day=days, hour=h, minute=m))
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
                        
                    triggers.append(CronTrigger(month=months, day_of_week=day_expr, hour=h, minute=m))
                    
    except Exception as e:
        logger.error(f"Error building triggers from config: {e}")
        
    return triggers
