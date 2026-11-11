"""Print a one-time admin sign-up link for a site that has no admin yet (first deploy).

Admins can only sign up from the link an admin generates in Settings, so a brand-new site has no way in.
Run this once on the server:

    python create_first_admin_link.py            # prints the link; refuses if an admin already exists
    python create_first_admin_link.py --revoke   # the link stops working (run after the first admin signs up)

The first admin then signs up from the link with a company address (@covrenttax.com) and confirms the code
emailed to it, as any admin sign-up does. Later admin links come from Admin → Settings as usual.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings
from app.core.database import SessionLocal
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.system import AppSetting, AuditLog
from app.platform.services import admin_signup
import app.db_models  # noqa: F401 — registers every model


def main() -> int:
    db = SessionLocal()
    try:
        if "--revoke" in sys.argv:
            row = db.get(AppSetting, admin_signup.KEY)
            if row:
                row.value = None
                db.add(AuditLog(actor_account_id=None, action="admin_signup_link_revoked", entity_type="app_setting",
                                entity_id=admin_signup.KEY, new_values={"revoked": True}))
                db.commit()
            print("Admin sign-up link revoked. Generate a new one from Admin > Settings when needed.")
            return 0

        if db.query(AuthAccount).filter(AuthAccount.account_type == AccountType.ADMIN).first():
            print("An admin account already exists. Generate admin links from Admin > Settings instead.")
            return 1

        key = admin_signup.generate(db, None)
        base = settings.PUBLIC_BASE_URL.rstrip("/") or "https://<your-site>"
        print(f"First admin sign-up link (works until revoked or replaced):\n\n  {base}{admin_signup.PATH}{key}\n")
        print("Sign up with a @covrenttax.com address, then run:  python create_first_admin_link.py --revoke")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
