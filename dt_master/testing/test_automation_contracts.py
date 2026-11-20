"""Expose repository-level automation contracts to Frappe's test discovery.

The full suite remains under the app's top-level tests/ directory because it
exercises shell scripts and fixtures there. Bench imports only the dt_master
Python package, so this module loads that suite into an importable package
module without duplicating the test definitions.
"""

from pathlib import Path

_suite_path = Path(__file__).resolve().parents[2] / "tests" / "test_automation_contracts.py"
if not _suite_path.is_file():
    raise ImportError(f"Automation contract suite not found: {_suite_path}")

# The suite derives ROOT from __file__, expecting its own location under
# apps/dt_master/tests. Preserve that path while exposing its test classes
# through dt_master.testing.test_automation_contracts.
__file__ = str(_suite_path)
exec(compile(_suite_path.read_text(), str(_suite_path), "exec"), globals())
