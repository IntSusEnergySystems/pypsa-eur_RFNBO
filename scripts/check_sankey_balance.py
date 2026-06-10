#!/usr/bin/env python3
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Check that sankey intermediate nodes are mass-balanced and match country CSV exports.

Rebuilds flow data via ``prepare_sepia(flows_only=True)`` (including SEPIA balancing),
then warns on any remaining intermediate-node imbalance or CSV mismatch.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEPIA_DIR = ROOT / "SEPIA"
DEFAULT_CONFIGFILES = [
    "config/config.quick_test_chain.yaml",
    "config/plotting.default.yaml",
]

logger = logging.getLogger(__name__)


def discover_sankey_pages(results_dir: Path) -> list[dict[str, str | Path]]:
    pages: list[dict[str, str | Path]] = []
    for html in sorted(results_dir.glob("*/htmls/*_sankeys_*.html")):
        stem = html.stem
        if "_sankeys_" not in stem:
            continue
        country, scenario = stem.split("_sankeys_", 1)
        pages.append({"path": html, "country": country, "scenario": scenario})
    return pages


def ensure_snakefile() -> Path | None:
    snakefile = ROOT / "Snakefile"
    if snakefile.exists():
        return None
    target = ROOT / "Snakefile_quick_test_chain"
    if not target.exists():
        raise FileNotFoundError(
            "No Snakefile found. Create one or add Snakefile_quick_test_chain."
        )
    snakefile.symlink_to("Snakefile_quick_test_chain")
    return snakefile


def load_sepia_module():
    if str(SEPIA_DIR) not in sys.path:
        sys.path.insert(0, str(SEPIA_DIR))
    spec = importlib.util.spec_from_file_location("sepia_main", SEPIA_DIR / "SEPIA.py")
    if spec is None or spec.loader is None:
        raise ImportError("Could not load SEPIA/SEPIA.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sepia_main"] = module
    spec.loader.exec_module(module)
    return module


def load_flows_for_scenario(scenario: str, configfiles: list[str]):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts._helpers import mock_snakemake, set_scenario_config

    os.chdir(ROOT)
    smk = mock_snakemake(
        "generate_sepia",
        configfiles=configfiles,
        run=scenario,
    )
    set_scenario_config(smk)

    sepia = load_sepia_module()
    sepia.snakemake = smk
    sepia.study = smk.params.study
    sepia.df = sepia.biomass_potentials()

    tot_flows, tot_co2 = sepia.prepare_sepia(smk.params.countries, flows_only=True)
    return tot_flows, tot_co2, smk.params.study


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check intermediate-node balance in SEPIA sankey diagrams."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "results",
        help="Directory containing scenario subfolders (default: results/)",
    )
    parser.add_argument(
        "--config",
        action="append",
        dest="configfiles",
        help="Snakemake config file (repeatable; defaults to quick_test_chain configs)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="Limit to one or more scenarios (default: all found sankey HTML files)",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )

    args = parse_args()
    configfiles = args.configfiles or DEFAULT_CONFIGFILES

    pages = discover_sankey_pages(args.results_dir)
    if args.scenario:
        allowed = set(args.scenario)
        pages = [p for p in pages if p["scenario"] in allowed]

    if not pages:
        logger.error("No sankey HTML files found under %s", args.results_dir)
        return 1

    if str(SEPIA_DIR) not in sys.path:
        sys.path.insert(0, str(SEPIA_DIR))
    from sankey_balance import (
        check_sankey_node_balance,
        intermediate_carbon_nodes,
        intermediate_energy_nodes,
        verify_country_csvs,
        warn_balance_issues,
        warn_csv_mismatches,
    )

    energy_nodes = intermediate_energy_nodes()
    carbon_nodes = intermediate_carbon_nodes()

    by_scenario: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        by_scenario[str(page["scenario"])].append(str(page["country"]))

    logger.info(
        "Checking %d sankey page(s) across %d scenario(s)",
        len(pages),
        len(by_scenario),
    )

    snakefile_link = ensure_snakefile()
    total_issues = 0

    try:
        for scenario, countries in sorted(by_scenario.items()):
            logger.info("Loading flows for scenario %s", scenario)
            tot_flows, tot_co2, study = load_flows_for_scenario(scenario, configfiles)

            for country in sorted(set(countries)):
                if country not in tot_flows:
                    logger.warning(
                        "Scenario %s: no flow data for country %s (skipping)",
                        scenario,
                        country,
                    )
                    continue

                csv_mismatches = verify_country_csvs(
                    tot_flows[country],
                    study,
                    country,
                )
                if csv_mismatches:
                    warn_csv_mismatches(
                        csv_mismatches,
                        scenario=scenario,
                        country=country,
                    )
                    total_issues += len(csv_mismatches)

                for sankey_type, flows, nodes in (
                    ("energy", tot_flows[country], energy_nodes),
                    ("carbon", tot_co2[country], carbon_nodes),
                ):
                    issues = check_sankey_node_balance(
                        flows,
                        intermediate_nodes=nodes,
                    )
                    if issues:
                        warn_balance_issues(
                            issues,
                            scenario=scenario,
                            country=country,
                            sankey_type=sankey_type,
                        )
                        total_issues += len(issues)
                    else:
                        logger.info(
                            "OK %s/%s %s sankey: all intermediate nodes balanced",
                            scenario,
                            country,
                            sankey_type,
                        )
    finally:
        if snakefile_link is not None and snakefile_link.is_symlink():
            snakefile_link.unlink()

    if total_issues:
        logger.warning("Found %d issue(s) in total", total_issues)
        return 1

    logger.info("All intermediate sankey nodes are balanced and CSVs match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
