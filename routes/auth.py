import os
from datetime import datetime, timezone
from urllib.parse import urlencode

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from config.database import get_database
from models.user import ForgotPasswordRequest, UserCreate, UserLogin, UserResponse
from services.auth_service import (
    create_access_token,
    generate_verification_token,
    hash_password,
    reset_expiry,
    verification_expiry,
    verify_password,
)
from services.calendar_service import (
    AUTH_SCOPES,
    CALENDAR_SCOPES,
    exchange_code_for_tokens,
    fetch_google_user_info,
    get_google_auth_url,
)
from services.email_service import (
    ALLOW_CONSOLE_EMAIL_FALLBACK,
    EmailDeliveryError,
    log_verification_link,
    send_password_reset_email,
    send_verification_email,
)

router = APIRouter(prefix="/auth", tags=["auth"])

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


def serialize_user(user: dict) -> UserResponse:
    return UserResponse(
        id=str(user["_id"]),
        name=user["name"],
        email=user["email"],
        phone=user.get("phone"),
        is_verified=user.get("is_verified", False),
        google_calendar_connected=bool(user.get("google_calendar_token")),
    )


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_oauth_redirect(token: str, user: UserResponse) -> str:
    params = urlencode(
        {
            "token": token,
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone or "",
            "is_verified": str(user.is_verified).lower(),
            "google_calendar_connected": str(user.google_calendar_connected).lower(),
        }
    )
    return f"{FRONTEND_URL}/oauth/callback?{params}"


@router.post("/signup")
async def signup(payload: UserCreate):
    db = get_database()
    existing = await db.users.find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    token = generate_verification_token()
    user = {
        "name": payload.name,
        "email": payload.email.lower(),
        "password": hash_password(payload.password),
        "phone": payload.phone,
        "is_verified": False,
        "verification_token": token,
        "verification_token_expires": verification_expiry(),
        "google_calendar_token": None,
        "google_refresh_token": None,
        "created_at": datetime.now(timezone.utc),
    }
    await db.users.insert_one(user)
    try:
        send_verification_email(user["email"], user["name"], token)
    except EmailDeliveryError as exc:
        if not ALLOW_CONSOLE_EMAIL_FALLBACK:
            await db.users.delete_one({"email": user["email"]})
            raise HTTPException(status_code=400, detail=f"Verification email failed: {exc}") from exc
        log_verification_link(user["email"], token)
        return {
            "message": "Verification email could not be sent in Resend test mode. Open the verification link from the backend terminal."
        }
    return {"message": "Verification email sent"}


@router.get("/verify")
async def verify_email(token: str = Query(...)):
    db = get_database()
    user = await db.users.find_one({"verification_token": token})
    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification token")
    expires_at = ensure_utc(user.get("verification_token_expires"))
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Verification token expired")
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"is_verified": True},
            "$unset": {"verification_token": "", "verification_token_expires": ""},
        },
    )
    return {"message": "Email verified successfully"}


@router.post("/login")
async def login(payload: UserLogin):
    db = get_database()
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not verify_password(payload.password, user["password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.get("is_verified"):
        raise HTTPException(status_code=403, detail="Please verify your email before logging in")

    token = create_access_token(str(user["_id"]), user["email"])
    return {"access_token": token, "user": serialize_user(user)}


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    db = get_database()
    user = await db.users.find_one({"email": payload.email.lower()})
    if user:
        token = generate_verification_token()
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"reset_token": token, "reset_token_expires": reset_expiry()}},
        )
        try:
            send_password_reset_email(user["email"], user["name"], token)
        except EmailDeliveryError as exc:
            raise HTTPException(status_code=400, detail=f"Reset email failed: {exc}") from exc
    return {"message": "Reset email sent"}


@router.get("/google")
async def google_auth(state: str = ""):
    return RedirectResponse(get_google_auth_url(state or "pingme", CALENDAR_SCOPES))


@router.get("/google/login")
async def google_login():
    return RedirectResponse(get_google_auth_url("auth-login", CALENDAR_SCOPES))


@router.get("/google/callback")
async def google_callback(
    code: str | None = None,
    state: str = "",
    error: str | None = None,
):
    db = get_database()
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/login?google_error={error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    if state == "auth-login":
        tokens = exchange_code_for_tokens(code, CALENDAR_SCOPES)
        google_user = await fetch_google_user_info(tokens["token"])
        email = google_user.get("email", "").lower()
        if not email:
            raise HTTPException(status_code=400, detail="Google account email not available")

        user = await db.users.find_one({"email": email})
        if user is None:
            user_doc = {
                "name": google_user.get("name") or email.split("@")[0],
                "email": email,
                "password": "",
                "phone": None,
                "is_verified": True,
                "verification_token": None,
                "verification_token_expires": None,
                "google_calendar_token": tokens,
                "google_refresh_token": tokens.get("refresh_token"),
                "created_at": datetime.now(timezone.utc),
            }
            result = await db.users.insert_one(user_doc)
            user = await db.users.find_one({"_id": result.inserted_id})
        else:
            await db.users.update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "name": google_user.get("name") or user["name"],
                        "is_verified": True,
                        "google_calendar_token": tokens,
                    },
                    "$unset": {"verification_token": "", "verification_token_expires": ""},
                },
            )
            if tokens.get("refresh_token"):
                await db.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"google_refresh_token": tokens["refresh_token"]}},
                )
            user = await db.users.find_one({"_id": user["_id"]})

        serialized_user = serialize_user(user)
        token = create_access_token(serialized_user.id, serialized_user.email)
        return RedirectResponse(build_oauth_redirect(token, serialized_user))

    if not state.startswith("user:"):
        raise HTTPException(status_code=400, detail="Missing user state")
    user_id = state.split("user:", 1)[1]
    tokens = exchange_code_for_tokens(code, CALENDAR_SCOPES)
    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"google_calendar_token": tokens}},
    )
    if tokens.get("refresh_token"):
        await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"google_refresh_token": tokens["refresh_token"]}},
        )
    return RedirectResponse(f"{FRONTEND_URL}/profile?google=connected")
