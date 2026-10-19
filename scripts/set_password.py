#!/usr/bin/env python3
"""
Set or reset a login password for a tenant, reseller, or admin user.

Usage:
    python scripts/set_password.py tenant <tenant_id>
    python scripts/set_password.py reseller <reseller_id>
    python scripts/set_password.py admin <email>

The password is read interactively (never from argv, so it doesn't land in shell history).
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from app.db.session import sync_engine  # noqa: E402
from app.core.auth import hash_password, validate_new_password  # noqa: E402

TABLES = {
    "tenant": ("tenants", "tenant_id"),
    "reseller": ("resellers", "reseller_id"),
    "admin": ("admin_users", "email"),
}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in TABLES:
        print(__doc__)
        return 2
    table, id_col = TABLES[sys.argv[1]]
    account_id = sys.argv[2]

    pwd = getpass.getpass("New password: ")
    if pwd != getpass.getpass("Confirm password: "):
        print("Passwords do not match.")
        return 1
    try:
        pwd = validate_new_password(pwd)
    except HTTPException as e:
        print(e.detail)
        return 1

    with sync_engine.connect() as conn:
        res = conn.execute(
            text(f"UPDATE {table} SET password_hash = :pwd WHERE {id_col} = :id"),
            {"pwd": hash_password(pwd), "id": account_id}
        )
        if res.rowcount == 0:
            print(f"No {sys.argv[1]} found with {id_col} = {account_id!r}.")
            return 1
        conn.commit()
    print(f"Password updated for {sys.argv[1]} {account_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
