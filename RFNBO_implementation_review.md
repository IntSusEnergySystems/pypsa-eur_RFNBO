# RFNBO Implementation Review

*Date: 2026-06-10 (initial). Last updated: 2026-06-11 (full-Europe 6H run analysis,
§6–§7). Scope: RFNBO constraint implementation (`scripts/solve_network.py`),
workflow/config plumbing, the BE+FR quick-test results, and the **full-Europe 27-country
33-cluster 6H run** (`results/{baseline,baseline_without_H2,RFNBO_CR}`), cross-checked
against the specification in `latex/main.tex`.*

Severity legend: **CRITICAL** = wrong results or crash; **MAJOR** = materially affects
results or will break variants/full run; **MINOR** = robustness/cosmetic.

---

## 1. Executive summary

The pipeline runs end-to-end; all quick-test horizons solve optimal; constraint machinery
is wired correctly. Most MAJOR blockers from the initial review are **fixed** (see §1b).
After the counterfactual repair and cohort-year corrections, RFNBO_CR **discriminates**
from baseline (2030 electrolyser capacity 42 vs 64 GW pre-floor; with the endogenous H₂
floor, +5.9 → +11.8 bn €/yr cost premium at equal e-fuel service, §3.7).

**Full-Europe 6H run (2026-06-11, §6):** all 18 solves optimal, all RFNBO constraints
added as designed — yet the policy effect is **muted** (cost +0–3 %, CO₂ ≈ identical,
e-fuel volumes pinned). Root causes (§6.4): (i) zero electrolysis anywhere before 2035,
so the 2030 constraints have nothing to act on; (ii) the route-agnostic H₂ demand floor
lets **fossil SMR backfill** the e-fuel chain (2035: −180 TWh electrolysis, +202 TWh
SMR); (iii) additionality is structurally slack in every large H₂ country; (iv) the
exempt direct-connection chain absorbs ~12 % of H₂. Temporal correlation **is** operative
(binding 40–70 % of snapshots in major H₂ countries by 2040–2050) and drives the
observable +10–14 % VRE / +2–3 % cost signal. Recommendations to obtain significant
results in §7.

**Still open before the full-country 2H run:**

1. **Exemption criteria (C1–C3):** fixed on `origin/master` (`c91e8ccd`, `90364c9b`) but
   not yet on local HEAD — re-enable only after merging those commits and re-validating.
   Quick test keeps criteria disabled (`c6533245`).
2. ~~**Documentation:** `main.tex` updates~~ — **Done 2026-06-11**: `latex/main.tex`
   rewritten to match the implementation (incremental additionality, 2025 cohort /
   annual-PPA, monthly-at-2025 schedule, new H₂ demand-floor subsection, direct-connection
   chain and its exemption, criteria status, CO₂-price notation); compiles with
   `pdflatex -shell-escape` (10 pp.).
3. **Minor hardening (§2.2):** snapshot weights, bus→country mapping, pandas 3.x
   `groupby(axis=1)`, cached baseline loads, typos.
4. **Operational:** 2H memory smoke test at 33 clusters; manual run-order for full model.
5. **NEW (§7):** close the SMR backfill route and the additionality stock blindness
   before re-running, otherwise the RFNBO signal stays buried.

---

## 1b. Fix status (2026-06-11)

| Item | Status | Commit(s) |
|------|--------|-----------|
| **C1** Grid CO₂ intensity (fuel vs electric output) | **Fixed** *(on `origin/master`)* | `c91e8ccd` — emissions from fuel input with efficiency division |
| **C2** Renewable-share definition (H₂ FC/turbine circularity) | **Partially fixed** *(on `origin/master`)* | `c91e8ccd` — removed `H2 Fuel Cell` and `H2 turbine` from RES set; biomass CHP / hydro still open |
| **C3** Exemption union vs intersection | **Fixed** *(on `origin/master`)* | `90364c9b` — additionality uses intersection when both criteria active |
| **C4** `target_year` / `temporal_year` / `monthly_year` | **Fixed** | `8cac357d` |
| **C5** Monthly correlation 2025-only | **Documented, kept as-is** | `2c478563` |
| **C6** `active_countries` NameError in monthly constraint | **Fixed** | `278487ed` |
| **C7** RFNBO demand-share wrong carriers | **Fixed** (flag still off) | `5223c89f` |
| **C10** Silent constraint skips | **Fixed** | `11b6590d`, `278487ed` |
| Exemption criteria deferred in quick test | **Workaround** | `c6533245`, `561e3b35` |
| Norway `'NO': 30` CO₂ budget typo | **Fixed** | `0762771d` |
| Full-run `sector_opts` mismatch (6H/3H) | **Fixed** — unified to `2H` | `a0eee4b2` |
| CO₂-payment reporting in summaries | **Fixed** | `bffdc658` |
| Dangling links in no-H₂ counterfactual | **Fixed** | `81e66286` |
| District-heat demand double-removal in counterfactual | **Fixed** | `2ac940f7` |
| No-H₂ demand removal hardening (oil scaling) | **Fixed** | `50a63bef`, `56dc8972` |
| Endogenous H₂ demand floor (fair comparison) | **Fixed and validated** | `c5473c87` |
| Quick test where constraints bind | **Validated** | `03ea7da7` (post `81e66286`) |

**Temporal-correlation bindingness** (no formulation bug): binds in ~1–8 % of snapshots;
cheap to satisfy because the additional-VRE pool is 6–19× electrolyser capacity and
electrolysers already dispatch flexibly. Full slack analysis in §3.3. Restricting the
pool to "contracted" VRE per electrolyser is an open modelling decision.

---

## 2. Constraint implementation

### 2.1 Fixed issues (summary)

| ID | Summary | Commit |
|----|---------|--------|
| **C1** | Grid CO₂ intensity now computed from fuel input divided by efficiency, not electric output × fuel intensity. | `c91e8ccd` *(origin)* |
| **C2** | `H2 Fuel Cell` and `H2 turbine` removed from renewable generation set. Biomass CHP and hydro inclusion still to decide/document. | `c91e8ccd` *(origin, partial)* |
| **C3** | Additionality exemption set changed from union to intersection when both VRE-share and carbon-intensity criteria are active. | `90364c9b` *(origin)* |
| **C4** | All three year variables defined in every scenario branch; hourly constraint filters `build_year >= temporal_year` (CR cohort = 2025). | `8cac357d` |
| **C5** | Monthly correlation at 2025 horizon only — intentional per delegated act (monthly until end-2029, hourly from 2030); README updated. | `2c478563` |
| **C6** | Missing `else` branch added in monthly temporal constraint. | `278487ed` |
| **C7** | Demand carriers corrected (`H2 for shipping`, `land transport fuel cell`); Haber-Bosch H₂ at bus2; `vre H2 Electrolysis` counted in production. | `5223c89f` |
| **C10** | `logger.error; return` replaced by `raise RuntimeError`; vacuous constraints (empty country list) skipped with log. | `11b6590d`, `278487ed` |

### 2.2 Open issues

#### C8. Direct-connection chain (`vre H2 Electrolysis`) invisible to RFNBO constraints — document as design decision
`prepare_sector_network.py` adds a dedicated sub-network (carriers `solar vre`,
`onwind vre`, `offwind-* vre`, `vre H2 Electrolysis`, `vre battery *`). None match
constraint filters (`carrier == "H2 Electrolysis"`, `carrier.isin(renewable_carriers)`).
Defensible (dedicated direct line is inherently RFNBO-compliant) but: (a) escape route
once constraints bind; (b) must stay on production side of share constraint (done in C7);
(c) `main.tex` must describe the actual implementation.

#### C9. Additionality compares only extendable (current-horizon) assets — document the formulation
`add_additionality_constraint` sums extendable `p_nom` variables on the LHS and
extendable `p_nom_opt` in the baseline on the RHS — an incremental, per-horizon
constraint. Cumulative additionality holds only approximately. State explicitly in
`main.tex`; alternative (totals = fixed constants + extendable variables) is a small code
change closer to the literal spec.

### 2.3 Minor (still open)

- **Hydro in additionality VRE set:** reservoir hydro counts toward additionality; align
  code, tex and the CLIMACT decision.
- **Snapshot weightings:** annual-PPA, monthly and share sums omit `snapshot_weightings`
  (harmless with uniform weighting; add for robustness).
- **`.filter(like=country)`** matches substrings — prefer `bus → country` mapping for
  27 countries.
- **`groupby(..., axis=1)`** deprecated in pandas 3.x.
- **Hard-coded previous-horizon path** ignores `run.prefix`, assumes 5-year spacing.
- **Performance:** baseline network reloaded per country for CO₂ price and per constraint
  function — cache it.
- **Typos:** `co2_budget_nationl`, constraint name `RBNFO_h2_supply_share`.
- **Annual PPA constraint** redundant with hourly sum — document or remove.
- **CO₂ price sanity check:** no guard that dual `mu` exists and prices ≥ 0.

---

## 3. Quick-test results (BE+FR, 2 clusters, 6H)

### 3.1 Fixed bugs (summary)

| Issue | Summary | Commit |
|-------|---------|--------|
| Dangling links in no-H₂ counterfactual | Removed all links/generators/loads/stores touching dropped H₂/NH₃/methanol buses; counterfactual was producing phantom electricity from undefined-bus links. | `81e66286` |
| District-heat double-removal | Stopped subtracting waste heat from urban-central-heat loads when H₂ links were already removed. | `2ac940f7` |
| Oil-product demand rescaling | Denominator widened; guards for empty dispatch; documented as design choice (e-fuel share removed, not fossil-substituted). | `50a63bef`, `56dc8972`, `bea4f7df` |
| CO₂ payments excluded from cost summaries | Realized payments in `n.meta["co2_payments"]`; `make_summary.py` adds `co2 payments` row. | `bffdc658` |
| Unfair RFNBO vs baseline comparison | Endogenous H₂ demand floor pins each power-to-X pathway to ≥ baseline level. | `c5473c87` |

### 3.2 Headline numbers (pre-floor first run, §3.5)

| Horizon | base VRE | noH₂ VRE | RFNBO VRE | base Ely | RFNBO Ely | base cost | RFNBO cost | RFNBO CO₂ pay |
|---------|---------|----------|-----------|----------|-----------|-----------|------------|----------------|
| 2030 | 562 | **382** | 538 | 63.6 | **42.4** | 208.6 | 197.2 | 236.3 |
| 2035 | 786 | 522 | 811 | 110.6 | 107.3 | 228.1 | 225.7 | 151.8 |
| 2040 | 969 | 607 | 1004 | 145.2 | 155.7 | 242.1 | 244.3 | 62.7 |
| 2045 | 1105 | 671 | 1136 | 164.3 | 184.7 | 238.5 | 242.8 | 15.5 |
| 2050 | 1093 | 659 | 1124 | 164.3 | 184.7 | 219.6 | 225.4 | 1.1 |

All 64 steps optimal after counterfactual repair. RFNBO constraints visibly shape the
solution from 2030 (Ely 42 vs 64 GW); cost premium emerges from 2040.

### 3.3 Temporal-correlation slack analysis

Post-solve analysis of 2030/2035 RFNBO_CR networks — **no formulation bug**. Constraint
binds at solver tolerance in ~1–8 % of snapshots; FR electrolyser dispatch reshaped by
up to ~11 GW in individual hours. Cheap to satisfy because eligible additional-VRE pool
is 6–19× electrolyser capacity and electrolysers already operate flexibly (CF 0.55–0.64,
r ≈ 0.7–0.8 with new-VRE dispatch). Annual PPA constraint far from binding (13–78 TWh
slack). Restricting the pool to dedicated "contracted VRE" per electrolyser is deferred.

### 3.4 Endogenous H₂ demand floor — fair comparison (2026-06-10)

Pre-floor, RFNBO_CR reduced e-fuel production and backfilled the shared oil pool with
fossil imports (221 → 137 TWh electrolysis at 2030), confounding the policy comparison.
The floor constraint (`add_endogenous_H2_demand_floor_constraint`, flag
`solving.constraints.endogenous_H2_demand_floor`) pins each endogenous H₂-consuming
link carrier to ≥ baseline annual H₂ consumption (from solved baseline dispatch).

**Validation** (`c5473c87`, full quick-test chain, all horizons optimal):

| Indicator (2030) | baseline | RFNBO pre-floor | RFNBO **with floor** |
|------------------|----------|-----------------|----------------------|
| Electrolysis output (TWh) | 220.8 | 136.9 | **225.0** |
| FT H₂ input (TWh) | 188.7 | 107 | **188.7** |
| Fossil oil into EU oil bus (TWh) | 706.6 | 764 | **706.4** |
| Electrolyser capacity (GW) | 63.6 | 42.4 | **69.7** |
| capex+opex (bn €) | 208.6 | 197.2 | **214.5** |

**Policy-relevant cost premium** at equal e-fuel service (capex+opex delta; CO₂
payments excluded):

| Horizon | 2030 | 2035 | 2040 | 2045 | 2050 |
|---------|------|------|------|------|------|
| Δ cost RFNBO_CR − baseline (bn €/yr) | **+5.9** | +7.2 | +10.2 | +10.8 | +11.8 |
| Electrolyser capacity (GW) RFNBO vs base | 69.7 / 63.6 | 121.1 / 110.6 | 166.4 / 145.2 | 191.5 / 164.3 | 191.5 / 164.3 |

**Caveats:** comparison still mixes CO₂ budgets (baseline) vs CO₂ prices (RFNBO); floor
requires baseline solved at identical wildcards; re-electrification (H₂ FC) excluded from
floor LHS by design.

### 3.5 Plausibility flags (check before full run)

- 73 → 562 GW VRE and 0 → 63.5 GW electrolysers in BE+FR 2025→2030 — no build-rate
  limits; consider `max_growth`.
- 2050 gas generation spike and price collapse — myopic end-horizon artifact.
- Treat PyPSA consistency warnings about undefined buses as errors.

---

## 4. Workflow / configuration for the final run (all countries, 2H)

### 4.1 Fixed

| Item | Commit |
|------|--------|
| Unify `sector_opts: 2H` in all seven full-run scenario configs | `a0eee4b2` |
| Norway 2035 budget `'NO': 30` → `0.30` | `0762771d` |
| C4/C6 variant crashes | `8cac357d`, `278487ed` |

### 4.2 Still to verify

1. **Run order:** baseline → baseline_without_H2 → RFNBO variants; verify all six
   horizons of both baselines exist (2H, 33 clusters) before RFNBO runs. Quick-test chain
   (`Snakefile_quick_test_chain`) provides a single-DAG pattern.
2. **Counterfactual design:** quick test strips H₂ at solve time; full
   `config.baseline_without_H2.yaml` disables H₂ at prepare time — pick one for production.
3. **Exemption criteria:** merge `c91e8ccd` + `90364c9b` from origin before re-enabling.
4. **Memory:** 2H = 4380 snapshots/yr; smoke-test one horizon at 33 clusters before
   queueing all runs.
5. **Pre-flight:** `snakemake -n` on one RFNBO 2030 target; inspect resolved
   `baseline_network` / `baseline_updated` paths.

---

## 5. Prioritized action list

| # | Action | Severity | Status | Commit |
|---|--------|----------|--------|--------|
| 1 | Fix grid CO₂-intensity computation (C1) | CRITICAL | **Done** *(origin)* | `c91e8ccd` |
| 2 | Unify `sector_opts: 2H` across configs | CRITICAL | **Done** | `a0eee4b2` |
| 3 | Define cohort/activation years (C4) | MAJOR | **Done** | `8cac357d` |
| 4 | Union vs intersection for exemptions (C3) | MAJOR | **Done** *(origin)* | `90364c9b` |
| 5 | Monthly activation decision (C5) | MAJOR | **Documented** | `2c478563` |
| 6 | Renewable-share definition (C2) | MAJOR | **Partial** *(origin)* | `c91e8ccd` |
| 7 | Norway CO₂ budget typo | MAJOR | **Done** | `0762771d` |
| 8 | C6 `else` + C10 raises | MAJOR | **Done** | `278487ed`, `11b6590d` |
| 9 | RFNBO-share carriers (C7) | MAJOR | **Done** | `5223c89f` |
| 10 | CO₂-payment reporting | MAJOR | **Done** | `bffdc658` |
| 11 | Quick test where constraints bind | MAJOR | **Done** | `03ea7da7` |
| 12 | Update `latex/main.tex` (C8/C9, floor, schedule) | MINOR | **Done** (2026-06-11) | uncommitted |
| 13 | Hardening (§2.3 minor) | MINOR | **Open** | — |
| 14 | Consider `max_growth` limits | suggestion | **Open** | — |
| 15 | Endogenous H₂ demand floor | MAJOR | **Done** | `c5473c87` |
| 16 | Merge origin C1–C3 fixes into local branch | MAJOR | **Done** (2026-06-11 merge) | `c91e8ccd`, `90364c9b` |
| 17 | Pin **electrolytic** H₂, not just consumption (R1, §7) | **CRITICAL** | **Open** | — |
| 18 | Additionality blind to non-extendable stock (R3, §7) | MAJOR | **Open** | — |
| 19 | Investigate 2030 SMR→Sabatier loop (515 TWh, §6.5) | MAJOR | **Open** | — |
| 20 | Decide direct-connection chain treatment (R4, §7) | MAJOR | **Open** | — |
| 21 | Restrict temporal-correlation VRE pool (R5, §7) | suggestion | **Open** | — |

---

## 6. Full-Europe run (27 countries, 33 clusters, 6H) — why RFNBO effects are muted (2026-06-11)

Run: `Snakefile_quick_test_chain` scaled up to the full country set (config diff: 27
countries, 33 clusters, 6H, `mem_mb: 128000`; BE/FR 2035 budgets tightened to 0, 2050 EU
budget to 0). Scenarios `baseline`, `baseline_without_H2`, `RFNBO_CR` solved for all six
horizons. Exemption criteria **disabled** (per `scenarios.quick_test_chain.yaml`) → all
28 modelled countries constrained.

### 6.1 Run health — the machinery worked

- **All 18 solves optimal** (Gurobi barrier, 38–93 min/horizon). No skips, no
  RuntimeErrors, no infeasibilities.
- RFNBO_CR LP rows vs baseline: **+336** at 2025 (monthly correlation, 12 months × 28
  countries) and **≈ +41 k** from 2030 (28 additionality + 1460×28 hourly temporal +
  28 annual PPA + 4 H₂ floors). Constraint schedule exactly as coded (2025 → monthly
  only; 2030+ → full set).
- National CO₂ prices from baseline duals applied at 2030+: median 184 (2030) → 628
  (2035) → 691 (2040) → 671 (2045) → 471 €/t (2050).
- Gurobi warns about **large bounds** (all years) and **large RHS** (2045–2050, the
  ~1 100 TWh FT floor) — watch numerics at 2H.

### 6.2 Headline comparison (RFNBO_CR vs baseline)

| Indicator | 2030 | 2035 | 2040 | 2045 | 2050 |
|---|---|---|---|---|---|
| Total cost Δ (capex+opex) | +0.1 % | **−1.2 %** | +2.0 % | +1.9 % | +2.6 % |
| CO₂ payments (bn €) | 473 | 627 | 437 | 144 | −12 |
| Electrolyser cap (GW), base → CR | 0 → 0 | 107 → **52** | 364 → 409 | 545 → 616 | 549 → 624 |
| VRE cap Δ | 0 % | −3.8 % | **+14.1 %** | +10.2 % | +10.9 % |
| CO₂ emissions Δ | −0.1 % | **+4.0 %** | −0.9 % | −0.7 % | −11 Mt |
| FT / methanol / Sabatier / HB H₂ use | pinned ≈ baseline by floor (Δ ≤ 3 %) | | | | |
| Curtailment (solar, Δpp) | — | +1.2 | +2.4 | +1.9 | +1.6 |
| `vre H2 Electrolysis` share of H₂ | 0 % | 0 % | **12.1 %** (base 0.2 %) | 10 % | 11.6 % |

So the constraints are **not without effect** — from 2040 they reshape the supply side
(+10–14 % VRE and electrolyser capacity, more curtailment, dedicated direct-connection
chains) at +2–3 % system cost. What is missing is (a) any effect at 2030, (b) a
*positive* RFNBO effect at 2035 (the sign is reversed: less electrolysis), and (c) any
differentiation in the demand-side / CO₂ / e-fuel metrics the study is about.

### 6.3 Constraint bindingness (post-solve slack reconstruction)

Reproducible script: `analysis/rfnbo_bindingness.py` (replicates the constraint filters
from `solve_network.py` on the solved networks; all-countries enforcement, matching the
run; execute with `conda run -n pypsa-eur python analysis/rfnbo_bindingness.py`).

| Constraint | 2030 | 2035 | 2040 | 2050 |
|---|---|---|---|---|
| **Additionality** (countries binding / total) | 9/28, but no electrolysers exist | 10/28; all big fleets slack (GB +58 GW) | 9/28; GB +195, NL +50, FR +22 GW slack | 24/28 "binding" — artifact, ≈0 GW extendable ely left |
| **Hourly temporal** (median % snapshots binding) | 0 % (no ely) | ~25–30 % in GB/FR/NL/DK | **43 %** median; FR 67 %, GB 63 % | **48 %**; ES 69 %, IT 66 % |
| **H₂ demand floor** | binds (met by SMR) | binds at equality | binds 4/4 carriers | binds 3/4 (FT +31 TWh) |
| **Monthly** (2025 only) | vacuous — no electrolysers at 2025 | — | — | — |

**Temporal correlation is the only constraint doing real work.** Additionality binds
only where there is nothing to constrain (zero-electrolyser countries, where it
degenerates to "build ≥ counterfactual VRE"); every major H₂ country carries tens of GW
of slack because the system overbuilds VRE for decarbonisation anyway.

### 6.4 Diagnosis — the four escape routes (ranked)

**E1 — No electrolysis before 2035 (kills 2030, makes 2025/2030 constraints vacuous).**
Under this run's CO₂ budgets/prices, 2030 hydrogen (792 TWh) is produced **100 % by
SMR** in *both* scenarios. RFNBO constraints regulate electrolysers; with none built,
additionality reduces to a VRE floor already met, temporal correlation has an empty LHS,
and the monthly constraint (2025) never sees an electrolyser. The BE+FR quick test had
220 TWh of 2030 electrolysis — the full-Europe cost landscape (cheaper gas/SMR, softer
2030 budgets) eliminated it.

**E2 — The H₂ demand floor is route-agnostic → fossil SMR backfill (CRITICAL).**
The floor pins PtX H₂ *consumption* to baseline but says nothing about where the H₂
comes from. At 2035 RFNBO_CR cuts grid electrolysis from 339 → 159 TWh and replaces it
with **+202 TWh of SMR** (516 → 718 TWh). The model "complies" with RFNBO rules by
producing *less* RFNBO hydrogen. This inverts the expected 2035 signal (−52 %
electrolyser capacity, −1.2 % cost, +4 % CO₂) and is precisely the unfair-comparison
failure mode the floor was designed to prevent — just one level up the supply chain.

H₂ production by route (TWh):

| Route | 2030 base/CR | 2035 base/CR | 2040 base/CR | 2050 base/CR |
|---|---|---|---|---|
| H2 Electrolysis | 0 / 0 | 339 / **159** | 1287 / 1154 | 1702 / 1696 |
| vre H2 Electrolysis | 0 / 0 | 0 / 0 | 3 / **160** | 169 / 224 |
| SMR | 792 / 790 | 516 / **718** | 52 / 36 | 9 / 0 |

**E3 — Additionality blind to the existing fleet and energy content.**
Three compounding formulation gaps (cf. C9): only **extendable** electrolyser capacity is
subtracted (by 2050, GB has 211 GW of fleet and ~0 GW extendable → constraint trivially
satisfied); the subtraction is 1:1 in GW (1 GW ely offsets 1 GW VRE, though the VRE
delivers ~2–5× less energy per GW); and the VRE pool includes hydro and all national
extendable VRE, not capacity attributable to H₂ production.

**E4 — The exempt direct-connection chain (`vre H2 Electrolysis`).**
Same capex/efficiency as grid VRE + electrolysis, exempt from every RFNBO constraint by
carrier naming. Once temporal correlation starts to bite (2040+) RFNBO_CR routes
~12 % of H₂ through it (+157 TWh vs baseline at 2040). Defensible physically (dedicated
line = inherently compliant), but it caps the measurable cost of *any* RFNBO rule at
roughly the system value of grid connectivity, and it is currently invisible in the
results reporting.

**Secondary dampeners:** (i) e-fuel volumes pinned by the floor mean demand-side, CO₂
and fuel-mix metrics *cannot* differ by construction — the entire policy effect is
squeezed into supply-side cost/capacity channels; (ii) CO₂ budgets (baseline) vs CO₂
prices (RFNBO) confound the comparison (2035 emissions +4 %, electricity prices
60.7 vs 79.2 €/MWh at 2045); (iii) electrolyser flexibility + a national-pool temporal
constraint make hourly matching cheap (no PPA pairing).

### 6.5 Plausibility flags

1. **2030 SMR→Sabatier loop (515 TWh):** baseline 2030 converts methane → H₂ (SMR,
   792 TWh) and H₂ → methane (Sabatier, 515 TWh) simultaneously — a thermodynamically
   circular flow, presumably a CO₂-accounting / gas-price artifact of the national-budget
   formulation. It inflates the 2030 H₂ floors (Sabatier floor = 515 TWh!) and distorts
   the 2030 H₂ system in all scenarios. **Investigate before the 2H run.**
2. **Zero electrolysis at 2030** in all scenarios — check whether exogenous H₂/e-fuel
   demand assumptions and CO₂ budgets are consistent with the study's 2030 policy focus
   (RED III targets assume substantial 2030 RFNBO volumes).
3. **offwind-float curtailment collapse** at 2045 in RFNBO_CR (9.2 → 0.4 %) — dispatch
   artifact worth a look.
4. **Haber-Bosch floor constant at 68.7 TWh** across all horizons — exogenous NH₃ demand,
   fine, but confirm intended.
5. 2050 Sabatier floor ≈ 0 (389 MWh) — vacuous constraint, harmless.

---

## 7. Recommendations to obtain significant results

Ordered by expected impact on the study's core question (CO₂ / cost / capacity impact of
RFNBO certification rules):

**R1 — Make the floor (or a companion constraint) route-specific. CRITICAL.**
Pin **electrolytic** H₂ production, not PtX consumption: e.g. per-horizon
`Σ (H2 Electrolysis + vre H2 Electrolysis) output ≥ baseline electrolytic output`, or
equivalently cap SMR/SMR-CC output at its baseline level in RFNBO scenarios.
Alternatively (closer to the regulation): enable the existing
`RFNBO_demand_share` constraint with RED III trajectories (42 % of industrial H₂ RFNBO
by 2030, 60 % by 2035) so a minimum RFNBO volume is binding by construction. Without
R1, every RFNBO scenario can dodge the rules by substituting fossil H₂ and the
2030–2035 comparison is meaningless.

**R2 — Fix the 2030 hydrogen economy.** Resolve the SMR→Sabatier loop (§6.5.1) and
revisit 2030 CO₂ budgets / gas prices so that some electrolysis exists at 2030 (as RED
III presumes). Options: enforce R1's RFNBO share from 2030, add an SMR CO₂ intensity
or CCS requirement, or simply accept and document that the modelled cost-optimal 2030
has no electrolysis — in which case the study's RFNBO story starts at 2035.

**R3 — Repair additionality (C9 upgraded to blocking).** Compare full capacity stocks
(fixed + extendable) of the post-2025 cohort on both sides, not extendable-only; and
subtract electrolyser capacity in energy-equivalent terms (× CF ratio) or move to an
energy-based additionality formulation. Otherwise additionality stays decorative in
every horizon after the first one in which electrolysers are built.

**R4 — Decide and report the direct-connection chain.** Either (a) include
`vre H2 Electrolysis` in the constrained set and drop the dedicated sub-network, (b)
keep the exemption but add the realistic extra costs of an off-grid island system
(oversized storage already optional — check it is costed), or at minimum (c) report its
share of H₂ separately in all results so the "compliant-by-construction" volume is
visible. Currently it silently absorbs the policy pressure.

**R5 — Tighten temporal correlation toward PPA reality.** Restrict the eligible VRE
pool per country to a contracted subset (e.g. introduce a PPA capacity variable per
electrolyser node, LHS = contracted VRE dispatch only). The national post-2025 pool with
baseline-dispatch netting is 3–10× electrolyser capacity even in 2050 — hourly matching
is nearly free, which understates the cost of the hourly rule found in the literature.

**R6 — Disentangle the carbon-policy instrument from the RFNBO effect.** Either run a
baseline variant under the same national CO₂ *prices* used by RFNBO_CR (preferred: then
baseline vs RFNBO differs *only* in RFNBO rules), or report all comparisons in
"incl-CO₂-payment" terms with the instrument difference flagged. The current
budget-vs-price asymmetry produces artifacts (2035 emissions +4 %, divergent electricity
prices) that are easily mistaken for RFNBO effects.

**R7 — Report policy-resolution metrics, not just system totals.** At ±2–3 % of a
~1 000 bn €/yr system, the signal drowns in totals. Add to the post-processing:
€/MWh_H₂ production-cost premium by scenario/horizon/country, electrolyser capacity
factors, marginal H₂ prices, share of H₂ by route (grid / direct / SMR), curtailment,
and the bindingness statistics of §6.3 (the temporal-correlation duals can be captured
at solve time via `n.model.constraints` before the model is discarded — worth adding,
since saved networks contain no duals for custom constraints).

**R8 — Operational for the 2H full run.** `max_growth` limits to avoid 0→550 GW
electrolyser jumps between myopic steps; capture custom-constraint duals (R7); keep
exemption criteria disabled until origin fixes are merged and re-validated; mind the
Gurobi large-bounds/large-RHS warnings (consider `gurobi-numeric-focus` at 2H).

**Bottom line:** the implementation is sound, but as configured the model has cheap
legal (E3, E4) and illegal-in-spirit (E2) compliance routes, and the 2030 horizon has no
hydrogen economy to regulate (E1). R1 + R3 are the minimum changes for the study to
produce significant, interpretable RFNBO impacts; R6 + R7 are what will make them
publishable.
