#!/usr/bin/env bash
# Download public DEXPI P&ID examples used to test AutoPlant (not redistributed in this repo).
#  - DEXPI TrainingTestCases (CC BY 4.0): https://gitlab.com/dexpi/TrainingTestCases
#  - Equinor NOAKA/DISC DEXPI examples (no licence file published - for local testing only):
#    https://github.com/equinor/NOAKADEXPI
set -euo pipefail
mkdir -p pilot_data && cd pilot_data
[ -d dexpi ]  || git clone --depth 1 https://gitlab.com/dexpi/TrainingTestCases.git dexpi
[ -d noaka ]  || git clone --depth 1 https://github.com/equinor/NOAKADEXPI.git noaka
echo "done. Run: python scripts/run_corpus.py pilot_data"
