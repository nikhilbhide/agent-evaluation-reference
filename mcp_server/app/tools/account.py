"""Account tools — lookup_account, lookup_transaction."""


def lookup_account(identifier: str) -> dict:
    """Looks up account by email or account ID."""
    accounts = {
        "user@example.com": {"account_id": "ACC-001", "name": "Jane Doe", "email": "user@example.com", "plan": "Pro", "status": "active", "member_since": "2024-01-15"},
        "ACC-001": {"account_id": "ACC-001", "name": "Jane Doe", "email": "user@example.com", "plan": "Pro", "status": "active", "member_since": "2024-01-15"},
    }
    return accounts.get(identifier, {"error": f"Account '{identifier}' not found"})


def lookup_transaction(account_id: str, limit: int = 5) -> dict:
    """Returns recent transactions for an account."""
    transactions = [
        {"id": "TXN-301", "date": "2026-03-10", "description": "Pro Plan renewal", "amount_usd": 149.99, "status": "completed"},
        {"id": "TXN-300", "date": "2026-03-10", "description": "Pro Plan renewal (duplicate)", "amount_usd": 149.99, "status": "completed"},
        {"id": "TXN-299", "date": "2026-02-10", "description": "Pro Plan renewal", "amount_usd": 149.99, "status": "completed"},
    ]
    return {"account_id": account_id, "transactions": transactions[:limit]}
