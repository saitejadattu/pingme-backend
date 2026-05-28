from fastapi import APIRouter

from config.database import get_database
from middleware.auth_middleware import CurrentUser
from models.user import UserResponse, UserUpdate

router = APIRouter(prefix="/profile", tags=["profile"])


def serialize_user(user: dict) -> UserResponse:
    return UserResponse(
        id=str(user["_id"]),
        name=user["name"],
        email=user["email"],
        phone=user.get("phone"),
        is_verified=user.get("is_verified", False),
        google_calendar_connected=bool(user.get("google_calendar_token")),
    )


@router.get("", response_model=UserResponse)
async def get_profile(current_user=CurrentUser):
    return serialize_user(current_user)


@router.patch("", response_model=UserResponse)
async def update_profile(payload: UserUpdate, current_user=CurrentUser):
    db = get_database()
    await db.users.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"name": payload.name, "phone": payload.phone}},
    )
    updated = await db.users.find_one({"_id": current_user["_id"]})
    return serialize_user(updated)
