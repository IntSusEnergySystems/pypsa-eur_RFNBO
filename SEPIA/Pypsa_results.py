#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging

logger = logging.getLogger(__name__)
import pandas as pd
import pypsa
import logging
import geopandas as gpd
import os
import sys
import panel as pn
import base64
import matplotlib.pyplot as plt
from pandas.plotting import table
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.lines import Line2D
import cartopy.crs as ccrs
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots 
from jinja2 import Template
current_script_dir = os.path.dirname(os.path.abspath(__file__))
scripts_path = os.path.join(current_script_dir, "../scripts/")
sys.path.append(scripts_path)
from plot_summary import rename_techs, preferred_order
from plot_power_network import load_projection
from scripts.make_summary import assign_locations
from pypsa.plot import add_legend_circles, add_legend_lines, add_legend_patches
from add_electricity import calculate_annuity
from make_summary import assign_carriers
import yaml
import shutil
import plotly.io as pio
import gc    
import pickle
import numpy as np

def rename_techs_tyndp(tech):
    tech = rename_techs(tech)
    if "heat pump" in tech or "resistive heater" in tech:
        return "power-to-heat"
    elif tech in ["H2 Electrolysis"]:
        return "Electrolysers"
    elif tech in ["geothermal district heat","geothermal organic rankine cycle","geothermal heat"]:
        return "geothermal"
    elif tech in ["vre H2 Electrolysis"]:
        return "Direct Connected Electrolysers"
    elif tech in ["FLC Electrolysis"]:
        return "FLC Electrolysers"
    elif tech in ["solar Electrolyser","onwind Electrolyser","offwind-ac Electrolyser","offwind-dc Electrolyser"]:
        return "VRE connected Electrolysers"
    elif tech in ["electricity distribution grid"]:
        return "distribution network"
    elif tech in [ "CHP", "H2 Fuel Cell","H2 turbine"]:
        return "CHP"
    elif tech in [ "battery charger", "battery discharger","battery", "Li ion", "EV charger", "V2G"]:
        return "battery storage"
    elif tech in [ "biomass boiler", "oil boiler","gas boiler"]:
        return "boilers"
    elif tech in ["solar", "solar vre"]:
        return "solar"
    elif tech == "Fischer-Tropsch":
        return "power-to-liquid"
    elif tech in ["offshore wind", "offwind-ac vre","offwind-dc vre"]:
        return "offshore wind"
    elif tech in ["onshore wind", "onwind vre"]:
        return "onshore wind"
    elif tech in ["co2 sequestered","CO2 sequestration", "co2", "SMR CC", "process emissions CC","process emissions", "solid biomass for industry CC", "gas for industry CC","CO2 pipeline"]:
         return "CCUS"
    elif tech in ["biomass", "solid biomass", "solid biomass for industry", "biogas", "solid biomass transport", "biomass exports", "biogas exports","municipal solid waste","municipal solid waste transport","solid biomass powerplants","biomass-to-methanol","unsustainable bioliquids","unsustainable solid biomass"]:
          return "biomass fuel & techs"
    elif tech in ["shipping oil", "naphtha for industry", "land transport oil", "kerosene for aviation", "agriculture machinery oil", "coal for industry","gas for industry","coal fuel","gas fuel","oil","gas","coal","lignite","CCGT","OCGT","coal powerplants", "oil powerplants","oil primary", "oil refining"]:
          return "Fossil fuels & powerplants"
    elif tech in ["hot water storage", "H2", "H2 storage","H2 Store","water pits","water pits charger","water pits discharger","heat vent"]:
        return "TES & H2 storage"
    elif "load" in tech:
        return "load shedding"
    elif tech in ["SMR", "ammonia cracker", "Haber-Bosch", "BioSNG", "biomass to liquid","methanol","ammonia", "methanol exports", "ammonia exports","methanolisation","shipping methanol" ,"BioSNG CC","HVC to air","ammonia store","biomass to liquid CC","industry methanol","non-sequestered HVC","electrobiofuels","methanation","helmeth", "H2 liquefaction"]:
          return "synthetic fuels & techs"
    elif tech in ["uranium", "nuclear", "nuclear fuel"]:
          return "nuclear"
    elif tech in ["H2 pipeline", "gas pipeline","gas pipeline new","H2 pipeline retrofitted"]:
          return "H2 & gas pipelines"
    else:
        return tech
    


def logo():
    file = snakemake.input.sepia_config
    excel_file = pd.read_excel(file, ['MAIN_PARAMS'], index_col=0)
    excel_file = excel_file["MAIN_PARAMS"].drop('Description',axis=1).to_dict()['Value']
    logo = dict(source=excel_file['PROJECT_LOGO'],
        xref="paper",
        yref="paper",
        x=0.5,
        y=1,
        xanchor="center",
        yanchor="bottom",
        sizex=0.2,
        sizey=0.2,
        layer="below")
    return logo


def build_filename(cluster,opt,sector_opt,planning_horizon):
    prefix=f"results/{study}/networks/base_"
    return prefix+"s_{cluster}_{opt}_{sector_opt}_{planning_horizon}.nc".format(
        cluster=cluster,
        opt=opt,
        sector_opt=sector_opt,
        planning_horizon=planning_horizon
    )

def load_file(filename):
    # Use pypsa.Network to load the network from the filename
    return pypsa.Network(filename)

def load_files(study, planning_horizons, cluster, opt, sector_opt):
    files = {}
    for planning_horizon in planning_horizons:
        filename = build_filename(cluster, opt, sector_opt, planning_horizon)
        files[planning_horizon] = load_file(filename)
    return files

#uploading baseline network files to retrieve co2 prices for co2 price plot as they are computed in baseline as shadow
#price and then implemented in other RFNBO scenarios as costs
def build_filename_baseline(cluster,opt,sector_opt,planning_horizon):
    prefix="results/baseline/networks/base_"
    return prefix+f"s_{cluster}_{opt}_{sector_opt}_{planning_horizon}.nc".format(
        cluster=cluster,
        opt=opt,
        sector_opt=sector_opt,
        planning_horizon=planning_horizon
    )

def load_file_baseline(filename_baseline):
    # Use pypsa.Network to load the network from the filename
    return pypsa.Network(filename_baseline)

def load_files_baseline(study, planning_horizons, cluster, opt, sector_opt):
    files_baseline = {}
    for planning_horizon in planning_horizons:
        filename_baseline = build_filename_baseline(cluster, opt, sector_opt, planning_horizon)
        files_baseline[planning_horizon] = load_file_baseline(filename_baseline)
    return files_baseline


def calculate_ac_transmission(lines, line_numbers):
    transmission_ac = lines.s_nom_opt[line_numbers].sum()
    length_ac = lines.length[line_numbers].sum()
    options = pd.read_csv(fn ,index_col=[0, 1]).sort_index()
    ac_cost = options.loc[("HVAC overhead", "capital_cost")].sum()
    #0.5 for considering 50% is paid by each country
    transmission = ((lines.s_nom_opt[line_numbers].sum()) * ac_cost * length_ac * 0.5)

    return transmission_ac, transmission

def calculate_dc_transmission(links, link_numbers):
    transmission_dc = links.p_nom_opt[link_numbers].sum()
    length_dc = links.length[link_numbers].sum()
    
    options = pd.read_csv(fn ,index_col=[0, 1]).sort_index()
    dc_cost = options.loc[("HVDC overhead", "capital_cost")].sum()
    #0.5 for considering 50% is paid by each country
    transmissionc = ((links.p_nom_opt[link_numbers].sum()) * dc_cost * length_dc * 0.5)

    return transmission_dc, transmissionc

def calculate_transmission_values(cluster, opt, sector_opt, planning_horizons):
    results_dict = {}

    for planning_horizon in planning_horizons:
        n = loaded_files[planning_horizon]

        cap_ac = pd.DataFrame(index=countries)
        cos_ac = pd.DataFrame(index=countries)
        cap_dc = pd.DataFrame(index=countries)
        cos_dc = pd.DataFrame(index=countries)
        
        for country in countries:
          cross_border_ac = n.lines.bus0.str[:2] != n.lines.bus1.str[:2]
          filtered_ac = n.lines.bus0.str[:2] == country
          filtered_ac_r = n.lines.bus1.str[:2] == country
          combined_condition = (filtered_ac | filtered_ac_r) & cross_border_ac
          filtered_lines = n.lines[combined_condition]
          cross_border_dc = n.links.bus0.str[:2] != n.links.bus1.str[:2]
          filtered_dc = (n.links.carrier == 'DC') & (n.links.bus0.str[:2] == country) & (~n.links.index.str.contains('reversed'))
          filtered_dc_r = (n.links.carrier == 'DC') & (n.links.bus1.str[:2] == country) & (~n.links.index.str.contains('reversed'))
          combined_condition_dc = (filtered_dc | filtered_dc_r)  & cross_border_dc
          filtered_lines_dc = n.links[combined_condition_dc]
          transmission_ac, transmission = calculate_ac_transmission(filtered_lines, filtered_lines.index)
          transmission_dc, transmissionc = calculate_dc_transmission(filtered_lines_dc, filtered_lines_dc.index)
         
         
          cap_ac.loc[country, 'transmission_AC'] = transmission_ac
          cos_ac.loc[country, 'transmission_AC'] = transmission
          cap_dc.loc[country, 'transmission_DC'] = transmission_dc
          cos_dc.loc[country, 'transmission_DC'] = transmissionc


        # Create a dictionary for the planning horizon and store results
        results_dict[planning_horizon] = {
            'cap_ac': cap_ac,
            'cos_ac': cos_ac,
            'cap_dc': cap_dc,
            'cos_dc': cos_dc
        }

    return results_dict


def calculate_elec_import_export_costs(country, planning_horizons):
    def calculate_import_export_separate(flows, direction, network, marginal_prices):
        import_cost = pd.DataFrame(index=flows.index, columns=flows.columns)
        export_revenue = pd.DataFrame(index=flows.index, columns=flows.columns)

        for line in flows.columns:
            # Identify buses
            if direction == 'ac0' or direction == 'dc0':
                bus0 = network.lines.loc[line, "bus0"] if 'ac' in direction else network.links.loc[line, "bus0"]
                bus1 = network.lines.loc[line, "bus1"] if 'ac' in direction else network.links.loc[line, "bus1"]
            else:
                bus0 = network.lines.loc[line, "bus1"] if 'ac' in direction else network.links.loc[line, "bus1"]
                bus1 = network.lines.loc[line, "bus0"] if 'ac' in direction else network.links.loc[line, "bus0"]

            if bus0 not in marginal_prices.columns or bus1 not in marginal_prices.columns:
                continue

            price_bus0 = marginal_prices[bus0]
            price_bus1 = marginal_prices[bus1]

            for t in flows.index:
                flow = flows.at[t, line]
                if flow < 0:  # Import
                    import_cost.at[t, line] = -flow * price_bus1[t]
                    export_revenue.at[t, line] = 0
                else:  # Export
                    import_cost.at[t, line] = 0
                    export_revenue.at[t, line] = flow * price_bus0[t]

        return import_cost, export_revenue

    results = {}

    for planning_horizon in planning_horizons:
        # Load network
        n = loaded_files[planning_horizon]

        # Marginal prices
        marginal_price_filter = n.buses.carrier == "AC"
        marginal_price = n.buses_t.marginal_price.filter(items=marginal_price_filter[marginal_price_filter].index)
        # marginal_price = marginal_price.drop(columns=["GB3 0"], errors='ignore')

        # AC lines
        cross_border_lines = n.lines.bus0.str[:2] != n.lines.bus1.str[:2]
        filtered_ac_lines = (n.lines.bus0.str[:2] == country) & cross_border_lines
        ac_lines = n.lines_t.p0.filter(items=filtered_ac_lines[filtered_ac_lines].index)

        filtered_ac_lines_r = (n.lines.bus1.str[:2] == country) & cross_border_lines
        ac_lines_r = n.lines_t.p1.filter(items=filtered_ac_lines_r[filtered_ac_lines_r].index)

        # DC links
        cross_border_links = n.links.bus0.str[:2] != n.links.bus1.str[:2]
        filtered_dc_lines = (n.links.carrier == 'DC') & (n.links.bus0.str[:2] == country) & cross_border_links
        dc_lines = n.links_t.p0.filter(items=filtered_dc_lines[filtered_dc_lines].index)
        # dc_lines = dc_lines.drop(columns=["relation/6914309-500-DC"], errors='ignore')

        filtered_dc_lines_r = (n.links.carrier == 'DC') & (n.links.bus1.str[:2] == country) & cross_border_links
        dc_lines_r = n.links_t.p1.filter(items=filtered_dc_lines_r[filtered_dc_lines_r].index)
        # dc_lines_r = dc_lines_r.drop(columns=["relation/6914309-500-DC-reversed"], errors='ignore')
        # Calculate import/export
        ac_import, ac_export = calculate_import_export_separate(ac_lines, "ac0", n, marginal_price)
        ac_r_import, ac_r_export = calculate_import_export_separate(ac_lines_r, "acr", n, marginal_price)
        dc_import, dc_export = calculate_import_export_separate(dc_lines, "dc0", n, marginal_price)
        dc_r_import, dc_r_export = calculate_import_export_separate(dc_lines_r, "dcr", n, marginal_price)

        # Total import/export
        total_import_cost = ac_import.add(ac_r_import, fill_value=0).add(dc_import, fill_value=0).add(dc_r_import, fill_value=0)
        total_export_revenue = ac_export.add(ac_r_export, fill_value=0).add(dc_export, fill_value=0).add(dc_r_export, fill_value=0)

        # Net cost
        total_import_cost_sum = total_import_cost.sum().sum()
        total_export_revenue_sum = total_export_revenue.sum().sum()
        net_cost = total_import_cost_sum - total_export_revenue_sum

        results[planning_horizon] = net_cost

    # Convert results to DataFrame
    results_df = pd.DataFrame.from_dict(results, orient='index', columns=['net_cost'])
    results_df.index.name = 'planning_horizon'

    return results_df

def calculate_h2_import_export_costs(country, planning_horizons):
    def calculate_import_export_separate(flows, direction, network, marginal_prices):
     import_cost = pd.DataFrame(index=flows.index, columns=flows.columns)
     export_revenue = pd.DataFrame(index=flows.index, columns=flows.columns)

     for pipeline in flows.columns:
        # Always use network.links for pipelines
        if direction == 'forward':
            bus0 = network.links.loc[pipeline, "bus0"]
            bus1 = network.links.loc[pipeline, "bus1"]
        else:  # reverse
            bus0 = network.links.loc[pipeline, "bus1"]
            bus1 = network.links.loc[pipeline, "bus0"]

        if bus0 not in marginal_prices.columns or bus1 not in marginal_prices.columns:
            continue

        price_bus0 = marginal_prices[bus0]
        price_bus1 = marginal_prices[bus1]

        for t in flows.index:
            flow = flows.at[t, pipeline]
            if flow < 0:  # Import
                import_cost.at[t, pipeline] = -flow * price_bus1[t]
                export_revenue.at[t, pipeline] = 0
            else:  # Export
                import_cost.at[t, pipeline] = 0
                export_revenue.at[t, pipeline] = flow * price_bus0[t]

     return import_cost, export_revenue

    results = {}

    for planning_horizon in planning_horizons:
        # Load network
        n = loaded_files[planning_horizon]

        # Marginal prices
        marginal_price_filter = n.buses.carrier == "H2"
        marginal_price = n.buses_t.marginal_price.filter(items=marginal_price_filter[marginal_price_filter].index)
        # marginal_price = marginal_price.drop(columns=["GB3 0"], errors='ignore')

        cross_border_links = n.links.bus0.str[:2] != n.links.bus1.str[:2]
        filtered_h2_pipelines = (n.links.carrier == 'H2 pipeline') & (n.links.bus0.str[:2] == country) & cross_border_links
        h2_pipelines = n.links_t.p0.filter(items=filtered_h2_pipelines[filtered_h2_pipelines].index)

        filtered_h2_pipelines_r = (n.links.carrier == 'H2 pipeline') & (n.links.bus1.str[:2] == country) & cross_border_links
        h2_pipelines_r = n.links_t.p1.filter(items=filtered_h2_pipelines_r[filtered_h2_pipelines_r].index)

        # DC links
        filtered_h2_retro_pipelines = (n.links.carrier == 'H2 pipeline retrofitted') & (n.links.bus0.str[:2] == country) & cross_border_links
        h2_retro_pipelines = n.links_t.p0.filter(items=filtered_h2_retro_pipelines[filtered_h2_retro_pipelines].index) 


        filtered_h2_retro_pipelines_r = (n.links.carrier == 'H2 pipeline retrofitted') & (n.links.bus1.str[:2] == country) & cross_border_links
        h2_retro_pipelines_r = n.links_t.p1.filter(items=filtered_h2_retro_pipelines_r[filtered_h2_retro_pipelines_r].index)

        # Calculate import/export
        h2_import, h2_export = calculate_import_export_separate(h2_pipelines, "ac0", n, marginal_price)
        h2_r_import, h2_r_export = calculate_import_export_separate(h2_pipelines_r, "acr", n, marginal_price)
        h2_retro_import, h2_retro_export = calculate_import_export_separate(h2_retro_pipelines, "dc0", n, marginal_price)
        h2_retro_r_import, h2_retro_r_export = calculate_import_export_separate(h2_retro_pipelines_r, "dcr", n, marginal_price)

        # Total import/export
        total_import_cost = h2_import.add(h2_r_import, fill_value=0).add(h2_retro_import, fill_value=0).add(h2_retro_r_import, fill_value=0)
        total_export_revenue = h2_export.add(h2_r_export, fill_value=0).add(h2_retro_export, fill_value=0).add(h2_retro_r_export, fill_value=0)

        # Net cost
        total_import_cost_sum = total_import_cost.sum().sum()
        total_export_revenue_sum = total_export_revenue.sum().sum()
        net_cost = total_import_cost_sum - total_export_revenue_sum

        results[planning_horizon] = net_cost

    # Convert results to DataFrame
    results_df = pd.DataFrame.from_dict(results, orient='index', columns=['net_cost'])
    results_df.index.name = 'planning_horizon'

    return results_df


def costs(countries,results):
    costs = {}
    fn = snakemake.input.costs
    options = pd.read_csv(fn, index_col=[0, 1]).sort_index()
    for country in countries:
      net_costs_elc = calculate_elec_import_export_costs(country, planning_horizons)
      net_costs_h2 = calculate_h2_import_export_costs(country, planning_horizons)
      uranium = pd.read_excel(f"results/{study}/htmls/ChartData_{country}.xlsx",sheet_name="Chart 22", index_col=0,skiprows=2)
      uranium = uranium["Uranium"]
      coal_df = pd.read_csv(f"results/{study}/country_csvs/total_imports_{country}.csv", index_col=0)
      coal = coal_df["imp_cms_pe"]
      exports = pd.read_csv(f"results/{study}/country_csvs/exports_{country}.csv", index_col=0)
      exports = exports.clip(lower=0)
      exports = exports.where(exports <= 0, -exports)
      df=pd.read_csv(f"results/{study}/csvs/nodal_costs.csv", index_col=2)
      df = df.iloc[:, 2:]
      df = df.iloc[3:, :]
      df.index = df.index.str[:2]
      if country != 'EU':
       df = df[df.index == country]
      else:
       df = df
      df = df.rename(columns={'Unnamed: 3': 'tech', f'{cluster}': '2025',f'{cluster}.1': '2030',f'{cluster}.2': '2035',f'{cluster}.3': '2040',f'{cluster}.4': '2045',f'{cluster}.5': '2050'})
      df[['2025','2030','2035', '2040','2045','2050']] = df[['2025','2030','2035', '2040','2045','2050']].apply(pd.to_numeric, errors='coerce')
      df = df.groupby('tech').sum().reset_index()
      
      if country != 'EU':
       elc_row = {"tech": "Electricity Imports/Exports"}
       for year in net_costs_elc.index:
          elc_row[str(year)] = net_costs_elc.loc[year, 'net_cost']
       df = pd.concat([df, pd.DataFrame([elc_row])], ignore_index=True)
       h2_row = {"tech": "Hydrogen Imports/Exports"}
       for year in net_costs_h2.index:
          h2_row[str(year)] = net_costs_h2.loc[year, 'net_cost']
       df = pd.concat([df, pd.DataFrame([h2_row])], ignore_index=True)
       ura_row = {"tech": "nuclear fuel"}
       for year in uranium.index:
           ura_row[str(year)] = uranium.loc[year] * float(options.loc[("uranium", "fuel")]) * 1e6  
       df = pd.concat([df, pd.DataFrame([ura_row])], ignore_index=True)
       coal_row = {"tech": "coal fuel"}
       for year in coal.index:
           coal_row[str(year)] = coal.loc[year] * float(options.loc[("coal", "fuel")]) * 1e6  
       df = pd.concat([df, pd.DataFrame([coal_row])], ignore_index=True)
       biomass_exports = exports["enc_pe_exp"]
       bm_row = {"tech": "biomass exports"}
       for year in biomass_exports.index:
           bm_row[str(year)] = biomass_exports.loc[year] * float(options.loc[("biomass", "fuel")]) * 1e6
       df = pd.concat([df, pd.DataFrame([bm_row])], ignore_index=True)

       biogas_exports = exports["gaz_se_exp"]
       biogas_row = {"tech": "biogas exports"}
       for year in biogas_exports.index:
           biogas_row[str(year)] = biogas_exports.loc[year] * float(options.loc[("biogas", "fuel")]) * 1e6
       df = pd.concat([df, pd.DataFrame([biogas_row])], ignore_index=True)

       meth_exports = exports["met_fe_exp"]
       meth_row = {"tech": "methanol exports"}
       for year in meth_exports.index:
           meth_row[str(year)] = meth_exports.loc[year] * methanol_fuel * 1e6
       df = pd.concat([df, pd.DataFrame([meth_row])], ignore_index=True)

       amm_exports = exports["amm_fe_exp"]
       amm_row = {"tech": "ammonia exports"}
       for year in amm_exports.index:
           amm_row[str(year)] = amm_exports.loc[year] * ammonia_fuel * 1e6
       df = pd.concat([df, pd.DataFrame([amm_row])], ignore_index=True)
       
       if 'nuclear' not in df['tech'].values:
          new_row = pd.DataFrame(
              [['nuclear'] + [0]*len(planning_horizons)],
              columns=['tech'] + [str(h) for h in planning_horizons]
          )
          df = pd.concat([df, new_row], ignore_index=True)

       for planning_horizon in planning_horizons:

          n = loaded_files[planning_horizon]

          nuclear_links = n.links[
              n.links.index.str.contains(country) &
              n.links.index.str.contains("nuclear")
          ]
          nuc_inv=float(options.loc[("nuclear", "capital_cost")])
          efficiency = nuclear_links.efficiency.mean()
          nuclear_cap = nuclear_links.p_nom_opt.sum() * efficiency
          df.loc[df['tech'] == 'nuclear', str(planning_horizon)] = nuclear_cap * nuc_inv
     
      df['tech'] = df['tech'].map(rename_techs_tyndp)
      df = df.groupby('tech').sum().reset_index()

      result_df = df
      result_df.fillna(0, inplace=True)
      
      result_df.iloc[:, 1:] = result_df.iloc[:, 1:]#.clip(lower=0) 
      if not result_df.empty:
            years = ['2025','2030','2035', '2040','2045','2050']
            technologies = result_df['tech'].unique()
            
            costs[country] = result_df.set_index('tech').loc[technologies, years]

    for planning_horizon in planning_horizons:
      planning_horizon_str = str(planning_horizon)

      if planning_horizon in results:
        cos_ac_df = results[planning_horizon]['cos_ac']
        cos_dc_df = results[planning_horizon]['cos_dc']

        for country in countries:
            if country != 'EU':
                ac_transmission_values = cos_ac_df.loc[country, 'transmission_AC']
                dc_transmission_values = cos_dc_df.loc[country, 'transmission_DC']
                costs[country].loc['AC', planning_horizon_str] = ac_transmission_values
                costs[country].loc['DC', planning_horizon_str] = dc_transmission_values
            else:
                # Sum over all non-EU countries
                total_ac = cos_ac_df.loc[cos_ac_df.index != 'EU', 'transmission_AC'].sum()
                total_dc = cos_dc_df.loc[cos_dc_df.index != 'EU', 'transmission_DC'].sum()
                costs['EU'].loc['AC', planning_horizon_str] = total_ac
                costs['EU'].loc['DC', planning_horizon_str] = total_dc
    for country in countries:
     costs[country].index = costs[country].index.map(rename_techs_tyndp)
     costs[country] = costs[country].groupby(costs[country].index).sum()
    for country, dataframe in costs.items():
         # Specify the file path within the output directory
         file_path = f"results/{study}/country_csvs/{country}_costs.csv"
    
         # Save the DataFrame to a CSV file
         dataframe.to_csv(file_path, index=True)

         print(f"CSV file for {country} saved at: {file_path}")
    return costs

def clustered_costs(countries):
 c_costs = {}
 def rename_techs_ty(tech):
    tech = rename_techs(tech)
    if tech in ["synthetic fuels & techs","power-to-liquid","power-to-heat","power-to-gas","CCU","CCUS","DAC","boilers","load shedding","Electrolysers","Direct Connected Electrolysers"]:
        return "Uses"
    elif tech in ["transmission lines","distribution network","battery storage","TES & H2 storage","H2 & gas pipelines"]:
        return "Networks"
    elif tech in ["solar","solar PV","onshore wind","offshore wind","nuclear","hydroelectricity","biomass fuel & techs","CHP","geothermal"]:
        return "Power Plants"
    elif tech in ["Electricity Imports/Exports","Hydrogen Imports/Exports","Fossil fuels & powerplants"]:
          return "Imports"
    else:
        return tech
 for country in countries:
  clustered_costs = pd.read_csv(f"results/{study}/country_csvs/{country}_costs.csv")
  clustered_costs['tech'] = clustered_costs['tech'].map(rename_techs_ty)
  clustered_costs = clustered_costs.groupby('tech').sum().reset_index()
  c_costs[country] = clustered_costs.set_index('tech')
 for country, dataframe in c_costs.items():
      # Specify the file path within the output directory
      file_path = f"results/{study}/country_csvs/{country}_clustered_costs.csv"
 
      # Save the DataFrame to a CSV file
      dataframe.to_csv(file_path, index=True)

      print(f"CSV file for {country} saved at: {file_path}")
  
 return c_costs


def Investment_costs(countries,results):
    fn = snakemake.input.costs
    options = pd.read_csv(fn, index_col=[0, 1]).sort_index()
    investment_costs = {}
    for country in countries:
      df=pd.read_csv(f"results/{study}/csvs/nodal_costs.csv", index_col=2)
      df.drop(df.columns[1], axis=1, inplace=True)
      df = df.iloc[3:, :]
      df.index = df.index.str[:2]
      if country != 'EU':
       df = df[df.index == country]
      else:
       df=df
      df = df.rename(columns={'cluster': 'Costs','Unnamed: 3': 'tech', f'{cluster}': '2025',f'{cluster}.1': '2030',f'{cluster}.2': '2035',f'{cluster}.3': '2040',f'{cluster}.4': '2045',f'{cluster}.5': '2050'})
      df[['2025','2030','2035', '2040','2045','2050']] = df[['2025','2030','2035', '2040','2045','2050']].apply(pd.to_numeric, errors='coerce')
      df = df[df['Costs'] == 'capital']
      df = df.groupby('tech').sum().reset_index()
      df = df.drop(columns=['Costs'])
      if country != 'EU':
       if 'nuclear' not in df['tech'].values:
          new_row = pd.DataFrame(
              [['nuclear'] + [0]*len(planning_horizons)],
              columns=['tech'] + [str(h) for h in planning_horizons]
          )
          df = pd.concat([df, new_row], ignore_index=True)

       for planning_horizon in planning_horizons:

          n = loaded_files[planning_horizon]

          nuclear_links = n.links[
              n.links.index.str.contains(country) &
              n.links.index.str.contains("nuclear")
          ]
          nuc_inv=float(options.loc[("nuclear", "capital_cost")])
          efficiency = nuclear_links.efficiency.mean()
          nuclear_cap = nuclear_links.p_nom_opt.sum() * efficiency
          df.loc[df['tech'] == 'nuclear', str(planning_horizon)] = nuclear_cap * nuc_inv
      df['tech'] = df['tech'].map(rename_techs_tyndp)
      df = df.groupby('tech').sum().reset_index()
      tech_mapping = {'Fossil fuels & powerplants':'Fossil fuel powerplants' }
      df['tech'] = df['tech'].replace(tech_mapping)
      condition = df[['2025','2030','2035', '2040','2045','2050']].eq(0).all(axis=1)
      df = df[~condition]
      
      result_df = df
      result_df.fillna(0, inplace=True)
      if not result_df.empty:
            years = ['2025','2030','2035', '2040','2045','2050']
            technologies = result_df['tech'].unique()
            
            investment_costs[country] = result_df.set_index('tech').loc[technologies, years]

    for planning_horizon in planning_horizons:
      planning_horizon_str = str(planning_horizon)

      if planning_horizon in results:
        cos_ac_df = results[planning_horizon]['cos_ac']
        cos_dc_df = results[planning_horizon]['cos_dc']

        for country in countries:
            if country != 'EU':
                ac_transmission_values = cos_ac_df.loc[country, 'transmission_AC']
                dc_transmission_values = cos_dc_df.loc[country, 'transmission_DC']
                investment_costs[country].loc['AC', planning_horizon_str] = ac_transmission_values
                investment_costs[country].loc['DC', planning_horizon_str] = dc_transmission_values
            else:
                # Sum over all non-EU countries
                total_ac = cos_ac_df.loc[cos_ac_df.index != 'EU', 'transmission_AC'].sum()
                total_dc = cos_dc_df.loc[cos_dc_df.index != 'EU', 'transmission_DC'].sum()
                investment_costs['EU'].loc['AC', planning_horizon_str] = total_ac
                investment_costs['EU'].loc['DC', planning_horizon_str] = total_dc
    for country in countries:
     investment_costs[country].index = investment_costs[country].index.map(rename_techs_tyndp)
     investment_costs[country] = investment_costs[country].groupby(investment_costs[country].index).sum()
    for country, dataframe in investment_costs.items():
         # Specify the file path within the output directory
         file_path = f"results/{study}/country_csvs/{country}_investment costs.csv"
    
         # Save the DataFrame to a CSV file
         dataframe.to_csv(file_path, index=True)

         print(f"CSV file for {country} saved at: {file_path}")
    return investment_costs 

def rename_techs_tynd(tech):
    tech = rename_techs(tech)
    if tech in ["H2 Electrolysis", "methanation","helmeth", "H2 liquefaction","heat pump","resistive heater","Fischer-Tropsch","BioSNG CC","HVC to air","battery storage","municipal solid waste","municipal solid waste transport",
                "electricity distribution grid","CHP", "H2 Fuel Cell","CCGT","OCGT","H2 turbine","solid biomass powerplants","coal powerplants", "oil powerplants","geothermal heat", "geothermal organic rankine cycle","heat vent",
                "battery charger", "battery discharger","battery", "Li ion", "EV charger", "V2G","hot water storage", "H2", "H2 storage","biomass to liquid CC","biomass-to-methanol CC","electrobiofuels","geothermal district heat",
                "biomass boiler", "oil boiler","gas boiler","solar","Wind","co2 sequestered","CO2 sequestration", "co2", "SMR CC", "process emissions CC","process emissions", "solid biomass for industry CC", "gas for industry CC","DAC",
                "hydroelectricity","SMR", "ammonia cracker", "Haber-Bosch", "BioSNG", "biomass to liquid","methanol","ammonia","methanolisation","shipping methanol","non-sequestered HVC","offshore wind (Float)","solar-hsat","water pits",
                "air heat pump","air-sourced heat pump","ground heat pump","solar PV","solar rooftop", "offshore wind","offshore wind (AC)", "offshore wind (DC)","solid biomass to hydrogen","water pits charger","water pits discharger",
                "onshore wind", "solar thermal","H2 pipeline", "gas pipeline","gas pipeline new","H2 pipeline retrofitted","transmission lines","Transmission Lines","biomass-to-methanol","industry methanol", "OCGT methanol","CO2 pipeline",
                "solar Electrolyser","onwind Electrolyser","offwind-ac Electrolyser","offwind-dc Electrolyser","Direct Connected Electrolysers","geothermal"]:
        return "VOM of Technologies"
    elif tech in ["biomass", "solid biomass", "solid biomass for industry", "biogas", "solid biomass transport", "biomass exports", "biogas exports","solid biomass import","biomass-to-methanol","unsustainable bioliquids","unsustainable solid biomass"]:
          return "Biomass"
    elif tech in ["shipping oil", "naphtha for industry", "land transport oil", "kerosene for aviation", "agriculture machinery oil", "oil","gas", "coal for industry","gas for industry","coal","lignite","coal fuel","gas fuel","oil fuel","oil primary", "oil refining"]:
          return "Fossil Fuels"
    elif "load" in tech:
        return "load shedding"
    elif "electricity imports/exports" in tech:
        return "Electricity Imports/Exports"
    elif "hydrogen imports/exports" in tech:
        return "Hydrogen Imports/Exports"
    elif tech in ["uranium", "nuclear", "nuclear fuel"]:
          return "Nuclear"
    elif tech in ["methanol exports", "ammonia exports"]:
          return "Synthetic Fuels"
    else:
        return tech
def operational_costs(countries):
    operational_costs = {}
    fn = snakemake.input.costs
    options = pd.read_csv(fn, index_col=[0, 1]).sort_index()
    for country in countries:
      net_costs_elc = calculate_elec_import_export_costs(country, planning_horizons)
      net_costs_h2 = calculate_h2_import_export_costs(country, planning_horizons)
      uranium = pd.read_excel(f"results/{study}/htmls/ChartData_{country}.xlsx",sheet_name="Chart 22", index_col=0,skiprows=2)
      uranium = uranium["Uranium"]
      coal_df = pd.read_csv(f"results/{study}/country_csvs/total_imports_{country}.csv", index_col=0)
      coal = coal_df["imp_cms_pe"]
      exports = pd.read_csv(f"results/{study}/country_csvs/exports_{country}.csv", index_col=0)
      exports = exports.clip(lower=0)
      exports = exports.where(exports <= 0, -exports)
      df=pd.read_csv(f"results/{study}/csvs/nodal_costs.csv", index_col=2)
      df.drop(df.columns[1], axis=1, inplace=True)
      df = df.iloc[3:, :]
      df.index = df.index.str[:2]
      if country != 'EU':
       df = df[df.index == country]
      else:
       df=df
      df = df.rename(columns={'cluster': 'Costs','Unnamed: 3': 'tech', f'{cluster}': '2025',f'{cluster}.1': '2030',f'{cluster}.2': '2035',f'{cluster}.3': '2040',f'{cluster}.4': '2045',f'{cluster}.5': '2050'})
      df[['2025','2030','2035', '2040','2045','2050']] = df[['2025','2030','2035', '2040','2045','2050']].apply(pd.to_numeric, errors='coerce')
      df = df[df['Costs'] == 'marginal']
      df = df.groupby('tech').sum().reset_index()
      df = df.drop(columns=['Costs'])
      if country != 'EU':
       elc_row = {"tech": "electricity imports/exports"}
       for year in net_costs_elc.index:
          elc_row[str(year)] = net_costs_elc.loc[year, 'net_cost']
       df = pd.concat([df, pd.DataFrame([elc_row])], ignore_index=True)
       h2_row = {"tech": "hydrogen imports/exports"}
       for year in net_costs_h2.index:
          h2_row[str(year)] = net_costs_h2.loc[year, 'net_cost']
       df = pd.concat([df, pd.DataFrame([h2_row])], ignore_index=True)
       ura_row = {"tech": "nuclear fuel"}
       for year in uranium.index:
           ura_row[str(year)] = uranium.loc[year] * float(options.loc[("uranium", "fuel")]) * 1e6  
       df = pd.concat([df, pd.DataFrame([ura_row])], ignore_index=True)
       coal_row = {"tech": "coal fuel"}
       for year in coal.index:
           coal_row[str(year)] = coal.loc[year] * float(options.loc[("coal", "fuel")]) * 1e6  
       df = pd.concat([df, pd.DataFrame([coal_row])], ignore_index=True)
       biomass_exports = exports["enc_pe_exp"]
       bm_row = {"tech": "biomass exports"}
       for year in biomass_exports.index:
           bm_row[str(year)] = biomass_exports.loc[year] * float(options.loc[("biomass", "fuel")]) * 1e6
       df = pd.concat([df, pd.DataFrame([bm_row])], ignore_index=True)

       biogas_exports = exports["gaz_se_exp"]
       biogas_row = {"tech": "biogas exports"}
       for year in biogas_exports.index:
           biogas_row[str(year)] = biogas_exports.loc[year] * float(options.loc[("biogas", "fuel")]) * 1e6
       df = pd.concat([df, pd.DataFrame([biogas_row])], ignore_index=True)

       meth_exports = exports["met_fe_exp"]
       meth_row = {"tech": "methanol exports"}
       for year in meth_exports.index:
           meth_row[str(year)] = meth_exports.loc[year] * methanol_fuel * 1e6
       df = pd.concat([df, pd.DataFrame([meth_row])], ignore_index=True)

       amm_exports = exports["amm_fe_exp"]
       amm_row = {"tech": "ammonia exports"}
       for year in amm_exports.index:
           amm_row[str(year)] = amm_exports.loc[year] * ammonia_fuel * 1e6
       df = pd.concat([df, pd.DataFrame([amm_row])], ignore_index=True)
      df['tech'] = df['tech'].map(rename_techs_tynd)
      df = df.groupby('tech').sum().reset_index()
      condition = df[['2025','2030','2035', '2040','2045','2050']].eq(0).all(axis=1)
      df = df[~condition]
      
      result_df = df
      result_df.fillna(0, inplace=True)
      if not result_df.empty:
            years = ['2025','2030','2035', '2040','2045','2050']
            technologies = result_df['tech'].unique()
            
            operational_costs[country] = result_df.set_index('tech').loc[technologies, years]

      
    for country, dataframe in operational_costs.items():
         # Specify the file path within the output directory
         file_path = f"results/{study}/country_csvs/{country}_operational costs.csv"
    
         # Save the DataFrame to a CSV file
         dataframe.to_csv(file_path, index=True)

         print(f"CSV file for {country} saved at: {file_path}")
    return operational_costs

def co2_price(countries):

    for country in countries:
        co2_values = []

        for planning_horizon in planning_horizons:
            if planning_horizon == 2025:
             co2_values.append(0)
             continue
            n = loaded_files_baseline[planning_horizon]

            if country == "EU":
                #mean CO2 price across all countries
                mus = []

                for c in countries:
                    if c == "EU":
                        continue

                    constraint = f"co2_limit_per_country{c}"

                    if constraint in n.global_constraints.index:
                        mus.append(
                            abs(
                                n.global_constraints.loc[
                                    constraint, "mu"
                                ]
                            )
                        )

                co2_value = np.mean(mus) if mus else 0
            else:
                co2_value = abs(
                    n.global_constraints.loc[
                        f"co2_limit_per_country{country}", "mu"
                    ]
                )

            co2_values.append(round(co2_value, 2))

        df = pd.DataFrame({
         "year": planning_horizons,
         "co2_price": co2_values
         })

        fig = px.bar(
          df,
          x="year",
          y="co2_price",
          title=f"CO2 Prices - {country}",
          labels={
                  "year": "",
                  "co2_price": "CO2 Price [€/tCO2]"},
          color_discrete_sequence=["red"])
        fig.update_layout(height=600, width=1000)

        html_filename = f"{country}_co2_price.html"
        output_folder = f"results/{study}/htmls/raw_html"

        os.makedirs(output_folder, exist_ok=True)

        html_filepath = os.path.join(output_folder, html_filename)
        fig.write_html(
            html_filepath,
            full_html=False,
            include_plotlyjs=False
        )


def rename_techs_tyndpp(tech):
    tech = rename_techs(tech)
    if "heat pump" in tech or "resistive heater" in tech:
        return "power-to-heat"
    elif tech in ["H2 Electrolysis"]:
        return "Electrolysers"
    elif tech in ["vre H2 Electrolysis"]:
        return "Direct Connected Electrolysers"
    elif tech in ["solar Electrolyser","onwind Electrolyser","offwind-ac Electrolyser","offwind-dc Electrolyser"]:
        return "VRE connected Electrolysers"
    elif tech in ["electricity distribution grid"]:
        return "distribution network"
    elif tech in [ "CHP", "H2 Fuel Cell","H2 turbine"]:
        return "CHP"
    elif "solar" in tech:
        return "solar"
    elif tech == "Fischer-Tropsch":
        return "power-to-liquid"
    elif "offshore wind" in tech:
        return "offshore wind"
    elif tech in ["solar vre"]:
        return "solar rfnbo"
    elif tech == "Fischer-Tropsch":
        return "power-to-liquid"
    elif tech in ["offwind-ac vre","offwind-dc vre"]:
        return "offshore rfnbo"
    elif tech in ["onwind vre"]:
        return "onshore wind rfnbo"
    elif tech in ["hot water storage","water pits","water pits charger","water pits discharger"]:
        return "thermal energy storage"
    elif "load" in tech:
        return "load shedding"
    elif tech in ["nuclear"]:
          return "nuclear"
    elif tech in ["H2 pipeline", "gas pipeline","gas pipeline new","H2 pipeline retrofitted"]:
          return "H2 & gas pipelines"
    else:
        return tech
def capacities(countries,results):
    capacities = {}
    options = pd.read_csv(fn, index_col=[0, 1]).sort_index()
    for country in countries:
      cf = pd.read_csv(f"results/{study}/csvs/nodal_capacities.csv", index_col=1)
      cf = cf.iloc[:, 1:]
      cf = cf.iloc[3:, :]
      cf.index = cf.index.str[:2]
      if country != 'EU':
       cf = cf[cf.index == country]
      else:
       cf=cf
      cf = cf.rename(columns={'Unnamed: 2': 'tech',  f'{cluster}': '2025',f'{cluster}.1': '2030',f'{cluster}.2': '2035',f'{cluster}.3': '2040',f'{cluster}.4': '2045',f'{cluster}.5': '2050'})
      columns_to_convert = ['2025','2030','2035', '2040','2045','2050']
      cf[columns_to_convert] = cf[columns_to_convert].apply(pd.to_numeric, errors='coerce')
      cf = cf.groupby('tech').sum().reset_index()
      if 'nuclear' not in cf['tech'].values:
          new_row = pd.DataFrame(
              [['nuclear'] + [0]*len(planning_horizons)],
              columns=['tech'] + [str(h) for h in planning_horizons]
          )
          cf = pd.concat([cf, new_row], ignore_index=True)

      for planning_horizon in planning_horizons:

          n = loaded_files[planning_horizon]
          if country != 'EU':
           nuclear_links = n.links[
              n.links.index.str.contains(country) &
              n.links.index.str.contains("nuclear")
          ]
          else:
           nuclear_links = n.links[
             n.links.index.str.contains("nuclear")
         ]
          efficiency = nuclear_links.efficiency.mean()
          nuclear_cap = nuclear_links.p_nom_opt.sum() * efficiency
          cf.loc[cf['tech'] == 'nuclear', str(planning_horizon)] = nuclear_cap
      cf['tech'] = cf['tech'].map(rename_techs_tyndpp)
      cf = cf.groupby('tech').sum().reset_index()
      result_df = cf
      result_df.fillna(0, inplace=True)
      if country == 'EU':
           result_df['tech'] = result_df['tech'].map(rename_techs_tyndpp)
           result_df = result_df.groupby('tech').sum().reset_index()
      if not result_df.empty:
            years = ['2025','2030','2035', '2040','2045','2050']
            technologies = result_df['tech'].unique()

            capacities[country] = result_df.set_index('tech').loc[technologies, years]
    for country in countries:
       for planning_horizon in planning_horizons:
        # Convert planning_horizon to string for column name
        planning_horizon_str = str(planning_horizon)

        # Check if the planning horizon key exists in the results dictionary
        if planning_horizon in results:
         cap_ac_df = results[planning_horizon]['cap_ac']
         cap_dc_df = results[planning_horizon]['cap_dc']
         if country != 'EU':
            ac_transmission_values = cap_ac_df.loc[country, 'transmission_AC']
            dc_transmission_values = cap_dc_df.loc[country, 'transmission_DC']
            # Assign values to existing columns for each year
            capacities[country].loc['AC', planning_horizon_str] = ac_transmission_values
            capacities[country].loc['DC', planning_horizon_str] = dc_transmission_values
         else:
            total_ac = cap_ac_df.loc[cap_ac_df.index != 'EU', 'transmission_AC'].sum()
            total_dc = cap_dc_df.loc[cap_dc_df.index != 'EU', 'transmission_DC'].sum()
            capacities['EU'].loc['AC', planning_horizon_str] = total_ac
            capacities['EU'].loc['DC', planning_horizon_str] = total_dc
    for country in countries:
        capacities[country].index = capacities[country].index.map(rename_techs_tyndpp)
        capacities[country] = capacities[country].groupby(capacities[country].index).sum()
    for country in countries:
        
       for country, dataframe in capacities.items():
        # Specify the file path where you want to save the CSV file
        file_path = f"results/{study}/country_csvs/{country}_capacities.csv"
    
         # Save the DataFrame to a CSV file
        dataframe.to_csv(file_path, index=True)

        print(f"CSV file for {country} saved at: {file_path}")  

    return capacities

def storage_capacities(countries):
    s_capacities = {}
    for country in countries:
      cf = pd.read_csv(f"results/{study}/csvs/nodal_capacities.csv", index_col=1)
      cf = cf[cf['cluster'] == 'Store']
      cf = cf.iloc[1:, :]
      cf.index = cf.index.str[:2]
      if country != 'EU':
       cf = cf[cf.index == country]
      else:
       cf=cf
      cf = cf.rename(columns={'Unnamed: 2': 'tech',   f'{cluster}': '2025',f'{cluster}.1': '2030',f'{cluster}.2': '2035',f'{cluster}.3': '2040',f'{cluster}.4': '2045',f'{cluster}.5': '2050'})
      columns_to_convert = ['2025','2030','2035', '2040','2045','2050']
      cf[columns_to_convert] = cf[columns_to_convert].apply(pd.to_numeric, errors='coerce')
      cf = cf.groupby('tech').sum().reset_index()
      result_df = cf
      result_df.fillna(0, inplace=True)
      result_df['tech'] = result_df['tech'].replace({'urban central water pits': 'Thermal Energy Storage', 'battery':'Grid-scale battery', 'gas':'Gas storage'})
      if not result_df.empty:
            years = ['2025','2030','2035', '2040','2045','2050']
            technologies = result_df['tech'].unique()

            s_capacities[country] = result_df.set_index('tech').loc[technologies, years]
            for country, dataframe in s_capacities.items():
             # Specify the file path where you want to save the CSV file
             file_path = f"results/{study}/country_csvs/{country}_storage_capacities.csv"
         
              # Save the DataFrame to a CSV file
             dataframe.to_csv(file_path, index=True)

             print(f"CSV file for {country} saved at: {file_path}") 

    return s_capacities

def plot_demands(countries):
    colors = snakemake.params.plotting["tech_colors"]
    colors["methane"] = "#f0833a"
    colors["Non-energy demand"] = "#c5c9c7" 
    colors["hydrogen for industry"] = "#ffbacd"
    colors["agriculture electricity"] = "#11875d"
    colors["agriculture heat"] = "#fbeeac"
    colors["agriculture oil"] = "#fddc5c"
    colors["electricity demand of residential and tertairy"] = "#01889f"
    colors["gas for Industry"] = "#f0833a"
    colors["electricity for Industry"] = "#06b1c4"
    colors["aviation oil demand"] = "#fd5956"
    colors["land transport EV"] = "#95d0fc"
    colors["land transport hydrogen demand"] = "#82cbb2"
    colors["oil to transport demand"] = "#9a0200"
    colors["low-temperature heat for industry"] = "#c14a09"
    colors["naphtha for non-energy"] = "#c5c9c7"
    colors["shipping methanol"] = "#95d0fc"
    colors["shipping hydrogen"] = "#fcc006"
    colors["shipping oil"] = "#fcc006"
    colors["solid biomass for Industry"] = "#11875d"
    colors["solid biomass"] = "#11875d"
    colors["Residential and tertiary DH demand"] = "#f0833a"
    colors["Residential and tertiary heat demand"] = "#ffb07c"
    colors["electricity demand for rail network"] = "#82cbb2"
    colors["H2 for non-energy"] = "#32bf84" 
    colors["Oil for industry"] = "#ffbacd"
    
    mapping = {
        "hydrogen for industry": "hydrogen",
        "H2 for non-energy": "Non-energy",
        "shipping hydrogen": "hydrogen",
        "shipping oil": "oil",
        "agriculture electricity": "electricity",
        "agriculture heat": "heat",
        "agriculture oil": "oil",
        "electricity demand of residential and tertairy": "electricity",
        "gas for Industry": "methane",
        "electricity for Industry": "electricity",
        "aviation oil demand": "oil",
        "land transport EV": "electricity",
        "land transport hydrogen demand": "hydrogen",
        "oil to transport demand": "oil",
        "low-temperature heat for industry": "heat",
        "naphtha for non-energy": "Non-energy",
        "electricity demand for rail network": "electricity",
        "Residential and tertiary DH demand": "heat",
        "Residential and tertiary heat demand": "heat",
        "solid biomass for Industry": "solid biomass",
        "NH3":"hydrogen",
        "Oil for industry": "oil"
    }
    mapping_eu = {
            "preshydcfind": "hydrogen for industry",
            "preshydcfneind": "H2 for non-energy",
            "preshydwati": "shipping hydrogen",
            "preslqfcffrewati": "shipping oil",
            "preselccfagr": "agriculture electricity",
            "presvapcfagr": "agriculture heat",
            "prespetcfagr": "agriculture oil",
            "preselccfres": "electricity demand of residential and tertairy",
            "presgazcfind": "gas for Industry",
            "presgazcfindd": "gas for Industry",
            "preselccfind": "electricity for Industry",
            "preslqfcfavi": "aviation oil demand",
            "preselccftra": "land transport EV",
            "preshydcftra": "land transport hydrogen demand",
            "preslqfcftra": "oil to transport demand",
            "presvapcfind": "low-temperature heat for industry",
            "prespetcfneind": "naphtha for non-energy",
            "preserail": "electricity demand for rail network",
            "presvapcfdhs": "Residential and tertiary DH demand",
            "demandheat": "Residential and tertiary heat demand",
            "demandheata": "Residential and tertiary heat demand",
            "demandheatb": "Residential and tertiary heat demand",
            "demandheats": "Residential and tertiary heat demand",
            "presenccfind": "solid biomass for Industry",
            "presenccfindd": "solid biomass for Industry",
            "preammind":"NH3",
            "prespetcfind": "Oil for industry"
    }
   
    for country in countries:
        data = pd.read_excel(f"results/{study}/sepia/inputs{country}.xlsx", index_col=0)
        if country != 'EU':
         columns_to_drop = ['source', 'target']
         data = data.drop(columns=columns_to_drop)
         data = data.groupby(data.index).sum()
        else:
         columns_to_drop = ['source', 'target']
         data = data.drop(columns=columns_to_drop)
         data.rename(index=mapping_eu, inplace=True)
         data = data.groupby(data.index).sum()

        # Apply your mapping to the data
        data = data[data.index.isin(mapping.keys())]
        data.index = pd.MultiIndex.from_tuples([(mapping[i], i) for i in data.index])
        data = data.reset_index()
        data.rename(columns={'level_0': 'Demand'}, inplace=True)
        data.rename(columns={'level_1': 'Sectors'}, inplace=True)

        
        melted_data = pd.melt(data, id_vars=['Demand', 'Sectors'], var_name='year', value_name='value')
        melted_data['color'] = melted_data['Sectors'].map(colors)

        # Use plotly express to create a stacked bar plot
        fig = px.bar(
        melted_data,
        x='year',
        y='value',
        color='Sectors',
        color_discrete_map=dict(zip(melted_data['Sectors'].unique(), melted_data['color'])),
        facet_col='Demand',
        labels={'year': '', 'value': 'Final energy and non-energy demand [TWh/a]'}
        )
        fig.for_each_annotation(lambda a: a.update(text=a.text.split('=')[-1].strip()))
        fig.update_layout(height=800, width=1400)
        logo['y']=1.021
        # Show the plot
        html_filename = f"{country}_sectoral_demands.html"
        output_folder = f'results/{study}/htmls/raw_html' # Set your desired output folder
        os.makedirs(output_folder, exist_ok=True)
        html_filepath = os.path.join(output_folder, html_filename)
        fig.write_html(html_filepath,full_html=False, include_plotlyjs=False)
        file_path = f"results/{study}/country_csvs/{country}_sectordemands.csv"
        data.to_csv(file_path, index=True)
        
def plot_series_power(cluster, opt, sector_opt, planning_horizons,start,stop,title):
    tech_colors = snakemake.params.plotting["tech_colors"]
    colors = tech_colors 
    tabs = pn.Tabs()

    for country in countries:
      tabs = pn.Tabs()

      for planning_horizon in planning_horizons:
        tab = pn.Tabs()
        n = loaded_files[planning_horizon]

        assign_locations(n)
        assign_carriers(n)
        carrier = 'AC'
        busesn = n.buses.index[n.buses.carrier.str.contains(carrier)]

        supplyn = pd.DataFrame(index=n.snapshots)

        if country != 'EU':
          for c in n.iterate_components(n.branch_components):
            n_port = 4 if c.name == "Link" else 2  # port3
            for i in range(n_port):
                supplyn = pd.concat(
                    (
                        supplyn,
                        (-1)
                        * c.pnl["p" + str(i)]
                        .loc[:, c.df.index[c.df["bus" + str(i)].isin(busesn)]].filter(like=country)
                        .groupby(c.df.carrier, axis=1)
                        .sum(),
                    ),
                    axis=1,
                )
        else:
          for c in n.iterate_components(n.branch_components):
            n_port = 4 if c.name == "Link" else 2  # port3
            for i in range(n_port):
                supplyn = pd.concat(
                    (
                        supplyn,
                        (-1)
                        * c.pnl["p" + str(i)]
                        .loc[:, c.df.index[c.df["bus" + str(i)].isin(busesn)]]
                        .groupby(c.df.carrier, axis=1)
                        .sum(),
                    ),
                    axis=1,
                )
        if country != 'EU':
          for c in n.iterate_components(n.one_port_components):
            comps = c.df.index[c.df.bus.isin(busesn)]
            supplyn = pd.concat(
                (
                    supplyn,
                    ((c.pnl["p"].loc[:, comps]).multiply(c.df.loc[comps, "sign"])).filter(like=country)
                    .groupby(c.df.carrier, axis=1)
                    .sum(),
                ),
                axis=1,
            )
        else:
          for c in n.iterate_components(n.one_port_components):
            comps = c.df.index[c.df.bus.isin(busesn)]
            supplyn = pd.concat(
                (
                    supplyn,
                    ((c.pnl["p"].loc[:, comps]).multiply(c.df.loc[comps, "sign"]))
                    .groupby(c.df.carrier, axis=1)
                    .sum(),
                ),
                axis=1,
            ) 

        supplyn = supplyn.groupby(rename_techs_tyndpp, axis=1).sum()
        filtered_ac_lines = n.lines.bus0.str[:2] == country
        ac_lines = n.lines_t.p0.filter(items=filtered_ac_lines[filtered_ac_lines == True].index).sum(axis=1)
        filtered_ac_lines_r = n.lines.bus1.str[:2] == country
        ac_lines_r = n.lines_t.p1.filter(items=filtered_ac_lines_r[filtered_ac_lines_r == True].index).sum(axis=1)
        filtered_dc_lines = (n.links.carrier == 'DC') & (n.links.bus0.str[:2] == country)
        dc_lines = n.links_t.p0.filter(items=filtered_dc_lines[filtered_dc_lines == True].index).sum(axis=1)
        filtered_dc_lines_r = (n.links.carrier == 'DC') & (n.links.bus1.str[:2] == country)
        dc_lines_r = n.links_t.p1.filter(items=filtered_dc_lines_r[filtered_dc_lines_r == True].index).sum(axis=1)
        merged_series = pd.concat([ac_lines,ac_lines_r, dc_lines, dc_lines_r], axis=1)
        imp_exp = merged_series.sum(axis=1)
        imp_exp = imp_exp.rename('Imports_Exports')
        imp_exp=-imp_exp
        supplyn['Imports_Exports'] = imp_exp

        bothn = supplyn.columns[(supplyn < 0.0).any() & (supplyn > 0.0).any()]

        positive_supplyn = supplyn[bothn]
        negative_supplyn = supplyn[bothn]

        positive_supplyn = positive_supplyn.mask(positive_supplyn < 0.0, 0.0)
        negative_supplyn = negative_supplyn.mask(negative_supplyn > 0.0, 0.0)

        supplyn[bothn] = positive_supplyn

        supplyn = pd.concat((supplyn, negative_supplyn), axis=1)



        threshold = 0.1

        to_dropn = supplyn.columns[(abs(supplyn) < threshold).all()]

        if len(to_dropn) != 0:
            logger.info(f"Dropping {to_dropn.tolist()} from supplyn")
            supplyn.drop(columns=to_dropn, inplace=True)

        supplyn.index.name = None

        supplyn = supplyn / 1e3

        
        supplyn = supplyn.groupby(supplyn.columns, axis=1).sum()

        if country != 'EU':
          c_solarn = ((n.generators_t.p_max_pu * n.generators.p_nom_opt) - n.generators_t.p).filter(
            like="solar", axis=1
          ).filter(like=country).sum(axis=1) / 1e3
          c_onwindn = ((n.generators_t.p_max_pu * n.generators.p_nom_opt) - n.generators_t.p).filter(
            like="onwind", axis=1
          ).filter(like=country).sum(axis=1) / 1e3
          c_offwindn = ((n.generators_t.p_max_pu * n.generators.p_nom_opt) - n.generators_t.p).filter(
            like="offwind", axis=1
          ).filter(like=country).sum(axis=1) / 1e3
        else:
          c_solarn = ((n.generators_t.p_max_pu * n.generators.p_nom_opt) - n.generators_t.p).filter(
            like="solar", axis=1
          ).sum(axis=1) / 1e3
          c_onwindn = ((n.generators_t.p_max_pu * n.generators.p_nom_opt) - n.generators_t.p).filter(
            like="onwind", axis=1
          ).sum(axis=1) / 1e3
          c_offwindn = ((n.generators_t.p_max_pu * n.generators.p_nom_opt) - n.generators_t.p).filter(
            like="offwind", axis=1
          ).sum(axis=1) / 1e3
        supplyn = supplyn.T
        if "solar" in supplyn.index:
          supplyn.loc["solar"] = supplyn.loc["solar"] + c_solarn
          supplyn.loc["solar curtailment"] = -abs(c_solarn)
        if "onshore wind" in supplyn.index:
          supplyn.loc["onshore wind"] = supplyn.loc["onshore wind"] + c_onwindn
          supplyn.loc["onshore curtailment"] = -abs(c_onwindn)
        if "offshore wind" in supplyn.index:
          supplyn.loc["offshore wind"] = supplyn.loc["offshore wind"] + c_offwindn
          supplyn.loc["offshore curtailment"] = -abs(c_offwindn)
        if "H2 pipeline" in supplyn.index:
            supplyn = supplyn.drop('H2 pipeline')
        if "gas pipeline" in supplyn.index:
            supplyn = supplyn.drop('gas pipeline')
        supplyn = supplyn.T
        if "V2G" in n.carriers.index:
          if country != 'EU':
              v2g = n.links_t.p1.filter(like=country).filter(like="V2G").sum(axis=1)
              v2g = v2g.to_frame()
              v2g = v2g.rename(columns={v2g.columns[0]: 'V2G'})
              v2g = v2g/1e3
              if 'electricity distribution grid' not in supplyn.columns:
                 supplyn['electricity distribution grid'] = 0
              supplyn['electricity distribution grid'] += v2g['V2G']
              supplyn['V2G'] = v2g['V2G'].abs()
          else:
              v2g = n.links_t.p1.filter(like="V2G").sum(axis=1)
              v2g = v2g.to_frame()
              v2g = v2g.rename(columns={v2g.columns[0]: 'V2G'})
              v2g = v2g/1e3
              if 'electricity distribution grid' not in supplyn.columns:
                 supplyn['electricity distribution grid'] = 0
              supplyn['electricity distribution grid'] += v2g['V2G']
              supplyn['V2G'] = v2g['V2G'].abs()
        
        positive_supplyn = supplyn[supplyn >= 0].fillna(0)
        negative_supplyn = supplyn[supplyn < 0].fillna(0)
        
        positive_supplyn = positive_supplyn.loc[start:stop]
        negative_supplyn = negative_supplyn.loc[start:stop]
        positive_supplyn = positive_supplyn.applymap(lambda x: x if x >= 0.1 else 0)
        negative_supplyn = negative_supplyn.applymap(lambda x: x if x <= -0.1 else 0)
        positive_supplyn = positive_supplyn.loc[:, (positive_supplyn > 0).any()]
        negative_supplyn = negative_supplyn.loc[:, (negative_supplyn < 0).any()]
        

        
        fig = go.Figure()

        for col in positive_supplyn.columns:
            fig.add_trace(go.Scatter(
                x=positive_supplyn.index,
                y=positive_supplyn[col],
                mode='lines',
                line=dict(color=colors.get(col, 'black')),
                stackgroup='positive',
                showlegend=False,
                hovertemplate='%{y:.2f}',
                name=col
            ))

        for col in negative_supplyn.columns:
            fig.add_trace(go.Scatter(
                x=negative_supplyn.index,
                y=negative_supplyn[col],
                mode='lines',
                line=dict(color=colors.get(col, 'black')),
                stackgroup='negative',
                showlegend=False,
                hovertemplate='%{y:.2f}',
                name=col
            ))
         
        # Collect unique column names from both positive_supplyn and negative_supplyn
        unique_columns = set(positive_supplyn.columns).union(set(negative_supplyn.columns))
        preferred_order = ['solar', 'onshore wind', 'oil', 'offshore wind', 'nuclear', 'hydroelectricity', 'coal', 'battery storage','Imports_Exports', 'OCGT','H2 turbine', 'CHP', 'CCGT','distribution network','power-to-gas']
        unique_columns = list(unique_columns)
        sorted_columns = sorted(unique_columns, key=lambda x: preferred_order.index(x) if x in preferred_order else len(preferred_order))
        sorted_columns = sorted_columns[::-1]
        # Add a dummy trace for each unique column name to the legend
        for col in sorted_columns:
           fig.add_trace(go.Scatter(
             x=[None],
             y=[None],
             mode='lines',
             line=dict(color=colors.get(col, 'black'), width=4),
             legendgroup='supply',
             showlegend=True,
             name=col))
        # Update layout to customize axes, title, etc.
        fig.update_layout(
          xaxis=dict(title='Time', tickformat="%m-%d"),
          yaxis=dict(title='Power [GW]',),
          title=title + " - " + country + ' - ' + str(planning_horizon),
          width=1200, height=600,
          hovermode='x',)
            # Add the plot to the tabs
        tab.append((f"{planning_horizon}", fig))
        del fig
            # Add the tab for the planning horizon to the main Tabs
        tabs.append((f"{planning_horizon}", tab))
        del supplyn
        del positive_supplyn
        del negative_supplyn
        del merged_series
        del imp_exp

        if 'c_solarn' in locals(): del c_solarn
        if 'c_onwindn' in locals(): del c_onwindn
        if 'c_offwindn' in locals(): del c_offwindn
        if 'v2g' in locals(): del v2g
        del n
        gc.collect()
      html_filename = title + " - " + country + '.html'
      output_folder = f'results/{study}/htmls/raw_html' # Set your desired output folder
      os.makedirs(output_folder, exist_ok=True)
      html_filepath = os.path.join(output_folder, html_filename)
      tabs.save(html_filepath)
      del tabs
      del tab
      gc.collect()
      
def plot_series_power_cluster(cluster, opt, sector_opt, planning_horizons,start,stop,title):
    tech_colors = snakemake.params.plotting["tech_colors"]
    colors = tech_colors 
    tabs = pn.Tabs()

    for country in countries:
     tabs = pn.Tabs()

     for planning_horizon in planning_horizons:
        tab = pn.Tabs()
        n = loaded_files[planning_horizon]

        assign_locations(n)
        assign_carriers(n)
        carrier = 'AC'
        busesn = n.buses.index[n.buses.carrier.str.contains(carrier)]

        supplyn = pd.DataFrame(index=n.snapshots)

        if country != 'EU':
         for c in n.iterate_components(n.branch_components):
            n_port = 4 if c.name == "Link" else 2  # port3
            for i in range(n_port):
                supplyn = pd.concat(
                    (
                        supplyn,
                        (-1)
                        * c.pnl["p" + str(i)]
                        .loc[:, c.df.index[c.df["bus" + str(i)].isin(busesn)]].filter(like=country)
                        .groupby(c.df.carrier, axis=1)
                        .sum(),
                    ),
                    axis=1,
                )
        else:
         for c in n.iterate_components(n.branch_components):
            n_port = 4 if c.name == "Link" else 2  # port3
            for i in range(n_port):
                supplyn = pd.concat(
                    (
                        supplyn,
                        (-1)
                        * c.pnl["p" + str(i)]
                        .loc[:, c.df.index[c.df["bus" + str(i)].isin(busesn)]]
                        .groupby(c.df.carrier, axis=1)
                        .sum(),
                    ),
                    axis=1,
                )
        if country != 'EU':
         for c in n.iterate_components(n.one_port_components):
            comps = c.df.index[c.df.bus.isin(busesn)]
            supplyn = pd.concat(
                (
                    supplyn,
                    ((c.pnl["p"].loc[:, comps]).multiply(c.df.loc[comps, "sign"])).filter(like=country)
                    .groupby(c.df.carrier, axis=1)
                    .sum(),
                ),
                axis=1,
            )
        else:
         for c in n.iterate_components(n.one_port_components):
            comps = c.df.index[c.df.bus.isin(busesn)]
            supplyn = pd.concat(
                (
                    supplyn,
                    ((c.pnl["p"].loc[:, comps]).multiply(c.df.loc[comps, "sign"]))
                    .groupby(c.df.carrier, axis=1)
                    .sum(),
                ),
                axis=1,
            ) 

        supplyn = supplyn.groupby(rename_techs_tyndpp, axis=1).sum()

        bothn = supplyn.columns[(supplyn < 0.0).any() & (supplyn > 0.0).any()]

        positive_supplyn = supplyn[bothn]
        negative_supplyn = supplyn[bothn]

        positive_supplyn = positive_supplyn.mask(positive_supplyn < 0.0, 0.0)
        negative_supplyn = negative_supplyn.mask(negative_supplyn > 0.0, 0.0)

        supplyn[bothn] = positive_supplyn

        supplyn = pd.concat((supplyn, negative_supplyn), axis=1)



        threshold = 0.1

        to_dropn = supplyn.columns[(abs(supplyn) < threshold).all()]

        if len(to_dropn) != 0:
            # logger.info(f"Dropping {to_dropn.tolist()} from supplyn")
            supplyn.drop(columns=to_dropn, inplace=True)

        supplyn.index.name = None

        supplyn = supplyn / 1e3

        
        supplyn = supplyn.groupby(supplyn.columns, axis=1).sum()

        
        positive_supplyn = supplyn[supplyn >= 0].fillna(0)
        # positive_supplyn = positive_supplyn.drop(columns=["battery storage", "power-to-gas"])
        negative_supplyn = supplyn[supplyn < 0].fillna(0)
        # negative_supplyn = negative_supplyn[["distribution network"]]
        total_demand_df = pd.DataFrame({"Total Demand": negative_supplyn.sum(axis=1)}).abs()
        total_supply_df = pd.DataFrame({"Total Supply": positive_supplyn.sum(axis=1)})
        
        total_demand_df = total_demand_df.loc[start:stop]
        total_supply_df = total_supply_df.loc[start:stop]
        
        dispatch = total_supply_df["Total Supply"]
        demand = total_demand_df["Total Demand"]
        index = total_supply_df.index
        

        fig = go.Figure()

        fig.add_trace(go.Scatter(
         x=index, 
         y=demand, 
         mode='lines', 
         name='Total Demand', 
         line=dict(color='red',width=3)))

        fig.add_trace(go.Scatter(
         x=index, 
         y=dispatch, 
         mode='lines', 
         name='Total Supply', 
         line=dict(color='#01889f',width=3)))


        fig.add_trace(go.Scatter(
         x=index,
         y=demand,
         mode='none',
         fill='tozeroy',
         fillcolor='rgba(255,0,0,0.2)',
         name='Shortfall'))


        fig.add_trace(go.Scatter(
         x=index,
         y=dispatch,
         mode='none',
         fill='tozeroy',
         fillcolor='rgba(0, 120, 255, 0.1)',
         name='Surplus'))
        
        fig.add_trace(go.Scatter(
         x=[None],
         y=[None],
         mode='lines',
         line=dict(color='rgba(128, 0, 128, 0.4)', width=6),  # purple
         name='Direct Match',
         showlegend=True))


        fig.update_layout(
          title=title + " - " + country + ' - ' + str(planning_horizon),
          xaxis=dict(title='Time', tickformat="%m-%d"),
          yaxis=dict(title='Power [GW]',),
          legend_itemclick=False,
          legend_itemdoubleclick=False,
          width=1200, height=600,
          # hovermode='x unified',
          legend=dict(font=dict(size=12)))
        
            # Add the plot to the tabs
        tab.append((f"{planning_horizon}", fig))
        del fig
            # Add the tab for the planning horizon to the main Tabs
        tabs.append((f"{planning_horizon}", tab))
        del supplyn
        del positive_supplyn
        del negative_supplyn
        del total_demand_df
        del total_supply_df
        del dispatch
        del demand
        del index
        del n
        del tab
        gc.collect()
        
     html_filename = title + " - " + country + '.html'
     output_folder = f'results/{study}/htmls/raw_html' # Set your desired output folder
     os.makedirs(output_folder, exist_ok=True)
     html_filepath = os.path.join(output_folder, html_filename)
     tabs.save(html_filepath)
     del tabs
     gc.collect()

def plot_series_heat(cluster, opt, sector_opt, planning_horizons,start,stop,title):
    tech_colors = snakemake.params.plotting["tech_colors"]
    colors = tech_colors 
    tabs = pn.Tabs()

    for country in countries:
     tabs = pn.Tabs()

     for planning_horizon in planning_horizons:
        tab = pn.Tabs()
        n = loaded_files[planning_horizon]

        assign_locations(n)
        assign_carriers(n)
        carrier = 'heat'
        busesn = n.buses.index[n.buses.carrier.str.contains(carrier)]

        supplyn = pd.DataFrame(index=n.snapshots)

        if country != 'EU':
         for c in n.iterate_components(n.branch_components):
            n_port = 4 if c.name == "Link" else 2  # port3
            for i in range(n_port):
                supplyn = pd.concat(
                    (
                        supplyn,
                        (-1)
                        * c.pnl["p" + str(i)]
                        .loc[:, c.df.index[c.df["bus" + str(i)].isin(busesn)]].filter(like=country)
                        .groupby(c.df.carrier, axis=1)
                        .sum(),
                    ),
                    axis=1,
                )
        else:
         for c in n.iterate_components(n.branch_components):
            n_port = 4 if c.name == "Link" else 2  # port3
            for i in range(n_port):
                supplyn = pd.concat(
                    (
                        supplyn,
                        (-1)
                        * c.pnl["p" + str(i)]
                        .loc[:, c.df.index[c.df["bus" + str(i)].isin(busesn)]]
                        .groupby(c.df.carrier, axis=1)
                        .sum(),
                    ),
                    axis=1,
                )
        if country != 'EU':
         for c in n.iterate_components(n.one_port_components):
            comps = c.df.index[c.df.bus.isin(busesn)]
            supplyn = pd.concat(
                (
                    supplyn,
                    ((c.pnl["p"].loc[:, comps]).multiply(c.df.loc[comps, "sign"])).filter(like=country)
                    .groupby(c.df.carrier, axis=1)
                    .sum(),
                ),
                axis=1,
            )
        else:
         for c in n.iterate_components(n.one_port_components):
            comps = c.df.index[c.df.bus.isin(busesn)]
            supplyn = pd.concat(
                (
                    supplyn,
                    ((c.pnl["p"].loc[:, comps]).multiply(c.df.loc[comps, "sign"]))
                    .groupby(c.df.carrier, axis=1)
                    .sum(),
                ),
                axis=1,
            )
        supplyn = supplyn.rename(columns={"urban central resistive heater": "centralised electric boiler"})
        supplyn = supplyn.groupby(rename_techs_tyndpp, axis=1).sum()

        bothn = supplyn.columns[(supplyn < 0.0).any() & (supplyn > 0.0).any()]

        positive_supplyn = supplyn[bothn]
        negative_supplyn = supplyn[bothn]

        positive_supplyn = positive_supplyn.mask(positive_supplyn < 0.0, 0.0)
        negative_supplyn = negative_supplyn.mask(negative_supplyn > 0.0, 0.0)

        supplyn[bothn] = positive_supplyn

        supplyn = pd.concat((supplyn, negative_supplyn), axis=1)


        threshold = 0.1

        to_dropn = supplyn.columns[(abs(supplyn) < threshold).all()]

        if len(to_dropn) != 0:
            logger.info(f"Dropping {to_dropn.tolist()} from supplyn")
            supplyn.drop(columns=to_dropn, inplace=True)

        supplyn.index.name = None

        supplyn = supplyn / 1e3
        supplyn.rename(
            columns={"electricity": "electric demand", "heat": "heat demand"}, inplace=True
        )
        supplyn.columns = supplyn.columns.str.replace("residential ", "")
        supplyn.columns = supplyn.columns.str.replace("services ", "")
        supplyn.columns = supplyn.columns.str.replace("urban decentral ", "decentral ")


        supplyn = supplyn.groupby(supplyn.columns, axis=1).sum()
        positive_supplyn = supplyn[supplyn >= 0].fillna(0)
        negative_supplyn = supplyn[supplyn < 0].fillna(0)

        positive_supplyn = positive_supplyn.loc[start:stop]
        negative_supplyn = negative_supplyn.loc[start:stop]
        positive_supplyn = positive_supplyn.applymap(lambda x: x if x >= 0.1 else 0)
        negative_supplyn = negative_supplyn.applymap(lambda x: x if x <= -0.1 else 0)
        positive_supplyn = positive_supplyn.loc[:, (positive_supplyn > 0).any()]
        negative_supplyn = negative_supplyn.loc[:, (negative_supplyn < 0).any()]
        
        
        fig = go.Figure()

        for col in positive_supplyn.columns:
            fig.add_trace(go.Scatter(
                x=positive_supplyn.index,
                y=positive_supplyn[col],
                mode='lines',
                line=dict(color=colors.get(col, 'black')),
                stackgroup='positive',
                showlegend=False,
                hovertemplate='%{y:.2f}',
                name=col
            ))

        for col in negative_supplyn.columns:
            fig.add_trace(go.Scatter(
                x=negative_supplyn.index,
                y=negative_supplyn[col],
                mode='lines',
                line=dict(color=colors.get(col, 'black')),
                stackgroup='negative',
                showlegend=False,
                hovertemplate='%{y:.2f}',
                name=col
            ))
         
        # Collect unique column names from both positive_supplyn and negative_supplyn
        unique_columns = set(positive_supplyn.columns).union(set(negative_supplyn.columns))
        # Add a dummy trace for each unique column name to the legend
        for col in unique_columns:
            fig.add_trace(go.Scatter(
                x=[None],
                y=[None],
                mode='lines',
                line=dict(color=colors.get(col, 'black'), width=4),
                legendgroup='supply',
                showlegend=True,
                name=col
            ))
        
        # Update layout to customize axes, title, etc.
        fig.update_layout(
         xaxis=dict(title='Time', tickformat="%m-%d"),
         yaxis=dict(title='Heat [GW]',),
         title=title + " - " + country + ' - ' + str(planning_horizon),
         width=1200, height=600,
         hovermode='x',)
        tab.append((f"{planning_horizon}", fig))
        del fig
            # Add the tab for the planning horizon to the main Tabs
        tabs.append((f"{planning_horizon}", tab))
        del supplyn
        del positive_supplyn
        del negative_supplyn
        del unique_columns
        del n
        del tab
        gc.collect()

        # Save the tabs as an HTML file
     html_filename = title + " - " + country + '.html'
     output_folder = f'results/{study}/htmls/raw_html'  # Set your desired output folder
     os.makedirs(output_folder, exist_ok=True)
     html_filepath = os.path.join(output_folder, html_filename)
     tabs.save(html_filepath)
     del tabs
     gc.collect()

def plot_map(
    network,country,
    components=["links", "stores", "storage_units", "generators"],
    bus_size_factor=35e9,
    transmission=True,
    with_legend=True,
):
    tech_colors = snakemake.params.plotting["tech_colors"]
    tech_colors["distribution network"] = tech_colors["electricity distribution grid"]
    tech_colors["thermal energy storage"] = tech_colors["hot water storage"]
    tech_colors["H2 storage"] = tech_colors["H2"]
    tech_colors["CCUS"] = tech_colors["CCS"]
    tech_colors["CCU"] = tech_colors["CCS"]
    n = network.copy()
    assign_locations(n)
    # Drop non-electric buses so they don't clutter the plot
    n.buses.drop(n.buses.index[n.buses.carrier != "AC"], inplace=True)

    costs = pd.DataFrame(index=n.buses.index)

    for comp in components:
        df_c = getattr(n, comp)

        if df_c.empty:
            continue

        df_c["nice_group"] = df_c.carrier.map(rename_techs_tyndp)
        df_c.loc[df_c["nice_group"] == "Fossil fuels & powerplants", "nice_group"] = "Fossil fuel powerplants"

        attr = "e_nom_opt" if comp == "stores" else "p_nom_opt"

        costs_c = (
            (df_c.capital_cost * df_c[attr])
            .groupby([df_c.location, df_c.nice_group])
            .sum()
            .unstack()
            .fillna(0.0)
        )
        costs = pd.concat([costs, costs_c], axis=1)

        logger.debug(f"{comp}, {costs}")
        del df_c, costs_c
    costs = costs.T.groupby(costs.columns).sum().T

    costs.drop(list(costs.columns[(costs == 0.0).all()]), axis=1, inplace=True)

    new_columns = preferred_order.intersection(costs.columns).append(
        costs.columns.difference(preferred_order)
    )
    costs = costs[new_columns]

    for item in new_columns:
        if item not in tech_colors:
            logger.warning(f"{item} not in config/plotting/tech_colors")

    costs = costs.stack()  # .sort_index()

    n.links.drop(
        n.links.index[(n.links.carrier != "DC") & (n.links.carrier != "B2B")],
        inplace=True,
    )

    # drop non-bus
    to_drop = costs.index.levels[0].symmetric_difference(n.buses.index)
    if len(to_drop) != 0:
        logger.info(f"Dropping non-buses {to_drop.tolist()}")
        costs.drop(to_drop, level=0, inplace=True, axis=0, errors="ignore")

    # make sure they are removed from index
    costs.index = pd.MultiIndex.from_tuples(costs.index.values)
    combined_costs = costs.groupby(costs.index).sum()
    multi_index = pd.MultiIndex.from_tuples(combined_costs.index)
    combined_costs.index = multi_index
    combined_costs = combined_costs.groupby(level=0).sum()/1e9
    threshold = 100e6  # 100 mEUR/a
    carriers = costs.groupby(level=1).sum()
    carriers = carriers.where(carriers > threshold).dropna()
    carriers = list(carriers.index)

    # PDF has minimum width, so set these to zero
    line_lower_threshold = 500.0
    line_upper_threshold = 1e4
    linewidth_factor = 1e3
    # ac_color = "#FF3030"
    # dc_color = "#104E8B"
    ac_color = "#9a0200"
    dc_color = "#11875d"

    title = "added grid"
    ll = 'lvopt'
    if ll == "v1.0":
        # should be zero
        line_widths = n.lines.s_nom_opt - n.lines.s_nom
        link_widths = n.links.p_nom_opt - n.links.p_nom
        if transmission:
            line_widths = n.lines.s_nom_opt
            link_widths = n.links.p_nom_opt
            linewidth_factor = 1e3
            line_lower_threshold = 0.0
            title = "current grid"
    else:
        line_widths = n.lines.s_nom_opt - n.lines.s_nom_min
        link_widths = n.links.p_nom_opt - n.links.p_nom_min
        if transmission:
            line_widths = n.lines.s_nom_opt
            link_widths = n.links.p_nom_opt
            title = "total grid capacity"

    line_widths = line_widths.clip(line_lower_threshold, line_upper_threshold)
    link_widths = link_widths.clip(line_lower_threshold, line_upper_threshold)

    line_widths = line_widths.replace(line_lower_threshold, 0)
    link_widths = link_widths.replace(line_lower_threshold, 0)
    
    value_dict = {
       'AT2 0': 470,
       'BE2 0': 596,
       'BG2 0': 90,
       'CH2 0': 788,
       'CZ2 0': 350,
       'DE2 0': 4200,
       'DK0 0': 350,
       'DK2 0': 350,
       'EE2 0': 38,
       'ES2 0': 1500,
       'ES6 0': 1500,
       'FI0 0': 273,
       'FR2 0': 2822,
       'FR5 0': 2822,
       'GB1 0': 2100,
       'GB3 0': 2100,
       'GR2 0': 243,
       'HR2 0': 78,
       'HU2 0': 197,
       'IE3 0': 510,
       'IT2 0': 2200,
       'IT4 0': 2200,
       'LT2 0': 68,
       'LU2 0': 92,
       'LV2 0': 43,
       'NL2 0': 1000,
       'NO0 0': 477,
       'PL2 0': 774,
       'PT2 0': 330,
       'RO2 0': 340,
       'SE0 0': 560,
       'SI2 0': 62,
       'SK2 0': 115,
   }
    new_values = [value_dict.get(idx, 0) for idx in combined_costs.index]
    gdp_costs = pd.Series(new_values, index=combined_costs.index)
    gdp_ratio = (combined_costs / (gdp_costs)) * 100
    index_mapping = {
       'DK2 0': 'DK0 0',
       'ES6 0': 'ES2 0',
       'GB3 0': 'GB1 0',
       'FR5 0': 'FR2 0',
       'IT4 0': 'IT2 0'
   }
    for target_index, reference_index in index_mapping.items():
        if target_index in gdp_ratio.index and reference_index in gdp_ratio.index:
            gdp_ratio[target_index] = gdp_ratio[reference_index]
    regions = regions_base.copy()
    regions['GDP'] = regions.index.map(gdp_ratio)
    regions = regions.to_crs(ccrs.EqualEarth())
    
    fig, ax = plt.subplots(figsize=(15, 15), subplot_kw={"projection": proj})
    
    n.plot(
        bus_sizes=costs / bus_size_factor,
        bus_colors=tech_colors,
        line_colors=ac_color,
        link_colors=dc_color,
        line_widths=line_widths / linewidth_factor,
        link_widths=link_widths / linewidth_factor,
        ax=ax,
        **map_opts,
    )
    regions.plot(
           ax=ax,
           column="GDP",
           cmap="Greys",
           linewidths=0,
           legend=False,
           vmax=6,
           vmin=0,
           legend_kwds={
               "label": "GDP Percentage / year",
               "shrink": 0.6,
               # "extend": "max",
           },
       )

    sizes = [20, 10, 5]
    labels = [f"{s} bEUR/year" for s in sizes]
    sizes = [s / bus_size_factor * 1e9 for s in sizes]

    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(0.001, 0.98),
        labelspacing=1,
        frameon=False,
        handletextpad=1,
        fontsize=15,
        title="Annualised Investment Costs",
    )

    add_legend_circles(
        ax,
        sizes,
        labels,
        srid=n.srid,
        patch_kw=dict(facecolor="black"),
        legend_kw=legend_kw,
    )

    sizes = [10, 5, 1]
    labels = [f"{s} GW" for s in sizes]
    scale = 1e3 / linewidth_factor
    sizes = [s * scale for s in sizes]
    # if planning_horizons == 2020:
    #     value = "current grid"
    # else:
    #     value = "total grid capacity"
    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(0.45, 0.98),
        frameon=False,
        labelspacing=1,
        handletextpad=1,
        fontsize=15,
        title=title,
    )

    add_legend_lines(
        ax, sizes, labels, patch_kw=dict(color="black"), legend_kw=legend_kw
    )

    legend_kw = dict(
        bbox_to_anchor=(1.35, 1),
        frameon=False,
        fontsize=15
    )

    sm = plt.cm.ScalarMappable(cmap="Greys", norm=plt.Normalize(vmin=0, vmax=6))
    sm._A = []
    cbar = fig.colorbar(sm, ax=ax, orientation="vertical", shrink=0.5, pad=0)
    cbar.set_label(r"Cost [% GDP$_{2023}$ / year]", fontsize=15)
    legend_kw = dict(
          bbox_to_anchor=(1, 1),
          frameon=False,
          fontsize=15,
      )
    cbar.ax.tick_params(labelsize=15)
    colors = [tech_colors[c] for c in carriers] + [ac_color, dc_color]
    labels = carriers + ['AC line', 'DC line']
    legend_handles = [
        Line2D([0], [0], marker='o', color='w', markerfacecolor=color, markersize=15) for color in colors
    ]
    fig.legend(
        legend_handles,
        labels,
        # loc="lower center",            # Position the legend at the bottom center of the figure
        bbox_to_anchor=(0.9, 0.25),    # Centered horizontally and placed below the figure
        ncol=4,                        # Number of columns in the legend
        frameon=False,                 # No frame around the legend
        fontsize=15,
    )
    
    del costs
    del combined_costs
    del carriers
    del regions
    del gdp_costs
    del gdp_ratio
    return fig

def create_map_plots(planning_horizons, country):
    
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
    <style>
    /* ... (existing styles) ... */
    </style>
    </head>
    <body>
    <div class="tab">
    """
    for i, planning_horizon in enumerate(planning_horizons):
        # Load network for the current planning horizon
          n = loaded_files[planning_horizon]

        # Plot the map and get the figure
          fig = plot_map(
              n,country,
              components=["generators", "links", "stores", "storage_units"],
              bus_size_factor=35e9,
              transmission=True,
          )
          plt.rcParams['legend.title_fontsize'] = '20'
         
        # Save the map plot as an image
          output_image_path = f"results/{study}/htmls/raw_html/map_plot_{planning_horizon}_{country}.png"
          fig.savefig(output_image_path, dpi=80, bbox_inches="tight")
          plt.close(fig)  # Close the figure to avoid displaying it in the notebook
          del fig
          del n
          gc.collect()
        # Encode the image as base64
          with open(output_image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Add tab content for each planning horizon with embedded image data
          html_content += f"""
        <button class="map-tablinks{' active' if i == 0 else ''}" onclick="openMapTab(event, 'map_{planning_horizon}_{i + 1}')">{planning_horizon}</button>
        """

    html_content += """
    </div>
    """

    for i, planning_horizon in enumerate(planning_horizons):
        # Load network for the current planning horizon
          n = loaded_files[planning_horizon]
          fig = plot_map(
              n,country,
              components=["generators", "links", "stores", "storage_units"],
              bus_size_factor=35e9,
              transmission=True,
          )
          plt.rcParams['legend.title_fontsize'] = '20'
        # Save the map plot as an image
          # if country == 'BE':
          #  fig.savefig(f"results/pdf/Maps_{planning_horizon}.pdf", bbox_inches="tight")
          output_image_path = f"results/{study}/htmls/raw_html/map_plot_{planning_horizon}_{country}.png"
          fig.savefig(output_image_path, dpi=80, bbox_inches="tight")
          plt.close(fig)  # Close the figure to avoid displaying it in the notebook
          del fig
          del n
          gc.collect()
        # Encode the image as base64
          with open(output_image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Add tab content for each planning horizon with embedded image data
          html_content += f"""
        <div id="map_{planning_horizon}_{i + 1}" class="map-tabcontent" style="display: {'block' if i == 0 else 'none'};">
            <h2>Map Plot - {planning_horizon}</h2>
            <img src="data:image/png;base64,{encoded_image}" alt="Map Plot" width="1200" height="800">
        </div>
        """


    # Add JavaScript for tab functionality
    html_content += """
    <script>
    function openMapTab(evt, tabName) {
      var i, tabcontent, tablinks;
      tabcontent = document.getElementsByClassName("map-tabcontent");
      for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
      }
      tablinks = document.getElementsByClassName("map-tablinks");
      for (i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
      }
      document.getElementById(tabName).style.display = "block";
      evt.currentTarget.className += " active";
    }
    </script>
    </body>
    </html>
    """

    # Save the entire HTML content to a single file
    
    output_combined_html_path = f"results/{study}/htmls/raw_html/map_plots_{country}.html"
    with open(output_combined_html_path, "w") as html_file:
        html_file.write(html_content)
    gc.collect()
    
def group_pipes(df, drop_direction=False):
    """
    Group pipes which connect same buses and return overall capacity.
    """
    df = df.copy()
    if drop_direction:
        positive_order = df.bus0 < df.bus1
        df_p = df[positive_order]
        swap_buses = {"bus0": "bus1", "bus1": "bus0"}
        df_n = df[~positive_order].rename(columns=swap_buses)
        df = pd.concat([df_p, df_n])

    # there are pipes for each investment period rename to AC buses name for plotting
    df["index_orig"] = df.index
    df.index = df.apply(
        lambda x: f"H2 pipeline {x.bus0.replace(' H2', '')} -> {x.bus1.replace(' H2', '')}",
        axis=1,
    )
    return df.groupby(level=0).agg(
        {"p_nom_opt": "sum", "bus0": "first", "bus1": "first", "index_orig": "first"}
    )


def plot_h2_map(network):
    # if "H2 pipeline" not in n.links.carrier.unique():
    #     return
    n = network.copy()
    assign_locations(n)
    h2_storage = n.stores.query("carrier == 'H2'")
    regions = regions_base.copy()
    regions["H2"] = (
        h2_storage.rename(index=h2_storage.bus.map(n.buses.location))
        .e_nom_opt.groupby(level=0)
        .sum()
        .div(1e6)
    )  # TWh
    regions["H2"] = regions["H2"].where(regions["H2"] > 0.1)

    bus_size_factor = 1e5
    linewidth_factor = 7e3
    # MW below which not drawn
    line_lower_threshold = 750

    # Drop non-electric buses so they don't clutter the plot
    n.buses.drop(n.buses.index[n.buses.carrier != "AC"], inplace=True)

    carriers = ["H2 Electrolysis"]

    elec = n.links[n.links.carrier.isin(carriers)].index

    bus_sizes = (
        n.links.loc[elec, "p_nom_opt"].groupby([n.links["bus0"], n.links.carrier]).sum()
        / bus_size_factor
    )

    # make a fake MultiIndex so that area is correct for legend
    bus_sizes.rename(index=lambda x: x.replace(" H2", ""), level=0, inplace=True)
    # drop all links which are not H2 pipelines
    n.links.drop(
        n.links.index[~n.links.carrier.str.contains("H2 pipeline")], inplace=True
    )

    h2_new = n.links[n.links.carrier == "H2 pipeline"]
    h2_retro = n.links[n.links.carrier == "H2 pipeline retrofitted"]

    if snakemake.params.foresight == "myopic":
        # sum capacitiy for pipelines from different investment periods
        h2_new = group_pipes(h2_new)

        if not h2_retro.empty:
            h2_retro = (
                group_pipes(h2_retro, drop_direction=True)
                .reindex(h2_new.index)
                .fillna(0)
            )

    if not h2_retro.empty:
        if snakemake.params.foresight != "myopic":
            positive_order = h2_retro.bus0 < h2_retro.bus1
            h2_retro_p = h2_retro[positive_order]
            swap_buses = {"bus0": "bus1", "bus1": "bus0"}
            h2_retro_n = h2_retro[~positive_order].rename(columns=swap_buses)
            h2_retro = pd.concat([h2_retro_p, h2_retro_n])

            h2_retro["index_orig"] = h2_retro.index
            h2_retro.index = h2_retro.apply(
                lambda x: f"H2 pipeline {x.bus0.replace(' H2', '')} -> {x.bus1.replace(' H2', '')}",
                axis=1,
            )

        retro_w_new_i = h2_retro.index.intersection(h2_new.index)
        h2_retro_w_new = h2_retro.loc[retro_w_new_i]

        retro_wo_new_i = h2_retro.index.difference(h2_new.index)
        h2_retro_wo_new = h2_retro.loc[retro_wo_new_i]
        h2_retro_wo_new.index = h2_retro_wo_new.index_orig

        to_concat = [h2_new, h2_retro_w_new, h2_retro_wo_new]
        h2_total = pd.concat(to_concat).p_nom_opt.groupby(level=0).sum()

    else:
        h2_total = h2_new.p_nom_opt

    link_widths_total = h2_total / linewidth_factor

    n.links.rename(index=lambda x: x.split("-2")[0], inplace=True)
    n.links = n.links.groupby(level=0).first()
    link_widths_total = link_widths_total.reindex(n.links.index).fillna(0.0)
    link_widths_total[n.links.p_nom_opt < line_lower_threshold] = 0.0

    retro = n.links.p_nom_opt.where(
        n.links.carrier == "H2 pipeline retrofitted", other=0.0
    )
    link_widths_retro = retro / linewidth_factor
    link_widths_retro[n.links.p_nom_opt < line_lower_threshold] = 0.0

    n.links.bus0 = n.links.bus0.str.replace(" H2", "")
    n.links.bus1 = n.links.bus1.str.replace(" H2", "")
    regions = regions.to_crs(proj.proj4_init)

    fig, ax = plt.subplots(figsize=(15, 15), subplot_kw={"projection": proj})

    color_h2_pipe = "#c5c9c7"
    color_retrofit = "#11875d"
    bus_colors = {"H2 Electrolysis": "#ffbacd"}
    n.plot(
        geomap=True,
        bus_sizes=bus_sizes,
        bus_colors=bus_colors,
        link_colors=color_h2_pipe,
        link_widths=link_widths_total,
        branch_components=["Link"],
        ax=ax,
        **map_opts,
    )

    n.plot(
        geomap=True,
        bus_sizes=0,
        link_colors=color_retrofit,
        link_widths=link_widths_retro,
        branch_components=["Link"],
        ax=ax,
        **map_opts,
    )

    regions.plot(
        ax=ax,
        column="H2",
        cmap="Blues",
        linewidths=0,
        legend=True,
        vmax=6,
        vmin=0,
        legend_kwds={
            "label": "Hydrogen Storage [TWh]",
            "shrink": 0.7,
            "extend": "max",
        },
    )

    sizes = [50, 10]
    labels = [f"{s} GW" for s in sizes]
    sizes = [s / bus_size_factor * 1e3 for s in sizes]

    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(0.05, 1),
        labelspacing=1.2,
        handletextpad=0,
        frameon=False,
        fontsize=15,
    )

    add_legend_circles(
        ax,
        sizes,
        labels,
        srid=n.srid,
        patch_kw=dict(facecolor="black"),
        legend_kw=legend_kw,
    )

    sizes = [30, 10]
    labels = [f"{s} GW" for s in sizes]
    scale = 1e3 / linewidth_factor
    sizes = [s * scale for s in sizes]

    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(0.05, 0.9),
        frameon=False,
        labelspacing=0.8,
        handletextpad=1,
        fontsize=15,
    )

    add_legend_lines(
        ax,
        sizes,
        labels,
        patch_kw=dict(color="black"),
        legend_kw=legend_kw,
    )

    colors = [bus_colors[c] for c in carriers] + [color_h2_pipe, color_retrofit]
    labels = carriers + ["H2 pipeline (total)", "H2 pipeline (repurposed)"]

    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(0.2, 1),
        ncol=1,
        frameon=False,
        fontsize=15,
    )

    add_legend_patches(ax, colors, labels, legend_kw=legend_kw)


    ax.set_facecolor("white")
    
    return fig

def plot_ch4_map(network):
    # if "gas pipeline" not in n.links.carrier.unique():
    #     return
    n = network.copy()
    assign_locations(n)

    bus_size_factor = 10e8
    linewidth_factor = 0.5e4
    # MW below which not drawn
    line_lower_threshold = 1e3

    # Drop non-electric buses so they don't clutter the plot
    n.buses.drop(n.buses.index[n.buses.carrier != "AC"], inplace=True)

    fossil_gas_i = n.generators[n.generators.carrier == "gas"].index
    fossil_gas = (
        n.generators_t.p.loc[:, fossil_gas_i]
        .mul(n.snapshot_weightings.generators, axis=0)
        .sum()
        .groupby(n.generators.loc[fossil_gas_i, "bus"])
        .sum()
        / bus_size_factor
    )
    fossil_gas.rename(index=lambda x: x.replace(" gas", ""), inplace=True)
    fossil_gas = fossil_gas.reindex(n.buses.index).fillna(0)
    # make a fake MultiIndex so that area is correct for legend
    fossil_gas.index = pd.MultiIndex.from_product([fossil_gas.index, ["fossil gas"]])

    methanation_i = n.links.query("carrier == 'Sabatier'").index
    methanation = (
        abs(
            n.links_t.p1.loc[:, methanation_i].mul(
                n.snapshot_weightings.generators, axis=0
            )
        )
        .sum()
        .groupby(n.links.loc[methanation_i, "bus1"])
        .sum()
        / bus_size_factor
    )
    methanation = (
        methanation.groupby(methanation.index)
        .sum()
        .rename(index=lambda x: x.replace(" gas", ""))
    )
    # make a fake MultiIndex so that area is correct for legend
    methanation.index = pd.MultiIndex.from_product([methanation.index, ["methanation"]])

    biogas_i = n.generators[n.generators.carrier == "biogas"].index
    biogas = (
        n.generators_t.p.loc[:, biogas_i]
        .mul(n.snapshot_weightings.generators, axis=0)
        .sum()
        .groupby(n.generators.loc[biogas_i, "bus"])
        .sum()
        / bus_size_factor
    )
    biogas = (
        biogas.groupby(biogas.index)
        .sum()
        .rename(index=lambda x: x.replace(" biogas", ""))
    )
    # make a fake MultiIndex so that area is correct for legend
    biogas.index = pd.MultiIndex.from_product([biogas.index, ["biogas"]])

    bus_sizes = pd.concat([fossil_gas, methanation, biogas])
    bus_sizes.sort_index(inplace=True)

    to_remove = n.links.index[~n.links.carrier.str.contains("gas pipeline")]
    n.links.drop(to_remove, inplace=True)

    link_widths_rem = n.links.p_nom_opt / linewidth_factor
    link_widths_rem[n.links.p_nom_opt < line_lower_threshold] = 0.0

    link_widths_orig = n.links.p_nom / linewidth_factor
    link_widths_orig[n.links.p_nom < line_lower_threshold] = 0.0

    max_usage = n.links_t.p0[n.links.index].abs().max(axis=0)
    link_widths_used = max_usage / linewidth_factor
    link_widths_used[max_usage < line_lower_threshold] = 0.0

    tech_colors = snakemake.params.plotting["tech_colors"]

    # pipe_colors = {
    #     "gas pipeline": "#f08080",
    #     "gas pipeline new": "#c46868",
    #     "gas pipeline retrofitted to H2": "#499a9c",
    #     "gas pipeline (available)": "#e8d1d1",
    # }
    pipe_colors = {
        "gas pipeline": "#ffb07c",
        "gas pipeline new": "#c14a09",
        "gas pipeline retrofitted to H2": "#11875d",
        "gas pipeline (available)": "#fbeeac",
    }

    link_color_used = n.links.carrier.map(pipe_colors)

    n.links.bus0 = n.links.bus0.str.replace(" gas", "")
    n.links.bus1 = n.links.bus1.str.replace(" gas", "")

    # bus_colors = {
    #     "fossil gas": tech_colors["fossil gas"],
    #     "methanation": tech_colors["methanation"],
    #     "biogas": "seagreen",
    # }
    bus_colors = {
        "fossil gas": "#9a0200",
        "methanation": "#ffbacd",
        "biogas": "#32bf84",
    }

    fig, ax = plt.subplots(figsize=(15, 15), subplot_kw={"projection": proj})
    eu_location = config["plotting"].get("eu_node_location", dict(x=-50, y=46))
    n.buses.loc["EU gas", "x"] = eu_location["x"]
    n.buses.loc["EU gas", "y"] = eu_location["y"]
    n.plot(
        bus_sizes=bus_sizes,
        bus_colors=bus_colors,
        link_colors=pipe_colors["gas pipeline retrofitted to H2"],
        link_widths=link_widths_orig,
        branch_components=["Link"],
        ax=ax,
        **map_opts,
    )

    n.plot(
        ax=ax,
        bus_sizes=0.0,
        link_colors=pipe_colors["gas pipeline (available)"],
        link_widths=link_widths_rem,
        branch_components=["Link"],
        # color_geomap=False,
        boundaries=map_opts["boundaries"],
    )

    n.plot(
        ax=ax,
        bus_sizes=0.0,
        link_colors=link_color_used,
        link_widths=link_widths_used,
        branch_components=["Link"],
        # color_geomap=False,
        boundaries=map_opts["boundaries"],
    )

    sizes = [100, 10]
    labels = [f"{s} TWh" for s in sizes]
    sizes = [s / bus_size_factor * 1e6 for s in sizes]

    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(0, 0.8),
        labelspacing=0.8,
        frameon=False,
        handletextpad=1,
        fontsize=15,
        title="gas sources supply",
    )

    add_legend_circles(
        ax,
        sizes,
        labels,
        srid=n.srid,
        patch_kw=dict(facecolor="black"),
        legend_kw=legend_kw,
    )

    sizes = [50, 10]
    labels = [f"{s} GW" for s in sizes]
    scale = 1e3 / linewidth_factor
    sizes = [s * scale for s in sizes]

    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(0, 0.6),
        frameon=False,
        labelspacing=0.8,
        fontsize=15,
        handletextpad=1,
        title="gas pipeline",
    )

    add_legend_lines(
        ax,
        sizes,
        labels,
        patch_kw=dict(color="black"),
        legend_kw=legend_kw,
    )

    colors = list(pipe_colors.values()) + list(bus_colors.values())
    labels = list(pipe_colors.keys()) + list(bus_colors.keys())

    # legend on the side
    # legend_kw = dict(
    #     bbox_to_anchor=(1.47, 1.04),
    #     frameon=False,
    # )

    legend_kw = dict(
        loc="upper left",
        bbox_to_anchor=(1, 0.9),
        ncol=1,
        frameon=False,
        fontsize=15,
    )

    add_legend_patches(
        ax,
        colors,
        labels,
        legend_kw=legend_kw,
    )
    
    return fig



def create_H2_map_plots(planning_horizons):
    
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
    <style>
    /* ... (existing styles) ... */
    </style>
    </head>
    <body>
    <div class="tab">
    """
    planning_horizons = [2025, 2030, 2035, 2040, 2045, 2050]
    for i, planning_horizon in enumerate(planning_horizons):
        # Load network for the current planning horizon
        n = loaded_files[planning_horizon]

        # Plot the H2 map and get the figure
        fig = plot_h2_map(network=n)
        plt.rcParams['legend.title_fontsize'] = '20'
        
        # Save the H2 map plot as an image
        output_image_path = f"results/{study}/htmls/raw_html/map_h2_plot_{planning_horizon}.png"
        fig.savefig(output_image_path, dpi=80, bbox_inches="tight")
        plt.close(fig)  # Close the figure to avoid displaying it in the notebook
        del fig
        del n
        gc.collect()
        # Encode the image as base64
        with open(output_image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Add tab content for each planning horizon with embedded image data
        html_content += f"""
       <button class="h2-tablinks{' active' if i == 0 else ''}" onclick="openH2Tab(event, 'h2_{planning_horizon}_{i + 1}')">{planning_horizon}</button>
       """

    html_content += """
    </div>
    """

    for i, planning_horizon in enumerate(planning_horizons):
        # Load network for the current planning horizon
        n = loaded_files[planning_horizon]
        fig = plot_h2_map(network=n)
        plt.rcParams['legend.title_fontsize'] = '20'

        # fig.savefig(f"results/pdf/H2_maps_{planning_horizon}.pdf", bbox_inches="tight")
        # Save the H2 map plot as an image
        output_image_path = f"results/{study}/htmls/raw_html/map_h2_plot_{planning_horizon}.png"
        fig.savefig(output_image_path, dpi=80, bbox_inches="tight")
        plt.close(fig)  # Close the figure to avoid displaying it in the notebook
        del fig
        del n
        gc.collect()
        # Encode the image as base64
        with open(output_image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")
        html_content += f"""
        <div id="h2_{planning_horizon}_{i + 1}" class="h2-tabcontent" style="display: {'block' if i == 0 else 'none'};">
            <h2>H2 Map Plot - {planning_horizon}</h2>
            <img src="data:image/png;base64,{encoded_image}" alt="H2 Map Plot" width="1200" height="800">
        </div>
        """
    html_content += """
    <script>
    function openH2Tab(evt, tabName) {
      var i, tabcontent, tablinks;
      tabcontent = document.getElementsByClassName("h2-tabcontent");
      for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
      }
      tablinks = document.getElementsByClassName("h2-tablinks");
      for (i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
      }
      document.getElementById(tabName).style.display = "block";
      evt.currentTarget.className += " active";
    }
    </script>
    </body>
    </html>
    """

    # Save the entire HTML content to a single file
    output_combined_html_path = f"results/{study}/htmls/raw_html/map_h2_plots.html"
    with open(output_combined_html_path, "w") as html_file:
        html_file.write(html_content)
    gc.collect()
def create_gas_map_plots(planning_horizons):
   
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
    <style>
    /* ... (existing styles) ... */
    </style>
    </head>
    <body>
    <div class="tab">
    """
    for i, planning_horizon in enumerate(planning_horizons):
        # Load network for the current planning horizon
        n = loaded_files[planning_horizon]

        # Plot the H2 map and get the figure
        fig = plot_ch4_map(network=n)
        plt.rcParams['legend.title_fontsize'] = '20'
        # Save the H2 map plot as an image
        output_image_path = f"results/{study}/htmls/raw_html/map_ch4_plot_{planning_horizon}.png"
        fig.savefig(output_image_path, dpi=80, bbox_inches="tight")
        plt.close(fig)  # Close the figure to avoid displaying it in the notebook
        del fig
        del n
        gc.collect()
        # Encode the image as base64
        with open(output_image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Add tab content for each planning horizon with embedded image data
        html_content += f"""
        <button class="gas-tablinks{' active' if i == 0 else ''}" onclick="openGasTab(event, 'gas_{planning_horizon}_{i + 1}')">{planning_horizon}</button>
        """

    html_content += """
    </div>
    """
    
    for i, planning_horizon in enumerate(planning_horizons):
        # Load network for the current planning horizon
        n = loaded_files[planning_horizon]
        fig = plot_ch4_map(network=n)
        plt.rcParams['legend.title_fontsize'] = '20'
    
        # fig.savefig(f"results/pdf/gas_maps_{planning_horizon}.pdf", bbox_inches="tight")
        # Save the H2 map plot as an image
        output_image_path = f"results/{study}/htmls/raw_html/map_ch4_plot_{planning_horizon}.png"
        fig.savefig(output_image_path, dpi=80, bbox_inches="tight")
        plt.close(fig)  # Close the figure to avoid displaying it in the notebook
        del fig
        del n
        gc.collect()
        # Encode the image as base64
        with open(output_image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Add tab content for each planning horizon with embedded image data
        html_content += f"""
        <div id="gas_{planning_horizon}_{i + 1}" class="gas-tabcontent" style="display: {'block' if i == 0 else 'none'};">
            <h2>Gas Map Plot - {planning_horizon}</h2>
            <img src="data:image/png;base64,{encoded_image}" alt="Gas Map Plot" width="1200" height="800">
        </div>
        """

    # Add JavaScript for tab functionality
    html_content += """
    <script>
    function openGasTab(evt, tabName) {
      var i, tabcontent, tablinks;
      tabcontent = document.getElementsByClassName("gas-tabcontent");
      for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
      }
      tablinks = document.getElementsByClassName("gas-tablinks");
      for (i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
      }
      document.getElementById(tabName).style.display = "block";
      evt.currentTarget.className += " active";
    }
    </script>
    </body>
    </html>
    """

    # Save the entire HTML content to a single file
    output_combined_html_path = f"results/{study}/htmls/raw_html/map_ch4_plots.html"
    with open(output_combined_html_path, "w") as html_file:
        html_file.write(html_content)
    gc.collect() 
def create_bar_chart(costs, country, unit='Euros/year'):
    tech_colors = snakemake.params.plotting["tech_colors"]
    tech_colors["AC Transmission"] = "#FF3030"
    tech_colors["DC Transmission"] = "#104E8B"

    title = f"{country} - Total Annual Costs"
    df = costs[country]
    df = df.rename_axis(unit)
    df = df.reset_index()
    df.index = df.index.astype(str)

    fig = go.Figure()
    df_transposed = df.set_index(unit).T

    for tech in df_transposed.columns:
        y = df_transposed[tech]
        color = tech_colors.get(tech, 'lightgrey')

        # Positive values
        fig.add_trace(go.Bar(
            x=df_transposed.index,
            y=y.where(y > 0, 0),
            name=tech,
            marker_color=color,
            width=0.62
        ))

        # Negative values (plotted separately, but still under the same name)
        fig.add_trace(go.Bar(
            x=df_transposed.index,
            y=y.where(y < 0, 0),
            name=tech,
            marker_color=color,
            width=0.62,
            showlegend=False  # avoid duplicate legend
        ))

    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        name='Euro reference value = 2020',
        marker=dict(color='rgba(0,0,0,0)')
    ))

    fig.update_layout(
        height=900, width=1000,
        title=title,
        barmode='relative',  # important for splitting positive and negative
        yaxis=dict(title=unit, title_font=dict(size=15), tickfont=dict(size=15)),
        xaxis=dict(tickfont=dict(size=15)),
        legend=dict(font=dict(size=15)),
        hovermode='y'
    )


    return fig

def create_clustered_costs(c_costs, country,  unit='Euros/year'):
    tech_colors = snakemake.params.plotting["tech_colors"]
    tech_colors["Uses"] = "#01889f"
    tech_colors["Networks"] = "#fcc006"
    tech_colors["Power Plants"] = "#11875d"
    tech_colors["Imports"] = "#9a0200"

    title = f"{country} - Total Costs"
    df = c_costs[country]
    df = df.rename_axis(unit)
    df = df.reset_index()
    df.index = df.index.astype(str)

    # Create a bar chart using Plotly
    fig = go.Figure()
    df_transposed = df.set_index(unit).T

    for tech in df_transposed.columns:
        fig.add_trace(go.Bar(x=df_transposed.index, y=df_transposed[tech], name=tech, marker_color=tech_colors.get(tech, 'lightgrey'),width=0.62))
    
    fig.add_trace(go.Scatter(x=[None], y=[None], mode='markers', name='Euro reference value = 2020', marker=dict(color='rgba(0,0,0,0)')))
    # Configure layout and labels
    fig.update_layout(height=900, width=1000,title=title, barmode='stack', yaxis=dict(title=unit,title_font=dict(size=15),tickfont=dict(size=15)),xaxis=dict(tickfont=dict(size=15)),legend=dict(font=dict(size=15)))
    fig.update_layout(hovermode='y')
    
    return fig

def create_investment_costs(investment_costs, country,  unit='Euros/year'):
    tech_colors = snakemake.params.plotting["tech_colors"]
    colors = config["plotting"]["tech_colors"]
    tech_colors["AC Transmission"] = "#FF3030"
    tech_colors["DC Transmission"] = "#104E8B"
    tech_colors["Biogas Plants"] = tech_colors["Biomass"]
    tech_colors["Fossil Fuel techs"] = tech_colors["Fossil Fuels"]

    title = f"{country} - Investment Costs"
    df = investment_costs[country]
    df = df.rename_axis(unit)
    df = df.reset_index()
    df.index = df.index.astype(str)

    # Create a bar chart using Plotly
    fig = go.Figure()
    df_transposed = df.set_index(unit).T

    for tech in df_transposed.columns:
        fig.add_trace(go.Bar(x=df_transposed.index, y=df_transposed[tech], name=tech, marker_color=tech_colors.get(tech, 'lightgrey'),width=0.62))
    
    fig.add_trace(go.Scatter(x=[None], y=[None], mode='markers', name='Euro reference value = 2020', marker=dict(color='rgba(0,0,0,0)')))
    # Configure layout and labels
    fig.update_layout(height=900, width=1000,title=title, barmode='stack', yaxis=dict(title=unit,title_font=dict(size=15),tickfont=dict(size=15)),xaxis=dict(tickfont=dict(size=15)),legend=dict(font=dict(size=15)))
    fig.update_layout(hovermode='y')
    return fig


def create_operational_costs(operational_costs, country, unit='Euros/year'):
    tech_colors = snakemake.params.plotting["tech_colors"]
    tech_colors["AC Transmission"] = "#FF3030"
    tech_colors["DC Transmission"] = "#104E8B"

    title = f"{country} - Operational Costs"
    df = operational_costs[country]
    df = df.rename_axis(unit)
    df = df.reset_index()
    df.index = df.index.astype(str)

    fig = go.Figure()
    df_transposed = df.set_index(unit).T

    for tech in df_transposed.columns:
        y = df_transposed[tech]
        color = tech_colors.get(tech, 'lightgrey')

        # Positive values
        fig.add_trace(go.Bar(
            x=df_transposed.index,
            y=y.where(y > 0, 0),
            name=tech,
            marker_color=color,
            width=0.62
        ))

        # Negative values
        fig.add_trace(go.Bar(
            x=df_transposed.index,
            y=y.where(y < 0, 0),
            name=tech,
            marker_color=color,
            width=0.62,
            showlegend=False
        ))

    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        name='Euro reference value = 2020',
        marker=dict(color='rgba(0,0,0,0)')
    ))

    fig.update_layout(
        height=900, width=1000,
        title=title,
        barmode='relative',
        yaxis=dict(title=unit, title_font=dict(size=15), tickfont=dict(size=15)),
        xaxis=dict(tickfont=dict(size=15)),
        legend=dict(font=dict(size=15)),
        hovermode='y'
    )


    return fig

def create_capacity_chart(capacities, country, unit='Capacity [GW]'):
    tech_colors = snakemake.params.plotting["tech_colors"]
    colors = config["plotting"]["tech_colors"]
    tech_colors["AC Transmission"] = "#FF3030"
    tech_colors["DC Transmission"] = "#104E8B"
    tech_colors["Transmission lines"] = tech_colors["Transmission Lines"]
    groups = [
        ["solar","solar rfnbo"],
        ["onshore wind", "offshore wind"],
        ["onshore wind rfnbo", "offshore wind rfnbo"],
        ["Electrolysers","vre H2 Electrolysis"],
        ["transmission lines"],
        ["nuclear"],
        ["CCGT"],
        ["CHP"],
    ]
    
    groupss = [
        ["solar","solar rfnbo"],
        ["onshore wind", "offshore wind"],
        ["onshore wind rfnbo", "offshore wind rfnbo"],
        ["Electrolysers","vre H2 Electrolysis"],
        ["transmission lines"],
        ["power-to-liquid"],
        ["CCGT"],
        ["nuclear"],
    ]

    # Create a subplot for each technology
    years = ['2025','2030','2035', '2040','2045', '2050']
    if country != "EU":
        value = groups
    else:
        value = groupss
    def smart_capitalize(phrase):
     return phrase[0].upper() + phrase[1:] if phrase and not phrase[0].isupper() else phrase
    fig = make_subplots(rows=2, cols=len(value) // 2, subplot_titles=[", ".join(smart_capitalize(t) for t in tech_group) for tech_group in value], shared_yaxes=True)

    df = capacities[country]

    for i, tech_group in enumerate(value, start=1):
        row_idx = 1 if i <= len(value) // 2 else 2
        col_idx = i if i <= len(value) // 2 else i - len(value) // 2

        for tech in tech_group:
            if tech in df.index:
                y_values = [val / 1000 for val in df.loc[tech, years]]
                trace = go.Bar(
                    x=years,
                    y=y_values,
                    name=smart_capitalize(tech),
                    marker_color=tech_colors.get(tech, 'gray')
                )
                fig.add_trace(trace, row=row_idx, col=col_idx)
                fig.update_yaxes(title_text=unit, row=2, col=1)

    # Update layout
    fig.update_layout(height=800, width=1200, showlegend=True, title=f"Capacities for {country}", yaxis_title=unit,legend=dict(font=dict(size=15)))
    num_cols = len(value) // 2 + (len(value) % 2 > 0)
    for row in [1, 2]:
     for col in range(1, num_cols + 1):
        fig.update_xaxes(tickfont=dict(size=13), row=row, col=col)
        fig.update_yaxes(
            tickfont=dict(size=13),
            title_font=dict(size=15),
            row=row,
            col=col
        )
    logo['y']=1.03

    return fig

def storage_capacity_chart(s_capacities, country, unit='Capacity [GWh]'):
    tech_colors = snakemake.params.plotting["tech_colors"]
    colors = config["plotting"]["tech_colors"]
    colors["Thermal Energy Storage"] = colors["urban central water tanks"]
    colors["Thermal Energy storage tanks"] = colors["urban central water tanks"]
    colors["Grid-scale battery"] = 'green'
    colors["Gas storage"] = colors["gas"]
    groups = [
        ["Grid-scale battery"],
        ["Thermal Energy Storage"],
        ["Gas storage"],
    ]

    # Create a subplot for each technology
    years = ['2025','2030','2035', '2040','2045', '2050']
    fig = make_subplots(rows=1, cols=len(groups) // 1, subplot_titles=[
        f"{', '.join(tech_group)}" for tech_group in groups], shared_yaxes=False)

    df = s_capacities[country]

    for i, tech_group in enumerate(groups, start=1):
        row_idx = 1 if i <= len(groups) // 1 else 2
        col_idx = i if i <= len(groups) // 1 else i - len(groups) // 1

        for tech in tech_group:
            if tech in df.index:
                y_values = [val / 1000 for val in df.loc[tech, years]]
                trace = go.Bar(
                    x=years,
                    y=y_values,
                    name=f"{tech}",
                    marker_color=tech_colors.get(tech, 'gray')
                )
                fig.add_trace(trace, row=row_idx, col=col_idx)
                fig.update_yaxes(title_text=unit, row=2, col=1)
    
    # Update layout
    fig.update_layout(height=500, width=1200, showlegend=True, title=f" Storage Capacities for {country}", yaxis_title=unit,legend=dict(font=dict(size=15)))
    num_cols = len(groups)
    for col in range(1, num_cols + 1):
     fig.update_xaxes(tickfont=dict(size=13), row=1, col=col)
     fig.update_yaxes(
        tickfont=dict(size=13),
        title_font=dict(size=15),
        row=1,
        col=col
    )
    logo['y']=1.05
    

    return fig

def create_combined_chart_country(costs,investment_costs, capacities, s_capacities, country):
    # Create output folder if it doesn't exist
    output_folder = f"results/{study}/htmls"
    raw_html = os.path.join(output_folder,'raw_html/')
    os.makedirs(output_folder, exist_ok=True)
    os.makedirs(raw_html, exist_ok=True)
    
    #load the html plot flags
    with open(snakemake.input.plots_html, 'r') as file:
      plots = yaml.safe_load(file)
    pypsa_plots = plots.get("Pypsa_plots", {})
    display_country = "28 Countries" if country == "EU" else country
    if pypsa_plots["Sectoral Demands"] == True:
      display_country = "28 Countries" if country == "EU" else country
      plot_demands_file_path = os.path.join(raw_html, f"{country}_sectoral_demands.html")
      with open(plot_demands_file_path, "r") as plot_demands_file:
        plot_demands_html = plot_demands_file.read()
    # Create bar chart
    if pypsa_plots["Annual Costs"] == True:
      bar_chart = create_bar_chart(costs, country)
     
    if pypsa_plots["Annual Clustered Costs"] == True:
      bar_chart_clustered = create_clustered_costs(c_costs, country)
    
    # Create Investment Costs
    if pypsa_plots["Annual Investment Costs"] == True:
      bar_chart_investment = create_investment_costs(investment_costs, country)
     
    if pypsa_plots["Annual Operational Costs"] == True:
      bar_chart_operational = create_operational_costs(operational_costs, country)
    
    if pypsa_plots["CO2 Prices"] == True:
     plot_co2_file_path = os.path.join(raw_html, f"{country}_co2_price.html")
     with open(plot_co2_file_path, "r") as plot_co2_file:
       plot_co2_html = plot_co2_file.read()
    # Create capacities chart
    if pypsa_plots["Capacities"] == True:
      capacities_chart = create_capacity_chart(capacities, country)
    
    # Create storage capacities chart
    if pypsa_plots["Storage Capacities"] == True:
      s_capacities_chart = storage_capacity_chart(s_capacities, country)

    # Save the Panel object to HTML
    if pypsa_plots["Power Dispatch Winter"] == True:
      plot_series_file_path = os.path.join(raw_html, f"Power Dispatch (Winter Week) - {country}.html")
      plot_series_file_path_simp = os.path.join(raw_html, f"Power Dispatch Simplified (Winter Week) - {country}.html")
      with open(plot_series_file_path, "r") as plot_series_file:
          plot_series_html = plot_series_file.read()
    with open(plot_series_file_path_simp, "r") as plot_series_file_simp:
        plot_series_simp_html = plot_series_file_simp.read()
    if pypsa_plots["Power Dispatch Summer"] == True:
      plot_series_file_path_sum = os.path.join(raw_html, f"Power Dispatch (Summer Week) - {country}.html")
      plot_series_file_path_sum_simp = os.path.join(raw_html, f"Power Dispatch Simplified (Summer Week) - {country}.html")
      with open(plot_series_file_path_sum, "r") as plot_series_file_sum:
          plot_series_html_w = plot_series_file_sum.read()
    with open(plot_series_file_path_sum_simp, "r") as plot_series_file_sum_simp:
        plot_series_html_w_simp = plot_series_file_sum_simp.read()
    if pypsa_plots["Heat Dispatch Winter"] == True:    
      plot_series_heat_file_path = os.path.join(raw_html, f"Heat Dispatch (Winter Week) - {country}.html")
      with open(plot_series_heat_file_path, "r") as plot_series_heat_file:
          plot_series_heat_html = plot_series_heat_file.read()
    if pypsa_plots["Heat Dispatch Summer"] == True:   
      plot_series_heat_file_path_sum = os.path.join(raw_html, f"Heat Dispatch (Summer Week) - {country}.html")
      with open(plot_series_heat_file_path_sum, "r") as plot_series_heat_file_sum:
          plot_series_heat_html_w = plot_series_heat_file_sum.read()
    if pypsa_plots["Map Plots"] == True:
     if country == 'EU':
      plot_map_path = os.path.join(raw_html, f"map_plots_{country}.html")
      with open(plot_map_path, "r") as plot_map_path:
          plot_map_html = plot_map_path.read()
    if pypsa_plots["H2 Map Plots"] == True:
     if country == 'EU':
      plot_map_h2_path = os.path.join(raw_html, "map_h2_plots.html")
      with open(plot_map_h2_path, "r") as plot_map_h2_path:
          plot_map_h2_html = plot_map_h2_path.read()
    if pypsa_plots["Gas Map Plots"] == True:
     if country == 'EU':
      plot_map_ch4_path = os.path.join(raw_html, "map_ch4_plots.html")
      with open(plot_map_ch4_path, "r") as plot_map_ch4_path:
          plot_map_ch4_html = plot_map_ch4_path.read()

    # Create the content for the "Table of Contents" and "Main" sections
    table_of_contents = {
    "demands.html": "<a href='#sectoral_demands'>Sectoral Demands</a><br>",
    "costs.html": "<a href='#annual_costs'>Annual Costs</a><br>" 
                  "<a href='#annual_total_costs'>Annual Total Costs</a><br>"
                  "<a href='#investment_costs'>Annual Investment Costs</a><br>"
                  "<a href='#operational_costs'>Annual Operational Costs</a><br>"
                  "<a href='#carbon_prices'>CO2 Cost</a><br>",
                  
    "capacities.html": "<a href='#capacities'>Capacities</a><br>"
                        "<a href='#storage capacities'>Storage Capacities</a><br>",
    "dispatch_plots.html": "<a href='#heat_dispatch_winter'>Heat Dispatch (Winter)</a><br>"
                            "<a href='#heat_dispatch_summer'>Heat Dispatch (Summer)</a><br>"
                            "<a href='#power_dispatch_winter'>Power Dispatch (Winter)</a><br>"
                            "<a href='#power_dispatch_summer'>Power Dispatch (Summer)</a><br>"
                            "<a href='#power_dispatch_winter_simplified'>Power Dispatch Simplified (Winter)</a><br>"
                            "<a href='#power_dispatch_summer_simplified'>Power Dispatch Simplified (Summer)</a><br>",}
    if country == "EU":
     table_of_contents["maps.html"] = (
        "<a href='#map_plots'>Map Plots</a><br>"
        "<a href='#h2_map_plots'>H2 Map Plots</a><br>"
        "<a href='#gas_map_plots'>Gas Map Plots</a><br>"
    )
    
    html_sections = {
    "demands.html": f"<div id='sectoral_demands'><h2>{display_country} - Sectoral Demands</h2>{plot_demands_html}</div>",
    "costs.html": f"<div id='annual_costs'><h2>{display_country} - Annual Costs</h2>{bar_chart.to_html(full_html=False, include_plotlyjs='cdn')}</div>"
                  f"<div id='annual_total_costs'><h2>{display_country} - Annual Total Costs</h2>{bar_chart_clustered.to_html(full_html=False, include_plotlyjs='cdn')}</div>"
                  f"<div id='investment_costs'><h2>{display_country} - Annual Investment Costs</h2>{bar_chart_investment.to_html(full_html=False, include_plotlyjs='cdn')}</div>"
                  f"<div id='operational_costs'><h2>{display_country} - Annual Operational Costs</h2>{bar_chart_operational.to_html(full_html=False, include_plotlyjs='cdn')}</div>"
                  f"<div id='carbon_prices'><h2>{display_country} - CO2 Costs</h2>{plot_co2_html}</div>",
    "capacities.html": f"<div id='capacities'><h2>{display_country} - Capacities</h2>{capacities_chart.to_html(full_html=False, include_plotlyjs='cdn')}</div>"
                        f"<div id='storage capacities'><h2>{display_country} - Storage Capacities</h2>{s_capacities_chart.to_html(full_html=False, include_plotlyjs='cdn')}</div>",
    "dispatch_plots.html": f"<div id='heat_dispatch_winter'><h2>{display_country} - Heat Dispatch (Winter)</h2>{plot_series_heat_html}</div>"
                            f"<div id='heat_dispatch_summer'><h2>{display_country} - Heat Dispatch (Summer)</h2>{plot_series_heat_html_w}</div>"
                            f"<div id='power_dispatch_winter'><h2>{display_country} - Power Dispatch (Winter)</h2>{plot_series_html}</div>"
                            f"<div id='power_dispatch_summer'><h2>{display_country} - Power Dispatch (Summer)</h2>{plot_series_html_w}</div>"
                            f"<div id='power_dispatch_winter_simplified'><h2>{display_country} - Power Dispatch Simplified (Winter)</h2>{plot_series_simp_html}</div>"
                            f"<div id='power_dispatch_summer_simplified'><h2>{display_country} - Power Dispatch Simplified (Summer)</h2>{plot_series_html_w_simp}</div>",}
    if country == "EU":
     html_sections["maps.html"] = (
        f"<div id='map_plots'><h2>Map Plots</h2>{plot_map_html}</div>"
        f"<div id='h2_map_plots'><h2>H2 Map Plots</h2>{plot_map_h2_html}</div>"
        f"<div id='gas_map_plots'><h2>Gas Map Plots</h2>{plot_map_ch4_html}</div>"
    )
    
    template_path =  snakemake.input.template
    with open(template_path, "r") as template_file:
        template_content = template_file.read()
        template = Template(template_content)
        
    for file_name, main_content in html_sections.items():
      rendered_html = template.render(
        title=f"{country} - {file_name.split('.')[0].capitalize()}",
        country=country,
        TABLE_OF_CONTENTS=table_of_contents.get(file_name, ""),  # Add TOC per section
        MAIN=main_content,
      )
      file_name = file_name.replace(".html", "")
      combined_file_path = os.path.join(output_folder, f"{country}_{file_name}_{study}.html")
      with open(combined_file_path, "w", encoding='utf-8') as html_file:
        html_file.write(rendered_html)

def replicate_map_html(countries):
    output_folder = f"results/{study}/htmls"
    maps_file_path = os.path.join(output_folder, f"EU_maps_{study}.html")
    if not os.path.exists(maps_file_path):
        print(f"EU maps file not found: {maps_file_path}")
        return

    for country in countries:
        dst = os.path.join(output_folder, f"{country}_maps_{study}.html")
        if not os.path.exists(dst):
            shutil.copy(maps_file_path, dst)

    
if __name__ == "__main__":
    if "snakemake" not in globals():
        with open("snakemake_dump.pkl", "rb") as f:
            snakemake = pickle.load(f)
    
    cluster = snakemake.params.scenario["clusters"][0]
    opt = snakemake.params.scenario["opts"][0]
    sector_opt = snakemake.params.scenario["sector_opts"][0]
    planning_horizons = [2025, 2030, 2035, 2040, 2045, 2050]
    discount_rate = 0.07
    methanol_fuel = 119 #https://www.methanol.org/wp-content/uploads/2023/05/Marine_Methanol_Report_Methanol_Institute_May_2023.pdf
    ammonia_fuel = 92 #https://www.iee.fraunhofer.de/en/presse-infothek/press-media/2022/green-ammonia-for-climate-protection.html
    total_country = 'EU'
    countries = snakemake.params.countries 
    fn = snakemake.input.costs
    proj = load_projection(snakemake.params.plotting)
    map_opts = snakemake.params.plotting["map"]
    countries.append(total_country)
    logging.basicConfig(level=snakemake.config["logging"]["level"])
    config = snakemake.config
    study = snakemake.params.study
    logo = logo()
    loaded_files = load_files(study, planning_horizons,cluster, opt, sector_opt)
    loaded_files_baseline = load_files_baseline(study, planning_horizons,cluster, opt, sector_opt)
    results = calculate_transmission_values( cluster, opt, sector_opt, planning_horizons)
    costs = costs(countries, results)
    c_costs = clustered_costs(countries)
    investment_costs = Investment_costs(countries, results)
    operational_costs = operational_costs(countries)
    capacities = capacities(countries, results)
    s_capacities = storage_capacities(countries)
    regions_base = gpd.read_file(f"resources/{study}/regions_onshore_base_s_{cluster}.geojson").set_index("name")
    with open(snakemake.input.plots_html, 'r') as file:
     plots = yaml.safe_load(file).get('Pypsa_plots')
    if plots['Power Dispatch Winter']:
        plot_series_power(cluster, opt, sector_opt, planning_horizons,start = "2013-02-08",stop = "2013-02-14",title="Power Dispatch (Winter Week)")
        plot_series_power_cluster(cluster, opt, sector_opt, planning_horizons,start = "2013-02-06",stop = "2013-02-12",title="Power Dispatch Simplified (Winter Week)")
    if plots['Power Dispatch Summer']:
        plot_series_power(cluster, opt, sector_opt, planning_horizons,start = "2013-07-01",stop = "2013-07-07",title="Power Dispatch (Summer Week)")
        plot_series_power_cluster(cluster, opt, sector_opt, planning_horizons,start = "2013-06-20",stop = "2013-06-26",title="Power Dispatch Simplified (Summer Week)")
    if plots['Heat Dispatch Winter']:
        plot_series_heat(cluster, opt, sector_opt, planning_horizons,start = "2013-02-01",stop = "2013-02-07",title="Heat Dispatch (Winter Week)")
    if plots['Heat Dispatch Summer']:
        plot_series_heat(cluster, opt, sector_opt, planning_horizons,start = "2013-07-01",stop = "2013-07-07",title="Heat Dispatch (Summer Week)")
    plot_demands(countries)
    co2_price(countries)
    if plots['Map Plots']:
        for country in countries:
         if country == "EU":
            create_map_plots(planning_horizons, country)
    if plots['H2 Map Plots']:
     if country == "EU":
        create_H2_map_plots(planning_horizons)
    if plots['Gas Map Plots']:
     if country == "EU":
        create_gas_map_plots(planning_horizons)
    
    for country in countries:
        create_combined_chart_country(costs,investment_costs, capacities,s_capacities, country)
    replicate_map_html(countries)
