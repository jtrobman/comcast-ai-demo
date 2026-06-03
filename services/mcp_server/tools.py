from __future__ import annotations

from typing import Any, Dict, List


LOCATIONS = {
    "loc_walnut": {
        "id": "loc_walnut",
        "label": "Walnut Demo Cafe",
        "address": "Demo Walnut location, Philadelphia, PA",
        "service_tier": "Business Internet 500",
    },
    "loc_queen_village": {
        "id": "loc_queen_village",
        "label": "Queen Village Demo Cafe",
        "address": "Demo Queen Village location, Philadelphia, PA",
        "service_tier": "Business Internet 300",
    },
}


def get_customer_locations(account_id: str, selected_location_id: str) -> Dict[str, Any]:
    return {
        "account_id": account_id,
        "locations": list(LOCATIONS.values()),
        "selected_location": LOCATIONS[selected_location_id],
    }


def check_area_outage(location_id: str) -> Dict[str, Any]:
    if location_id == "loc_queen_village":
        return {
            "location_id": location_id,
            "status": "confirmed",
            "incident_id": "OUT-DEMO-88421",
            "estimated_restore_window": "2:30-4:00 PM ET",
        }
    return {
        "location_id": location_id,
        "status": "clear",
        "incident_id": None,
        "estimated_restore_window": None,
    }


def get_device_signal_status(location_id: str) -> Dict[str, Any]:
    if location_id == "loc_walnut":
        return {
            "location_id": location_id,
            "gateway_id": "GW-DEMO-WAL-2190",
            "online": True,
            "signal_state": "degraded",
            "downstream_snr_db": 27.4,
            "upstream_power_dbmv": 51.1,
            "drops_last_24h": 18,
        }
    return {
        "location_id": location_id,
        "gateway_id": "GW-DEMO-QV-1138",
        "online": False,
        "signal_state": "offline",
        "downstream_snr_db": None,
        "upstream_power_dbmv": None,
        "drops_last_24h": 3,
    }


def restart_gateway(location_id: str, approved_by_support_lead: bool) -> Dict[str, Any]:
    if not approved_by_support_lead:
        return {
            "location_id": location_id,
            "action": "restart_gateway",
            "status": "not_performed",
            "reason": "Support lead approval is required before restarting business-critical connectivity.",
        }
    return {
        "location_id": location_id,
        "action": "restart_gateway",
        "status": "queued",
        "reason": "Restart queued after support lead approval.",
    }


def check_credit_eligibility(account_id: str, location_id: str) -> Dict[str, Any]:
    return {
        "account_id": account_id,
        "location_id": location_id,
        "eligible": False,
        "reason": "Credit eligibility requires confirmed service-impact duration and billing review.",
        "support_lead_review_required": True,
    }


def create_dispatch_ticket(
    account_id: str,
    location_id: str,
    reason: str,
    approved_by_support_lead: bool,
) -> Dict[str, Any]:
    if not approved_by_support_lead:
        return {
            "status": "approval_required",
            "ticket_id": None,
            "reason": "Dispatch candidate prepared, but not scheduled until a representative approves.",
        }
    return {
        "status": "created",
        "ticket_id": "DSP-DEMO-77820",
        "reason": reason,
        "account_id": account_id,
        "location_id": location_id,
    }


def get_policy_for_issue_type(issue_type: str) -> Dict[str, Any]:
    policies: Dict[str, List[str]] = {
        "degraded_gateway_signal": [
            "Confirm no active area outage.",
            "Review signal telemetry.",
            "Require support lead approval before dispatch scheduling.",
        ],
        "billing_credit_request": [
            "Do not promise credit without eligibility confirmation.",
            "Route billing-impacting actions to support lead review.",
        ],
    }
    return {"issue_type": issue_type, "policy_steps": policies.get(issue_type, [])}
