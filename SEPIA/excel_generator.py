import pandas as pd  # Read/analyse data
import pypsa
import os
import shutil
from pypsa.descriptors import get_switchable_as_dense as as_dense
    
def build_filename(cluster,opt,sector_opt ,planning_horizon):
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

def process_network(cluster, opt, sector_opt, planning_horizon, country):
        results_dict = {}
    
        n = loaded_files[planning_horizon]
        config = snakemake.config

        energy_demand = pd.read_csv(
            f"resources/{study}/energy_totals.csv",
            index_col=0)

        industry_demand = pd.read_csv(
            f"resources/{study}/industrial_energy_demand_base_s_" + str(cluster) + "_" + str(
                planning_horizon) + ".csv", index_col=0).T
        Rail_demand = energy_demand.loc[(energy_demand['year'] == year),'total rail']
        if country != 'EU':
         Rail_demand = Rail_demand[country].sum()
        else:
         Rail_demand = Rail_demand.sum().sum()
        if config["run"]["name"] != "baseline_without_H2":
         if country != 'EU':
              ammonia_t = industry_demand.loc["ammonia"]
              ammonia = ammonia_t.filter(like=country).sum()
         else:
              ammonia_t = industry_demand.loc["ammonia"]
              ammonia = ammonia_t.sum().sum()
        else:
            ammonia = 0

        collection = []
        collection.append(
            pd.Series(
                dict(label="ammonia", source="H2", target="ammonia for industry", value=ammonia)
            )
        )

        collection = pd.concat(collection, axis=1).T

        columns = ["label", "source", "target", "value"]

        if country != 'EU':
            gen = (
            (n.snapshot_weightings.generators @ n.generators_t.p).filter(like=country)
            .groupby(
                [
                    n.generators.carrier,
                    n.generators.carrier,
                    n.generators.bus.map(n.buses.carrier),
                ]
            )
            .sum()
            .div(1e6)
        )  # TWh
        else:
            gen = (
            (n.snapshot_weightings.generators @ n.generators_t.p)
            .groupby(
                [
                    n.generators.carrier,
                    n.generators.carrier,
                    n.generators.bus.map(n.buses.carrier),
                ]
            )
            .sum()
            .div(1e6)
        )  # TWh
        gen.index.set_names(columns[:-1], inplace=True)
        gen = gen.reset_index(name="value")
        gen = gen.loc[gen.value > 0.1]

        gen["source"] = gen["source"].replace({"gas": "fossil gas", "oil": "fossil oil"})
        gen["label"] = gen["label"].replace({"gas": "fossil gas", "oil": "fossil oil"})

        if country != 'EU':
            sto = (
            (n.snapshot_weightings.generators @ n.stores_t.p).filter(like=country)
            .groupby(
                [n.stores.carrier, n.stores.carrier, n.stores.bus.map(n.buses.carrier)]
            )
            .sum()
            .div(1e6)
        )
        else:
            sto = (
            (n.snapshot_weightings.generators @ n.stores_t.p)
            .groupby(
                [n.stores.carrier, n.stores.carrier, n.stores.bus.map(n.buses.carrier)]
            )
            .sum()
            .div(1e6)
        )
        sto.index.set_names(columns[:-1], inplace=True)
        sto = sto.reset_index(name="value")
        sto = sto.loc[sto.value > 0.1]

        if country != 'EU':
           su = (
            (n.snapshot_weightings.generators @ n.storage_units_t.p).filter(like=country)
            .groupby(
                [
                    n.storage_units.carrier,
                    n.storage_units.carrier,
                    n.storage_units.bus.map(n.buses.carrier),
                ]
            )
            .sum()
            .div(1e6)
        )
        else:
            su = (
              (n.snapshot_weightings.generators @ n.storage_units_t.p)
              .groupby(
                  [
                      n.storage_units.carrier,
                      n.storage_units.carrier,
                      n.storage_units.bus.map(n.buses.carrier),
                  ]
              )
              .sum()
              .div(1e6)
          )
        su.index.set_names(columns[:-1], inplace=True)
        su = su.reset_index(name="value")
        su = su.loc[su.value > 0.1]

        if country != 'EU':
           load = (
            (n.snapshot_weightings.generators @ as_dense(n, "Load", "p_set")).filter(like=country)
            .groupby([n.loads.carrier, n.loads.carrier, n.loads.bus.map(n.buses.carrier)])
            .sum()
            .div(1e6)
            .swaplevel()
        )  # TWh
        else:
            load = (
             (n.snapshot_weightings.generators @ as_dense(n, "Load", "p_set"))
             .groupby([n.loads.carrier, n.loads.carrier, n.loads.bus.map(n.buses.carrier)])
             .sum()
             .div(1e6)
             .swaplevel()
         )  # TWh
        load.index.set_names(columns[:-1], inplace=True)
        load = load.reset_index(name="value")

        load = load.loc[~load.label.str.contains("emissions")]
        load.target += " demand"
        
        value = load.loc[load.label.str.contains("electricity") & (load.label == "electricity"), "value"]
        load.loc[load.label.str.contains("electricity") & (load.label == "electricity"), "value"] = value - Rail_demand

        p_values = [int(key[1:]) for key in n.links_t.keys() if key.startswith("p") and key[1:].isdigit()]
        max_i = max(p_values, default=-1) + 1
        if country != 'EU':
          for i in range(max_i):
             n.links[f"total_e{i}"] = (
                     n.snapshot_weightings.generators @ n.links_t[f"p{i}"]
             ).filter(like=country).div(
                 1e6
             )  # TWh
             n.links[f"carrier_bus{i}"] = n.links[f"bus{i}"].map(n.buses.carrier).filter(like=country)
        else:
          for i in range(max_i):
            n.links[f"total_e{i}"] = (
                    n.snapshot_weightings.generators @ n.links_t[f"p{i}"]
            ).div(
                1e6
            )  # TWh
            n.links[f"carrier_bus{i}"] = n.links[f"bus{i}"].map(n.buses.carrier)

        def calculate_losses(x):
            energy_ports = x.loc[
                x.index.str.contains("carrier_bus") & ~x.str.contains("co2", na=False)
                ].index.str.replace("carrier_bus", "total_e")
            return -x.loc[energy_ports].sum()

        if country != 'EU':
          n.links[f"total_e{max_i}"] = n.links.apply(calculate_losses, axis=1).filter(
            like=country)  # e4 and bus 4 for bAU 2050
        else:
          n.links[f"total_e{max_i}"] = n.links.apply(calculate_losses, axis=1)  # e4 and bus 4 for bAU 2050
        n.links[f"carrier_bus{max_i}"] = "losses"

        df = pd.concat(
            [
                n.links.groupby(["carrier", "carrier_bus0", "carrier_bus" + str(i)]).sum()[
                    "total_e" + str(i)
                    ]
                for i in range(1, max_i + 1)
            ]
        ).reset_index()
        df.columns = columns

        # fix heat pump energy balance

        hp = n.links.loc[n.links.carrier.str.contains("heat pump")]

        if country != 'EU':
          hp_t_elec = n.links_t.p0.filter(like="heat pump").filter(like=country)
        else:
          hp_t_elec = n.links_t.p0.filter(like="heat pump")

        grouper = [hp["carrier"], hp["carrier_bus0"], hp["carrier_bus1"]]
        if country != 'EU':
          hp_elec = (
            (-n.snapshot_weightings.generators @ hp_t_elec).filter(like=country)
            .groupby(grouper)
            .sum()
            .div(1e6)
            .reset_index()
        )
        else:
          hp_elec = (
            (-n.snapshot_weightings.generators @ hp_t_elec)
            .groupby(grouper)
            .sum()
            .div(1e6)
            .reset_index()
        )
        hp_elec.columns = columns

        df = df.loc[~(df.label.str.contains("heat pump") & (df.target == "losses"))]

        df.loc[df.label.str.contains("heat pump"), "value"] -= hp_elec["value"].values

        df.loc[df.label.str.contains("air heat pump"), "source"] = "air-sourced ambient"
        df.loc[
            df.label.str.contains("ground heat pump"), "source"
        ] = "ground-sourced ambient"

        df = pd.concat([df, hp_elec])
        df = df.set_index(["label", "source", "target"]).squeeze()
        df = pd.concat(
            [
                df.loc[df < 0].mul(-1),
                df.loc[df > 0].swaplevel(1, 2),
            ]
        ).reset_index()
        df.columns = columns

        # make DAC demand
        df.loc[df.label == "DAC", "target"] = "DAC"

        to_concat = [df, gen, su, sto, load,collection]
        connections = pd.concat(to_concat).sort_index().reset_index(drop=True)
        # aggregation

        src_contains = connections.source.str.contains
        trg_contains = connections.target.str.contains

        connections.loc[src_contains("low voltage"), "source"] = "AC"
        connections.loc[trg_contains("low voltage"), "target"] = "AC"
        connections.loc[src_contains("CCGT"), "source"] = "gas"
        connections.loc[trg_contains("CCGT"), "target"] = "AC"
        connections.loc[src_contains("OCGT"), "source"] = "gas"
        connections.loc[trg_contains("OCGT"), "target"] = "AC"
        connections.loc[src_contains("water tank"), "source"] = "water tank"
        connections.loc[trg_contains("water tank"), "target"] = "water tank"
        connections.loc[src_contains("solar thermal"), "source"] = "solar thermal"
        connections.loc[src_contains("battery"), "source"] = "battery"
        connections.loc[trg_contains("battery"), "target"] = "battery"
        connections.loc[src_contains("Li ion"), "source"] = "battery"
        connections.loc[trg_contains("Li ion"), "target"] = "battery"

        connections.loc[src_contains("heat") & ~src_contains("demand"), "source"] = "heat"
        connections.loc[trg_contains("heat") & ~trg_contains("demand"), "target"] = "heat"
        new_row1 = {'label': 'Rail Network',
                    'source': 'Electricity grid',
                    'target': 'Rail Network',
                    'value': Rail_demand}
        connections.loc[len(connections)] = pd.Series(new_row1)

        connections = connections.loc[
            ~(connections.source == connections.target)
            & ~connections.source.str.contains("co2")
            & ~connections.target.str.contains("co2")
            & ~connections.source.str.contains("emissions")
            & ~connections.source.isin(["gas for industry", "solid biomass for industry"])
            & (connections.value >= 0.1)
            ]

        connections.replace("AC", "electricity grid", inplace=True)
        suffix_counter = {}

        def generate_new_label(label):
            if label in suffix_counter:
                suffix_counter[label] += 1
            else:
                suffix_counter[label] = 1

            if suffix_counter[label] > 1:
                return f"{label}_{suffix_counter[label]}"
            return label

        connections['label'] = connections['label'].apply(generate_new_label)
        connections.rename(columns={'value': str(planning_horizon)}, inplace=True)
        results_dict[country] = connections

        return results_dict


# %%
entries_to_select = ['solar', 'solar rooftop', 'onwind','solar-hsat',
                      'offwind-ac', 'offwind-dc', 'hydro', 'ror', 'nuclear',
                      'coal', 'lignite', 'CCGT', 'urban central solid biomass CHP', 'urban central solid biomass CHP_2',
                      'H2 Electrolysis', 'methanolisation', 'Haber-Bosch', 'SMR', 'SMR CC', 'coal_2',
                      'lignite_2','solar Electrolyser','onwind Electrolyser','offwind-ac Electrolyser','offwind-dc Electrolyser',
                      'CCGT_2', 'urban central solid biomass CHP_3', 'H2 Electrolysis_3', 'methanolisation_2',
                      'Haber-Bosch_2','H2 Electrolysis_2','FLC Electrolysis','FLC Electrolysis_2',
                      'urban decentral biomass boiler_2','FLC Electrolysis_3',
                      'rural gas boiler_2','Haber-Bosch_3','vre H2 Electrolysis','vre H2 Electrolysis_2',
                      'rural oil boiler_2','solar vre','onwind vre','offwind-ac vre','offwind-dc vre',
                      'urban decentral oil boiler_2','biomass-to-methanol_2',
                      'electricity', 'Rail Network', 'urban decentral gas boiler',
                      'rural gas boiler', 'rural oil boiler',
                      'urban decentral oil boiler','biomass-to-methanol',
                      'rural ground heat pump', 'rural ground heat pump_2',
                      'urban decentral air heat pump', 'urban decentral air heat pump_2',
                      'rural resistive heater', 'urban decentral resistive heater_2',
                      'rural resistive heater_2','agriculture heat',
                      'land transport oil', 'land transport fuel cell', 'land transport EV', 'kerosene for aviation',
                      'shipping oil', 'shipping methanol',
                      'solid biomass for industry', 'solid biomass for industry CC', 'gas for industry',
                      'gas for industry CC', 'industry electricity',
                      'low-temperature heat for industry', 'H2 for industry', 'naphtha for industry',
                      'H2 for non-energy', 'agriculture machinery oil', 'agriculture electricity',
                      'EV charger', 'EV charger_2', 'V2G', 'V2G_2','ammonia',
                      'urban central air heat pump', 'urban central air heat pump_2', 'urban central gas boiler',
                      'urban central gas boiler_2','methanolisation_3',
                      'urban central resistive heater', 'urban central resistive heater_2',
                      'rural heat','urban decentral gas boiler_2','biomass to liquid',
                      'urban decentral heat','rural biomass boiler_2','urban central water tanks charger',
                      'gas for industry CC_2', 'SMR_2', 'SMR CC_2', 'urban central heat',
                      'rural water tanks charger', 'rural water tanks discharger',
                      'battery charger', 'battery charger_2','H2 pipeline_2','fossil oil',
                      'battery discharger', 'battery discharger_2', 'H2 turbine', 'H2 turbine_2', 'Fischer-Tropsch',
                      'Fischer-Tropsch_2', 'Fischer-Tropsch_3','urban central water tanks discharger',
                      'urban central gas CHP', 'urban central gas CHP_2', 'urban central gas CHP_3', 'OCGT', 'OCGT_2',
                      'biogas to gas', 'BioSNG', 'Sabatier','kerosene for aviation_2',
                      'urban central solid biomass CHP CC', 'urban central solid biomass CHP CC_2',
                      'urban central solid biomass CHP CC_3','biomass to liquid CC',
                      'DAC', 'DAC_3', 'H2 for shipping', 'biomass','BioSNG_2',
                      'urban central gas CHP CC','urban central gas CHP CC_2','urban central gas CHP CC_3',
                      'H2 Fuel Cell', 'H2 Fuel Cell_2','H2 Fuel Cell_3','coal for industry','biogas to gas CC',
                      'home battery charger', 'home battery discharger','home battery charger_2', 'home battery discharger_2',
                      'DC','Oil for industry','industry methanol','agriculture machinery oil_2','industry methanol_2',
                      'municipal solid waste','naphtha for industry_2','offwind-float','shipping methanol_2',
                      'solid biomass import','urban central water pits charger','urban central water pits discharger',
                      'urban decentral water tanks charger','urban decentral water tanks discharger',
                      'waste CHP', 'waste CHP CC','naphtha for industry_3','waste CHP_2','waste CHP CC_2',
                      'HVC to air','Haber-Bosch_4','biomass to liquid CC_2','electricity distribution grid',
                      'electrobiofuels','electrobiofuels_2','methanolisation_4',#'oil refining_2','oil refining', 
                      'solid biomass for industry CC_2','waste CHP_3','waste CHP CC_3','electrobiofuels_3',
                      'naphtha for industry_4','coal for industry_2','land transport oil_2','oil','oil_2',
                      'urban decentral biomass boiler','urban decentral biomass boiler_2',
                      'rural biomass boiler','urban decentral resistive heater','biomass to liquid_2']  # Add moe entries if needed
  


entry_label_mapping = {
    'solar': {'label': 'Solar photovoltaic Production', 'source': 'TWh', 'target': 'prospv'},
    'solar rooftop': {'label': 'Solar photovoltaic Production Rooftop', 'source': 'TWh', 'target': 'prospvr'},
    'solar-hsat': {'label': 'Solar photovoltaic Production hsat', 'source': 'TWh', 'target': 'prohsat'},
    'onwind': {'label': 'Onshore wind-generated electricity', 'source': 'TWh', 'target': 'proeon'},
    'oil': {'label': 'Oil-fired generation', 'source': 'TWh', 'target': 'proelcpet'},
    'oil_2': {'label': 'Trnasformation losses iil-fired generation', 'source': 'TWh', 'target': 'pof'},
    'offwind-ac': {'label': 'Offshore wind-generated electricity', 'source': 'TWh', 'target': 'proeofac'},
    'offwind-dc': {'label': 'Offshore wind-generated electricity', 'source': 'TWh', 'target': 'proeofdc'},
    'offwind-float': {'label': 'floating offshore wind-generated electricity', 'source': 'TWh', 'target': 'proeof'},
    'hydro': {'label': 'Total hydropower production', 'source': 'TWh', 'target': 'prohdr'},
    'ror': {'label': 'Total ror production', 'source': 'TWh', 'target': 'prohdror'},
    'nuclear': {'label': 'Nuclear production', 'source': 'TWh', 'target': 'proelcnuc'},
    'biomass': {'label': 'biomass elec production', 'source': 'TWh', 'target': 'proelcboi'},
    'coal': {'label': 'Coal-fired power generation', 'source': 'TWh', 'target': 'proelccms'},
    'lignite': {'label': 'lignite power generation', 'source': 'TWh', 'target': 'proelign'},
    'CCGT': {'label': 'Gas-fired power generation', 'source': 'TWh', 'target': 'proelcgaz'},
    'solar vre':{'label': 'solar connected electrolyser', 'source': 'TWh', 'target': 'proesolh'},
    'onwind vre':{'label': 'onwind connected electrolyser', 'source': 'TWh', 'target': 'proeonh'},
    'offwind-ac vre':{'label': 'offwind-ac connected electrolyser', 'source': 'TWh', 'target': 'proeoffach'},
    'offwind-dc vre':{'label': 'offwind-dc connected electrolyser', 'source': 'TWh', 'target': 'proeoffdch'},
    'vre H2 Electrolysis':{'label': 'vre connected electrolyser', 'source': 'TWh', 'target': 'pregenvrehyd'},
    'vre H2 Electrolysis_2':{'label': 'vre connected electrolyser losses', 'source': 'TWh', 'target': 'proesolhlos'},
    'urban central solid biomass CHP': {'label': 'Power output from solid biomass CHP plants', 'source': 'TWh',
                                        'target': 'prbelcchpboi'},
    'urban central solid biomass CHP_2': {'label': 'Heat output from solid biomass CHP plants', 'source': 'TWh',
                                          'target': 'prbvapchpboi'},
    'H2 Electrolysis': {'label': 'Production of H2 via electrolysis', 'source': 'TWh', 'target': 'prohyd'},
    'FLC Electrolysis': {'label': 'Production of H2 via electrolysis', 'source': 'TWh', 'target': 'prohydflc'},
    'methanolisation': {'label': 'Production of methanol via methanolisation', 'source': 'TWh',
                        'target': 'promethanol'},
    'Haber-Bosch': {'label': 'Production of liquid ammonia from electricity', 'source': 'TWh', 'target': 'prohydcl'},
    'SMR': {'label': 'Production of H2 via steam methane reforming', 'source': 'TWh', 'target': 'prohydgaz'},
    'SMR CC': {'label': 'Production of H2 via steam methane reforming', 'source': 'TWh', 'target': 'prohydgazcc'},
    'coal_2': {'label': 'Transformation losses (coal-fired powerplants)', 'source': 'TWh', 'target': 'losscoal'},
    'lignite_2': {'label': 'Transformation losses (coal-fired powerplants)', 'source': 'TWh', 'target': 'losslig'},
    'CCGT_2': {'label': 'Transformation losses (gas-fired powerplants)', 'source': 'TWh', 'target': 'lossgas'},
    'urban central solid biomass CHP_3': {'label': 'Transformation losses (solid biomass CHP plants)', 'source': 'TWh',
                                          'target': 'lossbchp'},
    'H2 Electrolysis_3': {'label': 'Transformation losses (electrolysis)', 'source': 'TWh', 'target': 'losshely'},
    'H2 Electrolysis_2': {'label': 'electrolysis to heat', 'source': 'TWh', 'target': 'prehvvap'},
    'FLC Electrolysis_3': {'label': 'Transformation losses (electrolysis)', 'source': 'TWh', 'target': 'losshelyflc'},
    'FLC Electrolysis_2': {'label': 'electrolysis to heat', 'source': 'TWh', 'target': 'prehvvapflc'},
    'methanolisation_3': {'label': 'Transformation losses (methanolisation)', 'source': 'TWh', 'target': 'lossmet'},
    'methanolisation_4': {'label': 'electricity to methanol', 'source': 'TWh', 'target': 'pretareen'},
    'methanolisation_2': {'label': 'waste heat methanolisation', 'source': 'TWh', 'target': 'prehdvap'},
    'Haber-Bosch_3': {'label': 'Production losses of ammonia)', 'source': 'TWh', 'target': 'prohydclam'},
    'Haber-Bosch_2': {'label': 'haberbosch waste heat', 'source': 'TWh', 'target': 'prohydvap'},
    'Haber-Bosch_4': {'label': 'Production of ammonia from H2)', 'source': 'TWh', 'target': 'prohydclammt'},
    'urban decentral biomass boiler_2': {'label': 'Transformation losses (solid biomass boilers)',
                                                      'source': 'TWh', 'target': 'lossbbb'},
    'rural gas boiler_2': {'label': 'Transformation losses rural gas boiler', 'source': 'TWh',
                                    'target': 'lossgbs'},
    'rural biomass boiler': {'label': 'rural biomass boiler', 'source': 'TWh',
                                    'target': 'presenccfres'},
    'rural biomass boiler_2': {'label': 'rural biomass boiler losses', 'source': 'TWh',
                                    'target': 'lossbb'},
    'rural oil boiler_2': {'label': 'Transformation losses rural oil boiler', 'source': 'TWh',
                                        'target': 'lossob'},
    'urban decentral oil boiler_2': {'label': 'Transformation losses (oil-fired boilers)', 'source': 'TWh',
                                                  'target': 'lossobb'},
    'electricity': {'label': 'electricity demand of residential and tertairy', 'source': 'TWh',
                    'target': 'preselccfres'},
    'Rail Network': {'label': 'electricity demand for rail network', 'source': 'TWh', 'target': 'preserail'},
    'urban central heat': {'label': 'Residential and tertiary DH demand', 'source': 'TWh', 'target': 'presvapcfdhs'},
    'urban decentral biomass boiler': {'label': 'Residential and tertiary biomass for heating',
                                                    'source': 'TWh', 'target': 'presenccfb'},
    'urban decentral gas boiler': {'label': 'urban decentral gas boiler', 'source': 'TWh',
                                                'target': 'presgazcfg'},
    'urban decentral gas boiler_2': {'label': 'urban decentral gas boiler losses', 'source': 'TWh',
                                                'target': 'lossgbss'},
    'rural gas boiler': {'label': 'rural gas boiler', 'source': 'TWh',
                                  'target': 'presgazcfgg'},
    'rural oil boiler': {'label': 'rural oil boiler', 'source': 'TWh',
                                      'target': 'prespetcfres'},
    'urban decentral oil boiler': {'label': 'urban decentral oil boiler', 'source': 'TWh',
                                                'target': 'prespetcfo'},
    'rural ground heat pump': {'label': 'rural ground heat pump',
                                        'source': 'TWh', 'target': 'prespaccfta'},
    'rural ground heat pump_2': {'label': 'rural ground heat pump', 'source': 'TWh',
                                          'target': 'prespaccftaa'},
    'urban decentral air heat pump': {'label': 'urban decentral air heat pump',
                                                'source': 'TWh', 'target': 'prespaccfftt'},
    'urban decentral air heat pump_2': {'label': 'urban decentral air heat pump', 'source': 'TWh',
                                                  'target': 'prespaccffff'},
    'urban decentral resistive heater': {
        'label': 'urban decentral resistive heater', 'source': 'TWh',
        'target': 'preehplx'},
    'urban decentral resistive heater_2': {
        'label': 'urban decentral resistive heater losses', 'source': 'TWh',
        'target': 'lossrhh'},
    'rural resistive heater_2': {'label': 'rural resistive heater losses',
                                          'source': 'TWh', 'target': 'lossrh'},
    'rural resistive heater': {'label': 'rural resistive heater',
                                          'source': 'TWh', 'target': 'preehplyy'},
    'land transport oil': {'label': 'oil to transport demand', 'source': 'TWh', 'target': 'preslqfcftra'},
    'land transport oil_2': {'label': 'oil to transport oil demand', 'source': 'TWh', 'target': 'preslqfcfdem'},
    'land transport fuel cell': {'label': 'land transport hydrogen demand', 'source': 'TWh', 'target': 'preshydcftra'},
    'land transport EV': {'label': 'land transport EV', 'source': 'TWh', 'target': 'preselccftra'},
    'kerosene for aviation': {'label': 'aviation oil demand', 'source': 'TWh', 'target': 'preslqfcfavi'},
    'kerosene for aviation_2': {'label': 'aviation oil demand to demand', 'source': 'TWh', 'target': 'preslqfcfaviker'},
    'shipping oil': {'label': 'shipping oil', 'source': 'TWh', 'target': 'preslqfcffrewati'},
    'shipping methanol': {'label': 'shipping methanol', 'source': 'TWh', 'target': 'presngvcffrewati'},
    'shipping methanol_2': {'label': 'shipping methanol to demand', 'source': 'TWh', 'target': 'presngvcffrewatidm'},
    'H2 for shipping': {'label': 'shipping hydrogen', 'source': 'TWh', 'target': 'preshydwati'},
    'solid biomass for industry': {'label': 'solid biomass for Industry', 'source': 'TWh', 'target': 'presenccfind'},
    'solid biomass import': {'label': 'solid biomass import', 'source': 'TWh', 'target': 'presencimp'},
    'solid biomass for industry CC': {'label': 'solid biomass for Industry', 'source': 'TWh',
                                      'target': 'presenccfindd'},
    'solid biomass for industry CC_2': {'label': 'solid biomass for Industry cc losses', 'source': 'TWh',
                                      'target': 'presenccfinddloss'},
    'gas for industry': {'label': 'gas for Industry', 'source': 'TWh', 'target': 'presgazcfind'},
    'gas for industry CC': {'label': 'gas for Industry', 'source': 'TWh', 'target': 'presgazcfindd'},
    'industry electricity': {'label': 'electricity for Industry', 'source': 'TWh', 'target': 'preselccfind'},
    'industry methanol': {'label': 'methanol for Industry', 'source': 'TWh', 'target': 'presmethcfind'},
    'industry methanol_2': {'label': 'methanol to demand', 'source': 'TWh', 'target': 'presmethcfindmeth'},
    'low-temperature heat for industry': {'label': 'low-temperature heat for industry', 'source': 'TWh',
                                          'target': 'presvapcfind'},
    'H2 for industry': {'label': 'hydrogen for industry', 'source': 'TWh', 'target': 'preshydcfind'},
    'naphtha for industry': {'label': 'naphtha for non-energy', 'source': 'TWh', 'target': 'prespetcfneind'},
    'naphtha for industry_2': {'label': 'oil to naphtha for non-energy', 'source': 'TWh', 'target': 'prespetcfneindoil'},
    'naphtha for industry_3': {'label': 'oil to process emissions', 'source': 'TWh', 'target': 'prespetcfprodoil'},
    'naphtha for industry_4': {'label': 'naphtha losses', 'source': 'TWh', 'target': 'prespetcfproil'},
    'H2 for non-energy': {'label': 'H2 for non-energy', 'source': 'TWh', 'target': 'preshydcfneind'},
    'agriculture machinery oil': {'label': 'agriculture oil', 'source': 'TWh', 'target': 'prespetcfagr'},
    'agriculture machinery oil_2': {'label': 'agriculture oil to demand', 'source': 'TWh', 'target': 'prespetcfagrr'},
    'agriculture electricity': {'label': 'agriculture electricity', 'source': 'TWh', 'target': 'preselccfagr'},
    'agriculture heat': {'label': 'agriculture heat', 'source': 'TWh', 'target': 'presvapcfagr'},
    'EV charger': {'label': 'BEV charging', 'source': 'TWh', 'target': 'prebev'},
    'EV charger_2': {'label': 'BEV charging losses', 'source': 'TWh', 'target': 'prebevloss'},
    'V2G': {'label': 'vehicle to grid', 'source': 'TWh', 'target': 'prevtg'},
    'V2G_2': {'label': 'vehicle to grid losses', 'source': 'TWh', 'target': 'prevtgloss'},
    'ammonia': {'label': 'hydrogen to ammonia', 'source': 'TWh', 'target': 'prohydclamm'},
    'urban central air heat pump': {'label': 'Heat energy output from centralised heat pumps', 'source': 'TWh',
                                    'target': 'prbrchpac'},
    'urban central air heat pump_2': {'label': 'Heat energy output from centralised heat pumps', 'source': 'TWh',
                                      'target': 'prbrchpacc'},
    'urban central gas boiler': {'label': 'Heat energy output from gas-fired boilers', 'source': 'TWh',
                                  'target': 'prbrchgaz'},
    'urban central gas boiler_2': {'label': 'Heat energy output from gas-fired boilers', 'source': 'TWh',
                                    'target': 'prbrchgazzloss'},
    'urban central resistive heater': {'label': 'Heat energy output from centralised resistive heaters',
                                        'source': 'TWh', 'target': 'prbchresh'},
    'urban central resistive heater_2': {'label': 'centralised resistive heaters losses',
                                          'source': 'TWh', 'target': 'prbchreshh'},
    'urban central water tanks charger': {'label': 'TES charging', 'source': 'TWh', 'target': 'pretesdhs'},
    'urban central water tanks discharger': {'label': 'TES discharging', 'source': 'TWh', 'target': 'pretesdhd'},
    'gas for industry CC_2': {'label': 'Transformation losses gas for industry CC', 'source': 'TWh',
                              'target': 'lossgasind'},
    'SMR_2': {'label': 'Transformation losses (steam methane reforming)', 'source': 'TWh', 'target': 'lossmr'},
    'SMR CC_2': {'label': 'Transformation losses (steam methane reforming)', 'source': 'TWh', 'target': 'lossmrr'},
    'biomass to liquid': {'label': 'biomass to liquid', 'source': 'TWh', 'target': 'probmliqu'},
    'biomass to liquid CC': {'label': 'biomass to liquid CC', 'source': 'TWh', 'target': 'probmliqucc'},
    'biomass to liquid_2': {'label': 'biomass to liquid losses', 'source': 'TWh', 'target': 'probmliqlos'},
    'biomass to liquid CC_2': {'label': 'biomass to liquid CC losses', 'source': 'TWh', 'target': 'probmliqloscc'},
    'battery charger': {'label': 'battery charger', 'source': 'TWh', 'target': 'probattchg'},
    'battery charger_2': {'label': 'battery charger losses', 'source': 'TWh', 'target': 'probattchlos'},
    'battery discharger': {'label': 'battery discharger', 'source': 'TWh', 'target': 'probattdhg'},
    'battery discharger_2': {'label': 'battery discharger losses', 'source': 'TWh', 'target': 'probattdhlos'},
    'H2 turbine': {'label': 'hydrogen turbine', 'source': 'TWh', 'target': 'proelchyd'},
    'H2 turbine_2': {'label': 'hydrogen turbine losses', 'source': 'TWh', 'target': 'fftfy'},
    'Fischer-Tropsch': {'label': 'Fischer-Tropsch', 'source': 'TWh', 'target': 'profischer'},
    'Fischer-Tropsch_2': {'label': 'Fischer-Tropsch heat', 'source': 'TWh', 'target': 'profischerh'},
    'Fischer-Tropsch_3': {'label': 'Fischer-Tropsch losses', 'source': 'TWh', 'target': 'profischerlo'},
    'urban central gas CHP': {'label': 'Power output from gas CHP plants', 'source': 'TWh', 'target': 'prbelcchpgaz'},
    'urban central gas CHP_2': {'label': 'Heat output from gas CHP plants', 'source': 'TWh', 'target': 'prbvapchpgass'},
    'urban central gas CHP_3': {'label': 'Losses from gas CHP plants', 'source': 'TWh', 'target': 'dqzf'},
    'OCGT': {'label': 'Gas-fired power generation', 'source': 'TWh', 'target': 'proelcgazoc'},
    'OCGT_2': {'label': 'Gas-fired power generation losses', 'source': 'TWh', 'target': 'proelcgazoclo'},
    'biogas to gas': {'label': 'biogas to gas', 'source': 'TWh', 'target': 'prodombgr'},
    'BioSNG': {'label': 'BioSNG', 'source': 'TWh', 'target': 'probiosng'},
    'BioSNG_2': {'label': 'BioSNG losses', 'source': 'TWh', 'target': 'probiosngloss'},
    'Sabatier': {'label': 'Sabatier', 'source': 'TWh', 'target': 'presaba'},
    'Sabatier_2': {'label': 'Sabatier losses', 'source': 'TWh', 'target': 'presabaloss'},
    'urban central solid biomass CHP CC': {'label': 'Power output from solid biomass CHP plants', 'source': 'TWh',
                                            'target': 'prbelccbmcc'},
    'urban central solid biomass CHP CC_2': {'label': 'Heat output from solid biomass CHP plants', 'source': 'TWh',
                                              'target': 'prbelccbmcch'},
    'urban central solid biomass CHP CC_3': {'label': 'losses solid biomass CHP plants', 'source': 'TWh',
                                              'target': 'prbelccbmccl'},
    'DAC': {'label': 'DAC', 'source': 'TWh', 'target': 'predacelc'},
    'DAC_2': {'label': 'DAC', 'source': 'TWh', 'target': 'predache'},
    'DAC_3': {'label': 'DAC', 'source': 'TWh', 'target': 'predachee'},
    # 'oil refining': {'label': 'fossil oil', 'source': 'TWh', 'target': 'prefossiloil'},
    'fossil oil': {'label': 'fossil oil', 'source': 'TWh', 'target': 'prefossiloil'},
    # 'oil refining_2': {'label': 'fossil oil refining losses', 'source': 'TWh', 'target': 'prefoslossesref'},
    'rural heat': {'label': 'Residential and tertiary heat demand', 'source': 'TWh',
                                'target': 'demandheat'},
    'residential urban decentral heat': {'label': 'Residential and tertiary heat demand', 'source': 'TWh',
                                          'target': 'demandheata'},
    'services rural heat': {'label': 'Residential and tertiary heat demand', 'source': 'TWh', 'target': 'demandheatb'},
    'urban decentral heat': {'label': 'Residential and tertiary heat demand', 'source': 'TWh',
                                      'target': 'demandheatc'},
    'urban central gas CHP CC': {'label': 'Power output from gas CHP CC plants', 'source': 'TWh',
                                            'target': 'prbelcchpccgaz'},
    'urban central gas CHP CC_2': {'label': 'Heat output from gas CHP CC plants', 'source': 'TWh',
                                              'target': 'prbvapchpccgaz'},
    'urban central gas CHP CC_3': {'label': 'losses gas CC CHP plants', 'source': 'TWh',
                                              'target': 'lossgazchpcc'},
    'H2 Fuel Cell': {'label': 'H2 fuell cell to electricity', 'source': 'TWh', 'target': 'prbelcchphyd'},
    'H2 Fuel Cell_2': {'label': 'H2 fuell cell to heat', 'source': 'TWh', 'target': 'prbvapchphyd'},
    'H2 Fuel Cell_3': {'label': 'H2 fuell cell losses', 'source': 'TWh', 'target': 'afzf'},
    'coal for industry': {'label': 'coal for industry', 'source': 'TWh', 'target': 'cmscfind'},
    'coal for industry_2': {'label': 'coal for industry demand', 'source': 'TWh', 'target': 'cmscfinddem'},
    'biogas to gas CC': {'label': 'biogas to gas CC', 'source': 'TWh', 'target': 'prodombgrcc'},
    'home battery charger': {'label': 'home battery charger', 'source': 'TWh', 'target': 'prehbatelc'},
    'home battery charger_2': {'label': 'home battery charger losses', 'source': 'TWh', 'target': 'prebatloss'},
    'home battery discharger': {'label': 'home battery discharger', 'source': 'TWh', 'target': 'prebatelcd'},
    'home battery discharger_2': {'label': 'home battery discharger losses', 'source': 'TWh', 'target': 'prebatelcdloss'},
    'DC': {'label': 'Electricity grid losses', 'source': 'TWh', 'target': 'pregridloss'},
    'Oil for industry': {'label': 'Oil for industry', 'source': 'TWh', 'target': 'prespetcfind'},
    'municipal solid waste': {'label': 'municipal solid waste', 'source': 'TWh', 'target': 'premunwst'},
    'rural water tanks charger': {'label': 'rural water tanks charger', 'source': 'TWh', 'target': 'prerurtescharg'},
    'rural water tanks discharger': {'label': 'rural water tanks discharger', 'source': 'TWh', 'target': 'prerurtesdischarg'},
    'urban decentral water tanks charger': {'label': 'urban decentral water tanks charger', 'source': 'TWh', 'target': 'preurbtescharg'},
    'urban decentral water tanks discharger': {'label': 'urban decentral water tanks discharger', 'source': 'TWh', 'target': 'preurbtesdischarg'},
    'urban central water pits charger': {'label': 'Water pits TES charging', 'source': 'TWh', 'target': 'pretespitsdhs'},
    'urban central water pits discharger': {'label': 'Water pits TES discharging', 'source': 'TWh', 'target': 'pretespitsdhd'},
    'waste CHP': {'label': 'waste CHP', 'source': 'TWh', 'target': 'prbelcchpwst'},
    'waste CHP CC': {'label': 'waste CHP CC', 'source': 'TWh', 'target': 'prewstelccc'},
    'waste CHP_2': {'label': 'waste CHP to heat', 'source': 'TWh', 'target': 'prbvapchpwst'},
    'waste CHP CC_2': {'label': 'waste CHP CC to heat', 'source': 'TWh', 'target': 'prewstvapcc'},
    'HVC to air': {'label': 'HVC to air losses', 'source': 'TWh', 'target': 'prelosshvc'},
    'electricity distribution grid': {'label': 'electricity distribution grid losses', 'source': 'TWh', 'target': 'prelcdistloss'},
    'electrobiofuels': {'label': 'electrobiofuels', 'source': 'TWh', 'target': 'probmelcbio'},
    'electrobiofuels_2': {'label': 'electrobiofuels losses', 'source': 'TWh', 'target': 'probmelcbioloss'},
    'waste CHP_3': {'label': 'waste CHP losses', 'source': 'TWh', 'target': 'dz'},
    'waste CHP CC_3': {'label': 'waste CHP CC losses', 'source': 'TWh', 'target': 'dzzz'},
    'electrobiofuels_3': {'label': 'electrobiofuels H2', 'source': 'TWh', 'target': 'probmelcbioh'},
    'H2 pipeline_2': {'label': 'electricity to H2 compression', 'source': 'TWh', 'target': 'preelchhydcomp'},
    'biomass-to-methanol': {'label': 'biomass-to-methanol', 'source': 'TWh', 'target': 'prebmmeth'},
    'biomass-to-methanol_2': {'label': 'biomass-to-methanol losses', 'source': 'TWh', 'target': 'prebmmethlos'},
}

# %%
def prepare_emissions(cluster, opt, sector_opt,planning_horizon, country):
        '''
         This function prepare the data for co2 emissions sankey chart. All the emissions from
         the technologies are compiled together
        '''
        results_dict_co2 = {}

        n = loaded_files[planning_horizon]
        fn = snakemake.input.costs
        options = pd.read_csv(fn, index_col=[0, 1]).sort_index()

        collection = []

        # DAC
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p3.filter(like="DAC")).sum()
        else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p3.filter(like="DAC").filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(label="DAC", source="co2 atmosphere", target="co2 stored", value=value)
            )
        )

        # process emissions
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p1.filter(regex="process emissions$")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p1.filter(regex="process emissions$")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="process emissions",
                    source="process emissions",
                    target="co2 atmosphere",
                    value=value,
                )
            )
        )

        # process emissions CC
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p2.filter(regex="process emissions CC")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(regex="process emissions CC")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="process emissions CC",
                    source="process emissions",
                    target="co2 stored",
                    value=value,
                )
            )
        )

        # OCGT
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="OCGT")).sum()
        else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="OCGT").filter(like=country)).sum()
        collection.append(
            pd.Series(dict(label="OCGT", source="gas", target="co2 atmosphere", value=value))
        )
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="OCGT")
                 ).sum() * float(options.loc[("gas", "CO2 intensity")])
        else:
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="OCGT").filter(like=country)
                     ).sum() * float(options.loc[("gas", "CO2 intensity")])
        collection.append(
            pd.Series(dict(label="OCGT", source="gas", target="co2 atmosphere", value=value))
        )
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="CCGT")).sum()
        else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="CCGT").filter(like=country)).sum()
        collection.append(
            pd.Series(dict(label="CCGT", source="gas", target="co2 atmosphere", value=value))
        )
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="CCGT")
                 ).sum() * float(options.loc[("gas", "CO2 intensity")])
        else:
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="CCGT").filter(like=country)
                     ).sum() * float(options.loc[("gas", "CO2 intensity")])
        collection.append(
            pd.Series(dict(label="CCGT", source="gas", target="co2 atmosphere", value=value))
        )
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="lignite")).sum()
        else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="lignite").filter(like=country)).sum()
        collection.append(
            pd.Series(dict(label="lignite", source="coal", target="co2 atmosphere", value=value))
        )
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="lignite")
                 ).sum() * float(options.loc[("lignite", "CO2 intensity")])
        else:
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="lignite").filter(like=country)
                     ).sum() * float(options.loc[("lignite", "CO2 intensity")])
        collection.append(
            pd.Series(dict(label="lignite", source="coal", target="co2 atmosphere", value=value))
        )
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="coal")).sum()
        else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="coal").filter(like=country)).sum()
        collection.append(
            pd.Series(dict(label="coal", source="coal", target="co2 atmosphere", value=value))
        )
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="coal")
                 ).sum() * float(options.loc[("coal", "CO2 intensity")])
        else:
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="coal").filter(like=country)
                     ).sum() * float(options.loc[("coal", "CO2 intensity")])
        collection.append(
            pd.Series(dict(label="coal", source="coal", target="co2 atmosphere", value=value))
        )

        # Sabatier
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.links_t.p2.filter(like="Sabatier")).sum()
        else:
            value = (n.snapshot_weightings.generators @ n.links_t.p2.filter(like="Sabatier").filter(like=country)).sum()
        collection.append(
            pd.Series(dict(label="Sabatier", source="co2 stored", target="gas", value=value))
        )

        # SMR
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="SMR")).sum()
        else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(like="SMR").filter(like=country)).sum()
        collection.append(
            pd.Series(dict(label="SMR", source="gas", target="co2 atmosphere", value=value))
        )

        # SMR CC
        if "p3" in n.links_t:
          if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(regex="SMR CC")).sum()
          else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p2.filter(regex="SMR CC").filter(like=country)).sum()
          collection.append(
                pd.Series(dict(label="SMR CC", source="gas", target="co2 atmosphere", value=value))
            )
          if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p3.filter(like="SMR CC")).sum()
          else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p3.filter(like="SMR CC").filter(like=country)).sum()
          collection.append(
                pd.Series(dict(label="SMR CC", source="gas", target="co2 stored", value=value))
            )

        # gas boiler
        gas_boilers = [
            "rural gas boiler",
            "urban decentral gas boiler",
            "urban central gas boiler",
        ]
        for gas_boiler in gas_boilers:
          if country == 'EU':
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like=gas_boiler)
                    ).sum()
          else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like=gas_boiler)
                    .filter(like=country)).sum()
          collection.append(
                pd.Series(
                    dict(label=gas_boiler, source="gas", target="co2 atmosphere", value=value)
                )
            )
        # oil boiler
        oil_boilers = [
            "rural oil boiler",
            "urban decentral oil boiler",
            "urban central oil boiler",
        ]
        for oil_boiler in oil_boilers:
          if country == 'EU':
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like=oil_boiler)
                    ).sum()
          else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like=oil_boiler)
                    .filter(like=country)).sum()
          collection.append(
                pd.Series(
                    dict(label=oil_boiler, source="oil", target="co2 atmosphere", value=value)
                )
            )
        # biogas to gas
        if country == 'EU':
            value = (
                n.snapshot_weightings.generators @ n.links_t.p2.filter(like="biogas to gas")
                ).sum()
        else:
            value = (
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like="biogas to gas")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="biogas to gas", source="co2 atmosphere", target="biogas", value=value
                )
            )
        )
        collection.append(
            pd.Series(dict(label="biogas to gas", source="biogas", target="gas", value=value))
        )
        
        # biogas to gas with CO2 upgradation
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p2.filter(like="biogas to gas CC")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like="biogas to gas CC")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="biogas to gas CC", source="biogas", target="co2 stored", value=value
                )
            )
        )
        if country == 'EU':
            value = (
                n.snapshot_weightings.generators @ n.links_t.p3.filter(like="biogas to gas CC")
                ).sum()
        else:
            value = (
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="biogas to gas CC")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="biogas to gas CC2", source="co2 atmosphere", target="biogas", value=value
                )
            )
        )
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p1.filter(like="biogas to gas CC")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p1.filter(like="biogas to gas CC")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="biogas to gas CC3", source="biogas", target="gas", value=value * float(options.loc[("gas", "CO2 intensity")])
                )
            )
        )

        # solid biomass for industry
        if country == 'EU':
            value = (
                n.snapshot_weightings.generators
                @ n.links_t.p0.filter(regex="solid biomass for industry$")
                ).sum()
        else:
            value = (
                    n.snapshot_weightings.generators
                    @ n.links_t.p0.filter(regex="solid biomass for industry$")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="solid biomass for industry",
                    source="solid biomass",
                    target="co2 atmosphere",
                    value=value * float(options.loc[("solid biomass", "CO2 intensity")]),
                )
            )
        )
        collection.append(
            pd.Series(dict(label="solid biomass for industry", source="co2 atmosphere", target="solid biomass",
                           value=value * float(options.loc[("solid biomass", "CO2 intensity")])))
        )
        # solid biomass for industry CC
        if "p3" in n.links_t:
          if country == 'EU':
            value = (
                    n.snapshot_weightings.generators
                    @ n.links_t.p2.filter(like="solid biomass for industry CC")
                    ).sum()
          else:
            value = (
                    n.snapshot_weightings.generators
                    @ n.links_t.p2.filter(like="solid biomass for industry CC")
                    .filter(like=country)).sum() 
          collection.append(
                pd.Series(
                    dict(
                        label="solid biomass for industry CC",
                        source="co2 atmosphere",
                        target="BECCS",
                        value=value,
                    )
                )
            )
          if country == 'EU':
            value = -(
                    n.snapshot_weightings.generators
                    @ n.links_t.p3.filter(like="solid biomass for industry CC")
                    ).sum()
          else:
            value = -(
                    n.snapshot_weightings.generators
                    @ n.links_t.p3.filter(like="solid biomass for industry CC")
                    .filter(like=country)).sum()
          collection.append(
                pd.Series(
                    dict(
                        label="solid biomass for industry CC",
                        source="BECCS",
                        target="co2 stored",
                        value=value,
                    )
                )
            )

        # methanolisation
        if "p3" in n.links_t:
          if country == 'EU':
            value = (
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="methanolisation")
                    ).sum()
          else:
            value = (
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="methanolisation")
                    .filter(like=country)).sum()
          collection.append(
                pd.Series(
                    dict(
                        label="methanolisation", source="co2 stored", target="methanol", value=value
                        # C02 intensity of gas from cost data
                    )
                )
            )
        # gas for industry
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p2.filter(regex="gas for industry$")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(regex="gas for industry$")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="gas for industry", source="gas", target="co2 atmosphere", value=value
                )
            )
        )

        # gas for industry CC
        if "p3" in n.links_t:
          if country == 'EU':
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like="gas for industry CC")
                    ).sum()
          else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like="gas for industry CC")
                    .filter(like=country)).sum() 
          collection.append(
                pd.Series(
                    dict(
                        label="gas for industry CC1",
                        source="gas",
                        target="co2 atmosphere",
                        value=value,
                    )
                )
            )
          if country == 'EU':
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="gas for industry CC")
                    ).sum()
          else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="gas for industry CC")
                    .filter(like=country)).sum() 
          collection.append(
                pd.Series(
                    dict(
                        label="gas for industry CC2", source="gas", target="co2 stored", value=value
                    )
                )
            )
        # solid biomass to gas
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p1.filter(regex=r"solid biomass solid biomass to gas(-\d{4})?$")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p1.filter(regex=r"solid biomass solid biomass to gas(-\d{4})?$")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="solid biomass solid biomass to gas", source="solid biomass", target="gas",
                    value=value * float(options.loc[("gas", "CO2 intensity")]),
                    # CO2 stored in bioSNG from cost data
                )
            )
        )
        collection.append(
            pd.Series(dict(label="solid biomass solid biomass to gas1", source="co2 atmosphere", target="solid biomass",
                           value=value * float(options.loc[("gas", "CO2 intensity")]))))
        # solid biomass to gas CC
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p1.filter(like="solid biomass solid biomass to gas CC")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p1.filter(like="solid biomass solid biomass to gas CC")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="solid biomass solid biomass to gas CC1",
                    source="solid biomass",
                    target="gas",
                    value=value * float(options.loc[("gas", "CO2 intensity")]),
                )
            )
        )
        collection.append(
            pd.Series(
                dict(label="solid biomass solid biomass to gas CC2", source="co2 atmosphere", target="solid biomass",
                     value=value * float(options.loc[("gas", "CO2 intensity")])))
        )
        if country == 'EU':
            value = -(
                n.snapshot_weightings.generators @ n.links_t.p2.filter(like="solid biomass solid biomass to gas CC")
                ).sum()
        else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like="solid biomass solid biomass to gas CC")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="solid biomass solid biomass to gas CC3", source="solid biomass", target="co2 stored",
                    value=value
                )
            )
        )
        collection.append(
            pd.Series(
                dict(label="solid biomass solid biomass to gas CC4", source="co2 atmosphere", target="solid biomass",
                     value=value))
        )
        # biomass boilers
        # residential urban decentral biomass boiler
        tech = "urban decentral biomass boiler"
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like=tech)).sum()
        else:
            value = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like=tech).filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(label=tech, source="solid biomass", target="co2 atmosphere",
                     value=value * float(options.loc[("solid biomass", "CO2 intensity")]))
            )
        )
        collection.append(
            pd.Series(
                dict(label="urban decentral biomass boiler", source="co2 atmosphere",
                     target="solid biomass",
                     value=value * float(options.loc[("solid biomass", "CO2 intensity")])))
        )
        
        # residential rural biomass boiler
        tech = "rural biomass boiler"
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like=tech)).sum()
        else:
            value = (n.snapshot_weightings.generators @ n.links_t.p0.filter(like=tech).filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(label=tech, source="solid biomass", target="co2 atmosphere",
                     value=value * float(options.loc[("solid biomass", "CO2 intensity")]))
            )
        )
        collection.append(
            pd.Series(dict(label="rural biomass boiler", source="co2 atmosphere", target="solid biomass",
                           value=value * float(options.loc[("solid biomass", "CO2 intensity")])))
        )
        
        # Solid biomass to liquid
        if country == 'EU':
            value = (
                n.snapshot_weightings.generators @ n.links_t.p0.filter(like="solid biomass biomass to liquid")
                ).sum()
        else:
            value = (
                    n.snapshot_weightings.generators @ n.links_t.p0.filter(like="solid biomass biomass to liquid")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(
                    label="solid biomass biomass to liquid", source="solid biomass", target="oil",
                    value=value * float(options.loc[("BtL", "CO2 stored")])
                    # C02 stored in oil by BTL from costs data 2050
                )
            )
        )
        collection.append(
            pd.Series(dict(label="solid biomass biomass to liquid", source="co2 atmosphere", target="solid biomass",
                           value=value * float(options.loc[("BtL", "CO2 stored")])))
        )
        # # solid biomass liquid to  CC
        if "p3" in n.links_t:
          if country == 'EU':
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="solid biomass biomass to liquid CC")
                    ).sum()
          else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="solid biomass biomass to liquid CC")
                    .filter(like=country)).sum() 
          collection.append(
                pd.Series(
                    dict(
                        label="solid biomass biomass to liquid CC", source="solid biomass", target="co2 stored",
                        value=value
                    )
                )
            )
          collection.append(
                pd.Series(
                    dict(label="solid biomass biomass to liquid CC", source="co2 atmosphere", target="solid biomass",
                         value=value))
            )
        # Fischer-Tropsch
        if country == 'EU':
            value = (
                n.snapshot_weightings.generators @ n.links_t.p2.filter(like="Fischer-Tropsch")
                ).sum()
        else:
            value = (
                    n.snapshot_weightings.generators @ n.links_t.p2.filter(like="Fischer-Tropsch")
                    .filter(like=country)).sum()
        collection.append(
            pd.Series(
                dict(label="Fischer-Tropsch", source="co2 stored", target="oil", value=value)
            )
        )

        # urban central gas CHP
        if "p3" in n.links_t:
          if country == 'EU':
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="urban central gas CHP")
                    ).sum()
          else:
            value = -(
                    n.snapshot_weightings.generators @ n.links_t.p3.filter(like="urban central gas CHP")
                    .filter(like=country)).sum()
          collection.append(
                pd.Series(
                    dict(
                        label="urban central gas CHP",
                        source="gas",
                        target="co2 atmosphere",
                        value=value,
                    )
                )
            )

        # urban central gas CHP CC
        if "p4" in n.links_t:
            tech = "urban central gas CHP CC"
            if country == 'EU':
                value = -(n.snapshot_weightings.generators @ n.links_t.p3.filter(like=tech)).sum()
            else:
                value = -(n.snapshot_weightings.generators @ n.links_t.p3.filter(like=tech).filter(like=country)).sum()
            collection.append(
                pd.Series(dict(label="urban central gas CHP CC1", source="gas", target="co2 atmosphere", value=value))
            )
            if country == 'EU':
                value = -(n.snapshot_weightings.generators @ n.links_t.p4.filter(like=tech)).sum()
            else:
                value = -(n.snapshot_weightings.generators @ n.links_t.p4.filter(like=tech).filter(like=country)).sum()
            collection.append(
                pd.Series(dict(label="urban central gas CHP CC2", source="gas", target="co2 stored", value=value))
            )

        # urban solid biomass CHP
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.links_t.p0.filter(regex="urban central solid biomass CHP")).sum()
        else:
            value = (n.snapshot_weightings.generators @ n.links_t.p0.filter(regex="urban central solid biomass CHP").filter(
                like=country)).sum()
        collection.append(
            pd.Series(
                dict(label="urban central solid biomass CHP", source="solid biomass", target="co2 atmosphere",
                     value=value * float(options.loc[("solid biomass", "CO2 intensity")]))
            )
        )
        collection.append(
            pd.Series(
                dict(label="urban central solid biomass CHP", source="co2 atmosphere", target="solid biomass",
                     value=value * float(options.loc[("solid biomass", "CO2 intensity")]))
            )
        )

        if "p4" in n.links_t:
            # urban solid biomass CHP CC
            tech = "urban central solid biomass CHP CC"
            if country == 'EU':
                value = (n.snapshot_weightings.generators @ n.links_t.p3.filter(like=tech)).sum()
            else:
                value = (n.snapshot_weightings.generators @ n.links_t.p3.filter(like=tech).filter(like=country)).sum()
            collection.append(
                pd.Series(
                    dict(label=tech, source="co2 atmosphere", target="BECCS", value=value)
                )
            )
            if country == 'EU':
                value = -(n.snapshot_weightings.generators @ n.links_t.p4.filter(like=tech)).sum()
            else:
                value = -(n.snapshot_weightings.generators @ n.links_t.p4.filter(like=tech).filter(like=country)).sum()
            collection.append(
                pd.Series(
                    dict(label=tech, source="BECCS", target="co2 stored", value=value)
                )
            )

        # oil emissions
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(like="naphtha for industry")
                  ).sum()
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p2.filter(like=country).filter(like="naphtha for industry")
                      ).sum()
        collection.append(
            pd.Series(
                dict(label="naphtha for industry", source="oil", target="process emissions", value=value)
            )
        )

        # aviation oil emissions
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(like="kerosene for aviation")
                  ).sum()
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p2.filter(like=country).filter(like="kerosene for aviation")
                      ).sum()
        collection.append(
            pd.Series(
                dict(label="kerosene for aviation", source="oil", target="co2 atmosphere", value=value)
            )
        )
        
        #oil refining emissions
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(
                      like="oil refining")).sum()
        else:
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(like=country).filter(
                      like="oil refining")).sum()
        collection.append(
            pd.Series(
                dict(label="oil refining emissions", source="oil", target="co2 atmosphere", value=value)
            )
        )

        # agriculture machinery oil emissions
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(
                      like="agriculture machinery oil")
                  ).sum()
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p2.filter(like=country).filter(
                          like="agriculture machinery oil")
                      ).sum()
        collection.append(
            pd.Series(
                dict(
                    label="agriculture machinery oil emissions",
                    source="oil",
                    target="co2 atmosphere",
                    value=value,
                )
            )
        )
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(
                      like="land transport oil")
                  ).sum()
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p2.filter(like=country).filter(
                          like="land transport oil")
                      ).sum()
        collection.append(
            pd.Series(
                dict(
                    label="land transport oil emissions",
                    source="oil",
                    target="co2 atmosphere",
                    value=value,
                )
            )
        )
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(
                      like="shipping oil")
                  ).sum()
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p2.filter(like=country).filter(
                          like="shipping oil")
                      ).sum()
        collection.append(
            pd.Series(
                dict(
                    label="shipping oil emissions",
                    source="oil",
                    target="co2 atmosphere",
                    value=value,
                )
            )
        )
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p2.filter(
                      like="shipping methanol")
                  ).sum()
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p2.filter(like=country).filter(
                          like="shipping methanol")
                      ).sum()
        collection.append(
            pd.Series(
                dict(
                    label="shipping methanol emissions",
                    source="methanol",
                    target="co2 atmosphere",
                    value=value,
                )
            )
        )
        
        #efuels input
        if country == 'EU':
            value = (n.snapshot_weightings.generators @
                  n.links_t.p3.filter(
                      like="electrobiofuels")
                  ).sum().sum() #* options.loc[("electrobiofuels", "capture rate"), "value"] * options.loc[("Fischer-Tropsch", "capture rate"), "value"]
        else:
            value = (n.snapshot_weightings.generators @
                      n.links_t.p3.filter(like=country).filter(
                          like="electrobiofuels")
                      ).sum().sum() #* options.loc[("electrobiofuels", "capture rate"), "value"] * options.loc[("Fischer-Tropsch", "capture rate"), "value"]
        collection.append(
            pd.Series(
                dict(
                    label="Electrobiofuels",
                    source="solid biomass",
                    target="oil",
                    value=value,
                )
            )
        )
        collection.append(
            pd.Series(
                dict(
                    label="Electrobiofuels",
                    source="atm",
                    target="solid biomass",
                    value=value,
                )
            )
        )
        # collection.append(
        #     pd.Series(
        #         dict(
        #             label="Electrobiofuels",
        #             source="oil",
        #             target="atm",
        #             value=value,
        #         )
        #     )
        # )
        #Waste CHP
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p3.filter(
                      like="waste CHP")).sum().sum()          
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p3.filter(like=country).filter(
                          like="waste CHP")).sum().sum()
        collection.append(
            pd.Series(
                dict(
                    label="Waste CHP emissions",
                    source="Waste CHP",
                    target="atm",
                    value=value,
                )
            )
        )
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p4.filter(
                      like="waste CHP CC")).sum().sum()          
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p4.filter(like=country).filter(
                          like="waste CHP CC")).sum().sum()
        collection.append(
            pd.Series(
                dict(
                    label="Waste CHP CC",
                    source="Waste CHP",
                    target="co2 stored",
                    value=value,
                )
            )
        )
        
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p3.filter(
                      like="waste CHP CC")).sum().sum()          
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p3.filter(like=country).filter(
                          like="waste CHP CC")).sum().sum()
        collection.append(
            pd.Series(
                dict(
                    label="Waste CHP CC1",
                    source="Waste CHP",
                    target="atm",
                    value=value,
                )
            )
        )
        
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @
                  n.links_t.p1.filter(
                      like="HVC to air")).sum().sum()          
        else:
            value = -(n.snapshot_weightings.generators @
                      n.links_t.p1.filter(like=country).filter(
                          like="HVC to air")).sum().sum()
        collection.append(
            pd.Series(
                dict(
                    label="HVC to air",
                    source="oil",
                    target="atm",
                    value=value,
                )
            )
        )
        # biomass to methanol
        if country == 'EU':
            value = -(n.snapshot_weightings.generators @ n.links_t.p1.filter(like="biomass-to-methanol")).sum().sum() 
        else:
            value = -(n.snapshot_weightings.generators @ n.links_t.p1.filter(like="biomass-to-methanol").filter(
                like=country)).sum().sum() 
        collection.append(
            pd.Series(
                dict(label="biomass-to-methanol", source="solid biomass", target="methanol",
                     value=value * float(options.loc[("biomass-to-methanol", "CO2 stored")]))
            )
        )
        collection.append(
            pd.Series(
                dict(label="biomass-to-methanol1", source="co2 atmosphere", target="solid biomass",
                     value=value * float(options.loc[("biomass-to-methanol", "CO2 stored")]))
            )
        )
        
        cf = pd.concat(collection, axis=1).T
        cf.value /= 1e6  # Mt

        # fossil gas
        if country == 'EU':
            value_gas = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="gas")).div(
            1e6).sum() * float(options.loc[("gas", "CO2 intensity")])
            value = value_gas
        else:
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="gas").filter(like=country)).div(
                1e6).sum() * float(options.loc[("gas", "CO2 intensity")])    
        row = pd.DataFrame(
            [dict(label="fossil gas", source="fossil gas", target="gas", value=value)]
        )

        cf = pd.concat([cf, row], axis=0)

        # fossil oil
        # co2_intensity = 0.27  # t/MWh
        if country == 'EU':
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="oil")).div(
            1e6).sum() * float(options.loc[("oil", "CO2 intensity")])
        else:
            value = (n.snapshot_weightings.generators @ n.generators_t.p.filter(like="oil").filter(like=country)).div(
                1e6).sum() * float(options.loc[("oil", "CO2 intensity")])
        row = pd.DataFrame(
            [dict(label="fossil oil", source="fossil oil", target="oil", value=value)]
        )
        cf = pd.concat([cf, row], axis=0)

        #sequestration
        value = (
                cf.loc[cf.target == "co2 stored", "value"].sum()
                - cf.loc[cf.source == "co2 stored", "value"].sum()
        )
        row = pd.DataFrame(
            [
                dict(
                    label="co2 sequestration",
                    source="co2 stored",
                    target="co2 sequestration",
                    value=value,
                )
            ]
        )

        cf = pd.concat([cf, row], axis=0)
        
        # net co2 emissions
        value = (
                cf.loc[cf.target == "co2 atmosphere", "value"].sum()
                - cf.loc[cf.source == "co2 atmosphere", "value"].sum()
        )
        row = pd.DataFrame(
            [
                dict(
                    label="net co2 emissions",
                    source="co2 atmosphere",
                    target="net co2 emissions",
                    value=value,
                )
            ]
        )
        cf = pd.concat([cf, row], axis=0)
        cf = cf.loc[(cf.value >= 0.01)]

        cf.rename(columns={'value': str(planning_horizon)}, inplace=True)
        results_dict_co2[country] = cf

        return results_dict_co2


# %%
entries_to_select_c = ['process emissions', 'process emissions CC', 'CCGT', 'lignite',
                       'SMR CC', 'SMR CC_2',
                       'rural gas boiler','oil refining emissions',
                       'urban decentral gas boiler',
                       'urban central gas boiler',
                       'rural oil boiler',
                       'urban decentral oil boiler',
                       'solid biomass for industry', 'solid biomass for industry_2', 'methanolisation',
                       'gas for industry',
                       'urban decentral biomass boiler', 'urban decentral biomass boiler_2',
                       'rural biomass boiler','biomass-to-methanol','biomass-to-methanol1',
                       'rural biomass boiler_2',
                       'kerosene for aviation', 'agriculture machinery oil emissions', 'land transport oil emissions',
                       'shipping oil emissions','Waste CHP CC',
                       'shipping methanol emissions', 'LULUCF', 'fossil oil', 'net co2 emissions',
                       'gas for industry CC1','Waste CHP emissions','HVC to air',
                       'gas for industry CC2', 'solid biomass for industry CC',
                       'solid biomass for industry CC_2','solid biomass biomass to liquid CC_2',
                       'urban central solid biomass CHP', 'urban central solid biomass CHP_2', 'coal',
                       'solid biomass biomass to liquid','solid biomass biomass to liquid CC',
                       'solid biomass biomass to liquid_2', 'biogas to gas', 'biogas to gas_2', 'urban central gas CHP',
                       'Fischer-Tropsch','solid biomass solid biomass to gas1',
                       'OCGT', 'SMR', 'urban central solid biomass CHP CC', 'urban central solid biomass CHP CC_2',
                       'DAC', 'co2 sequestration','Electrobiofuels','Electrobiofuels_2',
                       'solid biomass solid biomass to gas', 'solid biomass solid biomass to gas CC1',
                       'solid biomass solid biomass to gas CC2','Waste CHP CC1',
                       'solid biomass solid biomass to gas CC3', 'solid biomass solid biomass to gas CC4', 'Sabatier',
                       'urban central gas CHP CC1','urban central gas CHP CC2','biogas to gas CC','biogas to gas CC2',
                       'biogas to gas CC3','naphtha for industry','fossil gas','Non-energy GHG Agriculture']  # Add moe entries if needed

entry_label_mapping_c = {
    'process emissions': {'label': 'process emissions', 'source': 'MtCO2', 'target': 'emmprocess'},
    'process emissions CC': {'label': 'process emissions CC', 'source': 'MtCO2', 'target': 'emmprocessccst'},
    # 'process emissions CC_2': {'label': 'process emissions CC', 'source': 'MtCO2', 'target': 'emmprocessccst'},
    'CCGT': {'label': 'CCGT emissions', 'source': 'MtCO2', 'target': 'emmccgt'},
    'OCGT': {'label': 'OCGT emissions', 'source': 'MtCO2', 'target': 'emmocgt'},
    'lignite': {'label': 'lignite emissions', 'source': 'MtCO2', 'target': 'emmlig'},
    'SMR CC': {'label': 'SMR emissions', 'source': 'MtCO2', 'target': 'emmsmr'},
    'SMR CC_2': {'label': 'SMR emissions captored', 'source': 'MtCO2', 'target': 'emmsmrcc'},
    'rural gas boiler': {'label': 'rural gas boiler', 'source': 'MtCO2', 'target': 'emmresbo'},
    'urban decentral gas boiler': {'label': 'urban decentral gas boiler', 'source': 'MtCO2', 'target': 'emmresubbo'},
    'urban central gas boiler': {'label': 'urban central gas boiler', 'source': 'MtCO2', 'target': 'emmurbbbo'},
    'rural oil boiler': {'label': 'rural oil boiler', 'source': 'MtCO2', 'target': 'emmresoil'},
    'urban decentral oil boiler': {'label': 'urban decentral oil boiler', 'source': 'MtCO2', 'target': 'emmresuoil'},
    'solid biomass for industry': {'label': 'solid biomass for industry', 'source': 'MtCO2', 'target': 'emmindbm'},
    'solid biomass for industry_2': {'label': 'solid biomass for industry', 'source': 'MtCO2', 'target': 'emmindbmatm'},
    'methanolisation': {'label': 'methanolisation', 'source': 'MtCO2', 'target': 'emmmet'},
    'gas for industry': {'label': 'gas for industry', 'source': 'MtCO2', 'target': 'emmgas'},
    'urban decentral biomass boiler': {'label': 'urban decentral biomass boiler',
                                                   'source': 'MtCO2', 'target': 'emmresbm'},
    'urban decentral biomass boiler_2': {'label': 'residential urban decentral biomass boiler',
                                                     'source': 'MtCO2', 'target': 'emmresbmatm'},
    'rural biomass boiler': {'label': 'rural biomass boiler', 'source': 'MtCO2', 'target': 'emmresbmm'},
    'rural biomass boiler_2': {'label': 'rural biomass boiler', 'source': 'MtCO2', 'target': 'emmresbmmatm'},
    'kerosene for aviation': {'label': 'kerosene for aviation', 'source': 'MtCO2', 'target': 'emmavi'},
    'agriculture machinery oil emissions': {'label': 'agriculture machinery oil emissions', 'source': 'MtCO2',
                                            'target': 'emmoilagr'},
    'land transport oil emissions': {'label': 'land transport oil emissions', 'source': 'MtCO2', 'target': 'emmoiltra'},
    'naphtha for industry': {'label': 'naphtha for industry', 'source': 'MtCO2', 'target': 'emmoil'},
    'oil refining emissions': {'label': 'oil refining emissions', 'source': 'MtCO2', 'target': 'emmoilref'},
    'shipping oil emissions': {'label': 'shipping oil emissions', 'source': 'MtCO2', 'target': 'emmoilwati'},
    'shipping methanol emissions': {'label': 'shipping methanol emissions', 'source': 'MtCO2', 'target': 'emmmetwati'},
    'LULUCF': {'label': 'LULUCF', 'source': 'MtCO2', 'target': 'emmluf'},
    'fossil gas': {'label': 'fossil gas', 'source': 'MtCO2', 'target': 'emmfossgas'},
    'fossil oil': {'label': 'fossil oil', 'source': 'MtCO2', 'target': 'emmfossoil'},
    'net co2 emissions': {'label': 'net co2 emissions', 'source': 'MtCO2', 'target': 'emmnet'},
    'gas for industry CC1': {'label': 'gas for industry CC', 'source': 'MtCO2', 'target': 'emmgascc'},
    'gas for industry CC2': {'label': 'gas for industry CC', 'source': 'MtCO2', 'target': 'emmgasccx'},
    'solid biomass for industry CC': {'label': 'solid biomass for industry CC', 'source': 'MtCO2',
                                      'target': 'emmindatmbm'},
    'solid biomass for industry CC_2': {'label': 'solid biomass for industry CC', 'source': 'MtCO2',
                                        'target': 'emmindbeccs'},
    'urban central solid biomass CHP': {'label': 'urban central solid biomass CHP', 'source': 'MtCO2',
                                        'target': 'emmbmchp'},
    'urban central solid biomass CHP_2': {'label': 'urban central solid biomass CHP', 'source': 'MtCO2',
                                          'target': 'emmbmchpatm'},
    'coal': {'label': 'coal emissions', 'source': 'MtCO2', 'target': 'emmcoal'},
    'solid biomass biomass to liquid': {'label': 'solid biomass to liquid', 'source': 'MtCO2', 'target': 'emmbmliq'},
    'solid biomass biomass to liquid_2': {'label': 'solid biomass to liquid', 'source': 'MtCO2',
                                          'target': 'emmbmliqat'},
    'solid biomass biomass to liquid CC': {'label': 'solid biomass to liquid CC', 'source': 'MtCO2', 'target': 'emmbmliqcc'},
    'solid biomass biomass to liquid CC_2': {'label': 'solid biomass to liquid CC', 'source': 'MtCO2',
                                          'target': 'emmbmliqatcc'},
    'biogas to gas': {'label': 'biogas to gas', 'source': 'MtCO2', 'target': 'emmbiogasat'},
    'biogas to gas_2': {'label': 'biogas to gas', 'source': 'MtCO2', 'target': 'emmbiogas'},
    'urban central gas CHP': {'label': 'urban central gas CHP', 'source': 'MtCO2', 'target': 'emmgaschp'},
    'Fischer-Tropsch': {'label': 'Fischer-Tropsch', 'source': 'MtCO2', 'target': 'emmfischer'},
    'SMR': {'label': 'SMR emissions', 'source': 'MtCO2', 'target': 'emmsmrsm'},
    'urban central solid biomass CHP CC': {'label': 'urban central solid biomass CHP', 'source': 'MtCO2',
                                           'target': 'emmbmchpcc'},
    'urban central solid biomass CHP CC_2': {'label': 'urban central solid biomass CHP', 'source': 'MtCO2',
                                             'target': 'emmbmchpatmcc'},
    'urban central gas CHP CC1': {'label': 'urban central gas CHP CC', 'source': 'MtCO2',
                                           'target': 'emmgaschpcc'},
    'urban central gas CHP CC2': {'label': 'urban central gas CHP CC', 'source': 'MtCO2',
                                             'target': 'emmgaschpatmcc'},
    'DAC': {'label': 'DAC', 'source': 'MtCO2', 'target': 'emmdac'},
    'co2 sequestration': {'label': 'co2 sequestration', 'source': 'MtCO2', 'target': 'emmseq'},
    'solid biomass solid biomass to gas': {'label': 'solid biomass solid biomass to gas', 'source': 'MtCO2',
                                           'target': 'emmbmsng'},
    'solid biomass solid biomass to gas1': {'label': 'solid biomass solid biomass to gas', 'source': 'MtCO2',
                                           'target': 'emmbmsngatm'},
    'solid biomass solid biomass to gas CC1': {'label': 'solid biomass solid biomass to gas CC', 'source': 'MtCO2',
                                               'target': 'emmbmsngcc'},
    'solid biomass solid biomass to gas CC2': {'label': 'solid biomass solid biomass to gas CC', 'source': 'MtCO2',
                                               'target': 'emmbmsngccc'},
    'solid biomass solid biomass to gas CC3': {'label': 'solid biomass solid biomass to gas CC', 'source': 'MtCO2',
                                               'target': 'emmbmsngccca'},
    'solid biomass solid biomass to gas CC4': {'label': 'solid biomass solid biomass to gas CC', 'source': 'MtCO2',
                                               'target': 'emmbmsngcccb'},
    'Sabatier': {'label': 'Sabatier"', 'source': 'MtCO2', 'target': 'emmsaba'},
    'biogas to gas CC': {'label': 'biogas to gas CC', 'source': 'MtCO2', 'target': 'emmbiogasatcc'},
    'biogas to gas CC2': {'label': 'biogas to gas CC2', 'source': 'MtCO2', 'target': 'emmbiogascc'},
    'biogas to gas CC3': {'label': 'biogas to gas CC', 'source': 'MtCO2', 'target': 'emmbiogasccc'},
    'Non-energy GHG Agriculture': {'label': 'Non-energy GHG Agriculture', 'source': 'MtCO2', 'target': 'emmghgagri'},
    'Electrobiofuels': {'label': 'Electrobiofuels', 'source': 'MtCO2', 'target': 'emmefuel'},
    # 'Electrobiofuels_2': {'label': 'Electrobiofuels', 'source': 'MtCO2', 'target': 'emmefuelatm'},
    'Electrobiofuels_2': {'label': 'Electrobiofuels emissions', 'source': 'MtCO2', 'target': 'emmefuelemm'},
    'Waste CHP emissions': {'label': 'Waste CHP emissions', 'source': 'MtCO2', 'target': 'emmewasteatm'},
    'Waste CHP CC': {'label': 'Waste CHP CC', 'source': 'MtCO2', 'target': 'emmewastestm'},
    'Waste CHP CC1': {'label': 'Waste CHP CC1', 'source': 'MtCO2', 'target': 'emmewastestmatm'},
    'HVC to air': {'label': 'HVC to air', 'source': 'MtCO2', 'target': 'emmehvcatm'},
    'biomass-to-methanol': {'label': 'biomass-to-methanol', 'source': 'MtCO2', 'target': 'emmbmmet'},
    'biomass-to-methanol1': {'label': 'biomass-to-methanol', 'source': 'MtCO2', 'target': 'emmbmmetatm'},
}
def write_to_excel(cluster, opt, sector_opt, planning_horizons,countries, filename):
    '''
    Function that writes the simulation results to the SEPIA excel input file
    :param filename_template: Template for the excel file name with a placeholder for the country
    '''
    for country in countries:
        # Generate the filename for the current country

        merged_df = process_network(cluster, opt, sector_opt, planning_horizons[0], country)
        merged_df = merged_df[country]
        for planning_horizon in planning_horizons[1:]:
            temp = process_network(cluster, opt, sector_opt, planning_horizon, country)
            temp = temp[country]
            merged_df = pd.merge(merged_df, temp, on=['label', 'source', 'target'], how='outer')

        # Fill missing values with 0
        merged_df = merged_df.fillna(0)
        connections = merged_df

        df = connections
        selected_entries_df = pd.DataFrame()
        filename=snakemake.output.excelfile[countries.index(country)]
        country_filename = str(filename)

        with pd.ExcelWriter(country_filename, engine='openpyxl') as writer:
            for entry in entries_to_select:
                selected_df = df[df['label'] == entry].copy()  # Create a copy of the DataFrame

                # Get the label mapping for the current entry
                label_mapping = entry_label_mapping.get(entry, {})

                # Replace the values in the selected DataFrame based on the mapping
                selected_df.loc[:, 'label'] = label_mapping.get('label', '')
                selected_df.loc[:, 'source'] = label_mapping.get('source', '')
                selected_df.loc[:, 'target'] = label_mapping.get('target', '')
                # Concatenate the selected entry to the main DataFrame
                selected_entries_df = pd.concat([selected_entries_df, selected_df])

            selected_entries_df = selected_entries_df.groupby('target').agg({'label': 'first', 'source': 'first',
                                                                             '2025': 'sum',
                                                                             '2030': 'sum',
                                                                             '2035': 'sum',
                                                                             '2040': 'sum',
                                                                             '2045': 'sum',
                                                                             '2050': 'sum'
                                                                             }).reset_index()
            selected_entries_df = selected_entries_df[['label', 'source', 'target','2025', '2030','2035', '2040','2045', '2050']]
            total_df = selected_entries_df
            for year in ['2025', '2030','2035', '2040','2045', '2050']:
              if year in total_df.columns:
                total_df[year] = total_df[year].fillna(0)
            total_df = total_df.groupby(['label', 'source', 'target'], as_index=False)[['2025', '2030','2035', '2040','2045', '2050']].sum()
            # Write the concatenated DataFrame to a new sheet
            total_df.to_excel(writer, sheet_name='Inputs', index=False)

        print(f'Excel file "{filename}" created with the selected entries on the "SelectedEntries" sheet.')

        list_as_set = set(entries_to_select)
        df_as_set = set(map(str, connections.label))
        # Find the different elements
        different_elements = list_as_set.symmetric_difference(df_as_set)
        # Convert the result back to a list
        different_elements_list = list(different_elements)
        print("Different elements between the list and DataFrame:", different_elements_list)

        # Deal with the emissions:
    for country in countries:
        merged_emissions = prepare_emissions(cluster, opt, sector_opt, planning_horizons[0], country)
        merged_emissions = merged_emissions[country]
        for planning_horizon in planning_horizons[1:]:
            temp = prepare_emissions(cluster, opt, sector_opt, planning_horizon, country)
            temp = temp[country]
            merged_emissions = pd.merge(merged_emissions, temp, on=['label', 'source', 'target'], how='outer')

        # Fill missing values with 0
        merged_emissions.fillna(0, inplace=True)

        suffix_counter = {}

        def generate_new_label(label):
            if label in suffix_counter:
                suffix_counter[label] += 1
            else:
                suffix_counter[label] = 1

            if suffix_counter[label] > 1:
                return f"{label}_{suffix_counter[label]}"
            return label

        merged_emissions['label'] =  merged_emissions['label'].apply(generate_new_label)
        filename=snakemake.output.excelfile[countries.index(country)]
        country_filename = str(filename)

        selected_entries_cf = pd.DataFrame()
        with pd.ExcelWriter(country_filename, engine='openpyxl', mode='a',
                            if_sheet_exists='overlay') as writer:  # Use 'a' to append to the existing file
            for entry in entries_to_select_c:
                selected_cf = merged_emissions[
                    merged_emissions['label'] == entry].copy()  # Create a copy of the DataFrame

                # Get the label mapping for the current entry
                label_mapping_c = entry_label_mapping_c.get(entry, {})

                # Replace the values in the selected DataFrame based on the mapping
                selected_cf.loc[:, 'label'] = label_mapping_c.get('label', '')
                selected_cf.loc[:, 'source'] = label_mapping_c.get('source', '')
                selected_cf.loc[:, 'target'] = label_mapping_c.get('target', '')
                # Concatenate the selected entry to the main DataFrame
                selected_entries_cf = pd.concat([selected_entries_cf, selected_cf])

            selected_entries_cf = selected_entries_cf.groupby('target').agg({'label': 'first', 'source': 'first',
                                                                             '2025': 'sum',
                                                                             '2030': 'sum',
                                                                             '2035': 'sum',
                                                                             '2040': 'sum',
                                                                             '2045': 'sum',
                                                                             '2050': 'sum'
                                                                             }).reset_index()
            selected_entries_cf = selected_entries_cf[['label', 'source', 'target','2025', '2030','2035', '2040','2045', '2050']]
            total_cf = selected_entries_cf
            for year in ['2025', '2030','2035', '2040','2045', '2050']:
              if year in total_cf.columns:
                total_cf[year] = total_cf[year].fillna(0)
            total_cf = total_cf.groupby(['label', 'source', 'target'], as_index=False)[['2025', '2030','2035', '2040','2045', '2050']].sum()
            total_cf.to_excel(writer, sheet_name='Inputs_co2', index=False)

        print(f'Excel file "{filename}" updated with the emissions data.')

        list_as_set = set(entries_to_select_c)
        cf_as_set = set(map(str, merged_emissions.label))
        # Find the different elements
        different_elements_c = list_as_set.symmetric_difference(cf_as_set)
        # Convert the result back to a list
        different_elements_list_c = list(different_elements_c)
        print("Different elements between the list and the Emissions dataFrame:", different_elements_list_c)



if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake(
            "prepare_sepia")

        

    cluster = snakemake.params.scenario["clusters"][0]
    opt = snakemake.params.scenario["opts"][0]
    sector_opt = snakemake.params.scenario["sector_opts"][0]
    planning_horizons = [2025, 2030, 2035, 2040, 2045, 2050] 

    countries = snakemake.params.countries
    total_country = 'EU'
    include_total_country = True
    if include_total_country == True:
         countries.append(total_country)
    else:
         countries = countries
    study = snakemake.params.study
    year = snakemake.params.year
    loaded_files = load_files(study, planning_horizons, cluster, opt, sector_opt)
    
    networks_dict = {
            (cluster, opt + sector_opt, planning_horizon): f"results/{study}" +
            f"/networks/base_s_{cluster}_{opt}_{sector_opt}_{planning_horizon}.nc"
            for planning_horizon in planning_horizons
        }

        
    write_to_excel(
            snakemake.params.scenario["clusters"][0],
            snakemake.params.scenario["opts"][0],
            snakemake.params.scenario["sector_opts"][0],
            snakemake.params.planning_horizons,
            countries,
            filename=snakemake.output.excelfile,
        )
