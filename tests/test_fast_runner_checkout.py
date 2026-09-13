"""Child CLI imports must follow the tested checkout, not an editable install."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace


def test_full_gate_pins_checkout_source_for_pytest_and_its_children(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_fast_runner", root / "scripts/test_fast.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner.sys, "argv", ["test_fast.py", "--tier", "full"])
    monkeypatch.setenv("PYTHONPATH", "/other-checkout/src")
    called = []
    def run(command, *, cwd, env):
        called.append(command)
        assert cwd == root
        assert env["PYTHONPATH"].split(os.pathsep) == [str(root / "src"), "/other-checkout/src"]
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == 0
    assert called[0][-1] == "tests"
