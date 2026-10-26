"""Celery application for Perimeter Automation Platform.

Provides async task execution for long-running operations:
VM provisioning, playbook runs, template refreshes, cert operations.
"""

import os
import sys

# Ensure python/ is on the path
sys.path.insert(0, os.path.dirname(__file__))

from celery import Celery
from config import cfg

celery = Celery(
    "perimeter",
    broker=cfg.CELERY_BROKER_URL,
    backend=cfg.CELERY_RESULT_BACKEND,
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=3600,         # 1 hour hard limit
    task_soft_time_limit=3500,    # soft limit for graceful cleanup
    worker_prefetch_multiplier=1, # one task at a time per worker
    worker_concurrency=4,         # 4 parallel tasks
    result_expires=3600,          # results expire after 1 hour
)

# Celery Beat (embedded via `celery worker -B`): a once-a-minute tick that fires
# any due node-update schedules (Updates page, Phase 4). Dynamic schedules live
# in SQLite, so no beat reconfiguration/restart is needed to add or remove them.
celery.conf.beat_schedule = {
    "updates-scheduler-tick": {
        "task": "perimeter.scheduler_tick",
        "schedule": 60.0,
    },
}

# Explicitly import task modules so they register with Celery
import tasks.workflows  # noqa: F401
