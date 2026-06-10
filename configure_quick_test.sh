#!/usr/bin/env bash
# Patch scenario configs for a fast local test run.
# Settings changed: countries, scenario.clusters, scenario.sector_opts,
# gurobi-default threads, and solving.mem_mb.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COUNTRIES="BE FR"
SECTOR_OPTS="6H"
GUROBI_THREADS=8
MEM_MB=90000

usage() {
    cat <<'EOF'
Usage: ./configure_quick_test.sh [OPTIONS]

Set countries, cluster count (one per country), temporal resolution, Gurobi
thread count, and solve memory limit in all scenario config files. No other
config keys are modified.

Options:
  --countries LIST    Comma- or space-separated ISO country codes (default: BE,FR)
  --resolution RES    sector_opts time step, e.g. 6H or 24H (default: 6H)
  --threads N         Gurobi threads for gurobi-default preset (default: 8)
  --mem-gb N          Snakemake memory limit for solves in GB (default: 90)
  -h, --help            Show this help

Examples:
  ./configure_quick_test.sh
  ./configure_quick_test.sh --countries BE,FR --resolution 6H
  ./configure_quick_test.sh --countries "BE FR" --resolution 24H
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --countries)
            COUNTRIES="${2//,/ }"
            shift 2
            ;;
        --resolution|--sector-opts)
            SECTOR_OPTS="$2"
            shift 2
            ;;
        --threads)
            GUROBI_THREADS="$2"
            shift 2
            ;;
        --mem-gb)
            MEM_MB=$(( $2 * 1000 ))
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

export QUICK_TEST_COUNTRIES="$COUNTRIES"
export QUICK_TEST_SECTOR_OPTS="$SECTOR_OPTS"
export QUICK_TEST_GUROBI_THREADS="$GUROBI_THREADS"
export QUICK_TEST_MEM_MB="$MEM_MB"

python3 <<'PY'
import os
import re
import sys
from pathlib import Path

countries = os.environ["QUICK_TEST_COUNTRIES"].split()
n_clusters = len(countries)
sector_opts = os.environ["QUICK_TEST_SECTOR_OPTS"]
gurobi_threads = os.environ["QUICK_TEST_GUROBI_THREADS"]
mem_mb = os.environ["QUICK_TEST_MEM_MB"]

configs = [
    "config/config.quick_test_chain.yaml",
    "config/config.baseline.yaml",
    "config/config.baseline_without_H2.yaml",
    "config/config.RFNBO_CR.yaml",
    "config/config.RFNBO_Temp.yaml",
    "config/config.RFNBO_Add.yaml",
    "config/config.RFNBO_VAR-A1.yaml",
    "config/config.RFNBO_VAR-A2.yaml",
]

countries_re = re.compile(r"^countries:\n(?:- .+\n)+", re.MULTILINE)
clusters_re = re.compile(r"(  clusters:\n)  - \d+\n", re.MULTILINE)
sector_opts_re = re.compile(r'(  sector_opts:\n)  - "[^"]*"\n', re.MULTILINE)
gurobi_threads_re = re.compile(r'("gurobi-default":\n      threads: )\d+')
mem_mb_re = re.compile(r"(  mem_mb: )\d+")


def format_country(code: str) -> str:
    if code in {"NO"}:
        return f"- '{code}'"
    return f"- {code}"


for cfg in configs:
    path = Path(cfg)
    if not path.exists():
        print(f"Skipping missing file: {cfg}", file=sys.stderr)
        continue

    text = path.read_text()
    new_countries = "countries:\n" + "\n".join(format_country(c) for c in countries) + "\n"

    new_text, n_countries = countries_re.subn(new_countries, text, count=1)
    if n_countries != 1:
        sys.exit(f"Failed to patch countries in {cfg}")

    new_text, n_clusters_patch = clusters_re.subn(
        rf"\g<1>  - {n_clusters}\n", new_text, count=1
    )
    if n_clusters_patch != 1:
        sys.exit(f"Failed to patch clusters in {cfg}")

    new_text, n_sector_opts = sector_opts_re.subn(
        rf'\1  - "{sector_opts}"\n', new_text, count=1
    )
    if n_sector_opts != 1:
        sys.exit(f"Failed to patch sector_opts in {cfg}")

    new_text, n_threads = gurobi_threads_re.subn(
        rf"\g<1>{gurobi_threads}", new_text, count=1
    )
    if n_threads != 1:
        sys.exit(f"Failed to patch gurobi-default threads in {cfg}")

    new_text, n_mem = mem_mb_re.subn(rf"\g<1>{mem_mb}", new_text, count=1)
    if n_mem != 1:
        sys.exit(f"Failed to patch mem_mb in {cfg}")

    if new_text != text:
        path.write_text(new_text)
        print(
            f"Updated {cfg}: countries={countries}, clusters={n_clusters}, "
            f"sector_opts={sector_opts}, gurobi-default threads={gurobi_threads}, "
            f"mem_mb={mem_mb}"
        )
    else:
        print(f"Already set: {cfg}")
PY

echo "Done. Countries: $COUNTRIES | clusters: $(echo "$COUNTRIES" | wc -w) | sector_opts: $SECTOR_OPTS | gurobi threads: $GUROBI_THREADS | mem_mb: $MEM_MB"
