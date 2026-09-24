# Auth System Setup

This directory contains the authentication system for the application.

## Setup

1. Add the following environment variables to your `.env` file:

```
# Session settings
SESSION_COOKIE_DOMAIN=localhost  # Optional, only needed for production
SECURE_COOKIES=false  # Set to true in production
```

2. Run migrations to create the auth tables:

```bash
python -m app.scripts.run_migrations
```

## How it Works

1. The auth system uses cookies and Bearer tokens for authentication
2. Sign-in is by email: a 6-digit verification code is emailed and exchanged for a session
3. User sessions are stored in the database for security

## Protecting Routes

Use the dependencies from `app.auth.dependencies` to protect your routes:

```python
from fastapi import Depends
from app.auth.dependencies import get_required_user
from app.schemas.user import CurrentUser

@router.get("/protected-route")
async def protected_route(current_user: CurrentUser = Depends(get_required_user)):
    # Only authenticated users can access this
    return {"user": current_user}
```

## Frontend Integration

1. The frontend posts the email to `/api/auth/email/signin` to send a code
2. It then posts the code to `/api/auth/email/verify`, which sets the session cookie
3. After successful authentication, the user is redirected to `/auth/callback` on the frontend
4. Use `/api/me` endpoint to retrieve current user information

## Logging Out

Use the `/api/logout` endpoint to log out users. Set `all_devices=true` query parameter to log out from all devices.
