"""Deterministic, explainable read-only Sui wallet intelligence."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional


def analyze_wallet(
    address: str,
    network: str,
    balances: Optional[List[Dict[str, Any]]],
    activity: Optional[List[Dict[str, Any]]],
    checkpoint: Optional[int],
    provider_errors: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    holdings = []
    for item in balances or []:
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
    success = sum(1 for tx in activity or [] if isinstance(tx, dict) and tx.get("status") in {"success", "successful"})
    failed = sum(1 for tx in activity or [] if isinstance(tx, dict) and tx.get("status") in {"failure", "failed"})
    unknown = len(activity or []) - success - failed
    insights = []
    if failed:
        insights.append({"severity": "attention", "code": "failed_transactions",
                         "title": "Failed transactions detected",
                         "detail": f"{failed} of the {len(activity)} recent transactions reported failure. Review them before repeating similar actions."})
    if activity is not None and not activity:
        insights.append({"severity": "info", "code": "limited_activity_data",
                         "title": "No recent sent transactions returned",
                         "detail": "The provider returned no recent sent transactions for this address; this does not prove the wallet has never transacted."})
    errors = provider_errors or []
    unavailable_sections = [item["section"] for item in errors]
    portfolio = (
        {"known_token_count": len(holdings), "holdings": holdings}
        if balances is not None
        else None
    )
    activity_data = (
        {
            "sample_size": len(activity),
            "successful": success,
            "failed": failed,
            "unknown_status": unknown,
            "transactions": activity,
        }
        if activity is not None
        else None
    )
    return {
        "wallet_address": address, "network": network, "fetched_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint": checkpoint,
        "snapshot_consistency": "independent_provider_reads",
        "data_status": "unavailable" if len(unavailable_sections) == 3 else "partial" if unavailable_sections else "complete",
        "unavailable_sections": unavailable_sections,
        "provider_errors": errors,
        "portfolio": portfolio,
        "activity": activity_data,
        "insights": insights,
        "limitations": ["Read-only public chain data.", "Token amounts are not USD valuations.",
                        "Amounts use token-specific units and are not summed or compared across token types.",
                        "No portfolio concentration is calculated without comparable valuations.",
                        "Activity covers recent sent transactions returned by the provider, not a complete wallet history.",
                        "Heuristic indicators are informational and are not fraud, investment, or security determinations."],
    }
