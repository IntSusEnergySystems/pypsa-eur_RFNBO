#!/usr/bin/env python3
"""RFNBO_CR constraint bindingness analysis (v2).

Corrections vs v1:
- The actual RFNBO_CR run (config/scenarios.quick_test_chain.yaml) has
  activate_vre_share_criterion: false and activate_carbon_intensity_criterion:
  false, so additionality and temporal correlation were enforced for ALL
  countries (no eligibility filtering, no previous-horizon stats needed).
- Horizons extended to 2030, 2035, 2040, 2050.
- Added: H2 production by route (baseline vs RFNBO_CR), PtX H2 consumption
  comparison at 2030/2035, and a baseline-2030 H2 balance sanity check
  (Sabatier floor value).
"""

from __future__ import annotations

import gc
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

REPO = Path("/home/sylvain/svn/pypsa-eur_RFNBO")
HORIZONS = [2030, 2035, 2040, 2050]
CLUSTERS = 33
SECTOR_OPTS = "6H"

RENEWABLE_CARRIERS = [
    "solar", "solar-hsat", "onwind", "offwind-ac", "offwind-dc", "offwind-float", "hydro"
]
GENERATOR_TYPES = list(set(RENEWABLE_CARRIERS + ["solar rooftop", "geothermal organic rankine cycle"]))
TEMPORAL_YEAR = 2025  # RFNBO_CR cohort for hourly temporal correlation
PTX_CARRIERS = ["Fischer-Tropsch", "Sabatier", "methanolisation", "Haber-Bosch"]
BIND_MW = 1.0  # additionality binding threshold (MW)


def net_path(study: str, year: int) -> Path:
    return REPO / f"results/{study}/networks/base_s_{CLUSTERS}__{SECTOR_OPTS}_{year}.nc"


def load_net(path: Path) -> pypsa.Network:
    print(f"  loading {path.parent.parent.name}/{path.name} ...", flush=True)
    return pypsa.Network(str(path))


# ---------------------------------------------------------------- additionality

def vre_capacity_by_country(n: pypsa.Network) -> pd.Series:
    gens = n.generators[(n.generators.p_nom_extendable) & (n.generators.carrier.isin(GENERATOR_TYPES))].index
    links = n.links[(n.links.p_nom_extendable) & (n.links.carrier.isin(GENERATOR_TYPES))].index
    gen = n.generators.loc[gens].groupby(n.generators.loc[gens].bus.map(n.buses.country)).p_nom_opt.sum()
    lnk = n.links.loc[links].groupby(n.links.loc[links].bus1.map(n.buses.country)).p_nom_opt.sum()
    out = gen.add(lnk, fill_value=0)
    out.index.name = "country"
    return out


def electrolyser_capacity_by_country(n: pypsa.Network, extendable_only: bool = True) -> pd.Series:
    mask = n.links.carrier == "H2 Electrolysis"
    if extendable_only:
        mask &= n.links.p_nom_extendable
    elec = n.links.index[mask]
    return n.links.loc[elec].groupby(n.links.loc[elec].bus0.map(n.buses.country)).p_nom_opt.sum()


def additionality_analysis(rfnbo: pypsa.Network, baseline_wo: pypsa.Network) -> pd.DataFrame:
    vre = vre_capacity_by_country(rfnbo)
    elec = electrolyser_capacity_by_country(rfnbo, extendable_only=True)
    elec_all = electrolyser_capacity_by_country(rfnbo, extendable_only=False)
    rhs = vre_capacity_by_country(baseline_wo)
    common = vre.index.intersection(rhs.index)  # ALL countries (no filtering)
    rows = []
    for c in common:
        lhs = vre.get(c, 0) - elec.get(c, 0)
        r = rhs.get(c, 0)
        slack = lhs - r
        rows.append({
            "country": c,
            "vre_gw": vre.get(c, 0) / 1e3,
            "elec_ext_gw": elec.get(c, 0) / 1e3,
            "elec_total_gw": elec_all.get(c, 0) / 1e3,
            "rhs_gw": r / 1e3,
            "slack_gw": slack / 1e3,
            "slack_pct": 100 * slack / r if r > 1e-6 else np.nan,
            "binding": slack <= BIND_MW,
        })
    return pd.DataFrame(rows).sort_values("slack_gw")


# ------------------------------------------------------- temporal correlation

def temporal_analysis(rfnbo: pypsa.Network, baseline_wo: pypsa.Network) -> pd.DataFrame:
    gens = rfnbo.generators[(rfnbo.generators.build_year >= TEMPORAL_YEAR) & (rfnbo.generators.carrier.isin(GENERATOR_TYPES))].index
    gens_links = rfnbo.links[(rfnbo.links.build_year >= TEMPORAL_YEAR) & (rfnbo.links.carrier.isin(GENERATOR_TYPES))].index
    elec_idx = rfnbo.links[(rfnbo.links.build_year >= TEMPORAL_YEAR) & (rfnbo.links.carrier == "H2 Electrolysis")].index

    sns = rfnbo.snapshots
    gen_country = rfnbo.generators.loc[gens, "bus"].map(rfnbo.buses.country)
    link_country = rfnbo.links.loc[gens_links, "bus1"].map(rfnbo.buses.country)
    elec_country = rfnbo.links.loc[elec_idx, "bus0"].map(rfnbo.buses.country)

    vre = rfnbo.generators_t.p.loc[sns, gens].T.groupby(gen_country).sum().T.add(
        rfnbo.links_t.p0.loc[sns, gens_links].T.groupby(link_country).sum().T, fill_value=0
    )
    elec = rfnbo.links_t.p0.loc[sns, elec_idx].T.groupby(elec_country).sum().T

    bg = baseline_wo.generators[(baseline_wo.generators.build_year >= TEMPORAL_YEAR) & (baseline_wo.generators.carrier.isin(GENERATOR_TYPES))].index
    bl = baseline_wo.links[(baseline_wo.links.build_year >= TEMPORAL_YEAR) & (baseline_wo.links.carrier.isin(GENERATOR_TYPES))].index
    bgc = baseline_wo.generators.loc[bg, "bus"].map(baseline_wo.buses.country)
    blc = baseline_wo.links.loc[bl, "bus1"].map(baseline_wo.buses.country)
    rhs = baseline_wo.generators_t.p.loc[sns, bg].T.groupby(bgc).sum().T.add(
        baseline_wo.links_t.p0.loc[sns, bl].T.groupby(blc).sum().T, fill_value=0
    )

    elec_cap = rfnbo.links.loc[elec_idx].groupby(elec_country).p_nom_opt.sum()
    add_vre_cap = rfnbo.generators.loc[gens].groupby(gen_country).p_nom_opt.sum().add(
        rfnbo.links.loc[gens_links].groupby(link_country).p_nom_opt.sum(), fill_value=0
    )

    common = sorted(set(vre.columns) & set(elec.columns) & set(rhs.columns))  # ALL countries
    rows = []
    for c in common:
        slack_ts = (vre[c] - rhs[c]) - elec[c]
        cap = elec_cap.get(c, 0)
        tol = 1e-3 * cap if cap > 0 else 1e-3
        rows.append({
            "country": c,
            "elec_cap_gw": cap / 1e3,
            "add_vre_cap_gw": add_vre_cap.get(c, 0) / 1e3,
            "vre_to_elec_ratio": add_vre_cap.get(c, 0) / cap if cap > 0 else np.nan,
            "pct_snapshots_binding": (slack_ts < tol).mean() * 100,
            "median_slack_mw": slack_ts.median(),
            "min_slack_mw": slack_ts.min(),
        })
    return pd.DataFrame(rows).sort_values("elec_cap_gw", ascending=False)


# ------------------------------------------------------------- H2 supply mix

def h2_production_by_carrier(n: pypsa.Network) -> pd.Series:
    """Annual H2 output (TWh) per link carrier producing onto H2 buses.

    A link produces H2 at port k>=1 if bus_k is an H2 bus and efficiency_k > 0.
    Links with more than one H2 bus (pipelines) are excluded.
    """
    h2_buses = set(n.buses.index[n.buses.carrier == "H2"])
    bus_cols = sorted([c for c in n.links.columns if re.match(r"^bus\d+$", c)], key=lambda c: int(c[3:]))
    on_h2 = pd.DataFrame({col: n.links[col].isin(h2_buses) for col in bus_cols}, index=n.links.index)
    single = on_h2.sum(axis=1)[lambda s: s == 1].index
    links = n.links.loc[single]
    w = n.snapshot_weightings.generators
    totals: dict[str, float] = {}
    for col in bus_cols[1:]:
        k = int(col[3:])
        eff_col = "efficiency" if k == 1 else f"efficiency{k}"
        if eff_col not in links.columns:
            continue
        producers = links.index[(links[col].isin(h2_buses)) & (links[eff_col] > 0)]
        if len(producers) == 0:
            continue
        out = (-getattr(n.links_t, f"p{k}")[producers]).clip(lower=0)
        per_carrier = (w @ out).groupby(links.loc[producers, "carrier"]).sum()
        for carrier, v in per_carrier.items():
            totals[carrier] = totals.get(carrier, 0.0) + float(v)
    return pd.Series(totals).sort_values(ascending=False) / 1e6  # TWh


def h2_consumption_by_carrier(n: pypsa.Network) -> pd.Series:
    """Annual H2 input (TWh) per link carrier consuming from H2 buses."""
    h2_buses = set(n.buses.index[n.buses.carrier == "H2"])
    bus_cols = sorted([c for c in n.links.columns if re.match(r"^bus\d+$", c)], key=lambda c: int(c[3:]))
    on_h2 = pd.DataFrame({col: n.links[col].isin(h2_buses) for col in bus_cols}, index=n.links.index)
    single = on_h2.sum(axis=1)[lambda s: s == 1].index
    links = n.links.loc[single]
    w = n.snapshot_weightings.generators
    totals: dict[str, float] = {}
    # port 0 consumers
    p0_cons = links.index[links["bus0"].isin(h2_buses)]
    if len(p0_cons):
        inp = n.links_t.p0[p0_cons].clip(lower=0)
        per_carrier = (w @ inp).groupby(links.loc[p0_cons, "carrier"]).sum()
        for carrier, v in per_carrier.items():
            totals[carrier] = totals.get(carrier, 0.0) + float(v)
    # ports k>=1 with negative efficiency
    for col in bus_cols[1:]:
        k = int(col[3:])
        eff_col = "efficiency" if k == 1 else f"efficiency{k}"
        if eff_col not in links.columns:
            continue
        consumers = links.index[(links[col].isin(h2_buses)) & (links[eff_col] < 0)]
        if len(consumers) == 0:
            continue
        inp = getattr(n.links_t, f"p{k}")[consumers].clip(lower=0)
        per_carrier = (w @ inp).groupby(links.loc[consumers, "carrier"]).sum()
        for carrier, v in per_carrier.items():
            totals[carrier] = totals.get(carrier, 0.0) + float(v)
    return pd.Series(totals).sort_values(ascending=False) / 1e6  # TWh


def h2_loads_twh(n: pypsa.Network) -> pd.Series:
    """Exogenous loads attached to H2 buses (TWh)."""
    h2_buses = set(n.buses.index[n.buses.carrier == "H2"])
    loads = n.loads[n.loads.bus.isin(h2_buses)]
    if loads.empty:
        return pd.Series(dtype=float)
    w = n.snapshot_weightings.generators
    cols = loads.index.intersection(n.loads_t.p_set.columns)
    energy = pd.Series(0.0, index=loads.index)
    if len(cols):
        energy.loc[cols] = w @ n.loads_t.p_set[cols]
    static = loads.index.difference(cols)
    if len(static):
        energy.loc[static] = loads.loc[static, "p_set"] * w.sum()
    return (energy.groupby(loads.carrier).sum() / 1e6).sort_values(ascending=False)


def h2_store_net_twh(n: pypsa.Network) -> float:
    """Net annual H2 store discharge (positive = net release into the bus), TWh."""
    h2_buses = set(n.buses.index[n.buses.carrier == "H2"])
    stores = n.stores.index[n.stores.bus.isin(h2_buses)]
    if len(stores) == 0:
        return 0.0
    w = n.snapshot_weightings.stores
    return float((w @ n.stores_t.p[stores]).sum()) / 1e6


# ---------------------------------------------------------------- summaries

def summarize_additionality(df: pd.DataFrame) -> None:
    n_bind = int(df["binding"].sum())
    print(f"  countries: {len(df)}, binding (slack<=1MW): {n_bind}")
    print(f"  median slack: {df['slack_gw'].median():.3f} GW, total slack: {df['slack_gw'].sum():.1f} GW")
    big = df.nlargest(6, "elec_total_gw")
    print("  largest electrolyser fleets:")
    for _, r in big.iterrows():
        flag = "BIND" if r["binding"] else "slack"
        print(
            f"    {r['country']}: elec {r['elec_total_gw']:.1f} GW (ext {r['elec_ext_gw']:.1f}), "
            f"slack {r['slack_gw']:.3f} GW ({flag})"
        )
    print(df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


def summarize_temporal(df: pd.DataFrame) -> None:
    high = df[df["pct_snapshots_binding"] > 50]
    print(f"  countries: {len(df)}, with >50% binding snapshots: {len(high)}")
    print(f"  median binding fraction: {df['pct_snapshots_binding'].median():.1f}%")
    print(f"  median VRE/electrolyser cap ratio: {df['vre_to_elec_ratio'].median():.2f}")
    print("  largest electrolyser fleets:")
    for _, r in df.nlargest(6, "elec_cap_gw").iterrows():
        print(
            f"    {r['country']}: elec {r['elec_cap_gw']:.1f} GW, ratio {r['vre_to_elec_ratio']:.1f}, "
            f"{r['pct_snapshots_binding']:.1f}% snaps binding, med slack {r['median_slack_mw']:.0f} MW"
        )
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))


def main() -> None:
    print("=" * 76)
    print("RFNBO_CR bindingness v2 — NO country eligibility filtering (actual run)")
    print(f"Horizons: {HORIZONS}; temporal cohort build_year>={TEMPORAL_YEAR}")
    print("=" * 76)

    prod_tables: dict[tuple[str, int], pd.Series] = {}
    cons_tables: dict[tuple[str, int], pd.Series] = {}

    for year in HORIZONS:
        print(f"\n{'#' * 76}\nHORIZON {year}\n{'#' * 76}")

        rfnbo = load_net(net_path("RFNBO_CR", year))
        bwo = load_net(net_path("baseline_without_H2", year))

        print("\n1. ADDITIONALITY (all countries)")
        add_df = additionality_analysis(rfnbo, bwo)
        summarize_additionality(add_df)

        print("\n2. TEMPORAL CORRELATION hourly (all countries)")
        temp_df = temporal_analysis(rfnbo, bwo)
        summarize_temporal(temp_df)

        del bwo
        gc.collect()

        prod_tables[("RFNBO_CR", year)] = h2_production_by_carrier(rfnbo)
        cons_tables[("RFNBO_CR", year)] = h2_consumption_by_carrier(rfnbo)
        del rfnbo
        gc.collect()

        baseline = load_net(net_path("baseline", year))
        prod_tables[("baseline", year)] = h2_production_by_carrier(baseline)
        cons_tables[("baseline", year)] = h2_consumption_by_carrier(baseline)

        if year == 2030:
            print("\n--- SANITY CHECK: baseline 2030 H2 balance (TWh) ---")
            prod = prod_tables[("baseline", 2030)]
            cons = cons_tables[("baseline", 2030)]
            loads = h2_loads_twh(baseline)
            store_net = h2_store_net_twh(baseline)
            print("Production by carrier:")
            print(prod.to_string(float_format=lambda x: f"{x:.2f}"))
            print(f"  TOTAL production: {prod.sum():.2f}")
            print("Consumption by link carrier:")
            print(cons.to_string(float_format=lambda x: f"{x:.2f}"))
            print(f"  TOTAL link consumption: {cons.sum():.2f}")
            if not loads.empty:
                print("Exogenous H2 loads:")
                print(loads.to_string(float_format=lambda x: f"{x:.2f}"))
                print(f"  TOTAL loads: {loads.sum():.2f}")
            print(f"Net store discharge: {store_net:.2f}")
            bal = prod.sum() + store_net - cons.sum() - (loads.sum() if not loads.empty else 0)
            print(f"Balance residual (prod + store - cons - loads): {bal:.2f}")
            sab = cons.get("Sabatier", 0.0)
            print(f"\nSabatier H2 input, baseline 2030: {sab:.2f} TWh (solve log floor: 514.8 TWh)")

        del baseline
        gc.collect()

    # ----------------------------------------------------- H2 production mix
    print(f"\n{'=' * 76}\nH2 PRODUCTION BY ROUTE (TWh H2 output)\n{'=' * 76}")
    all_carriers = sorted(set().union(*[s.index for s in prod_tables.values()]))
    for scen in ["baseline", "RFNBO_CR"]:
        print(f"\n{scen}:")
        df = pd.DataFrame(
            {year: prod_tables[(scen, year)].reindex(all_carriers).fillna(0) for year in HORIZONS}
        )
        df.loc["TOTAL"] = df.sum()
        print(df.to_string(float_format=lambda x: f"{x:.2f}"))

    # ------------------------------------------------- PtX consumption 2030/35
    print(f"\n{'=' * 76}\nPtX H2 CONSUMPTION (TWh H2 input), 2030 & 2035\n{'=' * 76}")
    for year in [2030, 2035]:
        print(f"\n{year}:")
        rows = []
        for carrier in PTX_CARRIERS:
            rows.append({
                "carrier": carrier,
                "baseline_twh": cons_tables[("baseline", year)].get(carrier, 0.0),
                "RFNBO_CR_twh": cons_tables[("RFNBO_CR", year)].get(carrier, 0.0),
            })
        df = pd.DataFrame(rows)
        df["delta"] = df["RFNBO_CR_twh"] - df["baseline_twh"]
        print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    print("\nScript: /tmp/rfnbo_bindingness.py")


if __name__ == "__main__":
    main()
