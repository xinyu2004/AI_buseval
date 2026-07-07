"""GMSL independent package: link bandwidth calculator + report."""
from .calculator import calculate_link, GmslLinkResult, GmslReport  # noqa: F401
from .report import render_gmsl_terminal, build_gmsl_structured  # noqa: F401
