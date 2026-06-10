# -*- coding: utf-8 -*-
"""Build navigation manifests for SEPIA HTML dashboards."""

import json
import re
from pathlib import Path

import pandas as pd
import yaml

CATEGORIES = [
    "demands",
    "emissions",
    "costs",
    "capacities",
    "fec",
    "sankeys",
    "maps",
    "dispatch_plots",
]

DEFAULT_SCENARIO_LABELS = {
    "baseline": "Baseline",
    "baseline_without_H2": "Baseline without H2",
    "RFNBO_CR": "Current Regulation",
}

NAV_MANIFEST_PATTERN = re.compile(
    r'(<script id="nav-manifest" type="application/json">)(.*?)(</script>)',
    re.DOTALL,
)


def parse_html_filename(filename, scenario):
    """Parse {country}_{category}_{scenario}.html; scenario may contain underscores."""
    if not filename.endswith(".html"):
        return None
    stem = filename[:-5]
    suffix = f"_{scenario}"
    if not stem.endswith(suffix):
        return None
    prefix = stem[: -len(suffix)]
    if "_" not in prefix:
        return None
    country, category = prefix.split("_", 1)
    return country, category, scenario


def dashboard_countries(config):
    countries = list(config.get("countries") or [])
    if "EU" not in countries:
        countries.append("EU")
    return countries


def load_country_labels(countries_xlsx_path="SEPIA/COUNTRIES.xlsx"):
    labels = {"EU": "EU aggregate"}
    try:
        df = pd.read_excel(countries_xlsx_path, sheet_name="COUNTRIES", index_col="Code")
        for code, row in df.iterrows():
            label = row.get("Label")
            if pd.notna(label):
                labels[str(code)] = str(label)
    except Exception:
        pass
    if labels.get("EU") == "Total":
        labels["EU"] = "EU aggregate"
    return labels


def get_configured_scenarios(config):
    run = config.get("run", {})
    names = run.get("name") or []
    if isinstance(names, str):
        names = [names]
    scenarios_file = run.get("scenarios", {}).get("file")
    yaml_scenarios = {}
    if scenarios_file and Path(scenarios_file).exists():
        yaml_scenarios = yaml.safe_load(Path(scenarios_file).read_text()) or {}
    if names:
        ordered = [s for s in names if s in yaml_scenarios or isinstance(s, str)]
        for s in yaml_scenarios:
            if s not in ordered:
                ordered.append(s)
        return ordered
    return list(yaml_scenarios.keys())


def scenario_labels(config):
    labels = dict(DEFAULT_SCENARIO_LABELS)
    for name in get_configured_scenarios(config):
        labels.setdefault(name, name.replace("_", " ").title())
    return labels


def _sort_categories(categories):
    return sorted(set(categories), key=lambda cat: CATEGORIES.index(cat) if cat in CATEGORIES else 999)


def expected_pages_from_config(config, plots_path="config/plots.yaml"):
    """Pages that should exist according to config/plots.yaml."""
    countries = dashboard_countries(config)
    scenarios = get_configured_scenarios(config)
    if not scenarios:
        return {}

    plots = {}
    if Path(plots_path).exists():
        plots = yaml.safe_load(Path(plots_path).read_text()) or {}
    pypsa = plots.get("Pypsa_plots", {})
    sepia = plots.get("Sepia_plots", {})

    categories = []
    if pypsa.get("Sectoral Demands"):
        categories.append("demands")
    if any(
        pypsa.get(key)
        for key in [
            "Annual Costs",
            "Annual Clustered Costs",
            "Annual Investment Costs",
            "Annual Operational Costs",
            "CO2 Prices",
        ]
    ):
        categories.append("costs")
    if pypsa.get("Capacities") or pypsa.get("Storage Capacities"):
        categories.append("capacities")
    if any(
        pypsa.get(key)
        for key in [
            "Heat Dispatch Winter",
            "Heat Dispatch Summer",
            "Power Dispatch Winter",
            "Power Dispatch Summer",
        ]
    ):
        categories.append("dispatch_plots")
    if any(
        sepia.get(key)
        for key in [
            "CO2 emissions by sector",
            "CO2 emissions by source",
            "Cumulative CO2 emissions by sector",
            "Cumulative CO2 emissions by source",
        ]
    ):
        categories.append("emissions")
    if sepia.get("Sankey diagram") or sepia.get("Carbon Sankey diagram"):
        categories.append("sankeys")
    if any(
        sepia.get(key)
        for key in [
            "Final energy consumption by each sector",
            "Final energy consumption by carrier for each sector",
            "Mix of secondary energies",
            "Final energy consumption by origin",
            "Renewable energy share",
            "Share of domestic production",
        ]
    ):
        categories.append("fec")

    has_maps = any(pypsa.get(key) for key in ["Map Plots", "H2 Map Plots", "Gas Map Plots"])

    pages = {}
    for scenario in scenarios:
        pages[scenario] = {}
        for country in countries:
            country_categories = list(categories)
            if has_maps:
                country_categories.append("maps")
            pages[scenario][country] = _sort_categories(country_categories)
    return pages


def merge_pages(*page_dicts):
    merged = {}
    for pages in page_dicts:
        for scenario, by_country in (pages or {}).items():
            merged.setdefault(scenario, {})
            for country, categories in by_country.items():
                merged[scenario].setdefault(country, [])
                merged[scenario][country] = _sort_categories(
                    set(merged[scenario][country]) | set(categories)
                )
    return merged


def scan_html_pages(results_root="results"):
    root = Path(results_root)
    pages = {}
    scenarios_found = []
    if not root.is_dir():
        return pages, scenarios_found

    for scenario_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        htmls_dir = scenario_dir / "htmls"
        if not htmls_dir.is_dir():
            continue
        scenario = scenario_dir.name
        scenarios_found.append(scenario)
        pages.setdefault(scenario, {})
        for html_file in htmls_dir.glob("*.html"):
            parsed = parse_html_filename(html_file.name, scenario)
            if not parsed:
                continue
            country, category, _file_scenario = parsed
            pages[scenario].setdefault(country, [])
            if category not in pages[scenario][country]:
                pages[scenario][country].append(category)

    for scenario in pages:
        for country in pages[scenario]:
            pages[scenario][country] = _sort_categories(pages[scenario][country])
    return pages, scenarios_found


def build_nav_manifest(
    config,
    results_root="results",
    countries_xlsx="SEPIA/COUNTRIES.xlsx",
    plots_path="config/plots.yaml",
):
    scanned, scenarios_found = scan_html_pages(results_root)
    expected = expected_pages_from_config(config, plots_path=plots_path)
    pages = merge_pages(scanned, expected)

    configured = get_configured_scenarios(config)
    scenarios = list(configured)
    for scenario in scenarios_found:
        if scenario not in scenarios:
            scenarios.append(scenario)
    if not scenarios:
        scenarios = scenarios_found

    return {
        "scenarios": scenarios,
        "scenarioLabels": scenario_labels(config),
        "countryLabels": load_country_labels(countries_xlsx),
        "categories": CATEGORIES,
        "pages": pages,
    }


def nav_manifest_json(config, **kwargs):
    return json.dumps(build_nav_manifest(config, **kwargs), ensure_ascii=False)


def write_nav_manifest_file(config, output_path="results/nav_manifest.json", **kwargs):
    manifest = build_nav_manifest(config, **kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def inject_nav_manifest(template_content, config, **kwargs):
    return template_content.replace("{{NAV_MANIFEST}}", nav_manifest_json(config, **kwargs))


def replace_nav_manifest_in_html(html_content, manifest_json):
    if "{{NAV_MANIFEST}}" in html_content:
        return html_content.replace("{{NAV_MANIFEST}}", manifest_json)
    if not NAV_MANIFEST_PATTERN.search(html_content):
        return html_content
    return NAV_MANIFEST_PATTERN.sub(
        rf"\1{manifest_json}\3",
        html_content,
        count=1,
    )


def refresh_all_html_manifests(
    config,
    results_root="results",
    countries_xlsx="SEPIA/COUNTRIES.xlsx",
    plots_path="config/plots.yaml",
):
    """Re-embed a complete manifest in every dashboard HTML file."""
    manifest = build_nav_manifest(
        config,
        results_root=results_root,
        countries_xlsx=countries_xlsx,
        plots_path=plots_path,
    )
    manifest_json = json.dumps(manifest, ensure_ascii=False)
    root = Path(results_root)
    updated = 0

    for scenario_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        htmls_dir = scenario_dir / "htmls"
        if not htmls_dir.is_dir():
            continue
        scenario = scenario_dir.name
        for html_file in htmls_dir.glob("*.html"):
            if parse_html_filename(html_file.name, scenario) is None:
                continue
            content = html_file.read_text(encoding="utf-8")
            new_content = replace_nav_manifest_in_html(content, manifest_json)
            if new_content != content:
                html_file.write_text(new_content, encoding="utf-8")
                updated += 1

    write_nav_manifest_file(
        config,
        output_path=str(root / "nav_manifest.json"),
        results_root=results_root,
        countries_xlsx=countries_xlsx,
        plots_path=plots_path,
    )
    return updated, manifest
