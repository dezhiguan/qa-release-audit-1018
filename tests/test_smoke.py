import ast
import json
from pathlib import Path
import yaml
from keel.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]

def test_manifest_matches_the_delivered_contract():
    manifest = load_manifest(ROOT / "agent.yaml")
    assert manifest.metadata.name == 'qa-release-audit-1018'

def test_entry_is_valid_python_and_has_an_async_entry():
    tree = ast.parse((ROOT / "app.py").read_text())
    assert any(isinstance(node, ast.AsyncFunctionDef) and node.name == "run" for node in tree.body)
