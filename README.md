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

**BE+FR calibration (required for feasible solves):** national CO₂ budgets in
`config/config.quick_test_chain.yaml` are relaxed relative to the full-Europe
trajectory (2030: 0.55, 2035: 0.40, …, 2050: 0.05 of 1990 per country). The
full-Europe caps are near-infeasible for a two-country subset and produce
pathological CO₂ duals (≈ 10⁴ €/t) that make RFNBO runs unbounded. Per-scenario
overrides in `config/scenarios.quick_test_chain.yaml` disable **hourly** temporal
correlation for `RFNBO_CR` (the no-H₂ counterfactual dispatch baseline is
incompatible with the RFNBO load/build at this scale); **annual PPA** still applies
via the additionality pairing, and **monthly** correlation still runs at 2025.
Use the full-country configs for hourly temporal validation.

**Solver safeguards:** quick-test Gurobi options include, `DualReductions: 0`, and `BarHomogeneous: 1`. `scripts/solve_network.py` rejects
infeasible/unbounded/time-limit terminations and pathologically negative objectives
before exporting a network, so a bad solve fails in minutes instead of hanging for
hours.

### 2. Run the test chain (recommended — single Snakemake)

[`Snakefile_quick_test_chain`](Snakefile_quick_test_chain) runs **baseline**, **baseline_without_H2**, and **RFNBO_CR** (additionality + temporal + monthly correlation) in one invocation. Shared electricity preprocessing is built once (`run.shared_resources.policy: base`); per-scenario solve logs are kept separately under `logs/{run}/` and `results/{run}/logs/`.

```bash
conda activate pypsa-eur

snakemake --snakefile Snakefile_quick_test_chain --cores 8 -n     # dry run
snakemake --snakefile Snakefile_quick_test_chain --cores 8 -call  # execute (-call, not -callall)
```

`rule all` builds solved networks and SEPIA HTML dashboards for BE, FR, and EU under `results/{run}/htmls/` (e.g. `results/baseline/htmls/BE_demands_baseline.html`). To skip post-processing, target a single `.nc` file as below.

To rebuild SEPIA HTML only (after solves are already done), use the wildcard-free `sepia_all` target — not `prepare_sepia`, `generate_sepia`, or `prepare_results` (those rules carry `{run}` / `{country}` wildcards and Snakemake will reject them):

```bash
snakemake --snakefile Snakefile_quick_test_chain --cores 8 -call sepia_all
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
| SEPIA dashboard | `results/<scenario>/htmls/BE_demands_<scenario>.html` exists (36 files per three-scenario chain) |

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

### `sector_opts` must match across scenarios

Cross-scenario inputs (baseline CO₂ duals, `baseline_without_H2` reference networks) are resolved at the **current** scenario's `{clusters}_{opts}_{sector_opts}` wildcards, so all scenarios must use the same temporal resolution. The full-run configs are now unified at **`2H`** (`scenario.sector_opts` in every `config.<scenario>.yaml`); the quick-test chain stays at `6H`. Do not mix resolutions.

### H₂ underground storage (salt caverns)

Small country subsets (e.g. the BE/FR quick test) can produce a salt-cavern CSV with only some of the configured location types (`onshore`, `nearshore`). Upstream PyPSA-Eur then crashes with `KeyError: "['nearshore'] not in index"` because it checks for any matching column but selects all configured types. This repo patches `scripts/prepare_sector_network.py` to sum only columns that exist. Full-Europe runs are unaffected (both types are present).

### RFNBO constraint activation and cohort years

Constraint behaviour depends on the scenario name in `scripts/solve_network.py`. Three
variables are defined per scenario branch in `__main__`:

- `temporal_year` — asset cohort for the hourly temporal-correlation constraint
  (generators/electrolysers with `build_year >= temporal_year`). For
  `RFNBO_CR`/`RFNBO_Temp`/`RFNBO_Add` this is **2025** (grandfathering applies to
  additionality, not temporal correlation).
- `target_year` — first planning horizon where the hourly constraint is active
  (**2030** for CR/Temp/Add).
- `monthly_year` — cohort for the monthly correlation constraint, which is applied
  **only at the 2025 horizon** for CR/Temp (the delegated act allows monthly matching
  until end-2029; hourly applies from 2030).

Variant branches (`RFNBO_VAR-A1/A2`) are still marked `TO BE CHECKED & ADAPTED FOR
VARIANTS` — review before interpreting variant results.

### Per-carrier capacity growth limits (`max_growth`)

Myopic solves can otherwise deploy implausible amounts of new capacity in a single
5-year step (e.g. 0→364 GW electrolysers). The flag
`solving.constraints.max_growth` (default `enable: true` with identical carrier caps
in every scenario config) adds EU-wide **absolute** caps on new extendable capacity
per planning horizon in `scripts/solve_network.py`
(`add_max_growth_constraint`). Values are in GW per carrier per 5-year step; they
apply to the sum of extendable generator and link `p_nom` for that carrier. Set
`enable: false` to deactivate. The same caps must be used in baseline,
`baseline_without_H2`, and all RFNBO scenarios so cross-scenario constraints
(additionality, endogenous H₂ demand floor) stay mutually feasible. Native PyPSA
`carriers.max_growth` is not used here because it only applies to multi-investment
networks (`n._multi_invest`).

### Endogenous H₂ demand floor (fair comparison with baseline)

Without further constraints, RFNBO scenarios respond to the certification rules partly
by **producing less e-fuel** and backfilling the shared oil pool with fossil imports,
which makes baseline–RFNBO comparisons unfair (different renewable service levels).

The flag `solving.constraints.endogenous_H2_demand_floor` (default `true` in all
`config.RFNBO_*.yaml`, `false` for the baselines) activates a constraint in
`scripts/solve_network.py` (`add_endogenous_H2_demand_floor_constraint`, active for
`RFNBO*` runs from 2030): for each endogenous H₂-consuming power-to-X carrier
(Fischer-Tropsch, Sabatier, methanolisation, Haber-Bosch, …), annual H₂ consumption
summed over all modelled nodes must be **≥ the solved baseline value at the same
horizon** (read from `snakemake.input.baseline_network`).

Consumers are discovered **structurally** (any link port on a carrier-`H2` bus with a
consuming sign convention), so new power-to-fuel technologies are covered automatically;
pipelines are excluded structurally, and `H2 Fuel Cell` / `H2 turbine`
(re-electrification = flexibility) and `H2 liquefaction` (driven by the exogenous
shipping load) are excluded by default
(`solving.constraints.endogenous_H2_demand_floor_exclude` to override). The floor is
per-carrier (preserves the product mix) and EU-level (spatial reallocation stays free).
See `RFNBO_implementation_review.md` §3.7 for design rationale and validation.

### Additionality = capacity + annual-PPA pair

Per the Delegated Act (Art. 5(a)/(b), see `RFNBO_rules.md`), additionality is a
*capacity-eligibility* rule (new assets) coupled with a *volumetric PPA-equivalence*
rule (PPAs must cover the claimed MWh). The model mirrors this: whenever the
per-country capacity constraint (`add_additionality_constraint`) is activated,
the annual volumetric constraint (`add_annual_ppa_constraint`: post-2025-cohort VRE
dispatch above the no-H₂ counterfactual ≥ electrolyser consumption, per country and
year) is activated with it (`extra_functionality`). Because of capacity factors the
volumetric constraint requires ≈ 2–4 GW of VRE per GW of electrolyser and is
generally the binding one of the pair (review §9.1). The hourly temporal-correlation
constraint implies the annual sum, so the explicit annual PPA matters most in
`RFNBO_Add` (additionality without hourly matching); it is not added from the
temporal-correlation branch (`RFNBO_Temp` stays hourly-only).

### Non-RFNBO production cap (`non_rfnbo_h2_cap_2025`)

The additionality/PPA and temporal-correlation constraints are conditional on
electrolysers ("for every MW/MWh of electrolyser, matching additional VRE"): they
regulate the **quality** of electrolytic H₂, not its **quantity** in the supply mix,
so a zero-electrolyser, all-SMR solution satisfies them trivially (the fossil-backfill
escape, review §6.4 E2). The flag `solving.constraints.non_rfnbo_h2_cap_2025`
(default `true` in all RFNBO configs, `false` for baselines; replaces the former
`smr_cap_2025`) caps EU-wide annual H₂ output of **all non-electrolytic production
routes** at its level in the solved **baseline 2025** network (≈ 93 TWh; reference
wired via `input_baseline_network_2025` in `rules/common.smk`, scenario-independent
so the cap is identical across RFNBO variants). Producers are discovered
**structurally** (`_find_h2_producing_link_ports`: link ports injecting into
carrier-`H2` buses with positive efficiency, excluding `H2 Electrolysis` /
`vre H2 Electrolysis`) — today SMR, SMR CC and ammonia cracker — so new
non-electrolytic routes are covered automatically. Semantics: grandfathering — the
2025 non-RFNBO quantity may persist, but combined with the endogenous H₂ demand
floor all consumption growth beyond 2025 must be electrolytic and hence, under the
other active constraints, RFNBO-compliant. If the exogenous `RFNBO_demand_share`
is also enabled, the effective electrolytic share is the max of its trajectory and
the cap-implied share (which rises toward 100 % as H₂ demand grows).

### RFNBO exemption criteria (deferred)

The 90 % renewable-share and 18 gCO₂/MJ grid-intensity exemptions
(`activate_vre_share_criterion`, `activate_carbon_intensity_criterion`) are **disabled**
in the quick-test chain: the computation in `get_vre_share_carbon_intensity()` has known
bugs (fuel- vs electricity-based CO₂ intensity, circular renewable-share definition) —
see `RFNBO_implementation_review.md`. Until fixed, additionality and temporal
correlation apply to all modelled countries in all active horizons.

All RFNBO constraints (additionality, annual PPA, hourly and monthly temporal
correlation) now share one exemption filter (`_rfnbo_active_countries` in
`scripts/solve_network.py`): a country is exempt if it satisfies **either**
criterion, i.e. the constraints apply to the **intersection** of the two
non-compliant country sets (previously the temporal constraints checked only the
VRE-share criterion).

### CO₂ venting (`sector.co2_vent`)

`co2_vent: true` in all eight scenario configs adds per-node vent links
(`{node} co2 stored` → `co2 atmosphere`, upstream PyPSA-Eur option, created at
**prepare** time — networks must be rebuilt). Without it, a binding sequestration
cap forces captured CO₂ to be *utilized*, driving `co2 stored` prices deeply
negative (−400 €/t at 2030) and financing the SMR→Sabatier loop (review §8.4).
Vented CO₂ is charged to the **venting** country's national budget/price (link-name
fallback in `add_co2limit_country`/`add_co2price_country`) while capture stays
credited to the capture country, so cross-border "capture in A, vent in B" is a CO₂
export from A and an emission in B; the EU-wide cap counts vented CO₂ too. Note
venting is not free under binding national budgets: the `co2 stored` value floor is
≈ −(cheapest national CO₂ price + transport). The CO₂ sankey and metrics do not yet
show vent/trade flows (review §9.4, item 29).

### CO₂ payments in cost summaries

For scenarios with `co2_price_national`, the applied per-country prices and the realized
payments are stored in the solved network's `meta` (`co2_prices`, `co2_payments`), and
`metrics.csv` includes `co2 payments` and `total costs incl co2 payments` rows. Use the
latter when comparing system costs across scenarios (plain `total costs` excludes the
CO₂ bill).

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
