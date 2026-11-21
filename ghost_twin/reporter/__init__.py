"""Bug report generation (BUG_REPORT.md for coding agents)."""

from ghost_twin.reporter.dedupe import Bug, fingerprint, group_findings
from ghost_twin.reporter.markdown import build_report, write_report

__all__ = ["Bug", "build_report", "fingerprint", "group_findings", "write_report"]
