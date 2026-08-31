"""
Product Analytics & Conversation Telemetry.

Captures structured metrics for every user interaction:
- Tier distribution (L1 Triage, L2 Enterprise RAG, L3 Deep Diagnostics)
- Semantic Cache Hit Rate
- System query breakdown (ERP, HRM, CRM, etc.)
- Latency (ms) and resolution outcomes
Logs structured telemetry to Cloud Logging and maintains in-memory rolling analytics for dashboard/SLA review.
"""

import os
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Any
from collections import deque

logger = logging.getLogger("it_helpdesk_telemetry")

# In-memory sliding window of recent telemetry events (max 1,000 events) for real-time stats
_METRICS_BUFFER: deque = deque(maxlen=1000)


import hashlib

# Configuration for sensitive/regulated environments (Banking, Pharma, Healthcare)
TELEMETRY_ANONYMIZE_USERS = os.getenv("TELEMETRY_ANONYMIZE_USERS", "false").lower() in ("true", "1", "yes")
TELEMETRY_INCLUDE_QUERY = os.getenv("TELEMETRY_INCLUDE_QUERY", "true").lower() in ("true", "1", "yes")


class ProductMetricsCollector:
    """Collects and aggregates business and operational metrics for the Helpdesk Agent."""

    @staticmethod
    def infer_system(query: str, tools_called: Optional[list[str]] = None) -> str:
        """Infers target enterprise system (ERP, HRM, CRM) from query or tool invocation."""
        q_upper = query.upper() if query else ""
        if "SAP" in q_upper or "ERP" in q_upper or "ME21N" in q_upper or "PO" in q_upper:
            return "ERP"
        if "HRM" in q_upper or "WORKDAY" in q_upper or "PHÉP" in q_upper or "BHXH" in q_upper:
            return "HRM"
        if "CRM" in q_upper or "SALESFORCE" in q_upper or "LEAD" in q_upper:
            return "CRM"
        if tools_called:
            for t in tools_called:
                if "rag" in t.lower():
                    return "ENTERPRISE_RAG"
                if "ticket" in t.lower():
                    return "TICKETING"
                if "log" in t.lower():
                    return "LOG_ANALYZER"
        return "GENERAL"

    @staticmethod
    def record_interaction(
        session_id: str,
        user_id: str,
        domain: str,
        query: str,
        tier_invoked: str,
        system: Optional[str] = None,
        cache_hit: bool = False,
        latency_ms: float = 0.0,
        resolution_status: str = "RESOLVED",
        tools_called: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Records a single conversation turn metric.
        Applies privacy hashing/redaction if configured, logs structured JSON to Cloud Logging,
        and buffers event in memory for quick summary dashboard view.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Privacy protection for regulated enterprises
        safe_user_id = (
            f"anon_{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:12]}"
            if TELEMETRY_ANONYMIZE_USERS and user_id
            else user_id
        )

        safe_query_snippet = (
            query[:100] if (TELEMETRY_INCLUDE_QUERY and query) else "[REDACTED_PRIVACY]"
        )

        detected_system = system or ProductMetricsCollector.infer_system(query, tools_called)

        event = {
            "timestamp": now,
            "session_id": session_id,
            "user_id": safe_user_id,
            "domain": domain,
            "query_snippet": safe_query_snippet,
            "tier_invoked": tier_invoked.upper(),
            "system": detected_system.upper(),
            "cache_hit": bool(cache_hit),
            "latency_ms": round(latency_ms, 2),
            "resolution_status": resolution_status.upper(),
            "tools_called": tools_called or [],
        }

        # 1. Append to rolling memory buffer
        _METRICS_BUFFER.append(event)

        # 2. Structured Cloud Logging Output (Source of Truth for Multi-Instance aggregation)
        try:
            logger.info("PRODUCT_METRIC: %s", json.dumps(event, ensure_ascii=False))
        except Exception:
            logger.info("PRODUCT_METRIC: %s", event)

        return event

    @staticmethod
    def get_summary_stats() -> dict[str, Any]:
        """
        Computes aggregate metrics across buffered events.
        Provides instant visibility into Cache Hit Rate, Tier Distribution, and Query Trends.
        """
        events = list(_METRICS_BUFFER)
        total_events = len(events)
        if total_events == 0:
            return {
                "total_interactions": 0,
                "cache_hit_rate_pct": 0.0,
                "tier_breakdown": {"L1": 0, "L2": 0, "L3": 0},
                "system_breakdown": {},
                "avg_latency_ms": 0.0,
                "resolution_breakdown": {},
            }

        cache_hits = sum(1 for e in events if e.get("cache_hit"))
        cache_hit_rate = round((cache_hits / total_events) * 100, 2)

        tier_counts: dict[str, int] = {}
        system_counts: dict[str, int] = {}
        resolution_counts: dict[str, int] = {}
        total_latency = 0.0

        for e in events:
            tier = e.get("tier_invoked", "L1")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

            sys_name = e.get("system", "GENERAL")
            system_counts[sys_name] = system_counts.get(sys_name, 0) + 1

            res = e.get("resolution_status", "RESOLVED")
            resolution_counts[res] = resolution_counts.get(res, 0) + 1

            total_latency += float(e.get("latency_ms", 0.0))

        avg_latency = round(total_latency / total_events, 2)

        return {
            "total_interactions": total_events,
            "cache_hit_count": cache_hits,
            "cache_hit_rate_pct": cache_hit_rate,
            "tier_breakdown": tier_counts,
            "system_breakdown": system_counts,
            "avg_latency_ms": avg_latency,
            "resolution_breakdown": resolution_counts,
        }

    @staticmethod
    def clear_buffer() -> None:
        """Clears the in-memory telemetry buffer (useful for testing)."""
        _METRICS_BUFFER.clear()
