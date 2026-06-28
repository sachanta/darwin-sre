"""Arize AX integration for Darwin SRE.

Handles:
- Golden dataset upload from training incidents
- Experiment creation per Darwin generation
- Annotation config + evaluator setup

All calls use the Arize REST v2 API or ax CLI subprocess.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx

import config

_API_BASE = "https://api.arize.com/v2"
_SPACE = config.ARIZE_SPACE_ID
_API_KEY = config.ARIZE_API_KEY
_PROJECT = config.ARIZE_PROJECT_NAME


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    }


def _ax(*args: str) -> str:
    """Run an ax CLI command and return stdout. Raises on non-zero exit."""
    cmd = ["poetry", "run", "ax"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    if result.returncode != 0:
        raise RuntimeError(f"ax {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def setup_ax_profile() -> None:
    """Create or update the ax CLI profile with our API key (silent, fail-safe)."""
    try:
        _ax("profiles", "create", "default", "--api-key", _API_KEY)
    except RuntimeError:
        try:
            _ax("profiles", "update", "--api-key", _API_KEY)
        except RuntimeError:
            pass


def create_annotation_configs() -> dict[str, str]:
    """Create SRE annotation configs via REST API (CLI doesn't support scores).

    Returns dict of config_name -> config_id.
    """
    configs = [
        {
            "name": "SRE Root Cause",
            "space_id": _SPACE,
            "annotation_config_type": "categorical",
            "values": [
                {"label": "correct", "score": 1.0},
                {"label": "partially_correct", "score": 0.5},
                {"label": "incorrect", "score": 0.0},
            ],
            "optimization_direction": "maximize",
        },
        {
            "name": "SRE Remediation",
            "space_id": _SPACE,
            "annotation_config_type": "categorical",
            "values": [
                {"label": "correct", "score": 1.0},
                {"label": "partially_correct", "score": 0.5},
                {"label": "incorrect", "score": 0.0},
            ],
            "optimization_direction": "maximize",
        },
        {
            "name": "SRE Severity",
            "space_id": _SPACE,
            "annotation_config_type": "categorical",
            "values": [
                {"label": "correct", "score": 1.0},
                {"label": "incorrect", "score": 0.0},
            ],
            "optimization_direction": "maximize",
        },
        {
            "name": "SRE Composite Score",
            "space_id": _SPACE,
            "annotation_config_type": "continuous",
            "minimum_score": 0.0,
            "maximum_score": 1.0,
            "optimization_direction": "maximize",
        },
    ]

    ids = {}
    with httpx.Client(timeout=30) as client:
        for cfg in configs:
            resp = client.post(f"{_API_BASE}/annotation-configs", headers=_headers(), json=cfg)
            if resp.status_code in (200, 201):
                ids[cfg["name"]] = resp.json().get("id", "")
                print(f"  ✅ annotation config '{cfg['name']}': {ids[cfg['name']]}")
            elif resp.status_code == 409:
                print(f"  ℹ️  annotation config '{cfg['name']}' already exists")
            else:
                print(f"  ⚠️  annotation config '{cfg['name']}': {resp.status_code} {resp.text[:200]}")
    return ids


def setup_ai_integration() -> None:
    """Register Anthropic as the LLM judge provider in Arize."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        print("  ⚠️  ANTHROPIC_API_KEY not set — skipping AI integration")
        return
    try:
        out = _ax(
            "ai-integrations", "create",
            "--space", _SPACE,
            "--provider", "anthropic",
            "--api-key", anthropic_key,
        )
        print(f"  ✅ AI integration (Anthropic): {out[:60]}")
    except RuntimeError as e:
        if "already exists" in str(e).lower():
            print("  ℹ️  Anthropic AI integration already exists")
        else:
            print(f"  ⚠️  AI integration: {e}")


def create_evaluators() -> None:
    """Create the 3 SRE LLM-as-judge evaluators in Arize."""
    evaluators = [
        {
            "name": "SRE Root Cause Accuracy",
            "prompt": (
                "You are evaluating an AI SRE agent's root cause identification.\n\n"
                "Incident: {input}\n"
                "Agent Resolution: {output}\n"
                "Ground Truth: {expected_output}\n\n"
                "Classify as:\n"
                "- correct: Root cause matches or is a more specific version of ground truth\n"
                "- partially_correct: Right area but misses key technical details\n"
                "- incorrect: Contradicts ground truth or completely wrong"
            ),
            "choices": ["correct", "partially_correct", "incorrect"],
        },
        {
            "name": "SRE Remediation Quality",
            "prompt": (
                "You are evaluating remediation steps from an AI SRE agent.\n\n"
                "Incident: {input}\n"
                "Agent Resolution: {output}\n"
                "Ground Truth: {expected_output}\n\n"
                "Classify as:\n"
                "- correct: Steps are complete, correctly ordered, and would resolve the incident\n"
                "- partially_correct: Addresses the issue but incomplete or suboptimally ordered\n"
                "- incorrect: Steps are wrong, dangerous, or would not resolve the incident"
            ),
            "choices": ["correct", "partially_correct", "incorrect"],
        },
        {
            "name": "SRE Severity Accuracy",
            "prompt": (
                "You are evaluating severity classification by an AI SRE agent.\n\n"
                "Incident: {input}\n"
                "Agent Resolution: {output}\n"
                "Ground Truth: {expected_output}\n\n"
                "Severity levels: P1=critical/service-down, P2=degraded, P3=minor/monitoring\n\n"
                "Classify as:\n"
                "- correct: Severity (P1/P2/P3) exactly matches ground truth\n"
                "- incorrect: Severity does not match ground truth"
            ),
            "choices": ["correct", "incorrect"],
        },
    ]

    for ev in evaluators:
        args = [
            "evaluators", "create",
            "--name", ev["name"],
            "--space", _SPACE,
            "--prompt", ev["prompt"],
        ]
        for choice in ev["choices"]:
            args += ["--classification-choice", choice]
        try:
            _ax(*args)
            print(f"  ✅ evaluator '{ev['name']}'")
        except RuntimeError as e:
            if "already exists" in str(e).lower():
                print(f"  ℹ️  evaluator '{ev['name']}' already exists")
            else:
                print(f"  ⚠️  evaluator '{ev['name']}': {e}")


def upload_golden_dataset(training_incidents: list[dict], dataset_name: str = "darwin-sre-golden-v1") -> str:
    """Convert training incidents into Arize dataset format and upload via ax CLI.

    Returns the dataset name.
    """
    examples = []
    for inc in training_incidents:
        gt = inc.get("ground_truth", {})
        examples.append({
            "input": json.dumps({
                "title": inc["title"],
                "service": inc["service"],
                "category": inc["category"],
                "description": inc["description"],
                "logs": inc.get("logs", ""),
                "metrics": inc.get("metrics", {}),
            }),
            "expected_output": json.dumps({
                "root_cause": gt.get("root_cause", ""),
                "severity": gt.get("severity", ""),
                "remediation_steps": gt.get("remediation_steps", []),
            }),
            "category": inc.get("category", "unknown"),
            "is_edge_case": str(inc.get("is_edge_case", False)).lower(),
            "metadata": json.dumps({
                "source": "synthetic_training",
                "incident_id": inc["id"],
            }),
        })

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(examples, f, indent=2)
        tmp_path = f.name

    try:
        _ax(
            "datasets", "create",
            "--name", dataset_name,
            "--space", _SPACE,
            "--file", tmp_path,
        )
        print(f"  ✅ golden dataset '{dataset_name}' uploaded ({len(examples)} examples)")
    except RuntimeError as e:
        if "already exists" in str(e).lower():
            print(f"  ℹ️  dataset '{dataset_name}' already exists")
        else:
            print(f"  ⚠️  dataset upload: {e}")
    finally:
        os.unlink(tmp_path)

    return dataset_name


def create_experiment(generation: int, results: list[dict], dataset_name: str = "darwin-sre-golden-v1") -> str:
    """Create an Arize experiment for a Darwin generation's results.

    Each result in `results` must have: incident (with 'id'), resolution, scores.
    Returns the experiment name.
    """
    exp_name = f"darwin-gen-{generation}"

    runs = []
    for r in results:
        runs.append({
            "example_id": r["incident"]["id"],
            "output": json.dumps(r["resolution"]),
            "score_composite": r["scores"]["composite"],
            "score_root_cause": r["scores"]["root_cause_accuracy"],
            "score_remediation": r["scores"]["remediation_quality"],
            "score_severity": r["scores"]["severity_accuracy"],
            "generation": generation,
            "is_edge_case": str(r["incident"].get("is_edge_case", False)).lower(),
        })

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(runs, f, indent=2)
        tmp_path = f.name

    try:
        _ax(
            "experiments", "create",
            "--name", exp_name,
            "--dataset", dataset_name,
            "--space", _SPACE,
            "--file", tmp_path,
        )
        print(f"  ✅ Arize experiment '{exp_name}' created ({len(runs)} runs)")
    except RuntimeError as e:
        if "already exists" in str(e).lower():
            print(f"  ℹ️  experiment '{exp_name}' already exists")
        else:
            print(f"  ⚠️  experiment '{exp_name}': {e}")
    finally:
        os.unlink(tmp_path)

    return exp_name
