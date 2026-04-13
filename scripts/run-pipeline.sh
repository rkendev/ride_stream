#!/bin/bash
# Trigger a pipeline task via CLI
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate
python -m ridestream.entry_points.cli pipeline "$@"
