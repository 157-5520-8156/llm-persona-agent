"""Offline-only experiment: prepare a request or inspect a saved witness response.

PYTHONPATH=src python scripts/inspect_visible_source_witness.py --help
No provider, credentials, database, receipt or delivery port is used.
"""

import argparse
import json
from pathlib import Path

from companion_daemon.world_v2.visible_source_witness_experiment import prepare_witness_experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--beats", type=Path, required=True, help="JSON array of original Beat strings"
    )
    parser.add_argument(
        "--sources",
        type=Path,
        required=True,
        help="Full frozen JSON array of canonical source-reference rows",
    )
    parser.add_argument(
        "--response", type=Path, help="Saved experimental model response to inspect; never sent"
    )
    parser.add_argument(
        "--request-output",
        type=Path,
        help="New file for request JSON; existing files never overwritten",
    )
    options = parser.parse_args()
    prepared = prepare_witness_experiment(
        beats=tuple(json.loads(options.beats.read_text())),
        sources=tuple(json.loads(options.sources.read_text())),
    )
    if options.request_output:
        with options.request_output.open("x") as stream:
            json.dump(prepared.request(), stream, ensure_ascii=False, indent=2)
    result = {
        "preparation_sha256": prepared.sha256,
        "receipt_authority": False,
        "request_bytes": len(
            json.dumps(prepared.request(), ensure_ascii=False, separators=(",", ":")).encode()
        ),
    }
    if options.response:
        result["inspection"] = prepared.inspect_response(options.response.read_text())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
