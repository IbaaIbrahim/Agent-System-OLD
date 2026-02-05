"""Job scheduler for billing background tasks.

Uses APScheduler to run periodic jobs:
- Monthly credit reset (daily at 00:05 UTC)
- Top-up expiration (hourly at :15)
- Low balance alerts (every 6 hours)
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common import get_logger

logger = get_logger(__name__)

# Global scheduler instance
_scheduler = None


def setup_scheduler(app: FastAPI) -> None:
    """Set up the job scheduler with FastAPI lifecycle.

    Args:
        app: FastAPI application instance
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning(
            "APScheduler not installed. Background jobs will not run. "
            "Install with: pip install apscheduler"
        )
        return

    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Import job functions
    from .credit_reset import reset_monthly_credits
    from .low_balance_alert import check_low_balances
    from .topup_expiration import expire_topups

    # Schedule jobs
    _scheduler.add_job(
        reset_monthly_credits,
        CronTrigger(hour=0, minute=5),  # Daily at 00:05 UTC
        id="reset_monthly_credits",
        name="Reset monthly subscription credits",
        replace_existing=True,
    )

    _scheduler.add_job(
        expire_topups,
        CronTrigger(minute=15),  # Every hour at :15
        id="expire_topups",
        name="Expire old top-ups",
        replace_existing=True,
    )

    _scheduler.add_job(
        check_low_balances,
        CronTrigger(hour="*/6"),  # Every 6 hours
        id="check_low_balances",
        name="Check for low wallet balances",
        replace_existing=True,
    )

    @app.on_event("startup")
    async def start_scheduler():
        """Start the scheduler on app startup."""
        _scheduler.start()
        logger.info(
            "Job scheduler started",
            jobs=[job.id for job in _scheduler.get_jobs()],
        )

    @app.on_event("shutdown")
    async def shutdown_scheduler():
        """Shut down the scheduler on app shutdown."""
        _scheduler.shutdown(wait=False)
        logger.info("Job scheduler stopped")


def get_scheduler():
    """Get the scheduler instance.

    Returns:
        AsyncIOScheduler instance or None if not initialized
    """
    return _scheduler


async def run_job_now(job_id: str) -> None:
    """Manually trigger a job to run immediately.

    Useful for testing or manual intervention.

    Args:
        job_id: Job identifier (e.g., "reset_monthly_credits")
    """
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized")

    job = _scheduler.get_job(job_id)
    if job is None:
        raise ValueError(f"Job not found: {job_id}")

    logger.info("Manually triggering job", job_id=job_id)
    await job.func()


def list_jobs() -> list[dict]:
    """List all scheduled jobs.

    Returns:
        List of job information dicts
    """
    if _scheduler is None:
        return []

    return [
        {
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        }
        for job in _scheduler.get_jobs()
    ]
