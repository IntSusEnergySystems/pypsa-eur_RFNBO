# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Compute RFNBO policy-resolution metrics across planning horizons.

Aggregates hydrogen balances, electrolyser utilisation, VRE curtailment,
H2 prices and H2-related CO2 flows into a single CSV with planning horizons
as columns.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

import pandas as pd
import pypsa

from scripts._helpers import configure_logging, set_scenario_config

logger = logging.getLogger(__name__)

TWH = 1e6
GW = 1e3
MTCO2 = 1e6

PIPELINE_CARRIER_PATTERN = re.compile(r"pipeline", re.IGNORECASE)
VRE_CARRIER_PATTERN = re.compile(r"^(solar|onwind|offwind)", re.IGNORECASE)
HORIZON_PATTERN = re.compile(r"(\d{4})\.nc$")
ELECTROLYSIS_CARRIERS = {"H2 Electrolysis", "vre H2 Electrolysis"}
H2_PRODUCTION_NON_RFNBO_CARRIERS = {"SMR", "SMR CC", "ammonia cracker"}
PTX_CO2_CARRIERS = {"Fischer-Tropsch", "Sabatier", "methanolisation"}


def _weights(n: pypsa.Network) -> pd.Series:
    return n.snapshot_weightings.generators


def _to_twh(energy_mwh: float) -> float:
    return energy_mwh / TWH


def _to_mtco2(energy_mwh: float) -> float:
    return energy_mwh / MTCO2


def _h2_buses(n: pypsa.Network) -> set[str]:
    return set(n.buses.index[n.buses.carrier == "H2"])


def _co2_atmosphere_buses(n: pypsa.Network) -> set[str]:
    if "co2 atmosphere" in n.buses.index:
        return {"co2 atmosphere"}
    return set(n.buses.index[n.buses.carrier == "co2"])


def _co2_stored_buses(n: pypsa.Network) -> set[str]:
    return set(n.buses.index[n.buses.carrier == "co2 stored"])


def _link_h2_port(link: pd.Series, h2_buses: set[str]) -> int | None:
    for i in range(5):
        bus = link.get(f"bus{i}")
        if bus in h2_buses:
            return i
    return None


def _link_port_to_bus(link: pd.Series, buses: set[str]) -> int | None:
    for i in range(5):
        if link.get(f"bus{i}") in buses:
            return i
    return None


def _weighted_link_flow(
    n: pypsa.Network, name: str, port: int, weights: pd.Series
) -> float:
    if name not in n.links_t[f"p{port}"].columns:
        return 0.0
    return float(weights @ n.links_t[f"p{port}"][name])


def _is_pipeline_carrier(carrier: str) -> bool:
    return bool(PIPELINE_CARRIER_PATTERN.search(str(carrier)))


def _h2_production_by_carrier(n: pypsa.Network) -> dict[str, float]:
    h2_buses = _h2_buses(n)
    if not h2_buses:
        return {}

    weights = _weights(n)
    production: dict[str, float] = defaultdict(float)

    for name, link in n.links.iterrows():
        carrier = link.carrier
        if _is_pipeline_carrier(carrier):
            continue
        port = _link_h2_port(link, h2_buses)
        if port is None:
            continue
        port_flow = -_weighted_link_flow(n, name, port, weights)
        if link.p_nom <= 0 and port_flow <= 0:
            continue
        if port_flow > 0:
            production[carrier] += _to_twh(port_flow)

    return dict(production)


def _h2_consumption_by_carrier(n: pypsa.Network) -> dict[str, float]:
    h2_buses = _h2_buses(n)
    if not h2_buses:
        return {}

    weights = _weights(n)
    consumption: dict[str, float] = defaultdict(float)

    for name, link in n.links.iterrows():
        carrier = link.carrier
        if _is_pipeline_carrier(carrier):
            continue
        port = _link_h2_port(link, h2_buses)
        if port is None:
            continue
        port_flow = _weighted_link_flow(n, name, port, weights)
        if port_flow > 0:
            consumption[carrier] += _to_twh(port_flow)

    h2_loads = n.loads[n.loads.bus.isin(h2_buses)]
    for name, load in h2_loads.iterrows():
        if name in n.loads_t.p.columns:
            ts = n.loads_t.p[name]
        elif name in n.loads_t.p_set.columns:
            ts = n.loads_t.p_set[name]
        else:
            continue
        energy = float(weights @ ts.abs())
        if energy > 0:
            consumption[load.carrier] += _to_twh(energy)

    return dict(consumption)


def _electrolyser_metrics(n: pypsa.Network) -> tuple[dict[str, float], dict[str, float]]:
    weights = _weights(n)
    duration = weights.sum()
    capacities: dict[str, float] = defaultdict(float)
    capacity_factors: dict[str, float] = defaultdict(float)

    for carrier in ELECTROLYSIS_CARRIERS:
        links = n.links[n.links.carrier == carrier]
        if links.empty:
            continue
        cap_mw = links.p_nom_opt.sum()
        if cap_mw <= 0:
            continue
        h2_buses = _h2_buses(n)
        energy = 0.0
        for name, link in links.iterrows():
            port = _link_h2_port(link, h2_buses)
            if port is None:
                continue
            energy += max(-_weighted_link_flow(n, name, port, weights), 0.0)
        capacities[carrier] = cap_mw / GW
        capacity_factors[carrier] = 100.0 * energy / (cap_mw * duration) if duration else 0.0

    return dict(capacities), dict(capacity_factors)


def _electrolytic_share(n: pypsa.Network) -> float:
    production = _h2_production_by_carrier(n)
    if not production:
        return float("nan")
    electrolytic = sum(production.get(c, 0.0) for c in ELECTROLYSIS_CARRIERS)
    total = sum(production.values())
    return 100.0 * electrolytic / total if total else float("nan")


def _vre_curtailment(n: pypsa.Network) -> dict[str, float]:
    weights = _weights(n)
    duration = weights.sum()
    if duration <= 0:
        return {}

    curtailment = n.statistics.curtailment(comps="Generator", groupby="carrier")
    capacity = n.statistics.optimal_capacity("Generator", groupby="carrier")

    result: dict[str, float] = {}
    for carrier in sorted(set(curtailment.index) & set(capacity.index)):
        if not VRE_CARRIER_PATTERN.match(str(carrier)):
            continue
        available = capacity[carrier] * duration
        if available <= 0:
            continue
        result[carrier] = 100.0 * curtailment[carrier] / available

    return result


def _bus_h2_withdrawal(n: pypsa.Network, h2_buses: set[str]) -> pd.Series:
    weights = _weights(n)
    withdrawal = pd.Series(0.0, index=sorted(h2_buses))

    for name, link in n.links.iterrows():
        if _is_pipeline_carrier(link.carrier):
            continue
        port = _link_h2_port(link, h2_buses)
        if port is None:
            continue
        bus = link[f"bus{port}"]
        flow = _weighted_link_flow(n, name, port, weights)
        if flow > 0:
            withdrawal[bus] += flow

    for name, load in n.loads[n.loads.bus.isin(h2_buses)].iterrows():
        if name in n.loads_t.p.columns:
            ts = n.loads_t.p[name]
        elif name in n.loads_t.p_set.columns:
            ts = n.loads_t.p_set[name]
        else:
            continue
        withdrawal[load.bus] += float(weights @ ts.abs())

    return withdrawal


def _h2_prices(n: pypsa.Network) -> dict[str, float]:
    h2_bus_idx = n.buses.index[n.buses.carrier == "H2"]
    if h2_bus_idx.empty:
        return {
            "H2 price mean (EUR/MWh)": float("nan"),
            "H2 price demand-weighted mean (EUR/MWh)": float("nan"),
            "H2 price min country demand-weighted (EUR/MWh)": float("nan"),
            "H2 price max country demand-weighted (EUR/MWh)": float("nan"),
        }

    weights = _weights(n)
    prices = n.buses_t.marginal_price[h2_bus_idx]
    bus_means = prices.T @ weights / weights.sum()

    unweighted_mean = float(bus_means.mean())

    h2_buses = set(h2_bus_idx)
    withdrawal = _bus_h2_withdrawal(n, h2_buses)
    total_withdrawal = withdrawal.sum()
    if total_withdrawal > 0:
        demand_weighted_mean = float((bus_means * withdrawal).sum() / total_withdrawal)
    else:
        demand_weighted_mean = float("nan")

    countries = n.buses.loc[h2_bus_idx, "country"]
    country_prices = []
    for country in countries.unique():
        buses = countries[countries == country].index
        country_withdrawal = withdrawal.reindex(buses, fill_value=0.0)
        if country_withdrawal.sum() <= 0:
            continue
        country_price = float(
            (bus_means.reindex(buses, fill_value=0.0) * country_withdrawal).sum()
            / country_withdrawal.sum()
        )
        country_prices.append(country_price)

    if country_prices:
        min_country = min(country_prices)
        max_country = max(country_prices)
    else:
        min_country = float("nan")
        max_country = float("nan")

    return {
        "H2 price mean (EUR/MWh)": unweighted_mean,
        "H2 price demand-weighted mean (EUR/MWh)": demand_weighted_mean,
        "H2 price min country demand-weighted (EUR/MWh)": min_country,
        "H2 price max country demand-weighted (EUR/MWh)": max_country,
    }


def _h2_co2_metrics(n: pypsa.Network) -> dict[str, float]:
    weights = _weights(n)
    co2_atmosphere = _co2_atmosphere_buses(n)
    co2_stored = _co2_stored_buses(n)

    non_rfnbo_emissions = 0.0
    if co2_atmosphere:
        non_rfnbo_links = n.links[n.links.carrier.isin(H2_PRODUCTION_NON_RFNBO_CARRIERS)]
        for name, link in non_rfnbo_links.iterrows():
            port = _link_port_to_bus(link, co2_atmosphere)
            if port is None:
                continue
            flow = _weighted_link_flow(n, name, port, weights)
            non_rfnbo_emissions += max(-flow, 0.0)

    ptx_co2 = 0.0
    if co2_stored:
        for carrier in PTX_CO2_CARRIERS:
            for name, link in n.links[n.links.carrier == carrier].iterrows():
                port = _link_port_to_bus(link, co2_stored)
                if port is None:
                    continue
                flow = _weighted_link_flow(n, name, port, weights)
                ptx_co2 += max(flow, 0.0)

    non_rfnbo_mt = _to_mtco2(non_rfnbo_emissions)
    ptx_mt = _to_mtco2(ptx_co2)
    return {
        "H2 production CO2 to atmosphere (MtCO2)": non_rfnbo_mt,
        "H2 PtX CO2 from stored (MtCO2)": ptx_mt,
        "H2-related CO2 net (MtCO2)": non_rfnbo_mt - ptx_mt,
    }


def calculate_rfnbo_metrics(n: pypsa.Network) -> pd.Series:
    rows: dict[str, float] = {}

    for carrier, value in sorted(_h2_production_by_carrier(n).items()):
        rows[f"H2 production | {carrier} (TWh)"] = value

    production = _h2_production_by_carrier(n)
    non_rfnbo_twh = sum(
        production.get(c, 0.0) for c in H2_PRODUCTION_NON_RFNBO_CARRIERS
    )
    if non_rfnbo_twh > 0:
        rows["H2 production | non-RFNBO (TWh)"] = non_rfnbo_twh

    for carrier, value in sorted(_h2_consumption_by_carrier(n).items()):
        rows[f"H2 consumption | {carrier} (TWh)"] = value

    capacities, capacity_factors = _electrolyser_metrics(n)
    for carrier, value in sorted(capacities.items()):
        rows[f"Electrolyser capacity | {carrier} (GW)"] = value
    for carrier, value in sorted(capacity_factors.items()):
        rows[f"Electrolyser capacity factor | {carrier} (%)"] = value

    rows["Electrolytic share of H2 production (%)"] = _electrolytic_share(n)

    for carrier, value in sorted(_vre_curtailment(n).items()):
        rows[f"VRE curtailment | {carrier} (%)"] = value

    rows.update(_h2_prices(n))
    rows.update(_h2_co2_metrics(n))

    return pd.Series(rows)


def horizon_from_network_path(path: str) -> str:
    match = HORIZON_PATTERN.search(path)
    if not match:
        raise ValueError(f"Could not parse planning horizon from network path: {path}")
    return match.group(1)


def metrics_table(
    networks: dict[str, str], horizons: list[str] | None = None
) -> pd.DataFrame:
    if horizons is None:
        horizons = sorted(networks.keys(), key=int)

    columns = {}
    for horizon in horizons:
        path = networks.get(horizon)
        if path is None:
            logger.warning("Missing network for planning horizon %s", horizon)
            continue
        logger.info("Computing RFNBO metrics for %s", path)
        n = pypsa.Network(path)
        columns[horizon] = calculate_rfnbo_metrics(n)

    df = pd.DataFrame(columns)
    df.index.name = "metric"
    return df.sort_index()


def _dev_snakemake(run_name: str = "baseline"):
    """Build a minimal snakemake object for standalone runs."""
    import os
    from pathlib import Path
    from types import SimpleNamespace

    import yaml

    root = Path(__file__).resolve().parent.parent
    run_name = os.environ.get("RUN", run_name)
    config: dict = {}
    for cfg in (
        "config/config.quick_test_chain.yaml",
        "config/plotting.default.yaml",
    ):
        with open(root / cfg) as fh:
            config.update(yaml.safe_load(fh))

    scenario = config["scenario"]
    networks = [
        str(
            root
            / f"results/{run_name}/networks/base_s_{cluster}_{opt}_{sector_opt}_{horizon}.nc"
        )
        for cluster in scenario["clusters"]
        for opt in scenario["opts"]
        for sector_opt in scenario["sector_opts"]
        for horizon in scenario["planning_horizons"]
    ]
    output_path = root / f"results/{run_name}/csvs/rfnbo_metrics.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = root / f"results/{run_name}/logs/make_rfnbo_metrics.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(
        input=SimpleNamespace(networks=networks),
        output=SimpleNamespace(rfnbo_metrics=str(output_path)),
        params=SimpleNamespace(scenario=scenario),
        config=config,
        log={"python": str(log_path)},
        rule="make_rfnbo_metrics",
        wildcards={"run": run_name},
    )


if __name__ == "__main__":
    dev_fallback = False
    if "snakemake" not in globals():
        try:
            from scripts._helpers import mock_snakemake

            snakemake = mock_snakemake(
                "make_rfnbo_metrics",
                run="baseline",
                configfiles=[
                    "config/config.quick_test_chain.yaml",
                    "config/plotting.default.yaml",
                ],
            )
        except Exception as exc:
            logger.warning("mock_snakemake failed (%s); using dev fallback", exc)
            snakemake = _dev_snakemake()
            dev_fallback = True

    if dev_fallback:
        logging.basicConfig(level=logging.INFO)
    else:
        configure_logging(snakemake)
        set_scenario_config(snakemake)

    network_paths = {
        horizon_from_network_path(path): path for path in snakemake.input.networks
    }
    horizons = [str(h) for h in snakemake.params.scenario["planning_horizons"]]

    metrics_table(network_paths, horizons=horizons).to_csv(
        snakemake.output.rfnbo_metrics
    )
