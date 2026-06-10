# -*- coding: utf-8 -*-
"""Refresh embedded navigation manifests in all dashboard HTML files."""

import logging

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake("refresh_dashboard_nav")

    import logging
    from pathlib import Path

    import nav_helpers as nh

    logging.basicConfig(level=snakemake.config.get("logging", {}).get("level", "INFO"))

    updated, manifest = nh.refresh_all_html_manifests(
        snakemake.config,
        plots_path=snakemake.input.plots_html,
        countries_xlsx=snakemake.input.countries,
    )
    logging.info(
        "Updated navigation manifest in %s dashboard HTML files (%s scenarios).",
        updated,
        len(manifest.get("scenarios", [])),
    )

    Path(snakemake.output[0]).parent.mkdir(parents=True, exist_ok=True)
    Path(snakemake.output[0]).write_text("", encoding="utf-8")
