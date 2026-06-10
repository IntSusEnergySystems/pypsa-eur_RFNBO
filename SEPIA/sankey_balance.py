# -*- coding: utf-8 -*-
"""Check and enforce mass balance at sankey intermediate nodes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import SEPIA_functions as sf

logger = logging.getLogger(__name__)

SANKEY_YEARS = range(2025, 2051, 5)
MIN_FLOW = 1e-4

ENERGY_INTERMEDIATE_TYPES = ("SECONDARY_ENERGIES", "FINAL_ENERGIES")
BOUNDARY_TYPES = (
    "PRIMARY_ENERGIES",
    "DEMAND_SECTORS",
    "IMPORTS",
    "EXPORTS",
    "LOCAL_PROD",
    "GHG",
    "GHG_SECTORS",
    "GROUPS",
    "OTHER",
    "SECONDARY_IMPORTS",
)

# Default imp/exp boundary nodes used by balance_node for carbon transit nodes.
CARBON_BALANCE_PORTS: dict[str, tuple[str, str]] = {
    "met_ghg": ("hth_ghg", "eth_ghg"),
    "gas_ghg": ("fol_ghg", "stm"),
    "oil_ghg": ("fol_ghg", "stm"),
    "atm": ("fol_ghg", "net_ghg"),
    "blg_ghg": ("fol_ghg", "net_ghg"),
    "bm_ghg": ("fol_ghg", "net_ghg"),
    "stm": ("fol_ghg", "net_ghg"),
}

ENERGY_IMPORT_CSV_FLOWS = [
    ("imp", "gaz_pe"),
    ("imp", "pet_pe"),
    ("imp", "elc_se"),
    ("imp", "hyd_se"),
    ("imp", "enc_pe"),
    ("imp", "amm_fe"),
    ("imp", "met_fe"),
    ("imp", "cms_pe"),
]

ENERGY_LOCAL_CSV_FLOWS = [
    ("prod", "gaz_pe"),
    ("prod", "pet_pe"),
    ("prod", "hdr_pe"),
    ("prod", "eon_pe"),
    ("prod", "eof_pe"),
    ("prod", "enc_pe"),
    ("prod", "spv_pe"),
    ("prod", "pac_pe"),
    ("prod", "cms_pe"),
    ("prod", "bgl_pe"),
    ("prod", "win_pe"),
]

ENERGY_EXPORT_CSV_FLOWS = [
    ("elc_se", "exp"),
    ("hyd_se", "exp"),
    ("enc_pe", "exp"),
    ("met_fe", "exp"),
    ("amm_fe", "exp"),
    ("gaz_se", "exp"),
]


@dataclass(frozen=True)
class BalanceIssue:
    year: int
    node: str
    inflow: float
    outflow: float
    diff: float
    tolerance: float


@dataclass(frozen=True)
class CsvMismatch:
    csv_file: str
    column: str
    year: int
    csv_value: float
    flow_value: float
    diff: float


def load_sepia_nodes(config_path: Path | str = "SEPIA/SEPIA_config.xlsx") -> pd.DataFrame:
    return pd.read_excel(config_path, sheet_name="NODES", index_col=0)


def intermediate_energy_nodes(nodes: pd.DataFrame | None = None) -> set[str]:
    nodes = nodes if nodes is not None else load_sepia_nodes()
    mask = nodes["Type"].isin(ENERGY_INTERMEDIATE_TYPES)
    return set(nodes.loc[mask].index)


def intermediate_carbon_nodes(processes_path: Path | str = "SEPIA/SEPIA_config.xlsx") -> set[str]:
    proc = pd.read_excel(processes_path, sheet_name="PROCESSES_2", index_col=0).reset_index()
    sources = set(proc["Source"].dropna())
    targets = set(proc["Target"].dropna())
    return sources & targets - {"imp", "exp"}


def deduplicate_flows(flows: pd.DataFrame) -> pd.DataFrame:
    if flows.empty:
        return flows
    deduped = flows.T.groupby(level=[0, 1, 2]).sum().T
    return deduped.sort_index(axis=1)


def zero_small_flows(flows: pd.DataFrame, min_flow: float = MIN_FLOW) -> pd.DataFrame:
    """Zero sub-threshold values without corrupting MultiIndex columns."""
    out = flows.copy()
    values = out.to_numpy(dtype=float, copy=True)
    values[np.abs(values) < min_flow] = 0.0
    out.iloc[:, :] = values
    return out


def _node_inflow_outflow(flows: pd.DataFrame, year: int, node: str) -> tuple[float, float]:
    year_key = str(year) if str(year) in flows.index else year
    if year_key not in flows.index:
        return 0.0, 0.0

    try:
        inflow = float(flows.xs(node, level="Target", axis=1).loc[year_key].sum())
    except KeyError:
        inflow = 0.0

    try:
        outflow = float(flows.xs(node, level="Source", axis=1).loc[year_key].sum())
    except KeyError:
        outflow = 0.0

    return inflow, outflow


def prepare_flows_for_sankey(flows: pd.DataFrame, decimals: int = 3) -> pd.DataFrame:
    prepared = deduplicate_flows(flows.copy())
    prepared = prepared.round(decimals)
    return zero_small_flows(prepared)


def balance_intermediate_nodes(
    flows: pd.DataFrame,
    nodes: Iterable[str],
    *,
    imp: str = "imp",
    exp: str = "exp",
    port_map: dict[str, tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """Route imbalances at intermediate nodes to boundary imp/exp (or mapped ports)."""
    flows = deduplicate_flows(flows)
    port_map = port_map or {}
    for node in nodes:
        imp_node, exp_node = port_map.get(node, (imp, exp))
        sf.balance_node(flows, node, imp=imp_node, exp=exp_node)
    return flows


def balance_energy_flows(flows: pd.DataFrame, nodes: pd.DataFrame | None = None) -> pd.DataFrame:
    return balance_intermediate_nodes(flows, sorted(intermediate_energy_nodes(nodes)))


def balance_carbon_flows(
    flows: pd.DataFrame,
    processes_path: Path | str = "SEPIA/SEPIA_config.xlsx",
) -> pd.DataFrame:
    return balance_intermediate_nodes(
        flows,
        sorted(intermediate_carbon_nodes(processes_path)),
        port_map=CARBON_BALANCE_PORTS,
    )


def check_sankey_node_balance(
    flows: pd.DataFrame,
    *,
    intermediate_nodes: set[str] | None = None,
    decimals: int = 3,
    abs_tolerance: float = 0.01,
    min_flow: float = MIN_FLOW,
    years: Iterable[int] = SANKEY_YEARS,
) -> list[BalanceIssue]:
    """Return intermediate nodes whose inflow and outflow differ beyond tolerance."""
    prepared = prepare_flows_for_sankey(flows, decimals=decimals)
    issues: list[BalanceIssue] = []

    for year in years:
        if str(year) not in prepared.index and year not in prepared.index:
            continue

        candidates = intermediate_nodes
        if candidates is None:
            candidates = set(prepared.columns.get_level_values("Target")) | set(
                prepared.columns.get_level_values("Source")
            )

        for node in sorted(candidates):
            inflow, outflow = _node_inflow_outflow(prepared, year, node)

            if max(inflow, outflow) < min_flow:
                continue
            if inflow < min_flow or outflow < min_flow:
                continue

            diff = inflow - outflow
            tolerance = max(abs_tolerance, 1e-3 * max(inflow, outflow))
            if abs(diff) > tolerance:
                issues.append(
                    BalanceIssue(
                        year=year,
                        node=node,
                        inflow=inflow,
                        outflow=outflow,
                        diff=diff,
                        tolerance=tolerance,
                    )
                )

    return issues


def _flow_column_sum(flows: pd.DataFrame, flow: tuple[str, str, str], year) -> float:
    if flow not in flows.columns:
        return 0.0
    value = flows.loc[year, flow]
    if isinstance(value, pd.Series):
        value = value.sum()
    return 0.0 if pd.isna(value) else float(value)


def verify_country_csvs(
    flows: pd.DataFrame,
    study: str,
    country: str,
    *,
    abs_tolerance: float = 0.01,
    years: Iterable[int] = SANKEY_YEARS,
) -> list[CsvMismatch]:
    """Compare boundary imp/prod/exp flows with country_csvs written by SEPIA."""
    flows = deduplicate_flows(flows)
    csv_dir = Path(f"results/{study}/country_csvs")
    mismatches: list[CsvMismatch] = []

    checks = [
        ("total_imports", ENERGY_IMPORT_CSV_FLOWS),
        ("local_product", ENERGY_LOCAL_CSV_FLOWS),
        ("exports", ENERGY_EXPORT_CSV_FLOWS),
    ]

    for stem, flow_pairs in checks:
        csv_path = csv_dir / f"{stem}_{country}.csv"
        if not csv_path.exists():
            logger.warning("Missing CSV %s (skip verification)", csv_path)
            continue

        csv_df = pd.read_csv(csv_path, index_col=0)
        csv_df.index = csv_df.index.map(str)

        for source, target in flow_pairs:
            col_name = f"{source}_{target}"
            if col_name not in csv_df.columns:
                continue
            flow_key = (source, target, "")
            for year in years:
                year_key = str(year)
                if year_key not in csv_df.index:
                    continue
                csv_val = float(csv_df.loc[year_key, col_name])
                flow_val = _flow_column_sum(flows, flow_key, year_key)
                diff = csv_val - flow_val
                if abs(diff) > abs_tolerance:
                    mismatches.append(
                        CsvMismatch(
                            csv_file=csv_path.name,
                            column=col_name,
                            year=year,
                            csv_value=csv_val,
                            flow_value=flow_val,
                            diff=diff,
                        )
                    )

    return mismatches


def warn_balance_issues(
    issues: list[BalanceIssue],
    *,
    scenario: str,
    country: str,
    sankey_type: str,
) -> None:
    if not issues:
        return

    logger.warning(
        "%s sankey for %s/%s: %d unbalanced intermediate node(s)",
        sankey_type,
        scenario,
        country,
        len(issues),
    )
    for issue in issues:
        logger.warning(
            "  %s/%s/%s year %s node %s: inflow=%.4f, outflow=%.4f, "
            "diff=%+.4f (tolerance=%.4f)",
            scenario,
            country,
            sankey_type,
            issue.year,
            issue.node,
            issue.inflow,
            issue.outflow,
            issue.diff,
            issue.tolerance,
        )


def warn_csv_mismatches(
    mismatches: list[CsvMismatch],
    *,
    scenario: str,
    country: str,
) -> None:
    if not mismatches:
        return

    logger.warning(
        "CSV mismatch for %s/%s: %d row(s)",
        scenario,
        country,
        len(mismatches),
    )
    for item in mismatches:
        logger.warning(
            "  %s/%s %s year %s: csv=%.4f flow=%.4f diff=%+.4f",
            scenario,
            country,
            item.csv_file,
            item.column,
            item.year,
            item.csv_value,
            item.flow_value,
            item.diff,
        )
