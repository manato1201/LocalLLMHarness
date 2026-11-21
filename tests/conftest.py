from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fake_backend import FakeBackend  # noqa: E402
from llh.config import Config, ModelSpec  # noqa: E402
from llh.core import Harness  # noqa: E402
from llh.policy import load_policy  # noqa: E402

ROOT = Path(__file__).parent.parent

MAIN, VL, WRITER = "main-model", "vl-model", "writer-model"


@pytest.fixture
def backend():
    b = FakeBackend(loaded=[MAIN, VL, WRITER])
    yield b
    b.stop()


@pytest.fixture
def cfg(tmp_path, backend):
    return Config(
        backend_base_url=backend.base_url,
        backend_api_key_env="LLH_TEST_KEY",
        token_env="LLH_TEST_TOKEN",
        models={
            "main": ModelSpec(
                "main",
                "llh-main",
                MAIN,
                tasks=("code", "json", "mermaid", "diff", "log", "rag"),
            ),
            "vision": ModelSpec("vision", "llh-vision", VL, tasks=("vision", "screen")),
            "writer": ModelSpec(
                "writer", "llh-writer", WRITER, tasks=("doc", "summary", "note")
            ),
        },
        max_context_tokens=2000,
        max_output_tokens=500,
        default_max_tokens=100,
        queue_timeout_s=3,
        policy_path=ROOT / "policy.example.toml",
        audit_path=tmp_path / "audit.jsonl",
    )


@pytest.fixture
def harness(cfg):
    return Harness(cfg, policy=load_policy(cfg.policy_path))
