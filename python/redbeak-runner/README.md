# `redbeak-runner`

Generic outbound runner for Redbeak Demo v0. It claims one leased case at a
time, invokes a configured `TargetAdapter`, submits idempotent turn evidence,
and resumes from a local checkpoint after interruption.

The private checkpoint records each complete turn/completion request before
transmission. If the server accepts it but the reply is lost, restart resends
the same output, observations, timing and failure information under the same
idempotency key. Turn replay may rebuild adapter session state, but it does not
replace that saved request. A pending completion is submitted without resetting
or calling the target again. Checkpoint and temporary files are mode 0600;
they contain a lease capability and must not be included in evidence exports.
The optional field is backward-compatible with older checkpoint files.

The package contains no BFCL-specific code and no evaluation criteria. Dataset
import, mock services, demo targets, and scoring live elsewhere.

## CLI

Customer-facing commands talk to a Redbeak Project over outbound HTTP:

```bash
redbeak runner doctor --adapter your.adapter:Adapter --base-url https://app.example
redbeak runner start --adapter your.adapter:Adapter --base-url https://app.example
```

`--reference-server` is a repository-test flag. It lazily imports
`redbeak-reference-server`, an internal in-memory OpenAPI stand-in that never
binds a socket. A customer `pip install redbeak-runner` does not include that
package, a web framework, or an ASGI server.

The four-case walking-skeleton checkpoint is:

```bash
make walking-skeleton
# or: uv run redbeak-bfcl-demo walking-skeleton
```

## Tests

```bash
uv run pytest python/redbeak-runner
```
