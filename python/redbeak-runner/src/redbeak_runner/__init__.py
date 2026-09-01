"""Generic outbound Redbeak runner.

The runner is a customer-side client. It claims one leased case at a time,
invokes a configured ``TargetAdapter``, submits idempotent turn evidence, and
resumes from a local checkpoint after interruption. It contains no evaluation
criteria and no dataset-specific logic: importer, mock environment, demo
targets, and evaluator own those.

Nothing here is a scorer. Adapter output and observations are evidence; whether
they satisfy a hidden expected outcome is decided later, in Redbeak Cloud.
"""

from __future__ import annotations

import redbeak_contracts as rc

CONTRACT_VERSION = rc.CONTRACT_VERSION
RUNNER_VERSION = "0.1.0"

__all__ = ["CONTRACT_VERSION", "RUNNER_VERSION"]
