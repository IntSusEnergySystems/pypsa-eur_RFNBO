# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

if config["foresight"] != "perfect":

    rule plot_base_network:
        input:
            network=resources("networks/base.nc"),
            onshore_regions=resources("onshore_regions_base.geojson"),
        output:
            map=resources("maps/base_network.pdf"),
        benchmark:
            benchmarks("plot_base_network")
        threads: 1
        resources:
            mem_mb=4000,
        params:
            plotting=config_provider("plotting"),
        message:
            "Plotting base power network"
        script:
            scripts("plot_base_network.py")

    rule plot_clustered_network:
        input:
            network=resources("networks/clustered.nc"),
            regions=resources("onshore_regions.geojson"),
        output:
            map=resources("maps/clustered_network.pdf"),
        benchmark:
            benchmarks("plot_clustered_network")
        threads: 1
        resources:
            mem_mb=4000,
        params:
            plotting=config_provider("plotting"),
        message:
            "Plotting clustered network clusters"
        script:
            scripts("plot_power_network_clustered.py")

    rule plot_power_network:
        input:
            network=RESULTS + "networks/solved_{horizon}.nc",
            regions=resources("onshore_regions.geojson"),
        output:
            map=RESULTS + "maps/static/power_network_{horizon}.pdf",
        log:
            RESULTS + "logs/plot_power_network_{horizon}.log",
        benchmark:
            (RESULTS + "benchmarks/plot_power_network_{horizon}")
        threads: 2
        resources:
            mem_mb=10000,
        params:
            plotting=config_provider("plotting"),
            transmission_limit=config_provider("electricity", "transmission_limit"),
        message:
            "Plotting power network for {wildcards.horizon} planning horizon"
        script:
            scripts("plot_power_network.py")

    rule plot_hydrogen_network:
        input:
            network=RESULTS + "networks/solved_{horizon}.nc",
            regions=resources("onshore_regions.geojson"),
        output:
            map=RESULTS + "maps/static/h2_network_{horizon}.pdf",
        log:
            RESULTS + "logs/plot_hydrogen_network_{horizon}.log",
        benchmark:
            (RESULTS + "benchmarks/plot_hydrogen_network_{horizon}")
        threads: 2
        resources:
            mem_mb=10000,
        params:
            plotting=config_provider("plotting"),
            foresight=config_provider("foresight"),
        message:
            "Plotting hydrogen network for {wildcards.horizon} planning horizon"
        script:
            scripts("plot_hydrogen_network.py")

    rule plot_gas_network:
        input:
            network=RESULTS + "networks/solved_{horizon}.nc",
            regions=resources("onshore_regions.geojson"),
        output:
            map=RESULTS + "maps/static/ch4_network_{horizon}.pdf",
        log:
            RESULTS + "logs/plot_gas_network_{horizon}.log",
        benchmark:
            (RESULTS + "benchmarks/plot_gas_network_{horizon}")
        threads: 2
        resources:
            mem_mb=10000,
        params:
            plotting=config_provider("plotting"),
        message:
            "Plotting methane network for {wildcards.horizon} planning horizon"
        script:
            scripts("plot_gas_network.py")

    rule plot_balance_map:
        input:
            network=RESULTS + "networks/solved_{horizon}.nc",
            regions=resources("onshore_regions.geojson"),
        output:
            RESULTS + "maps/static/balance_map_{carrier}_{horizon}.pdf",
        log:
            RESULTS + "logs/plot_balance_map_{horizon}_{carrier}.log",
        benchmark:
            (RESULTS + "benchmarks/plot_balance_map_{horizon}_{carrier}")
        threads: 1
        resources:
            mem_mb=8000,
        params:
            plotting=config_provider("plotting"),
            settings=lambda w: config_provider("plotting", "balance_map", w.carrier),
        message:
            "Plotting balance map for {wildcards.horizon} planning horizon and {wildcards.carrier} carrier"
        script:
            scripts("plot_balance_map.py")

    rule plot_balance_map_interactive:
        input:
            network=RESULTS + "networks/solved_{horizon}.nc",
            regions=resources("onshore_regions.geojson"),
        output:
            RESULTS + "maps/interactive/balance_map_{carrier}_{horizon}.html",
        log:
            RESULTS + "logs/plot_balance_map_interactive/{horizon}_{carrier}.log",
        benchmark:
            RESULTS + "benchmarks/plot_balance_map_interactive/{horizon}_{carrier}"
        threads: 1
        resources:
            mem_mb=8000,
        params:
            settings=lambda w: config_provider(
                "plotting", "balance_map_interactive", w.carrier
            ),
        script:
            scripts("plot_balance_map_interactive.py")

    rule plot_heat_source_map:
        input:
            regions=resources("onshore_regions.geojson"),
            heat_source_temperature=lambda w: (
                resources("temp_" + w.carrier + "_temporal_aggregate.nc")
                if w.carrier in ["river_water", "sea_water", "ambient_air"]
                else []
            ),
            heat_source_energy=lambda w: (
                resources("heat_source_energy_" + w.carrier + "_temporal_aggregate.nc")
                if w.carrier in ["river_water"]
                else []
            ),
        output:
            temp_map=RESULTS
            + "maps/static/heat_source_temperature_map_{carrier}_{horizon}.html",
            energy_map=RESULTS
            + "maps/static/heat_source_energy_map_{carrier}_{horizon}.html",
        log:
            RESULTS + "logs/plot_heat_source_map/{carrier}_{horizon}.log",
        benchmark:
            (RESULTS + "benchmarks/plot_heat_source_map/{carrier}_{horizon}")
        threads: 1
        resources:
            mem_mb=150000,
        script:
            scripts("plot_heat_source_map.py")


rule make_summary:
    input:
        networks=lambda w: (
            [RESULTS + f"networks/solved_{config['planning_horizons'][-1]}.nc"]
            if config["foresight"] == "perfect"
            else [
                RESULTS + f"networks/solved_{h}.nc"
                for h in config["planning_horizons"]
            ]
        ),
    output:
        nodal_costs=RESULTS + "csvs/nodal_costs.csv",
        nodal_capacities=RESULTS + "csvs/nodal_capacities.csv",
        nodal_capacity_factors=RESULTS + "csvs/nodal_capacity_factors.csv",
        capacity_factors=RESULTS + "csvs/capacity_factors.csv",
        costs=RESULTS + "csvs/costs.csv",
        capacities=RESULTS + "csvs/capacities.csv",
        curtailment=RESULTS + "csvs/curtailment.csv",
        energy=RESULTS + "csvs/energy.csv",
        energy_balance=RESULTS + "csvs/energy_balance.csv",
        nodal_energy_balance=RESULTS + "csvs/nodal_energy_balance.csv",
        prices=RESULTS + "csvs/prices.csv",
        weighted_prices=RESULTS + "csvs/weighted_prices.csv",
        market_values=RESULTS + "csvs/market_values.csv",
        metrics=RESULTS + "csvs/metrics.csv",
        cumulative_costs=RESULTS + "csvs/cumulative_costs.csv",
    log:
        RESULTS + "logs/make_summary.log",
    benchmark:
        RESULTS + "benchmarks/make_summary"
    threads: 1
    resources:
        mem_mb=16000,
    params:
        foresight=config_provider("foresight"),
        planning_horizons=config_provider("planning_horizons"),
    message:
        "Creating optimization results summary statistics"
    script:
        scripts("make_summary.py")


rule plot_summary:
    input:
        costs=RESULTS + "csvs/costs.csv",
        energy=RESULTS + "csvs/energy.csv",
        balances=RESULTS + "csvs/energy_balance.csv",
        eurostat=resources("eurostat_energy_balances.csv"),
        co2=rules.retrieve_ghg_emissions.output["csv"],
    output:
        costs=RESULTS + "graphs/costs.pdf",
        energy=RESULTS + "graphs/energy.pdf",
        balances=RESULTS + "graphs/balances_energy.pdf",
    log:
        RESULTS + "logs/plot_summary.log",
    threads: 2
    resources:
        mem_mb=10000,
    params:
        countries=config_provider("countries"),
        planning_horizons=config_provider("planning_horizons"),
        emissions_scope=config_provider("co2_budget", "emissions_scope"),
        plotting=config_provider("plotting"),
        foresight=config_provider("foresight"),
        sector=config_provider("sector"),
        RDIR=RDIR,
    message:
        "Plotting summary statistics and results"
    script:
        scripts("plot_summary.py")


rule plot_balance_timeseries:
    input:
        network=RESULTS + "networks/solved_{horizon}.nc",
        rc="matplotlibrc",
    output:
        directory(RESULTS + "graphs/balance_timeseries_{horizon}"),
    log:
        RESULTS + "logs/plot_balance_timeseries_{horizon}.log",
    benchmark:
        RESULTS + "benchmarks/plot_balance_timeseries_{horizon}"
    threads: 16
    resources:
        mem_mb=10000,
    params:
        plotting=config_provider("plotting"),
        snapshots=config_provider("snapshots"),
        drop_leap_day=config_provider("enable", "drop_leap_day"),
    message:
        "Plotting energy balance time series for {wildcards.horizon} planning horizon"
    script:
        scripts("plot_balance_timeseries.py")


rule plot_heatmap_timeseries:
    input:
        network=RESULTS + "networks/solved_{horizon}.nc",
        rc="matplotlibrc",
    output:
        directory(RESULTS + "graphs/heatmap_timeseries_{horizon}"),
    log:
        RESULTS + "logs/plot_heatmap_timeseries_{horizon}.log",
    benchmark:
        RESULTS + "benchmarks/plot_heatmap_timeseries_{horizon}"
    threads: 16
    resources:
        mem_mb=10000,
    params:
        plotting=config_provider("plotting"),
        snapshots=config_provider("snapshots"),
        drop_leap_day=config_provider("enable", "drop_leap_day"),
    message:
        "Plotting heatmap time series visualization for {wildcards.horizon} planning horizon"
    script:
        scripts("plot_heatmap_timeseries.py")


STATISTICS_BARPLOTS = [
    "capacity_factor",
    "installed_capacity",
    "optimal_capacity",
    "capital_expenditure",
    "operational_expenditure",
    "curtailment",
    "supply",
    "withdrawal",
    "market_value",
]


rule plot_base_statistics:
    input:
        network=RESULTS + "networks/solved_{horizon}.nc",
    output:
        **{
            f"{plot}_bar": RESULTS + f"figures/statistics_{plot}_bar_{{horizon}}.pdf"
            for plot in STATISTICS_BARPLOTS
        },
        barplots_touch=RESULTS + "figures/.statistics_plots_{horizon}",
    params:
        plotting=config_provider("plotting"),
        barplots=STATISTICS_BARPLOTS,
    message:
        "Plotting base scenario statistics"
    script:
        scripts("plot_statistics.py")


rule build_ambient_air_temperature_yearly_average:
    input:
        cutout=lambda w: input_cutout(w),
        onshore_regions=resources("onshore_regions.geojson"),
    output:
        average_ambient_air_temperature=resources(
            "temp_ambient_air_temporal_aggregate.nc"
        ),
    log:
        logs("logs/build_ambient_air_temperature_yearly_average.log"),
    benchmark:
        benchmarks("build_ambient_air_temperature_yearly_average")
    threads: 1
    resources:
        mem_mb=5000,
    script:
        scripts("build_ambient_air_temperature_yearly_average.py")


rule plot_cop_profiles:
    input:
        cop_profiles=resources("cop_profiles_{horizon}.nc"),
    output:
        html=RESULTS + "graphs/cop_profiles_{horizon}.html",
    log:
        RESULTS + "logs/plot_cop_profiles_{horizon}.log",
    benchmark:
        RESULTS + "benchmarks/plot_cop_profiles/{horizon}"
    resources:
        mem_mb=10000,
    script:
        scripts("plot_cop_profiles/plot_cop_profiles.py")


rule plot_interactive_bus_balance:
    input:
        network=RESULTS + "networks/solved_{horizon}.nc",
        rc="matplotlibrc",
    output:
        directory=directory(RESULTS + "graphics/interactive_bus_balance/{horizon}"),
    log:
        RESULTS + "logs/plot_interactive_bus_balance/{horizon}.log",
    benchmark:
        RESULTS + "benchmarks/plot_interactive_bus_balance/{horizon}"
    resources:
        mem_mb=20000,
    params:
        plotting=config_provider("plotting"),
        snapshots=config_provider("snapshots"),
        drop_leap_day=config_provider("enable", "drop_leap_day"),
        bus_name_pattern=config_provider(
            "plotting", "interactive_bus_balance", "bus_name_pattern"
        ),
    script:
        scripts("plot_interactive_bus_balance.py")
        
        
local_countries = config["countries"].copy()
if "EU" not in local_countries:
    local_countries.append("EU")


def multi_scenario_enabled():
    return config["run"].get("scenarios", {}).get("enable", False)


def results_expand(template, **kwargs):
    if multi_scenario_enabled():
        template = template.replace("{run}", "{{run}}")
    return expand(template, **kwargs)


rule make_rfnbo_metrics:
    input:
        networks=results_expand(
            RESULTS
            + "networks/solved_{horizon}.nc",
            horizon=config["planning_horizons"],
        ),
    output:
        rfnbo_metrics=RESULTS + "csvs/rfnbo_metrics.csv",
    log:
        RESULTS + "logs/make_rfnbo_metrics.log",
    benchmark:
        RESULTS + "benchmarks/make_rfnbo_metrics"
    threads: 1
    resources:
        mem_mb=8000,
    params:
        planning_horizons=config_provider("planning_horizons"),
    message:
        "Creating RFNBO policy-resolution metrics across planning horizons"
    script:
        scripts("make_rfnbo_metrics.py")


def sepia_study_expand_kw():
    if multi_scenario_enabled():
        return {}
    return {"study": config["run"]["name"]}


def sepia_html_suffix(suffix):
    if multi_scenario_enabled():
        return RESULTS + f"htmls/{{country}}_{suffix}_{{run}}.html"
    return RESULTS + f"htmls/{{country}}_{suffix}_{{study}}.html"


def sepia_results_html_path():
    if multi_scenario_enabled():
        return RESULTS + "htmls/{country}_{section}_{run}.html"
    return RESULTS + "htmls/{country}_{section}_{study}.html"


def dashboard_html_sections():
    return ["demands", "costs", "capacities", "dispatch_plots", "maps", "emissions", "sankeys", "fec"]


def dashboard_html_outputs():
    if multi_scenario_enabled():
        return expand(
            "results/{run}/htmls/{country}_{section}_{run}.html",
            run=config["run"]["name"],
            country=local_countries,
            section=dashboard_html_sections(),
        )
    study = config["run"]["name"]
    if isinstance(study, list):
        study = study[0]
    return expand(
        f"results/{study}/htmls/{{country}}_{{section}}_{study}.html",
        country=local_countries,
        section=dashboard_html_sections(),
    )


ALL_DASHBOARD_HTML = dashboard_html_outputs()


def sepia_costs_input(w):
    return per_run_resource(w, "costs_2050_processed.csv")


def sepia_biomass_potentials_input(w):
    horizons = config["planning_horizons"]
    if multi_scenario_enabled():
        return [f"resources/{w.run}/biomass_potentials_{h}.csv" for h in horizons]
    return [resources(f"biomass_potentials_{h}.csv") for h in horizons]


rule prepare_sepia:
    params:
        countries=config_provider("countries"),
        planning_horizons=config_provider("planning_horizons"),
        sector_opts=config_provider("scenario", "sector_opts"),
        emissions_scope=config_provider("co2_budget", "emissions_scope"),
        eurostat_report_year=config_provider("energy", "eurostat_report_year"),
        plotting=config_provider("plotting"),
        scenario=config_provider("scenario"),
        study=study_name,
        year = config_provider("energy", "energy_totals_year"),
    input:
        networks=results_expand(
            RESULTS
            + "networks/solved_{horizon}.nc",
            horizon=config["planning_horizons"],
        ),
        costs=sepia_costs_input,
        nodal_capacity_factors=RESULTS + "csvs/nodal_capacity_factors.csv",
    output:
        excelfile=results_expand(
            RESULTS + "sepia/inputs{country}.xlsx",
            country=local_countries,
        ),
    threads: 1
    resources:
        mem_mb=10000,
    log:
        RESULTS + "logs/prepare_sepia.log",
    benchmark:
        RESULTS + "benchmarks/prepare_sepia",
    conda:
        "../envs/environment.yaml"
    script:
        "../SEPIA/excel_generator.py"
        
rule generate_sepia:
    params:
        countries=config_provider("countries"),
        year = config_provider("energy", "energy_totals_year"),
        study=study_name,
        planning_horizons=config_provider("planning_horizons"),
        cluster=config_provider("scenario","clusters"),
    input:
        countries = "SEPIA/COUNTRIES.xlsx",
        costs=sepia_costs_input,
        sepia_config = "SEPIA/SEPIA_config.xlsx",
        template = "SEPIA/Template/pypsa.html",
        biomass_potentials=sepia_biomass_potentials_input,
        excelfile=results_expand(
            RESULTS + "sepia/inputs{country}.xlsx",
            country=local_countries,
        ),
        plots_html = "config/plots.yaml",
        
    output:
        excelfile=results_expand(
            RESULTS + "htmls/ChartData_{country}.xlsx",
            country=local_countries,
        ),
        htmlfile_emissions=results_expand(
            sepia_html_suffix("emissions"),
            country=local_countries,
            **sepia_study_expand_kw(),
        ),
        htmlfile_sankeys=results_expand(
            sepia_html_suffix("sankeys"),
            country=local_countries,
            **sepia_study_expand_kw(),
        ),
        htmlfile_fec=results_expand(
            sepia_html_suffix("fec"),
            country=local_countries,
            **sepia_study_expand_kw(),
        ),
    threads: 1
    resources:
        mem_mb=10000,
    log:
        RESULTS + "logs/generate_sepia.log",
    benchmark:
        RESULTS + "benchmarks/generate_sepia",
    conda:
        "../envs/environment.yaml"
    script:
        "../SEPIA/SEPIA.py"
        
        
rule prepare_results:
    params:
        countries=config_provider("countries"),
        planning_horizons=config_provider("planning_horizons"),
        sector_opts=config_provider("scenario", "sector_opts"),
        plotting=config_provider("plotting"),
        scenario=config_provider("scenario"),
        study=study_name,
        foresight=config_provider("foresight"),
    input:
        networks=results_expand(
            RESULTS
            + "networks/solved_{horizon}.nc",
            horizon=config["planning_horizons"],
        ),
        excelfile=results_expand(
            RESULTS + "htmls/ChartData_{country}.xlsx",
            country=local_countries,
        ),
        costs=sepia_costs_input,
        sepia_config = "SEPIA/SEPIA_config.xlsx",
        template = "SEPIA/Template/pypsa.html",
        logo = "SEPIA/Template/logo.png",
        plots_html = "config/plots.yaml",        
    output:
        htmlfile=results_expand(
            sepia_results_html_path(),
            country=local_countries,
            section=["demands", "costs", "capacities", "dispatch_plots"],
            **sepia_study_expand_kw(),
        ),
    threads: 1
    resources:
        mem_mb=10000,
    log:
        RESULTS + "logs/prepare_results.log",
    benchmark:
        RESULTS + "benchmarks/prepare_results",
    conda:
        "../envs/environment.yaml"
    script:
        "../SEPIA/Pypsa_results.py"


rule refresh_dashboard_nav:
    input:
        countries="SEPIA/COUNTRIES.xlsx",
        plots_html="config/plots.yaml",
        htmlfiles=ALL_DASHBOARD_HTML,
    output:
        touch("results/.dashboard_nav_refreshed"),
    log:
        "results/logs/refresh_dashboard_nav.log",
    conda:
        "../envs/environment.yaml"
    script:
        "../SEPIA/refresh_dashboard_nav.py"
