#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri May 15 12:35:01 2026

@author: umair
"""

import pandas as pd
import pypsa
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

planning_horizons = [2025, 2030, 2035, 2040, 2045, 2050]
results = []
for planning_horizon in planning_horizons:
    n=pypsa.Network(f"/home/umair/pypsa-eur_RFNBO/results/RFNBO_vre_electrolysers/networks/base_s_33__6H_{planning_horizon}.nc")
    
    dem_ind = (n.snapshot_weightings.generators @ n.loads_t.p.filter(like="H2 for industry")).sum().sum()/1e6
    dem_transport = (n.snapshot_weightings.generators @ n.loads_t.p.filter(like="land transport fuel cell")).sum().sum()/1e6
    dem_navigation = (n.snapshot_weightings.generators @ n.loads_t.p.filter(like="H2 for shipping")).sum().sum()/1e6
    tech_methanolisation = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like="methanolisation")).sum().sum()/1e6
    tech_haber_bosch = (n.snapshot_weightings.generators @ n.links_t.p2.filter(like="Haber-Bosch")).sum().sum()/1e6
    tech_fischer_tropsch = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like="Fischer-Tropsch")).sum().sum()/1e6
    tech_methanation = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like="Sabatier")).sum().sum()/1e6
    tech_turbine = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like="H2 turbine")).sum().sum()/1e6
    tech_fuellcell = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like="H2 Fuel Cell")).sum().sum()/1e6
    gen_smr = -(n.snapshot_weightings.generators @ n.links_t.p1.filter(like="SMR")).sum().sum()/1e6
    gen_electrolysis = -(n.snapshot_weightings.generators @ n.links_t.p1.filter(like="Electrolysis")).sum().sum()/1e6 
    gen_vre_electrolysis = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="H2 Plant")).sum().sum()/1e6 
    gen_ammonia_cracker = -(n.snapshot_weightings.generators @ n.links_t.p1.filter(like="ammonia cracker")).sum().sum()/1e6
    
    
    results.append({
        ("Demand", "Industry"): dem_ind,
        ("Demand", "Transport"): dem_transport,
        ("Demand", "Shipping"): dem_navigation,

        ("Demand", "Methanolisation"): tech_methanolisation,#no other technology biomass to methanol decativated
        ("Endogenous", "Haber-Bosch"): tech_haber_bosch,
        ("Endogenous", "Fischer-Tropsch"): tech_fischer_tropsch,
        ("Endogenous", "Methanation"): tech_methanation,
        ("Endogenous", "H2 Turbine"): tech_turbine,
        ("Endogenous", "Fuel Cell"): tech_fuellcell,

        ("Generation", "SMR"): gen_smr,
        ("Generation", "Electrolysis"): gen_electrolysis,
        ("Generation", "VRE Electrolysis"): gen_vre_electrolysis,
        ("Generation", "Ammonia Cracker"): gen_ammonia_cracker,
    })
    
df = pd.DataFrame(
    results,
    index=planning_horizons
)

df.columns = pd.MultiIndex.from_tuples(df.columns)
#1Mtons H2 = 33.33 TWh(LHV)
df = df/33.33


#%%
plot_df = pd.DataFrame(index=df.index)

# Left bar
plot_df["Generation"] = df["Generation"].sum(axis=1)

plot_df["Endogenous"] = df["Endogenous"].sum(axis=1)
plot_df["Demand"] = df["Demand"].sum(axis=1)

x = np.arange(len(plot_df.index))
width = 0.35

fig, ax = plt.subplots(figsize=(10,6))

ax.bar(
    x - width/2,
    plot_df["Generation"],
    width,
    label="Generation",
    color="tab:blue"
)

# Endogenous part
ax.bar(
    x + width/2,
    plot_df["Endogenous"],
    width,
    label="Endogenous",
    color="tab:orange"
)

ax.bar(
    x + width/2,
    plot_df["Demand"],
    width,
    bottom=plot_df["Endogenous"],
    label="Demand",
    color="tab:green"
)

ax.set_xticks(x)
ax.set_xticklabels(plot_df.index)

ax.set_ylabel("Mt H$_2$")
ax.set_xlabel("Planning Horizon")

ax.legend()

plt.tight_layout()
plt.show()

#%%
fig, ax = plt.subplots(figsize=(12,7))

x = np.arange(len(df.index))
width = 0.35

bottom_gen = np.zeros(len(df.index))
gen_handles = []

for col in df["Generation"].columns:

    values = df["Generation"][col].values

    bars = ax.bar(
        x - width/2,
        values,
        width,
        bottom=bottom_gen,
    )

    bottom_gen += values

    # use actual bar color
    color = bars[0].get_facecolor()

    gen_handles.append(
        Patch(facecolor=color, label=col)
    )


bottom_cons = np.zeros(len(df.index))

combined = pd.concat(
    [df["Endogenous"], df["Demand"]],
    axis=1
)

cons_handles = []

for col in combined.columns:

    values = combined[col].values

    bars = ax.bar(
        x + width/2,
        values,
        width,
        bottom=bottom_cons,
    )

    bottom_cons += values

    color = bars[0].get_facecolor()

    cons_handles.append(
        Patch(facecolor=color, label=col)
    )

ax.set_xticks(x)
ax.set_xticklabels(df.index)

ax.set_ylabel("Mt H$_2$")
ax.set_xlabel("Planning Horizon")

legend1 = ax.legend(
    handles=gen_handles,
    title="Generation",
    bbox_to_anchor=(1.02, 1),
    loc="upper left"
)

legend2 = ax.legend(
    handles=cons_handles,
    title="Consumption + Demand",
    bbox_to_anchor=(1.02, 0.45),
    loc="upper left"
)

ax.add_artist(legend1)

plt.tight_layout()
plt.show()

