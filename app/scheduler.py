"""
Harmonogram zadań ETL — APScheduler.
Uruchamia ETL automatycznie co noc o 2:00.
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.services.etl_service import run_full_etl

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def setup_scheduler():
    scheduler.add_job(
        func=_run_etl_job,
        trigger=CronTrigger(hour=settings.etl_cron_hour, minute=0),
        id="nightly_etl",
        name="Nocny ETL SubiektGT → PostgreSQL",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"Scheduler uruchomiony. ETL o {settings.etl_cron_hour}:00 każdej nocy.")


async def _run_etl_job():
    logger.info("Nocny ETL: start")
    result = run_full_etl()
    logger.info(f"Nocny ETL: zakończony: {result}")
