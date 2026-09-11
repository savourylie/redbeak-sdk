# `redbeak-reference-server`

In-memory OpenAPI stand-in used by runner tests and the local Demo v0
checkpoints. It never binds a socket: callers drive it through
`httpx.ASGITransport`.

This package is an internal test tool. It is not part of a customer
`redbeak-runner` install, and no customer is meant to host a Redbeak service
from it. The public SDK repository (TAI-214) should not relocate it.
