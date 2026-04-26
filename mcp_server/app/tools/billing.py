"""Billing tools — issue_refund, lookup_invoice."""
from datetime import datetime


from typing import Optional

def issue_refund(invoice_id: str, reason: str, amount: Optional[float] = None) -> dict:
    """
    Issues a refund for an invoice.
    In production this calls your billing microservice / Stripe API / etc.
    Here it returns a simulated confirmation for the reference implementation.
    """
    # Simulate looking up invoice amount if not specified
    full_amount = 149.99  # Simulated invoice total
    refund_amount = amount if amount is not None else full_amount

    return {
        "status": "refund_issued",
        "refund_id": f"REF-{invoice_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "invoice_id": invoice_id,
        "amount_refunded_usd": refund_amount,
        "reason": reason,
        "estimated_arrival_days": 3,
        "message": f"Refund of ${refund_amount:.2f} for invoice {invoice_id} has been initiated. Funds will appear in 3-5 business days."
    }


def lookup_invoice(invoice_id: str) -> dict:
    """
    Looks up invoice details by ID.
    In production this queries your billing database.
    """
    # Simulated invoice data
    invoices = {
        "INV-12345": {"invoice_id": "INV-12345", "customer": "user@example.com", "amount_usd": 149.99, "status": "paid", "date": "2026-03-10", "charges": [{"description": "Pro Plan - Monthly", "amount": 149.99}]},
        "INV-12344": {"invoice_id": "INV-12344", "customer": "user@example.com", "amount_usd": 149.99, "status": "paid", "date": "2026-02-10", "charges": [{"description": "Pro Plan - Monthly", "amount": 149.99}]},
    }
    if invoice_id not in invoices:
        return {"error": f"Invoice {invoice_id} not found", "invoice_id": invoice_id}
    return invoices[invoice_id]
