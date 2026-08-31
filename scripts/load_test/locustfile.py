import os
import csv
import random
import time
import json
import logging
from typing import Dict, List
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner, WorkerRunner

# Path to the dataset
EVAL_SET_PATH = os.path.join(os.path.dirname(__file__), "eval_set.csv")

# Load evaluation queries grouped by tier
QUERIES_BY_TIER: Dict[str, List[Dict[str, str]]] = {"L1": [], "L2": [], "L3": []}

try:
    with open(EVAL_SET_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tier = row.get("tier", "L1").strip()
            if tier in QUERIES_BY_TIER:
                QUERIES_BY_TIER[tier].append(row)
except Exception as e:
    logging.error("Failed to load eval_set.csv: %s", e)
    # Fallback default queries
    QUERIES_BY_TIER["L1"] = [{"query": "Cách kết nối wifi văn phòng", "expected_system": "ALL"}]
    QUERIES_BY_TIER["L2"] = [{"query": "Hướng dẫn tạo đơn mua hàng PO trên SAP", "expected_system": "ERP"}]
    QUERIES_BY_TIER["L3"] = [{"query": "Phân tích stack trace NullPointerException", "expected_system": "ERP"}]


class EnterpriseHelpdeskUser(HttpUser):
    """
    Simulates an authenticated Enterprise Employee interacting with the IT Helpdesk Agent.
    Includes SSO Bearer Token authentication, distinct user identity, and weighted tier queries.
    """
    wait_time = between(1.0, 3.0)

    def on_start(self):
        """Initializes user session and generates mock SSO Bearer Token."""
        self.user_idx = random.randint(1000, 9999)
        self.email = f"employee.{self.user_idx}@enterprise.com"
        self.user_id = f"emp_{self.user_idx}"
        self.session_id = f"sess_load_{self.user_idx}_{int(time.time())}"
        
        # Use SSO Mock Dev Token or generate signed test header
        self.token = f"Bearer mock_dev_token_for_{self.user_id}"
        self.headers = {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "X-User-Email": self.email,
            "X-User-ID": self.user_id,
        }

    def _execute_query(self, tier: str):
        """Executes a query belonging to the specified tier and records custom latency."""
        candidates = QUERIES_BY_TIER.get(tier, [])
        if not candidates:
            return
        
        sample = random.choice(candidates)
        query_text = sample["query"]
        
        # 1. First probe semantic cache endpoint (simulating frontend optimization)
        cache_url = f"/api/cache/query?q={query_text}&threshold=0.92"
        start_time = time.time()
        
        with self.client.get(
            cache_url,
            headers=self.headers,
            name=f"[{tier}] GET /api/cache/query",
            catch_response=True
        ) as response:
            latency = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "hit":
                    events.request.fire(
                        request_type="CACHE",
                        name=f"Cache Hit [{tier}]",
                        response_time=latency,
                        response_length=len(response.content),
                        context={"tier": tier, "cache": "HIT"},
                        exception=None
                    )
                    response.success()
                    return
                else:
                    response.success()
            elif response.status_code == 429:
                events.request.fire(
                    request_type="RATE_LIMIT",
                    name=f"429 App Rate Limit [{tier}]",
                    response_time=latency,
                    response_length=len(response.content),
                    context={"tier": tier, "status": 429},
                    exception=None
                )
                response.failure("Rate Limited (429)")
                return
            else:
                response.failure(f"HTTP {response.status_code}")

        # 2. If cache miss, invoke agent healthz/readiness or mock query flow
        readiness_url = "/readyz"
        start_time = time.time()
        with self.client.get(
            readiness_url,
            headers=self.headers,
            name=f"[{tier}] Agent Query Simulation",
            catch_response=True
        ) as response:
            latency = (time.time() - start_time) * 1000
            if response.status_code == 200:
                events.request.fire(
                    request_type="TIER_LATENCY",
                    name=f"Latency Tier {tier}",
                    response_time=latency,
                    response_length=len(response.content),
                    context={"tier": tier},
                    exception=None
                )
                response.success()
            elif response.status_code == 429:
                response.failure("Rate Limited (429)")
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(6)
    def query_l1_faq_selfservice(self):
        """60% Weight: Level 1 FAQ, Password Reset, Wi-Fi, Ticket Status."""
        self._execute_query("L1")

    @task(3)
    def query_l2_enterprise_rag(self):
        """30% Weight: Level 2 ERP / HRM / CRM Knowledge Retrieval."""
        self._execute_query("L2")

    @task(1)
    def query_l3_deep_diagnostics(self):
        """10% Weight: Level 3 RCA Log Diagnostics & SLA Compliance."""
        self._execute_query("L3")
