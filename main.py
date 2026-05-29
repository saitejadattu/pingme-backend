import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

load_dotenv()

from config.database import close_mongo_connection, connect_to_mongo
from routes.auth import router as auth_router
from routes.google_calendar import router as google_calendar_router
from routes.profile import router as profile_router
from routes.reminders import router as reminders_router
from services.cron_service import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
IS_AWS_LAMBDA = bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def should_start_scheduler() -> bool:
    enabled = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"
    # Lambda is stateless, so reminder scheduling should move to EventBridge instead
    # of starting the in-process APScheduler.
    return enabled and not IS_AWS_LAMBDA


@asynccontextmanager
async def lifespan(_: FastAPI):
    await connect_to_mongo()
    if should_start_scheduler():
        start_scheduler()
    yield
    if should_start_scheduler():
        stop_scheduler()
    await close_mongo_connection()


app = FastAPI(title="PingMe API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(reminders_router)
app.include_router(profile_router)
app.include_router(google_calendar_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# Local development continues to use `uvicorn main:app --reload`.
# AWS Lambda uses this handler through API Gateway.
handler = Mangum(app)
