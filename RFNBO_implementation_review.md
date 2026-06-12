# RFNBO Implementation Review

*Date: 2026-06-10 (initial). Last updated: 2026-06-12 (constraint-logic review §9.1–9.2;
plans §9.3–9.4; **implemented** §9.5: additionality+PPA pairing, shared exemption
filter, non-RFNBO cap, CO₂ vent).
Scope: RFNBO constraint implementation (`scripts/solve_network.py`),
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

**Second iteration (2026-06-11 PM, §8):** origin merged (incl. the GB
industrial-demand fix — **all results must be re-run**); `smr_cap_2025` implemented
(new H₂ demand growth must be electrolytic in RFNBO scenarios — closes the SMR
backfill); direct-connection chain deactivated everywhere; configurable `max_growth`
caps added; `rfnbo_metrics.csv` reporting added; SMR→Sabatier loop diagnosed as a
**capture-overflow artifact** (no CO₂ vent + 40 Mt sequestration cap → `co2 stored` at
−400 €/t pays Sabatier to absorb CO₂) — vent-link fix proposed (item 22).

**Third iteration (2026-06-12, §9):** constraint-logic review (§9.1–9.2: counterfactual
netting verified, additionality slack explained, demand-share-at-100 % analysis), then
**implemented** (§9.5): additionality now always paired with the annual-PPA volumetric
constraint (Art. 5(a)/(b), `RFNBO_rules.md`); shared exemption filter (intersection
logic) across all RFNBO constraints; `smr_cap_2025` generalized to the
`non_rfnbo_h2_cap_2025` production cap (baseline-2025 reference, 93.24 TWh);
`sector.co2_vent` enabled everywhere (kills the SMR→Sabatier disposal subsidy,
cross-border attribution verified §9.4); `latex/main.tex` updated and compiling.
**All networks must be re-prepared and re-solved.** Sankey/metrics CO₂-trade
reporting (item 29) still open.

**Still open before the full-country 2H run:**

1. **Exemption criteria (C1–C3):** origin fixes now merged (`49079156`) — re-validate
   before re-enabling. Quick test keeps criteria disabled (`c6533245`).
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
- ~~**Annual PPA constraint** redundant with hourly sum — document or remove.~~
  **Resolved 2026-06-12 (§9.5):** extracted into `add_annual_ppa_constraint`, paired
  with additionality (capacity + volume per Art. 5(a)/(b)), removed from the hourly
  temporal function.
- **CO₂ price sanity check:** no guard that dual `mu` exists and prices ≥ 0.
- ~~**Criteria-filter inconsistency (found 2026-06-12, §9.1)**~~ — **Fixed 2026-06-12
  (§9.5):** all RFNBO constraints now share `_rfnbo_active_countries` (intersection
  logic).

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
| 14 | `max_growth` limits | suggestion | **Done** (§8.2) | uncommitted |
| 15 | Endogenous H₂ demand floor | MAJOR | **Done** | `c5473c87` |
| 16 | Merge origin C1–C3 fixes into local branch | MAJOR | **Done** (2026-06-11 merge) | `49079156` |
| 17 | Pin **electrolytic** H₂, not just consumption (R1, §7) | **CRITICAL** | **Done** — `smr_cap_2025` (§8.2), superseded by item 28 (§9.5) | uncommitted |
| 18 | Additionality blind to non-extendable stock (R3, §7) | MAJOR | **Mitigated** — SMR cap closes the volume route (§8.2); formulation itself unchanged | uncommitted |
| 19 | Investigate 2030 SMR→Sabatier loop (515 TWh, §6.5) | MAJOR | **Diagnosed** — capture-overflow artifact (§8.4); fix proposed, not yet implemented | — |
| 20 | Decide direct-connection chain treatment (R4, §7) | MAJOR | **Done** — deactivated everywhere (§8.2) | uncommitted |
| 21 | Restrict temporal-correlation VRE pool (R5, §7) | suggestion | **Open** | — |
| 22 | Implement `co2 stored` vent (or raise sequestration cap) — fix for §8.4 | **CRITICAL** | **Done** (§9.5: `co2_vent: true` in all 8 configs; networks must be rebuilt) | uncommitted |
| 23 | Re-run with GB industrial-demand fix (`680b3126`, merged) | MAJOR | **Open** (re-run pending) | — |
| 24 | RED III electrolytic H₂ share for realistic 2030/2035 mix (§8.5) | MAJOR (policy realism) | **Proposed** | — |
| 25 | Rooftop solar potential unbounded (2.7 TW by 2050, §8.6) | MAJOR (plausibility) | **Open** (mitigated by max_growth) | — |
| 26 | RFNBO policy metrics post-processing (R7) | MAJOR (reporting) | **Done** (§8.2) | uncommitted |
| 27 | Align temporal/PPA exemption filter with additionality intersection logic (§9.1) | MINOR | **Done** (§9.5: shared `_rfnbo_active_countries`) | uncommitted |
| 28 | Replace `smr_cap_2025` with grandfathered non-RFNBO production cap (§9.3) | MAJOR | **Done** (§9.5: `non_rfnbo_h2_cap_2025`, baseline-2025 ref 93.24 TWh verified) | uncommitted |
| 29 | CO₂ sankey + metrics: add vent and cross-border CO₂ trade flows (§9.4) | MAJOR (reporting) | **Open** | — |
| 30 | Pair additionality with annual PPA constraint (capacity + volume, Art. 5(a)/(b)) | MAJOR | **Done** (§9.5: `add_annual_ppa_constraint`) | uncommitted |

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
of slack. *(Correction 2026-06-12, see §9.1: the slack is **not** decarbonisation
overbuild counted against the requirement — the no-H₂ counterfactual RHS nets that out.
It is structural: the constraint demands 1 GW VRE per GW electrolyser while the energy
balance and the annual-PPA constraint force ≈ 2–4 GW per GW.)*

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
extendable VRE, not capacity attributable to H₂ production. *(Revisited in §9.1: with
the no-H₂ counterfactual on the RHS the netting itself is sound and the per-horizon
increments do sum to cohort additionality; the 1:1 GW unit mismatch is the operative
gap.)*

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

---

## 8. Second iteration (2026-06-11 PM): fixes implemented, loop diagnosis, remaining decisions

### 8.1 Origin merge and R2 assessment

`origin/master` merged (`49079156`). Substantive commits: `c91e8ccd` (grid CO₂-intensity
efficiency fix), `90364c9b` (exemption set union→intersection), `680b3126` (**GB
industrial demand zeroed by a Eurostat NaN bug** — fixed by falling back to 2019 data),
plus todo-file updates. **None solves the zero-electrolysis-2030 problem on its own:**
the criteria fixes only touch the (still disabled) exemption logic, and the GB fix adds
back missing demand without constraining how the H₂ that serves it is produced. The GB
fix *does* remove ~67 Mt of artificial CO₂-budget headroom that hosted a third of the
2030 SMR fleet (§8.4), so it shifts the 2030 equilibrium in the right direction —
**all results must be re-run** since `build_industrial_production_per_country` feeds
every scenario.

### 8.2 Implemented in this iteration (all uncommitted, ready for re-run)

| Change | What it does | Files |
|---|---|---|
| **`smr_cap_2025` constraint** (closes E2/R1, mitigates R3/E3) | In RFNBO scenarios from 2030, EU-wide annual SMR + SMR CC H₂ output ≤ the same run's solved **2025** level (RFNBO_CR reference: **93.2 TWh** vs the 792 TWh SMR produced at 2030 — an 8.5× tightening). All H₂ demand growth beyond 2025 must therefore be electrolytic; combined with the endogenous floor (pins PtX H₂ consumption ≥ baseline), the fossil-backfill escape route is closed. Reference network wired cleanly as a Snakemake input (`input_network_2025` in `rules/common.smk`, `network_2025` on the solve rule). | `scripts/solve_network.py`, `rules/common.smk`, `rules/solve_myopic.smk`, all RFNBO configs (`true`), baselines (`false`), `latex/main.tex` (new subsection) |
| **Direct-connection chain deactivated** (closes E4/R4) | `activate_direct_vre_connected_electrolysers: false` in all nine config locations; falls back to the standard PyPSA-Eur formulation, so **all** electrolysis is grid-connected and subject to the RFNBO constraints. Code kept (guarded), verified safe when disabled; latex notes the status. Feature history: `c6d0b4fb` → `4f1d6368` → `552a09cc`. | configs, `latex/main.tex` |
| **`max_growth` capacity caps** (R8) | `solving.constraints.max_growth` (`enable` + per-carrier GW per 5-year horizon, EU-wide, **absolute** caps — relative growth is unusable from zero stock). Custom constraint in `solve_network.py` (native PyPSA `max_growth` is a no-op without multi-investment periods — verified in pypsa 1.2.1). Defaults: solar 400, solar-hsat 150, solar rooftop 200, onwind 200, offwind-ac/dc/float 60/80/40, H2 Electrolysis 200. **Identical block in all eight configs** — required so baseline references (floor, additionality RHS) stay feasible for RFNBO runs. Caveat: in myopic mode caps *delay* deployment, they cannot pull builds forward. | `scripts/solve_network.py`, all configs, `README.md` |
| **RFNBO policy metrics** (R7) | `scripts/make_rfnbo_metrics.py` + `make_rfnbo_metrics` rule → `results/{run}/csvs/rfnbo_metrics.csv`: H₂ production by route, consumption by carrier, electrolyser capacity + CF, electrolytic share, VRE curtailment, H₂ prices (demand-weighted), and **H₂-related CO₂** (SMR emissions to atmosphere, PtX CO₂ drawn from `co2 stored`, net). Validated against existing results (2035 SMR 516 vs 718 TWh; 2040 electrolytic share 96/97 %). | `scripts/make_rfnbo_metrics.py`, `rules/postprocess.smk`, `Snakefile_quick_test_chain` |

### 8.3 What remains unconstrained (deliberate)

The VRE-capacity additionality formulation itself (extendable-only, 1:1 GW subtraction,
C9) is **unchanged**: with the SMR route capped, the *volume* escape is closed and the
electrolysers that must now be built from 2030 fall under the (binding, §6.3) temporal
correlation. Upgrading the capacity formulation (item 18) remains worthwhile but is no
longer the binding defect. The temporal-correlation pool restriction (item 21) is still
an open modelling decision.

### 8.4 SMR→Sabatier loop (515 TWh at 2030): diagnosed — capture-overflow artifact

Two-pass investigation (first hypothesis — a CO₂ accounting sign error — was **ruled
out**: `add_co2limit_country` uses the port-0 variable, `eff₂·p₀`, and the
reconstructed per-country accounting reproduces every cap to 2 decimals; all 28
countries sit exactly at their cap). The loop is **not** energy storage either: SMR and
Sabatier run simultaneously in 84–100 % of snapshots, fully co-located fleet-wise, with
H₂ store swings of only 0.3 TWh against 792 TWh throughput.

**Actual mechanism (2030, baseline):**

1. Tight national budgets force **147 Mt/yr of carbon capture** (process emissions CC
   49.8, solid biomass CC 38.2, gas industry CC 37.7, DAC 19.0 Mt).
2. The sequestration cap (40 Mt) is **binding** (μ = 406 €/t) and there is **no CO₂
   vent link** — captured CO₂ *must* be utilized.
3. `co2 stored` therefore trades at **−400 €/t** (it is +38 €/t in 2025): utilization
   is *paid*. Sabatier is the marginal sink (81.5 Mt, ≈ €33 bn/yr disposal revenue),
   which finances 515 TWh of SMR-H₂ plus the 39 % round-trip loss.
4. The SMR fleet sites in cheap-carbon countries (corr(net H₂ export, μ) = +0.48):
   GB 66.6 Mt of SMR emissions (exporting 243 TWh H₂ by pipeline), DE 44.5, RO 31.2 —
   **amplified by the GB zero-industrial-demand bug** which freed ~67 Mt of headroom.
   Combustion of the synthetic methane is still correctly charged to the burning
   country; the loop arbitrages *where* emissions occur, not whether they are counted.
5. At 2025 there are no national budgets → no forced capture → positive `co2 stored`
   price → no loop. It switches on with the 2030 budgets.

**Verdict:** an artifact of constraint design (no escape valve for captured CO₂), not of
the CO₂ accounting, and not legitimate methane storage.

**Proposed fix (item 22):** add a **`co2 stored` → `co2 atmosphere` vent link** (free or
small opex, location-resolved so the venting is charged to the right national budget).
This caps the disposal subsidy that finances the loop, while leaving genuine
utilization (FT, methanolisation) driven by fuel value. *(Audited 2026-06-12, §9.4:
use the existing upstream `sector.co2_vent` option; cross-border attribution verified;
the "≈ 0 €/t floor" claim corrected — under binding national budgets the floor is
≈ −(min μ + transport); sankey/postprocessing extensions required.)* Complements: (a) the GB fix re-run (necessary anyway); (b) the
`smr_cap_2025` constraint already kills the loop's H₂ source in RFNBO scenarios — if the
loop should also disappear from the **baseline**, either enable the cap there too
(changes the reference system) or rely on the vent + GB fix; (c) optionally raise the
2030 sequestration cap if 40 Mt is judged too conservative; (d) an EU-wide (ETS-like)
budget for traded sectors would remove the μ-spread (82–667 €/t) powering the spatial
arbitrage, but that is a study-design decision, not a bug fix.

### 8.5 Realistic 2030/2035 production mix (R2 follow-through)

With the GB fix, the vent link, the SMR cap and max_growth in place, the RFNBO scenarios
will build electrolysers from 2030 by construction. If the **baseline** should also show
a policy-realistic mix (rather than pure cost optimality), enable the existing
`add_RFNBO_demand_share_constraint` (carriers fixed in `5223c89f`, flag
`RFNBO_demand_share`, currently `false` everywhere) **in all scenarios** with the RED
III trajectory — electrolytic share of H₂ supply ≥ **42 % in 2030, 60 % from 2035**
(values via the existing `RFNBO_demand_shares` config mapping). This is EU law
independent of the certification details under study and is the cleanest way to anchor
2030/2035 volumes; it also keeps baseline and RFNBO comparable (both serve the same
mandated electrolytic volume, the scenarios differ only in *how* it must be produced).
Decision pending — not yet enabled.

### 8.6 New plausibility flag: unbounded rooftop solar

Baseline rooftop solar grows 39 → 619 → 1 270 → 2 181 → 2 701 → **2 767 GW** (2050),
with per-step builds up to **+912 GW** — far beyond realistic EU rooftop potential
(~1 TW estimates). The max_growth cap (200 GW/step) now binds hard on this carrier and
will shift the mix toward utility solar/onwind (aggregate headroom verified: worst
observed step needs ~960 GW total VRE vs 1 130 GW summed caps). The root cause —
missing/ineffective rooftop potential limit at 33 clusters — should be checked
separately (item 25).

### 8.7 Pre-flight for the next run

1. Re-run **all three quick-chain scenarios** (GB fix invalidates current results):
   baseline → baseline_without_H2 → RFNBO_CR, 33 clusters, 6H.
2. Verify in logs: `smr_cap_2025` added at 2030+ with reference ≈ 2025 SMR output;
   `max_growth-*` constraints present; no direct-connection (`* vre`) components built.
3. Verify in `rfnbo_metrics.csv`: 2030/2035 electrolytic share > 0 in RFNBO_CR and
   rising vs baseline; SMR ≤ cap; Sabatier H₂ input plausible (loop gone or shrunk if
   the vent link is implemented).
4. Watch for infeasibilities from the new constraint stack (floor + SMR cap +
   max_growth): the floor references the re-run baseline, which itself respects the
   same caps, so the stack is consistent by construction — but the 2030 RFNBO horizon
   now has to build electrolysers under temporal correlation; if infeasible, check the
   H2 Electrolysis growth cap (200 GW) against the floor-implied electrolysis volume.

---

## 9. Constraint-logic review (2026-06-12): counterfactual netting, additionality slack, `RFNBO_demand_share` vs SMR cap

### 9.1 Does the no-H₂ counterfactual make additionality slack-free? (verification)

Question examined: the additionality RHS is the **no-H₂ counterfactual**
(`input_baseline_updated` → `results/baseline_without_H2/...`, `rules/common.smk`
l. 247), so the counterfactual VRE covers general decarbonisation and any VRE built on
top of it in the RFNBO run is attributable to H₂ — in theory leaving no
"decarbonisation-overbuild" slack. Verified against the implementation:

- **The netting is implemented as intended.** Per country and horizon, additionality
  reads `ΔVRE_cap ≥ Ely_cap` with `ΔVRE_cap` = (extendable VRE `p_nom`, RFNBO run) −
  (extendable VRE `p_nom_opt`, no-H₂ counterfactual, same horizon and wildcards). The
  hourly temporal and annual-PPA constraints apply the **same netting in energy**:
  (new-cohort VRE dispatch) − (counterfactual new-cohort VRE dispatch) ≥ electrolyser
  consumption. Identical `max_growth` caps across configs keep the counterfactual RHS
  feasible. The earlier §6.3 explanation ("slack because the system overbuilds VRE for
  decarbonisation anyway") was imprecise and has been corrected: that overbuild sits in
  the RHS and is netted out.
- **Where the observed slack (e.g. GB +195 GW at 2040) actually comes from:**
  1. *Unit mismatch (dominant).* 1 GW of electrolyser (CF ≈ 0.5–0.6) needs ≈ 2–4 GW of
     VRE (CF ≈ 0.15–0.4) energetically — and the annual-PPA constraint enforces exactly
     that energy requirement on the same netted quantities. Wherever PPA holds, the
     1:1 GW capacity constraint is over-fulfilled by construction: structural slack
     ≈ 1–3 × electrolyser GW, consistent with the observed magnitudes.
  2. *Trajectory divergence.* The RFNBO run and the counterfactual differ in more than
     H₂ (CO₂ budgets vs prices, divergent inherited brownfield fleets, counterfactual
     demand adjustments), so `ΔVRE` also absorbs non-H₂ system differences, in either
     direction.
  3. *Per-horizon increments.* Both sides count only current-horizon extendable builds.
     Summed over horizons this **does** enforce cumulative post-2025-cohort
     additionality (each increment is constrained once; consistent with regulatory
     grandfathering), so the incremental form is not in itself the flaw C9 suggested —
     but it degenerates at horizons with no extendable electrolysers left (the 2050 GB
     artifact, E3).
- **Is capacity additionality redundant given annual PPA?** Almost, not strictly: PPA
  nets counterfactual *dispatch*, so its energy requirement could in principle be met
  by curtailment reduction on capacity that exists anyway; the capacity constraint
  forbids that one route. Empirically it never binds where PPA is active. In
  **RFNBO_Add** (additionality without temporal/PPA) it is the only supply-side RFNBO
  constraint, and in 1:1 GW form it still lets electrolysers draw the CF-gap
  (~60–75 %) of their energy from non-additional sources — the energy-based
  reformulation (R3) is what would make it meaningful standalone.
- **Checked consistent:** RHS network identity and horizon alignment, electrolyser
  country grouping (`bus0`), identical VRE carrier set on both sides. **New minor
  finding:** the temporal/PPA exemption filter checks `activate_vre_share_criterion`
  only, while additionality uses the intersection of both criteria (§2.3, item 27) —
  moot while criteria are disabled.

**Judgement update.** Within their domain the constraints are well formed and the
counterfactual netting works exactly as designed; the additionality slack is a
*finding about the policy* — capacity additionality is nearly costless when the
marginal electricity source is new VRE anyway — compounded by the 1:1 GW unit mismatch
(R3), not a netting bug. None of this changes the answer to "is the SMR cap redundant
given additionality + annual PPA?": **no** — both constraints are conditional on
electrolysers (they bound the *quality* of electrolytic H₂, never its *quantity* in the
supply mix), so a zero-electrolyser, all-SMR solution satisfies them trivially (E2).
Volume must be forced separately (SMR cap, or a demand share, §9.2).

### 9.2 Could `RFNBO_demand_share` at 100 % replace `smr_cap_2025`?

Implementation audit (`add_RFNBO_demand_share_constraint`, `solve_network.py`
l. 2702): one EU-wide annual constraint,
`Σ electrolytic H₂ output (H2 Electrolysis + vre H2 Electrolysis) ≥ level × D`,
with `D` = exogenous H₂ loads (industry, shipping, land-transport FC) + H₂ input of
the hard-coded link list (methanolisation, H2 turbine, H2 Fuel Cell, Sabatier,
Fischer-Tropsch, Haber-Bosch at bus2). Cross-checked on the solved 2035 full-Europe
baseline: the consumer list is **complete in the current configuration** — covered
consumption 855.4 TWh equals total production 855.4 TWh (electrolysis 339.1 + SMR
516.3; pipelines lossless, store cycling ≈ 0 net, ammonia cracker at 0). So at
`level = 1.0` the constraint forces electrolysis ≥ total H₂ consumption, i.e. SMR +
SMR CC (+ ammonia cracker) annual output ≈ 0.

**Functionally, yes** — a 100 % share is *strictly tighter* than the SMR cap and also
closes the ammonia-cracker import route (currently unused) which `smr_cap_2025` does
not cover. Three reasons to keep the SMR cap nevertheless:

1. **Semantics.** 100 % abolishes the *grandfathered* 2025-level SMR (93 TWh) serving
   pre-existing demand. The study question — all H₂ consumption **growth** vs the
   counterfactual is RFNBO — is exactly the cap's semantics; "100 % electrolytic from
   2030" goes beyond it and beyond RED III (42 %/60 % of industrial H₂).
2. **No exact tuning.** Reproducing the cap as a share needs
   `level(h) = 1 − SMR₂₀₂₅ / D(h)`, but `D(h)` is endogenous (the PtX link
   consumptions are decision variables) — only approximable ex ante.
3. **Robustness.** The consumer list is hard-coded (unlike the endogenous floor, which
   discovers consumers structurally); any H₂ consumer introduced by configuration
   (H₂ OCGT/CHP, H₂ liquefaction, …) silently shrinks `D` and reopens a proportional
   SMR niche precisely when `level` is high. Audit the list per config before relying
   on it, or port the floor's structural discovery.

**Proper division of labour:** `smr_cap_2025` as the growth-must-be-electrolytic
instrument in RFNBO scenarios; `RFNBO_demand_share` with RED III trajectories
(42 %/60 %) as the policy-realism anchor in *all* scenarios (§8.5 proposal,
unchanged). A 100 % share becomes the right instrument only if the study question
changes to "fully electrolytic H₂ from 2030" — after replacing the hard-coded consumer
list with structural discovery.

**Superseded same day:** a *grandfathered non-RFNBO production cap* resolves all three
objections while keeping the cap's growth-only semantics — adopted, implementation
plan in §9.3.

### 9.3 Decision (2026-06-12): replace `smr_cap_2025` by a grandfathered non-RFNBO production cap — implementation plan

**Adopted design.** Drop the SMR-specific cap and constrain instead, in RFNBO runs from
2030, the EU-wide annual output of **all non-RFNBO H₂ production routes** to its
**baseline-2025** level:

> Σ_links∈P Σ_t w_t · eff · p ≤ NonRFNBO₂₀₂₅(baseline),
> P = links injecting into a carrier-`H2` bus, *excluding* the electrolyser carriers
> (`H2 Electrolysis`, `vre H2 Electrolysis`).

Combined with the (optional, §8.5) exogenous `RFNBO_demand_share`, the effective
electrolytic share is automatically
`max( level(h), 1 − NonRFNBO₂₀₂₅ / D(h) )` — both constraints simply coexist as
inequalities; no tuning needed. The cap-implied share rises toward 100 % as H₂ demand
grows, which is the intended "all consumption growth is RFNBO" semantics.

**Why this resolves the §9.2 objections:**

1. *Semantics:* grandfathering preserved — the 2025 non-RFNBO quantity (≈ 93 TWh SMR)
   may persist; only growth must be RFNBO. No overshoot beyond the study question.
2. *Tuning:* the cap is a direct quantity, not a share — the endogenous-denominator
   problem disappears (the share-equivalent adjusts itself with D(h)).
3. *Robustness:* production-side with **structural discovery** (mirror of the floor's
   `_find_h2_consuming_link_ports`, l. 2356) — no hard-coded consumer or producer
   list. Covers `ammonia cracker` (closing the NH₃-import route the SMR cap missed)
   and any future non-electrolytic producer automatically. Behaves identically to
   `smr_cap_2025` while the cracker is at 0 (current runs).

**Reference network change** (per decision): baseline 2025 instead of the same run's
2025. The two are near-identical (at 2025 only the vacuous monthly constraint differs
between RFNBO and baseline), but the baseline reference makes the cap **identical
across all RFNBO variants** and independent of each variant's own 2025 solve.

**Implementation steps:**

1. `scripts/solve_network.py`:
   - Add `_find_h2_producing_link_ports(network)` mirroring
     `_find_h2_consuming_link_ports`: same single-H₂-port structural filter
     (excludes pipelines), but select ports k ≥ 1 on a carrier-`H2` bus with
     **positive** `efficiency{k}` (production; Haber-Bosch's H₂ port has
     `efficiency2 < 0` = consumption and is correctly excluded). Drop links with
     carrier in `["H2 Electrolysis", "vre H2 Electrolysis"]`.
   - Add `_non_rfnbo_h2_output_mwh(ref_net)`: evaluate the same structural set on the
     solved reference dispatch (`w @ (p0 · eff)` per port).
   - Replace `add_smr_cap_constraint` with `add_non_rfnbo_h2_cap_constraint`:
     reference from `snakemake.input.baseline_network_2025`; keep the existing skip
     guards (no producers / reference < 1 MWh) and log the reference TWh **and the
     covered carrier list** (audit trail). Remove `SMR_CARRIERS` and
     `_smr_h2_output_mwh`.
   - `extra_functionality`: rename the flag block `smr_cap_2025` →
     `non_rfnbo_h2_cap_2025` (activation unchanged: `RFNBO*` runs, horizon ≥ 2030).
2. Workflow: in `rules/common.smk` repoint/rename `input_network_2025` →
   `input_baseline_network_2025` returning
   `results/baseline/networks/base_s_{clusters}_{opts}_{sector_opts}_2025.nc`;
   rename the `solve_sector_network_myopic` input accordingly
   (`rules/solve_myopic.smk` l. 112). No run-order change: the full baseline chain is
   already a prerequisite of every RFNBO solve (floor, CO₂ prices).
3. Configs (all 8): rename `smr_cap_2025` → `non_rfnbo_h2_cap_2025` (`true` in RFNBO
   configs, `false` in baselines).
4. If/when `RFNBO_demand_share` is enabled (§8.5): first port the structural consumer
   discovery to its denominator (§9.2 robustness), and replace the placeholder
   `solving.constraints.share` trajectory (currently 0.2→0.8) with RED III values
   (2030: 0.42, 2035+: 0.60).
5. `scripts/make_rfnbo_metrics.py`: generalize `H2_PRODUCTION_SMR_CARRIERS` (l. 33) to
   a non-RFNBO production aggregate (SMR, SMR CC, ammonia cracker, …) and report cap
   utilisation per horizon.
6. `latex/main.tex`: rewrite the SMR-cap subsection (rename, baseline-2025 reference,
   structural producer set, max-of-shares interaction with the demand share).
7. Validation on the quick-test chain re-run: log line
   `non-RFNBO H2 cap: X TWh (baseline 2025)` at every RFNBO solve ≥ 2030 with
   X ≈ 93 TWh (confirm against the re-run baseline); `rfnbo_metrics.csv` shows
   SMR + SMR CC + cracker ≤ cap at all horizons and no 2035 fossil backfill (E2 gone).

### 9.4 CO₂ vent (item 22): plan audit, cross-border accounting, postprocessing

Audit of the §8.4 proposed fix against the code (2026-06-12):

- **No custom link needed.** Upstream PyPSA-Eur already ships the vent:
  `sector.co2_vent: true` adds per-node extendable links `{node} co2 vent`
  (`{node} co2 stored` → `co2 atmosphere`, efficiency 1, free) in
  `prepare_sector_network.py` l. 863 — nodal because `co2_spatial: true`. Currently
  `false` in all configs. **Plan: set `true` in all eight configs** (baselines
  included — the SMR→Sabatier loop lives in the baseline). Optionally add a small
  `marginal_cost` to break degeneracy; note the vent is created at *prepare* time, so
  networks must be rebuilt, not just re-solved.
- **National attribution verified correct** (code path read in both
  `add_co2limit_country` and `add_co2price_country`): the vent's atmosphere port
  (bus1, bus carrier `co2`) enters the per-country emission LHS; since
  `co2 atmosphere` has `location="EU"`, the country mapping falls through to the
  link-name prefix fallback (l. 1500–1501), which resolves `"BE1 0 co2 vent"` → `BE`,
  i.e. the **venting country**. CO₂ pipelines connect carrier-`co2 stored` buses —
  no port on carrier `co2` — so transport is accounting-neutral; capture (CC links,
  DAC) stays attributed to the origin country, where it reduces atmospheric
  emissions. Net effect of *capture in A → pipeline → vent in B*: A's territorial
  emissions fall by the captured amount (a CO₂ **export**), B's rise by the vented
  amount (**import + emission**) — exactly the intended territorial semantics, in
  both the budget and the price formulation. The EU-wide cap (the `co2 atmosphere`
  store) also counts vented CO₂, so venting cannot escape the EU budget.
- **Accuracy correction to §8.4:** the claim that the vent "restores the economic
  floor of `co2 stored` to ≈ 0 €/t" holds only where venting is unpriced (e.g. 2025).
  With all 28 national budgets binding at 2030 (μ = 82–667 €/t), venting costs the
  local μ: the floor becomes ≈ −(min_c μ_c + CO₂ transport) ≈ −82 €/t, not 0. The
  vent shrinks the forced-utilization subsidy ~5× but does not eliminate it, and it
  creates a **vent-tourism** incentive (pipe CO₂ to the cheapest-μ country to vent —
  the same spatial arbitrage as the SMR siting in §8.4). Acceptable for the study
  *provided it is visible in reporting* (below). The complementary measures of §8.4
  stand (GB-fix re-run; the §9.3 cap kills the loop's H₂ source in RFNBO runs;
  optionally raise the sequestration cap or move to an EU-wide traded-sector budget).
- **Postprocessing is currently blind to both venting and CO₂ trade — required
  extensions:**
  1. **SEPIA carbon sankey** (`SEPIA/excel_generator.py`, `prepare_emissions`): no
     `co2 vent` entry exists → vented CO₂ would be invisible and per-country sankeys
     unbalanced at the `co2 stored` node. Add a vent entry
     (`co2 stored` → `co2 atmosphere`, per-country via the link-name filter as for
     the other technologies).
  2. Same function has no cross-border CO₂-pipeline entry: per-country sankeys
     already mis-balance whenever CO₂ captured in one country is utilized, sequestered
     or (now) vented in another. Add per-country **net CO₂ import/export** flows: sum
     `CO2 pipeline` dispatch over links whose bus0/bus1 countries differ, signed
     toward the reporting country, rendered as `CO₂ import`/`CO₂ export` nodes
     attached to `co2 stored` (nets to ≈ 0 in the EU aggregate).
  3. Update the sankey balance checks (`scripts/check_sankey_balance.py`,
     `SEPIA/sankey_balance.py`) to include both new flows.
  4. `scripts/make_rfnbo_metrics.py`: add per-country vented tonnes and net CO₂
     pipeline imports (the H₂-related-CO₂ block already reads `co2 stored` draws).
  5. Validation: per country,
     capture − utilization − sequestration − venting ± pipeline net = 0 on
     `co2 stored`; EU vent total = captured CO₂ − (sequestration + utilization);
     `co2 stored` marginal price ≥ −(min_c μ_c + transport) in all snapshots.

### 9.5 Implementation status (2026-06-12 PM): §9.3/§9.4 + constraint pairing implemented

All implemented via delegated agents and verified; uncommitted. **Networks must be
re-prepared and all scenarios re-solved** (the vent acts at prepare time; the cap and
pairing at solve time).

1. **Additionality ⇄ annual PPA pairing (item 30).** Per the legal reading in
   `RFNBO_rules.md` §2 (Delegated Regulation 2023/1184, Art. 5(a)/(b): capacity-based
   additionality with volumetric PPA equivalence), the annual-PPA constraint was
   extracted from `add_temporal_correlation_constraint` into a standalone
   `add_annual_ppa_constraint` (identical formulation, 2025 cohort, counterfactual
   netting) and is now activated in `extra_functionality` immediately after every
   `add_additionality_constraint` call — capacity and volume always appear together.
   The hourly temporal branch no longer adds the annual constraint (the hourly sums
   imply it); `RFNBO_Temp` stays hourly-only, `RFNBO_Add` now carries the volumetric
   constraint that §9.1 identified as the binding half of the pair.
2. **Shared exemption filter (item 27).** `_rfnbo_active_countries` implements the
   four-branch criteria logic (intersection of VRE-share and carbon-intensity
   non-compliant sets when both active) once; additionality, annual PPA, hourly and
   monthly temporal all call it. No behavioural change while criteria stay disabled.
3. **Non-RFNBO production cap (item 28).** `add_non_rfnbo_h2_cap_constraint` replaces
   `add_smr_cap_constraint` exactly per the §9.3 plan: structural producer discovery
   (`_find_h2_producing_link_ports`, ports ≥ 1 on carrier-`H2` buses with positive
   efficiency, electrolyser carriers excluded), EU-wide annual cap at the **baseline
   2025** reference (`input_baseline_network_2025` in `rules/common.smk`). Config flag
   renamed in all 8 configs + `scenarios.quick_test_chain.yaml`;
   `make_rfnbo_metrics.py` gained a `non-RFNBO` production aggregate (SMR, SMR CC,
   ammonia cracker). **Empirically verified** on the solved baseline 2025 network:
   reference = 93.24 TWh (SMR 93.237, ammonia cracker 0.0) — identical to the old
   SMR-cap reference, so current-run behaviour is unchanged while the cracker route
   is closed going forward.
4. **CO₂ vent (item 22).** `sector.co2_vent: true` in all 8 scenario configs
   (`config.default.yaml` untouched). Attribution semantics as verified in §9.4.
   The postprocessing extensions (sankey vent + CO₂ trade flows, metrics) remain
   open as item 29.
5. **`latex/main.tex`** updated for all four changes (additionality as capacity+volume
   pair with CF-ratio discussion, intersection exemption logic, non-RFNBO cap
   subsection replacing SMR cap, CO₂-venting subsection with cross-border
   export/emission accounting); compiles clean, 12 pp.

Both Python scripts pass syntax/lint checks; repo-wide grep confirms no stale
references to `smr_cap_2025` / `SMR_CARRIERS` / `input_network_2025` outside the
historical sections of this file.
