# CRP HEL and Wetland Screening Tool (Prototype)

## The Problem

Farmers and USDA conservationists face a critical challenge: **identifying which land qualifies for conservation programs requires time-consuming manual analysis or expensive field visits.**

**Current Workflow:**
- Field assessments must follow NRCS Part 616 standards for Highly Erodible Land (HEL) determination
- Manual soil analysis across multiple USDA databases
- Coordination with NRCS field inspectors (weeks of waiting)
- Limited visibility into eligibility BEFORE applying to CRP

**Why It Matters:**
Conservation programs like the Conservation Reserve Program (CRP) provide critical financial support for farmers implementing soil conservation practices. But without quick, preliminary screening, farmers can't assess eligibility before investing time in applications.

---

## The Solution

This tool provides **preliminary HEL and wetland screening** using official NRCS Part 616 methodology and real-time USDA soil data, enabling farmers and conservationists to:
- Identify potential conservation-eligible land in minutes
- Plan field assessments confidently
- Prioritize which fields to submit to CRP
- Generate baseline data for NRCS verification

**Status:** 🟢 **v18 (May 26, 2026)** — Core HEL/PHEL determination ✅ (~83% complete). Two operating modes live (Farmer Mode for quick screening, Conservationist Mode for technical detail). Official NRCS-CPA-026e and AD-1026 forms auto-fill ready. Acreage calculation validated at 909.4 acres against real SSURGO data. See **[Master Reference Guide](CRP_HEL_Tool_Master_Reference.pdf)** for comprehensive documentation.

---

## Quick Start

1. **Draw a field polygon** on the interactive map OR enter bounding coordinates
2. **Click "Analyze"** to query USDA soil data
3. **View results** — HEL status, erosion index, and conservation practice recommendations
4. **Share with NRCS** — Results support field verification and CRP applications

---

## Two Operating Modes

### 🚜 Farmer Mode
**For:** Farmers, landowners, FSA agents

**Features:**
- Simple traffic-light wetland signal (Strong / Possible / Unlikely)
- Plain-English HEL result: "Likely HEL" or "Not Likely HEL"
- Total field acreage
- Dominant soil types summary card
- **Download AD-1026 FSA compliance certification form** (pre-filled, ready for signature)

### 🔬 Conservationist Mode
**For:** NRCS conservationists, agronomists, field technicians

**Features (5 tabs):**
- **Tab 1 — HEL Summary:** Full RUSLE2 parameters (R, K, LS, T, EI) per soil component; HEL/PHEL/NOT HEL status with EI min/max range; A/B/C confidence indicator
- **Tab 2 — Wetland Analysis:** Full four-indicator table (hydric soils, vegetation, hydrology, proximity); two-tier signal (Strong = hydric + poor drainage; Possible = hydric only)
- **Tab 3 — Soil Components:** Detailed table with K-factor, T-factor, slope range, hydric rating, drainage class, acreage per map unit
- **Tab 4 — NRCS-CPA-026e Form:** **Download official NRCS HEL/Wetland Determination form** (Section I + II pre-filled with RUSLE2 data, county/state auto-filled via reverse geocoding)
- **Tab 5 — Field Verification:** Manually override R, K, LS, T values and recalculate EI in real time (for on-site adjustments)

---

## Official Forms Auto-Filled

### NRCS-CPA-026e — HEL & Wetland Conservation Determination
**For:** NRCS conservationists to document field determination

**Auto-filled fields:**
- County, State (from Nominatim reverse geocoding)
- Request Date
- Section I: HEL table (soil name, HEL Y/N, EI value, acres per soil)
- Section II: Wetland table (indicator results, acres)
- RUSLE2 parameters (R, K, LS, T, EI)
- Footer note: acreage method used (SSURGO intersection vs. polygon estimate)

**Requires:** NRCS staff signature and field verification before official submission

### AD-1026 FSA — HELC and Wetland Conservation Certification
**For:** Farmers to certify compliance with USDA conservation rules

**Auto-filled fields:**
- Producer information
- HEL status from tool calculation
- Wetland status from tool calculation
- Date

**Requires:** Producer signature; filed with local FSA office

---

## How It Works

1. **User Input:** Draw a field polygon on the map or enter bounding coordinates
2. **R-Factor Determination:** NOAA CONUS 800m raster (±1–3% error) → NOAA CDO API (±5–8%) → State-level FOTG (±20–30%) → National default (R=100, last resort)
3. **Soil Data:** Query SSURGO for all soil components intersecting the polygon; major components only (majcompflag='yes')
4. **LS Factor:** Fetch real 30m DEM from USGS 3DEP (py3dep). Calculate true LS = L × S using DEM-derived slope steepness (S) and flow-accumulation slope length (L). Falls back to Slope^1.2 × 0.1 approximation if DEM fetch fails.
5. **HEL Calculation:** Compute Erosion Index (EI) per soil component using RUSLE2 formula: **EI = (R × K × LS) / T**
6. **HEL Determination:** 
   - **HEL:** EI_min ≥ 8.0 (entire field qualifies)
   - **PHEL:** EI_min < 8.0 AND EI_max ≥ 8.0 (slope range straddles threshold — requires NRCS field visit)
   - **NOT HEL:** EI_max < 8.0 (does not qualify)
7. **Acreage Calculation:** SSURGO mupolygon intersection (same method as NRCS GIS tools) → Fallback to polygon ÷ component proportions
8. **Wetland Indicators:** Four-tier check:
   - Hydric soils (SSURGO hydricrating)
   - Hydrophytic vegetation (USGS NLCD 2021)
   - Wetland hydrology (SSURGO drainage class)
   - Proximity to water bodies (FWS NWI)

---

## Formula & Methodology

### Erosion Index (EI) Formula

```
EI = (R × K × LS) / T
```

**Where:**
- **R** = Rainfall erosivity factor (state-level from NRCS FOTG; national default R=100 if detection fails)
- **K** = Soil erodibility factor (from SSURGO kwfact)
- **LS** = Slope length & steepness — calculated from USGS 3DEP 30m DEM (true L × S formula, ±5% error). Falls back to Slope^1.2 × 0.1 (~23% error) if DEM unavailable.
- **T** = Soil loss tolerance (from SSURGO tfact)

### HEL Threshold

**EI ≥ 8.0** indicates likely Highly Erodible Land (per NRCS Part 616 and 7 CFR § 12.21)

---

## Tech Stack

### Frontend & Mapping
- **Streamlit** — Interactive web framework
- **Streamlit-Folium** — Map integration
- **Folium** — Interactive mapping (Leaflet.js-based)

### Data Processing
- **Pandas, NumPy, SciPy** — Data manipulation and analysis
- **py3dep** — USGS 3DEP 30m DEM access for LS factor calculation

### Data Sources
- **USGS 3DEP** — 3D Elevation Program 30m DEM (LS factor, ±5% error)
- **USDA SSURGO Database** — Soil Survey Geographic data via SDA API
- **NRCS FOTG** — Field Office Technical Guide (R-factor tables)
- **Nominatim** — Reverse geocoding for state detection

### Deployment
- **Render** — Production hosting

---

## Key Design Decisions

### USGS 3DEP for LS Factor
**Challenge:** SSURGO does not provide slope length data — previous approximation (Slope^1.2 × 0.1) had ~23% residual error.

**Solution:** Real 30m DEM fetched at runtime from USGS 3DEP via py3dep. True LS = L × S formula using DEM-derived slope steepness and flow-accumulation slope length.

**Improvement:** Error reduced from ~23% to ±5%.

### SSURGO for Hydric Rating (Not NWI)
**Why:** CRP targets restoration on drained farmland, not existing wetlands. SSURGO's `hydricrating` field identifies soils with wetland-forming potential (restoration candidates) — the correct signal for CP23/CP28 practices.

**Limitation:** NWI maps only existing wetlands and would miss tile-drained Prairie Pothole fields, which are prime CRP targets.

---

## Known Limitations & Remaining Work

### Current Limitations
- **NOAA CDO fallback uses single year (2023)** — Should be 22+ year average per Wischmeier & Smith 1978; causes ±10–30% variance on fallback path
- **LS flow accumulation simplified** — Not true D8 algorithm; ±15–40% error on complex terrain. Mitigation: DEM-based primary method is accurate (±5%).
- **SSURGO surface horizon only** — Top soil layer; deeper horizons not considered
- **Sodbust Y/N column not auto-populated** — Requires NRCS internal lookup (cannot determine from public data)

**Note on C-Factor & P-Factor:** These are NOT required for HEL screening per 7 CFR 610.14. They are used in conservation *planning* to evaluate specific conservation practices, not for HEL *determination* (which only uses R, K, LS, T). This tool focuses on HEL screening, not practice selection.

### Planned Enhancements (v19+)
- Multi-year NOAA R-factor averaging (reduce variance to ±5%)
- Proper D8 flow accumulation algorithm for L-factor
- NRCS Office locator (currently stub; "coming soon")
- Unit tests for EI calculation, R-factor lookup, acreage methods
- Mobile layout optimization
- *Note: C-Factor & P-Factor conservation practice planning is out-of-scope for HEL screening tool*

### See Also
**For comprehensive technical documentation, detailed compliance audit, and validation results**, see **[CRP_HEL_Tool_Master_Reference.pdf](CRP_HEL_Tool_Master_Reference.pdf)** (~18 pages, covers all sections including data sources, fallback mechanisms, regulatory alignment, etc.)

---

## Documentation & Files

### Core Application
- `crp_final_v17.py` — Main application script (v17; Streamlit app)
- `rfactor_calculator.py` — R-factor calculation module
- `wetland_features.py` — Wetland indicator detection
- `requirements.txt` — Python dependencies

### Documentation
- **[CRP_HEL_Tool_Master_Reference.pdf](CRP_HEL_Tool_Master_Reference.pdf)** — Comprehensive 18-page technical reference (formulas, data sources, fallback mechanisms, compliance audit, validation results)
- **[CRP_HEL_Wetland_Gap_Analysis_v3.md](CRP_HEL_Wetland_Gap_Analysis_v3.md)** — Current feature status & remaining gaps (May 26, 2026, ~83% complete)
- **[PHASE_3_FORM_ANALYSIS_AND_IMPLEMENTATION.md](PHASE_3_FORM_ANALYSIS_AND_IMPLEMENTATION.md)** — NRCS-CPA-026e and AD-1026 form integration details
- **[NRCS_COMPLIANCE_VERIFICATION_REPORT.md](NRCS_COMPLIANCE_VERIFICATION_REPORT.md)** — Line-by-line NRCS Part 616 compliance audit
- `README.md` — This file

---

## Contact & Discussion

For questions, feedback, or domain expert input on methodology assumptions and CP recommendations, contact **shilpigupta30@gmail.com**

---

**⚠️ Important Disclaimer:**

This tool provides **preliminary HEL and wetland screening only** using public USDA/NRCS data and open-source methodologies. It is **NOT a substitute for official NRCS HEL determination** (which requires NRCS internal data and field verification).

**Legal Notice:**
- Results are for planning and awareness purposes only
- Cannot replace NRCS Part 616 official determinations
- Does not access NASIS (NRCS internal frozen soils database)
- Field verification by NRCS staff is required before CRP application
- Do not use for regulatory compliance claims without NRCS confirmation

**Data Sources:**
All data retrieved from official federal APIs (USDA SSURGO, USGS 3DEP, NOAA, NRCS FOTG). See Master Reference Guide for data freshness, accuracy, and fallback mechanisms.
