"""The Cloud Run service: a Pub/Sub push endpoint and the Monday job.

Answers are chosen for the sender, not for the reader: Pub/Sub gets 200 for
anything a retry cannot fix (a malformed message, a defect that would repeat)
and 500 only when the failure is transient, so redelivery has a point.
"""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from frostlog_notifier import service
from frostlog_notifier.errors import Transient
from frostlog_notifier.events import Undecodable, parse
from frostlog_notifier.notification import Notification
from frostlog_notifier.service import Notifier

log = logging.getLogger(__name__)

# Uvicorn configures its own loggers and leaves the root at WARNING with no handler,
# so without this every log.info in this service goes nowhere -- including the dry
# run's rendered blocks, which are the only way to see a notification before it is
# posted. Cloud Run reads the container's stderr, so a plain stream handler is all
# it takes; the level names go in because a run's failures are read together with
# its progress.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(title="frostlog-notifier")

_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    """The service's own notifier, built on first use (tests override this)."""
    global _notifier
    if _notifier is None:
        _notifier = service.build()
    return _notifier


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/signals/pubsub")
async def signals_pubsub(request: Request, notifier: Notifier = Depends(get_notifier)) -> Response:
    try:
        event = parse(await request.json())
    except (Undecodable, ValueError) as exc:
        # Nothing about this message will improve on redelivery; take it off the queue.
        log.warning("dropping an unusable message: %s", exc)
        return _answer(200, {"posted": [], "dropped": str(exc)})
    # BigQuery and Slack are both blocking; kept off the event loop so
    # that /healthz still answers while a summary is being drawn and uploaded.
    try:
        posted = await run_in_threadpool(notifier.handle, event)
    except Transient as exc:
        log.warning("transient failure; asking Pub/Sub to retry: %s", exc)
        return _answer(500, {"error": str(exc)})
    except Exception as exc:  # a defect, not a hiccup: reported, then acknowledged
        await run_in_threadpool(notifier.report_failure, "signals/pubsub", exc)
        return _answer(200, {"error": f"{type(exc).__name__}: {exc}"})
    return _answer(200, {"posted": [notification.kind for notification in posted]})


@app.post("/jobs/daily-summary")
def jobs_daily_summary(notifier: Notifier = Depends(get_notifier)) -> Response:
    return _run(notifier, "jobs/daily-summary", notifier.run_daily)


@app.post("/jobs/weekly-summary")
def jobs_weekly_summary(notifier: Notifier = Depends(get_notifier)) -> Response:
    return _run(notifier, "jobs/weekly-summary", notifier.run_weekly)


def _run(notifier: Notifier, context: str, job: Callable[[], list[Notification]]) -> Response:
    """One scheduled summary, and what the scheduler should make of the outcome.

    A retry is worth asking for either way -- the summary is a picture of a window
    ending now, so a second attempt says the same thing rather than a second thing.
    The narrow exception is a failure after Slack accepted the message, which no
    answer here can undo; the schedules are far enough apart that it is a duplicate
    and not a loop.
    """
    try:
        posted = job()
    except Transient as exc:
        log.warning("transient failure; asking Cloud Scheduler to retry: %s", exc)
        return _answer(500, {"error": str(exc)})
    except Exception as exc:  # reported, then answered so the run is not lost silently
        notifier.report_failure(context, exc)
        return _answer(500, {"error": f"{type(exc).__name__}: {exc}"})
    return _answer(200, {"posted": [notification.kind for notification in posted]})


def _answer(status: int, body: dict[str, Any]) -> Response:
    return JSONResponse(status_code=status, content=body)
