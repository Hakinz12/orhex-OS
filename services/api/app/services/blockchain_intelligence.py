"""Deterministic, explainable read-only Sui wallet intelligence."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List


def analyze_wallet(address: str, network: str, balances: List[Dict[str, Any]],
                   activity: List[Dict[str, Any]], checkpoint: int) -> Dict[str, Any]:
    holdings = []
    for item in balances:
        if not isinstance(item, dict):
            continue
        amount = item.get("amount")
        if amount is None:
            continue
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if not value.is_finite() or value < 0:
            continue
        coin_type = item.get("coin_type")
        if not isinstance(coin_type, str) or not coin_type:
            continue
        holdings.append({"coin_type": coin_type, "symbol": item.get("symbol"),
                         "amount": str(value), "decimals": item.get("decimals")})
    known = holdings
    success = sum(1 for tx in activity if isinstance(tx, dict) and tx.get("status") in {"success", "successful"})
    failed = sum(1 for tx in activity if isinstance(tx, dict) and tx.get("status") in {"failure", "failed"})
    unknown = len(activity) - success - failed
    insights = []
    if failed:
        insights.append({"severity": "attention", "code": "failed_transactions",
                         "title": "Failed transactions detected",
                         "detail": f"{failed} of the {len(activity)} recent transactions reported failure. Review them before repeating similar actions."})
    if not activity:
        insights.append({"severity": "info", "code": "limited_activity_data",
                         "title": "No recent sent transactions returned",
                         "detail": "The provider returned no recent sent transactions for this address; this does not prove the wallet has never transacted."})
    return {
        "wallet_address": address, "network": network, "fetched_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint": checkpoint,
        "snapshot_consistency": "independent_provider_reads",
        "portfolio": {"known_token_count": len(holdings), "holdings": known},
        "activity": {"sample_size": len(activity), "successful": success, "failed": failed,
                     "unknown_status": unknown, "transactions": activity},
        "insights": insights,
        "limitations": ["Read-only public chain data.", "Token amounts are not USD valuations.",
                        "Amounts use token-specific units and are not summed or compared across token types.",
                        "No portfolio concentration is calculated without comparable valuations.",
                        "Activity covers recent sent transactions returned by the provider, not a complete wallet history.",
                        "Heuristic indicators are informational and are not fraud, investment, or security determinations."],
    }
