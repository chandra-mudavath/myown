import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import SessionLocal
from app.modules.client.models.client import Client
from app.modules.staff.models.staff import Staff
from app.modules.admin.models.admin import Admin

def set_default_avatars():
    db = SessionLocal()
    source_ceo = Path("app/public/static/img/aboutus/CEO.jpg")

    if not source_ceo.exists():
        print("CEO.jpg source image not found at app/public/static/img/aboutus/CEO.jpg")
        return

    print("Updating Clients...")
    clients = db.query(Client).all()
    for c in clients:
        target_dir = Path("storage") / str(c.id) / "avatar"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "CEO.jpg"
        shutil.copy(source_ceo, target_file)
        c.profile_picture = f"/storage/{c.id}/avatar/CEO.jpg"

    print("Updating Staff...")
    staff_members = db.query(Staff).all()
    for s in staff_members:
        target_dir = Path("storage") / str(s.id) / "avatar"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "CEO.jpg"
        shutil.copy(source_ceo, target_file)
        s.profile_picture = f"/storage/{s.id}/avatar/CEO.jpg"

    print("Updating Admins...")
    admins = db.query(Admin).all()
    for a in admins:
        target_dir = Path("storage") / str(a.id) / "avatar"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "CEO.jpg"
        shutil.copy(source_ceo, target_file)
        a.profile_picture = f"/storage/{a.id}/avatar/CEO.jpg"

    db.commit()
    db.close()
    print("Successfully set CEO.jpg profile picture for all clients, staff, and admins!")

if __name__ == "__main__":
    set_default_avatars()
