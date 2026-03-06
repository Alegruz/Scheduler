from fastapi import APIRouter
from .endpoints import auth, goals, task_templates, schedules, audit, sync, health, notifications

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(goals.router)
api_router.include_router(task_templates.router)
api_router.include_router(schedules.router)
api_router.include_router(audit.router)
api_router.include_router(sync.router)
api_router.include_router(health.router)
api_router.include_router(notifications.router)
