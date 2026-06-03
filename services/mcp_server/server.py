from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP

from services.mcp_server.tools import (
    check_area_outage as check_area_outage_impl,
    check_credit_eligibility as check_credit_eligibility_impl,
    create_dispatch_ticket as create_dispatch_ticket_impl,
    get_customer_locations as get_customer_locations_impl,
    get_device_signal_status as get_device_signal_status_impl,
    get_policy_for_issue_type as get_policy_for_issue_type_impl,
    restart_gateway as restart_gateway_impl,
)


mcp = FastMCP("comcast-business-resolution-copilot")


@mcp.tool()
def get_customer_locations(account_id: str, selected_location_id: str) -> dict:
    return get_customer_locations_impl(account_id, selected_location_id)


@mcp.tool()
def check_area_outage(location_id: str) -> dict:
    return check_area_outage_impl(location_id)


@mcp.tool()
def get_device_signal_status(location_id: str) -> dict:
    return get_device_signal_status_impl(location_id)


@mcp.tool()
def restart_gateway(location_id: str, approved_by_support_lead: bool) -> dict:
    return restart_gateway_impl(location_id, approved_by_support_lead)


@mcp.tool()
def check_credit_eligibility(account_id: str, location_id: str) -> dict:
    return check_credit_eligibility_impl(account_id, location_id)


@mcp.tool()
def create_dispatch_ticket(
    account_id: str,
    location_id: str,
    reason: str,
    approved_by_support_lead: bool,
) -> dict:
    return create_dispatch_ticket_impl(account_id, location_id, reason, approved_by_support_lead)


@mcp.tool()
def get_policy_for_issue_type(issue_type: str) -> dict:
    return get_policy_for_issue_type_impl(issue_type)


if __name__ == "__main__":
    mcp.run()
