# Two-Tier UI System: Visual Mockups & User Flows
## CRP HEL Screening Tool v16

---

## SECTION 1: USER FLOW DIAGRAMS

### 1.1 Farmer User Flow

```
START: Farmer accesses tool
    │
    ▼
Enter field location
    ├─ Draw polygon on map
    ├─ Enter coordinates manually
    └─ Upload boundary file
    │
    ▼
[DEFAULT] Farmer View Displayed
    │
    ├─ HEL Status Badge
    │  ├─ ✅ LIKELY (EI ≥ 8.0)
    │  ├─ ⚠️  MAYBE (EI 6.0-8.0)
    │  └─ ❌ UNLIKELY (EI < 6.0)
    │
    ├─ Wetland Status
    │  ├─ 💧 Wetland soils detected
    │  └─ No wetland indicators
    │
    ├─ Next Steps
    │  └─ Always: "Contact NRCS"
    │
    └─ Action Buttons
       ├─ 📍 Find My NRCS Office
       │  │
       │  └─► [NRCS Locator Map]
       │       ├─ Office name, address
       │       ├─ Phone & email
       │       └─ Hours
       │
       ├─ 🖨️ Print Results
       │  └─► PDF: 1-page summary
       │
       └─ 🔗 Share with NRCS
          └─► Shareable URL (results embedded)
```

### 1.2 Conservationist User Flow

```
START: Conservationist accesses tool
    │
    ▼
Enter field location
    ├─ Draw polygon on map
    ├─ Enter coordinates manually
    └─ Upload boundary file
    │
    ▼
Click "🔬 Conservationist Mode" (sidebar)
    │
    ▼
Conservationist View Displayed (6 tabs)
    │
    ├─ TAB 1: Results Overview
    │  ├─ EI metrics (max, avg)
    │  ├─ HEL status with methodology
    │  ├─ Confidence level (A, B, C flags)
    │  └─ Comparison button → Tab 2
    │
    ├─ TAB 2: Field Verification
    │  ├─ Field metadata (name, date)
    │  ├─ Soil observations (hydric indicators, color, texture)
    │  ├─ Slope observations (compare to LS)
    │  ├─ Drainage observations
    │  ├─ Vegetation notes
    │  └─ Auto-generate discrepancy analysis
    │
    ├─ TAB 3: Component Breakdown
    │  ├─ Full table (soil type, K, LS, T, EI)
    │  ├─ Sort & filter
    │  └─ Export CSV
    │
    ├─ TAB 4: AD1026 Pre-fill
    │  ├─ Read-only: R, K, LS, T (auto-filled)
    │  ├─ EI calculation shown
    │  ├─ Formula & methodology
    │  └─ Download Pre-filled PDF
    │
    ├─ TAB 5: Technical Details
    │  ├─ Component uncertainties table
    │  ├─ Data provenance (SSURGO, USGS, NOAA, etc.)
    │  ├─ Methodology references & CFR citations
    │  └─ DEM resolution notes
    │
    └─ TAB 6: Export & Download
       ├─ CSV export (component data)
       ├─ PDF technical report
       └─ AD1026 pre-filled form
```

---

## SECTION 2: UI MOCKUPS (Text-Based)

### 2.1 Farmer View — Full Screen

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  CRP HEL SCREENING TOOL v16                          [🔬 Conservationist]  ║
║                                                                             ║
║ SIDEBAR ◀─────────────────────────────────────────────────────────────── ║
║ CRP Tool                                                                    ║
║ ▸ Configuration                                                             ║
║ ▸ Input Data                                                                ║
║ ─────────────────────────────                                              ║
║ 👤 View Mode                                                                ║
║ ┌──────────────┬──────────────────┐                                        ║
║ │ 🌾 Farmer    │ 🔬 Conserv...   │  ← Toggle here                         ║
║ │ [PRIMARY]    │ [SECONDARY]      │                                        ║
║ └──────────────┴──────────────────┘                                        ║
║ ℹ️ Currently viewing: Farmer View                                           ║
║                                                                             ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  MAIN CONTENT                                                               ║
║                                                                             ║
║  Map & Input (SHARED)                                                      ║
║  ┌─────────────────────────────────────────────────────────────────────┐  ║
║  │  [Interactive Folium Map with polygon drawing tools]                │  ║
║  │                                                                      │  ║
║  │  Draw polygon or enter coordinates → Click "Analyze"               │  ║
║  │                                                                      │  ║
║  │  [Analyze Button]                                                   │  ║
║  └─────────────────────────────────────────────────────────────────────┘  ║
║                                                                             ║
║  ─────────────────────────────────────────────────────────────────────     ║
║                                                                             ║
║  FARMER VIEW RESULTS                                                        ║
║                                                                             ║
║  ┌─────────────────────────────────────────────────────────────────────┐  ║
║  │  🌾 YOUR LAND ANALYSIS                                              │  ║
║  ├─────────────────────────────────────────────────────────────────────┤  ║
║  │                                                                      │  ║
║  │  HIGHLY ERODIBLE LAND (HEL) STATUS                                  │  ║
║  │  ┌────────────────────────────────────────────────────────────────┐ │  ║
║  │  │  ✅ LIKELY ELIGIBLE                                             │ │  ║
║  │  │                                                                  │ │  ║
║  │  │  Based on our analysis, this land MAY QUALIFY for CRP.         │ │  ║
║  │  │  An official determination requires NRCS field verification.   │ │  ║
║  │  │                                                                  │ │  ║
║  │  │  Erosion Index (EI): 9.5 / 8.0 threshold                       │ │  ║
║  │  └────────────────────────────────────────────────────────────────┘ │  ║
║  │                                                                      │  ║
║  │  WETLAND STATUS                                                      │  ║
║  │  ℹ️ 💧 Wetland soils detected (45% of soil components)             │  ║
║  │     CP23/CP28 wetland practices may be applicable. Confirm with    │  ║
║  │     NRCS wetland determination specialist.                         │  ║
║  │                                                                      │  ║
║  │  ─────────────────────────────────────────────────────────────────  │  ║
║  │                                                                      │  ║
║  │  WHAT TO DO NEXT                                                    │  ║
║  │                                                                      │  ║
║  │  1. Contact your local USDA Service Center — They can confirm      │  ║
║  │     eligibility and discuss CRP signup                             │  ║
║  │  2. Find your NRCS office using the button below                   │  ║
║  │  3. Have your land details ready — acres, current use, soil type   │  ║
║  │  4. Request a field visit — NRCS provides formal determination     │  ║
║  │                                                                      │  ║
║  │  ┌─────────────────────┬──────────────────────┐                    │  ║
║  │  │ 📍 Find NRCS Office │ 🖨️ Print Results   │                   │  ║
║  │  └─────────────────────┴──────────────────────┘                    │  ║
║  │                                                                      │  ║
║  │  SHARE WITH NRCS                                                    │  ║
║  │  Copy & paste this link to share results:                          │  ║
║  │  ┌───────────────────────────────────────────────────────────────┐ │  ║
║  │  │ https://crp-tool.com/share/abc123...                          │ │  ║
║  │  │                                                                 │ │  ║
║  │  │ [Copy to clipboard]                                            │ │  ║
║  │  └───────────────────────────────────────────────────────────────┘ │  ║
║  │                                                                      │  ║
║  └─────────────────────────────────────────────────────────────────────┘  ║
║                                                                             ║
║  HIDDEN IN FARMER VIEW:                                                     ║
║  • R, K, L, S factor details                                               ║
║  • AD1026 form                                                              ║
║  • Component breakdown table                                                ║
║  • Export options                                                           ║
║  • Technical methodology                                                    ║
║                                                                             ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### 2.2 Conservationist View — Tab 1: Results Overview

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  CRP HEL SCREENING TOOL v16                          [🌾 Farmer View]      ║
║                                                                             ║
║  SIDEBAR ◀─────────────────────────────────────────────────────────────── ║
║ 👤 View Mode                                                                ║
║ ┌──────────────┬──────────────────┐                                        ║
║ │ 🌾 Farmer    │ 🔬 Conserv...   │  ← Toggle here                         ║
║ │ [SECONDARY]  │ [PRIMARY]        │                                        ║
║ └──────────────┴──────────────────┘                                        ║
║ ⚠️ Technical View Enabled                                                   ║
║ This view shows raw RUSLE2 component data and pre-fills official          ║
║ USDA forms. Ensure you have domain expertise before sharing results.       ║
║                                                                             ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  CONSERVATIONIST ANALYSIS                                                   ║
║                                                                             ║
║  [Results Overview] [Field Verification] [Component Breakdown]             ║
║  [AD1026 Pre-fill]  [Technical Details]   [Export & Download]             ║
║                                                                             ║
║  ├─ TAB 1: RESULTS OVERVIEW ──────────────────────────────────────────────┤ ║
║  │                                                                          │ ║
║  │  ┌────────────────────┬────────────────────┬─────────────────────────┐ │ ║
║  │  │ Max EI (Indicative)│     HEL Status      │   Confidence Level      │ │ ║
║  │  │                    │                    │                         │ │ ║
║  │  │    9.5             │     LIKELY          │      HIGH               │ │ │  ║
║  │  │  (≥ 8.0 threshold) │  (EI ≥ 8.0)        │  ✓ Good data quality    │ │ │  ║
║  │  └────────────────────┴────────────────────┴─────────────────────────┘ │ ║
║  │                                                                          │ ║
║  │  ┌──────────────────────────────────────────────────────────────────┐  │ ║
║  │  │  CONFIDENCE: HIGH (🟢)                                           │  │ ║
║  │  │  ▸ Good spatial data resolution (USGS 3DEP 30m DEM)             │  │ ║
║  │  │  ▸ Point-specific R-factor (NOAA CDO: 5-8% accuracy)           │  │ ║
║  │  │  ▸ No steep slope warnings                                      │  │ ║
║  │  │  ▸ Complete SSURGO coverage                                     │  │ ║
║  │  └──────────────────────────────────────────────────────────────────┘  │ ║
║  │                                                                          │ ║
║  │  DETAILED FINDINGS                                                      │ ║
║  │                                                                          │ ║
║  │  Erosion Index (EI) Calculation:                                       │ ║
║  │  • R-factor (Rainfall): 95 (NOAA CDO point-specific)                   │ ║
║  │  • K-factor (Soil): 0.32 avg (range: 0.28 - 0.38)                     │ ║
║  │  • LS-factor (Slope): 1.8 avg (from DEM-derived flow accumulation)    │ ║
║  │  • T-factor (Tolerance): 4.5 avg (SSURGO data)                        │ ║
║  │  • EI = (95 × 0.32 × 1.8) / 4.5 = 12.1 max, 9.5 avg                  │ ║
║  │                                                                          │ ║
║  │  HEL Determination:                                                     │ ║
║  │  ✓ LIKELY HEL (maximum EI 9.5 ≥ 8.0 per 7 CFR § 12.21)               │ ║
║  │                                                                          │ ║
║  │  [📋 Compare with Field Verification]                                  │ ║
║  │   (Fill in the "Field Verification" tab to compare automated vs.       │ ║
║  │    field-measured data)                                                 │ ║
║  │                                                                          │ ║
║  └──────────────────────────────────────────────────────────────────────┘  ║
║                                                                             ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### 2.3 Conservationist View — Tab 2: Field Verification

```
┌─────────────────────────────────────────────────────────────────────────┐
│ FIELD VERIFICATION CHECKLIST                                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ FIELD METADATA                                                           │
│ ┌────────────────────────────┬────────────────────────────────────────┐ │
│ │ Field Name (optional):      │ Date of Site Visit:                  │ │
│ │ [Prairie Pothole Field  ]   │ [May 18, 2026]                      │ │
│ └────────────────────────────┴────────────────────────────────────────┘ │
│                                                                          │
│ PHYSICAL OBSERVATIONS                                                    │
│                                                                          │
│ Slope Observations:                                                      │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ Observed slope: 4-6% across field, concentrated in SW corner       │ │
│ │ Automated LS = 1.8 (based on DEM)                                   │ │
│ │ Field observation confirms higher slopes in map areas               │ │
│ │ ✓ Automated LS seems reasonable                                     │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ Soil Observations:                                                       │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ • Color: Dark gray, mottled at 24" depth (hydric indicator)        │ │
│ │ • Texture: Silt loam to silty clay loam                             │ │
│ │ • Drainage: Slow (tile drained, water table ~18-24" in spring)     │ │
│ │ • Hydric Indicators Present: YES (mottling, depleted matrix)       │ │
│ │ ✓ Matches SSURGO hydric rating (strong wetland signal)             │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ Other Observations:                                                      │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ Vegetation: Corn & soybeans (tilled), some residual wetland veg.   │ │
│ │ Drainage: Tile lines visible, outlet ditch to north                 │ │
│ │ Current Use: CRP-eligible (HEL + wetland potential)                │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ [📊 Compare Automated vs Field]  [Save Field Data]                 │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ DISCREPANCY SUMMARY (if any)                                            │
│ ✓ All automated values validated by field visit                         │
│ ✓ No major discrepancies                                                │
│ ✓ Ready for NRCS submission                                             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Conservationist View — Tab 3: Component Breakdown

```
┌─────────────────────────────────────────────────────────────────────────┐
│ COMPONENT BREAKDOWN (Detailed Soil Analysis)                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ 5 soil components identified across field:                              │
│                                                                          │
│ ┌───────────────────────────────────────────────────────────────────────┐
│ │ Soil Type | Acres |  K  | LS  | R  | T  | EI   | HEL    | Hydric    │
│ ├───────────────────────────────────────────────────────────────────────┤
│ │ Okoboji SL │  18.5 │0.35│ 1.9 │ 95 │ 4.5│ 13.9 │ HEL    │ Yes (95%)│
│ │ Harps SIL  │  22.0 │0.32│ 1.8 │ 95 │ 4.2│  12.1 │ HEL    │ Yes (88%)│
│ │ Sac SL     │  15.3 │0.38│ 2.1 │ 95 │ 5.0│  15.3 │ HEL    │ No       │
│ │ Tripoli L  │  12.8 │0.28│ 1.5 │ 95 │ 4.8│  8.8  │ HEL    │ No       │
│ │ Garwin SL  │   8.4 │0.31│ 2.3 │ 95 │ 4.1│  16.2 │ HEL    │ Yes (72%)│
│ ├───────────────────────────────────────────────────────────────────────┤
│ │ FIELD AVERAGE │ 77.0 │0.33│ 1.9 │ 95 │ 4.5│  12.0 │ HEL    │ 71%    │
│ └───────────────────────────────────────────────────────────────────────┘
│                                                                          │
│ Legend: All EI values ≥ 8.0 → Highly Erodible Land                     │
│         Hydric % = proportion of soil classified as hydric             │
│                                                                          │
│ [📊 Export to CSV]                                                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.5 Conservationist View — Tab 4: AD1026 Pre-fill

```
┌─────────────────────────────────────────────────────────────────────────┐
│ AD1026 PRE-FILL DATA                                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ 💡 These fields will be pre-filled in the official NRCS AD1026 form.   │
│ Review accuracy before submitting to NRCS.                             │
│                                                                          │
│ ┌────────────────────────────────┬────────────────────────────────────┐ │
│ │ R-Factor (Rainfall Erosivity)  │ K-Factor (Soil Erodibility)       │ │
│ │ [95.0 ◀︎ READ-ONLY]             │ [0.33 ◀︎ READ-ONLY]              │ │
│ │ Source: NOAA CDO (point-spec.) │ Source: SSURGO (avg. component)   │ │
│ │ ±5-8% accuracy                  │ ±10% uncertainty                  │ │
│ └────────────────────────────────┴────────────────────────────────────┘ │
│                                                                          │
│ ┌────────────────────────────────┬────────────────────────────────────┐ │
│ │ LS-Factor (Slope Metrics)       │ T-Factor (Soil Loss Tolerance)   │ │
│ │ [1.90 ◀︎ READ-ONLY]             │ [4.5 ◀︎ READ-ONLY]               │ │
│ │ Calculated from USGS 3DEP DEM   │ SSURGO published value            │ │
│ │ ±5% accuracy                    │ ±10% uncertainty                  │ │
│ └────────────────────────────────┴────────────────────────────────────┘ │
│                                                                          │
│ ─────────────────────────────────────────────────────────────────────── │
│                                                                          │
│ EROSION INDEX (EI) CALCULATION                                          │
│                                                                          │
│ Formula: EI = (R × K × LS) / T                                          │
│                                                                          │
│ EI = (95 × 0.33 × 1.9) / 4.5 = 12.0                                   │
│                                                                          │
│ Interpretation:                                                         │
│ • EI ≥ 8.0 → Highly Erodible Land (HEL) per 7 CFR § 12.21           │ │
│ • EI < 8.0 → Not HEL per Part 616                                     │ │
│                                                                          │
│ RESULT: ✓ HIGHLY ERODIBLE LAND (EI = 12.0 ≥ 8.0)                     │
│                                                                          │
│ ─────────────────────────────────────────────────────────────────────── │
│                                                                          │
│ [📥 Download Pre-filled AD1026 PDF]                                    │
│                                                                          │
│ This PDF includes:                                                       │
│ • Pre-filled R, K, LS, T values                                         │
│ • Calculated EI                                                         │
│ • HEL determination result                                              │
│ • Data sources & methodology                                            │
│ • Disclaimer for NRCS signature                                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.6 Conservationist View — Tab 5: Technical Details

```
┌─────────────────────────────────────────────────────────────────────────┐
│ TECHNICAL DETAILS                                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ RUSLE2 COMPONENT UNCERTAINTIES                                          │
│                                                                          │
│ ┌───────────────┬──────────────────────┬─────────────┬──────────────────┐
│ │ Component     │ Source               │ Uncertainty │ Notes             │
│ ├───────────────┼──────────────────────┼─────────────┼──────────────────┤
│ │ R-Factor      │ NOAA CDO (point)     │ ±5-8%       │ Brown & Foster    │
│ │               │                      │             │ 2023 data         │
│ ├───────────────┼──────────────────────┼─────────────┼──────────────────┤
│ │ K-Factor      │ SSURGO               │ ±10%        │ Soil component    │
│ │               │                      │             │ average           │
│ ├───────────────┼──────────────────────┼─────────────┼──────────────────┤
│ │ LS-Factor     │ USGS 3DEP 30m DEM    │ ±5%         │ Flow accumulation │
│ │               │ (py3dep)             │             │ algorithm         │
│ ├───────────────┼──────────────────────┼─────────────┼──────────────────┤
│ │ T-Factor      │ SSURGO               │ ±10%        │ Published NRCS    │
│ │               │                      │             │ soil loss tol.    │
│ └───────────────┴──────────────────────┴─────────────┴──────────────────┘
│                                                                          │
│ OVERALL UNCERTAINTY FOR EI:                                             │
│ Combined: ~±12-15% (using error propagation)                            │
│ Confidence Level: HIGH (quality data across all components)             │
│                                                                          │
│ ─────────────────────────────────────────────────────────────────────── │
│                                                                          │
│ DATA SOURCE PROVENANCE                                                  │
│                                                                          │
│ • SSURGO: USDA NRCS Soil Survey Geographic Database (SDA API)          │
│   └─ Coverage: Complete for Iowa, Minnesota, Missouri, etc.            │
│   └─ Update: Annual (most recent: 2025)                                │
│                                                                          │
│ • USGS 3DEP: U.S. Geological Survey 3D Elevation Program 30m DEM       │
│   └─ Coverage: Nationwide                                               │
│   └─ Resolution: 30m × 30m raster cells                                │
│   └─ Source: LiDAR, IfSAR, and stereo imagery                          │
│                                                                          │
│ • NOAA CDO: National Oceanic & Atmospheric Admin Climate Data Online   │
│   └─ Coverage: Nationwide weather stations                              │
│   └─ Data: Daily precipitation records, 2023                           │
│   └─ Accuracy: ±1-2% (measured with standard rain gauges)             │
│                                                                          │
│ • State Detection: Nominatim reverse geocoding (OpenStreetMap)          │
│   └─ Fallback: NRCS FOTG R-factor tables (state-level average)        │
│                                                                          │
│ ─────────────────────────────────────────────────────────────────────── │
│                                                                          │
│ METHODOLOGY REFERENCES                                                  │
│                                                                          │
│ • RUSLE2 Formula: R × K × LS / T (NRCS Part 616, Agriculture          │
│   Handbook 703)                                                         │
│                                                                          │
│ • HEL Threshold: EI ≥ 8.0 per 7 CFR § 12.21 (Federal Code of          │
│   Regulations)                                                          │
│                                                                          │
│ • LS Calculation: L = (λ/22.13)^m, S = 10.8×sin(θ) + 0.03, where:     │
│   - λ = flow length (m)                                                │
│   - θ = slope angle (degrees)                                          │
│   - m = 0.4-0.6 (slope-dependent exponent)                            │
│                                                                          │
│ • R-Factor Method: Brown & Foster (1981) equation:                    │
│   R ≈ 0.04887 × P^1.61 (P = annual precipitation in mm)              │
│   Accuracy: ±5-8% vs. measured EI30 from hourly rainfall intensity    │
│                                                                          │
│ • Wetland Detection: Multi-source synthesis:                           │
│   - Hydric soils (SSURGO hydricrating field)                          │
│   - Drainage class (SSURGO drainagecl field)                          │
│   - Vegetation (NLCD 2021 water/wetland classes)                      │
│   - Hydrology (NHD Plus proximity to water bodies)                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.7 Conservationist View — Tab 6: Export & Download

```
┌─────────────────────────────────────────────────────────────────────────┐
│ EXPORT RESULTS                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ Export Data in Multiple Formats:                                        │
│                                                                          │
│ ┌──────────────────────────┬──────────────────┬──────────────────────┐ │
│ │ 📊 Export to CSV         │ 📄 Export to PDF │ 📋 AD1026 Form      │ │
│ │                          │                  │                      │ │
│ │ Contains:                │ Contains:        │ Contains:            │ │
│ │ • All soil components    │ • Full report    │ • Pre-filled fields │ │
│ │ • K, LS, T, EI values    │ • Methodology    │ • R, K, LS, T values│
│ │ • Hydric status per      │ • Data sources   │ • HEL determination │ │
│ │   component              │ • Confidence     │ • NRCS signature    │ │
│ │ • Can open in Excel      │   flags          │   block             │ │
│ │                          │ • Official       │ • Ready for NRCS    │ │
│ │ Use for:                 │   format         │   submission         │ │
│ │ • Personal analysis      │ • Legal record   │ • Official HEL      │ │
│ │ • Database import        │ • Sharing with   │   determination      │ │
│ │ • Further calculations   │   NRCS or       │                      │ │
│ │                          │   consultants    │ Use for:            │ │
│ │ [⬇️ Download CSV]        │ [⬇️ Download PDF]│ [⬇️ Download Form]  │ │
│ │ Size: 8 KB               │ Size: 120 KB     │ Size: 95 KB          │ │
│ └──────────────────────────┴──────────────────┴──────────────────────┘ │
│                                                                          │
│ ─────────────────────────────────────────────────────────────────────── │
│                                                                          │
│ All exports include:                                                    │
│ ✓ Date generated                                                        │ │
│ ✓ Tool version (CRP HEL Screening v16)                                │ │
│ ✓ Disclaimer: "Indicative only — confirm with NRCS field visit"     │ │
│ ✓ Data sources & methodology                                           │ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## SECTION 3: FARMER VIEW ACTION FLOWS

### 3.1 "Find NRCS Office" Flow

```
USER CLICKS: "📍 Find NRCS Office"
    │
    ▼
Extract field coordinates from map
    │
    ▼
Call find_nrcs_office(lat, lon)
    │
    ├─ Try USDA Service Locator API
    │  └─ Returns: {name, address, phone, email, hours, distance}
    │
    └─ Fallback: State-level hardcoded NRCS office list
    │
    ▼
DISPLAY: NRCS Office Details
    ┌─────────────────────────────────────────┐
    │ Nearest NRCS Office                     │
    │                                         │
    │ Fleming County NRCS Office              │
    │ 1010 Main St, Room 200                  │
    │ Flemingsburg, KY 41041                  │
    │                                         │
    │ 📞 (606) 845-2665                       │
    │ 📧 nrcs@flemingky.usda.gov             │
    │ 🌐 www.nrcs.usda.gov/ky                │
    │                                         │
    │ ⏰ Hours: Mon-Fri 8:00 AM - 4:30 PM    │
    │ Distance: 3.2 miles from your field    │
    │                                         │
    │ ─────────────────────────────────────── │
    │                                         │
    │ 📬 What to Bring                        │
    │                                         │
    │ 1. This screening report (Print button) │
    │ 2. Property deed or tax map             │
    │ 3. Current land use details             │
    │ 4. Soil samples (optional)              │
    │                                         │
    │ 📋 What NRCS Will Do                    │
    │                                         │
    │ • Verify HEL status (field visit)      │
    │ • Determine wetland presence            │
    │ • Recommend CRP practices               │
    │ • Discuss enrollment & payments         │
    │                                         │
    │ [📍 Open in Maps]  [📧 Send Email]     │
    └─────────────────────────────────────────┘
```

### 3.2 "Print Results" Flow

```
USER CLICKS: "🖨️ Print Results"
    │
    ▼
Generate simple PDF summary
    │
    ├─ Header: Tool name & date
    ├─ Field location (coordinates)
    ├─ HEL Status (Yes/No/Maybe)
    ├─ Wetland Status (Yes/No)
    ├─ Next Steps (Contact NRCS)
    ├─ NRCS office info
    ├─ Disclaimer
    └─ Shareable link
    │
    ▼
USER: Saves / prints document
    │
    ▼
Can take to NRCS office or email ahead
```

### 3.3 "Share with NRCS" Flow

```
USER CLICKS: "Share"
    │
    ▼
Generate shareable link with embedded data:
    URL: https://crp-tool.com/share/abc123xyz...
    │
    ├─ Encodes: field coordinates, EI results, wetland status
    └─ Expires: 30 days (or until field re-analyzed)
    │
    ▼
USER: Copy link → Email/text to NRCS
    │
    ▼
NRCS STAFF: Click link
    │
    ├─ Opens tool with field pre-loaded
    ├─ Shows all results (Tier 2 conservationist view auto-triggered)
    └─ Can add field verification notes
    │
    ▼
NRCS: Downloads AD1026 & schedules field visit
```

---

## SECTION 4: CONSERVATIONIST WORKFLOW

### 4.1 Complete Conservationist Workflow

```
Step 1: DRAW FIELD
  └─► User draws polygon on map or enters coordinates
      └─► Hit "Analyze"

Step 2: TOGGLE TO CONSERVATIONIST VIEW
  └─► Click "🔬 Conservationist" in sidebar
      └─► 6 tabs appear

Step 3: REVIEW RESULTS
  └─► Tab 1: Results Overview
      ├─► Check max EI, HEL status, confidence level
      ├─► Note any confidence flags (A, B, C)
      └─► Click "Compare with Field Verification"

Step 4: CONDUCT FIELD VISIT (if not done)
  └─► Tab 2: Field Verification
      ├─► Enter field name, date of visit
      ├─► Record soil observations (hydric indicators, color, texture)
      ├─► Record slope observations (compare to automated LS)
      ├─► Record drainage class
      ├─► Record vegetation notes
      ├─► Hit "Compare Automated vs Field"
      └─► Validates automated calculations

Step 5: REVIEW COMPONENT BREAKDOWN
  └─► Tab 3: Component Breakdown
      ├─► See each soil type's contribution to EI
      ├─► Identify most erodible components
      └─► Export to CSV if needed

Step 6: PREPARE NRCS SUBMISSION
  └─► Tab 4: AD1026 Pre-fill
      ├─► Review pre-filled values (R, K, LS, T)
      ├─► Verify EI calculation matches 7 CFR § 12.21
      └─► Download pre-filled AD1026 PDF

Step 7: REVIEW TECHNICAL DETAILS
  └─► Tab 5: Technical Details
      ├─► Check uncertainty ranges
      ├─► Verify data sources
      └─► Note methodology references

Step 8: EXPORT & SUBMIT
  └─► Tab 6: Export & Download
      ├─► Download CSV (component data)
      ├─► Download PDF Report (full technical report)
      ├─► Download AD1026 form (pre-filled)
      └─► Email results to NRCS or save for records

OUTCOME: NRCS-ready HEL determination with documentation
```

---

## SECTION 5: DATA FLOW WITHIN CONSERVATIONIST MODE

```
Field Verification Data (Session State)
    │
    ├─ field_name
    ├─ date_visited
    ├─ soil_notes
    ├─ slope_notes
    ├─ drainage_observations
    └─ vegetation_notes
        │
        ▼
    Comparison Logic:
    ├─ Compare soil_notes with SSURGO hydricrating
    │  └─ Validate hydric soils detected
    ├─ Compare slope_notes with automated LS
    │  └─ Flag discrepancies > 20%
    ├─ Compare drainage_observations with SSURGO drainagecl
    │  └─ Validate poor drainage signals
    └─ Compare vegetation_notes with NLCD classes
       └─ Validate wetland vegetation
        │
        ▼
    Discrepancy Report (Tab 2):
    ├─ No discrepancies → "✓ All automated values validated"
    ├─ Minor discrepancy → "⚠️ Note: field slope slightly steeper than DEM shows"
    └─ Major discrepancy → "❌ Field investigation contradicts tool results"
        │
        ▼
    AD1026 Pre-fill (Tab 4):
    ├─ If no major discrepancies → Fill R, K, LS, T with confidence
    ├─ If minor discrepancies → Flag in "Notes" field
    └─ If major discrepancies → Recommend NRCS field verification
```

---

## SECTION 6: MOBILE-RESPONSIVE LAYOUTS

### 6.1 Farmer View (Mobile)

```
MOBILE SCREEN (375px width):

┌──────────────────────┐
│ CRP Tool  ☰          │  ← Hamburger menu
├──────────────────────┤
│                      │
│  [Interactive Map]   │  ← Full width
│                      │
│  [Analyze Button]    │
│                      │
├──────────────────────┤
│                      │
│ HEL Status:          │
│ ✅ LIKELY ELIGIBLE   │
│                      │
│ Wetland:             │
│ 💧 Yes detected      │
│                      │
│ [Find NRCS Office]   │  ← Stack vertically
│ [Print Results]      │
│                      │
│ Share:               │
│ [Copy Link]          │
│                      │
└──────────────────────┘
```

### 6.2 Conservationist View (Mobile)

```
MOBILE SCREEN (375px width):

┌──────────────────────┐
│ CRP Tool  ☰          │
├──────────────────────┤
│ [Tabs scroll →]      │
│ Res | Field | Comp...│
├──────────────────────┤
│ Max EI: 9.5          │
│ Status: LIKELY       │
│ Confidence: HIGH     │
│                      │
│ [Compare with Field] │
│                      │
├──────────────────────┤
│ TAB 2: Field Ver.    │
│                      │
│ Field Name:          │
│ [________________]   │
│                      │
│ Visit Date:          │
│ [May 18, 2026]       │
│                      │
│ Slope Notes:         │
│ [________________]   │
│ [________________]   │
│                      │
└──────────────────────┘

(Tables in mobile: horizontal scroll or card layout)
```

---

## SECTION 7: ERROR HANDLING FLOWS

### 7.1 Invalid Polygon

```
USER: Draws polygon too small / large, or outside US
    │
    ▼
VALIDATION: Polygon area < 1 acre or > 10,000 acres
    │
    ▼
ERROR MESSAGE:
┌────────────────────────────────────────┐
│ ⚠️ Invalid Field Boundary               │
│                                         │
│ Your field polygon is too small or too │
│ large. Please draw a polygon between   │
│ 1 acre and 10,000 acres.               │
│                                         │
│ Current size: 0.3 acres                │
│                                         │
│ [OK] [Try Again]                       │
└────────────────────────────────────────┘

USER: Redraws polygon
    │
    ▼
(Retry analysis)
```

### 7.2 State Not Detected

```
USER: Polygon drawn in ambiguous location or out of bounds
    │
    ▼
R-FACTOR LOOKUP: State detection fails
    │
    ▼
WARNING (Farmer View):
┌────────────────────────────────────────┐
│ ⚠️ State Not Detected                   │
│                                         │
│ R-factor defaulted to 100 (national    │
│ average). Results are less reliable.   │
│ Try redrawing the polygon or use       │
│ Precision Entry with verified          │
│ coordinates.                           │
│                                         │
│ [Redraw] [Use Precision Coords]        │
└────────────────────────────────────────┘

INFO (Conservationist View):
All tabs still available, but with note:
"State not detected — R-factor estimated as 100"
```

### 7.3 SSURGO Data Unavailable

```
USER: Polygon in area without SSURGO coverage
    │
    ▼
SSURGO QUERY: No soil data found
    │
    ▼
ERROR:
┌────────────────────────────────────────┐
│ ❌ No Soil Data Available                │
│                                         │
│ SSURGO database does not have soil     │
│ information for this location. This    │
│ typically occurs in non-agricultural   │
│ areas (urban, water, public lands).    │
│                                         │
│ CRP enrollment is available for       │
│ agricultural land only.                │
│                                         │
│ Contact your local NRCS office to     │
│ discuss alternatives.                 │
│                                         │
│ [OK]                                   │
└────────────────────────────────────────┘
```

---

## SECTION 8: CONFIDENCE INDICATORS (A, B, C)

### 8.1 Confidence Flags (Conservationist View)

```
CONFIDENCE LEVEL: HIGH (🟢) / MEDIUM (🟡) / LOW (🔴)

Three independent flags trigger confidence adjustments:

Flag A: R-Factor Confidence
  ✓ GREEN:   NOAA CDO point-specific (±5-8% error)
  ⚠️ YELLOW: State-level average (±20-30% error)
  ❌ RED:    Unknown state (defaulted to R=100)

Flag B: LS-Factor Confidence
  ✓ GREEN:   USGS 3DEP 30m DEM (±5% error)
  ⚠️ YELLOW: LS approximation from SSURGO slope only
  ❌ RED:    Steep slope warning (slope > 12%)

Flag C: SSURGO Coverage
  ✓ GREEN:   Complete soil survey coverage
  ⚠️ YELLOW: Partial coverage (some components extrapolated)
  ❌ RED:    No soil data (area non-agricultural)

OVERALL CONFIDENCE SCORE:
  GREEN (all 3 flags green)    → HIGH confidence
  YELLOW (1-2 flags yellow)    → MEDIUM confidence
  RED (any flag red)           → LOW confidence
```

---

**End of Mockups & Flows Document**
