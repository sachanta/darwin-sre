#!/usr/bin/env python3
"""One-shot Arize AX setup for Darwin SRE.

Run once before the first demo:
    poetry run python scripts/setup_arize.py

Creates:
  - ax profile (API key)
  - Anthropic AI integration (for LLM-as-judge)
  - 4 annotation configs (Root Cause, Remediation, Severity, Composite Score)
  - 3 SRE evaluators
  - Golden dataset from training incidents (darwin-sre-golden-v1)
"""
import sys
import os
from pathlib import Path

# Make sure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import config  # noqa: E402 — must come after load_dotenv
from darwin.arize_client import (  # noqa: E402
    setup_ax_profile,
    create_annotation_configs,
    setup_ai_integration,
    create_evaluators,
    upload_golden_dataset,
)


def main() -> None:
    training_path = Path(__file__).parent.parent / "data" / "incidents_training.json"
    if not training_path.exists():
        print("❌ Training incidents not found. Run: poetry run python data/generate_incidents.py")
        sys.exit(1)

    import json
    with open(training_path) as f:
        training_incidents = json.load(f)

    print(f"Darwin SRE — Arize AX Setup (space: {config.ARIZE_SPACE_ID[:12]}...)")
    print("=" * 60)

    print("\n1. Setting up ax profile...")
    setup_ax_profile()
    print("  ✅ ax profile configured")

    print("\n2. Creating annotation configs...")
    create_annotation_configs()

    print("\n3. Registering AI integration (Anthropic)...")
    setup_ai_integration()

    print("\n4. Creating SRE evaluators...")
    create_evaluators()

    print(f"\n5. Uploading golden dataset ({len(training_incidents)} training incidents)...")
    upload_golden_dataset(training_incidents)

    print("\n" + "=" * 60)
    print("✅ Arize setup complete.")
    print(f"   → Open: https://app.arize.com/spaces/{config.ARIZE_SPACE_ID}")
    print("   → Project: darwin-sre")
    print("   → Dataset: darwin-sre-golden-v1")
    print("\nNext: poetry run python main.py")


if __name__ == "__main__":
    main()
