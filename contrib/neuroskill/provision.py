"""Provision the NeuroSkill bridge in Open Wearables: one user and one API key.

Run inside the backend container (uses OW's own services). Idempotent: reuses the user by email.
Prints only ids; the raw key goes to /out/ow_api_key (caller chmods it 600).
"""

import os
from pathlib import Path

from app.database import SessionLocal
from app.models import ApiKey, User
from app.schemas.model_crud.user_management.user import UserCreate
from app.services.api_key_service import api_key_service
from app.services.user_service import user_service

EMAIL = os.environ.get("BRIDGE_USER_EMAIL", "kvn@local.ow")
KEY_NAME = "neuroskill-bridge"

with SessionLocal() as db:
    user = db.query(User).filter(User.email == EMAIL).one_or_none()
    if user is None:
        user = user_service.create(db, UserCreate(first_name="Kevin", email=EMAIL))
    print("user_id", user.id)
    out = Path("/out/ow_api_key")
    if out.exists() and db.query(ApiKey).filter(ApiKey.name == KEY_NAME).count():
        print("api_key exists, kept")
    else:
        key, raw = api_key_service.create_api_key(db, None, KEY_NAME)
        out.write_text(raw)
        print("api_key_id", key.id)
    db.commit()
