# Two-Tier UI System Implementation Plan
## CRP HEL Screening Tool v16+

**Document Version:** 1.0  
**Date:** 2026-05-18  
**Target File:** `crp_final_v12_hf.py` → `crp_final_v16.py`

---

## Executive Summary

This plan details a dual-audience UI system for the CRP HEL Screening Tool that serves two distinct user types with dramatically different needs:

- **TIER 1: FARMER VIEW** — Non-technical results, actionable next steps, NRCS locator
- **TIER 2: CONSERVATIONIST VIEW** — Field verification, technical details, AD1026 pre-fill, export-ready documentation

The system maintains a **single unified data model** with **branching UI layers** that toggle via session state. This approach maximizes code reuse while providing role-specific experiences.

---

## 1. ARCHITECTURE OVERVIEW

### 1.1 High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CRP HEL SCREENING TOOL v16                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ AUTHENTICATION & MODE SELECTION LAYER                         │  │
│  ├──────────────────────────────────────────────────────────────┤  │
│  │ • Farmer View (default)    [TOGGLE]  Conservationist View    │  │
│  │ • Session state: conservationist_mode (bool)                 │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│         ┌────────────────────┴────────────────────┐                 │
│         │                                         │                 │
│         ▼                                         ▼                 │
│  ┌──────────────────────┐              ┌──────────────────────┐    │
│  │  SHARED DATA LAYER   │              │  SHARED DATA LAYER   │    │
│  ├──────────────────────┤              ├──────────────────────┤    │
│  │ • SSURGO query       │◄────┬────►   │ • SSURGO query       │    │
│  │ • DEM fetch (LS)     │     │        │ • DEM fetch (LS)     │    │
│  │ • R-factor (NOAA)    │  SHARED      │ • R-factor (NOAA)    │    │
│  │ • EI calculation     │              │ • EI calculation     │    │
│  │ • Wetland detection  │              │ • Wetland detection  │    │
│  │ • Hydric soils       │              │ • Hydric soils       │    │
│  └──────────────────────┘              └──────────────────────┘    │
│         │                                         │                 │
│         └────────────────────┬────────────────────┘                 │
│                              │                                       │
│         ┌────────────────────┴────────────────────┐                 │
│         │                                         │                 │
│         ▼                                         ▼                 │
│  ┌──────────────────────┐              ┌──────────────────────┐    │
│  │  FARMER VIEW UI      │              │  CONSERVATIONIST UI  │    │
│  ├──────────────────────┤              ├──────────────────────┤    │
│  │ RESULTS PANEL:       │              │ RESULTS PANEL:       │    │
│  │ • HEL Yes/No/Maybe   │              │ • HEL Status         │    │
│  │ • Wetland Yes/No     │              │ • Detailed breakdown  │    │
│  │ • Next step (contact)│              │ • Confidence metrics  │    │
│  │                      │              │                      │    │
│  │ ACTION PANEL:        │              │ VERIFICATION PANEL:  │    │
│  │ • Find NRCS office   │              │ • Field checklist    │    │
│  │ • Print results      │              │ • Automated vs field │    │
│  │ • Share via link     │              │ • Discrepancy flags  │    │
│  │                      │              │                      │    │
│  │ HIDDEN:              │              │ TECHNICAL PANEL:     │    │
│  │ • R, K, L, S factors │              │ • R, K, L, S details │    │
│  │ • AD1026 form        │              │ • Uncertainty ranges │    │
│  │ • Pre-filled PDF     │              │ • Raster sources     │    │
│  │ • Comparison table   │              │                      │    │
│  │                      │              │ EXPORT PANEL:        │    │
│  │                      │              │ • Pre-filled AD1026  │    │
│  │                      │              │ • PDF download       │    │
│  │                      │              │ • CSV export         │    │
│  │                      │              │ • Field verification │    │
│  └──────────────────────┘              └──────────────────────┘    │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow Diagram

```
USER INPUT (Shared)
│
├─ Draw polygon on map  ──┐
├─ Precision coords       │
└─ Upload boundary file   │
                          │
                          ▼
            ┌─────────────────────────┐
            │  GEOSPATIAL PROCESSING  │
            └─────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
    ┌────────┐      ┌──────────┐      ┌──────────┐
    │ SSURGO │      │USGS 3DEP │      │NOAA CDO  │
    │ Query  │      │ DEM Data │      │ Precip   │
    └────────┘      └──────────┘      └──────────┘
        │                 │                 │
        │ K, T factors    │ LS calculation  │ R-factor
        │ Hydric rating   │ (DEM-derived)   │ (Point-specific)
        │ Drainage class  │ ±5% error       │ Brown & Foster
        │ Vegetation      │                 │ ±5% error
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
                          ▼
            ┌─────────────────────────┐
            │  DATA AGGREGATION       │
            │  (per soil component)   │
            └─────────────────────────┘
                          │
                          ▼
            ┌─────────────────────────┐
            │  EI CALCULATION LAYER   │
            │  EI = (R×K×LS) / T      │
            └─────────────────────────┘
                          │
                          ▼
            ┌─────────────────────────┐
            │  WETLAND DETECTION      │
            │  (SSURGO + NLCD + NHD)  │
            └─────────────────────────┘
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
        ▼                                   ▼
  ┌──────────────────┐           ┌──────────────────┐
  │ FARMER VIEW      │           │ CONSERVATIONIST  │
  │ RENDER           │           │ VIEW RENDER      │
  ├──────────────────┤           ├──────────────────┤
  │ • Simplified EI  │           │ • Full data      │
  │ • Yes/No status  │           │ • Component view │
  │ • NRCS finder    │           │ • Comparison     │
  │ • Print/share    │           │ • AD1026 pre-fill│
  │                  │           │ • PDF export     │
  └──────────────────┘           └──────────────────┘
```

### 1.3 Component Sharing Matrix

| Component | Data Layer | Farmer View | Conservationist View | Notes |
|-----------|-----------|-------------|---------------------|-------|
| Map visualization | Shared | Yes | Yes | Same base layer |
| Polygon draw/edit | Shared | Yes | Yes | Shared input |
| SSURGO query | Shared | Yes | Yes | Single API call |
| DEM fetch | Shared | Yes | Yes | Cached LS factors |
| R-factor lookup | Shared | Yes | Yes | NOAA CDO + fallback |
| EI calculation | Shared | Yes | Yes | Vectorized computation |
| Wetland detection | Shared | Yes | Yes | Multi-source synthesis |
| Results summary | Layer | Yes (simple) | Yes (detailed) | Different templates |
| Technical details | Layer | No (hidden) | Yes | Conditional render |
| AD1026 form | Layer | No | Yes | Conservationist-only |
| NRCS office finder | Layer | Yes | No | Farmer action |
| PDF export | Layer | Simple | Complex | Format differs |

---

## 2. CODE STRUCTURE FOR crp_final_v16.py

### 2.1 Session State Initialization

```python
# At top of Streamlit app, after st.set_page_config()

def init_session_state():
    """Initialize session state variables for two-tier UI system."""
    
    # Authentication & mode
    if "conservationist_mode" not in st.session_state:
        st.session_state.conservationist_mode = False
    if "user_authenticated" not in st.session_state:
        st.session_state.user_authenticated = False
    if "user_email" not in st.session_state:
        st.session_state.user_email = None
    
    # Geospatial & data
    if "last_polygon_bounds" not in st.session_state:
        st.session_state.last_polygon_bounds = None
    if "last_wkt_string" not in st.session_state:
        st.session_state.last_wkt_string = None
    
    # Cached calculation results
    if "cached_analysis_results" not in st.session_state:
        st.session_state.cached_analysis_results = None
    if "cached_r_factor" not in st.session_state:
        st.session_state.cached_r_factor = None
    if "cached_r_source" not in st.session_state:
        st.session_state.cached_r_source = None
    if "cached_ei_data" not in st.session_state:
        st.session_state.cached_ei_data = None  # DataFrame with all EI calcs
    
    # Conservationist mode specifics
    if "field_verification_data" not in st.session_state:
        st.session_state.field_verification_data = {
            "field_name": None,
            "farmer_name": None,
            "date_visited": None,
            "soil_samples": [],
            "slope_measurements": [],
            "vegetation_notes": None,
            "drainage_observations": None
        }
    
    if "ad1026_form_data" not in st.session_state:
        st.session_state.ad1026_form_data = {}

init_session_state()
```

### 2.2 Mode Toggle UI Component

```python
def render_mode_selector():
    """Render the farmer/conservationist mode toggle in sidebar."""
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("👤 View Mode")
    
    col1, col2 = st.sidebar.columns(2)
    
    with col1:
        if st.button(
            "🌾 Farmer View",
            key="farmer_mode_btn",
            use_container_width=True,
            type=("secondary" if st.session_state.conservationist_mode else "primary")
        ):
            st.session_state.conservationist_mode = False
            st.rerun()
    
    with col2:
        if st.button(
            "🔬 Conservationist",
            key="conservationist_mode_btn",
            use_container_width=True,
            type=("primary" if st.session_state.conservationist_mode else "secondary")
        ):
            st.session_state.conservationist_mode = True
            st.rerun()
    
    # Show current mode
    mode_label = "Conservationist View (Advanced)" if st.session_state.conservationist_mode else "Farmer View (Simple)"
    st.sidebar.info(f"**Currently viewing:** {mode_label}")
    
    # Conservationist mode warning/info
    if st.session_state.conservationist_mode:
        st.sidebar.warning(
            "⚠️ **Technical View Enabled**\n\n"
            "This view shows raw RUSLE2 component data and pre-fills official USDA forms. "
            "Ensure you have domain expertise before sharing results.",
            icon="🔐"
        )

render_mode_selector()
```

### 2.3 Function Signatures & Stub Implementations

#### Main Orchestration Functions

```python
def analyze_field_for_tier(lat_bounds, lon_bounds, tier="farmer"):
    """
    Unified analysis function that branches to appropriate tier-specific logic.
    
    Args:
        lat_bounds (tuple): (min_lat, max_lat)
        lon_bounds (tuple): (min_lon, max_lon)
        tier (str): "farmer" or "conservationist"
    
    Returns:
        dict: {
            "ei_results": DataFrame with EI per component,
            "wetland_assessment": dict or None,
            "r_factor": float,
            "r_source": str,
            "confidence": {"level": str, "color": str, "message": str},
            "recommended_practices": list,
            "tier_specific_data": dict  # Varies by tier
        }
    """
    # 1. Check cache
    if st.session_state.cached_analysis_results:
        return st.session_state.cached_analysis_results
    
    # 2. Shared: Fetch geospatial data
    bbox = (lat_bounds[0], lon_bounds[0], lat_bounds[1], lon_bounds[1])
    
    # Query SSURGO, DEM, NOAA in parallel
    ssurgo_data = query_ssurgo_within_bounds(bbox)
    dem_ls_factors = fetch_dem_and_calculate_ls(lat_bounds, lon_bounds)
    r_factor, r_source = get_state_r_factor_hybrid(
        (lat_bounds[0] + lat_bounds[1]) / 2,
        (lon_bounds[0] + lon_bounds[1]) / 2
    )
    
    # 3. Calculate EI (shared layer)
    ei_results = calculate_ei_per_component(
        ssurgo_data, dem_ls_factors, r_factor
    )
    
    # 4. Detect wetlands (shared layer)
    wetland_assessment = detect_wetland_status(ssurgo_data, bbox)
    
    # 5. Branch: tier-specific processing
    if tier == "farmer":
        tier_data = prepare_farmer_tier_data(ei_results, wetland_assessment)
    else:  # conservationist
        tier_data = prepare_conservationist_tier_data(
            ei_results, wetland_assessment, ssurgo_data
        )
    
    # 6. Compile results
    results = {
        "ei_results": ei_results,
        "wetland_assessment": wetland_assessment,
        "r_factor": r_factor,
        "r_source": r_source,
        "confidence": calculate_confidence_metrics(ei_results, r_source),
        "recommended_practices": generate_practice_recommendations(
            ei_results, wetland_assessment
        ),
        "tier_specific_data": tier_data
    }
    
    # 7. Cache and return
    st.session_state.cached_analysis_results = results
    return results
```

#### Tier-Specific Rendering Functions

```python
def show_farmer_view(analysis_results):
    """
    Render TIER 1 (Farmer) interface.
    
    Components:
    - HEL Status (Yes/No/Maybe with color coding)
    - Wetland Status (Yes/No with simple explanation)
    - Next Step recommendation (always "Contact NRCS")
    - Find NRCS Office button (launches locator)
    - Print/Share buttons
    
    Args:
        analysis_results (dict): Output from analyze_field_for_tier()
    """
    st.markdown("## 🌾 Your Land Analysis")
    
    # Extract key data
    ei_results = analysis_results["ei_results"]
    max_ei = ei_results["EI"].max() if "EI" in ei_results else 0
    wetland_assessment = analysis_results["wetland_assessment"]
    
    # HEL Status Display
    st.subheader("Highly Erodible Land (HEL) Status")
    
    if max_ei >= 8.0:
        st.success("✅ LIKELY ELIGIBLE")
        status_color = "#1B4332"
        status_text = "Based on our analysis, this land MAY QUALIFY for CRP."
    elif max_ei >= 6.0:
        st.warning("⚠️ MAYBE ELIGIBLE")
        status_color = "#92400E"
        status_text = "This land is BORDERLINE. An NRCS field visit is recommended."
    else:
        st.error("❌ LIKELY INELIGIBLE")
        status_color = "#7f1d1d"
        status_text = "Based on our analysis, this land does NOT appear to qualify."
    
    st.markdown(
        f'<div style="background-color:{status_color};padding:15px;border-radius:5px;'
        f'color:#fff;font-size:14px;margin:10px 0;">{status_text}</div>',
        unsafe_allow_html=True
    )
    
    # Wetland Status
    st.subheader("Wetland Status")
    if wetland_assessment and wetland_assessment.get("confidence") in ["High", "Medium"]:
        st.info(f"💧 {wetland_assessment['wetland_type']}")
    else:
        st.info("No wetland indicators detected (but confirm with site visit)")
    
    # Next Steps
    st.subheader("What to Do Next")
    st.markdown("""
    1. **Contact your local USDA Service Center** — They can confirm eligibility and discuss CRP signup
    2. **Find your NRCS office** using the button below
    3. **Have your land details ready** — acres, current use, soil type
    4. **Consider a field visit** — NRCS can provide a formal HEL determination
    """)
    
    # NRCS Finder
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📍 Find Your NRCS Office", use_container_width=True):
            show_nrcs_office_finder(
                analysis_results["ei_results"],
                analysis_results["r_source"]
            )
    
    with col2:
        if st.button("🖨️ Print Results", use_container_width=True):
            generate_farmer_print_pdf(analysis_results)
    
    # Share link
    st.markdown("**Share results with NRCS:**")
    share_link = generate_shareable_link(analysis_results)
    st.code(share_link, language="text")


def show_conservationist_view(analysis_results):
    """
    Render TIER 2 (Conservationist) interface.
    
    Tabs:
    1. Results Overview — Full technical summary
    2. Field Verification — Checklist + field measurements
    3. Component Breakdown — Detailed per-soil data
    4. AD1026 Pre-fill — USDA form integration
    5. Technical Details — R, K, L, S uncertainties
    6. Export & Download — PDF, CSV, AD1026 forms
    
    Args:
        analysis_results (dict): Output from analyze_field_for_tier()
    """
    st.markdown("## 🔬 Conservationist Analysis")
    
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Results Overview",
        "Field Verification",
        "Component Breakdown",
        "AD1026 Pre-fill",
        "Technical Details",
        "Export & Download"
    ])
    
    # TAB 1: Results Overview
    with tab1:
        show_conservationist_results_overview(analysis_results)
    
    # TAB 2: Field Verification
    with tab2:
        show_field_verification_panel(analysis_results)
    
    # TAB 3: Component Breakdown
    with tab3:
        show_component_breakdown(analysis_results["ei_results"])
    
    # TAB 4: AD1026 Pre-fill
    with tab4:
        show_ad1026_prefill_panel(analysis_results)
    
    # TAB 5: Technical Details
    with tab5:
        show_technical_details_panel(analysis_results)
    
    # TAB 6: Export
    with tab6:
        show_export_panel(analysis_results)


def show_conservationist_results_overview(analysis_results):
    """Results summary with all confidence flags and methodology notes."""
    
    ei_results = analysis_results["ei_results"]
    max_ei = ei_results["EI"].max() if "EI" in ei_results else 0
    wetland = analysis_results["wetland_assessment"]
    confidence = analysis_results["confidence"]
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Maximum Erosion Index (EI)",
            f"{max_ei:.2f}",
            f"EI {'≥ 8.0' if max_ei >= 8.0 else '< 8.0'}"
        )
    
    with col2:
        st.metric(
            "HEL Status",
            "LIKELY" if max_ei >= 8.0 else "UNLIKELY",
            f"Based on RUSLE2 threshold"
        )
    
    with col3:
        st.metric(
            "Confidence Level",
            confidence["level"],
            confidence["message"]
        )
    
    # Full methodology display
    st.subheader("Detailed Findings")
    
    # Comparison: Automated vs. Field Verification
    if st.session_state.field_verification_data.get("date_visited"):
        show_automated_vs_field_comparison(analysis_results)
    else:
        st.info(
            "📋 **Field Verification Not Yet Recorded** — "
            "Click the 'Field Verification' tab to compare automated results with field measurements."
        )


def show_field_verification_panel(analysis_results):
    """Conservationist field verification & comparison interface."""
    
    st.subheader("Field Verification Checklist")
    
    # Field metadata
    col1, col2 = st.columns(2)
    with col1:
        field_name = st.text_input(
            "Field Name (optional)",
            value=st.session_state.field_verification_data.get("field_name", "")
        )
    with col2:
        visit_date = st.date_input("Date of Site Visit")
    
    st.session_state.field_verification_data["field_name"] = field_name
    st.session_state.field_verification_data["date_visited"] = str(visit_date)
    
    # Physical observations
    st.subheader("Physical Observations")
    
    col1, col2 = st.columns(2)
    with col1:
        slope_notes = st.text_area(
            "Slope Observations",
            help="Compare automated LS factor with observed slope"
        )
    with col2:
        soil_notes = st.text_area(
            "Soil Observations",
            help="Document soil color, texture, hydric indicators"
        )
    
    # Verification summary
    st.subheader("Discrepancy Analysis")
    
    if slope_notes or soil_notes:
        col1, col2 = st.columns(2)
        with col1:
            st.success("✅ Field data recorded")
        with col2:
            if st.button("📊 Compare Automated vs Field"):
                show_automated_vs_field_comparison(analysis_results)


def show_ad1026_prefill_panel(analysis_results):
    """AD1026 form (NRCS determination form) pre-fill interface."""
    
    st.subheader("AD1026 Pre-fill Data")
    st.info(
        "💡 These fields will be pre-filled in the official NRCS AD1026 form. "
        "Review accuracy before submitting to NRCS."
    )
    
    # Extract pre-fillable data
    ei_data = analysis_results["ei_results"]
    r_source = analysis_results["r_source"]
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.text_input(
            "R-Factor (Rainfall Erosivity)",
            value=f"{analysis_results['r_factor']:.1f}",
            disabled=True,
            help=f"Source: {r_source}"
        )
        
        if "K" in ei_data.columns:
            avg_k = ei_data["K"].mean()
            st.text_input(
                "K-Factor (Soil Erodibility) — Average",
                value=f"{avg_k:.3f}",
                disabled=True,
                help="Average across all soil components"
            )
    
    with col2:
        if "LS" in ei_data.columns:
            avg_ls = ei_data["LS"].mean()
            st.text_input(
                "LS-Factor (Slope Length & Steepness)",
                value=f"{avg_ls:.3f}",
                disabled=True,
                help="Calculated from USGS 3DEP 30m DEM"
            )
        
        if "T" in ei_data.columns:
            avg_t = ei_data["T"].mean()
            st.text_input(
                "T-Factor (Soil Loss Tolerance) — Average",
                value=f"{avg_t:.1f}",
                disabled=True,
                help="Average across all soil components"
            )
    
    # EI calculation
    st.markdown("---")
    st.subheader("Erosion Index (EI) Calculation")
    st.markdown("""
    **Formula:** EI = (R × K × LS) / T
    
    **Interpretation:**
    - **EI ≥ 8.0** → Highly Erodible Land (HEL) per 7 CFR § 12.21
    - **EI < 8.0** → Not HEL per Part 616
    """)
    
    if "EI" in ei_data.columns:
        max_ei = ei_data["EI"].max()
        avg_ei = ei_data["EI"].mean()
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Maximum EI", f"{max_ei:.2f}")
        with col2:
            st.metric("Average EI", f"{avg_ei:.2f}")
        with col3:
            st.metric("HEL Threshold", "8.0 (ref: 7 CFR § 12.21)")
    
    # Download pre-filled AD1026
    st.markdown("---")
    if st.button("📥 Download Pre-filled AD1026 PDF", use_container_width=True):
        pdf_bytes = generate_ad1026_pdf(analysis_results)
        st.download_button(
            label="⬇️ Save AD1026.pdf",
            data=pdf_bytes,
            file_name="AD1026_HEL_Determination.pdf",
            mime="application/pdf"
        )


def show_technical_details_panel(analysis_results):
    """Uncertainty ranges, data sources, methodological notes."""
    
    st.subheader("RUSLE2 Component Uncertainties")
    
    # Uncertainty summary table
    uncertainty_data = {
        "Component": ["R-Factor", "K-Factor", "LS-Factor", "T-Factor"],
        "Source": [
            analysis_results["r_source"],
            "SSURGO",
            "USGS 3DEP 30m DEM",
            "SSURGO"
        ],
        "Uncertainty": [
            "±5-8%" if "NOAA" in analysis_results["r_source"] else "±20-30%",
            "±10%",
            "±5%",
            "±10%"
        ],
        "Notes": [
            "Point-specific from precipitation" if "NOAA" in analysis_results["r_source"] else "State-level average",
            "SSURGO soil survey data",
            "Flow accumulation algorithm",
            "Published NRCS soil loss tolerance"
        ]
    }
    
    uncertainty_df = pd.DataFrame(uncertainty_data)
    st.dataframe(uncertainty_df, use_container_width=True)
    
    st.markdown("---")
    st.subheader("Data Source Provenance")
    
    sources = {
        "SSURGO": "USDA NRCS Soil Survey Geographic Database (queried via SDA API)",
        "USGS 3DEP": "U.S. Geological Survey 3D Elevation Program 30m DEM",
        "R-Factor": analysis_results["r_source"],
        "State Detection": "Nominatim reverse geocoding (OpenStreetMap)",
        "Wetland Indicators": "NLCD 2021, NHD Plus High Resolution, SSURGO hydricrating"
    }
    
    for source, description in sources.items():
        st.markdown(f"- **{source}:** {description}")
    
    st.markdown("---")
    st.subheader("Methodology References")
    st.markdown("""
    - **RUSLE2 Formula:** R × K × LS / T (NRCS Part 616)
    - **HEL Threshold:** EI ≥ 8.0 per 7 CFR § 12.21
    - **LS Calculation:** True L × S from DEM-derived flow accumulation & slope steepness
    - **R-Factor Method:** Brown & Foster (1981) equation: R ≈ 0.04887 × P^1.61
    - **Wetland Detection:** Multi-source synthesis (hydric soils + drainage class + vegetation + hydrology)
    """)


def show_export_panel(analysis_results):
    """CSV, PDF, and form exports."""
    
    st.subheader("Export Results")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("📊 Export to CSV", use_container_width=True):
            csv_data = analysis_results["ei_results"].to_csv(index=False)
            st.download_button(
                label="⬇️ Download CSV",
                data=csv_data,
                file_name="EI_Analysis.csv",
                mime="text/csv"
            )
    
    with col2:
        if st.button("📄 Export to PDF Report", use_container_width=True):
            pdf_bytes = generate_conservationist_pdf(analysis_results)
            st.download_button(
                label="⬇️ Download PDF",
                data=pdf_bytes,
                file_name="HEL_Analysis_Report.pdf",
                mime="application/pdf"
            )
    
    with col3:
        if st.button("📋 AD1026 Form (NRCS)", use_container_width=True):
            pdf_bytes = generate_ad1026_pdf(analysis_results)
            st.download_button(
                label="⬇️ Download AD1026",
                data=pdf_bytes,
                file_name="AD1026_HEL_Determination.pdf",
                mime="application/pdf"
            )
```

### 2.4 Helper Functions for Data Preparation

```python
def prepare_farmer_tier_data(ei_results, wetland_assessment):
    """
    Simplify data for farmer view (summary only).
    
    Returns only high-level aggregations.
    """
    return {
        "max_ei": ei_results["EI"].max() if "EI" in ei_results else 0,
        "wetland_detected": (
            wetland_assessment.get("confidence") in ["High", "Medium"]
            if wetland_assessment else False
        ),
        "hel_status": "likely" if ei_results["EI"].max() >= 8.0 else "unlikely"
    }


def prepare_conservationist_tier_data(ei_results, wetland_assessment, ssurgo_data):
    """
    Prepare detailed data for conservationist view (all components).
    
    Returns full component-level breakdown.
    """
    return {
        "component_breakdown": ei_results.to_dict(orient="records"),
        "wetland_details": wetland_assessment,
        "ssurgo_metadata": ssurgo_data.get("metadata", {}),
        "field_verification_ready": True
    }
```

---

## 3. DETAILED COMPONENT BREAKDOWN

### 3.1 Farmer View Components (TIER 1)

#### Layout Structure
```
┌─────────────────────────────────────────┐
│  SHARED: Map Input (draw polygon)       │
├─────────────────────────────────────────┤
│  SHARED: Analyze Button                 │
├─────────────────────────────────────────┤
│  FARMER VIEW ONLY:                      │
│                                         │
│  ┌─────────────────────────────────────┐│
│  │ HEL Status Badge                    ││
│  │ ✅ LIKELY / ⚠️ MAYBE / ❌ UNLIKELY  ││
│  └─────────────────────────────────────┘│
│                                         │
│  ┌─────────────────────────────────────┐│
│  │ Wetland Status (Yes/No)             ││
│  │ Simple yes/no + contact NRCS        ││
│  └─────────────────────────────────────┘│
│                                         │
│  ┌─────────────────────────────────────┐│
│  │ Next Steps                          ││
│  │ 1. Contact NRCS                     ││
│  │ 2. Provide this reference #         ││
│  │ 3. Request field visit              ││
│  └─────────────────────────────────────┘│
│                                         │
│  ┌─ ACTIONS ──────────────────────────┐ │
│  │ [📍 Find NRCS] [🖨️ Print]          │ │
│  └─────────────────────────────────────┘ │
│                                         │
│  ┌─ SHARE LINK ───────────────────────┐ │
│  │ Copy & paste this link to NRCS:    │ │
│  │ [crp-tool.com/share/abc123...] │ │
│  └─────────────────────────────────────┘ │
│                                         │
│  HIDDEN: R, K, LS, T breakdown         │
│  HIDDEN: AD1026 form fields             │
│  HIDDEN: Component table                │
│  HIDDEN: Uncertainty metrics            │
└─────────────────────────────────────────┘
```

#### What's Hidden
- R-factor source details
- K, L, S, T individual factors
- Component-by-component data
- AD1026 form
- PDF pre-fill interface
- Export buttons

#### What's Displayed
| Element | Details |
|---------|---------|
| **HEL Status** | Binary color-coded badge + brief explanation |
| **Wetland Status** | Yes/No with simple recommendation |
| **Next Step** | "Contact NRCS" (always) |
| **NRCS Finder** | Button → launches map locator |
| **Print** | Generates simple 1-page printable summary |
| **Share Link** | Shareable URL with embedded results |

---

### 3.2 Conservationist View Components (TIER 2)

#### Tab Structure

**TAB 1: Results Overview**
- Maximum EI, HEL status, confidence level (3-column layout)
- Comparison button: "View Field Verification"
- Methodology notes
- Color-coded confidence indicator

**TAB 2: Field Verification**
- Field metadata (name, visit date)
- Soil observations (color, texture, hydric indicators)
- Slope observations (compare to automated LS)
- Vegetation notes
- Drainage observations
- "Compare Automated vs Field" button → Discrepancy analysis

**TAB 3: Component Breakdown**
- Full table: Soil Type | Acres | K | LS | R | T | EI | HEL/PHEL Status | Hydric
- Sortable columns
- Export CSV button
- Uncertainty badges on each row

**TAB 4: AD1026 Pre-fill**
- Read-only fields: R, K, LS, T (auto-filled)
- EI calculation display
- Formula explanation
- "Download Pre-filled AD1026.pdf" button
- Disclaimer: "Verify values before submission to NRCS"

**TAB 5: Technical Details**
- Component uncertainties table
- Data provenance (SSURGO, USGS 3DEP, NOAA, etc.)
- Methodology references (RUSLE2, CFR citations)
- DEM resolution note
- R-factor source & accuracy range

**TAB 6: Export & Download**
- [📊 CSV] button → EI_Analysis.csv
- [📄 PDF Report] button → Full technical report
- [📋 AD1026 Form] button → Pre-filled NRCS form
- License/disclaimer reminder

---

## 4. AD1026 PDF TEMPLATE DATA

### 4.1 USDA Form AD1026 Overview

**Official Form Name:** AD Form 1026 — "Highly Erodible Land (HEL) and Wetland Conservation Compliance Determination"

**Issued By:** USDA Farm Service Agency (FSA)

**Purpose:** Official determination of HEL and wetland status for CRP and commodity program compliance

**Key Fields to Pre-fill:**
1. **Field Identification** (user enters)
2. **RUSLE2 Erosion Index Calculation** (tool auto-fills)
3. **R-Factor** (rainfall erosivity) — Our tool provides
4. **K-Factor** (soil erodibility) — From SSURGO
5. **LS-Factor** (slope length & steepness) — From DEM
6. **T-Factor** (soil loss tolerance) — From SSURGO
7. **EI Calculation** (R × K × LS) / T — Our tool calculates
8. **HEL Determination** (EI ≥ 8.0?) — Our tool determines

### 4.2 Pre-fill Data Mapping

```python
def generate_ad1026_pdf(analysis_results):
    """
    Generate PDF with pre-filled AD1026 form data.
    
    Returns:
        bytes: PDF document ready for download
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from datetime import datetime
    
    # Extract analysis data
    ei_results = analysis_results["ei_results"]
    r_factor = analysis_results["r_factor"]
    r_source = analysis_results["r_source"]
    max_ei = ei_results["EI"].max() if "EI" in ei_results else 0
    
    # Create buffer
    from io import BytesIO
    pdf_buffer = BytesIO()
    
    # Create canvas
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    width, height = letter
    
    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "AD1026 — HEL Determination Report")
    
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 70, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    c.drawString(50, height - 85, f"Tool Version: CRP HEL Screening Tool v16")
    
    # Section 1: Field Identification
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 120, "1. FIELD IDENTIFICATION")
    
    c.setFont("Helvetica", 10)
    c.drawString(70, height - 140, "[Pre-fill from field verification data]")
    c.drawString(70, height - 155, "Field Name: ___________________")
    c.drawString(70, height - 170, "County: ___________________")
    c.drawString(70, height - 185, "Acres: ___________________")
    
    # Section 2: RUSLE2 Calculation
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 220, "2. RUSLE2 EROSION INDEX (EI) CALCULATION")
    
    c.setFont("Helvetica", 10)
    c.drawString(70, height - 240, f"Formula: EI = (R × K × LS) / T")
    
    # Data rows
    y_pos = height - 265
    c.drawString(70, y_pos, f"R-Factor (Rainfall Erosivity): {r_factor:.1f}")
    y_pos -= 15
    c.drawString(70, y_pos, f"    Source: {r_source}")
    
    y_pos -= 20
    if "K" in ei_results.columns:
        avg_k = ei_results["K"].mean()
        c.drawString(70, y_pos, f"K-Factor (Soil Erodibility): {avg_k:.3f} (average across {len(ei_results)} soil components)")
    
    y_pos -= 15
    c.drawString(70, y_pos, f"    Range: {ei_results['K'].min():.3f} — {ei_results['K'].max():.3f}")
    
    y_pos -= 20
    if "LS" in ei_results.columns:
        avg_ls = ei_results["LS"].mean()
        c.drawString(70, y_pos, f"LS-Factor (Slope Length & Steepness): {avg_ls:.3f} (from USGS 3DEP 30m DEM)")
    
    y_pos -= 20
    if "T" in ei_results.columns:
        avg_t = ei_results["T"].mean()
        c.drawString(70, y_pos, f"T-Factor (Soil Loss Tolerance): {avg_t:.1f} (average)")
    
    y_pos -= 25
    c.setFont("Helvetica-Bold", 10)
    c.drawString(70, y_pos, f"EROSION INDEX (EI): {max_ei:.2f}")
    
    # Section 3: HEL Determination
    c.setFont("Helvetica-Bold", 12)
    y_pos -= 40
    c.drawString(50, y_pos, "3. HEL DETERMINATION (7 CFR § 12.21)")
    
    c.setFont("Helvetica", 10)
    y_pos -= 20
    if max_ei >= 8.0:
        c.drawString(70, y_pos, "✓ HIGHLY ERODIBLE LAND (HEL) — EI ≥ 8.0")
        c.drawString(70, y_pos - 15, "This land is eligible for CRP with erosion control practices")
    else:
        c.drawString(70, y_pos, "✗ NOT HEL — EI < 8.0")
        c.drawString(70, y_pos - 15, "This land does not meet HEL criteria")
    
    # Footer
    c.setFont("Helvetica", 8)
    c.drawString(50, 40, "⚠️ DISCLAIMER: This report is for screening purposes only.")
    c.drawString(50, 25, "Official HEL determination requires NRCS field verification and Form AD1026 signed by NRCS staff.")
    
    # Save PDF
    c.save()
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()
```

---

## 5. NRCS OFFICE FINDER

### 5.1 Data Source

**Primary Source:** NRCS Service Locator API  
**Endpoint:** https://offices.sc.egov.usda.gov/  
**Fallback:** NRCS office database with ZIP code lookup

### 5.2 Integration Approach

```python
def find_nrcs_office(lat, lon):
    """
    Find nearest NRCS Service Center using coordinates.
    
    Args:
        lat (float): Latitude
        lon (float): Longitude
    
    Returns:
        dict: {
            "office_name": str,
            "address": str,
            "phone": str,
            "email": str,
            "website": str,
            "distance_miles": float,
            "hours": str
        }
    """
    try:
        # Query USDA Service Locator API
        url = "https://offices.sc.egov.usda.gov/locator/services"
        params = {
            "lat": lat,
            "lon": lon,
            "agency": "NRCS",
            "mode": "json"
        }
        
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        if data.get("offices"):
            # Return nearest office
            nearest = data["offices"][0]
            return {
                "office_name": nearest.get("name"),
                "address": nearest.get("address"),
                "phone": nearest.get("phone"),
                "email": nearest.get("email"),
                "website": nearest.get("url"),
                "distance_miles": nearest.get("distance"),
                "hours": nearest.get("hours")
            }
    except Exception as e:
        print(f"NRCS Service Locator API failed: {e}")
    
    # Fallback: Use hardcoded state-level NRCS offices
    return get_state_nrcs_office(lat, lon)


def show_nrcs_office_finder(ei_results, r_source):
    """
    Display NRCS office locator UI (farmer view).
    
    Allows user to:
    1. Auto-detect nearest office from field location
    2. Search by ZIP code
    3. View office hours and contact info
    4. Generate "bring these results to NRCS" guidance
    """
    st.subheader("📍 Find Your NRCS Office")
    
    # Get field coordinates from session state
    if st.session_state.last_polygon_bounds:
        lat = st.session_state.last_polygon_bounds[0]
        lon = st.session_state.last_polygon_bounds[1]
        
        # Auto-find nearest office
        office = find_nrcs_office(lat, lon)
        
        if office:
            st.success(f"Nearest NRCS Office: {office['office_name']}")
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"📍 **Address**\n{office['address']}")
            with col2:
                st.markdown(f"📞 **Phone**\n{office['phone']}")
            
            if office.get("hours"):
                st.markdown(f"⏰ **Hours**\n{office['hours']}")
            
            # Preparation guide
            st.markdown("""
            ---
            ### What to Bring to NRCS
            
            1. **This screening report** (use Print button above)
            2. **Field location** — Use coordinates: {:.4f}°N, {:.4f}°W
            3. **Property deed or tax map** — Shows ownership & field boundaries
            4. **Current land use** — Crop type, tillage practice, etc.
            5. **Soil information** — If available (soil type, drainage, etc.)
            
            ### What NRCS Will Do
            
            - **Verify HEL status** — Through official field visit & Part 616 determination
            - **Confirm wetland presence** — Using Wetland Determination Form
            - **Recommend CRP practices** — Based on your land characteristics
            - **Discuss enrollment** — Timeline, payments, contract terms
            """.format(lat, lon))
    else:
        st.warning("⚠️ Draw a polygon on the map first to find the nearest NRCS office.")
```

---

## 6. IMPLEMENTATION ROADMAP

### Phase 1: UI Toggle & Basic Split (Week 1-2)

**Deliverables:**
- [ ] Session state initialization (conservationist_mode toggle)
- [ ] Mode selector UI in sidebar
- [ ] Basic farmer view (HEL Yes/No, next step, NRCS finder button)
- [ ] Stub conservationist view (placeholder tabs)
- [ ] Git commit: "feat: Add two-tier UI toggle framework"

**Files to Modify:**
- `crp_final_v12_hf.py` → `crp_final_v16.py`

**Code Changes:**
1. Add session state initialization
2. Add mode selector UI
3. Create empty `show_farmer_view()` and `show_conservationist_view()` functions
4. Branch main logic at end of script: `if st.session_state.conservationist_mode: show_conservationist_view() else: show_farmer_view()`

---

### Phase 2: NRCS Office Finder (Week 2)

**Deliverables:**
- [ ] NRCS Service Locator API integration
- [ ] Fallback hardcoded state office database
- [ ] Interactive map showing nearest office
- [ ] Contact info display
- [ ] "Bring this report to NRCS" prep guide
- [ ] Git commit: "feat: Add NRCS office locator for farmers"

**Files to Modify:**
- `crp_final_v16.py` — Add `find_nrcs_office()` and `show_nrcs_office_finder()`

**Dependencies:**
- USDA Service Locator API (free, no auth required)

---

### Phase 3: AD1026 Pre-fill & PDF Export (Week 3)

**Deliverables:**
- [ ] AD1026 form data mapping (R, K, LS, T → form fields)
- [ ] Pre-filled PDF generation using reportlab or pypdf
- [ ] Conservationist "AD1026 Pre-fill" tab
- [ ] Download button with PDF generation
- [ ] Disclaimer: "Verify before submission"
- [ ] Git commit: "feat: Add AD1026 pre-filled PDF export"

**Files to Modify:**
- `crp_final_v16.py` — Add `generate_ad1026_pdf()` and form field mapping

**Dependencies:**
- reportlab (for PDF generation) or pypdf (for form filling)

---

### Phase 4: Field Verification & Comparison (Week 4)

**Deliverables:**
- [ ] Field verification checklist UI
- [ ] Data entry fields (field name, visit date, soil observations, slope notes)
- [ ] Automated vs. field comparison logic
- [ ] Discrepancy highlighting (e.g., "automated LS=1.5, field observed slope steeper")
- [ ] "Confidence adjustment" logic (user can flag low confidence)
- [ ] Git commit: "feat: Add field verification comparison for conservationists"

**Files to Modify:**
- `crp_final_v16.py` — Add `show_field_verification_panel()` and comparison logic

---

### Phase 5: Technical Details & Full Export (Week 4)

**Deliverables:**
- [ ] Component breakdown table (full data)
- [ ] Uncertainty ranges per component
- [ ] Data provenance (SSURGO, USGS 3DEP, NOAA, etc.)
- [ ] Methodology references & citations
- [ ] CSV export
- [ ] Full PDF technical report
- [ ] Git commit: "feat: Add technical details & multi-format export"

**Files to Modify:**
- `crp_final_v16.py` — Add `show_technical_details_panel()`, `show_export_panel()`, export functions

---

## 7. SESSION STATE VARIABLES REFERENCE

### Authentication & Mode
| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `conservationist_mode` | bool | False | Toggle between farmer/conservationist views |
| `user_authenticated` | bool | False | Track if user logged in (future: GitHub/email) |
| `user_email` | str | None | Logged-in user email (future) |

### Geospatial & Analysis
| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `last_polygon_bounds` | tuple | None | (min_lat, min_lon, max_lat, max_lon) from map |
| `last_wkt_string` | str | None | WKT polygon for API queries |
| `cached_analysis_results` | dict | None | Full analysis output (cache) |
| `cached_r_factor` | float | None | R-factor value (cache) |
| `cached_r_source` | str | None | R-factor source label (cache) |
| `cached_ei_data` | DataFrame | None | EI results per component (cache) |

### Conservationist Mode Data
| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `field_verification_data` | dict | {...} | Field visit observations |
| `ad1026_form_data` | dict | {} | AD1026 form fields |

---

## 8. CONDITIONAL RENDERING CHECKLIST

### Farmer View (Show)
- [ ] HEL status badge (Yes/No/Maybe)
- [ ] Simple wetland indicator (Yes/No)
- [ ] "Contact NRCS" next step
- [ ] Find NRCS office button
- [ ] Print/share buttons
- [ ] Confidence indicator (high/medium/low)

### Farmer View (Hide)
- [ ] R, K, L, S factor details
- [ ] Component breakdown table
- [ ] Uncertainty metrics
- [ ] AD1026 form
- [ ] Technical methodology
- [ ] Export options (except simple print)

### Conservationist View (Show)
- [ ] All tabs (Results, Verification, Component, AD1026, Technical, Export)
- [ ] Full data tables with component breakdown
- [ ] Uncertainty ranges
- [ ] Data provenance
- [ ] AD1026 pre-fill fields
- [ ] Export buttons (CSV, PDF, AD1026)

### Conservationist View (Hide)
- [ ] NRCS office finder (farmer-specific)
- [ ] Simplified status badges (show detailed analysis instead)

---

## 9. INTEGRATION CHECKLIST

### Code Quality
- [ ] All new functions have docstrings with Args, Returns, Raises
- [ ] Session state accessed via `st.session_state` (not direct assignment)
- [ ] Caching logic for expensive API calls (SSURGO, DEM, NOAA)
- [ ] Error handling with user-friendly messages
- [ ] No hardcoded credentials (use environment variables)

### UI/UX
- [ ] Mode toggle visible in sidebar (always accessible)
- [ ] Current mode labeled clearly
- [ ] Tab navigation clear and logically grouped
- [ ] Read-only fields clearly marked (e.g., `disabled=True` in forms)
- [ ] Download buttons labeled with file name & format
- [ ] Disclaimers visible on key results

### Data Integrity
- [ ] All inputs validated before API calls
- [ ] Bounds checking on coordinates
- [ ] Cache invalidation when polygon changes
- [ ] Field verification data not lost on tab switch
- [ ] Shareable links include required parameters

### Documentation
- [ ] Docstrings for all functions
- [ ] Inline comments for complex logic
- [ ] README.md updated with two-tier description
- [ ] CHANGELOG.md entry for v16

---

## 10. TESTING STRATEGY

### Unit Tests (Phase 5)

```python
# test_two_tier_ui.py

def test_farmer_view_hides_technical_fields():
    """Conservationist fields should not render in farmer view."""
    pass

def test_conservationist_view_shows_all_tabs():
    """All 6 tabs should render in conservationist mode."""
    pass

def test_session_state_persistence():
    """Field verification data should persist across reruns."""
    pass

def test_ad1026_pdf_generation():
    """Generated PDF should contain pre-filled form data."""
    pass

def test_nrcs_office_finder():
    """Should return nearest NRCS office within 50 miles."""
    pass

def test_cache_invalidation():
    """Cache should clear when polygon bounds change."""
    pass
```

### Manual Testing (Phase 5)

**Farmer View Scenarios:**
1. Draw polygon → See HEL status badge → Click "Find NRCS" → Map appears
2. Print button → PDF downloads with simple summary
3. Share link → URL with shareable code generated
4. Verify technical fields hidden (F12 developer tools)

**Conservationist View Scenarios:**
1. Toggle to conservationist mode → See all 6 tabs
2. Fill field verification → Data persists across tab switches
3. AD1026 tab → Download pre-filled PDF
4. Export tab → CSV, PDF Report, AD1026 all download correctly
5. Technical tab → Uncertainty table, provenance, references visible

---

## 11. DEPLOYMENT NOTES

### Environment Variables
```bash
# .env file
NOAA_CDO_TOKEN=pyhBbWOmnzTdfSJUCpLhDBafxwfCxCbW  # Already in v12
NRCS_API_KEY=  # If Service Locator requires auth
SSURGO_API_KEY=  # Free, but track usage
```

### Requirements.txt Updates
```
# New dependencies for Phase 3
reportlab>=4.0.0  # PDF generation

# Already present
streamlit>=1.30.0
folium>=0.14.0
pandas>=2.0.0
numpy>=1.24.0
requests>=2.31.0
```

### Render Deployment
- No code changes needed to Render config (already using streamlit==1.30.0)
- Environment variables auto-imported from Render dashboard
- Cache directory (/tmp) available for temporary PDF files

---

## 12. EXPECTED OUTCOMES

### For Farmers
- Fewer manual questions (HEL status is Yes/No, not a table)
- Clear next step (always "contact NRCS")
- NRCS office instantly available (no searching)
- Confidence in results (shows basis for determination)

### For Conservationists
- Field-verified EI calculations
- NRCS-ready documentation (pre-filled AD1026)
- Export-ready formats (CSV for analysis, PDF for submission)
- Methodology transparency (all sources documented)

### For NRCS Staff
- Incoming farmers have preliminary data
- Reduces verification time (tool handles SSURGO + DEM + R-factor)
- Standardized methodology (RUSLE2, Part 616, 7 CFR § 12.21)
- Shareable reports (email link from farmer to NRCS)

---

## Appendix A: File Structure After Implementation

```
/Users/vivekgupta/crp/
├── crp_final_v16.py                    # Main application (NEW)
├── crp_final_v12_hf.py                 # Previous version (archive)
├── rfactor_calculator.py                # R-factor module (existing)
├── wetland_features.py                  # Wetland detection (existing)
├── nrcs_office_locator.py               # NEW: NRCS office finder
├── ad1026_pdf_generator.py              # NEW: AD1026 form generation
├── requirements.txt                     # Updated with reportlab
├── README.md                            # Updated with two-tier description
├── CHANGELOG.md                         # v16 entry
└── tests/
    └── test_two_tier_ui.py              # NEW: Unit tests for two-tier system
```

---

## Appendix B: Reference Materials

**NRCS Documentation:**
- [Part 616 — Highly Erodible Land Conservation](https://directives.sc.egov.usda.gov/27640.wps)
- [7 CFR § 12.21 — HEL Determination](https://www.ecfr.gov/current/title-7/section-12.21)
- [USDA NRCS Field Office Technical Guide (FOTG)](https://efotg.sc.egov.usda.gov/)

**Scientific References:**
- [RUSLE2 Water Erosion Prediction](https://www.nrcs.usda.gov/resources/tech-tools/water-erosion-rusle2)
- [Brown & Foster (1981) — R-Factor Estimation](https://doi.org/10.1016/S0167-1987(81)80034-7)

**APIs & Data Sources:**
- [USDA SSURGO SDA Query Service](https://sdmdataaccess.sc.egov.usda.gov/)
- [USGS 3DEP Elevation Data](https://www.usgs.gov/3dep)
- [NOAA Climate Data Online](https://www.ncei.noaa.gov/cdo-web/)
- [USDA Service Locator](https://offices.sc.egov.usda.gov/)

---

**End of Implementation Plan**

*This document is version-controlled. Updates should be committed with:*  
`git commit -m "docs: Update Two-Tier UI Implementation Plan (Phase N)"`
