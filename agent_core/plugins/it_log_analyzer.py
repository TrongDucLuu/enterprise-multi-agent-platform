"""
IT Log Analyzer plugin for system root cause analysis (RCA).
"""
from agent_core.tools.log_analyzer import (
    analyze_system_logs_for_rca,
)

# Tool registry aliasing
analyze_log_rca = analyze_system_logs_for_rca
