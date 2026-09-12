"""Exercise real isolated builds and installed packages outside any checkout."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import redbeak_contracts as rc
from redbeak_contracts.verify import verify

REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "python/redbeak-contracts"
RESOURCE = "redbeak_contracts/_contracts/"


def run(*args: str, cwd: Path, override: Path | None = None) -> str:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"REDBEAK_CONTRACTS_ROOT", "PYTHONPATH"}
    }
    env["UV_OFFLINE"] = "1"
    if override:
        env["REDBEAK_CONTRACTS_ROOT"] = str(override)
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("distributions")
    assert not root.is_relative_to(REPO)
    direct = root / "direct"
    for name in ("redbeak-contracts", "redbeak-adapter-sdk", "redbeak-runner"):
        run(
            "uv",
            "build",
            "--offline",
            "--wheel",
            "--out-dir",
            str(direct),
            str(REPO / "python" / name),
            cwd=root,
        )
    run("uv", "build", "--offline", "--sdist", "--out-dir", str(root), str(PACKAGE), cwd=root)
    sdist = next(root.glob("*.tar.gz"))
    extracted = root / "extracted"
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert not any("/fixtures/" in name or "_editable_contracts_root" in name for name in names)
        assert any(name.endswith("/hatch_build.py") for name in names)
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    # uv uses an isolated PEP 517 build environment, with only the sdist's files.
    rebuilt = root / "rebuilt"
    run("uv", "build", "--offline", "--wheel", "--out-dir", str(rebuilt), str(source), cwd=root)
    return {"root": root, "direct": direct, "rebuilt": rebuilt, "source": source}


PROBE = """
import asyncio
import importlib.metadata as md
import json
import sys
from pathlib import Path

import httpx
import redbeak_contracts as rc
import redbeak_runner.cli
from redbeak_adapter_sdk import AdapterCapabilities, AgentOutput
from redbeak_runner.client import RunnerClient

assert rc.CONTRACT_VERSION == '0.1'
assert len(rc.schema_names()) == 21
assert Path(rc.__file__).is_relative_to(Path(sys.prefix))
corpus = json.loads(Path(sys.argv[1]).read_text())
for kind, entries in corpus.items():
    for name, document in entries:
        assert rc.is_valid(name, document) == (kind == 'valid'), (kind, name)
AdapterCapabilities(adapter_name='test_adapter', adapter_version='0.1.0')
AgentOutput.from_dict({'type': 'agent_message', 'content': 'Synthetic response'})
try:
    AgentOutput.from_dict({'type': 'not-an-output'})
except rc.ValidationError:
    pass
else:
    raise AssertionError('SDK accepted an invalid output')

async def check_runner():
    request = next(doc for name, doc in corpus['valid'] if name == 'work-claim-request')
    response = next(doc for name, doc in corpus['valid']
                    if name == 'work-claim-response' and doc['status'] == 'no_work')
    async with httpx.AsyncClient(base_url='https://redbeak.invalid',
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))) as http:
        client = RunnerClient(http, runner_key='synthetic-test-key')
        assert await client.claim(request) == response
        try:
            await client.claim({})
        except rc.ValidationError:
            pass
        else:
            raise AssertionError('runner accepted an invalid claim')
asyncio.run(check_runner())
names = {dist.metadata['Name'].lower().replace('_', '-') for dist in md.distributions()}
assert not names & {'starlette', 'uvicorn', 'fastapi', 'redbeak-reference-server'}
assert 'redbeak_reference_server' not in sys.modules
assert 'starlette' not in sys.modules
try:
    rc.fixture_dir('valid')
except rc.ContractError:
    pass
else:
    raise AssertionError('fixtures shipped in the wheel')
"""


@pytest.mark.parametrize("kind", ["direct", "rebuilt"])
def test_clean_install_validates_all_three_packages(
    artifacts: dict[str, Path], tmp_path: Path, kind: str
) -> None:
    venv = tmp_path / "venv"
    run(sys.executable, "-m", "venv", str(venv), cwd=tmp_path)
    python = str(venv / "bin/python")
    # Runtime dependencies are cached by uv sync; pip installs the actual artifacts.
    run(
        "uv",
        "pip",
        "install",
        "--offline",
        "--python",
        python,
        "jsonschema>=4.23",
        "httpx>=0.27",
        "typer>=0.12",
        cwd=tmp_path,
    )
    wheels = list(artifacts["direct"].glob("*.whl"))
    if kind == "rebuilt":
        wheels = [p for p in wheels if not p.name.startswith("redbeak_contracts-")]
        wheels.extend(artifacts["rebuilt"].glob("*.whl"))
    run(
        python,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
        *(str(p) for p in wheels),
        cwd=tmp_path,
    )
    run(python, "-m", "pip", "check", cwd=tmp_path)
    corpus = {
        kind: [
            (p.parent.name, json.loads(p.read_text()))
            for p in sorted((REPO / "contracts/fixtures" / kind).rglob("*.json"))
        ]
        for kind in ("valid", "invalid")
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus))
    run(python, "-c", PROBE, str(corpus_path), cwd=tmp_path)
    contract_wheel = next(p for p in wheels if p.name.startswith("redbeak_contracts-"))
    run(
        python,
        "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import redbeak_contracts as rc; assert '.whl/' in rc.__file__; "
        "assert len(rc.schema_names()) == 21; "
        "rc.validate('agent-output', {'type': 'agent_message', 'content': 'ZIP resource'})",
        str(contract_wheel),
        cwd=tmp_path,
    )
    # The verifier inspects bundled data even when a source override is set.
    run(
        python,
        "-m",
        "redbeak_contracts.verify",
        "--repository",
        str(REPO),
        "--ref",
        "HEAD",
        cwd=tmp_path,
        override=tmp_path / "nonexistent",
    )

    override = tmp_path / "override"
    shutil.copytree(REPO / "contracts/json-schema", override / "json-schema")
    common = override / "json-schema/0.1/common.schema.json"
    schema = json.loads(common.read_text())
    schema["title"] = "Overridden live schema"
    common.write_text(json.dumps(schema))
    run(
        python,
        "-c",
        "import redbeak_contracts as rc; "
        "assert rc.load_schema('common')['title'] == 'Overridden live schema'",
        cwd=tmp_path,
        override=override,
    )
    run(
        python,
        "-c",
        "import redbeak_contracts as rc\n"
        "try: rc.load_schema('common')\n"
        "except rc.ContractError: pass\n"
        "else: raise AssertionError('invalid override was ignored')",
        cwd=tmp_path,
        override=tmp_path / "nonexistent",
    )


def test_artifacts_match_committed_schemas(artifacts: dict[str, Path]) -> None:
    manifests = []
    for kind in ("direct", "rebuilt"):
        wheel = next(artifacts[kind].glob("redbeak_contracts-*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            assert not any(
                "/fixtures/" in n or "_editable_contracts_root" in n for n in archive.namelist()
            )
            root = zipfile.Path(archive, RESOURCE)
            assert verify(root, REPO, "HEAD") == 21
            # A published tag is an additional requirement, not a HEAD alias.
            tagged = (
                subprocess.run(
                    ["git", "rev-parse", "--verify", "refs/tags/contract-v0.1"],
                    cwd=REPO,
                    capture_output=True,
                ).returncode
                == 0
            )
            if tagged:
                assert verify(root, REPO, "contract-v0.1") == 21
            manifests.append(root.joinpath("schema-manifest.json").read_bytes())
    assert manifests[0] == manifests[1]


def test_drift_is_rejected_even_with_updated_manifest(
    artifacts: dict[str, Path], tmp_path: Path
) -> None:
    root = tmp_path / "contracts"
    shutil.copytree(artifacts["source"] / "src/redbeak_contracts/_contracts", root)
    schema = root / "json-schema/0.1/common.schema.json"
    schema.write_bytes(schema.read_bytes() + b"\n")
    with pytest.raises(rc.ContractError, match="SHA-256 manifest"):
        verify(root, REPO, "HEAD")
    manifest = root / "schema-manifest.json"
    data = json.loads(manifest.read_text())
    data["schemas"]["0.1/common.schema.json"] = hashlib.sha256(schema.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    with pytest.raises(rc.ContractError, match="differ from HEAD"):
        verify(root, REPO, "HEAD")


def test_sdist_build_rejects_tampering(artifacts: dict[str, Path], tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(artifacts["source"], source)
    schema = source / "src/redbeak_contracts/_contracts/json-schema/0.1/common.schema.json"
    schema.write_bytes(schema.read_bytes() + b"\n")
    result = subprocess.run(
        ["uv", "build", "--offline", "--wheel", str(source)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "sdist schemas do not match" in result.stderr


def test_missing_tag_is_not_replaced_with_head(artifacts: dict[str, Path]) -> None:
    root = artifacts["source"] / "src/redbeak_contracts/_contracts"
    with pytest.raises(rc.ContractError):
        verify(root, REPO, "refs/tags/contract-v-missing-for-test")


def test_tag_comparison_ignores_working_tree(artifacts: dict[str, Path], tmp_path: Path) -> None:
    # A disposable source repo exercises the tag path without creating release refs.
    shutil.copytree(REPO / "contracts/json-schema", tmp_path / "contracts/json-schema")
    run("git", "init", cwd=tmp_path)
    run("git", "add", "contracts", cwd=tmp_path)
    run(
        "git",
        "-c",
        "user.name=Distribution test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        "Synthetic contract snapshot",
        cwd=tmp_path,
    )
    run("git", "-c", "tag.gpgsign=false", "tag", "contract-v0.1", cwd=tmp_path)
    (tmp_path / "contracts/json-schema/0.1/common.schema.json").write_text("{}")
    root = artifacts["source"] / "src/redbeak_contracts/_contracts"
    assert verify(root, tmp_path, "refs/tags/contract-v0.1") == 21


def test_editable_install_reads_live_source() -> None:
    assert rc.contracts_root() == REPO / "contracts"
    assert rc.fixture_dir("valid") == REPO / "contracts/fixtures/valid"
    assert rc.load_schema("common") == json.loads(
        (REPO / "contracts/json-schema/0.1/common.schema.json").read_text()
    )
