"""The example, exercised the way a customer gets it: installed, outside the repo.

Every other test in this directory imports the example from the workspace, where
a broken package declaration is invisible because the source tree is on the path
anyway. This one builds the wheels, installs them into a fresh virtualenv under
a temporary directory, and drives the adapter there with no checkout reachable,
no ``REDBEAK_CONTRACTS_ROOT``, and no ``PYTHONPATH`` — which is the only way to
show that what a customer installs is what these tests describe.

The model provider is still a stub. A clean environment is the point; a network
call and a credential are not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
PACKAGES = (
    REPO / "python/redbeak-contracts",
    REPO / "python/redbeak-adapter-sdk",
    REPO / "python/redbeak-runner",
    REPO / "python/examples/llm-api",
)


def run(*args: str, cwd: Path) -> str:
    """A subprocess with every development escape hatch removed."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"REDBEAK_CONTRACTS_ROOT", "PYTHONPATH"}
    }
    env["UV_OFFLINE"] = "1"
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def customer_python(tmp_path_factory: pytest.TempPathFactory, pytestconfig: pytest.Config) -> str:
    root = tmp_path_factory.mktemp("customer-install")
    assert not root.is_relative_to(REPO)

    # Runtime dependencies come from the workspace lock, so the clean install
    # resolves nothing and needs no index.
    runtime_lock = root / "pylock.runtime.toml"
    run(
        "uv",
        "export",
        "--offline",
        "--frozen",
        "--package",
        "redbeak-runner",
        "--no-dev",
        "--no-emit-workspace",
        "--format",
        "pylock.toml",
        "--output-file",
        str(runtime_lock),
        cwd=pytestconfig.rootpath,
    )
    wheels = root / "wheels"
    for package in PACKAGES:
        run("uv", "build", "--offline", "--wheel", "--out-dir", str(wheels), str(package), cwd=root)

    venv = root / "venv"
    run(sys.executable, "-m", "venv", str(venv), cwd=root)
    python = str(venv / "bin/python")
    run("uv", "pip", "sync", "--offline", "--python", python, str(runtime_lock), cwd=root)
    run(
        python,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
        *(str(wheel) for wheel in sorted(wheels.glob("*.whl"))),
        cwd=root,
    )
    run(python, "-m", "pip", "check", cwd=root)
    return python


PROBE = """
import asyncio
import importlib.metadata as md
import json
import os
import sys
from pathlib import Path

import httpx
import redbeak_contracts as rc
import redbeak_example_llm_api
from redbeak_adapter_sdk import ObservationRequest, SessionContext, TargetAdapter, UserInput
from redbeak_example_llm_api import LlmApiAdapter
from redbeak_runner.adapters import load_adapter

prefix = Path(sys.prefix)
assert "REDBEAK_CONTRACTS_ROOT" not in os.environ
assert Path(rc.__file__).is_relative_to(prefix), rc.__file__
assert Path(redbeak_example_llm_api.__file__).is_relative_to(prefix)
assert rc.CONTRACT_VERSION == "0.1"

# Nothing private, and nothing that could open a port, came along.
names = {dist.metadata["Name"].lower().replace("_", "-") for dist in md.distributions()}
assert not names & {"starlette", "uvicorn", "fastapi", "redbeak-reference-server"}, names
assert not any(name.startswith("redbeak-bfcl") for name in names), names
assert "redbeak_reference_server" not in sys.modules

# The spec the README tells a customer to pass to --adapter, through the
# installed runner's own discovery.
os.environ.update(
    LLM_API_BASE_URL="https://models.example.test/v1",
    LLM_API_KEY="sk-clean-install-probe",
    LLM_API_MODEL="example-model-1",
)
discovered = load_adapter("redbeak_example_llm_api")
assert isinstance(discovered, TargetAdapter)
capabilities = asyncio.run(discovered.capabilities())
assert capabilities.adapter_name == "llm_api"
assert capabilities.supports_observations is False
assert capabilities.contract_version == "0.1"

REPLY = "\\u6b63\\u78ba\\u7b54\\u6848\\u662f (B) 7\\u3002"
seen = []


def provider(request):
    seen.append(json.loads(request.content))
    return httpx.Response(200, json={
        "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY}}]
    })


adapter = LlmApiAdapter(
    base_url="https://models.example.test/v1",
    api_key="sk-clean-install-probe",
    model="example-model-1",
    transport=httpx.MockTransport(provider),
)
context = SessionContext(
    project_id="11111111-1111-4111-8111-111111111111",
    run_id="22222222-2222-4222-8222-222222222222",
    case_execution_id="33333333-3333-4333-8333-333333333333",
    scenario_id="44444444-4444-4444-8444-444444444444",
    scenario_external_id="tmmlu_sample_1",
)
question = "\\u4e0b\\u5217\\u4f55\\u8005\\u70ba\\u8cea\\u6578\\uff1f (A) 4 (B) 7"

asyncio.run(adapter.reset(context))
output = asyncio.run(adapter.send(UserInput(sequence=0, content=question), context))
observations = asyncio.run(adapter.observe(ObservationRequest(fact_names=("state",)), context))
asyncio.run(adapter.close(context))

# The question reached the provider; the reply reached Redbeak unchanged.
assert seen == [{"model": "example-model-1",
                 "messages": [{"role": "user", "content": question}]}], seen
assert output.to_dict() == {"type": "agent_message", "content": REPLY}
assert observations == []
print("clean install ok")
"""


def test_the_example_works_from_installed_distributions(
    customer_python: str, tmp_path: Path
) -> None:
    output = run(customer_python, "-c", PROBE, cwd=tmp_path)

    assert "clean install ok" in output
