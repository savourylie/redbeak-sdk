# `redbeak-runner`

Generic outbound runner for Redbeak Demo v0. It claims one leased case at a
time, invokes a configured `TargetAdapter`, submits idempotent turn evidence,
and resumes from a local checkpoint after interruption.

The package contains no BFCL-specific code and no evaluation criteria. Dataset
import, mock services, demo targets, and scoring live elsewhere.

## CLI

```bash
redbeak runner doctor --adapter redbeak_bfcl_demo.golden --reference-server
redbeak runner start --adapter redbeak_bfcl_demo.golden --reference-server \
  --work-file work.json --until-idle
```

`--reference-server` serves the frozen OpenAPI contract in-process. That is the
local stand-in for the SaaS control plane (TAI-162 / TAI-163), not a product
deployment.

The four-case walking-skeleton checkpoint is:

```bash
make walking-skeleton
# or: uv run redbeak-bfcl-demo walking-skeleton
```

## Tests

```bash
uv run pytest python/redbeak-runner
```
