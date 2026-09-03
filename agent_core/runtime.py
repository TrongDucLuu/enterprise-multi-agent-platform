"""
Runtime execution context, Gemini models, caching and telemetry callbacks for agent_core.
Extracted into a standalone module to break circular dependencies between agent definitions and dynamic builders.
"""
import os
import re
import time
import logging
from typing import Optional
from contextvars import ContextVar
from google.adk.models import Gemini, LlmRequest, LlmResponse
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from agent_core.app_utils.env import (
    init_environment,
    get_model_names_for_environment,
)
from agent_core.app_utils.semantic_cache import (
    get_semantic_cache,
    resolve_caller_clearance,
    get_max_source_clearance,
    reset_max_source_clearance,
)
from agent_core.app_utils.sso_auth import current_sso_user
from agent_core.app_utils.telemetry import ProductMetricsCollector

logger = logging.getLogger("agent_core.runtime")

# Initialize environment and fetch model configurations
PROJECT_ID, MODEL_LOC, SERVICE_LOC, SECRETS = init_environment()
FAST_MODEL_NAME, REASONING_MODEL_NAME = get_model_names_for_environment()

# 1. Standard fast model for Triage, L1 and L2 agents
fast_model = Gemini(
    model=FAST_MODEL_NAME,
    vertexai=True,
    project=PROJECT_ID,
    location=MODEL_LOC,
    retry_options=types.HttpRetryOptions(attempts=3),
)

# 2. High-reasoning pro model for L3 deep diagnostics & compliance analysis
# Configured with attempts=2 to prevent runaway token costs on expensive reasoning retries
high_reasoning_model = Gemini(
    model=REASONING_MODEL_NAME,
    vertexai=True,
    project=PROJECT_ID,
    location=MODEL_LOC,
    retry_options=types.HttpRetryOptions(attempts=2),
)

_current_l3_soft_warning: ContextVar[Optional[str]] = ContextVar("_current_l3_soft_warning", default=None)
_turn_start_time: ContextVar[Optional[float]] = ContextVar("_turn_start_time", default=None)


async def save_session_to_memory_callback(*args, **kwargs) -> None:
    """
    Defensively persists user session context and resolution history to Vertex AI Memory Bank.
    """
    ctx = kwargs.get("callback_context") or (args[0] if args else None)
    if ctx and hasattr(ctx, "_invocation_context") and ctx._invocation_context.memory_service:
        await ctx._invocation_context.memory_service.add_session_to_memory(
            ctx._invocation_context.session
        )


async def semantic_cache_before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """
    Checks L3 rate limits and semantic cache for matching questions before calling Gemini.
    - For L3 Deep Diagnostics: Enforces strict 10 req/min quota to prevent runaway Gemini Pro costs.
    - If cache hit: Returns LlmResponse immediately to short-circuit the model call and save 100% tokens.
    """
    start_t = time.perf_counter()
    _turn_start_time.set(start_t)
    reset_max_source_clearance()

    inv_ctx = getattr(callback_context, "_invocation_context", None)
    agent_name = inv_ctx.agent.name if inv_ctx and hasattr(inv_ctx, "agent") else ""

    user = current_sso_user.get()
    user_id = user.user_id if user else None

    # 1. Protect expensive L3 Pro model with per-user rate limiting and monthly token budget
    if agent_name == "l3_deep_diagnostics_agent":
        from agent_core.app_utils.token_budget import is_budget_exceeded
        if is_budget_exceeded():
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(
                        text="⚠️ [Deployment Token Budget Exceeded] Hệ thống đã đạt hạn mức token hàng tháng (MONTHLY_TOKEN_BUDGET). "
                             "Chế độ Degrade Mode đang kích hoạt: Tính năng phân tích sâu L3 tạm thời bị khóa. "
                             "Vui lòng sử dụng L1 FAQ hoặc L2 tra cứu tri thức."
                    )]
                ),
                custom_metadata={"rate_limited": True, "tier": "L3", "degrade_mode": True}
            )

        from agent_core.app_utils.rate_limiter import check_l3_rate_limit_with_warning
        allowed, rem, retry_after, is_soft_warning, warn_msg = check_l3_rate_limit_with_warning(user_id)
        if not allowed:
            l3_limit = os.getenv("L3_RATE_LIMIT_PER_MINUTE", "10")
            msg = warn_msg or f"⚠️ [L3 Rate Limit Exceeded] Hạn mức gọi mô hình phân tích sâu L3 ({REASONING_MODEL_NAME}) của bạn đã vượt quá giới hạn ({l3_limit} lượt/phút). Vui lòng thử lại sau {retry_after}s."
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=msg)]
                ),
                custom_metadata={"rate_limited": True, "tier": "L3"}
            )
        if is_soft_warning and warn_msg:
            _current_l3_soft_warning.set(warn_msg)
            logger.info("User %s soft quota reached: %s", user_id, warn_msg)
        else:
            _current_l3_soft_warning.set(None)

        # L3 Cache Bypass: Root-cause analysis, deep system diagnostics, and SLA/compliance require live reasoning.
        # Bypass semantic cache completely for L3 to eliminate high-risk false cache collisions across distinct incidents.
        return None

    # 2. Check semantic cache
    if not os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes"):
        return None

    if not llm_request.contents:
        return None

    last_content = llm_request.contents[-1]
    if getattr(last_content, "role", None) not in ("user", None, ""):
        return None

    parts = getattr(last_content, "parts", []) or []
    query_parts = [p.text for p in parts if hasattr(p, "text") and p.text]
    query_text = " ".join(query_parts).strip()
    if not query_text or len(query_text) < 3:
        return None

    cache = get_semantic_cache()
    cached = cache.get(query=query_text, user_id=user_id, tier=agent_name)
    if cached:
        # Record cache hit in product metrics telemetry with actual cache lookup latency
        hit_latency_ms = round((time.perf_counter() - start_t) * 1000.0, 2)
        try:
            session_id = getattr(getattr(inv_ctx, "session", None), "id", "sess_unknown")
            user_inst = current_sso_user.get()
            domain = user_inst.hosted_domain or (user_inst.email.split("@")[-1] if user_inst and user_inst.email else "unknown") if user_inst else "unknown"
            ProductMetricsCollector.record_interaction(
                session_id=session_id,
                user_id=user_id or "anonymous",
                domain=domain,
                query=query_text,
                tier_invoked=agent_name or "L1",
                cache_hit=True,
                latency_ms=hit_latency_ms,
                resolution_status="RESOLVED_CACHE"
            )
        except Exception as e:
            logger.debug("Failed to record cache hit telemetry: %s", e)

        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=cached["response"])]
            ),
            custom_metadata={"cached": True, "cached_query": cached.get("cached_query", query_text)}
        )
    return None


def _is_safe_public_faq(query: str, agent_name: str, tools_called: list, is_first_turn: bool = True) -> bool:
    """
    Determines if a query and response are completely safe to cache with is_public=True.
    Criteria:
    1. First turn only (is_first_turn == True) in multi-turn conversation.
    2. Executed by L1 Self-Service agent (agent_name == "l1_selfservice_agent").
    3. Zero business tools called (pure informational FAQ/guidance).
    4. No personal keywords or private state (password reset, account unlock, personal ticket IDs, payroll, salary, PII).
    5. Matches general enterprise IT FAQ topics (Wi-Fi, printer setup, VPN guides, standard software, office policies).
    """
    if not is_first_turn:
        return False
    if agent_name != "l1_selfservice_agent":
        return False
    if tools_called and len(tools_called) > 0:
        return False

    q_lower = query.lower()

    # Strictly forbid caching personal or account-sensitive actions with word-boundary matching
    private_triggers = [
        "mật khẩu", "password", "reset", "đổi pass", "quên pass",
        "mở khóa", "unlock", "tài khoản của tôi", "my account",
        "ticket", "lương", "payroll", "bảng lương", "bhxh",
        "sđt", "phone", "email cá nhân", "token", "otp", "2fa", "mfa",
        "cccd", "cmnd", "hóa đơn", "po", "purchase order"
    ]
    # Word boundary matching so that "po" does not match "support", "powerpoint", "portal", "policy"
    private_pattern = r"(?:\b|_)(?:" + "|".join(re.escape(k) for k in private_triggers) + r")(?:\b|_)"
    if re.search(private_pattern, q_lower, flags=re.IGNORECASE):
        return False

    # Safe general corporate IT topics
    safe_faq_patterns = [
        "wifi", "wi-fi", "mạng", "internet",
        "máy in", "printer", "in ấn",
        "cài đặt vpn", "hướng dẫn vpn", "vpn văn phòng", "vpn",
        "phần mềm tiêu chuẩn", "quy định it", "chính sách bảo mật",
        "giờ làm việc", "hotline it", "thời gian hỗ trợ",
        "office 365", "chrome", "slack", "zoom", "powerpoint", "support"
    ]
    safe_pattern = r"(?:\b|_)(?:" + "|".join(re.escape(p) for p in safe_faq_patterns) + r")(?:\b|_)"
    return bool(re.search(safe_pattern, q_lower, flags=re.IGNORECASE))


async def semantic_cache_after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """
    1. Records live conversational telemetry (tier, system, latency, resolution status) to ProductMetricsCollector.
    2. Persists successful conversational text responses into Semantic Cache for subsequent queries.
    Enforces strict Fail-Closed multi-tenant isolation: Never cache unauthenticated/missing user context.
    """
    if not llm_response:
        return None

    inv_ctx = getattr(callback_context, "_invocation_context", None)
    agent_name = inv_ctx.agent.name if inv_ctx and hasattr(inv_ctx, "agent") else "root"
    session_id = getattr(getattr(inv_ctx, "session", None), "id", "sess_unknown")
    user = current_sso_user.get()
    user_id = user.user_id if user else "anonymous"
    domain = user.hosted_domain or (user.email.split("@")[-1] if user and user.email else "unknown") if user else "unknown"

    # Extract user question from invocation events or session
    user_query = ""
    if inv_ctx:
        events = inv_ctx._get_events(current_invocation=True) if hasattr(inv_ctx, "_get_events") else []
        for ev in reversed(events):
            if getattr(ev, "author", "") == "user" and getattr(ev, "content", None) and getattr(ev.content, "parts", None):
                for p in ev.content.parts:
                    if hasattr(p, "text") and p.text:
                        user_query = p.text
                        break
                if user_query:
                    break

        if not user_query and getattr(inv_ctx, "session", None) and hasattr(inv_ctx.session, "events"):
            for ev in reversed(inv_ctx.session.events):
                if getattr(ev, "author", "") == "user" and getattr(ev, "content", None) and getattr(ev.content, "parts", None):
                    for p in ev.content.parts:
                        if hasattr(p, "text") and p.text:
                            user_query = p.text
                            break
                    if user_query:
                        break

    # Extract tools called if any
    tools_called = []
    if llm_response.content and getattr(llm_response.content, "parts", None):
        for p in llm_response.content.parts:
            if hasattr(p, "function_call") and p.function_call and hasattr(p.function_call, "name"):
                tools_called.append(p.function_call.name)

    # 1. Always record telemetry for model interactions (unless already recorded as cache hit or rate-limited)
    is_cached = bool(llm_response.custom_metadata and llm_response.custom_metadata.get("cached"))
    is_rate_limited = bool(llm_response.custom_metadata and llm_response.custom_metadata.get("rate_limited"))

    if not is_cached and not is_rate_limited:
        try:
            res_status = "INVOKED_TOOLS" if tools_called else "RESOLVED_MODEL"
            if getattr(llm_response, "error_code", None):
                res_status = "ERROR"

            start_t = _turn_start_time.get()
            measured_latency_ms = round((time.perf_counter() - start_t) * 1000.0, 2) if start_t is not None else 0.0

            ProductMetricsCollector.record_interaction(
                session_id=session_id,
                user_id=user_id,
                domain=domain,
                query=user_query,
                tier_invoked=agent_name,
                cache_hit=False,
                latency_ms=measured_latency_ms,
                resolution_status=res_status,
                tools_called=tools_called,
            )
        except Exception as e:
            logger.debug("Failed to record model interaction telemetry: %s", e)

        # Record token usage for cluster-wide monthly budget tracking
        try:
            from agent_core.app_utils.token_budget import record_token_usage
            usage = getattr(llm_response, "usage_metadata", None)
            token_count = 0
            if usage:
                token_count = getattr(usage, "total_token_count", 0) or (getattr(usage, "prompt_token_count", 0) + getattr(usage, "candidates_token_count", 0))
            if not token_count and llm_response.content and getattr(llm_response.content, "parts", None):
                total_chars = sum(len(p.text) for p in llm_response.content.parts if hasattr(p, "text") and p.text)
                total_chars += len(user_query or "")
                token_count = max(1, total_chars // 4)
            if token_count > 0:
                record_token_usage(token_count)
        except Exception as e:
            logger.debug("Failed to record token usage: %s", e)

    # 2. Check if there is an active L3 soft warning to deliver to the user
    soft_warn = _current_l3_soft_warning.get()
    modified_response = None
    if soft_warn:
        _current_l3_soft_warning.set(None)
        if llm_response.content and getattr(llm_response.content, "parts", None):
            new_parts = []
            warn_inserted = False
            for p in llm_response.content.parts:
                if hasattr(p, "text") and p.text and not warn_inserted:
                    new_parts.append(types.Part.from_text(text=f"{soft_warn}\n\n{p.text}"))
                    warn_inserted = True
                else:
                    new_parts.append(p)
            if not warn_inserted:
                new_parts.insert(0, types.Part.from_text(text=soft_warn))

            meta = dict(llm_response.custom_metadata or {})
            meta["soft_warning"] = soft_warn

            modified_response = LlmResponse(
                content=types.Content(role=llm_response.content.role, parts=new_parts),
                custom_metadata=meta
            )

    # 3. Persist to Semantic Cache if eligible (L1 / L2 only; L3 is strictly bypassed)
    if agent_name == "l3_deep_diagnostics_agent":
        return modified_response

    if not os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes"):
        return modified_response

    if is_cached or is_rate_limited or getattr(llm_response, "error_code", None):
        return modified_response

    if tools_called or not llm_response.content or not getattr(llm_response.content, "parts", None):
        return modified_response

    response_parts = [p.text for p in llm_response.content.parts if hasattr(p, "text") and p.text]
    response_text = " ".join(response_parts).strip()
    if not response_text:
        return modified_response

    if user_query and len(user_query) >= 3:
        # Fail-Closed: If user context is missing (unauthenticated or lost contextvar), do NOT cache
        if not user or not user.user_id:
            return modified_response

        # Check if session is on first turn (first user query in session)
        is_first_turn = True
        if inv_ctx and getattr(inv_ctx, "session", None) and hasattr(inv_ctx.session, "events"):
            user_events_count = sum(1 for ev in inv_ctx.session.events if getattr(ev, "author", "") == "user")
            if user_events_count > 1:
                is_first_turn = False

        # Classify if public FAQ or user-specific private query (only turn 1 can be public)
        is_safe_public = _is_safe_public_faq(user_query, agent_name, tools_called, is_first_turn=is_first_turn)
        max_source = get_max_source_clearance()
        user_clearance = resolve_caller_clearance(user_id=user.user_id if user else None)

        if max_source is not None and max_source > 0:
            is_safe_public = False
            eff_clearance = max(max_source, user_clearance)
        else:
            eff_clearance = 0 if is_safe_public else user_clearance

        cache = get_semantic_cache()
        cache.set(
            query=user_query,
            response=response_text,
            user_id=None if is_safe_public else user.user_id,
            is_public=is_safe_public,
            tier=agent_name,
            clearance_level=eff_clearance,
        )
        reset_max_source_clearance()

    return modified_response
