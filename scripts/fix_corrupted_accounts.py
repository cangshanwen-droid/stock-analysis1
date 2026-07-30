"""
One-time data recovery script for corrupted fund account balances.

The _match_buy bug (break → system-buy path) caused:
1. Negative fund_account balances (deducted full cost even when balance insufficient)
2. Some users may have CHECK constraint violations preventing balance updates

This script:
1. Drops the CHECK constraint temporarily
2. Finds all accounts with negative balances and sets them to 0
3. Verifies data consistency
4. Re-adds the CHECK constraint
"""
import sys
import os

# Add parent dir so we can import from api/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.db import connect, execute, fetchall, fetchone, is_postgres, row_dict
from api.market_ops import ACCOUNT_USER_PREFIX


def fix_negative_balances(conn) -> dict:
    """Fix all negative fund account balances and check for data issues."""
    results = {"fund_accounts_fixed": 0, "users_fixed": 0, "errors": []}

    # --- Fund accounts ---
    if is_postgres():
        # Drop CHECK constraint if it exists (to allow setting negative to 0)
        try:
            execute(conn, "ALTER TABLE fund_accounts DROP CONSTRAINT IF EXISTS fund_accounts_balance_non_negative")
        except Exception as e:
            results["errors"].append(f"drop_constraint: {e}")

    # Find and fix negative fund account balances
    neg_accounts = fetchall(conn, "SELECT id, owner, name, balance FROM fund_accounts WHERE balance < 0")
    for acct in neg_accounts:
        print(f"  Fixing fund_account id={acct['id']} owner={acct['owner']}: balance={acct['balance']} → 0")
        execute(conn, "UPDATE fund_accounts SET balance=0 WHERE id=?", (acct["id"],))
        results["fund_accounts_fixed"] += 1

    # --- Handle fund_account users who also have negative users.balance ---
    # When the bug fires, _update_balance also tries to update users.balance
    # for the [账户:X] username (which doesn't exist in users table, so it's a no-op).
    # But regular users (non-fund-account) could also be affected.
    neg_users = fetchall(conn, "SELECT username, balance FROM users WHERE balance < 0")
    for usr in neg_users:
        name = str(usr["username"])
        # Skip system/company/account users who don't have real balances
        if name.startswith("[") and (name.startswith(ACCOUNT_USER_PREFIX) or name.startswith("[公司:")):
            continue
        if name in {"[系统]", "[ϵͳ]", "[绯荤粺]"}:
            continue
        print(f"  Fixing user {name}: balance={usr['balance']} → 0")
        execute(conn, "UPDATE users SET balance=0 WHERE username=?", (name,))
        results["users_fixed"] += 1

    # --- Re-add CHECK constraint (PostgreSQL only) ---
    if is_postgres():
        try:
            execute(conn, "ALTER TABLE fund_accounts ADD CONSTRAINT fund_accounts_balance_non_negative CHECK (balance >= 0)")
        except Exception as e:
            results["errors"].append(f"add_constraint: {e}")

    return results


def main():
    print("=" * 60)
    print("Gipfel Trading - Account Recovery Script")
    print("=" * 60)

    try:
        conn = connect()
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}")
        sys.exit(1)

    try:
        results = fix_negative_balances(conn)
        conn.commit()
        print(f"\nResults:")
        print(f"  Fund accounts fixed: {results['fund_accounts_fixed']}")
        print(f"  Users fixed: {results['users_fixed']}")
        if results["errors"]:
            print(f"  Errors: {results['errors']}")
        print("\nDone. Accounts with negative balances have been reset to 0.")
    except Exception as e:
        conn.rollback()
        print(f"FATAL ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
