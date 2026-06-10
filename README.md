# PyPSA-Eur RFNBO

A sector-coupled energy system optimisation model studying **Renewable Fuels of Non-Biological Origin (RFNBO)** certification criteria, built on top of [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur).

The workflow compares a baseline decarbonisation pathway with several RFNBO policy variants (additionality, temporal correlation, carbon intensity, VRE share, and related constraints).

## Authors

**Sylvain Quoilin** and **Umair Tareen**  
[University of Liège](https://www.uliege.be) — Energy Systems Research Unit (ESRU)

## Acknowledgements

This work is based on [PyPSA-Eur v2026.02.0](https://github.com/PyPSA/pypsa-eur). Please cite the original model if you use this repository:

> T. Brown, J. Hörsch, D. Schlachtberger, *PyPSA-Eur: An Open Optimisation Model
> of the European Transmission System*, 2018,
> [arXiv:1806.01613](https://arxiv.org/abs/1806.01613).

## Scenarios

All scenarios use **myopic foresight** over planning horizons **2025, 2030, 2035, 2040, 2045, 2050**.

| Scenario | Snakefile | Config file | Role |
|---|---|---|---|
| `baseline` | `Snakefile_baseline` | `config/config.baseline.yaml` | Cost-optimal decarbonisation with national CO₂ budgets; reference for RFNBO CO₂ prices |
| `baseline_without_H2` | `Snakefile_baseline_without_H2` | `config/config.baseline_without_H2.yaml` | Baseline without hydrogen demand (from 2030 onward); reference for RFNBO additionality / temporal constraints |
| `RFNBO_CR` | `Snakefile_RFNBO_CR` | `config/config.RFNBO_CR.yaml` | Combined RFNBO criteria (additionality + temporal correlation + monthly correlation) |
| `RFNBO_Temp` | `Snakefile_RFNBO_Temp` | `config/config.RFNBO_Temp.yaml` | Temporal correlation (+ monthly) only |
| `RFNBO_Add` | `Snakefile_RFNBO_Add` | `config/config.RFNBO_Add.yaml` | Additionality only |
| `RFNBO_VAR-A1` | `Snakefile_RFNBO_VAR-A1` | `config/config.RFNBO_VAR-A1.yaml` | Variant A1 (additionality + temporal correlation) |
| `RFNBO_VAR-A2` | `Snakefile_RFNBO_VAR-A2` | `config/config.RFNBO_VAR-A2.yaml` | Variant A2 (additionality + temporal correlation, different activation years) |

### Default settings (full model)

| Parameter | Value |
|---|---|
| Countries | Full PyPSA-Eur set (27 countries in config) |
| Spatial resolution | 33 clusters (`scenario.clusters: [33]`); quick test uses one cluster per country |
| Temporal resolution | 6-hourly for `baseline`, `RFNBO_VAR-A1`, `RFNBO_VAR-A2`; **3-hourly** for `baseline_without_H2`, `RFNBO_CR`, `RFNBO_Temp`, `RFNBO_Add` |
| CO₂ policy (baseline) | National per-country CO₂ budgets (`co2_budget_national: true`) |
| CO₂ policy (RFNBO) | CO₂ prices taken from solved baseline networks (`co2_price_national: true`) |
| CCL constraint | Enabled |
| Solver | Gurobi (`gurobi-default`, 8 threads) |

### Scenario dependencies

RFNBO scenarios **depend on solved baseline results**. The solve rule in `rules/solve_myopic.smk` reads:

- `results/baseline/networks/...` — CO₂ shadow prices for non-baseline scenarios
- `results/baseline_without_H2/networks/...` — optimised VRE capacities for RFNBO additionality and temporal-correlation constraints

**Run order:**

1. `Snakefile_baseline`
2. `Snakefile_baseline_without_H2`
3. RFNBO variant Snakefiles (any order, once steps 1–2 are complete)

### `baseline_without_H2` — oil demand adjustment

For horizons after 2025, `scripts/solve_network.py` (`remove_hydrogen_demands`) strips H₂ and e-fuel infrastructure and rescales oil-product loads using the solved **baseline** network: each load is multiplied by `1 − FT / Σ(oil loads)`, where FT is total Fischer–Tropsch output and the denominator sums aviation kerosene, shipping oil, agriculture oil, **land transport oil**, and **naphtha for industry**. Static `p_set` is scaled for aviation, shipping, agriculture, and naphtha; **land transport oil** demand is carried in `loads_t.p_set` and scaled there separately (merged with the upstream fix in `c28ed22a`).

This fixes an infeasibility that appeared from 2035 onward when the old formula used only aviation + shipping + agriculture in the denominator. Baseline FT then exceeded that subset (much of the e-fuel goes to road transport), producing a **negative** scale factor and negative `p_set` on shipping-oil loads.

**Caveat:** the adjustment is still a **single global ratio** applied to all listed oil loads. It does not trace per-sector e-fuel shares from the baseline (nor biomass-to-liquid or other non-FT e-fuels). Sector-level attribution or prepare-time network building would be more accurate; see discussion in the project issue history / agent notes if you need to refine the counterfactual.

If the reference **baseline** solve for a horizon fails to produce dispatch time series (observed for **2050** with Gurobi “numerical trouble”), `remove_hydrogen_demands` skips methanation and waste-heat adjustments that depend on `links_t` / `loads_t` and leaves static loads unchanged for those steps.

## Repository Structure

```
.
├── Snakefile_quick_test_chain      # Unified baseline + baseline_without_H2 + RFNBO_CR
├── Snakefile_baseline              # Baseline scenario
├── Snakefile_baseline_without_H2   # Baseline without H₂ demand
├── Snakefile_RFNBO_CR            # RFNBO combined criteria
├── Snakefile_RFNBO_Temp          # RFNBO temporal correlation
├── Snakefile_RFNBO_Add           # RFNBO additionality
├── Snakefile_RFNBO_VAR-A1        # RFNBO variant A1
├── Snakefile_RFNBO_VAR-A2        # RFNBO variant A2
├── Snakefile_master              # Legacy orchestrator (references missing Snakefile_RFNBO)
├── config/
│   ├── config.quick_test_chain.yaml   # BE+FR quick test (multi-scenario)
│   ├── scenarios.quick_test_chain.yaml
│   ├── config.baseline.yaml
│   ├── config.baseline_without_H2.yaml
│   ├── config.RFNBO_*.yaml
│   └── config.default.yaml
├── rules/                        # Snakemake rule definitions
├── scripts/
│   ├── prepare_sector_network.py
│   ├── solve_network.py          # RFNBO constraints and solve wrapper
│   └── ...
├── SEPIA/                        # HTML/Excel result post-processing
├── data/                         # Static input data
├── cutouts/                      # Atlite weather cutouts (downloaded automatically)
├── resources/                    # Intermediate build artefacts (generated)
└── results/                      # Final solved networks and plots (generated)
```

## Installation

### Prerequisites

- [Conda/Mamba](https://conda.io) (or [Pixi](https://pixi.sh)) for environment management
- A valid [Gurobi licence](https://www.gurobi.com) (academic licence is sufficient)
- ~50 GB disk space for cutouts and intermediate files

### Environment setup

The workflow is run with the **`pypsa-eur`** conda environment. Create or update it from the provided environment file:

```bash
conda env create -f envs/environment.yaml   # first time only
conda activate pypsa-eur
conda env update -f envs/environment.yaml     # keep packages up to date
```

Or with pixi:

```bash
pixi install
```

### Gurobi licence

Place the licence file at `~/gurobi.lic` or set the `GRB_LICENSE_FILE` environment variable accordingly. A free academic licence is available at <https://www.gurobi.com/academia/academic-program-and-licenses/>.

## Running the Workflow (local)

Activate the environment first:

```bash
conda activate pypsa-eur
cd /path/to/pypsa-eur_RFNBO
```

### Full workflow

Run scenarios in dependency order. Each command builds intermediate files, solves all planning horizons, and generates SEPIA HTML/Excel outputs:

```bash
# 1. Baseline (required first)
snakemake --snakefile Snakefile_baseline --cores 8 -call

# 2. Baseline without H₂ (required before RFNBO variants)
snakemake --snakefile Snakefile_baseline_without_H2 --cores 8 -call

# 3. RFNBO variants (run after 1 and 2)
snakemake --snakefile Snakefile_RFNBO_CR --cores 8 -call
snakemake --snakefile Snakefile_RFNBO_Temp --cores 8 -call
snakemake --snakefile Snakefile_RFNBO_Add --cores 8 -call
snakemake --snakefile Snakefile_RFNBO_VAR-A1 --cores 8 -call
snakemake --snakefile Snakefile_RFNBO_VAR-A2 --cores 8 -call
```

Use `--cores` equal to the Gurobi thread count in the active config (`solving.solver_options.<preset>.threads`, typically 8).

### Dry run (check what would be executed)

```bash
snakemake --snakefile Snakefile_baseline --cores 8 -n
```

### Solve only (skip post-processing)

To test the optimisation without rebuilding SEPIA HTML outputs, target the solved networks directly:

```bash
snakemake --snakefile Snakefile_baseline --cores 8 -call \
  results/baseline/networks/base_s_2__6H_2030.nc
```

Wildcards must match the active `scenario` settings in the config file.

## Quick test run (France + Belgium, 6-hourly)

The full European model is slow. For a fast local smoke test, restrict the model to **France and Belgium** at **6-hourly** resolution.

### 1. Quick-test configuration

[`Snakefile_quick_test_chain`](Snakefile_quick_test_chain) reads only:

- [`config/config.quick_test_chain.yaml`](config/config.quick_test_chain.yaml) — countries, `scenario.clusters`, `scenario.sector_opts`, solver settings, `solving.mem_mb`, and multi-scenario `run.name`
- [`config/scenarios.quick_test_chain.yaml`](config/scenarios.quick_test_chain.yaml) — per-scenario constraint overrides (CO₂ policy, RFNBO criteria)

The individual scenario configs (`config.baseline.yaml`, `config.RFNBO_*.yaml`, …) are **not** used by the quick-test chain and can stay at full-model defaults (33 clusters, full country list).

Shipped quick-test defaults: **BE** and **FR**, **2** clusters, **`6H`** resolution, Gurobi **8** threads, solve memory **90** GB (`mem_mb: 90000`). To change countries, resolution, or memory, edit `config/config.quick_test_chain.yaml` directly. Keep `scenario.clusters` equal to the number of countries so result paths stay consistent (e.g. `base_s_2__6H_2030.nc`).

### 2. Run the test chain (recommended — single Snakemake)

[`Snakefile_quick_test_chain`](Snakefile_quick_test_chain) runs **baseline**, **baseline_without_H2**, and **RFNBO_CR** (additionality + temporal + monthly correlation) in one invocation. Shared electricity preprocessing is built once (`run.shared_resources.policy: base`); per-scenario solve logs are kept separately under `logs/{run}/` and `results/{run}/logs/`.

```bash
conda activate pypsa-eur

snakemake --snakefile Snakefile_quick_test_chain --cores 8 -call
```

For the quickest check, solve one horizon for one scenario only:

```bash
snakemake --snakefile Snakefile_quick_test_chain --cores 8 -call \
  results/RFNBO_CR/networks/base_s_2__6H_2030.nc
```

### 2b. Run scenarios separately (full model)

The individual Snakefiles use their own `config/config.<scenario>.yaml` files (27 countries, 33 clusters, etc.) and repeat preprocessing. Do not mix those results with quick-test outputs unless the wildcards match:

```bash
snakemake --snakefile Snakefile_baseline --cores 8 -call
snakemake --snakefile Snakefile_baseline_without_H2 --cores 8 -call
snakemake --snakefile Snakefile_RFNBO_CR --cores 8 -call
```

Per-scenario logs are kept separately for debugging infeasibilities:

| Log type | Location |
|----------|----------|
| Snakemake rule logs | `logs/{run}/` (e.g. `logs/baseline/prepare_sector_network_*.log`) |
| Gurobi solver | `results/{run}/logs/base_s_*_solver.log` |
| Python solve trace | `results/{run}/logs/base_s_*_python.log` |
| Memory profile | `results/{run}/logs/base_s_*_memory.log` |
| Combined tee | `logs/quick_test_chain.log` |

### 3. Verify success

| Check | What to look for |
|-------|------------------|
| Solved network | `results/<scenario>/networks/base_s_2__6H_2030.nc` exists |
| Gurobi optimality | `grep 'Optimal objective' results/<scenario>/logs/base_s_2__6H_2030_solver.log` |
| Python log | No traceback in `results/<scenario>/logs/base_s_2__6H_2030_python.log` |

Quick commands:

```bash
ls -lh results/baseline/networks/base_s_2__6H_*.nc
grep 'Optimal objective' results/baseline/logs/base_s_2__6H_*_solver.log
```

### Temporal resolution notes

The `sector_opts` entry controls the time step. `6H` gives 1 460 snapshots per year (one per 6-hour block). Coarser steps (e.g. `24H`) reduce solve time further but re-trigger all upstream profile-building rules. The `sector_opts` string is embedded in intermediate and result file names, so results at different resolutions coexist in `results/` without overwriting each other.

## Logs

Snakemake job logs are written to `logs/`. Per-solve logs are at:

- Solver: `results/<scenario>/logs/<network>_solver.log`
- Python: `results/<scenario>/logs/<network>_python.log`
- Memory: `results/<scenario>/logs/<network>_memory.log`

Snakemake's own log is in `.snakemake/log/`.

## Known Issues and Warnings

### linopy version — do not upgrade beyond 0.6.1

linopy 0.7.0 changed how `inf` bounds in PyPSA data structures are translated into LP variable bounds. With linopy ≥ 0.7.0 the resulting LP can contain thousands of truly free variables, causing Gurobi to report **"Unbounded model"**, much longer solve times, and sub-optimal termination.

`envs/environment.yaml` specifies `linopy >=0.6.1`. Pin to `linopy =0.6.1` if your environment resolves a newer version. Do not upgrade linopy without verifying the upstream regression has been fixed.

### `sector_opts` mismatch between scenarios

As noted above, the shipped configs use different temporal resolutions across scenarios. This is intentional for production runs only if each scenario is solved independently with matching baseline outputs. For any test or cross-scenario comparison, set `sector_opts` to the same value in **all** config files.

### H₂ underground storage (salt caverns)

Small country subsets (e.g. the BE/FR quick test) can produce a salt-cavern CSV with only some of the configured location types (`onshore`, `nearshore`). Upstream PyPSA-Eur then crashes with `KeyError: "['nearshore'] not in index"` because it checks for any matching column but selects all configured types. This repo patches `scripts/prepare_sector_network.py` to sum only columns that exist. Full-Europe runs are unaffected (both types are present).

### RFNBO constraint activation years

Constraint activation depends on the scenario name in `scripts/solve_network.py`. For example, `RFNBO_CR` and `RFNBO_Add` activate additionality from 2030, while other variants may wait until 2035. Several branches are marked `TO BE CHECKED & ADAPTED FOR VARIANTS` in the source — review before interpreting variant results.

### Gurobi solver settings

RFNBO configs use `gurobi-default` (8 threads, barrier method). The baseline config also defines a `gurobi-numeric-focus` preset with relaxed tolerances for numerically challenging models. If solves fail or terminate sub-optimally, try switching `solving.solver.options` to `gurobi-numeric-focus` in the relevant config file.

### Memory

Snakemake uses `solving.mem_mb` (megabytes) to schedule solve jobs. The shipped scenario configs default to `128000` (128 GB). On a machine with less RAM, lower this so jobs are not over-committed.

**Quick test:** `config/config.quick_test_chain.yaml` ships with `mem_mb: 90000` (90 GB). Lower it there if your machine has less RAM.

**Full model:** edit `solving.mem_mb` in each `config/config.<scenario>.yaml` (default `128000`).

Sector-coupled solves typically need ≥ 32 GB for a two-country test and ≥ 64 GB for the full model. Check actual usage in `results/<scenario>/logs/<network>_memory.log`.

## Hardware Requirements

Approximate requirements for the **full** workflow (all scenarios, 6 planning horizons):

| Resource | Recommendation |
|---|---|
| RAM | ≥ 64 GB (sector-coupled models can exceed 40 GB during solve) |
| CPU | ≥ 8 cores (matches Gurobi thread count in configs) |
| Disk | ~50 GB for cutouts, intermediate files, and results |
| Solve time | ~15–60 min per planning horizon (full model); much less for FR+BE test |

## Output

Solved networks are written to `results/<scenario>/networks/` as NetCDF files (`.nc`). Summary statistics, plots, and SEPIA HTML dashboards are produced by `rules/postprocess.smk` and the `SEPIA/` scripts into `results/<scenario>/htmls/`.

## Licence

The code in this repository is derived from [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur)
and inherits its [MIT licence](LICENSES/MIT.txt). Data files retain their original
licences as documented in [REUSE.toml](REUSE.toml).
