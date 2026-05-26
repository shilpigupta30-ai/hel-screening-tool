# Two-Tier UI Implementation — Quick Reference Guide
## CRP HEL Screening Tool v16

**Quick Links:**
- **Full Implementation Plan:** `TWO_TIER_UI_IMPLEMENTATION_PLAN.md` (Sections 1-12, 100+ pages)
- **Visual Mockups & Flows:** `TWO_TIER_UI_MOCKUPS_AND_FLOWS.md` (Diagrams, user flows, error handling)
- **This Document:** Quick lookup tables & copy-paste code snippets

---

## 1. SESSION STATE VARIABLES (Copy-Paste)

```python
def init_session_state():
    """Initialize all session state for two-tier UI."""
    
    # Authentication & mode
    if "conservationist_mode" not in st.session_state:
        st.session_state.conservationist_mode = False
    if "user_authenticated" not in st.session_state:
        st.session_state.user_authenticated = False
    if "user_email" not in st.session_state:
        st.session_state.user_email = None
    
    # Geospatial & analysis
    if "last_polygon_bounds" not in st.session_state:
        st.session_state.last_polygon_bounds = None
    if "last_wkt_string" not in st.session_state:
        st.session_state.last_wkt_string = None
    if "cached_analysis_results" not in st.session_state:
        st.session_state.cached_analysis_results = None
    if "cached_r_factor" not in st.session_state:
        st.session_state.cached_r_factor = None
    if "cached_r_source" not in st.session_state:
        st.session_state.cached_r_source = None
    if "cached_ei_data" not in st.session_state:
        st.session_state.cached_ei_data = None
    
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
```

---

## 2. MODE SELECTOR UI (Copy-Paste)

```python
def render_mode_selector():
    """Render farmer/conservationist toggle in sidebar."""
    
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
    
    mode_label = "Conservationist View (Advanced)" if st.session_state.conservationist_mode else "Farmer View (Simple)"
    st.sidebar.info(f"**Currently viewing:** {mode_label}")
    
    if st.session_state.conservationist_mode:
        st.sidebar.warning(
            "⚠️ **Technical View Enabled**\n\n"
            "This view shows raw RUSLE2 component data and pre-fills official USDA forms. "
            "Ensure you have domain expertise before sharing results.",
            icon="🔐"
        )
```

---

## 3. MAIN RENDERING LOGIC (Copy-Paste)

Add this at the end of `crp_final_v16.py`:

```python
# =============================================================================
# MAIN APP LOGIC — TWO-TIER RENDERING
# =============================================================================

# Initialize session state
init_session_state()

# Render sidebar with mode selector
render_mode_selector()

# Draw map and get user input (SHARED)
st.subheader("Draw Your Field")
m = folium.Map(location=[40.0, -95.0], zoom_start=12)
# ... existing map code ...

if st.button("Analyze"):
    # Get polygon bounds
    if st.session_state.last_polygon_bounds:
        lat_bounds = (st.session_state.last_polygon_bounds[0], st.session_state.last_polygon_bounds[2])
        lon_bounds = (st.session_state.last_polygon_bounds[1], st.session_state.last_polygon_bounds[3])
        
        # Analyze field (shared data layer)
        tier = "conservationist" if st.session_state.conservationist_mode else "farmer"
        analysis_results = analyze_field_for_tier(lat_bounds, lon_bounds, tier=tier)
        
        # Render appropriate view
        if st.session_state.conservationist_mode:
            show_conservationist_view(analysis_results)
        else:
            show_farmer_view(analysis_results)
    else:
        st.error("Please draw a polygon on the map first.")
```

---

## 4. FARMER VIEW — SIMPLIFIED RESULTS (Copy-Paste)

```python
def show_farmer_view(analysis_results):
    """Simple, non-technical results for farmers."""
    
    st.markdown("## 🌾 Your Land Analysis")
    
    ei_results = analysis_results["ei_results"]
    max_ei = ei_results["EI"].max() if "EI" in ei_results else 0
    wetland_assessment = analysis_results["wetland_assessment"]
    
    # HEL Status
    st.subheader("Highly Erodible Land (HEL) Status")
    
    if max_ei >= 8.0:
        st.success("✅ LIKELY ELIGIBLE")
        status_text = "Based on our analysis, this land MAY QUALIFY for CRP."
    elif max_ei >= 6.0:
        st.warning("⚠️ MAYBE ELIGIBLE")
        status_text = "This land is BORDERLINE. An NRCS field visit is recommended."
    else:
        st.error("❌ LIKELY INELIGIBLE")
        status_text = "Based on our analysis, this land does NOT appear to qualify."
    
    st.markdown(status_text)
    
    # Wetland Status
    st.subheader("Wetland Status")
    if wetland_assessment and wetland_assessment.get("confidence") in ["High", "Medium"]:
        st.info(f"💧 {wetland_assessment['wetland_type']}")
    else:
        st.info("No wetland indicators detected (but confirm with site visit)")
    
    # Next Steps
    st.subheader("What to Do Next")
    st.markdown("""
    1. **Contact your local USDA Service Center**
    2. **Find your NRCS office** (button below)
    3. **Have your land details ready**
    4. **Request a field visit**
    """)
    
    # Action Buttons
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📍 Find Your NRCS Office", use_container_width=True):
            show_nrcs_office_finder(analysis_results)
    with col2:
        if st.button("🖨️ Print Results", use_container_width=True):
            pdf_bytes = generate_farmer_print_pdf(analysis_results)
            st.download_button(
                label="⬇️ Save PDF",
                data=pdf_bytes,
                file_name="HEL_Results.pdf",
                mime="application/pdf"
            )
    
    # Share Link
    st.subheader("Share with NRCS")
    st.markdown("Copy this link to share results with your NRCS office:")
    share_link = generate_shareable_link(analysis_results)
    st.code(share_link, language="text")
```

---

## 5. CONSERVATIONIST VIEW — TAB STRUCTURE (Copy-Paste)

```python
def show_conservationist_view(analysis_results):
    """Advanced technical view for conservationists."""
    
    st.markdown("## 🔬 Conservationist Analysis")
    
    # Create tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Results Overview",
        "Field Verification",
        "Component Breakdown",
        "AD1026 Pre-fill",
        "Technical Details",
        "Export & Download"
    ])
    
    with tab1:
        show_conservationist_results_overview(analysis_results)
    
    with tab2:
        show_field_verification_panel(analysis_results)
    
    with tab3:
        show_component_breakdown(analysis_results["ei_results"])
    
    with tab4:
        show_ad1026_prefill_panel(analysis_results)
    
    with tab5:
        show_technical_details_panel(analysis_results)
    
    with tab6:
        show_export_panel(analysis_results)
```

---

## 6. AD1026 PDF GENERATION (Key Function Signature)

```python
def generate_ad1026_pdf(analysis_results):
    """
    Generate pre-filled AD1026 form PDF.
    
    Args:
        analysis_results (dict): {
            "r_factor": float,
            "r_source": str,
            "ei_results": DataFrame (contains K, LS, T columns)
        }
    
    Returns:
        bytes: PDF document ready for download
    
    Pre-fills:
        • R-Factor: analysis_results["r_factor"]
        • K-Factor: analysis_results["ei_results"]["K"].mean()
        • LS-Factor: analysis_results["ei_results"]["LS"].mean()
        • T-Factor: analysis_results["ei_results"]["T"].mean()
        • EI Calculation: (R × K × LS) / T
        • HEL Determination: EI ≥ 8.0?
    """
    # Implementation using reportlab or pypdf
    # See IMPLEMENTATION_PLAN.md Section 4 for full code
    pass
```

---

## 7. NRCS OFFICE FINDER (Key Function Signature)

```python
def find_nrcs_office(lat, lon):
    """
    Find nearest NRCS Service Center.
    
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
    
    Uses:
        1. USDA Service Locator API (primary)
        2. Fallback: State-level hardcoded NRCS offices
    """
    pass


def show_nrcs_office_finder(analysis_results):
    """Display NRCS office locator UI (farmer view only)."""
    
    st.subheader("📍 Find Your NRCS Office")
    
    if st.session_state.last_polygon_bounds:
        lat = st.session_state.last_polygon_bounds[0]
        lon = st.session_state.last_polygon_bounds[1]
        
        office = find_nrcs_office(lat, lon)
        
        if office:
            st.success(f"Nearest NRCS Office: {office['office_name']}")
            st.markdown(f"📍 {office['address']}")
            st.markdown(f"📞 {office['phone']}")
            st.markdown(f"⏰ {office['hours']}")
    else:
        st.warning("Draw a polygon on the map first.")
```

---

## 8. HIDDEN/VISIBLE COMPONENTS CHECKLIST

### Farmer View (🌾)

| Component | Visible | Hidden | Notes |
|-----------|---------|--------|-------|
| Map input | ✅ | | Shared |
| HEL status badge | ✅ | | Simple (Yes/No/Maybe) |
| Wetland Yes/No | ✅ | | Simple indicator |
| Next step text | ✅ | | Always "Contact NRCS" |
| Find NRCS office | ✅ | | Farmer-specific action |
| Print/Share | ✅ | | Simple formats |
| R, K, L, S factors | | ✅ | Hide detail |
| Component table | | ✅ | Hide breakdown |
| AD1026 form | | ✅ | Hide form |
| Export buttons | | ✅ | Hide multiple formats |
| Uncertainty metrics | | ✅ | Hide technical |

### Conservationist View (🔬)

| Component | Visible | Hidden | Notes |
|-----------|---------|--------|-------|
| Map input | ✅ | | Shared |
| 6-tab interface | ✅ | | Full results, verification, components, form, technical, exports |
| R, K, L, S factors | ✅ | | All components shown |
| Component table | ✅ | | Full breakdown per soil type |
| AD1026 form fields | ✅ | | Pre-filled read-only |
| Uncertainty ranges | ✅ | | Technical uncertainties |
| Field verification | ✅ | | Checklist & comparison |
| Export buttons | ✅ | | CSV, PDF, AD1026 |
| NRCS office finder | | ✅ | Farmer-specific |
| Simple badges | | ✅ | Show detailed analysis instead |

---

## 9. IMPLEMENTATION PHASES CHECKLIST

### Phase 1: UI Toggle & Basic Split (Week 1-2)
- [ ] Session state init: `conservationist_mode`, caching variables
- [ ] Mode selector UI: toggle button in sidebar
- [ ] Create `show_farmer_view()` stub (HEL status, wetland, next step)
- [ ] Create `show_conservationist_view()` stub (empty tabs)
- [ ] Branching logic at end of script (if/else on session state)
- [ ] Test: Toggle between modes without data loss
- [ ] Commit: `git commit -m "feat: Add two-tier UI toggle framework"`

### Phase 2: NRCS Office Finder (Week 2)
- [ ] Implement `find_nrcs_office()` function (USDA API + fallback)
- [ ] Create `show_nrcs_office_finder()` UI
- [ ] Add prep guide: "What to bring to NRCS"
- [ ] Test on 5+ states (verify API accuracy)
- [ ] Commit: `git commit -m "feat: Add NRCS office locator"`

### Phase 3: AD1026 Pre-fill & PDF (Week 3)
- [ ] Map form fields: R, K, LS, T → AD1026 sections
- [ ] Implement `generate_ad1026_pdf()` (reportlab or pypdf)
- [ ] Create conservationist "AD1026 Pre-fill" tab
- [ ] Add download button & disclaimer
- [ ] Test: Verify PDF opens & displays correctly
- [ ] Commit: `git commit -m "feat: Add AD1026 pre-filled PDF export"`

### Phase 4: Field Verification & Comparison (Week 4)
- [ ] Create `show_field_verification_panel()` UI
- [ ] Add input fields: field name, date, soil notes, slope notes, etc.
- [ ] Implement comparison logic: automated vs field observations
- [ ] Add discrepancy highlighting & confidence adjustment
- [ ] Test: Verify data persists across tab switches
- [ ] Commit: `git commit -m "feat: Add field verification & comparison"`

### Phase 5: Technical Details & Export (Week 4)
- [ ] Create component breakdown table (full data)
- [ ] Implement uncertainty range calculations
- [ ] Add data provenance section (SSURGO, USGS, NOAA, etc.)
- [ ] Implement `generate_conservationist_pdf()` for full report
- [ ] Add CSV export functionality
- [ ] Test: Verify all exports open correctly & contain data
- [ ] Commit: `git commit -m "feat: Add technical details & multi-format export"`

### Phase 6: Testing & Documentation (Week 5)
- [ ] Unit tests: 6+ test cases per phase
- [ ] Manual testing: both user flows end-to-end
- [ ] Mobile responsiveness testing
- [ ] Update README.md with two-tier description
- [ ] Create CHANGELOG.md entry for v16
- [ ] Commit: `git commit -m "test: Add comprehensive test suite for two-tier UI"`

---

## 10. FILE CHANGES SUMMARY

| File | Change Type | Key Changes |
|------|------------|-------------|
| `crp_final_v16.py` | Major | New main file (clone v12_hf as base) |
| `requirements.txt` | Minor | Add: `reportlab>=4.0.0` (for PDF) |
| `README.md` | Minor | Update with two-tier description |
| `CHANGELOG.md` | New | v16 entry with phase breakdown |
| `nrcs_office_locator.py` | New | NRCS office finder module |
| `ad1026_pdf_generator.py` | New | AD1026 form generation module |
| `tests/test_two_tier_ui.py` | New | Unit tests for two-tier system |

---

## 11. ENVIRONMENT VARIABLES

```bash
# .env (add to Render dashboard)
NOAA_CDO_TOKEN=pyhBbWOmnzTdfSJUCpLhDBafxwfCxCbW  # Already set in v12
NRCS_API_KEY=                                      # If Service Locator requires auth
SSURGO_API_KEY=                                    # Free, but track usage
```

---

## 12. TESTING MATRIX

| Scenario | Farmer View | Conservationist View | Pass/Fail |
|----------|-------------|----------------------|-----------|
| Draw polygon → Analyze | ✅ HEL badge | ✅ 6 tabs + results | [ ] |
| Toggle mode | ✅ Switch works | ✅ All data persists | [ ] |
| Find NRCS | ✅ Map appears | ❌ Hidden | [ ] |
| Print | ✅ Simple PDF | ❌ Hidden | [ ] |
| Field verification | ❌ Hidden | ✅ Tab 2 form | [ ] |
| Component table | ❌ Hidden | ✅ Tab 3 full data | [ ] |
| AD1026 download | ❌ Hidden | ✅ Tab 4 PDF | [ ] |
| CSV export | ❌ Hidden | ✅ Tab 6 download | [ ] |
| Mobile (375px) | ✅ Responsive | ✅ Tabs scroll | [ ] |
| State not detected | ✅ Warning shown | ✅ All tabs work | [ ] |

---

## 13. KEY METRICS & SUCCESS CRITERIA

### Farmer User Experience
- [ ] HEL determination visible in < 2 seconds after analyze
- [ ] NRCS office found in < 30 seconds
- [ ] Single click to print or share
- [ ] No technical jargon on screen
- [ ] NPS score > 7/10

### Conservationist User Experience
- [ ] All 6 tabs load without delay
- [ ] Field verification data persists across tab switches
- [ ] AD1026 pre-fill accurate & NRCS-submittable
- [ ] CSV export readable in Excel with no formatting issues
- [ ] Confidence flags clear & actionable

### System Performance
- [ ] Map renders in < 3 seconds
- [ ] Analyze button triggers results in < 10 seconds
- [ ] PDF generation < 2 seconds
- [ ] No memory leaks (session state cleanup)
- [ ] Render deployment stays green (no errors)

---

## 14. KNOWN LIMITATIONS & FUTURE WORK

### Current Limitations (v16)
- State-level R-factor fallback (±20-30% error when NOAA unavailable)
- LS approximation for steep slopes (slope > 12%, triggers flag)
- No multi-field bulk analysis
- No user authentication (all results public shareable links)

### Future Enhancements (v17+)
- [ ] User accounts + saved analyses
- [ ] Bulk field upload (shapefile/GeoJSON)
- [ ] Historical trend analysis (compare 2020 vs 2024)
- [ ] Practice recommendation engine (beyond grouped CP suggestions)
- [ ] Integration with CRP signup system (NRCS FACS)
- [ ] Mobile app (React Native)

---

## 15. CONTACT & SUPPORT

**Questions?**
- Email: shilpigupta30@gmail.com
- GitHub: shilpigupta30-ai/crp-hel-screening-tool
- Issues: crp-hel-screening-tool/issues

**Domain Expert Feedback:**
- NRCS Part 616 methodology validation
- AD1026 form accuracy verification
- CRP practice recommendation logic
- Field testing in Iowa, Minnesota, Missouri (pilot)

---

**Document Version:** 1.0  
**Last Updated:** 2026-05-18  
**Status:** Ready for Phase 1 Implementation

*For detailed code & diagrams, see full implementation plan documents.*
