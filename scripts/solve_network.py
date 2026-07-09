# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Solves optimal operation and capacity for a network with the option to
iteratively optimize while updating line reactances.

This script is used for optimizing the electrical network as well as the
sector coupled network.

Description
-----------

Total annual system costs are minimised with PyPSA. The full formulation of the
linear optimal power flow (plus investment planning
is provided in the
`documentation of PyPSA <https://pypsa.readthedocs.io/en/latest/optimal_power_flow.html#linear-optimal-power-flow>`_.

The optimization is based on the :func:`network.optimize` function.
Additionally, some extra constraints specified in :mod:`solve_network` are added.

.. note::

    The rules ``solve_elec_networks`` and ``solve_sector_networks`` run
    the workflow for all scenarios in the configuration file (``scenario:``)
    based on the rule :mod:`solve_network`.
"""

import importlib
import logging
import os
import re
import sys
from functools import partial
from typing import Any

import linopy
import numpy as np
import pandas as pd
import pypsa
import xarray as xr
import yaml
from linopy.remote.oetc import OetcCredentials, OetcHandler, OetcSettings
from pypsa.descriptors import get_activity_mask
from pypsa.descriptors import get_switchable_as_dense as get_as_dense
from prepare_sector_network import determine_emission_sectors
from scripts._benchmark import memory_logger
from scripts._helpers import (
    PYPSA_V1,
    configure_logging,
    get,
    set_scenario_config,
    update_config_from_wildcards,
)

logger = logging.getLogger(__name__)

# Allow for PyPSA versions <0.35
if PYPSA_V1:
    pypsa.network.power_flow.logger.setLevel(logging.WARNING)
else:
    pypsa.pf.logger.setLevel(logging.WARNING)


class ObjectiveValueError(Exception):
    pass


def add_land_use_constraint_perfect(n: pypsa.Network) -> None:
    """
    Add global constraints for tech capacity limit.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance

    Returns
    -------
    pypsa.Network
        Network with added land use constraints
    """
    logger.info("Add land-use constraint for perfect foresight")

    def compress_series(s):
        def process_group(group):
            if group.nunique() == 1:
                return pd.Series(group.iloc[0], index=[None])
            else:
                return group

        return s.groupby(level=[0, 1]).apply(process_group)

    def new_index_name(t):
        # Convert all elements to string and filter out None values
        parts = [str(x) for x in t if x is not None]
        # Join with space, but use a dash for the last item if not None
        return " ".join(parts[:2]) + (f"-{parts[-1]}" if len(parts) > 2 else "")

    def check_p_min_p_max(p_nom_max):
        p_nom_min = n.generators[ext_i].groupby(grouper).sum().p_nom_min
        p_nom_min = p_nom_min.reindex(p_nom_max.index)
        check = (
            p_nom_min.groupby(level=[0, 1]).sum()
            > p_nom_max.groupby(level=[0, 1]).min()
        )
        if check.sum():
            logger.warning(
                f"summed p_min_pu values at node larger than technical potential {check[check].index}"
            )

    grouper = [n.generators.carrier, n.generators.bus, n.generators.build_year]
    ext_i = n.generators.p_nom_extendable
    # get technical limit per node and investment period
    p_nom_max = n.generators[ext_i].groupby(grouper).min().p_nom_max
    # drop carriers without tech limit
    p_nom_max = p_nom_max[~p_nom_max.isin([np.inf, np.nan])]
    # carrier
    carriers = p_nom_max.index.get_level_values(0).unique()
    gen_i = n.generators[(n.generators.carrier.isin(carriers)) & (ext_i)].index
    n.generators.loc[gen_i, "p_nom_min"] = 0
    # check minimum capacities
    check_p_min_p_max(p_nom_max)
    # drop multi entries in case p_nom_max stays constant in different periods
    # p_nom_max = compress_series(p_nom_max)
    # adjust name to fit syntax of nominal constraint per bus
    df = p_nom_max.reset_index()
    df["name"] = df.apply(
        lambda row: (
            f"nom_max_{row['carrier']}"
            + (f"_{row['build_year']}" if row["build_year"] is not None else "")
        ),
        axis=1,
    )

    for name in df.name.unique():
        df_carrier = df[df.name == name]
        bus = df_carrier.bus
        n.buses.loc[bus, name] = df_carrier.p_nom_max.values


def add_land_use_constraint(n: pypsa.Network, planning_horizons: str) -> None:
    """
    Add land use constraints for renewable energy potential.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance
    planning_horizons : str
        The planning horizon year as string

    Returns
    -------
    pypsa.Network
        Modified PyPSA network with constraints added
    """
    # warning: this will miss existing offwind which is not classed AC-DC and has carrier 'offwind'

    for carrier in [
        "solar",
        "solar rooftop",
        "solar-hsat",
        "onwind",
        "offwind-ac",
        "offwind-dc",
        "offwind-float",
    ]:
        ext_i = (n.generators.carrier == carrier) & ~n.generators.p_nom_extendable
        grouper = n.generators.loc[ext_i].index.str.replace(
            f" {carrier}.*$", "", regex=True
        )
        existing = n.generators.loc[ext_i, "p_nom"].groupby(grouper).sum()
        existing.index += f" {carrier}-{planning_horizons}"
        n.generators.loc[existing.index, "p_nom_max"] -= existing

    # check if existing capacities are larger than technical potential
    existing_large = n.generators[
        n.generators["p_nom_min"] > n.generators["p_nom_max"]
    ].index
    if len(existing_large):
        logger.warning(
            f"Existing capacities larger than technical potential for {existing_large},\
                        adjust technical potential to existing capacities"
        )
        n.generators.loc[existing_large, "p_nom_max"] = n.generators.loc[
            existing_large, "p_nom_min"
        ]

    n.generators["p_nom_max"] = n.generators["p_nom_max"].clip(lower=0)


def add_solar_potential_constraints(n: pypsa.Network, config: dict) -> None:
    """
    Add constraint to make sure the sum capacity of all solar technologies (fixed, tracking, ets. ) is below the region potential.

    Example:
    ES1 0: total solar potential is 10 GW, meaning:
           solar potential : 10 GW
           solar-hsat potential : 8 GW (solar with single axis tracking is assumed to have higher land use)
    The constraint ensures that:
           solar_p_nom + solar_hsat_p_nom * 1.13 <= 10 GW
    """
    land_use_factors = {
        "solar-hsat": config["renewable"]["solar"]["capacity_per_sqkm"]
        / config["renewable"]["solar-hsat"]["capacity_per_sqkm"],
    }
    rename = {} if PYPSA_V1 else {"Generator-ext": "Generator"}

    solar_carriers = ["solar", "solar-hsat"]
    solar = n.generators[
        n.generators.carrier.isin(solar_carriers) & n.generators.p_nom_extendable
    ].index

    solar_today = n.generators[
        (n.generators.carrier == "solar") & (n.generators.p_nom_extendable)
    ].index
    solar_hsat = n.generators[(n.generators.carrier == "solar-hsat")].index

    if solar.empty:
        return

    land_use = pd.DataFrame(1, index=solar, columns=["land_use_factor"])
    for carrier, factor in land_use_factors.items():
        land_use = land_use.apply(
            lambda x: (x * factor) if carrier in x.name else x, axis=1
        )

    location = pd.Series(n.buses.index, index=n.buses.index)
    ggrouper = n.generators.loc[solar].bus
    rhs = (
        n.generators.loc[solar_today, "p_nom_max"]
        .groupby(n.generators.loc[solar_today].bus.map(location))
        .sum()
        - n.generators.loc[solar_hsat, "p_nom"]
        .groupby(n.generators.loc[solar_hsat].bus.map(location))
        .sum()
        * land_use_factors["solar-hsat"]
    ).clip(lower=0)

    lhs = (
        (n.model["Generator-p_nom"].rename(rename).loc[solar] * land_use.squeeze())
        .groupby(ggrouper)
        .sum()
    )

    logger.info("Adding solar potential constraint.")
    n.model.add_constraints(lhs <= rhs, name="solar_potential")
    
def add_co2_sequestration_limit(
    n: pypsa.Network,
    limit_dict: dict[str, float],
    planning_horizons: str | None,
) -> None:
    """
    Add a global constraint on the amount of Mt CO2 that can be sequestered.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance
    limit_dict : dict[str, float]
        CO2 sequestration potential limit constraints by year.
    planning_horizons : str, optional
        The current planning horizon year or None in perfect foresight
    """

    if not n.investment_periods.empty:
        nyears = n.snapshot_weightings.groupby(level="period").generators.sum() / 8760
        periods = n.investment_periods
        limit = pd.Series(
            {period: nyears[period] * get(limit_dict, period) for period in periods}
        )
        limit.index = limit.index.map(lambda s: f"co2_sequestration_limit-{s}")
        names = limit.index
    else:
        nyears = n.snapshot_weightings.generators.sum() / 8760
        limit = get(limit_dict, int(planning_horizons)) * nyears
        periods = np.nan
        names = "co2_sequestration_limit"

    n.add(
        "GlobalConstraint",
        names,
        sense=">=",
        constant=-limit * 1e6,
        type="operational_limit",
        carrier_attribute="co2 sequestered",
        investment_period=periods,
    )


def add_carbon_constraint(n: pypsa.Network, snapshots: pd.DatetimeIndex) -> None:
    glcs = n.global_constraints.query('type == "co2_atmosphere"')
    if glcs.empty:
        return
    for name, glc in glcs.iterrows():
        carattr = glc.carrier_attribute
        emissions = n.carriers.query(f"{carattr} != 0")[carattr]

        if emissions.empty:
            continue

        # stores
        bus_carrier = n.stores.bus.map(n.buses.carrier)
        stores = n.stores[bus_carrier.isin(emissions.index) & ~n.stores.e_cyclic]
        if not stores.empty:
            last = n.snapshot_weightings.reset_index().groupby("period").last()
            last_i = last.set_index([last.index, last.timestep]).index
            final_e = n.model["Store-e"].loc[last_i, stores.index]
            time_valid = int(glc.loc["investment_period"])
            time_i = pd.IndexSlice[time_valid, :]
            lhs = final_e.loc[time_i, :] - final_e.shift(snapshot=1).loc[time_i, :]

            rhs = glc.constant
            n.model.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{name}")


def add_carbon_budget_constraint(n: pypsa.Network, snapshots: pd.DatetimeIndex) -> None:
    glcs = n.global_constraints.query('type == "Co2Budget"')
    if glcs.empty:
        return
    for name, glc in glcs.iterrows():
        carattr = glc.carrier_attribute
        emissions = n.carriers.query(f"{carattr} != 0")[carattr]

        if emissions.empty:
            continue

        # stores
        bus_carrier = n.stores.bus.map(n.buses.carrier)
        stores = n.stores[bus_carrier.isin(emissions.index) & ~n.stores.e_cyclic]
        if not stores.empty:
            last = n.snapshot_weightings.reset_index().groupby("period").last()
            last_i = last.set_index([last.index, last.timestep]).index
            final_e = n.model["Store-e"].loc[last_i, stores.index]
            time_valid = int(glc.loc["investment_period"])
            time_i = pd.IndexSlice[time_valid, :]
            weighting = n.investment_period_weightings.loc[time_valid, "years"]
            lhs = final_e.loc[time_i, :] * weighting

            rhs = glc.constant
            n.model.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{name}")


def add_max_growth(n: pypsa.Network, opts: dict) -> None:
    """
    Add maximum growth rates for different carriers.
    """

    # take maximum yearly difference between investment periods since historic growth is per year
    factor = n.investment_period_weightings.years.max() * opts["factor"]
    for carrier in opts["max_growth"].keys():
        max_per_period = opts["max_growth"][carrier] * factor
        logger.info(
            f"set maximum growth rate per investment period of {carrier} to {max_per_period} GW."
        )
        n.carriers.loc[carrier, "max_growth"] = max_per_period * 1e3

    for carrier in opts["max_relative_growth"].keys():
        max_r_per_period = opts["max_relative_growth"][carrier]
        logger.info(
            f"set maximum relative growth per investment period of {carrier} to {max_r_per_period}."
        )
        n.carriers.loc[carrier, "max_relative_growth"] = max_r_per_period


def add_retrofit_gas_boiler_constraint(
    n: pypsa.Network, snapshots: pd.DatetimeIndex
) -> None:
    """
    Allow retrofitting of existing gas boilers to H2 boilers and impose load-following must-run condition on existing gas boilers.
    Modifies the network in place, no return value.

    n : pypsa.Network
        The PyPSA network to be modified
    snapshots : pd.DatetimeIndex
        The snapshots of the network
    """
    c = "Link"
    logger.info("Add constraint for retrofitting gas boilers to H2 boilers.")
    # existing gas boilers
    mask = n.links.carrier.str.contains("gas boiler") & ~n.links.p_nom_extendable
    gas_i = n.links[mask].index
    mask = n.links.carrier.str.contains("retrofitted H2 boiler")
    h2_i = n.links[mask].index

    n.links.loc[gas_i, "p_nom_extendable"] = True
    p_nom = n.links.loc[gas_i, "p_nom"]
    n.links.loc[gas_i, "p_nom"] = 0

    # heat profile
    cols = n.loads_t.p_set.columns[
        n.loads_t.p_set.columns.str.contains("heat")
        & ~n.loads_t.p_set.columns.str.contains("industry")
        & ~n.loads_t.p_set.columns.str.contains("agriculture")
    ]
    profile = n.loads_t.p_set[cols].div(
        n.loads_t.p_set[cols].groupby(level=0).max(), level=0
    )
    # to deal if max value is zero
    profile.fillna(0, inplace=True)
    profile.rename(columns=n.loads.bus.to_dict(), inplace=True)
    profile = profile.reindex(columns=n.links.loc[gas_i, "bus1"])
    profile.columns = gas_i

    rhs = profile.mul(p_nom)

    dispatch = n.model["Link-p"]
    active = get_activity_mask(n, c, snapshots, gas_i)
    rhs = rhs[active]
    if PYPSA_V1:
        p_gas = dispatch.sel(name=gas_i)
        p_h2 = dispatch.sel(name=h2_i)
    else:
        p_gas = dispatch.sel(Link=gas_i)
        p_h2 = dispatch.sel(Link=h2_i)

    lhs = p_gas + p_h2

    n.model.add_constraints(lhs == rhs, name="gas_retrofit")


def add_load_balance_components(n, config, sign=1):
    """
    Add load shedding or load sinks to the network with carrier 'load'.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network to be modified.
    config : dict
        The load shedding or load sinks settings.
    sign : float
        Direction of the added generators. Positive for load shedding, negative for load sinks.

    Returns
    -------
    None
        Modifies PyPSA network in place.
    """
    if "load" not in n.carriers.index:
        n.add("Carrier", "load")

    carriers = config.get("carriers", {})
    default_cost = config.get("default_cost")
    balance_comp = "shedding" if sign > 0 else "sink"

    logger.info(
        f"Add load {balance_comp} for {'all carriers' if config.get('all_carriers') else ', '.join(carriers)}."
    )

    for bus_carrier, price in carriers.items():
        buses_i = n.buses[n.buses.carrier == bus_carrier].index
        n.add(
            "Generator",
            buses_i,
            f" load {balance_comp}",
            bus=buses_i,
            carrier="load",
            marginal_cost=price,
            p_nom=np.inf,
            sign=sign,
        )

    if config.get("all_carriers", False):
        buses_rest_i = n.buses[~n.buses.carrier.isin(carriers)].index
        n.add(
            "Generator",
            buses_rest_i,
            f" load {balance_comp}",
            bus=buses_rest_i,
            carrier="load",
            marginal_cost=default_cost,
            p_nom=np.inf,
            sign=sign,
        )


def prepare_network(
    n: pypsa.Network,
    solve_opts: dict,
    foresight: str,
    planning_horizons: str | None,
    co2_sequestration_potential: dict[str, float],
    limit_max_growth: dict[str, Any] | None = None,
    rolling_horizon: bool = False,
) -> None:
    """
    Prepare network with various constraints and modifications.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance
    solve_opts : Dict
        Dictionary of solving options containing clip_p_max_pu, load_shedding etc.
    foresight : str
        Planning foresight type ('myopic' or 'perfect')
    planning_horizons : str or None
        The current planning horizon year or None for perfect foresight
    co2_sequestration_potential : Dict[str, float]
        CO2 sequestration potential constraints by year

    Returns
    -------
    pypsa.Network
        Modified PyPSA network with added constraints
    """
    if "clip_p_max_pu" in solve_opts:
        for df in (
            n.generators_t.p_max_pu,
            n.generators_t.p_min_pu,
            n.links_t.p_max_pu,
            n.links_t.p_min_pu,
            n.storage_units_t.inflow,
        ):
            df.where(df.abs() > solve_opts["clip_p_max_pu"], other=0.0, inplace=True)

    if (load_shedding := solve_opts.get("load_shedding", {})).get("enable", False):
        # intersect between macroeconomic and surveybased willingness to pay
        # http://journal.frontiersin.org/article/10.3389/fenrg.2015.00055/full
        add_load_balance_components(n, load_shedding)

    if (load_sinks := solve_opts.get("load_sinks", {})).get("enable", False):
        add_load_balance_components(n, load_sinks, sign=-1)

    if solve_opts.get("curtailment_mode"):
        n.add("Carrier", "curtailment", color="#fedfed", nice_name="Curtailment")
        n.generators_t.p_min_pu = n.generators_t.p_max_pu
        buses_i = n.buses.query("carrier == 'AC'").index
        n.add(
            "Generator",
            buses_i,
            suffix=" curtailment",
            bus=buses_i,
            p_min_pu=-1,
            p_max_pu=0,
            marginal_cost=-0.1,
            carrier="curtailment",
            p_nom=1e6,
        )

    if solve_opts.get("noisy_costs"):
        for t in n.components:
            # if 'capital_cost' in t.static:
            #    t.static['capital_cost'] += 1e1 + 2.*(np.random.random(len(t.static)) - 0.5)
            if "marginal_cost" in t.static:
                t.static["marginal_cost"] += 1e-2 + 2e-3 * (
                    np.random.random(len(t.static)) - 0.5
                )

        for t in n.components[["Line", "Link"]]:
            if t.static.empty:
                continue
            t.static["capital_cost"] += (
                1e-1 + 2e-2 * (np.random.random(len(t.static)) - 0.5)
            ) * t.static["length"]

    if solve_opts.get("nhours"):
        nhours = solve_opts["nhours"]
        n.set_snapshots(n.snapshots[:nhours])
        n.snapshot_weightings[:] = 8760.0 / nhours
    config=snakemake.config
    if not config['solving']['constraints']['CCL'] and foresight == "myopic" and planning_horizons:
        add_land_use_constraint(n, planning_horizons)

    if foresight == "perfect":
        add_land_use_constraint_perfect(n)
        if limit_max_growth is not None and limit_max_growth["enable"]:
            add_max_growth(n, limit_max_growth)

    if n.stores.carrier.eq("co2 sequestered").any():
        limit_dict = co2_sequestration_potential
        add_co2_sequestration_limit(
            n, limit_dict=limit_dict, planning_horizons=planning_horizons
        )

    # rolling horizon disables cyclic storage
    if rolling_horizon:
        n.storage_units.state_of_charge_cyclic = False
        n.storage_units.state_of_charge_initial = 0
        n.stores.e_cyclic = False
        n.stores.e_initial = 0

def imposed_transmission_limit(n, config):
    ''' This funtion impse values for TYNDP for transmissions lines'''
    tyndp_values_mapping = {
      ("AT2 0", "CH2 0"): {"s_nom": "at_ch", "s_nom_min": "at_ch"},
      ("AT2 0", "CZ2 0"): {"s_nom": "at_cz", "s_nom_min": "at_cz"},
      ("BE2 0", "NL2 0"): {"s_nom": "be_nl", "s_nom_min": "be_nl"},
      ("BG2 0", "GR2 0"): {"s_nom": "bg_gr", "s_nom_min": "bg_gr"},
      ("BG2 0", "RO2 0"): {"s_nom": "bg_ro", "s_nom_min": "bg_ro"},
      ("CH2 0", "DE2 0"): {"s_nom": "ch_de", "s_nom_min": "ch_de"},
      ("CH2 0", "FR2 0"): {"s_nom": "ch_fr", "s_nom_min": "ch_fr"},
      ("CH2 0", "IT2 0"): {"s_nom": "ch_it", "s_nom_min": "ch_it"},
      ("CZ2 0", "DE2 0"): {"s_nom": "cz_de", "s_nom_min": "cz_de"},
      ("CZ2 0", "PL2 0"): {"s_nom": "cz_pl", "s_nom_min": "cz_pl"},
      ("CZ2 0", "SK2 0"): {"s_nom": "cz_sk", "s_nom_min": "cz_sk"},
      ("DE2 0", "DK2 0"): {"s_nom": "de_dk", "s_nom_min": "de_dk"},
      ("DE2 0", "FR2 0"): {"s_nom": "de_fr", "s_nom_min": "de_fr"},
      ("DE2 0", "LU2 0"): {"s_nom": "de_lu", "s_nom_min": "de_lu"},
      ("DE2 0", "NL2 0"): {"s_nom": "de_nl", "s_nom_min": "de_nl"},
      ("DE2 0", "PL2 0"): {"s_nom": "de_pl", "s_nom_min": "de_pl"},
      ("DK0 0", "SE0 0"): {"s_nom": "dk_se", "s_nom_min": "dk_se"},
      ("EE2 0", "LV2 0"): {"s_nom": "ee_lv", "s_nom_min": "ee_lv"},
      ("ES2 0", "FR2 0"): {"s_nom": "es_fr", "s_nom_min": "es_fr"},
      ("AT2 0", "DE2 0"): {"s_nom": "at_de", "s_nom_min": "at_de"},
      ("ES2 0", "PT2 0"): {"s_nom": "es_pt", "s_nom_min": "es_pt"},
      ("FI0 0", "SE0 0"): {"s_nom": "fi_se", "s_nom_min": "fi_se"},
      ("FI0 0", "NO0 0"): {"s_nom": "fi_no", "s_nom_min": "fi_no"},
      ("FR2 0", "IT2 0"): {"s_nom": "fr_it", "s_nom_min": "fr_it"},
      ("FR2 0", "LU2 0"): {"s_nom": "fr_lu", "s_nom_min": "fr_lu"},
      ("GB3 0", "IE3 0"): {"s_nom": "gb_ie", "s_nom_min": "gb_ie"},
      ("HR2 0", "HU2 0"): {"s_nom": "hr_hu", "s_nom_min": "hr_hu"},
      ("HR2 0", "IT2 0"): {"s_nom": "hr_it", "s_nom_min": "hr_it"},
      ("HR2 0", "SI2 0"): {"s_nom": "hr_si", "s_nom_min": "hr_si"},
      ("HU2 0", "RO2 0"): {"s_nom": "hu_ro", "s_nom_min": "hu_ro"},
      ("HU2 0", "SK2 0"): {"s_nom": "hu_sk", "s_nom_min": "hu_sk"},
      ("HU2 0", "SI2 0"): {"s_nom": "hu_si", "s_nom_min": "hu_si"},
      ("IT2 0", "SI2 0"): {"s_nom": "it_si", "s_nom_min": "it_si"},
      ("LT2 0", "LV2 0"): {"s_nom": "lt_lv", "s_nom_min": "lt_lv"},
      ("LT2 0", "PL2 0"): {"s_nom": "lt_pl", "s_nom_min": "lt_pl"},
      ("AT2 0", "HU2 0"): {"s_nom": "at_hu", "s_nom_min": "at_hu"},
      ("NO0 0", "SE0 0"): {"s_nom": "no_se", "s_nom_min": "no_se"},
      ("PL2 0", "SK2 0"): {"s_nom": "pl_sk", "s_nom_min": "pl_sk"},
      ("AT2 0", "IT2 0"): {"s_nom": "at_it", "s_nom_min": "at_it"},
      ("AT2 0", "SI2 0"): {"s_nom": "at_si", "s_nom_min": "at_si"},
      ("BE2 0", "FR2 0"): {"s_nom": "be_fr", "s_nom_min": "be_fr"},
      ("BE2 0", "LU2 0"): {"s_nom": "be_lu", "s_nom_min": "be_lu"},}
    planning_horizon = int(snakemake.wildcards.planning_horizons[-4:])
    mask = (n.links.index.str.startswith("TYNDP")) & (n.links["build_year"] > planning_horizon)
    n.links.loc[mask, "p_nom_extendable"] = False
    if planning_horizon == 2025:
     n.lines = n.lines.drop_duplicates(subset=["bus0", "bus1"])
     for index, row in n.lines.iterrows():
        key = (row["bus0"], row["bus1"])
        if key in tyndp_values_mapping:
         values = tyndp_values_mapping[key]
         n.lines.loc[index, "s_nom"] = config["TYNDP_values"][values["s_nom"]]
         n.lines.loc[index, "s_nom_min"] = config["TYNDP_values"][values["s_nom_min"]]
    
    n.lines["s_nom_max"] = n.lines["s_nom"] * config["transmission_limit"][planning_horizon]
    condition = ((n.links['carrier'] == 'DC') & (n.links['p_nom'] != 0))
    n.links.loc[condition, "p_nom_max"] = n.links.loc[condition, "p_nom"] * config["transmission_limit"][planning_horizon]
    #Limit H2 pipelines expansion
    # h2_links = n.links[(n.links.carrier == "H2 pipeline") & n.links.p_nom_extendable]

    # for idx, link in h2_links.iterrows():
    #     c0 = link.bus0[:2]
    #     c1 = link.bus1[:2]
    #     countries = {c0, c1}
    #     #Assumptions from EU hydrogen backbone https://ehb.eu/files/downloads/2020_European-Hydrogen-Backbone_Report.pdf
    #     #Mega-Transit Hubs (13 GW)
    #     if countries in [{"NL", "DE"}, {"BE", "DE"}, {"DK", "DE"}, {"ES", "FR"}, {"FR", "DE"}, {"NO", "DE"}, {"GB", "NL"}]:
    #         cap = 13000
            
    #     #Internal domestic buses, assuming same 13GW as all are in high transit hubs
    #     elif c0 == c1:
    #         cap = 13000
            
    #     #Central & Southern Main Transit (7 GW)
    #     elif countries in [{"AT", "DE"}, {"CZ", "DE"}, {"SK", "CZ"}, {"IT", "AT"}, {"CH", "DE"}, {"PL", "DE"}, {"BE", "NL"}, {"BE", "FR"}]:
    #         cap = 7000
            
    #     #Long Subsea / Nordic Marine Links (3 GW)
    #     elif "SE" in countries or "FI" in countries or "EE" in countries or "IE" in countries or ("GB" in countries and "NO" in countries):
    #         cap = 3000
            
    #     #Minor / Alpine passes (1.5 GW)
    #     elif "LU" in countries or "CH" in countries:
    #         cap = 1500
            
    #     #Eastern / Balkan regional links (3.5 GW)
    #     else:
    #         cap = 3500
            
    #     n.links.at[idx, "p_nom_max"] = cap

    # #retrofitted pipelines also limiting the expansion
    # h2_retrofit_mask = (n.links.carrier == "H2 pipeline retrofitted") & n.links.p_nom_extendable
    # n.links.loc[h2_retrofit_mask, "p_nom_max"] *= config["H2_pipeline_limit"]
    # h2_links_mask = (n.links.carrier == "H2 pipeline") & n.links.p_nom_extendable
    # n.links.loc[h2_links_mask, "p_nom_max"] *= config["H2_pipeline_limit"]
    
    return n

def remove_hydrogen_demands(n: pypsa.Network):
    '''
    This function removes hydrogen and hydrogen based molecule demands and connecting links, store
    and buses for baseline without hydrogen scenario.
    '''                   
    remove_carriers = ["H2 for industry", "industry methanol","NH3","shipping methanol","land transport fuel cell","H2 for shipping"]
    n.loads = n.loads[~n.loads["carrier"].isin(remove_carriers)]
    n.loads_t.p_set = n.loads_t.p_set.loc[
    :,
    ~n.loads_t.p_set.columns.str.contains("land transport fuel cell")]
    links_to_remove = [
        "Haber-Bosch",
        "H2 Electrolysis",
        "ammonia cracker",
        "industry methanol",
        "methanolisation",
        "Fischer-Tropsch",
        "shipping methanol",
        "H2 for shipping",
        "SMR",
        "vre H2 Electrolysis"
    ]
    removed_links = n.links.index[
        n.links.carrier.isin(links_to_remove)
    ]
    n.links = n.links.drop(removed_links)
    buses_to_remove = [
        "H2",
        "NH3",
        "methanol",
        "industry methanol",
        "shipping methanol",
    ]
    removed_buses = n.buses.index[
        n.buses.carrier.isin(buses_to_remove)
    ]
    n.buses = n.buses.drop(removed_buses)
    # Remove every component still attached to a removed bus on ANY port.
    # A link whose bus was dropped has no nodal balance on that port and
    # becomes a free energy source/sink (e.g. H2 turbines generating
    # electricity from a non-existent H2 bus), silently distorting the solve.
    port_cols = [c for c in n.links.columns if c.startswith("bus")]
    dangling = pd.Series(False, index=n.links.index)
    for c in port_cols:
        dangling |= n.links[c].isin(removed_buses)
    n.links = n.links[~dangling]
    n.generators = n.generators[~n.generators.bus.isin(removed_buses)]
    n.loads = n.loads[~n.loads.bus.isin(removed_buses)]
    stores_to_remove = [
        "H2 Store",
        "ammonia store",
        "methanol",
    ]
    removed_stores = n.stores.index[
        n.stores.carrier.isin(stores_to_remove)
        | n.stores.bus.isin(removed_buses)
    ]
    n.stores = n.stores.drop(removed_stores)
    # As all e-fuel technologies have been removed, subtract the baseline Fischer-Tropsch
    # share from oil-product loads. Use all major EU-oil consumers in the denominator
    # (not only aviation/shipping/agriculture) so FT output is not over-attributed when
    # later horizons route more e-fuels to land transport and naphtha.
    baseline_network = pypsa.Network(snakemake.input.baseline_network)
    w = baseline_network.snapshot_weightings.generators
    loads_t = baseline_network.loads_t.p
    ft = -(w @ baseline_network.links_t.p1.filter(like="Fischer-Tropsch")).sum().sum()/1e6
    aviation = (w @ loads_t.filter(like="kerosene for aviation")).sum().sum()/1e6
    ship_oil = (w @ loads_t.filter(like="shipping oil")).sum().sum()/1e6
    agri_oil = (w @ loads_t.filter(like="agriculture machinery oil")).sum().sum()/1e6
    land_oil = (w @ loads_t.filter(like="land transport oil")).sum().sum()/1e6
    naphtha = (w @ loads_t.filter(like="naphtha for industry")).sum().sum()/1e6
    if not land_oil:
        land_oil = (
            w @ baseline_network.loads_t.p_set.filter(like="land transport oil")
        ).sum().sum() / 1e6
    oil_demands = aviation + ship_oil + agri_oil + land_oil + naphtha
    ft_percentage = ft / oil_demands if oil_demands else 0
    per_without_efuels = max(0, 1 - ft_percentage)
    static_oil_cols = n.loads.index[
        n.loads.index.str.contains(
            "kerosene for aviation|shipping oil|agriculture machinery oil|"
            "naphtha for industry",
            regex=True,
        )
    ]
    n.loads.loc[static_oil_cols, "p_set"] *= per_without_efuels
    land_transport_cols = n.loads_t.p_set.filter(like="land transport oil").columns
    if len(land_transport_cols):
        n.loads_t.p_set.loc[:, land_transport_cols] *= per_without_efuels
    # Removing e-fuels produced by methanation from gas-for-industry demands.
    # Skip when the baseline reference has no dispatch (e.g. failed 2050 solve).
    methanation = -(w @ baseline_network.links_t.p1.filter(like="Sabatier")).sum().sum()/1e6
    gas_ind = (w @ loads_t.filter(like="gas for industry")).sum().sum()/1e6
    if gas_ind:
        meth_percentage = methanation / gas_ind
        gas_without_efuels = max(0, 1 - meth_percentage)
        cols_gas = n.loads.p_set.filter(like="gas for industry").index
        n.loads.loc[cols_gas, "p_set"] *= gas_without_efuels
    # NOTE: district-heating demand is intentionally NOT reduced. Removing the
    # H2/e-fuel links already removes their waste-heat *supply* to the heat
    # buses; the end-use heat demand is unchanged in the counterfactual and
    # must be covered by other heating technologies. (A previous version
    # subtracted the baseline waste heat from urban central heat loads, which
    # double-removed ~70 TWh/yr of heat demand.)
    #making all heat sector technologies minimum investmnet according to baseline scenario
    heating_tech_carriers = ["urban central water tanks charger","urban central water tanks discharger",
                              "urban central water pits charger","urban central water pits discharger",
                              "urban central air heat pump","urban central resistive heater","urban central gas boiler",
                              "geothermal district heat","rural air heat pump","rural biomass boiler",
                              "rural gas boiler","rural ground heat pump","rural resistive heater",
                              "rural water tanks charger","rural water tanks discharger","urban decentral air heat pump",
                              "urban decentral biomass boiler","urban decentral gas boiler","urban decentral resistive heater",
                              "urban decentral water tanks charger","urban decentral water tanks discharger",
                              "urban central solid biomass CHP CC","urban central solid biomass CHP",
                              "urban central gas CHP CC","urban central gas CHP"]
    mask = (baseline_network.links.carrier.isin(heating_tech_carriers) & (baseline_network.links.build_year == investment_year))
    idx = baseline_network.links.index[mask].intersection(n.links.index)
    n.links.loc[idx, "p_nom_min"] = (baseline_network.links.loc[idx, "p_nom_opt"].round(2))
    
    return n



def add_CCL_constraints(
    n: pypsa.Network, config: dict, planning_horizons: str | None
) -> None:
    """
    Add CCL (country & carrier limit) constraint to the network.

    Add minimum and maximum levels of generator nominal capacity per carrier
    for individual countries. Opts and path for agg_p_nom_minmax.csv must be defined
    in config.yaml. Default file is available at data/agg_p_nom_minmax.csv.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance
    config : dict
        Configuration dictionary
    planning_horizons : str, optional
        The current planning horizon year or None in perfect foresight

    Example
    -------
    scenario:
        opts: [Co2L-CCL-24h]
    electricity:
        agg_p_nom_limits: data/agg_p_nom_minmax.csv
    """

    assert planning_horizons is not None, (
        "add_CCL_constraints are not implemented for perfect foresight, yet"
    )

    agg_p_nom_minmax = pd.read_csv(
        config["solving"]["agg_p_nom_limits"]["file"], index_col=[0, 1], header=[0, 1]
    )[planning_horizons]
    logger.info("Adding generation capacity constraints per carrier and country")
    p_nom = n.model["Generator-p_nom"]

    gens = n.generators.query("p_nom_extendable")

    if not PYPSA_V1:
        gens = gens.rename_axis(index="Generator-ext")

    if config["solving"]["agg_p_nom_limits"]["agg_offwind"]:
        rename_offwind = {
            "offwind-ac": "offwind-all",
            "offwind-dc": "offwind-all",
            "offwind-float": "offwind-all",
            "offwind": "offwind-all",
        }
        gens = gens.replace(rename_offwind)
    if config["solving"]["agg_p_nom_limits"]["agg_solar"]:
        rename_solar = {
            "solar": "solar-all",
            "solar-hsat": "solar-all",
            "solar rooftop": "solar-all",
        }
        gens = gens.replace(rename_solar)
    grouper = pd.concat([gens.bus.map(n.buses.country), gens.carrier], axis=1)
    lhs = p_nom.groupby(grouper).sum().rename(bus="country")

    if config["solving"]["agg_p_nom_limits"]["include_existing"]:
        gens_cst = n.generators.query("~p_nom_extendable").rename_axis(
            index="Generator-cst"
        )
        gens_cst = gens_cst[
            (gens_cst["build_year"] + gens_cst["lifetime"]) >= int(planning_horizons)
        ]
        if config["solving"]["agg_p_nom_limits"]["agg_offwind"]:
            gens_cst = gens_cst.replace(rename_offwind)
        if config["solving"]["agg_p_nom_limits"]["agg_solar"]:
            gens_cst = gens_cst.replace(rename_solar)
        rhs_cst = (
            pd.concat(
                [gens_cst.bus.map(n.buses.country), gens_cst[["carrier", "p_nom"]]],
                axis=1,
            )
            .groupby(["bus", "carrier"])
            .sum()
        )
        rhs_cst.index = rhs_cst.index.rename({"bus": "country"})
        rhs_min = agg_p_nom_minmax["min"].dropna()
        idx_min = rhs_min.index.join(rhs_cst.index, how="left")
        rhs_min = rhs_min.reindex(idx_min).fillna(0)
        rhs = (rhs_min - rhs_cst.reindex(idx_min).fillna(0).p_nom).dropna()
        rhs[rhs < 0] = 0
        minimum = xr.DataArray(rhs).rename(dim_0="group")
    else:
        minimum = xr.DataArray(agg_p_nom_minmax["min"].dropna()).rename(dim_0="group")

    index = minimum.indexes["group"].intersection(lhs.indexes["group"])
    if not index.empty:
        n.model.add_constraints(
            lhs.sel(group=index) >= minimum.loc[index], name="agg_p_nom_min"
        )

    if config["solving"]["agg_p_nom_limits"]["include_existing"]:
        rhs_max = agg_p_nom_minmax["max"].dropna()
        idx_max = rhs_max.index.join(rhs_cst.index, how="left")
        rhs_max = rhs_max.reindex(idx_max).fillna(0)
        rhs = (rhs_max - rhs_cst.reindex(idx_max).fillna(0).p_nom).dropna()
        rhs[rhs < 0] = 0
        maximum = xr.DataArray(rhs).rename(dim_0="group")
    else:
        maximum = xr.DataArray(agg_p_nom_minmax["max"].dropna()).rename(dim_0="group")

    index = maximum.indexes["group"].intersection(lhs.indexes["group"])
    if not index.empty:
        n.model.add_constraints(
            lhs.sel(group=index) <= maximum.loc[index], name="agg_p_nom_max"
        )


def add_EQ_constraints(n, o, scaling=1e-1):
    """
    Add equity constraints to the network.

    Currently this is only implemented for the electricity sector only.

    Opts must be specified in the config.yaml.

    Parameters
    ----------
    n : pypsa.Network
    o : str

    Example
    -------
    scenario:
        opts: [Co2L-EQ0.7-24h]

    Require each country or node to on average produce a minimal share
    of its total electricity consumption itself. Example: EQ0.7c demands each country
    to produce on average at least 70% of its consumption; EQ0.7 demands
    each node to produce on average at least 70% of its consumption.
    """
    # TODO: Generalize to cover myopic and other sectors?
    float_regex = r"[0-9]*\.?[0-9]+"
    level = float(re.findall(float_regex, o)[0])
    if o[-1] == "c":
        ggrouper = n.generators.bus.map(n.buses.country)
        lgrouper = n.loads.bus.map(n.buses.country)
        sgrouper = n.storage_units.bus.map(n.buses.country)
    else:
        ggrouper = n.generators.bus
        lgrouper = n.loads.bus
        sgrouper = n.storage_units.bus
    load = (
        n.snapshot_weightings.generators
        @ n.loads_t.p_set.groupby(lgrouper, axis=1).sum()
    )
    inflow = (
        n.snapshot_weightings.stores
        @ n.storage_units_t.inflow.groupby(sgrouper, axis=1).sum()
    )
    inflow = inflow.reindex(load.index).fillna(0.0)
    rhs = scaling * (level * load - inflow)
    p = n.model["Generator-p"]
    lhs_gen = (
        (p * (n.snapshot_weightings.generators * scaling))
        .groupby(ggrouper.to_xarray())
        .sum()
        .sum("snapshot")
    )
    # TODO: double check that this is really needed, why do have to subtract the spillage
    if not n.storage_units_t.inflow.empty:
        spillage = n.model["StorageUnit-spill"]
        lhs_spill = (
            (spillage * (-n.snapshot_weightings.stores * scaling))
            .groupby(sgrouper.to_xarray())
            .sum()
            .sum("snapshot")
        )
        lhs = lhs_gen + lhs_spill
    else:
        lhs = lhs_gen
    n.model.add_constraints(lhs >= rhs, name="equity_min")


def add_BAU_constraints(n: pypsa.Network, config: dict) -> None:
    """
    Add business-as-usual (BAU) constraints for minimum capacities.

    Parameters
    ----------
    n : pypsa.Network
        PyPSA network instance
    config : dict
        Configuration dictionary containing BAU minimum capacities
    """
    mincaps = pd.Series(config["electricity"]["BAU_mincapacities"])
    p_nom = n.model["Generator-p_nom"]
    ext_i = n.generators.query("p_nom_extendable")
    ext_carrier_i = xr.DataArray(ext_i.carrier)
    if not PYPSA_V1:
        ext_carrier_i = ext_carrier_i.rename_axis("Generator-ext")
    lhs = p_nom.groupby(ext_carrier_i).sum()
    rhs = mincaps[lhs.indexes["carrier"]].rename_axis("carrier")
    n.model.add_constraints(lhs >= rhs, name="bau_mincaps")


# TODO: think about removing or make per country
def add_SAFE_constraints(n, config):
    """
    Add a capacity reserve margin of a certain fraction above the peak demand.
    Renewable generators and storage do not contribute. Ignores network.

    Parameters
    ----------
        n : pypsa.Network
        config : dict

    Example
    -------
    config.yaml requires to specify opts:

    scenario:
        opts: [Co2L-SAFE-24h]
    electricity:
        SAFE_reservemargin: 0.1
    Which sets a reserve margin of 10% above the peak demand.
    """
    peakdemand = n.loads_t.p_set.sum(axis=1).max()
    margin = 1.0 + config["electricity"]["SAFE_reservemargin"]
    reserve_margin = peakdemand * margin
    conventional_carriers = config["electricity"]["conventional_carriers"]  # noqa: F841
    ext_gens_i = n.generators.query(
        "carrier in @conventional_carriers & p_nom_extendable"
    ).index
    p_nom = n.model["Generator-p_nom"].loc[ext_gens_i]
    lhs = p_nom.sum()
    exist_conv_caps = n.generators.query(
        "~p_nom_extendable & carrier in @conventional_carriers"
    ).p_nom.sum()
    rhs = reserve_margin - exist_conv_caps
    n.model.add_constraints(lhs >= rhs, name="safe_mintotalcap")


def add_operational_reserve_margin(n, sns, config):
    """
    Build reserve margin constraints based on the formulation given in
    https://genxproject.github.io/GenX/dev/core/#Reserves.

    Parameters
    ----------
        n : pypsa.Network
        sns: pd.DatetimeIndex
        config : dict

    Example:
    --------
    config.yaml requires to specify operational_reserve:
    operational_reserve: # like https://genxproject.github.io/GenX/dev/core/#Reserves
        activate: true
        epsilon_load: 0.02 # percentage of load at each snapshot
        epsilon_vres: 0.02 # percentage of VRES at each snapshot
        contingency: 400000 # MW
    """
    reserve_config = config["electricity"]["operational_reserve"]
    EPSILON_LOAD = reserve_config["epsilon_load"]
    EPSILON_VRES = reserve_config["epsilon_vres"]
    CONTINGENCY = reserve_config["contingency"]

    # Reserve Variables
    n.model.add_variables(
        0, np.inf, coords=[sns, n.generators.index], name="Generator-r"
    )
    reserve = n.model["Generator-r"]
    summed_reserve = reserve.sum("Generator")

    # Share of extendable renewable capacities
    ext_i = n.generators.query("p_nom_extendable").index
    vres_i = n.generators_t.p_max_pu.columns
    if not ext_i.empty and not vres_i.empty:
        capacity_factor = n.generators_t.p_max_pu[vres_i.intersection(ext_i)]
        p_nom_vres = n.model["Generator-p_nom"].loc[vres_i.intersection(ext_i)]
        if not PYPSA_V1:
            p_nom_vres = p_nom_vres.rename({"Generator-ext": "Generator"})
        lhs = summed_reserve + (
            p_nom_vres * (-EPSILON_VRES * xr.DataArray(capacity_factor))
        ).sum("Generator")

        # Total demand per t
        demand = get_as_dense(n, "Load", "p_set").sum(axis=1)

        # VRES potential of non extendable generators
        capacity_factor = n.generators_t.p_max_pu[vres_i.difference(ext_i)]
        renewable_capacity = n.generators.p_nom[vres_i.difference(ext_i)]
        potential = (capacity_factor * renewable_capacity).sum(axis=1)

        # Right-hand-side
        rhs = EPSILON_LOAD * demand + EPSILON_VRES * potential + CONTINGENCY

        n.model.add_constraints(lhs >= rhs, name="reserve_margin")

    # additional constraint that capacity is not exceeded
    gen_i = n.generators.index
    ext_i = n.generators.query("p_nom_extendable").index
    fix_i = n.generators.query("not p_nom_extendable").index

    dispatch = n.model["Generator-p"]
    reserve = n.model["Generator-r"]

    capacity_variable = n.model["Generator-p_nom"]
    if not PYPSA_V1:
        capacity_variable = capacity_variable.rename({"Generator-ext": "Generator"})
    capacity_fixed = n.generators.p_nom[fix_i]

    p_max_pu = get_as_dense(n, "Generator", "p_max_pu")

    lhs = dispatch + reserve - capacity_variable * xr.DataArray(p_max_pu[ext_i])

    rhs = (p_max_pu[fix_i] * capacity_fixed).reindex(columns=gen_i, fill_value=0)

    n.model.add_constraints(lhs <= rhs, name="Generator-p-reserve-upper")


def add_TES_energy_to_power_ratio_constraints(n: pypsa.Network) -> None:
    """
    Add TES constraints to the network.

    For each TES storage unit, enforce:
        Store-e_nom - etpr * Link-p_nom == 0

    Parameters
    ----------
    n : pypsa.Network
        A PyPSA network with TES and heating sectors enabled.

    Raises
    ------
    ValueError
        If no valid TES storage or charger links are found.
    RuntimeError
        If the TES storage and charger indices do not align.
    """
    indices_charger_p_nom_extendable = n.links.index[
        n.links.index.str.contains("water tanks charger|water pits charger")
        & n.links.p_nom_extendable
    ]
    indices_stores_e_nom_extendable = n.stores.index[
        n.stores.index.str.contains("water tanks|water pits")
        & n.stores.e_nom_extendable
    ]

    if indices_charger_p_nom_extendable.empty or indices_stores_e_nom_extendable.empty:
        logger.warning(
            "No valid extendable charger links or stores found for TES energy-to-power constraints.Not enforcing TES energy-to-power ratio constraints!"
        )
        return

    energy_to_power_ratio_values = n.links.loc[
        indices_charger_p_nom_extendable, "energy to power ratio"
    ].values

    linear_expr_list = []
    for charger, tes, energy_to_power_value in zip(
        indices_charger_p_nom_extendable,
        indices_stores_e_nom_extendable,
        energy_to_power_ratio_values,
    ):
        charger_var = n.model["Link-p_nom"].loc[charger]
        if not tes == charger.replace(" charger", ""):
            # e.g. "DE0 0 urban central water tanks charger-2050" -> "DE0 0 urban central water tanks-2050"
            raise RuntimeError(
                f"Charger {charger} and TES {tes} do not match. "
                "Ensure that the charger and TES are in the same location and refer to the same technology."
            )
        store_var = n.model["Store-e_nom"].loc[tes]
        linear_expr = store_var - energy_to_power_value * charger_var
        linear_expr_list.append(linear_expr)

    # Merge the individual expressions
    dim = "Store-ext, Link-ext" if PYPSA_V1 else "name"
    merged_expr = linopy.expressions.merge(
        linear_expr_list, dim=dim, cls=type(linear_expr_list[0])
    )

    n.model.add_constraints(merged_expr == 0, name="TES_energy_to_power_ratio")


def add_TES_charger_ratio_constraints(n: pypsa.Network) -> None:
    """
    Add TES charger ratio constraints.

    For each TES unit, enforce:
        Link-p_nom(charger) - efficiency * Link-p_nom(discharger) == 0

    Parameters
    ----------
    n : pypsa.Network
        A PyPSA network with TES and heating sectors enabled.

    Raises
    ------
    ValueError
        If no valid TES discharger or charger links are found.
    RuntimeError
        If the charger and discharger indices do not align.
    """
    indices_charger_p_nom_extendable = n.links.index[
        n.links.index.str.contains(
            "water tanks charger|water pits charger|aquifer thermal energy storage charger"
        )
        & n.links.p_nom_extendable
    ]
    indices_discharger_p_nom_extendable = n.links.index[
        n.links.index.str.contains(
            "water tanks discharger|water pits discharger|aquifer thermal energy storage discharger"
        )
        & n.links.p_nom_extendable
    ]

    if (
        indices_charger_p_nom_extendable.empty
        or indices_discharger_p_nom_extendable.empty
    ):
        logger.warning(
            "No valid extendable TES discharger or charger links found for TES charger ratio constraints. Not enforcing TES charger_ratio constraints."
        )
        return

    for charger, discharger in zip(
        indices_charger_p_nom_extendable, indices_discharger_p_nom_extendable
    ):
        if not charger.replace(" charger", " ") == discharger.replace(
            " discharger", " "
        ):
            # e.g. "DE0 0 urban central water tanks charger-2050" -> "DE0 0 urban central water tanks-2050"
            raise RuntimeError(
                f"Charger {charger} and discharger {discharger} do not match. "
                "Ensure that the charger and discharger are in the same location and refer to the same technology."
            )

    eff_discharger = n.links.efficiency[indices_discharger_p_nom_extendable].values
    lhs = (
        n.model["Link-p_nom"].loc[indices_charger_p_nom_extendable]
        - n.model["Link-p_nom"].loc[indices_discharger_p_nom_extendable]
        * eff_discharger
    )

    n.model.add_constraints(lhs == 0, name="TES_charger_ratio")


def add_battery_constraints(n):
    """
    Add constraint ensuring that charger = discharger, i.e.
    1 * charger_size - efficiency * discharger_size = 0
    """
    if not n.links.p_nom_extendable.any():
        return

    discharger_bool = n.links.index.str.contains("battery discharger")
    charger_bool = n.links.index.str.contains("battery charger")

    dischargers_ext = n.links[discharger_bool].query("p_nom_extendable").index
    chargers_ext = n.links[charger_bool].query("p_nom_extendable").index

    eff = n.links.efficiency[dischargers_ext].values
    lhs = (
        n.model["Link-p_nom"].loc[chargers_ext]
        - n.model["Link-p_nom"].loc[dischargers_ext] * eff
    )

    n.model.add_constraints(lhs == 0, name="Link-charger_ratio")


def add_lossy_bidirectional_link_constraints(n):
    if not n.links.p_nom_extendable.any() or not any(n.links.get("reversed", [])):
        return

    carriers = n.links.loc[n.links.reversed, "carrier"].unique()  # noqa: F841
    backwards = n.links.query(
        "carrier in @carriers and p_nom_extendable and reversed and active"
    ).index
    forwards = backwards.str.replace("-reversed", "")
    lhs = n.model["Link-p_nom"].loc[backwards]
    rhs = n.model["Link-p_nom"].loc[forwards]
    n.model.add_constraints(lhs == rhs, name="Link-bidirectional_sync")


def add_chp_constraints(n):
    electric = (
        n.links.index.str.contains("urban central")
        & n.links.index.str.contains("CHP")
        & n.links.index.str.contains("electric")
    )
    heat = (
        n.links.index.str.contains("urban central")
        & n.links.index.str.contains("CHP")
        & n.links.index.str.contains("heat")
    )

    electric_ext = n.links[electric].query("p_nom_extendable").index
    heat_ext = n.links[heat].query("p_nom_extendable").index

    electric_fix = n.links[electric].query("~p_nom_extendable").index
    heat_fix = n.links[heat].query("~p_nom_extendable").index

    p = n.model["Link-p"]  # dimension: [time, link]

    # output ratio between heat and electricity and top_iso_fuel_line for extendable
    if not electric_ext.empty:
        p_nom = n.model["Link-p_nom"]

        lhs = (
            p_nom.loc[electric_ext]
            * (n.links.p_nom_ratio * n.links.efficiency)[electric_ext].values
            - p_nom.loc[heat_ext] * n.links.efficiency[heat_ext].values
        )
        n.model.add_constraints(lhs == 0, name="chplink-fix_p_nom_ratio")

        rename = {} if PYPSA_V1 else {"Link-ext": "Link"}
        lhs = (
            p.loc[:, electric_ext]
            + p.loc[:, heat_ext]
            - p_nom.rename(rename).loc[electric_ext]
        )
        n.model.add_constraints(lhs <= 0, name="chplink-top_iso_fuel_line_ext")

    # top_iso_fuel_line for fixed
    if not electric_fix.empty:
        lhs = p.loc[:, electric_fix] + p.loc[:, heat_fix]
        rhs = n.links.p_nom[electric_fix]
        n.model.add_constraints(lhs <= rhs, name="chplink-top_iso_fuel_line_fix")

    # back-pressure
    if not electric.empty:
        lhs = (
            p.loc[:, heat] * (n.links.efficiency[heat] * n.links.c_b[electric].values)
            - p.loc[:, electric] * n.links.efficiency[electric]
        )
        n.model.add_constraints(lhs <= rhs, name="chplink-backpressure")


def add_pipe_retrofit_constraint(n):
    """
    Add constraint for retrofitting existing CH4 pipelines to H2 pipelines.
    """
    if "reversed" not in n.links.columns:
        n.links["reversed"] = False
    gas_pipes_i = n.links.query(
        "carrier == 'gas pipeline' and p_nom_extendable and ~reversed and active"
    ).index
    h2_retrofitted_i = n.links.query(
        "carrier == 'H2 pipeline retrofitted' and p_nom_extendable and ~reversed and active"
    ).index

    if h2_retrofitted_i.empty or gas_pipes_i.empty:
        return

    p_nom = n.model["Link-p_nom"]

    CH4_per_H2 = 1 / n.config["sector"]["H2_retrofit_capacity_per_CH4"]
    lhs = p_nom.loc[gas_pipes_i] + CH4_per_H2 * p_nom.loc[h2_retrofitted_i]
    rhs = n.links.p_nom[gas_pipes_i]
    if not PYPSA_V1:
        rhs = rhs.rename_axis("Link-ext")

    n.model.add_constraints(lhs == rhs, name="Link-pipe_retrofit")


def add_flexible_egs_constraint(n):
    """
    Upper bounds the charging capacity of the geothermal reservoir according to
    the well capacity.
    """
    well_index = n.links.loc[n.links.carrier == "geothermal heat"].index
    storage_index = n.storage_units.loc[
        n.storage_units.carrier == "geothermal heat"
    ].index

    p_nom_rhs = n.model["Link-p_nom"].loc[well_index]
    p_nom_lhs = n.model["StorageUnit-p_nom"].loc[storage_index]

    n.model.add_constraints(
        p_nom_lhs <= p_nom_rhs,
        name="upper_bound_charging_capacity_of_geothermal_reservoir",
    )


def add_import_limit_constraint(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    Add constraint for limiting green energy imports (synthetic and biomass).
    Does not include fossil fuel imports.
    """

    nyears = n.snapshot_weightings.generators.sum() / 8760

    import_links = n.links.loc[n.links.carrier.str.contains("import")].index
    import_gens = n.generators.loc[n.generators.carrier.str.contains("import")].index

    limit = n.config["sector"]["imports"]["limit"][investment_year]
    limit_sense = n.config["sector"]["imports"]["limit_sense"]

    if (import_links.empty and import_gens.empty) or not np.isfinite(limit):
        return

    weightings = n.snapshot_weightings.loc[sns, "generators"]

    # everything needs to be in MWh_fuel
    eff = n.links.loc[import_links, "efficiency"]

    p_gens = n.model["Generator-p"].loc[sns, import_gens]
    p_links = n.model["Link-p"].loc[sns, import_links]

    lhs = (p_gens * weightings).sum() + (p_links * eff * weightings).sum()

    rhs = limit * 1e6 * nyears

    n.model.add_constraints(lhs, limit_sense, rhs, name="import_limit")


def add_co2_atmosphere_constraint(n, snapshots):
    glcs = n.global_constraints[n.global_constraints.type == "co2_atmosphere"]

    if glcs.empty:
        return
    for name, glc in glcs.iterrows():
        carattr = glc.carrier_attribute
        emissions = n.carriers.query(f"{carattr} != 0")[carattr]

        if emissions.empty:
            continue

        # stores
        bus_carrier = n.stores.bus.map(n.buses.carrier)
        stores = n.stores[bus_carrier.isin(emissions.index) & ~n.stores.e_cyclic]
        if not stores.empty:
            last_i = snapshots[-1]
            lhs = n.model["Store-e"].loc[last_i, stores.index]
            rhs = glc.constant

            n.model.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{name}")

def add_co2limit_country(n, limit_countries, nyears=1.0):
    """
    Add a set of emissions limit constraints for specified countries.
    The countries and emissions limits are specified in the config file entry 'co2_budget_country_{investment_year}'.
    Parameters
    ----------
    n : pypsa.Network
    config : dict
    limit_countries : dict
    nyears: float, optional
        Used to scale the emissions constraint to the number of snapshots of the base network.
    """
    logger.info(f"Adding CO2 budget limit for each country as per unit of 1990 levels")

    countries = n.config["countries"]

    # TODO: import function from prepare_sector_network? Move to common place?
    sectors = determine_emission_sectors(options)

    # convert Mt to tCO2
    co2_totals = 1e6 * pd.read_csv(snakemake.input.co2_totals_name, index_col=0)
    co2_limit_countries = co2_totals.loc[countries, sectors].sum(axis=1)
    co2_limit_countries = co2_limit_countries.loc[
        co2_limit_countries.index.isin(limit_countries.keys())
    ]
    co2_limit_countries *= co2_limit_countries.index.map(limit_countries) * nyears
    co2_limit_countries = (co2_limit_countries)

    p = n.model["Link-p"]  # dimension: (time, component)

    # NB: Most country-specific links retain their locational information in bus1 (except for DAC, where it is in bus2, and process emissions, where it is in bus0)
    country = n.links.bus1.map(n.buses.location).map(n.buses.country)
    
    country_DAC = (
        n.links[n.links.carrier == "DAC"]
        .bus3.map(n.buses.location)
        .map(n.buses.country)
    )
    country[country_DAC.index] = country_DAC
    patterns = ["process emissions", "HVC to air", "electrobiofuels","unsustainable bioliquids","biomass-to-methanol","biomass to liquid"]

    for pattern in patterns:
      source = n.links[n.links.carrier.str.contains(pattern)].bus0.map(n.buses.location).map(n.buses.country)
      country[source.index] = source
    mask = country.isna() | (country == '')
    country[mask] = country[mask].index.str[:2]
    country = country[country != 'EU']
    lhs = []
    for port in [col[3:] for col in n.links if col.startswith("bus")]:
        if port == str(0):
            efficiency = (
                n.links["efficiency"].apply(lambda x: 1.0).rename("efficiency0")
            )
        elif port == str(1):
            efficiency = n.links["efficiency"]
        else:
            efficiency = n.links[f"efficiency{port}"]
        mask = n.links[f"bus{port}"].map(n.buses.carrier).eq("co2")

        idx = n.links[mask].index
        exclude = ["EU oil refining", "EU methanol import", "EU oil import"]
        idx = idx[~np.isin(idx, exclude)]
        grouping = country.loc[idx]

        if not grouping.isnull().all():
            expr = (
                (p.loc[:, idx] * efficiency[idx])
                .groupby(grouping, axis=1)
                .sum()
                * n.snapshot_weightings.generators
            ).sum(dims="snapshot")
            lhs.append(expr)

    lhs = sum(lhs)  # dimension: (country)
    lhs = lhs.rename({list(lhs.dims)[0]: "snapshot"})
    rhs = pd.Series(co2_limit_countries)  # dimension: (country)
    for ct in lhs.indexes["snapshot"]:
        n.model.add_constraints(
            lhs.loc[ct] <= rhs[ct],
            name=f"GlobalConstraint-co2_limit_per_country{ct}",
        )
        n.add(
            "GlobalConstraint",
            f"co2_limit_per_country{ct}",
            constant=rhs[ct],
            sense="<=",
            type="",
        )

def add_co2price_country(n, co2_price_countries, nyears=1.0):
    """
    Add a CO2 price per country by internalizing emissions into the objective.

    Parameters
    ----------
    n : pypsa.Network
    co2_price_countries : dict
        CO2 price in €/tCO2 per country (keys must match country codes)
    nyears : float, optional
        Scaling factor for snapshot weighting
    """

    logger.info("Adding CO2 price per country to objective function")
    p = n.model["Link-p"]  # dimension: (time, component)

    # NB: Most country-specific links retain their locational information in bus1 (except for DAC, where it is in bus2, and process emissions, where it is in bus0)
    country = n.links.bus1.map(n.buses.location).map(n.buses.country)
    
    country_DAC = (
        n.links[n.links.carrier == "DAC"]
        .bus3.map(n.buses.location)
        .map(n.buses.country)
    )
    country[country_DAC.index] = country_DAC
    patterns = ["process emissions", "HVC to air", "electrobiofuels","unsustainable bioliquids","biomass-to-methanol","biomass to liquid"]

    for pattern in patterns:
      source = n.links[n.links.carrier.str.contains(pattern)].bus0.map(n.buses.location).map(n.buses.country)
      country[source.index] = source
    mask = country.isna() | (country == '')
    country[mask] = country[mask].index.str[:2]
    country = country[country != 'EU']
    lhs = []
    for port in [col[3:] for col in n.links if col.startswith("bus")]:
        if port == str(0):
            efficiency = (
                n.links["efficiency"].apply(lambda x: 1.0).rename("efficiency0")
            )
        elif port == str(1):
            efficiency = n.links["efficiency"]
        else:
            efficiency = n.links[f"efficiency{port}"]
        mask = n.links[f"bus{port}"].map(n.buses.carrier).eq("co2")

        idx = n.links[mask].index
        exclude = ["EU oil refining", "EU methanol import", "EU oil import"]
        idx = idx[~np.isin(idx, exclude)]
        grouping = country.loc[idx]

        if not grouping.isnull().all():
            expr = (
                (p.loc[:, idx] * efficiency[idx])
                .groupby(grouping, axis=1)
                .sum()
                * n.snapshot_weightings.generators
            ).sum(dims="snapshot")
            lhs.append(expr)

    lhs = sum(lhs)  # dimension: (country)
    dim = list(lhs.dims)[0]
    price = pd.Series(co2_price_countries)
    # align with lhs countries
    price = price.reindex(lhs.indexes[dim]).fillna(0.0)
    price_da = xr.DataArray(price, dims=[dim])
    # total CO2 cost
    co2_cost = (lhs * price_da * nyears).sum(dim=dim)
    n.model.objective = n.model.objective + co2_cost
    n.meta = {**(n.meta or {}), "co2_prices": price.to_dict()}

    logger.info("CO2 pricing added to objective.")

def compute_country_co2_payments(n):
    """
    Compute realized per-country CO2 emission payments from a solved network.
    """
    co2_prices = (n.meta or {}).get("co2_prices")
    if not co2_prices:
        return {}

    country = n.links.bus1.map(n.buses.location).map(n.buses.country)

    country_DAC = (
        n.links[n.links.carrier == "DAC"]
        .bus3.map(n.buses.location)
        .map(n.buses.country)
    )
    country[country_DAC.index] = country_DAC
    patterns = ["process emissions", "HVC to air", "electrobiofuels","unsustainable bioliquids","biomass-to-methanol","biomass to liquid"]

    for pattern in patterns:
      source = n.links[n.links.carrier.str.contains(pattern)].bus0.map(n.buses.location).map(n.buses.country)
      country[source.index] = source
    mask = country.isna() | (country == '')
    country[mask] = country[mask].index.str[:2]
    country = country[country != 'EU']

    weights = n.snapshot_weightings.generators
    emissions_parts = []
    for port in [col[3:] for col in n.links if col.startswith("bus")]:
        if port == str(0):
            efficiency = (
                n.links["efficiency"].apply(lambda x: 1.0).rename("efficiency0")
            )
        elif port == str(1):
            efficiency = n.links["efficiency"]
        else:
            efficiency = n.links[f"efficiency{port}"]
        mask = n.links[f"bus{port}"].map(n.buses.carrier).eq("co2")

        idx = n.links[mask].index
        exclude = ["EU oil refining", "EU methanol import", "EU oil import"]
        idx = idx[~np.isin(idx, exclude)]
        grouping = country.loc[idx]

        if not grouping.isnull().all() and len(idx) > 0:
            flows = n.links_t.p0[idx].multiply(efficiency[idx], axis=1)
            expr = flows.groupby(grouping, axis=1).sum().mul(weights, axis=0).sum()
            emissions_parts.append(expr)

    if emissions_parts:
        emissions = sum(emissions_parts)
    else:
        emissions = pd.Series(dtype=float)

    price = pd.Series(co2_prices).reindex(emissions.index).fillna(0.0)
    payments = (emissions * price).to_dict()
    payments["total"] = sum(payments.values())
    return payments

def get_vre_share_carbon_intensity(country):
    '''
    This function gets the VRE share and co2 intensity (g/Kwh) in electricity grid power supply
    from the previous optimised planning horizon which is further used in the additionality constraint. 
    '''
    
    n=pypsa.Network(f"results/{study}/networks/base_s_{clusters}__{sector_opts}_{previous_year}.nc")
    params_file = pd.read_csv(snakemake.input.costs, index_col=[0, 1]).sort_index()
    co2_intensity = params_file["CO2 intensity"]
    co2_intensity.index = co2_intensity.index.droplevel(1)
    #convert t/MWh to g/kWh
    co2_intensity_g_kwh = co2_intensity * 1000

    generator_types = list(
      set(config["electricity"]["renewable_carriers"] + ["solar rooftop","ror"]))

    conv_types = list(
      set(config["electricity"]["conventional_carriers"] + ["urban central gas CHP",
            "urban central gas CHP CC","urban central solid biomass CHP","urban central solid biomass CHP CC",
            "H2 Fuel Cell","H2 turbine","geothermal organic rankine cycle"]))
    gens = n.generators.index[
      n.generators.carrier.isin(generator_types)
    ]

    links = n.links.index[
      n.links.carrier.isin(conv_types)
    ]
    
    hydro= n.storage_units.index[
      n.storage_units.carrier == "hydro"
    ]
    
    gen = (
      (n.snapshot_weightings.generators @ n.generators_t.p[gens])
      .filter(like=country)
      .groupby(
        [
            n.generators.loc[gens, "carrier"],
        ]
     )
    .sum()
    .mul(1e3)   #convert MWh to kWh
     )
    link = (
    (n.snapshot_weightings.generators @ -n.links_t.p1[links])
    .filter(like=country)
    .groupby(
        [
            n.links.loc[links, "carrier"],
        ]
    )
    .sum()
    .mul(1e3)   #convert MWh to kWh
    )
    hyd = (
      (n.snapshot_weightings.generators @ n.storage_units_t.p_dispatch[hydro])
      .filter(like=country)
      .groupby(
        [
            n.storage_units.loc[hydro, "carrier"],
        ]
     )
    .sum()
    .mul(1e3)   #convert MWh to kWh
     )
    tota_elec_grid_techs = pd.concat([gen, link, hyd])
    #align with grid carriers
    co2_intensity_g_kwh = co2_intensity_g_kwh.reindex(tota_elec_grid_techs.index).fillna(0)
    gas_chp_types = [
        "urban central gas CHP",
        "urban central gas CHP CC",
    ]

    existing_types = [t for t in gas_chp_types if t in co2_intensity_g_kwh.index]
    co2_intensity_g_kwh.loc[existing_types] = co2_intensity_g_kwh.loc["CCGT"]
    
    #mapping emissions
    gen_emissions = (
        (n.snapshot_weightings.generators @ n.generators_t.p[gens])
        .div(n.generators.loc[gens, "efficiency"])
        .filter(like=country).mul(1e3)
    )

    gen_emissions = (
        gen_emissions
        * n.generators.loc[gen_emissions.index, "carrier"].map(co2_intensity_g_kwh)
    )

    gen_emissions = (
        gen_emissions
        .groupby(n.generators.loc[gen_emissions.index, "carrier"])
        .sum()
    )
    link_emissions = (
        (n.snapshot_weightings.generators @ -n.links_t.p1[links])
        .div(n.links.loc[links, "efficiency"])
        .filter(like=country).mul(1e3)
    )

    link_emissions = (link_emissions
                     * n.links.loc[link_emissions.index, "carrier"].map(co2_intensity_g_kwh))

    link_emissions = (
        link_emissions
        .groupby(n.links.loc[link_emissions.index, "carrier"])
        .sum())
    emissions = pd.concat(
        [gen_emissions, link_emissions,]
    ).groupby(level=0).sum()
    
    #convert to g/MJ
    emissions = emissions / 3.6
    total_emissions = emissions.sum()

    renewable_carriers = generator_types + [
    "urban central solid biomass CHP",
    "urban central solid biomass CHP CC",
    "geothermal organic rankine cycle"]

    renewable_total = tota_elec_grid_techs[
    tota_elec_grid_techs.index.isin(renewable_carriers)
    ].sum()

    total_generation = tota_elec_grid_techs.sum()
    renewable_share = 100 * renewable_total / total_generation
    total_co2_intensity = total_emissions / total_generation
 
    return {
        "country": country,
        "renewable_share": renewable_share,
        "co2_intensity": total_co2_intensity,
    }   

def _rfnbo_active_countries(common_countries, criterion_type="both", log_prefix="RFNBO constraint"):
    if isinstance(common_countries, set):
        base = common_countries
    else:
        base = set(common_countries)

    #force flags based on the required criterion_type
    check_vre = constraints["activate_vre_share_criterion"] if criterion_type in ["both", "vre"] else False
    check_carbon = constraints["activate_carbon_intensity_criterion"] if criterion_type in ["both", "carbon"] else False

    if check_vre and check_carbon:
        if previous_horizon_data is None:
            active_countries = base
        else:
            level_vre = constraints["VRE_Share"]
            level_intensity = constraints["carbon_intensity"]
            stats = previous_horizon_data
            df = pd.DataFrame(stats).set_index("country")
            vre_noncompliant = df.index[df["renewable_share"] < level_vre]
            intensity_noncompliant = df.index[df["co2_intensity"] > level_intensity]
            eligible_countries = set(vre_noncompliant).intersection(
                set(intensity_noncompliant)
            )
            active_countries = base.intersection(eligible_countries)
        logger.info(
            f"{log_prefix} applied to countries "
            f"not complying with both VRE share and carbon intensity criterion: "
            f"{', '.join(sorted(active_countries))}"
        )
    elif check_vre:
        if previous_horizon_data is None:
            active_countries = base
        else:
            level_vre = constraints["VRE_Share"]
            vre_share = previous_horizon_data
            vre_df = pd.DataFrame(vre_share).set_index("country")
            eligible_countries = vre_df.index[vre_df["renewable_share"] < level_vre]
            active_countries = base.intersection(eligible_countries)
        logger.info(
            f"{log_prefix} applied to following countries "
            f"not having required VRE share in total generation: "
            f"{', '.join(sorted(active_countries))}"
        )
    elif check_carbon:
        if previous_horizon_data is None:
            active_countries = base
        else:
            level_intensity = constraints["carbon_intensity"]
            co2_intensity = previous_horizon_data
            intensity_df = pd.DataFrame(co2_intensity).set_index("country")
            eligible_countries = intensity_df.index[
                intensity_df["co2_intensity"] > level_intensity
            ]
            active_countries = base.intersection(eligible_countries)
        logger.info(
            f"{log_prefix} applied to following countries "
            f"not compliant to co2 intensity in total generation: "
            f"{', '.join(sorted(active_countries))}"
        )
    else:
        active_countries = base

    if isinstance(common_countries, pd.Index):
        return common_countries.intersection(active_countries)
    if isinstance(common_countries, set):
        return active_countries
    return sorted(active_countries)
    
    
def add_additionality_constraint(n: pypsa.Network):
    """
    This constraint will add additionality constraint in RFNBO scenario activated via config file.
    Canbe used for same bidding zone variant in which each country is considered as a bidding zone.
    """
    #considering only vre carriers
    generator_types = list(
        set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
    )
    #loading corresponding baseline network
    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    #vre variables in generators category
    p_nom_gen = n.model["Generator-p_nom"]
    #vre variables in links category
    p_nom_link = n.model["Link-p_nom"]
    gens = n.generators[
        (n.generators.p_nom_extendable == True) & 
        (n.generators.carrier.isin(generator_types))
    ].index
    gens_links = n.links[
        (n.links.p_nom_extendable == True) & 
        (n.links.carrier.isin(generator_types))
    ].index
    #vre capacities grouped by country
    vre_grouper = n.generators.loc[gens].bus.map(n.buses.country)
    vre_grouper_links = n.links.loc[gens_links].bus1.map(n.buses.country)
    #total vre capacities on country buses
    vre_cap_gen = p_nom_gen.loc[gens].groupby(vre_grouper).sum().rename(bus="country")
    vre_cap_link = p_nom_link.loc[gens_links].groupby(vre_grouper_links).sum().rename(bus1="country")
    vre_cap = vre_cap_gen + vre_cap_link
    
    rfnbo_technology_carriers = [
        "H2 Electrolysis",
        "Haber-Bosch"
    ]
    rfnbo_links = n.links[
        (n.links.p_nom_extendable == True) & 
        (n.links.carrier.isin(rfnbo_technology_carriers))
    ].index

    methanolisation = n.links[
        (n.links.p_nom_extendable == True) & 
        (n.links.carrier == "methanolisation")
    ].index
    #eff included as electricity node is not bus0 like other carriers but bus2
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()

    methanolisation_p_nom = n.model["Link-p_nom"].loc[methanolisation] * eff_methanolisation.loc[methanolisation]
    rfnbo_p_nom = n.model["Link-p_nom"].loc[rfnbo_links]
    links_grouper = n.links.loc[rfnbo_links].bus0.map(n.buses.country)
    rfnbo_cap = rfnbo_p_nom.groupby(links_grouper).sum().rename(bus0="country")

    methanolisation_grouper = n.links.loc[methanolisation].bus2.map(n.buses.country)
    methanolisation_cap = methanolisation_p_nom.groupby(methanolisation_grouper).sum().rename(bus2="country")

    rfnbo_total = rfnbo_cap + methanolisation_cap
    
    #computing optimised capacities of vre in baseline
    baseline_gens = baseline_updated.generators[
            (baseline_updated.generators.p_nom_extendable == True) & 
            (baseline_updated.generators.carrier.isin(generator_types))
    ]
    rhs_gens = baseline_gens.groupby(baseline_gens.bus.map(baseline_updated.buses.country)).p_nom_opt.sum()
    baseline_links = baseline_updated.links[
            (baseline_updated.links.p_nom_extendable == True) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ]
    rhs_links = baseline_links.groupby(baseline_links.bus1.map(baseline_updated.buses.country)).p_nom_opt.sum()
    rhs = rhs_gens + rhs_links
    
    rhs.index.name = "country"

    #steps to ensure alighnment
    lhs_countries = vre_cap.indexes["country"]
    common_countries = lhs_countries.intersection(rhs.index)
    active_countries = _rfnbo_active_countries(
        common_countries,criterion_type="both", log_prefix="Additionality constraint"
    )

    #skip constraint if all countries already satisfy VRE share
    if len(active_countries) == 0:
     logger.info(
        "No countries below VRE share threshold therefore skipping additionality constraint"
     )
     return
    lhs_vre = vre_cap.loc[active_countries]
    lhs_rfnbo = rfnbo_total.loc[active_countries]
    rhs_final = rhs.loc[active_countries]
    lhs_vre_aligned = lhs_vre.loc[active_countries]
    lhs_rfnbo_aligned = lhs_rfnbo.loc[active_countries]
    lhs = lhs_vre_aligned - lhs_rfnbo_aligned
    rhs_xr = xr.DataArray(rhs_final, coords=[active_countries], dims=["country"])
    
    n.model.add_constraints(
        lhs >= rhs_xr, 
        name="additionality_constraint"
    )

    logger.info("Additionality constraint added.")

def get_country_neighbours(n):
    '''
    This function retreives the data for interconnected zone variant considering the AC and DC
    interconnections between countries.
    '''
    countries = n.buses.country.unique()
    neighbours = {c: set() for c in countries}

    #AC lines
    for line in n.lines.index:
        c0 = n.buses.country[n.lines.bus0[line]]
        c1 = n.buses.country[n.lines.bus1[line]]
        neighbours[c0].add(c1)
        neighbours[c1].add(c0)

    #DC links
    for link in n.links.index:
        if n.links.carrier[link] in ["DC"]:
            c0 = n.buses.country[n.links.bus0[link]]
            c1 = n.buses.country[n.links.bus1[link]]
            neighbours[c0].add(c1)
            neighbours[c1].add(c0)

    return neighbours
    
def add_additionality_constraint_interconnected(n: pypsa.Network):
    """
    Adds interconnected-zone additionality constraint. 
    VRE in (country + neighbours) - electrolyser in country >= baseline VRE.
    Canbe used for interconnedted zone variant.
    """
    generator_types = list(
        set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
    
    )

    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )

    neighbours = get_country_neighbours(n)
    p_nom_gen = n.model["Generator-p_nom"]
    p_nom_link = n.model["Link-p_nom"]

    gens = n.generators[
        (n.generators.p_nom_extendable == True) & 
        (n.generators.carrier.isin(generator_types))
    ].index
    gens_links = n.links[
        (n.links.p_nom_extendable == True) & 
        (n.links.carrier.isin(generator_types))
    ].index
    #vre capacities grouped by country
    vre_grouper = n.generators.loc[gens].bus.map(n.buses.country)
    vre_grouper_links = n.links.loc[gens_links].bus1.map(n.buses.country)
    #total vre capacities on country buses
    vre_cap_gen = p_nom_gen.loc[gens].groupby(vre_grouper).sum().rename(bus="country")
    vre_cap_link = p_nom_link.loc[gens_links].groupby(vre_grouper_links).sum().rename(bus1="country")
    vre_cap = vre_cap_gen + vre_cap_link

    rfnbo_technology_carriers = [
        "H2 Electrolysis",
        "Haber-Bosch"
    ]
    rfnbo_links = n.links[
        (n.links.p_nom_extendable == True) & 
        (n.links.carrier.isin(rfnbo_technology_carriers))
    ].index

    methanolisation = n.links[
        (n.links.p_nom_extendable == True) & 
        (n.links.carrier == "methanolisation")
    ].index
    #eff included as electricity node is not bus0 like other carriers but bus2
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()

    methanolisation_p_nom = n.model["Link-p_nom"].loc[methanolisation] * eff_methanolisation.loc[methanolisation]
    rfnbo_p_nom = n.model["Link-p_nom"].loc[rfnbo_links]
    links_grouper = n.links.loc[rfnbo_links].bus0.map(n.buses.country)
    rfnbo_cap = rfnbo_p_nom.groupby(links_grouper).sum().rename(bus0="country")

    methanolisation_grouper = n.links.loc[methanolisation].bus2.map(n.buses.country)
    methanolisation_cap = methanolisation_p_nom.groupby(methanolisation_grouper).sum().rename(bus2="country")

    rfnbo_total = rfnbo_cap + methanolisation_cap

    baseline_gens = baseline_updated.generators[
        (baseline_updated.generators.p_nom_extendable)
        & (baseline_updated.generators.carrier.isin(generator_types))
    ]

    rhs = baseline_gens.groupby(
        baseline_gens.bus.map(baseline_updated.buses.country)
    ).p_nom_opt.sum()

    rhs.index.name = "country"
    
    lhs_countries = vre_cap.indexes["country"]
    common_countries = lhs_countries.intersection(rhs.index)
    active_countries = _rfnbo_active_countries(common_countries, criterion_type="both", log_prefix="Interconnected Additionality")
    if len(active_countries) == 0:
     logger.info(
        "No countries below VRE share threshold therefore skipping interconnected additionality constraint"
     )
     return
    
    rhs_final = rhs.loc[active_countries]
    lhs_list = []
    for c in active_countries:
        #get neighbors + current country
        allowed = (neighbours.get(c, set()) | {c}) & set(common_countries)
        vre_sum = vre_cap.loc[list(allowed)].sum()
        rfnbo = rfnbo_total.loc[c]
        lhs_list.append(vre_sum - rfnbo)

    lhs = linopy.expressions.merge(lhs_list, dim="country")
    lhs.coords["country"] = active_countries
    rhs_xr = xr.DataArray(rhs_final, coords=[active_countries], dims=["country"])
    
    n.model.add_constraints(
        lhs >= rhs_xr,
        name="additionality_constraint_interconnected",
        coords={"country": active_countries}
    )

    logger.info("Interconnected additionality constraint added.")
    
# def add_global_additionality_constraint(n: pypsa.Network):
#     """
#     Adds a single global additionality constraint for the entire network.
#     Canbe used for No geographic constraint variant.
#     """
#     generator_types = list(
#         set(config["electricity"]["renewable_carriers"] + ["solar rooftop"])
#     )
    
#     if snakemake.config["run"]["name"].startswith(("RFNBO")):
#         baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
#     else:
#         raise RuntimeError(
#             "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
#             + snakemake.config["run"]["name"]
#         )

#     gens = n.generators[
#         (n.generators.p_nom_extendable) & 
#         (n.generators.carrier.isin(generator_types))
#     ].index
    
#     vre_cap_total = n.model["Generator-p_nom"].loc[gens].sum()
    
#     electrolysers = n.links[
#         (n.links.p_nom_extendable) & 
#         (n.links.carrier == "H2 Electrolysis")
#     ].index
    
#     electrolysers_cap_total = n.model["Link-p_nom"].loc[electrolysers].sum()

#     baseline_gens = baseline_updated.generators[
#         (baseline_updated.generators.p_nom_extendable) & 
#         (baseline_updated.generators.carrier.isin(generator_types))
#     ]

#     rhs_total = baseline_gens.p_nom_opt.sum()

#     n.model.add_constraints(
#         vre_cap_total - electrolysers_cap_total >= rhs_total, 
#         name="global_additionality_constraint"
#     )
#     logger.info("Global additionality constraint added.")
    
def add_temporal_correlation_constraint_add(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    This constraint add the temporal correlation criterion consrtaint for RFNBO scenario.
    Used along same bidding zone additionality constraint. Applying only to VRE capacities and electrolusers installed
    in or after 2035 to consider istalled beofre as grandfathered generators.include, hydro, geothermal, ror
    """
    #considering only vre carriers
    generator_types = list(
         set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
     )
    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    gens = n.generators[(n.generators.build_year >= temporal_year) & (n.generators.carrier.isin(generator_types))].index
    gens_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(generator_types))].index
    p_gen = n.model["Generator-p"].sel(snapshot=sns, name=gens)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns, name=gens_links)
    
    #Adding baseline without H2 genberation
    baseline_gens = baseline_updated.generators[
            (baseline_updated.generators.build_year >= temporal_year) & 
            (baseline_updated.generators.carrier.isin(generator_types))
    ].index
    baseline_links = baseline_updated.links[
            (baseline_updated.links.build_year >= temporal_year) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ].index
    baseline_gen = baseline_updated.generators_t.p.loc[sns, baseline_gens]
    baseline_link = baseline_updated.links_t.p0.loc[sns, baseline_links]
    baseline_grouper = baseline_updated.generators.loc[baseline_gens, "bus"].map(baseline_updated.buses.country)
    baseline_grouper_links = baseline_updated.links.loc[baseline_links, "bus1"].map(baseline_updated.buses.country)
    baseline_vre = baseline_gen.groupby(baseline_grouper, axis=1).sum()
    baseline_links_vre = baseline_link.groupby(baseline_grouper_links, axis=1).sum()
    rhs = baseline_vre + baseline_links_vre
    rhs_xr = rhs.to_xarray()
    rhs_xr = rhs_xr.to_array(dim="country")
    
    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns, name=rfnbo_links)
    p_methanolisation = n.model["Link-p"].sel(snapshot=sns, name=methanolisation)
    methanolisation_elec_consumption = p_methanolisation * eff_methanolisation

    #grouping by country
    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_links, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    vre_gen = p_gen.groupby(gen_country).sum()
    
    vre_link = p_gen_link.groupby(gen_country_link).sum()
    
    vre_total = vre_gen + vre_link
    rfnbo = p_rfnbo.groupby(rfnbo_country).sum()
    methanolisation_total = methanolisation_elec_consumption.groupby(methanolisation_country).sum()
    rfnbo_total = rfnbo + methanolisation_total
    #allign countries
    common_countries = (
    set(vre_total.coords["country"].values) & 
    set(rfnbo_total.coords["country"].values) &
    set(rhs_xr.country.values))
    
    active_countries = _rfnbo_active_countries(
        common_countries,criterion_type="vre", log_prefix="Temporal constraint hourly"
    )
    active_countries = list(active_countries)
    if len(active_countries) == 0:
     logger.info(
        "No countries eligible for temporal correlation constraint, skipping"
     )
     return
    lhs_vre = vre_total.sel(country=active_countries)
    lhs_baseline = rhs_xr.sel(country=active_countries)
    rhs_rfnbo = rfnbo_total.sel(country=active_countries)
    # Hourly constraints imply the annual PPA sum.

    n.model.add_constraints(
        lhs_vre - lhs_baseline >= rhs_rfnbo,
        name="temporal_correlation",
        coords={"snapshot": sns, "country": active_countries}
        )
    
    logger.info("Temporal correlation constraint added.")

def add_temporal_correlation_constraint_no_add(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    This constraint add the temporal correlation criterion consrtaint for RFNBO scenario.
    Used along same bidding zone additionality constraint. Applying only to VRE capacities and electrolusers installed
    in or after 2035 to consider istalled beofre as grandfathered generators.include, hydro, geothermal, ror
    """
    #considering only vre carriers
    generator_types = list(
         set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
     )
    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    gens = n.generators[(n.generators.build_year >= temporal_year) & (n.generators.carrier.isin(generator_types))].index
    gens_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(generator_types))].index
    p_gen = n.model["Generator-p"].sel(snapshot=sns, name=gens)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns, name=gens_links)
    
    #Adding baseline without H2 genberation
    baseline_gens = baseline_updated.generators[
            (baseline_updated.generators.build_year >= temporal_year) & 
            (baseline_updated.generators.carrier.isin(generator_types))
    ].index
    baseline_links = baseline_updated.links[
            (baseline_updated.links.build_year >= temporal_year) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ].index
    baseline_gen = baseline_updated.generators_t.p.loc[sns, baseline_gens]
    baseline_link = baseline_updated.links_t.p0.loc[sns, baseline_links]
    baseline_grouper = baseline_updated.generators.loc[baseline_gens, "bus"].map(baseline_updated.buses.country)
    baseline_grouper_links = baseline_updated.links.loc[baseline_links, "bus1"].map(baseline_updated.buses.country)
    baseline_vre = baseline_gen.groupby(baseline_grouper, axis=1).sum()
    baseline_links_vre = baseline_link.groupby(baseline_grouper_links, axis=1).sum()
    rhs = baseline_vre + baseline_links_vre
    rhs_xr = rhs.to_xarray()
    rhs_xr = rhs_xr.to_array(dim="country")
    
    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns, name=rfnbo_links)
    p_methanolisation = n.model["Link-p"].sel(snapshot=sns, name=methanolisation)
    methanolisation_elec_consumption = p_methanolisation * eff_methanolisation

    #grouping by country
    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_links, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    vre_gen = p_gen.groupby(gen_country).sum()
    
    vre_link = p_gen_link.groupby(gen_country_link).sum()
    
    vre_total = vre_gen + vre_link
    rfnbo = p_rfnbo.groupby(rfnbo_country).sum()
    methanolisation_total = methanolisation_elec_consumption.groupby(methanolisation_country).sum()
    rfnbo_total = rfnbo + methanolisation_total
    #allign countries
    common_countries = (
    set(vre_total.coords["country"].values) & 
    set(rfnbo_total.coords["country"].values) &
    set(rhs_xr.country.values))
    
    active_countries = _rfnbo_active_countries(
        common_countries,criterion_type="vre", log_prefix="Temporal constraint hourly"
    )
    active_countries = list(active_countries)
    if len(active_countries) == 0:
     logger.info(
        "No countries eligible for temporal correlation constraint, skipping"
     )
     return
    lhs_vre = vre_total.sel(country=active_countries)
    lhs_baseline = rhs_xr.sel(country=active_countries)
    rhs_rfnbo = rfnbo_total.sel(country=active_countries)
    # Hourly constraints imply the annual PPA sum.

    n.model.add_constraints(
        lhs_vre >= rhs_rfnbo,
        name="temporal_correlation",
        coords={"snapshot": sns, "country": active_countries}
        )
    
    logger.info("Temporal correlation constraint added.")

def add_annual_ppa_constraint(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    Annual PPA-equivalence constraint paired with additionality.
    """
    generator_types = list(
         set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
     )
    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    gens = n.generators[(n.generators.build_year >= temporal_year) & (n.generators.carrier.isin(generator_types))].index
    gens_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(generator_types))].index
    p_gen = n.model["Generator-p"].sel(snapshot=sns, name=gens)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns, name=gens_links)

    baseline_gens = baseline_updated.generators[
            (baseline_updated.generators.build_year >= temporal_year) & 
            (baseline_updated.generators.carrier.isin(generator_types))
    ].index
    baseline_links = baseline_updated.links[
            (baseline_updated.links.build_year >= temporal_year) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ].index
    baseline_gen = baseline_updated.generators_t.p.loc[sns, baseline_gens]
    baseline_link = baseline_updated.links_t.p0.loc[sns, baseline_links]
    baseline_grouper = baseline_updated.generators.loc[baseline_gens, "bus"].map(baseline_updated.buses.country)
    baseline_grouper_links = baseline_updated.links.loc[baseline_links, "bus1"].map(baseline_updated.buses.country)
    baseline_vre = baseline_gen.groupby(baseline_grouper, axis=1).sum()
    baseline_links_vre = baseline_link.groupby(baseline_grouper_links, axis=1).sum()
    rhs = baseline_vre + baseline_links_vre
    rhs_xr = rhs.to_xarray()
    rhs_xr = rhs_xr.to_array(dim="country")

    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns, name=rfnbo_links)
    p_methanolisation = n.model["Link-p"].sel(snapshot=sns, name=methanolisation)
    methanolisation_elec_consumption = p_methanolisation * eff_methanolisation

    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_links, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    vre_gen = p_gen.groupby(gen_country).sum()
    vre_link = p_gen_link.groupby(gen_country_link).sum()
    vre_total = vre_gen + vre_link
    rfnbo = p_rfnbo.groupby(rfnbo_country).sum()
    methanolisation_total = methanolisation_elec_consumption.groupby(methanolisation_country).sum()
    rfnbo_total = rfnbo + methanolisation_total
    common_countries = (
    set(vre_total.coords["country"].values) & 
    set(rfnbo_total.coords["country"].values) &
    set(rhs_xr.country.values))

    active_countries = _rfnbo_active_countries(
        common_countries,criterion_type="vre", log_prefix="Annual PPA constraint"
    )
    active_countries = list(active_countries)
    if len(active_countries) == 0:
     logger.info(
        "No countries eligible for annual PPA constraint, skipping"
     )
     return

    vre_total_annual = vre_total.sum("snapshot").sel(country=active_countries)
    rfnbo_annual = rfnbo_total.sum("snapshot").sel(country=active_countries)
    rhs_annual = rhs_xr.sum("snapshot").sel(country=active_countries)
    n.model.add_constraints(
        vre_total_annual - rhs_annual >= rfnbo_annual,
        name="annual_ppa",
        coords={"country": active_countries}
        )

    logger.info("Annual PPA constraint added.")


def add_temporal_correlation_interconnected(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    Adds interconnected temporal correlation.
    """
    generator_types = list(
         set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
     )
    neighbours = get_country_neighbours(n)

    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    gens = n.generators[(n.generators.build_year >= temporal_year) & (n.generators.carrier.isin(generator_types))].index
    gens_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(generator_types))].index
    p_gen = n.model["Generator-p"].sel(snapshot=sns, name=gens)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns, name=gens_links)
    
    #Adding baseline without H2 genberation
    baseline_gens = baseline_updated.generators[
            (baseline_updated.generators.build_year >= temporal_year) & 
            (baseline_updated.generators.carrier.isin(generator_types))
    ].index
    baseline_links = baseline_updated.links[
            (baseline_updated.links.build_year >= temporal_year) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ].index
    baseline_gen = baseline_updated.generators_t.p.loc[sns, baseline_gens]
    baseline_link = baseline_updated.links_t.p0.loc[sns, baseline_links]
    baseline_grouper = baseline_updated.generators.loc[baseline_gens, "bus"].map(baseline_updated.buses.country)
    baseline_grouper_links = baseline_updated.links.loc[baseline_links, "bus1"].map(baseline_updated.buses.country)
    baseline_vre = baseline_gen.groupby(baseline_grouper, axis=1).sum()
    baseline_links_vre = baseline_link.groupby(baseline_grouper_links, axis=1).sum()
    rhs = baseline_vre + baseline_links_vre
    rhs_xr = xr.DataArray(rhs, dims=["snapshot", "country"])
    
    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns, name=rfnbo_links)
    p_methanolisation = n.model["Link-p"].sel(snapshot=sns, name=methanolisation)
    methanolisation_elec_consumption = p_methanolisation * eff_methanolisation

    #grouping by country
    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_links, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    vre_gen = p_gen.groupby(gen_country).sum()
    
    vre_link = p_gen_link.groupby(gen_country_link).sum()
    
    vre_total = vre_gen + vre_link
    rfnbo = p_rfnbo.groupby(rfnbo_country).sum()
    methanolisation_total = methanolisation_elec_consumption.groupby(methanolisation_country).sum()
    rfnbo_total = rfnbo + methanolisation_total
    #allign countries
    common_countries = sorted(list(
    set(vre_total.coords["country"].values) & 
    set(rfnbo_total.coords["country"].values) &
    set(rhs_xr.country.values)))
    
    active_countries = _rfnbo_active_countries(common_countries, criterion_type="vre", log_prefix="Interconnected RFNBO")
    if not active_countries:
        logger.info("No countries qualify for the RFNBO hourly matching constraint. Skipping.")
        return
    lhs_list = []
    for c in active_countries:
        zone = (neighbours.get(c, set()) | {c}) & set(common_countries)
        combined = vre_total.sel(country=list(zone)).sum("country")
        lhs_list.append(combined)

    lhs = linopy.expressions.merge(lhs_list, dim="country").assign_coords(country=active_countries)
    rhs_vre_aligned = rhs_xr.sel(country=active_countries)
    rfnbo_total_aligned = rfnbo_total.sel(country=active_countries)
    rhs_vre_aligned = rhs_vre_aligned.transpose("snapshot", "country")
    combined_lhs = lhs - rfnbo_total_aligned
    n.model.add_constraints(
        combined_lhs >= rhs_vre_aligned, 
        name="temporal_correlation_interconnected",
        coords={"snapshot": sns, "country": active_countries}
    )
    
    logger.info("Interconnected temporal correlation constraint added.")
    
def add_annual_ppa_interconnected_constraint(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    Adds annual PPA for the interconnected version.
    """
    generator_types = list(
         set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
     )
    neighbours = get_country_neighbours(n)

    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    gens = n.generators[(n.generators.build_year >= temporal_year) & (n.generators.carrier.isin(generator_types))].index
    gens_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(generator_types))].index
    p_gen = n.model["Generator-p"].sel(snapshot=sns, name=gens)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns, name=gens_links)
    
    #Adding baseline without H2 genberation
    baseline_gens = baseline_updated.generators[
            (baseline_updated.generators.build_year >= temporal_year) & 
            (baseline_updated.generators.carrier.isin(generator_types))
    ].index
    baseline_links = baseline_updated.links[
            (baseline_updated.links.build_year >= temporal_year) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ].index
    baseline_gen = baseline_updated.generators_t.p.loc[sns, baseline_gens]
    baseline_link = baseline_updated.links_t.p0.loc[sns, baseline_links]
    baseline_grouper = baseline_updated.generators.loc[baseline_gens, "bus"].map(baseline_updated.buses.country)
    baseline_grouper_links = baseline_updated.links.loc[baseline_links, "bus1"].map(baseline_updated.buses.country)
    baseline_vre = baseline_gen.groupby(baseline_grouper, axis=1).sum()
    baseline_links_vre = baseline_link.groupby(baseline_grouper_links, axis=1).sum()
    rhs = baseline_vre + baseline_links_vre
    rhs_xr = rhs.to_xarray()
    rhs_xr = rhs_xr.to_array(dim="country")
    
    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns, name=rfnbo_links)
    p_methanolisation = n.model["Link-p"].sel(snapshot=sns, name=methanolisation)
    methanolisation_elec_consumption = p_methanolisation * eff_methanolisation

    #grouping by country
    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_links, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    vre_gen = p_gen.groupby(gen_country).sum()
    
    vre_link = p_gen_link.groupby(gen_country_link).sum()
    
    vre_total = vre_gen + vre_link
    rfnbo = p_rfnbo.groupby(rfnbo_country).sum()
    methanolisation_total = methanolisation_elec_consumption.groupby(methanolisation_country).sum()
    rfnbo_total = rfnbo + methanolisation_total
    #allign countries
    common_countries = sorted(list(
    set(vre_total.coords["country"].values) & 
    set(rfnbo_total.coords["country"].values) &
    set(rhs_xr.country.values)))
    
    active_countries = _rfnbo_active_countries(common_countries, criterion_type="vre", log_prefix="Interconnected Annual PPA")
    if not active_countries:
        logger.info("No countries qualify for the Annual PPA interconnected constraint. Skipping.")
        return
    
    lhs_list = []
    for c in active_countries:
        zone = (neighbours.get(c, set()) | {c}) & set(common_countries)
        combined = vre_total.sel(country=list(zone)).sum("country")
        lhs_list.append(combined)
    
    lhs = linopy.expressions.merge(lhs_list, dim="country").assign_coords(country=active_countries)
    vre_total_annual = lhs.sum("snapshot").sel(country=active_countries)
    rfnbo_annual = rfnbo_total.sum("snapshot").sel(country=active_countries)
    rhs_annual = rhs_xr.sum("snapshot").sel(country=active_countries)
    n.model.add_constraints(
        vre_total_annual - rhs_annual >= rfnbo_annual,
        name="annual_ppa_interconnected",
        coords={"country": active_countries}
        )

    logger.info("Annual PPA interconnected constraint added.")

    
# def add_global_temporal_correlation_constraint(n: pypsa.Network, sns: pd.DatetimeIndex):
#     """
#     Adds a global temporal correlation constraint for each timestep.
#     Used along global additionality constraint.
#     """
#     generator_types = list(
#         set(config["electricity"]["renewable_carriers"] + ["solar rooftop"])
#     )
    
#     gens = n.generators[(n.generators.build_year >= temporal_year) & (n.generators.carrier.isin(generator_types))].index
#     p_gen_total = n.model["Generator-p"].sel(snapshot=sns, name=gens).sum("name")
    
#     electrolysers = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier == "H2 Electrolysis")].index
#     p_electrolysers_total = n.model["Link-p"].sel(snapshot=sns, name=electrolysers).sum("name")

#     n.model.add_constraints(
#         p_gen_total  >= p_electrolysers_total,
#         name="global_temporal_correlation",
#         coords={"snapshot": sns}
#     )
    
#     logger.info("Global temporal correlation constraint added.")

def add_temporal_correlation_monthly_constraint_add(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    Adds temporal correlation constraint aggregated on a monthly basis.
    """
    generator_types = list(
        set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
    )
    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    p_gen = n.model["Generator-p"].sel(snapshot=sns)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns)
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns)

    gens = n.generators[(n.generators.build_year >= monthly_year) & (n.generators.carrier.isin(generator_types))].index
    gens_link = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier.isin(generator_types))].index
    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"

    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_link, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    gen_monthly = p_gen.sel(name=gens).groupby(gen_country).sum().groupby("snapshot.month").sum()
    link_monthly = p_gen_link.sel(name=gens_link).groupby(gen_country_link).sum().groupby("snapshot.month").sum()
    vre_monthly = gen_monthly + link_monthly

    rfnbo_monthly = p_rfnbo.sel(name=rfnbo_links).groupby(rfnbo_country).sum().groupby("snapshot.month").sum()
    p_methanolisation_elec = p_rfnbo.sel(name=methanolisation) * eff_methanolisation
    methanolisation_monthly = (
        p_methanolisation_elec.groupby(methanolisation_country)
        .sum()
        .groupby("snapshot.month")
        .sum()
    )
    rfnbo_monthly_total = rfnbo_monthly + methanolisation_monthly

    rfnbo_monthly_total = rfnbo_monthly + methanolisation_monthly
    baseline_gens = baseline_updated.generators[
        (baseline_updated.generators.build_year >= monthly_year) & 
        (baseline_updated.generators.carrier.isin(generator_types))
    ].index
    
    baseline_p = baseline_updated.generators_t.p.loc[sns, baseline_gens]
    baseline_country = baseline_updated.generators.loc[baseline_gens, "bus"].map(baseline_updated.buses.country)
    baseline_links = baseline_updated.links[
            (baseline_updated.links.build_year >= monthly_year) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ].index
    baseline_link = baseline_updated.links_t.p0.loc[sns, baseline_links]
    baseline_grouper_links = baseline_updated.links.loc[baseline_links, "bus1"].map(baseline_updated.buses.country)
    baseline_links_vre = baseline_link.groupby(baseline_grouper_links, axis=1).sum()
    baseline_vre = baseline_p.groupby(baseline_country, axis=1).sum()
    rhs = baseline_vre + baseline_links_vre
    rhs_monthly = rhs.groupby(rhs.index.month).sum()
    rhs_monthly.index.name = "month"
    rhs_monthly.columns.name = "country"
    rhs_xr = rhs_monthly.to_xarray().to_array(dim="country")
    common_countries = list(
        set(vre_monthly.coords["country"].values) & 
        set(rfnbo_monthly_total.coords["country"].values) &
        set(rhs_xr.coords["country"].values)
    )
    active_countries = _rfnbo_active_countries(
        common_countries,criterion_type="vre", log_prefix="Temporal constraint monthly"
    )
    active_countries = list(active_countries)
    
    if len(active_countries) == 0:
     logger.info(
        "No countries eligible for monthly temporal correlation constraint, skipping"
     )
     return
    lhs_vre = vre_monthly.sel(country=active_countries)
    lhs_baseline = rhs_xr.sel(country=active_countries)
    rhs_rfnbo = rfnbo_monthly_total.sel(country=active_countries)
    
    unique_months = sorted(list(set(sns.month)))
    n.model.add_constraints(
        lhs_vre - lhs_baseline >= rhs_rfnbo,
        name="temporal_correlation_monthly",
        coords={"month": unique_months, "country": active_countries}
    )
    
    logger.info("Monthly temporal correlation constraint added.")

def add_temporal_correlation_monthly_constraint_no_add(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    Adds temporal correlation constraint aggregated on a monthly basis.
    """
    generator_types = list(
        set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
    )
    if snakemake.config["run"]["name"].startswith(("RFNBO")):
        baseline_updated = pypsa.Network(snakemake.input.baseline_updated)
    else:
        raise RuntimeError(
            "RFNBO constraint requires a baseline network but run name does not start with 'RFNBO': "
            + snakemake.config["run"]["name"]
        )
    p_gen = n.model["Generator-p"].sel(snapshot=sns)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns)
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns)

    gens = n.generators[(n.generators.build_year >= monthly_year) & (n.generators.carrier.isin(generator_types))].index
    gens_link = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier.isin(generator_types))].index
    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"

    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_link, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    gen_monthly = p_gen.sel(name=gens).groupby(gen_country).sum().groupby("snapshot.month").sum()
    link_monthly = p_gen_link.sel(name=gens_link).groupby(gen_country_link).sum().groupby("snapshot.month").sum()
    vre_monthly = gen_monthly + link_monthly

    rfnbo_monthly = p_rfnbo.sel(name=rfnbo_links).groupby(rfnbo_country).sum().groupby("snapshot.month").sum()
    p_methanolisation_elec = p_rfnbo.sel(name=methanolisation) * eff_methanolisation
    methanolisation_monthly = (
        p_methanolisation_elec.groupby(methanolisation_country)
        .sum()
        .groupby("snapshot.month")
        .sum()
    )
    rfnbo_monthly_total = rfnbo_monthly + methanolisation_monthly

    rfnbo_monthly_total = rfnbo_monthly + methanolisation_monthly
    baseline_gens = baseline_updated.generators[
        (baseline_updated.generators.build_year >= monthly_year) & 
        (baseline_updated.generators.carrier.isin(generator_types))
    ].index
    
    baseline_p = baseline_updated.generators_t.p.loc[sns, baseline_gens]
    baseline_country = baseline_updated.generators.loc[baseline_gens, "bus"].map(baseline_updated.buses.country)
    baseline_links = baseline_updated.links[
            (baseline_updated.links.build_year >= monthly_year) & 
            (baseline_updated.links.carrier.isin(generator_types))
    ].index
    baseline_link = baseline_updated.links_t.p0.loc[sns, baseline_links]
    baseline_grouper_links = baseline_updated.links.loc[baseline_links, "bus1"].map(baseline_updated.buses.country)
    baseline_links_vre = baseline_link.groupby(baseline_grouper_links, axis=1).sum()
    baseline_vre = baseline_p.groupby(baseline_country, axis=1).sum()
    rhs = baseline_vre + baseline_links_vre
    rhs_monthly = rhs.groupby(rhs.index.month).sum()
    rhs_monthly.index.name = "month"
    rhs_monthly.columns.name = "country"
    rhs_xr = rhs_monthly.to_xarray().to_array(dim="country")
    common_countries = list(
        set(vre_monthly.coords["country"].values) & 
        set(rfnbo_monthly_total.coords["country"].values) &
        set(rhs_xr.coords["country"].values)
    )
    active_countries = _rfnbo_active_countries(
        common_countries,criterion_type="vre", log_prefix="Temporal constraint monthly"
    )
    active_countries = list(active_countries)
    
    if len(active_countries) == 0:
     logger.info(
        "No countries eligible for monthly temporal correlation constraint, skipping"
     )
     return
    lhs_vre = vre_monthly.sel(country=active_countries)
    lhs_baseline = rhs_xr.sel(country=active_countries)
    rhs_rfnbo = rfnbo_monthly_total.sel(country=active_countries)
    
    unique_months = sorted(list(set(sns.month)))
    n.model.add_constraints(
        lhs_vre >= rhs_rfnbo,
        name="temporal_correlation_monthly",
        coords={"month": unique_months, "country": active_countries}
    )
    
    logger.info("Monthly temporal correlation constraint added.")
    
def add_temporal_correlation_monthly_interconnected_constraint_no_add(n: pypsa.Network, sns: pd.DatetimeIndex):
    """
    Adds temporal correlation constraint interconnected on a monthly basis.
    """
    generator_types = list(
        set(config["electricity"]["renewable_carriers"] + ["solar rooftop","geothermal organic rankine cycle"])
    )
    neighbours = get_country_neighbours(n)
    p_gen = n.model["Generator-p"].sel(snapshot=sns)
    p_gen_link = n.model["Link-p"].sel(snapshot=sns)
    p_rfnbo = n.model["Link-p"].sel(snapshot=sns)

    gens = n.generators[(n.generators.build_year >= monthly_year) & (n.generators.carrier.isin(generator_types))].index
    gens_link = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier.isin(generator_types))].index
    rfnbo_technology_carriers = ["H2 Electrolysis", "Haber-Bosch"]
    rfnbo_links = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier.isin(rfnbo_technology_carriers))].index
    methanolisation = n.links[(n.links.build_year >= monthly_year) & (n.links.carrier == "methanolisation")].index
    eff_methanolisation = n.links.loc[methanolisation, "efficiency2"].abs()
    eff_methanolisation.index.name = "name"

    gen_country = n.generators.loc[gens, "bus"].map(n.buses.country).rename("country")
    gen_country_link = n.links.loc[gens_link, "bus1"].map(n.buses.country).rename("country")
    rfnbo_country = n.links.loc[rfnbo_links, "bus0"].map(n.buses.country).rename("country")
    methanolisation_country = n.links.loc[methanolisation, "bus2"].map(n.buses.country).rename("country")

    gen_monthly = p_gen.sel(name=gens).groupby(gen_country).sum().groupby("snapshot.month").sum()
    link_monthly = p_gen_link.sel(name=gens_link).groupby(gen_country_link).sum().groupby("snapshot.month").sum()
    vre_monthly = gen_monthly + link_monthly

    rfnbo_monthly = p_rfnbo.sel(name=rfnbo_links).groupby(rfnbo_country).sum().groupby("snapshot.month").sum()
    p_methanolisation_elec = p_rfnbo.sel(name=methanolisation) * eff_methanolisation
    methanolisation_monthly = (
        p_methanolisation_elec.groupby(methanolisation_country)
        .sum()
        .groupby("snapshot.month")
        .sum()
    )
    rfnbo_monthly_total = rfnbo_monthly + methanolisation_monthly
    
    common_countries = list(
        set(vre_monthly.coords["country"].values) & 
        set(rfnbo_monthly_total.coords["country"].values)
    )
    active_countries = _rfnbo_active_countries(common_countries, criterion_type="vre", log_prefix="Interconnected Monthly RFNBO")
    if not active_countries:
        logger.info("No countries qualify for the Monthly interconnected constraint. Skipping.")
        return
    
    lhs_list = []
    for c in active_countries:
        zone = (neighbours.get(c, set()) | {c}) & set(common_countries)
        combined = vre_monthly.sel(country=list(zone)).sum("country")
        lhs_list.append(combined)
    
    lhs = linopy.expressions.merge(lhs_list, dim="country").assign_coords(country=active_countries)
    lhs_vre = lhs.sel(country=active_countries)
    rhs_rfnbo = rfnbo_monthly_total.sel(country=active_countries)
    
    unique_months = sorted(list(set(sns.month)))
    n.model.add_constraints(
        lhs_vre >= rhs_rfnbo,
        name="temporal_correlation_monthly_interconnected",
        coords={"month": unique_months, "country": active_countries}
    )
    
    logger.info("Monthly temporal correlation interconnected constraint added.")
 
# def add_global_temporal_correlation_monthly_constraint(n: pypsa.Network, sns: pd.DatetimeIndex):
#     """
#     Adds a global temporal correlation constraint aggregated on a monthly basis.
#     """
#     generator_types = list(
#         set(config["electricity"]["renewable_carriers"] + ["solar rooftop"])
#     )

#     gens = n.generators[(n.generators.build_year >= temporal_year) & (n.generators.carrier.isin(generator_types))].index
#     vre_monthly = n.model["Generator-p"].sel(snapshot=sns, name=gens).sum("name").groupby("snapshot.month").sum()

#     electrolysers = n.links[(n.links.build_year >= temporal_year) & (n.links.carrier == "H2 Electrolysis")].index
#     elec_monthly = n.model["Link-p"].sel(snapshot=sns, name=electrolysers).sum("name").groupby("snapshot.month").sum()

#     n.model.add_constraints(
#         vre_monthly >= elec_monthly,
#         name="global_temporal_correlation_monthly",
#     )
    
#     logger.info("Global monthly temporal correlation constraint added.")
 
def _link_efficiency_col(port: int) -> str:
    if port == 0:
        raise ValueError("port 0 has no efficiency column")
    return "efficiency" if port == 1 else f"efficiency{port}"


def _find_h2_consuming_link_ports(network: pypsa.Network) -> pd.DataFrame:
    exclude_carriers = constraints.get(
        "endogenous_H2_demand_floor_exclude",
        ["H2 Fuel Cell", "H2 turbine", "H2 liquefaction"],
    )
    h2_buses = set(network.buses.index[network.buses.carrier == "H2"])
    bus_cols = sorted(
        [c for c in network.links.columns if re.match(r"^bus\d+$", c)],
        key=lambda c: int(c[3:]),
    )

    on_h2 = pd.DataFrame(
        {col: network.links[col].isin(h2_buses) for col in bus_cols},
        index=network.links.index,
    )
    single_h2_links = on_h2.sum(axis=1)[lambda s: s == 1].index
    links = network.links.loc[single_h2_links]

    records = []
    for col in bus_cols:
        k = int(col[3:])
        h2_at_port = links[col].isin(h2_buses)
        if not h2_at_port.any():
            continue
        candidate_links = links.index[h2_at_port]
        if k == 0:
            consumers = candidate_links
        else:
            eff_col = _link_efficiency_col(k)
            if eff_col not in links.columns:
                continue
            negative_eff = links.loc[candidate_links, eff_col].lt(0)
            consumers = candidate_links[negative_eff.values]
        for link in consumers:
            carrier = links.at[link, "carrier"]
            if carrier in exclude_carriers:
                continue
            records.append({"link": link, "port": k, "carrier": carrier})

    return pd.DataFrame(records, columns=["link", "port", "carrier"])


def _find_h2_producing_link_ports(network: pypsa.Network) -> pd.DataFrame:
    exclude_carriers = ["H2 Electrolysis", "vre H2 Electrolysis"]
    h2_buses = set(network.buses.index[network.buses.carrier == "H2"])
    bus_cols = sorted(
        [c for c in network.links.columns if re.match(r"^bus\d+$", c)],
        key=lambda c: int(c[3:]),
    )

    on_h2 = pd.DataFrame(
        {col: network.links[col].isin(h2_buses) for col in bus_cols},
        index=network.links.index,
    )
    single_h2_links = on_h2.sum(axis=1)[lambda s: s == 1].index
    links = network.links.loc[single_h2_links]

    records = []
    for col in bus_cols:
        k = int(col[3:])
        if k == 0:
            continue
        h2_at_port = links[col].isin(h2_buses)
        if not h2_at_port.any():
            continue
        candidate_links = links.index[h2_at_port]
        eff_col = _link_efficiency_col(k)
        if eff_col not in links.columns:
            continue
        positive_eff = links.loc[candidate_links, eff_col].gt(0)
        producers = candidate_links[positive_eff.values]
        for link in producers:
            carrier = links.at[link, "carrier"]
            if carrier in exclude_carriers:
                continue
            records.append({"link": link, "port": k, "carrier": carrier})

    return pd.DataFrame(records, columns=["link", "port", "carrier"])


def _baseline_h2_energy_by_carrier(
    baseline: pypsa.Network, consuming: pd.DataFrame
) -> pd.Series:
    weights = baseline.snapshot_weightings.generators
    carrier_totals: dict[str, float] = {}
    for carrier, group in consuming.groupby("carrier"):
        total = 0.0
        for port, port_group in group.groupby("port"):
            links = port_group["link"].tolist()
            p_df = getattr(baseline.links_t, f"p{port}")[links].clip(lower=0)
            total += float((weights @ p_df).sum())
        carrier_totals[carrier] = total
    return pd.Series(carrier_totals)

def add_endogenous_H2_demand_floor_constraint(n: pypsa.Network) -> None:
    """
    Force RFNBO scenarios to consume at least as much H2 in each endogenous
    power-to-X pathway as the solved baseline network of the same horizon.
    """
    if not snakemake.config["run"]["name"].startswith("RFNBO"):
        raise RuntimeError(
            "Endogenous H2 demand floor constraint requires a baseline reference "
            "and is only meaningful for RFNBO runs; got run name "
            f"'{snakemake.config['run']['name']}'"
        )

    exclude_carriers = constraints.get(
        "endogenous_H2_demand_floor_exclude",
        # Re-electrification is power-system flexibility, not fuel demand;
        # liquefaction throughput is fixed by the identical exogenous shipping load.
        ["H2 Fuel Cell", "H2 turbine", "H2 liquefaction"],
    )
    logger.info(
        "Endogenous H2 demand floor: excluding carriers %s",
        exclude_carriers,
    )

    baseline = pypsa.Network(snakemake.input.baseline_network)
    baseline_consuming = _find_h2_consuming_link_ports(baseline)
    if baseline_consuming.empty:
        logger.info(
            "Endogenous H2 demand floor: no baseline H2 consumers found, skipping"
        )
        return

    baseline_by_carrier = _baseline_h2_energy_by_carrier(
        baseline, baseline_consuming
    )
    threshold_mwh = 1.0
    skipped_carriers = baseline_by_carrier[baseline_by_carrier < threshold_mwh].index.tolist()
    if skipped_carriers:
        logger.info(
            "Endogenous H2 demand floor: skipping near-zero baseline carriers %s",
            skipped_carriers,
        )
    active_carriers = baseline_by_carrier[baseline_by_carrier >= threshold_mwh]
    if active_carriers.empty:
        logger.info(
            "Endogenous H2 demand floor: no carriers above threshold, skipping"
        )
        return

    n_consuming = _find_h2_consuming_link_ports(n)
    weights = n.snapshot_weightings.generators
    hydrogen_dispatch = n.model["Link-p"]

    for carrier, baseline_value in active_carriers.items():
        carrier_links = n_consuming[n_consuming["carrier"] == carrier]
        if carrier_links.empty:
            raise RuntimeError(
                f"Endogenous H2 demand floor: baseline has {carrier} demand "
                f"({baseline_value:.1f} MWh) but no matching links in RFNBO network"
            )

        lhs_parts = []
        for port, port_group in carrier_links.groupby("port"):
            links = port_group["link"].tolist()

            if port == 0:
                expr = (hydrogen_dispatch.loc[:, links] * weights).sum()
                lhs_parts.append(expr)
            else:
                eff_col = _link_efficiency_col(port)
                eff = n.links.loc[links, eff_col].abs()
                expr = (hydrogen_dispatch.loc[:, links] * eff * weights).sum()
                lhs_parts.append(expr)

        lhs = sum(lhs_parts)
        safe_carrier = carrier.replace(" ", "_")
        n.model.add_constraints(
            lhs >= baseline_value,
            name=f"endogenous_H2_demand_floor-{safe_carrier}",
        )
        
        logger.info(
            "Endogenous H2 demand floor: %s >= %.1f TWh (links: %d)",
            carrier,
            baseline_value / 1e6,
            len(carrier_links),
        )


def _non_rfnbo_h2_output_mwh(network: pypsa.Network) -> float:
    producing = _find_h2_producing_link_ports(network)
    if producing.empty:
        return 0.0
    weights = network.snapshot_weightings.generators
    total = 0.0
    for port, group in producing.groupby("port"):
        links = group["link"].tolist()
        eff_col = _link_efficiency_col(port)
        eff = network.links.loc[links, eff_col]
        p_df = network.links_t.p0[links]
        total += float((weights @ (p_df * eff)).sum())
    return total


VRE_MAX_GROWTH_CARRIERS = {
    "solar",
    "solar-hsat",
    "solar rooftop",
    "onwind",
    "offwind-ac",
    "offwind-dc",
    "offwind-float",
}


def _warn_max_growth_additionality_headroom(
    carrier_caps_gw: dict[str, float],
) -> None:
    """Warn when baseline additionality demand approaches VRE growth caps."""
    if not constraints.get("additionality", False):
        return

    vre_cap_gw = sum(
        carrier_caps_gw.get(carrier, 0.0) for carrier in VRE_MAX_GROWTH_CARRIERS
    )
    if vre_cap_gw <= 0:
        return

    baseline_path = getattr(snakemake.input, "baseline_updated", None)
    if not baseline_path:
        return

    baseline = pypsa.Network(baseline_path)
    generator_types = list(
        set(
            config["electricity"]["renewable_carriers"]
            + ["solar rooftop", "geothermal organic rankine cycle"]
        )
    )

    baseline_gens = baseline.generators[
        baseline.generators.p_nom_extendable
        & baseline.generators.carrier.isin(generator_types)
    ]
    baseline_links = baseline.links[
        baseline.links.p_nom_extendable
        & baseline.links.carrier.isin(generator_types)
    ]
    rhs_gens = baseline_gens.groupby(
        baseline_gens.bus.map(baseline.buses.country)
    ).p_nom_opt.sum()
    rhs_links = baseline_links.groupby(
        baseline_links.bus1.map(baseline.buses.country)
    ).p_nom_opt.sum()
    rhs_vre_by_country = (rhs_gens + rhs_links).fillna(0)

    baseline_electro = baseline.links[
        baseline.links.p_nom_extendable
        & (baseline.links.carrier == "H2 Electrolysis")
    ]
    electro_by_country = baseline_electro.groupby(
        baseline_electro.bus0.map(baseline.buses.country)
    ).p_nom_opt.sum()

    threshold_gw = 0.8 * vre_cap_gw
    for country in rhs_vre_by_country.index.union(electro_by_country.index):
        country_rhs_gw = (
            rhs_vre_by_country.get(country, 0.0)
            + electro_by_country.get(country, 0.0)
        ) / 1e3
        if country_rhs_gw > threshold_gw:
            logger.warning(
                "max_growth: additionality demand for %s (baseline VRE + "
                "electrolysers = %.1f GW) exceeds 80%% of EU-wide VRE cap "
                "headroom (%.1f GW of %.1f GW); RFNBO may become infeasible",
                country,
                country_rhs_gw,
                threshold_gw,
                vre_cap_gw,
            )

    eu_demand_gw = (rhs_vre_by_country.sum() + electro_by_country.sum()) / 1e3
    if eu_demand_gw > threshold_gw:
        logger.warning(
            "max_growth: EU-wide additionality demand (baseline VRE + "
            "electrolysers = %.1f GW) exceeds 80%% of EU-wide VRE cap "
            "headroom (%.1f GW of %.1f GW); RFNBO may become infeasible",
            eu_demand_gw,
            threshold_gw,
            vre_cap_gw,
        )

def add_max_growth_constraint(n: pypsa.Network) -> None:
    """
    Cap EU-wide new capacity per carrier for the current myopic planning horizon.
    Direct connected electrolysers connected vre  also added tobe considered for each carrier.
    In myopic runs, extendable components represent new builds only; previous
    capacity is fixed via add_brownfield. Limits are absolute (GW per horizon),
    not relative growth rates.
    """
    mg_cfg = constraints.get("max_growth") or {}
    carrier_caps_gw = mg_cfg.get("carriers") or {}
    oversize_factor = constraints.get("oversize_factor") or {}

    if not carrier_caps_gw:
        logger.warning("max_growth enabled but no carriers configured; skipping")
        return

    #consider only extendable links and generators
    ext_gens = n.generators[n.generators.p_nom_extendable]
    ext_links = n.links[n.links.p_nom_extendable]

    for carrier, cap_gw in carrier_caps_gw.items():
        cap_mw = cap_gw * 1e3
        lhs_terms = []
        num_gens = 0
        num_links = 0
        if carrier == "H2 Electrolysis":
            #grid-connected electrolyzers modelled as links
            link_idx = ext_links[ext_links.carrier == carrier].index
            if not link_idx.empty:
                lhs_terms.append(n.model["Link-p_nom"].loc[link_idx].sum())
                num_links += len(link_idx)
            #direct connected electrolyzer modelled as generators
            h2_gen_idx = ext_gens[
                ext_gens.carrier.str.endswith("Electrolysis")
            ].index
            if not h2_gen_idx.empty:
                #convert output capacity MW_h2 to MW_el as links p-nom captures that
                gen_eff = ext_gens.loc[h2_gen_idx, "efficiency"]
                h2_gen_term = (
                    n.model["Generator-p_nom"].loc[h2_gen_idx] / gen_eff
                ).sum()
                lhs_terms.append(h2_gen_term)
                num_gens += len(h2_gen_idx)

        #VRE techs
        else:
            #grid generators
            gen_idx = ext_gens[ext_gens.carrier == carrier].index
            if not gen_idx.empty:
                lhs_terms.append(n.model["Generator-p_nom"].loc[gen_idx].sum())
                num_gens += len(gen_idx)
            #direct-connected standalone H2 plants matching this carrier
            direct_gen_idx = ext_gens[
                ext_gens.carrier == f"{carrier} Electrolysis"
            ].index
            if not direct_gen_idx.empty:
                factors = n.generators.loc[direct_gen_idx, "oversize_factor"]
                #convert MW_h2 output back to physical VRE capacity built
                direct_term = (
                    n.model["Generator-p_nom"].loc[direct_gen_idx] * factors).sum()
                
                lhs_terms.append(direct_term)
                num_gens += len(direct_gen_idx)
            #hybrid plants basedon solar + onwind
            #if carrier is "solar" or "onwind", it must count towards its respective limit
            hybrid_gen_idx = ext_gens[
                ext_gens.carrier == "solar-onwind Electrolysis"
            ].index
            if not hybrid_gen_idx.empty and carrier in ["solar", "onwind"]:
                hybrid_factors = n.generators.loc[hybrid_gen_idx, "oversize_factor"]
                hybrid_term = (
                    n.model["Generator-p_nom"].loc[hybrid_gen_idx] * hybrid_factors).sum()
                
                lhs_terms.append(hybrid_term)
                num_gens += len(hybrid_gen_idx)
        if not lhs_terms:
            logger.debug(
                "max_growth: no extendable assets for carrier group %s; skipping",
                carrier,
            )
            continue
        lhs = sum(lhs_terms) if len(lhs_terms) > 1 else lhs_terms[0]
        n.model.add_constraints(lhs <= cap_mw, name=f"max_growth-{carrier}")

        logger.info(
            "max_growth: carrier %s capped at %.0f GW (%d generators, %d links)",
            carrier,
            cap_gw,
            num_gens,
            num_links,
        )

        _warn_max_growth_additionality_headroom(carrier_caps_gw)
        
def add_non_rfnbo_h2_cap_constraint(n: pypsa.Network) -> None:
    """
    Cap EU-wide annual non-RFNBO hydrogen production at the baseline 2025 level.
    """
    if not snakemake.config["run"]["name"].startswith("RFNBO"):
        raise RuntimeError(
            "Non-RFNBO H2 cap 2025 constraint requires a 2025 reference network and is only "
            "meaningful for RFNBO runs; got run name "
            f"'{snakemake.config['run']['name']}'"
        )

    baseline_network_2025 = getattr(snakemake.input, "baseline_network_2025", None)
    if not baseline_network_2025:
        raise RuntimeError(
            "Non-RFNBO H2 cap 2025 constraint requires snakemake.input.baseline_network_2025"
        )

    producing = _find_h2_producing_link_ports(n)
    ref_net = pypsa.Network(baseline_network_2025)
    ref_mwh = _non_rfnbo_h2_output_mwh(ref_net)
    threshold_mwh = 1.0
    covered_carriers = sorted(producing["carrier"].unique()) if not producing.empty else []

    if producing.empty:
        if ref_mwh < threshold_mwh:
            logger.info(
                "Non-RFNBO H2 cap 2025: no producing links and reference %.3f MWh below threshold, "
                "skipping",
                ref_mwh,
            )
        else:
            logger.info(
                "Non-RFNBO H2 cap 2025: no producing links in current network, skipping"
            )
        return

    weights = n.snapshot_weightings.generators
    hydrogen_dispatch = n.model["Link-p"]
    lhs_parts = []
    for port, group in producing.groupby("port"):
        links = group["link"].tolist()
        eff_col = _link_efficiency_col(port)
        eff = n.links.loc[links, eff_col]
        lhs_parts.append((hydrogen_dispatch.loc[:, links] * eff * weights).sum())
    lhs = lhs_parts[0]
    for part in lhs_parts[1:]:
        lhs = lhs + part

    n.model.add_constraints(lhs <= ref_mwh, name="non_rfnbo_h2_cap_2025")
    logger.info(
        "Non-RFNBO H2 cap 2025: annual non-RFNBO H2 output <= %.3f TWh "
        "(baseline 2025 reference, %d links, carriers: %s)",
        ref_mwh / 1e6,
        len(producing),
        ", ".join(covered_carriers),
    )


def add_RFNBO_demand_share_constraint(n: pypsa.Network):
    '''
    This constraint adds RFNBO share in suppying total hydrogen demand on global level
    '''
    electrolyser_carriers = ["H2 Electrolysis", "vre H2 Electrolysis"]
    electrolysers = n.links[n.links.carrier.isin(electrolyser_carriers)].index
    eff = n.links.loc[electrolysers, "efficiency"]
    weights = n.snapshot_weightings.generators

    hydrogen_dispatch = n.model["Link-p"]
    total_h2_produced = (hydrogen_dispatch.loc[:, electrolysers] * eff * weights).sum()
    h2_consuming_carriers = ["methanolisation", "H2 turbine","H2 Fuel Cell","Sabatier","Fischer-Tropsch"] 
    consuming_links = n.links[n.links.carrier.isin(h2_consuming_carriers)].index
    h2_link_consumption = ( hydrogen_dispatch.loc[:, consuming_links] * weights).sum()
    # Haber-Bosch: H2 input at bus2, flow = Link-p (bus0) * |efficiency2|
    haber_bosch = n.links[n.links.carrier == "Haber-Bosch"].index
    if len(haber_bosch) > 0:
        eff2 = n.links.loc[haber_bosch, "efficiency2"].abs()
        h2_link_consumption = h2_link_consumption + (hydrogen_dispatch.loc[:, haber_bosch] * eff2 * weights).sum()

    demand_carriers = ["H2 for industry", "H2 for shipping", "land transport fuel cell"]
    hydrogen_loads = n.loads[n.loads.carrier.isin(demand_carriers)].index

    hydrogen_demand = 0
    for col in hydrogen_loads:
        if col in n.loads_t.p_set.columns:
            hydrogen_demand += (n.loads_t.p_set[col] * weights).sum()
        else:
            hydrogen_demand += n.loads.loc[col, "p_set"] * weights.sum()

    total_hydrogen_demand = hydrogen_demand + h2_link_consumption
    level = snakemake.config["solving"]["constraints"]["share"].get(investment_year)

    n.model.add_constraints(
        total_h2_produced >= level * total_hydrogen_demand,
        name="RBNFO_h2_supply_share"
    )

    logger.info("Applying RBNFO hydrogen demand share")
    
def extra_functionality(
    n: pypsa.Network, snapshots: pd.DatetimeIndex, planning_horizons: str | None = None
) -> None:
    """
    Add custom constraints and functionality.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance with config and params attributes
    snapshots : pd.DatetimeIndex
        Simulation timesteps
    planning_horizons : str, optional
        The current planning horizon year or None in perfect foresight

    Collects supplementary constraints which will be passed to
    ``pypsa.optimization.optimize``.

    If you want to enforce additional custom constraints, this is a good
    location to add them. The arguments ``opts`` and
    ``snakemake.config`` are expected to be attached to the network.
    """
    config = n.config
    if constraints["BAU"] and n.generators.p_nom_extendable.any():
        add_BAU_constraints(n, config)
    if constraints["SAFE"] and n.generators.p_nom_extendable.any():
        add_SAFE_constraints(n, config)
    if constraints["CCL"] and n.generators.p_nom_extendable.any():
        add_CCL_constraints(n, config, planning_horizons)

    reserve = config["electricity"].get("operational_reserve", {})
    if reserve.get("activate"):
        add_operational_reserve_margin(n, snapshots, config)

    if EQ_o := constraints["EQ"]:
        add_EQ_constraints(n, EQ_o.replace("EQ", ""))

    if {"solar-hsat", "solar"}.issubset(
        config["electricity"]["renewable_carriers"]
    ) and {"solar-hsat", "solar"}.issubset(
        config["electricity"]["extendable_carriers"]["Generator"]
    ):
      if investment_year > 2025:
        add_solar_potential_constraints(n, config)

    if n.config.get("sector", {}).get("tes", False):
        if n.buses.index.str.contains(
            r"urban central heat|urban decentral heat|rural heat",
            case=False,
            na=False,
        ).any():
            add_TES_energy_to_power_ratio_constraints(n)
            add_TES_charger_ratio_constraints(n)
    add_battery_constraints(n)
    add_lossy_bidirectional_link_constraints(n)
    add_pipe_retrofit_constraint(n)
    
    if n._multi_invest:
        add_carbon_constraint(n, snapshots)
        add_carbon_budget_constraint(n, snapshots)
        add_retrofit_gas_boiler_constraint(n, snapshots)
    else:
        add_co2_atmosphere_constraint(n, snapshots)

    if config["sector"]["enhanced_geothermal"]["flexible"]:
        add_flexible_egs_constraint(n)

    if config["sector"]["imports"]["enable"]:
        add_import_limit_constraint(n, snapshots)
    if constraints["co2_budget_nationl"]:
     if investment_year != 2025:
        # prepare co2 constraint
        nhours = n.snapshot_weightings.generators.sum()
        nyears = nhours / 8760
        limit_countries = snakemake.config["co2_budget_national"][investment_year]
        # add co2 constraint for each country
        add_co2limit_country(n, limit_countries, nyears)
    if constraints["co2_price_national"]:
        # prepare co2 constraint
        nhours = n.snapshot_weightings.generators.sum()
        nyears = nhours / 8760
        countries = snakemake.params.countries
        co2_price_countries = {}
        for country in countries:
         if investment_year != 2025:
            baseline_network = pypsa.Network(snakemake.input.baseline_network)
            co2_price = -baseline_network.global_constraints["mu"].loc[f"co2_limit_per_country{country}"]
            co2_price_countries[country] = co2_price
        # add co2 constraint for each country
        if investment_year != 2025:
         add_co2price_country(n,co2_price_countries,nyears)
    
    if constraints["additionality"]:
     if config["run"]["name"] in ["RFNBO_CR","RFNBO_CR-force","RFNBO_Add","RFNBO_VAR-A2","RFNBO_VAR-T1","RFNBO_VAR-T1-bis","RFNBO_VAR-SUN"]:
      if investment_year >= 2030:
        add_additionality_constraint(n)
        add_annual_ppa_constraint(n, snapshots)
     elif config["run"]["name"] in ["RFNBO_VAR-A1","RFNBO_VAR-MS"]:
      if investment_year >= 2040:
        add_additionality_constraint(n)
        add_annual_ppa_constraint(n, snapshots)
     else:
      if snakemake.config["run"]["name"].startswith(("RFNBO")):
       if not constraints["interconnected_additionality"]:
        raise NotImplementedError("Additionality constraint not implemented yet for this variant")
    # if constraints["interconnected_additionality"]: 
    #    raise NotImplementedError("Interconnected additionality constraint not implemented yet")
    #    if investment_year >= 2035:
    #     add_additionality_constraint_interconnected(n)
    if constraints["global_additionality"]:
      raise NotImplementedError("Global additionality constraint not implemented yet")
    #   if investment_year >= 2035:
    #     add_global_additionality_constraint(n)
    
    if constraints["temporal_correlation"]:
     if config["run"]["name"] in ["RFNBO_CR","RFNBO_CR-force","RFNBO_VAR-A2","RFNBO_VAR-SUN"]:
      if investment_year >= 2030:
        add_temporal_correlation_constraint_add(n, snapshots)
      else:
        add_temporal_correlation_monthly_constraint_no_add(n, snapshots)
     elif config["run"]["name"] in ["RFNBO_Temp"]:
      if investment_year >= 2030:
        add_temporal_correlation_constraint_no_add(n, snapshots)
      else:
        add_temporal_correlation_monthly_constraint_no_add(n, snapshots)
     elif config["run"]["name"] in ["RFNBO_VAR-A1"]:
      if investment_year >= 2040:
        add_temporal_correlation_constraint_add(n, snapshots)
      elif investment_year in [2030, 2035]:
        add_temporal_correlation_constraint_no_add(n, snapshots)
      else:
        add_temporal_correlation_monthly_constraint_no_add(n, snapshots)
     elif config["run"]["name"] in ["RFNBO_VAR-MS"]:
      if investment_year >= 2040:
        add_temporal_correlation_constraint_add(n, snapshots)
      else:
        add_temporal_correlation_monthly_constraint_no_add(n, snapshots)
     elif config["run"]["name"] in ["RFNBO_VAR-T1"]:
      if investment_year >= 2040:
        add_temporal_correlation_constraint_add(n, snapshots)
      elif investment_year in [2030, 2035]:
        add_temporal_correlation_monthly_constraint_add(n, snapshots)
      else:
        add_temporal_correlation_monthly_constraint_no_add(n, snapshots)
     elif config["run"]["name"] in ["RFNBO_VAR-T1-bis"]:
      if investment_year >= 2030:
        add_temporal_correlation_monthly_constraint_add(n, snapshots)
      else:
        add_temporal_correlation_monthly_constraint_no_add(n, snapshots)
     else:
      if snakemake.config["run"]["name"].startswith(("RFNBO")):
       if not constraints["interconnected_temporal_correlation"]:
        raise NotImplementedError("Temporal correlation constraint not implemented yet for this variant")
    
    # if constraints["interconnected_temporal_correlation"]: #!!! TO BE CHECKED & ADAPTED FOR VARAIANTS
    #    raise NotImplementedError("Interconnected temporal correlation constraint not implemented yet")
    #    if investment_year >= 2035:
    #     add_temporal_correlation_interconnected(n, snapshots)
    if constraints["global_temporal_correlation"]: #!!! TO BE CHECKED & ADAPTED FOR VARAIANTS
      raise NotImplementedError("Global temporal correlation constraint not implemented yet")
    #   if investment_year >= 2035:
    #     add_global_temporal_correlation_constraint(n, snapshots)
    

    if constraints["global_temporal_correlation_monthly"]: #!!! TO BE CHECKED & ADAPTED FOR VARAIANTS
       raise NotImplementedError("Global temporal correlation monthly constraint not implemented yet")
    #    if investment_year >= 2035:
    #     add_global_temporal_correlation_monthly_constraint(n, snapshots)
    if constraints["interconnected_additionality"]:
      if investment_year >= 2030:
        add_additionality_constraint_interconnected(n)
        add_annual_ppa_interconnected_constraint(n, snapshots)
    if constraints["interconnected_temporal_correlation"]:
      if investment_year >= 2030:
         add_temporal_correlation_interconnected(n, snapshots)
      else:
         add_temporal_correlation_monthly_interconnected_constraint_no_add(n, snapshots)
    if constraints["RFNBO_demand_share"]: #!!! TO BE CHECKED & ADAPTED FOR VARAIANTS
     if investment_year >= 2030:
        add_RFNBO_demand_share_constraint(n)
    if constraints.get("endogenous_H2_demand_floor", False):
     if scenario.startswith("RFNBO"):
      if investment_year >= 2030:
        add_endogenous_H2_demand_floor_constraint(n)
    if constraints.get("non_rfnbo_h2_cap_2025", False):
     if scenario.startswith("RFNBO"):
      if investment_year >= 2030:
        add_non_rfnbo_h2_cap_constraint(n)
    if constraints.get("max_growth", {}).get("enable", False):
        add_max_growth_constraint(n)
    if n.params.custom_extra_functionality:
        source_path = n.params.custom_extra_functionality
        assert os.path.exists(source_path), f"{source_path} does not exist"
        sys.path.append(os.path.dirname(source_path))
        module_name = os.path.splitext(os.path.basename(source_path))[0]
        module = importlib.import_module(module_name)
        custom_extra_functionality = getattr(module, module_name)
        custom_extra_functionality(n, snapshots, snakemake)  # pylint: disable=E0601


def _validate_solver_outcome(
    n: pypsa.Network,
    status: str,
    condition: str,
    solver_log_path: str | None,
) -> None:
    """Fail fast on pathological solver outcomes before exporting a bad network."""
    condition_l = (condition or "").lower()
    if status != "ok" and any(
        token in condition_l for token in ("infeasible", "unbounded", "time limit")
    ):
        raise RuntimeError(
            f"Solver failed: status={status!r}, condition={condition!r}. "
            "Check the constraint stack and national CO2 budget/price calibration."
        )

    if solver_log_path:
        try:
            with open(solver_log_path, encoding="utf-8", errors="replace") as fh:
                log_text = fh.read()
            if "Model is infeasible" in log_text and status == "ok":
                raise RuntimeError(
                    "Gurobi log reports 'Model is infeasible' but linopy returned "
                    f"status={status!r}; refusing to export a spurious solution."
                )
        except FileNotFoundError:
            pass

    obj = getattr(n, "objective", None)
    if obj is not None and obj < -1e12:
        raise RuntimeError(
            f"Objective {obj:.3e} is pathologically negative (likely unbounded "
            "cross-country CO2 arbitrage or inconsistent CO2 prices); "
            "solution discarded."
        )


def check_objective_value(n: pypsa.Network, solving: dict) -> None:
    """
    Check if objective value matches expected value within tolerance.

    Parameters
    ----------
    n : pypsa.Network
        Network with solved objective
    solving : Dict
        Dictionary containing objective checking parameters

    Raises
    ------
    ObjectiveValueError
        If objective value differs from expected value beyond tolerance
    """
    check_objective = solving["check_objective"]
    if check_objective["enable"]:
        atol = check_objective["atol"]
        rtol = check_objective["rtol"]
        expected_value = check_objective["expected_value"]
        if not np.isclose(n.objective, expected_value, atol=atol, rtol=rtol):
            raise ObjectiveValueError(
                f"Objective value {n.objective} differs from expected value "
                f"{expected_value} by more than {atol}."
            )


def collect_kwargs(
    config: dict,
    solving: dict,
    planning_horizons: str | None = None,
    log_fn: str | None = None,
    mode: str = "single",
) -> tuple[dict, dict]:
    """
    Prepare keyword arguments separated for model creation and model solving.

    Parameters
    ----------
    config : dict
        Configuration dictionary containing solver settings
    solving : dict
        Dictionary of solving options and configuration
    planning_horizons : str, optional
        The current planning horizon year or None in perfect foresight
    log_fn : str, optional
        Path to solver log file
    mode : str, optional
        Optimization mode: 'single', 'rolling_horizon', or 'iterative'
        Default is 'single'

    Returns
    -------
    tuple[dict, dict]
        Two dictionaries: (model_kwargs, solve_kwargs)
        - model_kwargs: Arguments for n.optimize.create_model()
        - solve_kwargs: Arguments for n.optimize.solve_model()
        For 'rolling_horizon' and 'iterative' modes, returns merged kwargs
        with additional mode-specific parameters
    """
    set_of_options = solving["solver"]["options"]
    cf_solving = solving["options"]

    # Model creation kwargs
    model_kwargs = {}
    model_kwargs["multi_investment_periods"] = config["foresight"] == "perfect"
    model_kwargs["transmission_losses"] = cf_solving.get("transmission_losses", False)
    model_kwargs["linearized_unit_commitment"] = cf_solving.get(
        "linearized_unit_commitment", False
    )

    # Solve kwargs
    solver_name = solving["solver"]["name"]
    solver_options = solving["solver_options"][set_of_options] if set_of_options else {}

    solve_kwargs = {}
    solve_kwargs["solver_name"] = solver_name
    solve_kwargs["solver_options"] = solver_options
    solve_kwargs["assign_all_duals"] = cf_solving.get("assign_all_duals", False)
    solve_kwargs["io_api"] = cf_solving.get("io_api", None)
    solve_kwargs["keep_files"] = cf_solving.get("keep_files", False)

    if log_fn:
        solve_kwargs["log_fn"] = log_fn

    oetc = solving.get("oetc", None)
    if oetc:
        oetc["credentials"] = OetcCredentials(
            email=os.environ["OETC_EMAIL"], password=os.environ["OETC_PASSWORD"]
        )
        oetc["solver"] = solver_name
        oetc["solver_options"] = solver_options
        oetc_settings = OetcSettings(**oetc)
        oetc_handler = OetcHandler(oetc_settings)
        solve_kwargs["remote"] = oetc_handler

    if solver_name == "gurobi":
        logging.getLogger("gurobipy").setLevel(logging.CRITICAL)

    # Handle special modes
    if mode == "rolling_horizon":
        all_kwargs = {**model_kwargs, **solve_kwargs}
        all_kwargs["horizon"] = cf_solving.get("horizon", 365)
        all_kwargs["overlap"] = cf_solving.get("overlap", 0)
        return all_kwargs, {}

    elif mode == "iterative":
        all_kwargs = {**model_kwargs, **solve_kwargs}
        all_kwargs["track_iterations"] = cf_solving["track_iterations"]
        all_kwargs["min_iterations"] = cf_solving["min_iterations"]
        all_kwargs["max_iterations"] = cf_solving["max_iterations"]

        if cf_solving["post_discretization"].get("enable", False):
            logger.info("Add post-discretization parameters.")
            cf_solving["post_discretization"].pop("enable", None)
            all_kwargs.update(cf_solving["post_discretization"])

        return all_kwargs, {}

    return model_kwargs, solve_kwargs


def create_optimization_model(
    n: pypsa.Network,
    config: dict,
    params: dict,
    model_kwargs: dict,
    solve_kwargs: dict,
    planning_horizons: str | None = None,
) -> None:
    """
    Prepare optimization problem by creating model and adding extra functionality.

    This function:
    1. Attaches config and params to network for extra_functionality
    2. Creates the optimization model
    3. Adds extra functionality (custom constraints)

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance
    config : dict
        Configuration dictionary containing solver settings
    params : dict
        Dictionary of solving parameters
    model_kwargs : dict
        Arguments for n.optimize.create_model()
    solve_kwargs : dict
        Arguments for n.optimize.solve_model()
    planning_horizons : str, optional
        The current planning horizon year or None in perfect foresight
    """
    # Add config and params to network for extra_functionality
    n.config = config
    n.params = params

    # Create optimization model
    logger.info("Creating optimization model...")
    n.optimize.create_model(**model_kwargs)

    # Add extra functionality (custom constraints)
    logger.info("Adding extra functionality (custom constraints)...")
    extra_functionality(n, n.snapshots, planning_horizons)


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake(
            "solve_sector_network",
            opts="",
            clusters="5",
            configfiles="config/test/config.overnight.yaml",
            sector_opts="",
            planning_horizons="2030",
        )
    configure_logging(snakemake)
    set_scenario_config(snakemake)
    update_config_from_wildcards(snakemake.config, snakemake.wildcards)
    config=snakemake.config
    solve_opts = snakemake.params.solving["options"]
    cf_solving = snakemake.params.solving["options"]
    constraints = config["solving"].get("constraints", {})
    countries = config["countries"]
    
    np.random.seed(solve_opts.get("seed", 123))
    options = snakemake.params.sector
    study = snakemake.params.study
    clusters = snakemake.params.clusters[0]
    sector_opts = snakemake.params.sector_opts[0]
    scenario = config["run"]["name"]
    # Load network
    investment_year = int(snakemake.wildcards.planning_horizons[-4:])
    previous_year = investment_year - 5
    
    if config["run"]["name"] in ["RFNBO_CR","RFNBO_CR-force","RFNBO_Add","RFNBO_Temp","RFNBO_VAR-A2","RFNBO_VAR-A1","RFNBO_VAR-T1","RFNBO_VAR-T1-bis","RFNBO_VAR-SUN","RFNBO_VAR-MS","RFNBO_INT"]:
        temporal_year = 2025 # for hourly temporal correlation & annual ppa: select techno built at that moment or later.
        monthly_year = 2025 # for hourly temporal correlation & annual ppa: select techno built at that moment or later.
    else:
      if snakemake.config["run"]["name"].startswith(("RFNBO")):
        raise NotImplementedError("Temporal and monthly years not implemented yet for this variant")

    if investment_year >= 2030 and (
        constraints.get("activate_vre_share_criterion")
        or constraints.get("activate_carbon_intensity_criterion")
    ):
        previous_horizon_data = [get_vre_share_carbon_intensity(country) for country in countries]
    else:
        previous_horizon_data = None
    n = pypsa.Network(snakemake.input.network)
    
    planning_horizons = snakemake.wildcards.get("planning_horizons", None)

    # Prepare network (settings before solving)
    prepare_network(
        n,
        solve_opts=snakemake.params.solving["options"],
        foresight=snakemake.params.foresight,
        planning_horizons=planning_horizons,
        co2_sequestration_potential=snakemake.params["co2_sequestration_potential"],
        limit_max_growth=snakemake.params.get("sector", {}).get("limit_max_growth"),
        rolling_horizon=cf_solving["rolling_horizon"],
    )
    n = imposed_transmission_limit(
        n,
        config=snakemake.config,)
    if scenario == "baseline_without_H2":
     if investment_year != 2025:
      n = remove_hydrogen_demands(n,)
    # Determine solve mode
    rolling_horizon = cf_solving.get("rolling_horizon", False)
    skip_iterations = cf_solving.get("skip_iterations", False)

    if not n.lines.s_nom_extendable.any():
        skip_iterations = True
        logger.info("No expandable lines found. Skipping iterative solving.")

    logging_frequency = snakemake.config.get("solving", {}).get(
        "mem_logging_frequency", 30
    )

    # Solve network based on mode
    with memory_logger(
        filename=getattr(snakemake.log, "memory", None), interval=logging_frequency
    ) as mem:
        if rolling_horizon:
            logger.info("Using rolling horizon optimization...")
            all_kwargs, _ = collect_kwargs(
                snakemake.config,
                snakemake.params.solving,
                planning_horizons,
                log_fn=snakemake.log.solver,
                mode="rolling_horizon",
            )

            n.config = snakemake.config
            n.params = snakemake.params
            all_kwargs["extra_functionality"] = partial(
                extra_functionality, planning_horizons=planning_horizons
            )
            n.optimize.optimize_with_rolling_horizon(**all_kwargs)
            status, condition = "", ""

        elif skip_iterations:
            logger.info("Using single-pass optimization...")
            model_kwargs, solve_kwargs = collect_kwargs(
                snakemake.config,
                snakemake.params.solving,
                planning_horizons,
                log_fn=snakemake.log.solver,
                mode="single",
            )
            create_optimization_model(
                n,
                config=snakemake.config,
                params=snakemake.params,
                model_kwargs=model_kwargs,
                solve_kwargs=solve_kwargs,
                planning_horizons=planning_horizons,
            )

            logger.info("Solving model...")
            status, condition = n.optimize.solve_model(**solve_kwargs)

        else:
            logger.info("Using iterative transmission expansion optimization...")

            all_kwargs, _ = collect_kwargs(
                snakemake.config,
                snakemake.params.solving,
                planning_horizons,
                log_fn=snakemake.log.solver,
                mode="iterative",
            )

            n.config = snakemake.config
            n.params = snakemake.params
            all_kwargs["extra_functionality"] = partial(
                extra_functionality, planning_horizons=planning_horizons
            )
            status, condition = n.optimize.optimize_transmission_expansion_iteratively(
                **all_kwargs
            )

    logger.info(f"Maximum memory usage: {mem.mem_usage}")

    # Check results
    if not rolling_horizon:
        if status != "ok":
            logger.warning(
                f"Solving status '{status}' with termination condition '{condition}'"
            )
        _validate_solver_outcome(n, status, condition, snakemake.log.solver)
        check_objective_value(n, snakemake.params.solving)

    if "warning" in condition:
        raise RuntimeError("Solving status 'warning'. Discarding solution.")

    if "infeasible" in condition:
        labels = n.model.compute_infeasibilities()
        logger.info(f"Labels:\n{labels}")
        n.model.print_infeasibilities()
        raise RuntimeError("Solving status 'infeasible'. Infeasibilities computed.")

    co2_prices = (n.meta or {}).get("co2_prices")
    co2_payments = compute_country_co2_payments(n) if co2_prices else None

    n.meta = dict(snakemake.config, **dict(wildcards=dict(snakemake.wildcards)))
    if co2_prices:
        n.meta["co2_prices"] = co2_prices
    if co2_payments:
        n.meta["co2_payments"] = co2_payments
    n.export_to_netcdf(snakemake.output.network)

    if snakemake.output.get("model"):
        n.model.to_netcdf(snakemake.output.model)

    with open(snakemake.output.config, "w") as file:
        yaml.dump(
            n.meta,
            file,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
