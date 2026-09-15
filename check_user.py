import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import SessionLocal
from app.models.auth import AuthAccount
from app.models.client import Client

def check_client():
    db = SessionLocal()
    acc = db.query(AuthAccount).filter(AuthAccount.email == "naik4312@gmail.com").first()
    if not acc:
        print("Account for naik4312@gmail.com not found!")
        return
    client = db.query(Client).filter(Client.account_id == acc.id).first()
    if not client:
        print(f"AuthAccount found (ID: {acc.id}) but no Client record associated!")
        return
    print(f"Client found: ID={client.id}, Name={client.first_name} {client.last_name}, profile_picture={client.profile_picture}")

if __name__ == "__main__":
    check_client()
