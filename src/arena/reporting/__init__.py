"""Automated daily performance & market structure Markdown report generator."""

from .daily_report import (
    DAILY_REPORT_SYSTEM_PROMPT,
    REPORTS_DIR,
    build_report_payload,
    build_smart_market_summary,
    generate_and_save_daily_report,
    render_deterministic_markdown_report,
    save_market_summary,
    validate_report_markdown,
)

__all__ = [
    "DAILY_REPORT_SYSTEM_PROMPT",
    "REPORTS_DIR",
    "build_smart_market_summary",
    "save_market_summary",
    "build_report_payload",
    "render_deterministic_markdown_report",
    "validate_report_markdown",
    "generate_and_save_daily_report",
]
