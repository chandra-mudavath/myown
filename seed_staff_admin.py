import os
import sys

# Ensure we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.auth import AuthAccount, AccountType
from app.models.staff import Staff, StaffRole
from app.models.admin import Admin
from app.services.auth_service import _next_account_number

def seed_data():
    db = SessionLocal()
    try:
        # 1. Staff 1: staff@urtax.com (INITIATOR)
        staff_1_acc = db.query(AuthAccount).filter_by(email="staff@urtax.com").first()
        if not staff_1_acc:
            staff_1_acc = AuthAccount(
                account_number=_next_account_number(db, AccountType.STAFF),
                email="staff@urtax.com",
                password_hash=hash_password("password123"),
                account_type=AccountType.STAFF,
                is_active=True,
                is_verified=True,
            )
            db.add(staff_1_acc)
            db.flush()
            staff_1 = Staff(
                staff_number="STF-00000001",
                account_id=staff_1_acc.id,
                first_name="Priya",
                last_name="Sharma",
                job_title="Senior Tax Preparer",
                role=StaffRole.INITIATOR
            )
            db.add(staff_1)
            print("Seeded staff: staff@urtax.com / password123 (INITIATOR)")
        else:
            staff_1_record = db.query(Staff).filter_by(account_id=staff_1_acc.id).first()
            if staff_1_record:
                staff_1_record.role = StaffRole.INITIATOR

        # 2. Staff 2: naik4312@urtax.com (INITIATOR)
        staff_2_acc = db.query(AuthAccount).filter_by(email="naik4312@urtax.com").first()
        if not staff_2_acc:
            staff_2_acc = AuthAccount(
                account_number=_next_account_number(db, AccountType.STAFF),
                email="naik4312@urtax.com",
                password_hash=hash_password("password123"),
                account_type=AccountType.STAFF,
                is_active=True,
                is_verified=True,
            )
            db.add(staff_2_acc)
            db.flush()
            staff_2 = Staff(
                staff_number="STF-00000002",
                account_id=staff_2_acc.id,
                first_name="Naik",
                last_name="User",
                job_title="Tax Initiator",
                role=StaffRole.INITIATOR
            )
            db.add(staff_2)
            print("Seeded staff: naik4312@urtax.com / password123 (INITIATOR)")
        else:
            staff_2_record = db.query(Staff).filter_by(account_id=staff_2_acc.id).first()
            if staff_2_record:
                staff_2_record.role = StaffRole.INITIATOR

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
