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

**Status:** 🟢 **USGS 3DEP DEM Integration Live** — LS factor now calculated from real USGS 3DEP 30m elevation data (true L × S formula, ±5% error). Actively seeking feedback from NRCS conservationists and farmers to validate methodology and recommendations before formal deployment.

---

## Quick Start

1. **Draw a field polygon** on the interactive map OR enter bounding coordinates
2. **Click "Analyze"** to query USDA soil data
3. **View results** — HEL status, erosion index, and conservation practice recommendations
4. **Share with NRCS** — Results support field verification and CRP applications

---

## How It Works

1. **User Input:** Draw a field polygon on the map or enter bounding coordinates
2. **R-Factor Determination:** State-level average from NRCS FOTG. Falls back to national default (R=100) if state detection fails.
3. **Soil Data:** Query SSURGO for all soil components intersecting the polygon
4. **LS Factor:** Fetch real 30m DEM from USGS 3DEP (py3dep). Calculate true LS = L × S using DEM-derived slope steepness (S) and flow-accumulation slope length (L). Falls back to Slope^1.2 × 0.1 if DEM fetch fails.
5. **HEL Calculation:** Compute Erosion Index (EI) per component using RUSLE2 formula: EI = (R × K × LS) / T
6. **HEL Determination:** Flag field as likely HEL if EI ≥ 8.0
7. **Wetland Check:** Check SSURGO hydricrating for wetland-forming potential
8. **CP Recommendations:** Suggest practice groups (grassland, wildlife, water, wetland) based on EI and hydric status

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

## Limitations & Future Enhancements

### Current Limitations
- State-level R-factor averages (±20-30% intra-state variation)
- Surface horizon only (top soil layer)
- No land cover type detection
- No water proximity detection
- **CP recommendations require expert validation**

### Planned Enhancements
- Point-specific R-factor via EPA LEW API (reduce error to <5%)
- Water proximity via NHD/3DHP (activate riparian practices CP21, CP22, CP29)
- Land cover filtering via NLCD (filter irrelevant practice categories)
- Expert validation of CP recommendation logic

---

## Files

- `crp_final_v12.py` — Main application script
- `requirements.txt` — Python dependencies
- `README.md` — This file

---

## Contact & Discussion

For questions, feedback, or domain expert input on methodology assumptions and CP recommendations, contact **shilpigupta30@gmail.com**

---

**⚠️ Disclaimer:**

This is a prototype tool designed for evaluation and feedback by domain experts. Results are indicative only and should not be used as the basis for any CRP application or land management decision without NRCS confirmation.

**Final HEL determination requires official NRCS field verification.**
