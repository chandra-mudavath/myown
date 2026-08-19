import os
import sys

# Ensure we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.auth import AuthAccount, AccountType
from app.models.staff import Staff
from app.models.admin import Admin
from app.services.auth_service import _next_account_number

def seed_data():
    db = SessionLocal()
    try:
        # Check if staff exists
        if not db.query(AuthAccount).filter_by(email="staff@urtax.com").first():
            staff_acc = AuthAccount(
                account_number=_next_account_number(db, AccountType.STAFF),
                email="staff@urtax.com",
                password_hash=hash_password("password123"),
                account_type=AccountType.STAFF,
                is_active=True,
                is_verified=True,
            )
            db.add(staff_acc)
            db.flush()
            staff = Staff(
                staff_number="STF-00000001",
                account_id=staff_acc.id,
                first_name="Priya",
                last_name="Sharma",
                job_title="Senior Tax Preparer"
            )
            db.add(staff)
            print("Seeded staff: staff@urtax.com / password123")

        # Check if admin exists
        if not db.query(AuthAccount).filter_by(email="admin@urtax.com").first():
            admin_acc = AuthAccount(
                account_number=_next_account_number(db, AccountType.ADMIN),
                email="admin@urtax.com",
                password_hash=hash_password("password123"),
                account_type=AccountType.ADMIN,
                is_active=True,
                is_verified=True,
            )
            db.add(admin_acc)
            db.flush()
            admin = Admin(
                admin_number="ADM-00000001",
                account_id=admin_acc.id,
                first_name="Marcus",
                last_name="Admin"
            )
            db.add(admin)
            print("Seeded admin: admin@urtax.com / password123")
        
        db.commit()
        print("Done seeding.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()
