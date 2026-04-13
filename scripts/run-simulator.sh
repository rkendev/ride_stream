#!/bin/bash
# Run the ride simulator via CLI
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate
python -m ridestream.entry_points.cli simulate "$@"
