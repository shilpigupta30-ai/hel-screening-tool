# Local Testing Guide

## Quick Start

### 1. Create virtual environment
```bash
cd /Users/vivekgupta/crp
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
pip install reportlab  # For PDF generation
```

### 3. Set NOAA API token (optional but recommended)
```bash
export NOAA_CDO_TOKEN="your_token_here"
```
Or skip this—the app will use state-level fallback for R-Factor.

### 4. Run the app locally
```bash
streamlit run crp_final_v12.py
```

This opens the app at: **http://localhost:8501**

---

## What to Test

### ✅ Test 1: Field Verification Defaults
1. Draw a polygon on the map (or use test coordinates)
2. Click "Analyze Field"
3. Switch to **Conservationist Mode** (toggle in sidebar)
4. Go to **Tab 2: Field Verification**
5. **Check:** Slope Length and Steepness fields should show automated DEM values (not 100 ft / 5%)
6. **Expected:** Values like 85–120 feet and 4–8% slope

### ✅ Test 2: PDF Download
1. In Conservationist Mode, Tab 4: NRCS-CPA-026 Form
2. Click **"📄 NRCS-CPA-026 PDF"** button
3. Open the downloaded PDF
4. **Check Section B:** RUSLE2 Parameters should be readable with:
   - R-Factor on line 1
   - K-Factor on line 2
   - LS-Factor on line 3
   - T-Factor on line 4
   - Each with source/range info aligned on the right
5. **Expected:** Clean, professional formatting (not cramped)

### ✅ Test 3: Real-time Field Comparison
1. In Field Verification tab, adjust the slope inputs
2. **Check:** Comparison metrics update instantly
3. **Expected:** Field-measured vs Automated LS values change in real-time

---

## Test Coordinates (Pre-filled Options)

**Iowa field (good for testing DEM + R-Factor):**
- Latitude: 41.8886
- Longitude: -93.9255

**Wisconsin field (different R-Factor):**
- Latitude: 43.1939
- Longitude: -89.4055

**Missouri field (steeper terrain):**
- Latitude: 38.5816
- Longitude: -92.1723

---

## Troubleshooting

**"DEM fetch failed" error?**
- Normal fallback—the app will use slope approximation
- LS-Factor will say "Slope approximation (±23% error)"
- Field defaults will be 100 ft / 5%

**"NOAA CDO timeout"?**
- API sometimes slow—app falls back to state average
- R-Factor will say "State-level average (±20-30%)"

**Session state not persisting?**
- Reload the page (Ctrl+R)
- Streamlit reruns when you make changes

---

## After Testing

1. Check that PDF downloads successfully
2. Verify field defaults match DEM calculation
3. Test with 2-3 different locations
4. Then: `git push` and deploy to Render ✅

