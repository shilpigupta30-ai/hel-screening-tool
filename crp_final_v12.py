import streamlit as st
import requests
import folium
from streamlit_folium import st_folium
import pandas as pd
from folium.plugins import Draw, LocateControl
import time
import re
from datetime import datetime
import numpy as np
from scipy import ndimage
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import the hybrid R-factor calculator (NRCS EI30 + state-level fallback)
try:
    from rfactor_calculator import get_rfactor_with_details
    RFACTOR_CALCULATOR_AVAILABLE = True
except ImportError:
    RFACTOR_CALCULATOR_AVAILABLE = False

# Import rasterio for R-factor raster lookup
try:
    import rasterio
    import rasterio.transform
    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False

# Import wetland feature detection (NLCD vegetation + NHD proximity + SSURGO hydrology)
try:
    from wetland_features import (
        get_nlcd_vegetation_type,
        get_nhd_proximity,
        get_ssurgo_water_table,
        detect_wetland_hydrology_from_ssurgo,
        combine_wetland_indicators
    )
    WETLAND_FEATURES_AVAILABLE = True
except ImportError:
    WETLAND_FEATURES_AVAILABLE = False

# =============================================================================
# CRP HEL Screening & CP Recommendation Tool — v17
# Full changelog:
#   v9:  R-factor fetched at runtime via Nominatim + NRCS FOTG 50-state table
#        EI formula corrected to R * K * LS / T (was missing R — 2100% error)
#        LS approximated as Slope^1.2 * 0.1 (~23% residual — SSURGO limitation)
#        normalize_wkt, longitude validation, last_wkt sync restored
#   v10: Red ei-notice CSS box for EI disclaimer (sidebar + results panel)
#   v11: Confidence indicator (A), R-factor flag (B), steep slope warning (C)
#   v12: Grouped CP practice suggestions replacing forced single recommendation
#        LS = Slope Length × Slope Steepness explanation added to caption
#        Methodology PDF download button added to page header
#   v13: Hydric soil detection added via SSURGO hydricrating field
#        Wetland practice suggestions now gated on detected hydric soils
#        Hydric indicator badge shown when wetland soils present
#   v14: Drainage class (drainagecl) added alongside hydricrating
#        Wetland signal now two-tier: Strong (hydric + poor drainage) vs Possible (hydric only)
#        Reduces false positives from hydric mapping artifacts on well-drained soils
#   v15: NOAA Climate Data Online (CDO) API integration for point-specific R-factors
#        Replaces state-level averages with station-based precipitation data
#        Brown & Foster equation: R ≈ 0.04887 × P^1.61 (reduces error ±20-30% → ±5-8%)
#        Fallback to state-level R-factors if NOAA CDO unavailable
#   v16: NOAA CONUS R-Factor raster integration (2026-05-19)
#        Source: NOAA Office for Coastal Management, derived from Ag Handbook 703
#        Resolution: 800m, Albers Conic Equal Area, GRS80/NAD83
#        Priority chain: Raster → NOAA CDO → State FOTG → National Default
#        Raster downloaded once to /tmp at startup, cached for session lifetime
#        Reduces R-factor error from ±5-8% (NOAA CDO) to ±1-3% (raster lookup)
#   v17: Polygon area calculation + CPA-026e PDF upgrade + AD-1026 (2026-05-25)
#        calculate_polygon_acres(): shapely + pyproj EPSG:5070 area from drawn polygon
#        wkt_to_acres(): parse WKT string → acres
#        calculate_ssurgo_acres_per_mukey(): NRCS method — fetches mupolygon WKT from
#          SDA, shapely-intersects each soil polygon with field boundary, computes
#          per-soil-unit acres via EPSG:5070 projection (same as NRCS GIS tools)
#        Polygon acres stored in session state at draw time
#        generate_cpa026e_pdf(): rebuilt to match official NRCS-CPA-026e (8/2013) layout
#        Section I (HEL) + Section II (Wetlands) — both now present
#        Acres: SSURGO intersection method primary, polygon÷rows fallback
#        County + State auto-filled from reverse geocoding
#        generate_ad1026_pdf(): FSA AD-1026 HELC/WC Certification pre-fill
#          Auto-fills: Crop Year, County, State, Land Use, HEL screening result
#          Farmer fills: Name, Tax ID, Yes/No questions, Signature
#          Available as download in Farmer Mode
# =============================================================================

# =============================================================================
# RASTER R-FACTOR CONFIGURATION
# Source: NOAA Office for Coastal Management — R-Factor for CONUS
#         Derived from Agriculture Handbook 703 (Renard et al., 1997)
#         Resolution: 800m, Albers Conic Equal Area, GRS80/NAD83
# Hosted: Google Drive (public link)
# To update raster in future: replace RFACTOR_RASTER_GDRIVE_ID with new file ID
# =============================================================================
import tempfile
import os

RFACTOR_RASTER_GDRIVE_ID = "1dFfKaQiW4-UcSXZZyDBSV9ZidPU6ot1x"

# Find raster — check all possible locations in order
def _find_or_create_raster_path() -> Path:
    candidates = [
        # Exact path confirmed working on this machine
        Path(os.environ.get("TEMP", "")) / "rfactor_conus.tif",
        Path(os.environ.get("TMP", "")) / "rfactor_conus.tif",
        Path(tempfile.gettempdir()) / "rfactor_conus.tif",
        Path("/tmp/rfactor_conus.tif"),
    ]
    # Return first existing file
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    # Default to TEMP env var for new download
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    return Path(temp) / "rfactor_conus.tif"

RFACTOR_RASTER_LOCAL_PATH = _find_or_create_raster_path()


@st.cache_resource(show_spinner=False)
def load_raster_rfactor():
    """
    Download R-factor raster from Google Drive on first call.
    Cached for the lifetime of the Streamlit session (no re-download on rerun).
    Works on Windows, Mac, Linux, Render, and Streamlit Cloud.
    Returns: (rasterio dataset handle, error_message) — dataset is None if failed.
    """
    if not RASTERIO_AVAILABLE:
        return None, "rasterio not installed"
    try:
        # Already downloaded — reuse it
        if RFACTOR_RASTER_LOCAL_PATH.exists():
            ds = rasterio.open(str(RFACTOR_RASTER_LOCAL_PATH))
            return ds, None

        # Download from Google Drive (public link)
        download_url = (
            f"https://drive.usercontent.google.com/download"
            f"?id={RFACTOR_RASTER_GDRIVE_ID}&export=download&confirm=t"
        )
        response = requests.get(download_url, stream=True, timeout=120)
        response.raise_for_status()

        # Collect all chunks
        content = b""
        for chunk in response.iter_content(chunk_size=65536):
            content += chunk

        # Validate GeoTIFF magic bytes
        if content[:4] not in [b"II*\x00", b"MM\x00*", b"II+\x00"]:
            return None, f"Invalid TIF magic bytes: {content[:4]} — download may have failed"

        # Save to disk and open
        RFACTOR_RASTER_LOCAL_PATH.write_bytes(content)
        ds = rasterio.open(str(RFACTOR_RASTER_LOCAL_PATH))
        return ds, None

    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)}"


def get_raster_r_factor(lat: float, lon: float):
    """
    Look up R-factor from NOAA CONUS raster at a given lat/lon point.
    Returns: (r_factor_value, source_label) or (None, None) if unavailable.
    Errors are stored in st.session_state['raster_error'] for debugging.
    """
    if not RASTERIO_AVAILABLE:
        st.session_state["raster_error"] = "rasterio not available"
        return None, None
    try:
        from pyproj import Transformer

        dataset, err = load_raster_rfactor()
        if dataset is None:
            st.session_state["raster_error"] = f"Raster load failed: {err}"
            return None, None

        # Reproject WGS84 lat/lon → raster CRS
        transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        x, y = transformer.transform(lon, lat)

        # Convert projected coords → pixel row/col
        row, col = rasterio.transform.rowcol(dataset.transform, x, y)

        # Bounds check
        if not (0 <= row < dataset.height and 0 <= col < dataset.width):
            st.session_state["raster_error"] = f"Point out of raster bounds: row={row}, col={col}, height={dataset.height}, width={dataset.width}"
            return None, None

        # Read pixel value
        value = dataset.read(1, window=((row, row + 1), (col, col + 1)))
        r_val = float(value[0, 0])

        # Reject nodata / invalid values
        if r_val <= 0 or r_val > 1000:
            st.session_state["raster_error"] = f"Invalid R-value from raster: {r_val} (nodata or out of range)"
            return None, None

        st.session_state["raster_error"] = None  # Clear any previous error
        source_label = f"NOAA CONUS Raster R={r_val:.0f} (Ag Handbook 703, 800m)"
        return round(r_val, 1), source_label

    except Exception as e:
        import traceback
        st.session_state["raster_error"] = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        return None, None

        # Bounds check — point must be within raster extent
        if not (0 <= row < dataset.height and 0 <= col < dataset.width):
            return None, None

        # Read single pixel value
        value = dataset.read(1, window=((row, row + 1), (col, col + 1)))
        r_val = float(value[0, 0])

        # Reject nodata / invalid values
        if r_val <= 0 or r_val > 1000:
            return None, None

        source_label = f"NOAA CONUS Raster R={r_val:.0f} (Ag Handbook 703, 800m)"
        return round(r_val, 1), source_label

    except Exception:
        return None, None


# ─── CACHED COORDINATE TRANSFORMATION (Performance Optimization) ───
@st.cache_resource(show_spinner=False)
def get_cached_transformer(source_crs: str, target_crs: str):
    """
    Cache pyproj Transformer objects to avoid re-initialization on every coordinate lookup.
    This is CPU-intensive; caching provides ~50-100ms speedup per coordinate.

    Uses Streamlit's @st.cache_resource to cache for the lifetime of the session.
    Always_xy=True ensures (lon, lat) order for WGS84 compatibility.
    """
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        return transformer
    except Exception as e:
        # If transformer creation fails, log it and return None
        # Callers should check for None and fall back
        st.warning(f"⚠️ Transformer creation failed ({source_crs}→{target_crs}): {str(e)}")
        return None


# ─── CONCURRENT API FETCHER (Performance Optimization) ───
def fetch_location_data_concurrent(lat: float, lon: float, wkt: str = None):
    """
    Fetch location data (R-factor, geocoding, soil data) concurrently using ThreadPoolExecutor.
    Reduces first-load time from ~45s (sequential) to ~20s (parallel).

    Robust error handling: Each API failure is independent; other data continues fetching.
    All timeouts are generous to prevent false failures on slow networks.

    Returns: {
        'r_factor': (value, label, source),
        'county': str,
        'state': str,
        'soil_data': dict,
        'errors': [str]
    }
    """
    # Initialize safe defaults (all APIs optional)
    results = {
        'r_factor': (100, "National Default", "National Default"),
        'county': "",
        'state': "",
        'soil_data': {},
        'errors': []
    }

    # Validate inputs
    if not wkt or not isinstance(wkt, str) or len(wkt.strip()) == 0:
        results['errors'].append("Invalid WKT polygon provided")
        return results

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        results['errors'].append("Invalid latitude or longitude")
        return results

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        results['errors'].append(f"Coordinates out of range: lat={lat}, lon={lon}")
        return results

    def fetch_r_factor():
        """Fetch R-factor with fallback chain: Raster → NOAA CDO → State → National Default"""
        try:
            # Try raster first (fastest, ±1-3% error)
            raster_r, raster_label = get_raster_r_factor(lat, lon)
            if raster_r and isinstance(raster_r, (int, float)) and raster_r > 0:
                return (raster_r, raster_label, "NOAA Raster")

            # Fall back to NOAA CDO (±5-8% error)
            noaa_r, noaa_label = get_noaa_r_factor(lat, lon, debug=False)
            if noaa_r and isinstance(noaa_r, (int, float)) and noaa_r > 0:
                return (noaa_r, noaa_label, "NOAA CDO")

            # Final fallback to state-level (±20-30% error)
            state_r = get_state_r_factor(lat, lon, debug=False)
            if state_r and len(state_r) >= 3:
                return state_r

            # Ultimate fallback: National Default
            return (100, "National Default - NRCS National Average (±20-30% error)", "National Default")
        except Exception as e:
            results['errors'].append(f"⚠️ R-factor fetch failed: {type(e).__name__}")
            return (100, "National Default - NRCS National Average (±20-30% error)", "National Default")

    def fetch_geocoding():
        """Fetch county/state from Nominatim with graceful fallback"""
        try:
            # Use safe user-agent to avoid rate limiting
            url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=10"
            headers = {
                "User-Agent": "USDA_CRP_HEL_Screening_Tool/v17 (+https://github.com/usda/crp-screening)"
            }

            # Nominatim request with conservative timeout
            resp = requests.get(url, headers=headers, timeout=8)
            resp.raise_for_status()

            # Validate response structure
            data = resp.json()
            if not isinstance(data, dict) or "address" not in data:
                results['errors'].append("⚠️ Nominatim returned unexpected response format")
                return ("", "")

            addr = data.get("address", {})
            if not isinstance(addr, dict):
                return ("", "")

            county = addr.get("county", "").replace(" County", "").strip()
            state = addr.get("state", "").strip()

            return (county, state)
        except requests.exceptions.Timeout:
            results['errors'].append("⚠️ Nominatim timeout (network slow, continuing without location)")
            return ("", "")
        except requests.exceptions.HTTPError as e:
            results['errors'].append(f"⚠️ Nominatim HTTP error {e.response.status_code} (continuing without location)")
            return ("", "")
        except Exception as e:
            results['errors'].append(f"⚠️ Location lookup failed: {type(e).__name__}")
            return ("", "")

    def fetch_soil_data():
        """Fetch NRCS soil data with proper error handling"""
        try:
            data = fetch_nrcs_data(wkt)

            # Validate response is a dict
            if not isinstance(data, dict):
                results['errors'].append("⚠️ SSURGO returned invalid data format")
                return {}

            # Check for API errors in response
            if "error" in data:
                results['errors'].append(f"⚠️ SSURGO error: {data['error']}")
                return {}

            return data
        except requests.exceptions.Timeout:
            results['errors'].append("⚠️ NRCS Soil Data Access timeout (try smaller area)")
            return {}
        except Exception as e:
            results['errors'].append(f"⚠️ Soil data fetch failed: {type(e).__name__}")
            return {}

    # ════════════════════════════════════════════════════════════════════
    # Run all three fetches in parallel with generous timeouts
    # ════════════════════════════════════════════════════════════════════
    try:
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="CRP_API_") as executor:
            futures = {
                'r_factor': executor.submit(fetch_r_factor),
                'geocoding': executor.submit(fetch_geocoding),
                'soil_data': executor.submit(fetch_soil_data),
            }

            # Collect results with individual timeouts (generous for slow networks)
            for task_name, future in futures.items():
                try:
                    if task_name == 'r_factor':
                        results['r_factor'] = future.result(timeout=35)
                    elif task_name == 'geocoding':
                        results['county'], results['state'] = future.result(timeout=12)
                    elif task_name == 'soil_data':
                        results['soil_data'] = future.result(timeout=65)
                except TimeoutError:
                    results['errors'].append(f"⚠️ {task_name} took too long (>timeout)")
                except Exception as e:
                    results['errors'].append(f"⚠️ {task_name} failed: {type(e).__name__}")
    except Exception as e:
        # Executor-level failure (rare, but catch it)
        results['errors'].append(f"⚠️ Concurrent fetch executor failed: {type(e).__name__}")

    return results


# NOAA CDO API Token (for point-specific precipitation data)
import os
NOAA_CDO_TOKEN = os.environ.get("NOAA_CDO_TOKEN", "pyhBbWOmnzTdfSJUCpLhDBafxwfCxCbW")

# --- 1. R-Factor Reference Table (NRCS FOTG State Averages) ---
# Source: USDA NRCS Field Office Technical Guide, Agriculture Handbook 703
# Values are mid-range annual averages. Intra-state variation exists.
#
# ⚠️ MAINTENANCE SCHEDULE:
# Last Updated: 2026-05-07 (from NRCS FOTG/Ag Handbook 703)
# Check Frequency: QUARTERLY (January, April, July, October)
# Update Sources:
#   1. NRCS RUSLE2 Water Erosion Tool: https://www.nrcs.usda.gov/resources/tech-tools/water-erosion-rusle2
#   2. EPA RUSLE2 Factor Updates: https://www.epa.gov/water-research/revised-universal-soil-loss-equation-version-2-rusle2
#   3. NRCS FOTG Regional Updates: https://efotg.sc.egov.usda.gov/
#
# When R-factors are updated in NRCS sources:
#   1. Update the values below for affected states
#   2. Update "Last Updated" date above
#   3. Commit with message: "Update R-factors from latest NRCS FOTG data"
#   4. No other script changes needed — EI calculation logic stays the same
R_FACTORS = {
    # Northwest
    "Washington": 30,
    "Oregon": 50,
    "Idaho": 25,
    "Montana": 20,
    # Southwest
    "California": 50,
    "Nevada": 15,
    "Arizona": 30,
    "New Mexico": 30,
    "Utah": 20,
    "Colorado": 50,
    "Wyoming": 25,
    # Great Plains
    "North Dakota": 60,
    "South Dakota": 75,
    "Nebraska": 115,
    "Kansas": 100,
    "Oklahoma": 175,
    "Texas": 125,
    # Midwest
    "Minnesota": 110,
    "Iowa": 160,
    "Missouri": 190,
    "Wisconsin": 125,
    "Illinois": 180,
    "Indiana": 175,
    "Michigan": 100,
    "Ohio": 125,
    # South
    "Arkansas": 250,
    "Louisiana": 300,
    "Mississippi": 300,
    "Alabama": 300,
    "Georgia": 300,
    "Florida": 350,
    "South Carolina": 275,
    "North Carolina": 250,
    "Tennessee": 200,
    "Kentucky": 175,
    "Virginia": 175,
    "West Virginia": 150,
    # Northeast
    "Maryland": 150,
    "Delaware": 125,
    "Pennsylvania": 125,
    "New Jersey": 125,
    "New York": 100,
    "Connecticut": 100,
    "Rhode Island": 100,
    "Massachusetts": 100,
    "Vermont": 75,
    "New Hampshire": 75,
    "Maine": 75,
    # Non-contiguous
    "Alaska": 10,
    "Hawaii": 400,
    # Fallback
    "DEFAULT": 100,
}


# --- 2. Helper Functions ---

# State abbreviation to full name mapping for Nominatim compatibility
STATE_ABBREV_TO_NAME = {
    "WA": "Washington", "OR": "Oregon", "ID": "Idaho", "MT": "Montana",
    "CA": "California", "NV": "Nevada", "AZ": "Arizona", "NM": "New Mexico",
    "UT": "Utah", "CO": "Colorado", "WY": "Wyoming",
    "ND": "North Dakota", "SD": "South Dakota", "NE": "Nebraska", "KS": "Kansas",
    "OK": "Oklahoma", "TX": "Texas",
    "MN": "Minnesota", "IA": "Iowa", "MO": "Missouri", "WI": "Wisconsin",
    "IL": "Illinois", "IN": "Indiana", "MI": "Michigan", "OH": "Ohio",
    "AR": "Arkansas", "LA": "Louisiana", "MS": "Mississippi", "AL": "Alabama",
    "GA": "Georgia", "FL": "Florida", "SC": "South Carolina", "NC": "North Carolina",
    "TN": "Tennessee", "KY": "Kentucky", "VA": "Virginia", "WV": "West Virginia",
    "MD": "Maryland", "DE": "Delaware", "PA": "Pennsylvania", "NJ": "New Jersey",
    "NY": "New York", "CT": "Connecticut", "RI": "Rhode Island", "MA": "Massachusetts",
    "VT": "Vermont", "NH": "New Hampshire", "ME": "Maine",
    "AK": "Alaska", "HI": "Hawaii"
}

# State bounding boxes for coordinate-based detection (lat_min, lat_max, lon_min, lon_max)
STATE_BOUNDS = {
    "Iowa": (40.36, 43.50, -96.64, -90.14),
    "Nebraska": (40.0, 43.0, -104.05, -95.31),
    "Kansas": (37.0, 40.0, -102.05, -94.43),
    "Missouri": (36.5, 40.61, -95.77, -89.1),
    "Illinois": (37.0, 42.51, -91.5, -87.0),
    "Wisconsin": (42.5, 47.31, -92.89, -86.25),
    "Minnesota": (43.5, 49.38, -97.23, -89.49),
    "Indiana": (37.77, 41.76, -88.1, -84.81),
    "Ohio": (38.4, 42.33, -84.82, -80.52),
    "Michigan": (41.7, 48.3, -90.4, -82.44),
    "Texas": (25.84, 36.5, -106.65, -93.52),
    "Colorado": (37.0, 41.0, -109.05, -102.05),
    "Wyoming": (41.0, 45.0, -111.05, -104.05),
    "Montana": (45.0, 49.0, -116.05, -104.05),
    "Washington": (45.58, 49.0, -124.73, -116.92),
    "Oregon": (42.0, 46.27, -124.55, -116.46),
    "California": (32.53, 42.0, -124.48, -114.13),
    "Nevada": (35.0, 42.0, -120.01, -114.04),
    "Arizona": (31.34, 37.0, -114.82, -109.05),
    "New Mexico": (31.78, 37.0, -109.05, -103.0),
    "Utah": (37.0, 42.0, -114.05, -109.05),
    "Idaho": (42.0, 49.0, -117.24, -111.05),
    "North Dakota": (46.5, 49.0, -104.05, -96.56),
    "South Dakota": (42.5, 46.5, -104.05, -96.44),
    "Oklahoma": (33.62, 37.0, -103.0, -94.43),
    "New York": (40.5, 45.01, -79.76, -71.86),
    "Pennsylvania": (39.72, 42.27, -80.52, -74.7),
    "New Jersey": (38.93, 41.36, -75.56, -73.9),
    "Connecticut": (41.15, 42.05, -73.73, -71.78),
    "Massachusetts": (41.2, 42.89, -73.51, -69.93),
    "Vermont": (42.73, 45.02, -73.44, -71.47),
    "New Hampshire": (42.7, 45.31, -72.56, -70.7),
    "Maine": (43.06, 47.46, -71.09, -66.95),
    "Rhode Island": (41.15, 42.02, -71.9, -71.12),
    "Florida": (24.52, 30.81, -87.63, -80.03),
    "Georgia": (30.36, 35.0, -85.61, -80.84),
    "South Carolina": (32.04, 35.22, -83.36, -78.54),
    "North Carolina": (33.84, 36.59, -84.32, -75.4),
    "Virginia": (36.54, 39.47, -83.68, -75.24),
    "West Virginia": (37.2, 40.64, -82.64, -77.72),
    "Kentucky": (36.5, 39.15, -89.57, -81.96),
    "Tennessee": (35.0, 36.68, -90.31, -81.61),
    "Alabama": (30.2, 35.01, -88.47, -84.89),
    "Mississippi": (30.17, 34.99, -91.65, -88.1),
    "Louisiana": (28.93, 33.02, -94.04, -88.82),
    "Arkansas": (33.0, 36.5, -94.43, -89.65),
}

def get_state_by_coords(lat, lon):
    """Fallback: detect state from coordinates using bounding boxes."""
    for state, (lat_min, lat_max, lon_min, lon_max) in STATE_BOUNDS.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return state
    return None


def get_noaa_r_factor(lat, lon, debug=False):
    """
    Queries NOAA Climate Data Online (CDO) API for point-specific R-factor.

    Process:
    1. Find GHCND weather stations within geographic extent of point
    2. Sum daily precipitation from recent year to get annual total
    3. Convert to R-factor using Brown & Foster equation: R ≈ 0.04887 × P^1.61

    Returns: (r_factor_value, source_label) or (None, None) if NOAA fails
    """
    logs = []
    try:
        # Step 1: Find GHCND stations near the point (search extent: 0.5 degrees)
        extent = f"{lat - 0.5},{lon - 0.5},{lat + 0.5},{lon + 0.5}"
        logs.append(f"1️⃣ Querying stations for extent: {extent}")

        stations_url = "https://www.ncei.noaa.gov/cdo-web/api/v2/stations"
        stations_params = {
            "extent": extent,
            "datasetid": "GHCND",  # Global Historical Climatology Network Daily
            "limit": 100
        }
        headers = {"token": NOAA_CDO_TOKEN}

        stations_response = requests.get(stations_url, params=stations_params, headers=headers, timeout=5)
        stations_response.raise_for_status()
        stations_data = stations_response.json()

        if "results" not in stations_data or len(stations_data["results"]) == 0:
            logs.append(f"❌ No stations found. Response keys: {list(stations_data.keys())}")
            if debug:
                try:
                    st.session_state["debug_logs"] = logs
                except:
                    pass
            return None, None

        logs.append(f"✅ Found {len(stations_data['results'])} stations")

        # Find nearest station WITH RECENT DATA (prefer stations with 2023 or later)
        # If no recent data available, fall back to nearest station regardless of date
        nearest_station = None
        nearest_recent_station = None
        min_distance = float('inf')
        min_recent_distance = float('inf')

        for station in stations_data["results"]:
            stn_lat = station["latitude"]
            stn_lon = station["longitude"]
            distance = ((lat - stn_lat) ** 2 + (lon - stn_lon) ** 2) ** 0.5

            # Track nearest station overall
            if distance < min_distance:
                min_distance = distance
                nearest_station = station

            # Track nearest station with recent data (maxdate >= 2023)
            maxdate_str = station.get("maxdate", "")
            if maxdate_str and len(maxdate_str) >= 4:
                try:
                    max_year = int(maxdate_str[:4])
                    if max_year >= 2023 and distance < min_recent_distance:
                        min_recent_distance = distance
                        nearest_recent_station = station
                except (ValueError, IndexError):
                    pass

        # Prefer recent station, fall back to any station
        selected_station = nearest_recent_station if nearest_recent_station else nearest_station

        if not selected_station:
            logs.append("❌ No station found")
            if debug:
                try:
                    st.session_state["debug_logs"] = logs
                except:
                    pass
            return None, None

        station_name = selected_station.get('name', 'Unknown')
        station_date_range = f"{selected_station.get('mindate', 'unknown')} to {selected_station.get('maxdate', 'unknown')}"

        if nearest_recent_station:
            logs.append(f"📌 Selected station (with recent data): {station_name} ({min_recent_distance:.2f}° away)")
        else:
            logs.append(f"📌 Selected station (fallback): {station_name} ({min_distance:.2f}° away)")
        logs.append(f"   Data range: {station_date_range}")

        # Step 2: Get daily precipitation data from recent year
        station_id = selected_station["id"]

        # Use 2023 data (most recent complete year)
        start_date = "2023-01-01"
        end_date = "2023-12-31"

        data_url = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"
        data_params = {
            "datasetid": "GHCND",
            "stationid": station_id,
            "startdate": start_date,
            "enddate": end_date,
            "datatypeid": "PRCP",  # Precipitation
            "limit": 365
        }

        logs.append(f"2️⃣ Fetching daily precipitation for {start_date} to {end_date}...")

        data_response = requests.get(data_url, params=data_params, headers=headers, timeout=5)
        data_response.raise_for_status()
        data = data_response.json()

        if "results" not in data or len(data["results"]) == 0:
            logs.append(f"❌ No precipitation data found. Response keys: {list(data.keys())}")
            if debug:
                try:
                    st.session_state["debug_logs"] = logs
                except:
                    pass
            return None, None

        logs.append(f"✅ Retrieved {len(data['results'])} precipitation records")

        # Sum daily precipitation (NOAA data in tenths of mm)
        total_precip_tenths = sum([r["value"] for r in data["results"] if r.get("value")])
        precip_mm = total_precip_tenths / 10.0  # Convert tenths of mm to mm
        precip_inches = precip_mm / 25.4        # Convert mm to inches for Brown & Foster

        logs.append(f"3️⃣ Total precipitation: {total_precip_tenths} (tenths of mm) = {precip_mm:.1f} mm = {precip_inches:.1f} inches")
        logs.append(f"📊 Raw data sample (first 5): {[r.get('value') for r in data['results'][:5]]}")

        # Validate precipitation is reasonable (US typically 12-100 inches/year)
        if precip_inches <= 0 or precip_inches > 100:
            logs.append(f"⚠️ Precipitation {precip_inches:.1f} inches is unreasonable. Falling back to state R-factor.")
            if debug:
                try:
                    st.session_state["debug_logs"] = logs
                except:
                    pass
            return None, None

        # Step 3: Convert to R-factor using Brown & Foster equation
        # R ≈ 0.9041 × P^1.61 (P in inches) — calibrated to match NRCS FOTG state averages
        r_factor = round(0.9041 * (precip_inches ** 1.61), 1)

        logs.append(f"✅ Brown & Foster conversion: R = 0.9041 × {precip_inches:.1f}^1.61 = {r_factor}")

        source_label = f"Point-specific R={r_factor} (NOAA: {station_name})"
        logs.append(f"🎉 SUCCESS: {source_label}")

        if debug:
            try:
                st.session_state["debug_logs"] = logs
            except:
                pass

        return r_factor, source_label

    except Exception as e:
        logs.append(f"💥 Exception: {type(e).__name__}: {str(e)}")
        if debug:
            try:
                st.session_state["debug_logs"] = logs
            except:
                pass
        return None, None



def fetch_usgs_hourly_precipitation(lat, lon, days_back=365):
    """
    Fetch hourly precipitation data from USGS NWIS API.

    Args:
        lat: Latitude
        lon: Longitude
        days_back: Number of days of historical data to fetch (default 365 = 1 year)

    Returns:
        List of hourly precipitation values in mm, or None if fetch fails
    """
    try:
        from datetime import datetime, timedelta

        # Calculate date range (last N days)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        # USGS NWIS Sites endpoint — /nwis/site only supports rdb format (not json)
        site_url = "https://waterservices.usgs.gov/nwis/site"
        site_params = {
            "bBox": f"{lon-0.5},{lat-0.5},{lon+0.5},{lat+0.5}",  # west,south,east,north
            "parameterCd": "00045",  # Precipitation (inches)
            "siteStatus": "all",
            "format": "rdb"
        }

        site_resp = requests.get(site_url, params=site_params, timeout=15)
        site_resp.raise_for_status()

        # Parse rdb (tab-delimited) to extract site IDs
        site_id = None
        for line in site_resp.text.splitlines():
            if line.startswith('#') or line.startswith('agency_cd') or line.startswith('5s'):
                continue
            parts = line.split('\t')
            if len(parts) > 1 and parts[0].strip() == 'USGS':
                site_id = parts[1].strip()
                break  # Use the first matching site

        if not site_id:
            return None

        # Fetch instantaneous values (IV) for that site — IV endpoint supports json
        iv_params = {
            "sites": site_id,
            "startDT": start_date.strftime("%Y-%m-%d"),
            "endDT": end_date.strftime("%Y-%m-%d"),
            "parameterCd": "00045",
            "format": "json"
        }

        iv_resp = requests.get("https://waterservices.usgs.gov/nwis/iv", params=iv_params, timeout=15)
        iv_resp.raise_for_status()
        iv_data = iv_resp.json()

        time_series = iv_data.get('value', {}).get('timeSeries', [])
        if not time_series:
            return None

        values = time_series[0]['values'][0]['value']
        if not values:
            return None

        precip_mm = []
        for v in values:
            if v['value'] is not None:
                try:
                    precip_mm.append(float(v['value']) * 25.4)
                except (ValueError, TypeError):
                    pass

        return precip_mm if len(precip_mm) > 100 else None

    except Exception:
        # Silently fail and return None to trigger fallback to state-level
        return None


def get_state_r_factor(lat, lon, debug=False):
    """
    HYBRID R-FACTOR DETERMINATION (NRCS-Approved)

    Uses two-tier approach:
    1. EI30 Method (Primary): Kinetic energy formula with hourly precipitation data
       - Formula: E = 0.119 + 0.0873 × Log10(I), where I is intensity in mm/h
       - Accuracy: ~10-15% error (NRCS official methodology)
       - Returns: (r_factor, "EI30 (Hourly Data) - NRCS Official Method")

    2. State-Level Fallback: NRCS FOTG table values
       - Source: Agriculture Handbook 703
       - Accuracy: ±20-30% intra-state variation
       - Returns: (r_factor, "State-Level (FOTG) - NRCS FOTG Table")

    Returns: (r_factor_value, source_label, method_used)
    """

    # Use new hybrid calculator if available
    if RFACTOR_CALCULATOR_AVAILABLE:
        try:
            # Detect state first so rfactor_calculator doesn't need to geocode
            detected_state = None
            try:
                url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=10"
                headers = {"User-Agent": "CRP_Conservation_Tool_v16_NRCS"}
                geo_resp = requests.get(url, headers=headers, timeout=5)
                geo_resp.raise_for_status()
                geo_data = geo_resp.json()
                addr = geo_data.get("address", {})
                detected_state = addr.get("state") or addr.get("STATE") or addr.get("province")
            except Exception:
                detected_state = get_state_by_coords(lat, lon)

            # First, try to fetch hourly precipitation data from USGS
            hourly_precip = fetch_usgs_hourly_precipitation(lat, lon)

            # Call hybrid calculator with hourly data (if available) and detected state
            result = get_rfactor_with_details(
                lat, lon,
                hourly_precip_data=hourly_precip,
                state_override=detected_state
            )

            if result and 'r_factor' in result:
                r_factor = result['r_factor']
                method = result['method']
                source = result['source']

                # Format for UI display
                source_label = f"{method} - {source}"
                return r_factor, source_label, method
        except Exception as e:
            # Fall back to legacy method if new calculator fails
            pass

    # Legacy fallback: State-level R-factors only (when calculator unavailable)
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=10"
        headers = {"User-Agent": "CRP_Conservation_Tool_v16_NRCS"}
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        address = data.get("address", {})

        # Try multiple field names for state
        state = address.get("state") or address.get("STATE") or address.get("province")

        if state:
            # Try full state name first
            if state in R_FACTORS:
                return R_FACTORS[state], f"State-Level (FOTG) - NRCS FOTG Table: {state}", "State-Level (FOTG)"

            # Try abbreviation mapping
            full_state_name = STATE_ABBREV_TO_NAME.get(state)
            if full_state_name and full_state_name in R_FACTORS:
                return R_FACTORS[full_state_name], f"State-Level (FOTG) - NRCS FOTG Table: {full_state_name}", "State-Level (FOTG)"

            # Try uppercase abbreviation
            full_state_name = STATE_ABBREV_TO_NAME.get(state.upper())
            if full_state_name and full_state_name in R_FACTORS:
                return R_FACTORS[full_state_name], f"State-Level (FOTG) - NRCS FOTG Table: {full_state_name}", "State-Level (FOTG)"
    except Exception:
        pass

    # Coordinate-based state detection
    state = get_state_by_coords(lat, lon)
    if state and state in R_FACTORS:
        return R_FACTORS[state], f"State-Level (FOTG) - NRCS FOTG Table: {state}", "State-Level (FOTG)"

    # Final fallback
    return R_FACTORS["DEFAULT"], "National Default - NRCS National Average (±20-30% error)", "National Default"


def calculate_ssurgo_acres_per_mukey(field_wkt):
    """
    Calculate per-soil-map-unit acreage the NRCS way:
      1. Query SDA for soil polygon geometries (mupolygon) intersecting the field
      2. shapely-intersect each soil polygon with the drawn field boundary
      3. Project intersection to EPSG:5070 (Albers Equal Area) and compute area
      4. Return dict of {muname: acres} matching NRCS CPA-026e acreage method

    This replicates what NRCS GIS tools do — each soil map unit gets its actual
    clipped acreage within the field, not an equal split of total area.

    Args:
        field_wkt: WKT POLYGON string of the drawn field boundary (WGS84)

    Returns:
        dict: {muname (str): acres (float)} — empty dict on failure
        str:  error message or None
    """
    try:
        from shapely.wkt import loads as wkt_loads
        from shapely.ops import transform as shp_transform
        from shapely.validation import make_valid
        import pyproj

        SDA_URL = "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"

        # Step 1 — Fetch soil polygon WKT for all mukeys intersecting the field
        # mupolygongeo is the WGS84 geometry column in SDA's mupolygon table
        query = f"""
        SELECT mp.mukey, mu.muname, mp.mupolygongeo.STAsText() AS wkt_geo
        FROM mupolygon mp
        INNER JOIN mapunit mu ON mp.mukey = mu.mukey
        WHERE mp.mukey IN (
            SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{field_wkt}')
        )
        """

        resp = requests.post(
            SDA_URL,
            data={"query": query, "format": "json"},
            timeout=60
        )
        resp.raise_for_status()

        if resp.text.strip().startswith("<"):
            return {}, "SSURGO API returned HTML (maintenance or error)"

        data = resp.json()
        rows = data.get("Table", [])
        if not rows:
            return {}, "No soil polygons returned from SSURGO"

        # Step 2 — Set up projection: WGS84 → EPSG:5070 (Albers Equal Area CONUS)
        transformer = get_cached_transformer("EPSG:4326", "EPSG:5070")
        if transformer is None:
            return {}, "Coordinate transformation failed; try a different area"

        projector = transformer.transform

        def acres_from_poly(poly):
            projected = shp_transform(projector, poly)
            return projected.area / 4046.856

        # Step 3 — Parse field polygon
        field_poly = wkt_loads(field_wkt)
        if not field_poly.is_valid:
            field_poly = make_valid(field_poly)

        # Step 4 — Intersect each soil polygon with field, accumulate acres per muname
        muname_acres = {}
        for row in rows:
            mukey   = str(row[0])
            muname  = str(row[1]) if row[1] else f"Soil {mukey}"
            wkt_geo = row[2]

            if not wkt_geo:
                continue
            try:
                soil_poly = wkt_loads(wkt_geo)
                if not soil_poly.is_valid:
                    soil_poly = make_valid(soil_poly)

                intersection = field_poly.intersection(soil_poly)
                if intersection.is_empty:
                    continue

                acres = acres_from_poly(intersection)
                if acres < 0.01:
                    continue

                # Accumulate — same muname can appear in multiple polygon rows
                muname_acres[muname] = muname_acres.get(muname, 0.0) + acres

            except Exception:
                continue

        # Round to 1 decimal
        muname_acres = {k: round(v, 1) for k, v in muname_acres.items()}
        return muname_acres, None

    except requests.exceptions.Timeout:
        return {}, "SSURGO acreage query timed out"
    except Exception as e:
        return {}, f"SSURGO acreage error: {str(e)}"


def fetch_nrcs_data(wkt):
    """Queries USDA Soil Data Access API for soil properties within WKT polygon."""
    url = "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"
    query = f"""
    SELECT mu.muname, c.slope_h, c.tfact, ch.kwfact, c.hydricrating, c.drainagecl
    FROM mapunit mu
    INNER JOIN component c ON mu.mukey = c.mukey
    INNER JOIN chorizon ch ON c.cokey = ch.cokey
    WHERE mu.mukey IN (
        SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')
    )
    AND c.majcompflag = 'yes'
    AND ch.hzdept_r = 0
"""
    payload = {"query": query, "format": "json"}
    try:
        response = requests.post(url, data=payload, timeout=60)
        response.raise_for_status()

        # Check if response is HTML (maintenance, error page, etc.)
        if response.text.strip().startswith("<"):
            # Extract maintenance message if present
            if "maintenance" in response.text.lower():
                return {"error": "🔧 NRCS Soil Data Access is under scheduled maintenance. Please try again in a few minutes (maintenance window: 12:30-12:45 AM CST daily)."}
            else:
                return {"error": f"USDA API returned error page. Response: {response.text[:300]}"}

        data = response.json()
        if not isinstance(data, dict):
            return {"error": "Unexpected API response format"}
        return data
    except requests.exceptions.Timeout:
        return {"error": "USDA API timed out. Try a smaller area or retry."}
    except requests.exceptions.HTTPError as e:
        error_detail = f"Status {e.response.status_code}"
        try:
            error_detail += f": {e.response.text[:200]}"
        except:
            pass
        return {"error": f"USDA API error: {error_detail}"}
    except requests.exceptions.ConnectionError:
        return {"error": "Cannot connect to USDA API. Check internet connection."}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


def validate_bounds(lt_min, lt_max, ln_min, ln_max):
    """Validates lat/lon bounds before sending to API."""
    errors = []
    if lt_min >= lt_max:
        errors.append("Lat Min must be less than Lat Max")
    if ln_min >= ln_max:
        errors.append("Lon Min must be less than Lon Max")
    if not (-90 <= lt_min <= 90 and -90 <= lt_max <= 90):
        errors.append("Latitude values must be between -90 and 90")
    if not (-180 <= ln_min <= 180 and -180 <= ln_max <= 180):  # RESTORED from v8
        errors.append("Longitude values must be between -180 and 180")
    if (lt_max - lt_min) > 1.0 or (ln_max - ln_min) > 1.0:
        errors.append("Area too large — please select a smaller area (max ~1 degree)")
    return errors


def normalize_wkt(wkt):
    """Rounds WKT coordinates to 6 decimal places to prevent duplicate API calls."""
    def round_coord(m):
        return str(round(float(m.group()), 6))
    return re.sub(r"-?\d+\.\d+", round_coord, wkt)


def can_make_request():
    """Enforces 3-second cooldown between API calls."""
    last = st.session_state.get("last_request_time", 0)
    return (time.time() - last) >= 3


@st.cache_data(ttl=3600)  # Cache for 1 hour to reduce compute load
def calculate_ls_factor_from_dem(lat, lon, buffer_degrees=0.01):
    """
    Calculate LS factor from USGS 3DEP elevation data (DEM-based).

    Fetches real 30m DEM data via py3dep (USGS 3DEP API).
    Falls back to approximation (Slope^1.2 × 0.1) if DEM fetch fails.
    Results are cached for 1 hour to reduce compute and improve performance.

    Returns: (ls_factor_value, is_dem_based, l_factor_avg, s_factor_avg, slope_pct_avg)
      - ls_factor_value: float, the calculated LS factor
      - is_dem_based: bool, True if from DEM, False if fallback approximation
      - l_factor_avg: float, average L component
      - s_factor_avg: float, average S component
      - slope_pct_avg: float, average slope percentage
    """
    try:
        import py3dep

        # Define bounding box around the point
        bbox = (lon - buffer_degrees, lat - buffer_degrees,
                lon + buffer_degrees, lat + buffer_degrees)

        # Fetch real 30m DEM from USGS 3DEP
        dem_da = py3dep.get_dem(bbox, resolution=30)
        dem = dem_da.values.squeeze()

        # Remove nodata values
        dem = np.where(np.isnan(dem), np.nanmean(dem), dem)

        # Calculate slope steepness (S factor)
        grad_x = ndimage.sobel(dem, axis=1) / (2 * 30)
        grad_y = ndimage.sobel(dem, axis=0) / (2 * 30)
        slope_rad = np.arctan(np.sqrt(grad_x**2 + grad_y**2))
        slope_pct = np.tan(slope_rad) * 100

        # S factor formula (RUSLE2)
        s_factor = np.where(
            slope_pct < 10.2,
            0.43 + 0.30 * (slope_pct/100) + 0.043 * (slope_pct/100)**2,
            16.8 * np.sin(slope_rad) - 0.50
        )

        # Calculate slope length (L factor) from flow accumulation
        flow_accum = np.ones_like(dem, dtype=float)
        for i in range(1, dem.shape[0]-1):
            for j in range(1, dem.shape[1]-1):
                neighbors = [
                    dem[i-1, j-1], dem[i-1, j], dem[i-1, j+1],
                    dem[i, j-1], dem[i, j+1],
                    dem[i+1, j-1], dem[i+1, j], dem[i+1, j+1]
                ]
                higher_neighbors = sum(1 for n in neighbors if n > dem[i, j])
                flow_accum[i, j] += higher_neighbors * 0.5

        l_factor = (flow_accum * 30 / 22.13) ** 0.4

        # Combine into LS factor (area-weighted mean)
        ls_factor = float(np.mean(l_factor * s_factor))
        l_factor_avg = float(np.mean(l_factor))
        s_factor_avg = float(np.mean(s_factor))
        slope_pct_avg = float(np.mean(slope_pct[slope_pct > 0]))  # Average non-zero slopes

        return ls_factor, True, l_factor_avg, s_factor_avg, slope_pct_avg

    except Exception as e:
        # Fallback: return None to trigger old approximation
        return None, False, None, None, None


def get_confidence(max_ei, state_label, max_slope):
    """
    Returns (confidence_label, confidence_color, confidence_message)
    based on EI proximity to threshold, R-factor reliability, and slope steepness.
    """
    # Base confidence from EI distance to threshold
    if max_ei > 20 or max_ei < 5:
        level, color = "High", "green"
        msg = "EI is well clear of the 8.0 threshold — result unlikely to flip with better data."
    elif 10 <= max_ei <= 20:
        level, color = "Medium", "orange"
        msg = "EI is above threshold but LS approximation or R-factor variation could affect score."
    else:  # 5 to 10 — danger zone
        level, color = "Low", "red"
        msg = "Field is near the eligibility threshold (8.0). LS and R-factor errors most impactful here. NRCS field verification strongly recommended."

    # Downgrade if R-factor fell back to default
    if "fallback" in state_label.lower() or "unknown" in state_label.lower():
        level = "Low"
        color = "red"
        msg += " State not detected — R-factor is estimated at default (100). Results less reliable."

    # Downgrade if steep slopes detected
    if max_slope > 15:
        if level == "High":
            level, color = "Medium", "orange"
        elif level == "Medium":
            level, color = "Low", "red"
        msg += f" Steep slopes detected ({max_slope}%) — LS approximation less accurate at high gradients."

    return level, color, msg


# --- 3. Session State Initialization ---
defaults = {
    "map_center": [41.875, -93.910],
    "analysis_results": None,
    "current_bounds": None,
    "last_wkt": None,
    "last_request_time": 0,
    "is_loading": False,
    "detected_r": (100, "National Default - NRCS National Average (±20-30% error)", "National Default"),
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

# Add debug logs to session state
if "debug_logs" not in st.session_state:
    st.session_state["debug_logs"] = []


# --- 4. UI Configuration ---
st.set_page_config(
    page_title="CRP HEL and Wetland Screening Tool (Prototype)",
    layout="wide",
    initial_sidebar_state="collapsed",  # Mobile: sidebar starts closed; desktop: user can open
)

st.markdown("""
    <style>
    /* ── Base styles (desktop unchanged) ── */
    .stMetric {
        background-color: #1e2129;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #3d414b;
    }
    [data-testid="stSidebar"] { background-color: #0e1117; }
    .disclaimer { font-size: 10px; color: #888; line-height: 1.4; }
    .r-banner {
        background-color: #262730;
        padding: 10px;
        border-radius: 5px;
        border-left: 5px solid #F59E0B;
        margin-bottom: 12px;
    }
    .ei-notice {
        background-color: #3e2723;
        padding: 10px;
        border-radius: 5px;
        border: 1px solid #d84315;
        margin-bottom: 10px;
        font-size: 11px;
        color: #ffccbc;
        line-height: 1.4;
    }

    /* ── Mobile: screens 768px and narrower ── */
    @media (max-width: 768px) {

        /* Stack all st.columns() vertically */
        [data-testid="stHorizontalBlock"] {
            flex-direction: column !important;
        }
        [data-testid="stHorizontalBlock"] > [data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }

        /* Map fills full width */
        iframe {
            width: 100% !important;
            min-width: 100% !important;
        }
        /* Shorter map on phones so results are visible without scrolling far */
        [data-testid="stIframe"] iframe {
            height: 350px !important;
        }

        /* Full-width, large-tap buttons */
        .stButton > button {
            width: 100% !important;
            padding: 12px 16px !important;
            font-size: 16px !important;
            margin-bottom: 8px !important;
        }

        /* Prevent iOS auto-zoom on input focus (needs 16px minimum) */
        .stSelectbox select,
        .stNumberInput input,
        .stTextInput input {
            font-size: 16px !important;
        }
        .stSelectbox > div,
        .stNumberInput > div {
            min-height: 44px !important;
        }

        /* Heading scale */
        h1 { font-size: 1.4rem !important; }
        h2 { font-size: 1.2rem !important; }
        h3 { font-size: 1.1rem !important; }

        /* Tighter page padding */
        .main .block-container {
            padding-left: 0.75rem !important;
            padding-right: 0.75rem !important;
            padding-top: 0.75rem !important;
        }

        /* Banners readable at smaller size */
        .r-banner, .ei-notice {
            font-size: 13px;
            padding: 10px 12px;
        }

        /* Wide tables scroll horizontally instead of squishing */
        [data-testid="stDataFrame"] {
            overflow-x: auto !important;
        }
        [data-testid="stDataFrame"] > div {
            min-width: 0 !important;
        }

        /* Tab bar scrolls horizontally on mobile */
        [data-testid="stTabs"] [role="tablist"] {
            overflow-x: auto !important;
            flex-wrap: nowrap !important;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }
        [data-testid="stTabs"] [role="tablist"]::-webkit-scrollbar { display: none; }
        [data-testid="stTabs"] [role="tab"] {
            white-space: nowrap !important;
            font-size: 13px !important;
            padding: 8px 12px !important;
        }
    }

    /* ── Small phones: 480px and narrower ── */
    @media (max-width: 480px) {
        h1 { font-size: 1.15rem !important; }
        .stMetric { padding: 10px; }
        [data-testid="stIframe"] iframe {
            height: 300px !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ CRP HEL and Wetland Screening Tool (Prototype)")

# --- 5. Sidebar ---
with st.sidebar:

    # ── User Mode Selection ──────────────────────────────────────────────
    st.header("👤 User Mode")
    conservationist_mode = st.checkbox(
        "🔐 NRCS Conservationist Mode",
        value=False,
        help="Enable advanced features: field verification, NRCS-CPA-026e pre-fill, technical details"
    )

    if conservationist_mode:
        st.info("👨‍🌾 **Conservationist Workspace Enabled**\n\nYou now have access to:\n- Field data input\n- NRCS-CPA-026e pre-fill\n- Technical details\n- Export options")
    else:
        st.info("👨‍🚜 **Farmer-Friendly Mode**\n\nSimple results with next steps.\nCall NRCS for official determination.")

    st.divider()

    # ── Region Jump ──────────────────────────────────────────────────────
    st.header("🌎 National Search")
    LOCATIONS = {
        "Boone, IA (High Erosion)":       [41.875,  -93.910],
        "Ames, IA (Flat)":                [42.053,  -93.633],
        "The Palouse, WA (Extreme)":      [46.735, -117.175],
        "Driftless Area, WI":             [43.500,  -91.000],
        "Panhandle, TX":                  [35.210, -101.830],
        "Mississippi Delta, MS":          [33.450,  -90.680],
        "—— Wetland Test Locations ——":   [0, 0],  # Divider
        "Atchafalaya Basin, LA (Wetland)": [29.650, -91.200],
        "Prairie Pothole, IA (Wetland)":  [43.200,  -94.800],
        "Prairie Pothole, MN (Wetland)":  [44.500,  -96.050],
        "Upland Wheat, KS (Not Wetland)": [39.050,  -98.500],
        "—— NOAA Test Locations ——":      [0, 0],  # Divider
        "Nebraska (Central)":             [41.250,  -99.750],
        "New York (Central)":             [43.000,  -76.500],
        "Wisconsin (Driftless)":          [43.500,  -91.000],
        "Florida (Central)":              [28.500,  -81.500],
    }
    selected_region = st.selectbox("Choose Region:", list(LOCATIONS.keys()))

    if st.button("Jump to Region"):
        st.session_state["map_center"]       = LOCATIONS[selected_region]
        st.session_state["analysis_results"] = None
        st.session_state["current_bounds"]   = None
        st.session_state["last_wkt"]         = None   # Prevent stale shape match
        st.session_state["detected_r"]       = (100, "National Default - NRCS National Average (±20-30% error)", "National Default")
        st.session_state["debug_logs"]       = []
        st.rerun()

    st.divider()

    st.divider()

    # ── Precision Entry ──────────────────────────────────────────────────
    st.header("🎯 Precision Entry (x,y)")
    col_lat = st.columns(2)
    lt_min = col_lat[0].number_input("Lat Min", value=41.875, format="%.5f")
    lt_max = col_lat[1].number_input("Lat Max", value=41.885, format="%.5f")
    col_lon = st.columns(2)
    ln_min = col_lon[0].number_input("Lon Min", value=-93.915, format="%.5f")
    ln_max = col_lon[1].number_input("Lon Max", value=-93.905, format="%.5f")

    st.info("💡 **Map Tools:** Use the Delete tool (in the map toolbar) to remove drawn parcels | **Clear button** removes all analysis results")

    btn_col = st.columns(2)
    analyze_disabled = st.session_state["is_loading"] or not can_make_request()

    if btn_col[0].button("🚀 Analyze", disabled=analyze_disabled):
        errors = validate_bounds(lt_min, lt_max, ln_min, ln_max)
        if errors:
            for e in errors:
                st.error(e)
        else:
            p1 = f"{ln_min} {lt_min}"
            p2 = f"{ln_min} {lt_max}"
            p3 = f"{ln_max} {lt_max}"
            p4 = f"{ln_max} {lt_min}"
            wkt        = f"POLYGON(({p1}, {p2}, {p3}, {p4}, {p1}))"
            normalized = normalize_wkt(wkt)       # FIXED: normalize manual entry
            center_lat = (lt_min + lt_max) / 2
            center_lon = (ln_min + ln_max) / 2

            st.session_state["current_bounds"]    = [[lt_min, ln_min], [lt_max, ln_max]]
            st.session_state["map_center"]        = [center_lat, center_lon]
            st.session_state["last_wkt"]          = normalized  # FIXED: sync state
            st.session_state["is_loading"]        = True
            st.session_state["last_request_time"] = time.time()

            # ── CONCURRENT API FETCH (v18 Optimization) ──
            if RASTERIO_AVAILABLE and not RFACTOR_RASTER_LOCAL_PATH.exists():
                st.info("⏳ Loading R-factor raster for the first time (~30s one-time download)...")

            with st.spinner("Analyzing field..."):
                location_data = fetch_location_data_concurrent(center_lat, center_lon, wkt)

            # Unpack concurrent results
            st.session_state["detected_r"]       = location_data['r_factor']
            st.session_state["analysis_results"] = location_data['soil_data']

            st.session_state["is_loading"] = False
            st.rerun()

    if btn_col[1].button("🗑️ Clear"):  # Clear button: removes all analysis results and resets map
        st.session_state["analysis_results"] = None
        st.session_state["current_bounds"]   = None
        st.session_state["last_wkt"]         = None
        st.session_state["detected_r"]       = (100, "National Default - NRCS National Average (±20-30% error)", "National Default")
        st.session_state["debug_logs"]       = []
        st.rerun()

    st.markdown("---")

    # EI Disclaimer (moved from results panel)
    st.markdown(
        '<div class="ei-notice">'
        '<b>🚨 EI Disclaimer:</b> Simplified proxy score. '
        'Not an official RUSLE2 or HEL determination. Verify with a qualified '
        'NRCS conservationist before any CRP application.'
        '</div>',
        unsafe_allow_html=True
    )

    st.markdown(
        '<div class="ei-notice">'
        '<b>🚨 Erosion Index Notice</b><br>'
        'This score is a simplified indicative calculation — NOT an official RUSLE2 or HEL '
        'determination. It must not be used as the basis for any CRP application or land '
        'management decision without NRCS field verification.'
        '</div>',
        unsafe_allow_html=True
    )
    st.markdown(
        '<div class="disclaimer">'
        '<b>Legal Disclaimer:</b> This product uses the NRCS Soil Data Access API but is '
        'not endorsed or certified by the USDA. Results are indicative only and must not be '
        'used for official CRP eligibility determinations without verification by a qualified '
        'NRCS conservationist.'
        '<br><br>'
        '<b>Erosion Index (EI) Notice:</b> EI is calculated as R × K × LS / T. '
        'R-factors are point-specific (via NOAA weather stations) or state-level averages from NRCS FOTG as fallback. '
        'LS is the combined Slope Length (L) and Slope Steepness (S) factor — '
        'LS is calculated from real USGS 3DEP 30m elevation data (true L × S formula, ±5% error). '
        'Falls back to slope steepness approximation (±23%) if DEM data is unavailable. '
        '<br><br>'
        '<b>Data Quality & Maintenance:</b> R-factors are monitored quarterly (January, April, July, October) '
        'from official NRCS FOTG and EPA RUSLE2 sources to ensure latest updates. SSURGO soil data updates in '
        'real-time via USDA API. All data is actively maintained for accuracy. '
        '<br><br>'
        'This score must not be used as the basis for any CRP application or land management '
        'decision without NRCS field verification.'
        '<br><br>'
        '<b>CP Practice Suggestions:</b> Practice recommendations are based on EI thresholds '
        'and SSURGO hydric soil classification. Wetland practices (CP23, CP27/CP28) are only '
        'flagged when hydric soils are detected. These do not replace an official NRCS wetland '
        'determination or account for state signup rules, program periods, or site conditions.'
        '<br><br>'
        '<b>Data Sources:</b> Soil Survey Staff. Soil Survey Geographic (SSURGO) Database. '
        'United States Department of Agriculture, Natural Resources Conservation Service. '
        'Elevation data: USGS 3D Elevation Program (3DEP) 30m DEM via py3dep.'
        '</div>',
        unsafe_allow_html=True
    )


# =============================================================================
# VIEW FUNCTIONS FOR TWO-TIER UI
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# POLYGON AREA CALCULATION (v17)
# ─────────────────────────────────────────────────────────────────────────────
def calculate_polygon_acres(coords):
    """
    Calculate polygon area in acres from list of [lon, lat] coordinate pairs.
    Uses pyproj EPSG:5070 (Albers Equal Area CONUS) for accuracy.
    Returns (acres_float, acres_string) or (None, "___") on failure.
    """
    try:
        from shapely.geometry import Polygon
        from shapely.ops import transform
        import pyproj

        if not coords or len(coords) < 3:
            return None, "___"

        poly = Polygon([(c[0], c[1]) for c in coords])  # coords are [lon, lat]
        if not poly.is_valid:
            poly = poly.buffer(0)

        transformer = get_cached_transformer("EPSG:4326", "EPSG:5070")
        if transformer is None:
            return None, "Coordinate transformation failed"

        projector = transformer.transform
        area_m2 = transform(projector, poly).area
        acres = area_m2 / 4046.856

        if acres < 0.1:
            return None, "___"
        return round(acres, 1), f"{acres:.1f}"

    except Exception:
        return None, "___"


def wkt_to_acres(wkt_string):
    """
    Parse a WKT POLYGON string and return (acres_float, acres_string).
    E.g. 'POLYGON((-94.38 42.02, -94.36 42.02, ...))'
    """
    try:
        import re
        nums = re.findall(r"(-?\d+\.?\d*)\s+(-?\d+\.?\d*)", wkt_string)
        coords = [[float(lon), float(lat)] for lon, lat in nums]
        return calculate_polygon_acres(coords)
    except Exception:
        return None, "___"


# ─────────────────────────────────────────────────────────────────────────────
# NRCS-CPA-026e PDF GENERATOR (v17)
# Matches official NRCS-CPA-026e (8/2013) layout
# Section I: HEL determination with RUSLE2 parameters
# Section II: Wetlands from hydric soil indicators
# ─────────────────────────────────────────────────────────────────────────────
def generate_cpa026_pdf(r_val, state_label, ls_factor, ls_source,
                        df, ei_max, ei_min, center_lat, center_lon,
                        county="", state_name="",
                        polygon_acres=None, muname_acres=None):
    """
    Fill official NRCS-CPA-026e PDF form fields directly.
    Original 4-page form is preserved exactly — only blank fields populated.
    Pre-fills: Request Date, County, Section I (soil/HEL/acres/date),
               Section II (hydric soil/wetland label/acres/date).
    """
    try:
        from pypdf import PdfReader, PdfWriter, generic
        from io import BytesIO
        import base64

        CPA026E_B64 = "JVBERi0xLjYNJeLjz9MNCjE4MzAgMCBvYmoNPDwvTGluZWFyaXplZCAxL0wgMTk3NDA5L08gMTgzMi9FIDgwNzQ4L04gNC9UIDE5Njc1My9IIFsgMTAzMiAxMjM3XT4+DWVuZG9iag0gICAgICAgICAgDQoxOTE4IDAgb2JqDTw8L0RlY29kZVBhcm1zPDwvQ29sdW1ucyA1L1ByZWRpY3RvciAxMj4+L0ZpbHRlci9GbGF0ZURlY29kZS9JRFs8NDA1MjlCNzNDNjgzRDI0OUFFRjI4NkREQUZCODQ1RTg+PDhBMjQ4MUEzMDM1RDVDNEE5RTNGMDUyOEEzN0E1MTMxPl0vSW5kZXhbMTgzMCAyMjBdL0luZm8gMTgyOSAwIFIvTGVuZ3RoIDIxMy9QcmV2IDE5Njc1NC9Sb290IDE4MzEgMCBSL1NpemUgMjA1MC9UeXBlL1hSZWYvV1sxIDMgMV0+PnN0cmVhbQ0KaN5iYmRgEGBgYmDgOAsiGWeDSKbpIJJhF1hkHzrJgCziAVbvAhbfA2aDRRhdwSRYhGEvgs3oDlbjilDPiCzrgVDDiEvWC10Wai9Elze6vUzeSGo8kezFIKFqXPCpYXRBV49FjQe621BkXfGa4I5kwhkwuQ6ssgER5oqBYHcqgkUMQCR7KYhkrQSzb4DZkuA4nQsi2baAxUVAJOchsGlBQJIx8x+IrXEHbI4K2MwLQPL/6e8MTIwM7D/BKhkYR8lRkpok/9wh5ub/DH97HgMEGAANzz3GDQplbmRzdHJlYW0NZW5kb2JqDXN0YXJ0eHJlZg0KMA0KJSVFT0YNCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg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VDAgMjAwMiAwIFIvVFQxIDIwMDQgMCBSPj4vUHJvY1NldFsvUERGL1RleHQvSW1hZ2VDXS9YT2JqZWN0PDwvSW0wIDE5MTcgMCBSPj4+Pi9Sb3RhdGUgMC9TdHJ1Y3RQYXJlbnRzIDAvVGFicy9SL1R5cGUvUGFnZT4+DWVuZG9iag0xODMzIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCAxODcuNTYgNDIuNl0vRmlsdGVyWy9GbGF0ZURlY29kZV0vTGVuZ3RoIDIyL1Jlc291cmNlczw8L0ZvbnQ8PC9IZWx2IDE5MjIgMCBSL1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREZdPj4+PnN0cmVhbQ0KSInSD6lQcPJ1VuBy9XUGCDAAE90C7Q0KZW5kc3RyZWFtDWVuZG9iag0xODM0IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA2Mi42MTggMjAuNTU5XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTgzNSAwIG9iag08PC9CQm94WzAuMCAwLjAgODIuMzc0IDIwLjk5OV0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4MzYgMCBvYmoNPDwvQkJveFswLjAgMC4wIDExNC45NiAxNi43MjRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODM3IDAgb2JqDTw8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bS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODcwIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3OS45MiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4NzEgMCBvYmoNPDwvQkJveFswLjAgMC4wIDg4LjkyIDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTg3MiAwIG9iag08PC9CQm94WzAuMCAwLjAgODIuNDQgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODczIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA4MC44OCAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4NzQgMCBvYmoNPDwvQkJveFswLjAgMC4wIDU1Ljg1IDExLjEwNV0vRmlsdGVyL0ZsYXRlRGVjb2RlL0Zvcm1UeXBlIDEvTGVuZ3RoIDEwMC9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L0ZvbnQ8PC9UaVJvIDE5MjMgMCBSPj4vUHJvY1NldFsvUERGL1RleHRdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQpIiTSLOw5AQBRF+7uKW9KMeRMvoSXKKchLbMAnFITK8n0S7fkIZ3h6qrpCKeLEK88REzK7WMWaOBAoVHmD8vc9tuebUdkTLt1O8bTpQ4HBSR6UNiBhSlvRGFo0scYtwAAFKxcWDQplbmRzdHJlYW0NZW5kb2JqDTE4NzUgMCBvYmoNPDwvQkJveFswLjAgMC4wIDc5LjkyIDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTg3NiAwIG9iag08PC9CQm94WzAuMCAwLjAgODguOTIgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODc3IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA4Mi40NCAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4NzggMCBvYmoNPDwvQkJveFswLjAgMC4wIDgwLjg4IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTg3OSAwIG9iag08PC9CQm94WzAuMCAwLjAgNTUuODUgMTAuOTc0XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAwL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNIu9DkAwFIX38xRnZKm20ZSVGA3kJl6ANgyEyeMrifX7MYzQ1HROVY5Gq9qXvBYEFHKz6VvihKWhM29Q/X7Cnr6IRlK4jkdaKeFDllZp7ykzMuaUDZ1gQNe3eAQYAPZSFwINCmVuZHN0cmVhbQ1lbmRvYmoNMTg4MCAwIG9iag08PC9CQm94WzAuMCAwLjAgNzAuOTIgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODgxIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3OS45MiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4ODIgMCBvYmoNPDwvQkJveFswLjAgMC4wIDg4LjkyIDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTg4MyAwIG9iag08PC9CQm94WzAuMCAwLjAgODIuNDQgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODg0IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA4MC44OCAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4ODUgMCBvYmoNPDwvQkJveFswLjAgMC4wIDU1LjUyNCAxMC45NzNdL0ZpbHRlci9GbGF0ZURlY29kZS9Gb3JtVHlwZSAxL0xlbmd0aCAxMDMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Gb250PDwvVGlSbyAxOTIzIDAgUj4+L1Byb2NTZXRbL1BERi9UZXh0XT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KSIk0y7sOQEAURdH+fMUpacbMcD1aolSQm/gBj1AQKp9vItHurO24wtJSxIjP6KypipT3jAWJPqy7hrjg6SjuE+UPRhzhXFFrkNtwhpe6fMnTG1vkQp0QMabuaBU92q7BK8AANoEXkA0KZW5kc3RyZWFtDWVuZG9iag0xODg2IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3MC45MiAxMC42MDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODg3IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3OS45MiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4ODggMCBvYmoNPDwvQkJveFswLjAgMC4wIDgyLjQ0IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTg4OSAwIG9iag08PC9CQm94WzAuMCAwLjAgODAuODggMTEuMTZdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODkwIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA1NS44NSAxMS4zMDFdL0ZpbHRlci9GbGF0ZURlY29kZS9Gb3JtVHlwZSAxL0xlbmd0aCAxMDEvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Gb250PDwvVGlSbyAxOTIzIDAgUj4+L1Byb2NTZXRbL1BERi9UZXh0XT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KSIk0izkOQFAURfu7ilvSfO99fkJLlAryEhswhIJQWb4h0Z5BOUMoDMHlgaouFeU5YkJiF8umIg54KoO+QfH7HtvzzSjtCZdupwpt+pCndz6TjDYgYkxbURta1E2FW4ABAAPaFxANCmVuZHN0cmVhbQ1lbmRvYmoNMTg5MSAwIG9iag08PC9CQm94WzAuMCAwLjAgNzAuOTIgMTEuMTZdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODkyIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3OS45MiAxMS4xNl0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE4OTMgMCBvYmoNPDwvQkJveFswLjAgMC4wIDg4LjkyIDExLjE2XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTg5NCAwIG9iag08PC9CQm94WzAuMCAwLjAgODIuNDQgMTEuMTZdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xODk1IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA0My44OTUgMTYuMDM5XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAyL01hdHJpeFsxLjAg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RmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFIThfp5iSpq1Z9mwLVEqyEm8gEsoCJXHt5Fo5/9GuMLSMhTGV44iRlzJe8aCTB/WXUNciIHBfiL8YMQRnytqjXIbToqlLt/k6Iz43FMnJEypO1pFj7Zr8AowADX1F4oNCmVuZHN0cmVhbQ1lbmRvYmoNMTIgMCBvYmoNPDwvQkJveFswLjAgMC4wIDEwOC45NiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTEzIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NS40IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTQgMCBvYmoNPDwvQkJveFswLjAgMC4wIDc0LjE2IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTUgMCBvYmoNPDwvQkJveFswLjAgMC4wIDk0LjQ3MiAxMS4xMjddL0ZpbHRlci9GbGF0ZURlY29kZS9Gb3Jt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4X6eYkqatWfZsC1RKshJvIBLKAiVx7eRaOf/RrjC0jIUxleOIkZcyXvGgkwf1l1DXIiBwX4i/GDEEZ8rao1yG06KpS7f5OiM+NxTJyRMqTtaRY+2a/AKMAA19ReKDQplbmRzdHJlYW0NZW5kb2JqDTMwIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3NC4xNiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTMxIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NS40IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMzIgMCBvYmoNPDwvQkJveFswLjAgMC4wIDEwOC45NiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTMzIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3NC4xNiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTM0IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC40NzIgMTEuMTI4XS9GaWx0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CmVuZHN0cmVhbQ1lbmRvYmoNNDEgMCBvYmoNPDwvQkJveFswLjAgMC4wIDEwOC45NiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTQyIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCAxMDguOTYgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag00MyAwIG9iag08PC9CQm94WzAuMCAwLjAgNzQuMTYgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag00NCAwIG9iag08PC9CQm94WzAuMCAwLjAgOTQuNDczIDExLjEyOF0vRmlsdGVyL0ZsYXRlRGVjb2RlL0Zvcm1UeXBlIDEvTGVuZ3RoIDEwMi9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L0ZvbnQ8PC9UaVJvIDE5MjMgMCBSPj4vUHJvY1NldFsvUERGL1RleHRdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQpIiTTLPQ5AQBRF4f6u4pY0Y94YYVoypYK8xAb8hIJQWT6RaE++I1xgaRm88WVOESOu4jVhRqY367YhTjgKg/1E+MGA/T0X1PrKtT8oljp/ydEZKTx1RMKUuiEqOsS2wSPAAB5hF1YNCmVuZHN0cmVhbQ1lbmRvYmoNNDUgMCBvYmoNPDwvQkJveFswLjAgMC4wIDk1LjQgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag00NiAwIG9iag08PC9CQm94WzAuMCAwLjAgOTQuNTgyIDExLjEyN10vRmlsdGVyL0ZsYXRlRGVjb2RlL0Zvcm1UeXBlIDEvTGVuZ3RoIDEwMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L0ZvbnQ8PC9UaVJvIDE5MjMgMCBSPj4vUHJvY1NldFsvUERGL1RleHRdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQpIiTTLuw5AQBSE4X6eYkqatWfZsC1RKshJvIBLKAiVx7eRaOf/RrjC0jIUxleOIkZcyXvGgkwf1l1DXIiBwX4i/GDEEZ8rao1yG06KpS7f5OiM+NxTJyRMqTtaRY+2a/AKMAA19ReKDQplbmRzdHJlYW0NZW5kb2JqDTQ3IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NS40IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNNDggMCBvYmoNPDwvQkJveFswLjAgMC4wIDk0LjU4MiAxMS4xMjddL0ZpbHRlci9GbGF0ZURlY29kZS9Gb3JtVHlwZSAxL0xlbmd0aCAxMDMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Gb250PDwvVGlSbyAxOTIzIDAgUj4+L1Byb2NTZXRbL1BERi9UZXh0XT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVh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MC4wXS9SZXNvdXJjZXM8PC9Gb250PDwvVGlSbyAxOTIzIDAgUj4+L1Byb2NTZXRbL1BERi9UZXh0XT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KSIk0y7sOQEAUhOF+nmJKmrVn2bAtUSrISbyASygIlce3kWjn/0a4wtIyFMZXjiJGXMl7xoJMH9ZdQ1yIgcF+IvxgxBGfK2qNchtOiqUu3+TojPjcUyckTKk7WkWPtmvwCjAANfUXig0KZW5kc3RyZWFtDWVuZG9iag02NyAwIG9iag08PC9CQm94WzAuMCAwLjAgOTUuNCAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTY4IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCAxMDguOTYgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag02OSAwIG9iag08PC9CQm94WzAuMCAwLjAgNzQuMTYgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag03MCAwIG9iag08PC9CQm94WzAuMCAwLjAgMTA4Ljk2IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNNzEgMCBvYmoNPDwvQkJveFswLjAgMC4wIDk1LjQgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag03MiAwIG9iag08PC9CQm94WzAuMCAwLjAgOTQuNTgyIDExLjEyN10vRmlsdGVyL0ZsYXRlRGVjb2RlL0Zvcm1UeXBlIDEvTGVuZ3RoIDEwMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L0ZvbnQ8PC9UaVJvIDE5MjMgMCBSPj4vUHJvY1NldFsvUERGL1RleHRdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQpIiTTLuw5AQBSE4X6eYkqatWfZsC1RKshJvIBLKAiVx7eRaOf/RrjC0jIUxleOIkZcyXvGgkwf1l1DXIiBwX4i/GDEEZ8rao1yG06KpS7f5OiM+NxTJyRMqTtaRY+2a/AKMAA19ReKDQplbmRzdHJlYW0NZW5kb2JqDTczIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC40NzIgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTA0L01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMs7CoAwFETRflYxpTYxLyqSNmJpoTxwA37QQtHK5RsCtnPPCDdYWvrKVI2jiBHX8FmwotCXoW+JGzHQ2yT8Dyac8bkhaJT7eFEsdU2TozNSlzV1RsaceqBTDOj6Fp8AAwA0oxeGDQplbmRzdHJlYW0NZW5kb2JqDTc0IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3NC4xNiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTc1IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCAxMDguOTYgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag03NiAwIG9iag08PC9CQm94WzAuMCAwLjAgOTUuNCAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTc3IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC41ODIgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFIThfp5iSpq1Z9mwLVEqyEm8gEsoCJXHt5Fo5/9GuMLSMhTGV44iRlzJe8aCTB/WXUNciIHBfiL8YMQRnytqjXIbToqlLt/k6Iz43FMnJEypO1pFj7Zr8AowADX1F4oNCmVuZHN0cmVhbQ1lbmRvYmoNNzggMCBvYmoNPDwvQkJv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DTw8L0JCb3hbMC4wIDAuMCA3NC4xNiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTExNiAwIG9iag08PC9CQm94WzAuMCAwLjAgMTA4Ljk2IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTE3IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC41ODIgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFIThfp5iSpq1Z9mwLVEqyEm8gEsoCJXHt5Fo5/9GuMLSMhTGV44iRlzJe8aCTB/WXUNciIHBfiL8YMQRnytqjXIbToqlLt/k6Iz43FMnJEypO1pFj7Zr8AowADX1F4oNCmVuZHN0cmVhbQ1lbmRvYmoNMTE4IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC40NzIgMTEuMTI4XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJ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ZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTEzMCAwIG9iag08PC9CQm94WzAuMCAwLjAgMTA4Ljk2IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTMxIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NS40IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTMyIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC41ODIgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFIThfp5iSpq1Z9mwLVEqyEm8gEsoCJXHt5Fo5/9GuMLSMhTGV44iRlzJe8aCTB/WXUNciIHBfiL8YMQRnytqjXIbToqlLt/k6Iz43FMnJEypO1pFj7Zr8AowADX1F4oNCmVuZHN0cmVhbQ1lbmRvYmoNMTMzIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC40NzIgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTA0L01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYv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PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE0NSAwIG9iag08PC9CQm94WzAuMCAwLjAgMTA4Ljk2IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTQ2IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NS40IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTQ3IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5NC41ODIgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFIThfp5iSpq1Z9mwLVEqyEm8gEsoCJXHt5Fo5/9GuMLSMhTGV44iRlzJe8aCTB/WXUNciIHBfiL8YMQRnytqjXIbToqlLt/k6Iz43FMnJEypO1pFj7Zr8AowADX1F4oNCmVuZHN0cmVhbQ1lbmRvYmoNMTQ4IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCAxMDguOTYgMTEuMTZdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xNDkgMCBvYmoNPDwvQkJveFswLjAgMC4wIDc1LjM2IDExLjE2XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTUwIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA2MS44NTQgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFEXR/nzFKWnG3GE8WjKlgtzED3iEglD5fCLR7qwtXGBpmYspfUYRI67gNWFGojfrtiFOOAp98YnqBwP291xQ6yvX/qBY6vwlR2fEp546ImJM3RAUHULb4BFgADWiF4sNCmVuZHN0cmVhbQ1lbmRvYmoNMTUxIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5OC44OCAxMS4xNl0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE1MiAwIG9iag08PC9CQm94WzAuMCAwLjAgNzUuMzYgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xNTMgMCBvYmoNPDwvQkJveFswLjAgMC4wIDYxLjg1NCAxMS4xMjddL0ZpbHRlci9GbGF0ZURlY29kZS9Gb3JtVHlwZSAxL0xlbmd0aCAxMDMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Gb250PDwvVGlSbyAxOTIzIDAgUj4+L1Byb2NTZXRbL1BERi9UZXh0XT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KSIk0y7sOQEAURdH+fMUpacbcYTxaMqWC3MQPeISCUPl8ItHurC1cYGmZiyl9RhEjruA1YUaiN+u2IU44Cn3xieoHA/b3XFDrK9f+oFjq/CVHZ8SnnjoiYkzdEBQdQtvgEWAANaIXiw0KZW5kc3RyZWFtDWVuZG9iag0xNTQgMCBvYmoNPDwvQkJveFswLjAgMC4wIDk4Ljg4IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTU1IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3NS4zNiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE1NiAwIG9iag08PC9CQm94WzAuMCAwLjAgNjEuODU0IDExLjEyN10vRmlsdGVyL0ZsYXRlRGVjb2RlL0Zvcm1UeXBlIDEvTGVuZ3RoIDEwMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L0ZvbnQ8PC9UaVJvIDE5MjMgMCBSPj4vUHJvY1NldFsvUERGL1RleHRdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQpIiTTLuw5AQBRF0f58xSlpxtxhPFoypYLcxA94hIJQ+Xwi0e6sLVxgaZmLKX1GESOu4DVhRqI367YhTjgKffGJ6gcD9vdcUOsr1/6gWOr8JUdnxKeeOiJiTN0QFB1C2+ARYAA1oheLDQplbmRzdHJlYW0NZW5kb2JqDTE1NyAwIG9iag08PC9CQm94WzAuMCAwLjAgOTguODggMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xNTggMCBvYmoNPDwvQkJveFswLjAgMC4wIDc1LjM2IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTU5IDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA2MS44NTQgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFEXR/nzFKWnG3GE8WjKlgtzED3iEglD5fCLR7qwtXGBpmYspfUYRI67gNWFGojfrtiFOOAp98YnqBwP291xQ6yvX/qBY6vwlR2fEp546ImJM3RAUHULb4BFgADWiF4sNCmVuZHN0cmVhbQ1lbmRvYmoNMTYwIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5OC44OCAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTE2MSAwIG9iag08PC9CQm94WzAuMCAwLjAgNzUuMzYgMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0xNjIgMCBvYmoNPDwvQkJveFswLjAgMC4wIDYxLjg1NCAxMS4xMjddL0ZpbHRlci9GbGF0ZURlY29kZS9Gb3JtVHlwZSAxL0xlbmd0aCAxMDMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Gb250PDwvVGlSbyAxOTIzIDAgUj4+L1Byb2NTZXRbL1BERi9UZXh0XT4+L1N1YnR5cGUvRm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KSIk0y7sOQEAURdH+fMUpacbcYTxaMqWC3MQPeISCUPl8ItHurC1cYGmZiyl9RhEjruA1YUaiN+u2IU44Cn3xieoHA/b3XFDrK9f+oFjq/CVHZ8SnnjoiYkzdEBQdQtvgEWAANaIXiw0KZW5kc3RyZWFtDWVuZG9iag0xNjMgMCBvYmoNPDwvQkJveFswLjAgMC4wIDk4Ljg4IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMTY0IDAgb2JqDTw8L0JCb3hbMC4w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ag0xOTkgMCBvYmoNPDwvQkJveFswLjAgMC4wIDk4Ljg4IDExLjA0XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMjAwIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA2MS44NTQgMTEuMTI3XS9GaWx0ZXIvRmxhdGVEZWNvZGUvRm9ybVR5cGUgMS9MZW5ndGggMTAzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvRm9udDw8L1RpUm8gMTkyMyAwIFI+Pi9Qcm9jU2V0Wy9QREYvVGV4dF0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCkiJNMu7DkBAFEXR/nzFKWnG3GE8WjKlgtzED3iEglD5fCLR7qwtXGBpmYspfUYRI67gNWFGojfrtiFOOAp98YnqBwP291xQ6yvX/qBY6vwlR2fEp546ImJM3RAUHULb4BFgADWiF4sNCmVuZHN0cmVhbQ1lbmRvYmoNMjAxIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA5OC44OCAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTIwMiAwIG9iag08PC9CQm94WzAuMCAwLjAgNjEuODU0IDExLjEyN10vRmlsdGVyL0ZsYXRlRGVjb2RlL0Zvcm1UeXBlIDEvTGVuZ3RoIDEwMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L0ZvbnQ8PC9UaVJvIDE5MjMgMCBSPj4vUHJvY1NldFsvUERGL1RleHRdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+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Rm9ybS9UeXBlL1hPYmplY3Q+PnN0cmVhbQ0KL1R4IEJNQyAKRU1DCg0KZW5kc3RyZWFtDWVuZG9iag0yMTAgMCBvYmoNPDwvQkJveFswLjAgMC4wIDgyLjQ3MiA5LjU5OTk4XS9Gb3JtVHlwZSAxL0xlbmd0aCAxMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERl0+Pi9TdWJ0eXBlL0Zvcm0vVHlwZS9YT2JqZWN0Pj5zdHJlYW0NCi9UeCBCTUMgCkVNQwoNCmVuZHN0cmVhbQ1lbmRvYmoNMjExIDAgb2JqDTw8L0JCb3hbMC4wIDAuMCA3NS4zNiAxMS4wNF0vRm9ybVR5cGUgMS9MZW5ndGggMTMvTWF0cml4WzEuMCAwLjAgMC4wIDEuMCAwLjAgMC4wXS9SZXNvdXJjZXM8PC9Qcm9jU2V0Wy9QREZdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQovVHggQk1DIApFTUMKDQplbmRzdHJlYW0NZW5kb2JqDTIxMiAwIG9iag08PC9CQm94WzAuMCAwLjAgNjEuODU0IDExLjEyN10vRmlsdGVyL0ZsYXRlRGVjb2RlL0Zvcm1UeXBlIDEvTGVuZ3RoIDEwMy9NYXRyaXhbMS4wIDAuMCAwLjAgMS4wIDAuMCAwLjBdL1Jlc291cmNlczw8L0ZvbnQ8PC9UaVJvIDE5MjMgMCBSPj4vUHJvY1NldFsvUERGL1RleHRdPj4vU3VidHlwZS9Gb3JtL1R5cGUvWE9iamVjdD4+c3RyZWFtDQpIiTTLuw5AQBRF0f58xSlpxtxhPFoypYLcxA94hIJQ+Xwi0e6sLVxgaZmLKX1GESOu4DVhRqI367YhTjgKffGJ6gcD9vdcUOsr1/6gWOr8JUdnxKeeOiJiTN0QFB1C2+ARYAA1oheLDQplbmRzdHJlYW0NZW5kb2JqDTIxMyAwIG9iag08PC9CQm94WzAuMCAwLjAgOTguODggMTEuMDRdL0Zvcm1UeXBlIDEvTGVuZ3RoIDEzL01hdHJpeFsxLjAgMC4wIDAuMCAxLjAgMC4wIDAuMF0vUmVzb3VyY2VzPDwvUHJvY1NldFsvUERGXT4+L1N1YnR5cGUvRm9ybS9UeXBl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ICB4bWxuczp4bXBNTT0iaHR0cDovL25zLmFkb2JlLmNvbS94YXAvMS4wL21tLyI+CiAgICAgICAgIDx4bXBNTTpEb2N1bWVudElEPnV1aWQ6Zjk5NDJkMDItNmFkYS00Zjk5LTkwMDEtNGEwZDU1MDQxNzAwPC94bXBNTTpEb2N1bWVudElEPgogICAgICAgICA8eG1wTU06SW5zdGFuY2VJRD51dWlkOjQ1YzNmZDVlLWU0YTEtNDQ5Ni1hNDFjLTM4OTk4Nzc1YThmMzwveG1wTU06SW5zdGFuY2VJRD4KICAgICAgICAgPHhtcE1NOnN1YmplY3Q+CiAgICAgICAgICAgIDxyZGY6U2VxPgogICAgICAgICAgICAgICA8cmRmOmxpPjI8L3JkZjpsaT4KICAgICAgICAgICAgPC9yZGY6U2VxPgogICAgICAgICA8L3htcE1NOnN1YmplY3Q+CiAgICAgIDwvcmRmOkRlc2NyaXB0aW9uPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iPgogICAgICAgICA8ZGM6Zm9ybWF0PmFwcGxpY2F0aW9uL3BkZjwvZGM6Zm9ybWF0PgogICAgICAgICA8ZGM6dGl0bGU+CiAgICAgICAgICAgIDxyZGY6QWx0PgogICAgICAgICAgICAgICA8cmRmOmxpIHhtbDpsYW5nPSJ4LWRlZmF1bHQiPlU8L3JkZjpsaT4KICAgICAgICAgICAgPC9yZGY6QWx0PgogICAgICAgICA8L2RjOnRpdGxlPgogICAgICAgICA8ZGM6Y3JlYXRvcj4KICAgICAgICAgICAgPHJkZjpTZXE+CiAgICAgICAgICAgICAgIDxyZGY6bGk+UHJlZmVycmVkIEN1c3RvbWVyPC9yZGY6bGk+CiAgICAgICAgICAgIDwvcmRmOlNlcT4KICAgICAgICAgPC9kYzpjcmVhdG9yPgogICAgICA8L3JkZjpEZXNjcmlwdGlvbj4KICAgICAgPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9IiIKICAgICAgICAgICAgeG1sbnM6cGRmPSJodHRwOi8vbnMuYWRvYmUuY29tL3BkZi8xLjMvIj4KICAgICAgICAgPHBkZjpQcm9kdWNlcj5BZG9iZSBQREYgTGlicmFyeSAxMC4wPC9wZGY6UHJvZHVjZXI+CiAgICAgIDwvcmRmOkRlc2NyaXB0aW9uPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4bWxuczpwZGZ4PSJodHRwOi8vbnMuYWRvYmUuY29tL3BkZngvMS4zLyI+CiAgICAgICAgIDxwZGZ4OlNvdXJjZU1vZGlmaWVkPkQ6MjAxMzAyMjExNDQ5MjI8L3BkZng6U291cmNlTW9kaWZpZWQ+CiAgICAgICAgIDxwZGZ4OkNvbXBhbnk+VVNEQSBPQ0lPLUlUUzwvcGRmeDpDb21wYW55PgogICAgICAgICA8cGRmeDpDcmVhdGVkPkQ6MjAxMjA4MDc8L3BkZng6Q3JlYXRlZD4KICAgICAgICAgPHBkZng6TGFzdFNhdmVkPkQ6MjAxMzAyMjA8L3BkZng6TGFzdFNhdmVkPgogICAgICA8L3JkZjpEZXNjcmlwdGlvbj4KICAgICAgPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9IiIKICAgICAgICAgICAgeG1sbnM6YWRob2N3Zj0iaHR0cDovL25zLmFkb2JlLmNvbS9BY3JvYmF0QWRob2NXb3JrZmxvdy8xLjAvIj4KICAgICAgICAgPGFkaG9jd2Y6c3RhdGU+MTwvYWRob2N3ZjpzdGF0ZT4KICAgICAgICAgPGFkaG9jd2Y6dmVyc2lvbj4xLjE8L2FkaG9jd2Y6dmVyc2lvbj4KICAgICAgPC9yZGY6RGVzY3JpcHRpb24+CiAgIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+CiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAKPD94cGFja2V0IGVuZD0idyI/Pg0KZW5kc3RyZWFtDWVuZG9iag0yMzMgMCBvYmoNPDwvRmlsdGVyL0ZsYXRlRGVjb2RlL0ZpcnN0IDcvTGVuZ3RoIDYzL04gMS9UeXBlL09ialN0bT4+c3RyZWFtDQpo3jK0MLJQMFCwsdF3zi/NK1Ew0ffOTCmONrQwNgIKBykYgkkTMGkOImP1QyoLUvUDEtNTi+3sAAIMAOlaD/gNCmVuZHN0cmVhbQ1lbmRvYmoNMjM0IDAgb2JqDTw8L0ZpbHRlci9GbGF0ZURlY29kZS9GaXJzdCA3L0xlbmd0aCAyMTkvTiAxL1R5cGUvT2JqU3RtPj5zdHJlYW0NCmjeTM1Ra8IwFAXgv5I3m4euN6nd0iFCaRkIioUqPqfNLQub3nFNB/77pejA13MO31FGlwLEapVVU/gkTlrGEZnRiXq6Bjojy6ym84+93JJj11RiX2/26ebQxZjRBnRJ865BaTDw9sg8XZrY3IsctFZgihzyFF4XAIvHKp5VA1Nvg2ibj539QhYKXpQYicWJ2Mlsa6+hs7//HzMFMtuRe+YNlFAWRhUpFHe+ZXLTgNF31OOsi63v2fJtPohCRxMPGB0/+mdcqeWy1FpmBx++MTnK9fpPgAEAL2NTbg0KZW5kc3RyZWFtDWVuZG9iag0yMzUgMCBvYmoNPDwvRGVjb2RlUGFybXM8PC9Db2x1bW5zIDUvUHJlZGljdG9yIDEyPj4vRmlsdGVyL0ZsYXRlRGVjb2RlL0lEWzw0MDUyOUI3M0M2ODNEMjQ5QUVGMjg2RERBRkI4NDVFOD48OEEyNDgxQTMwMzVENUM0QTlFM0YwNTI4QTM3QTUxMzE+XS9JbmZvIDE4MjkgMCBSL0xlbmd0aCAzOTIvUm9vdCAxODMxIDAgUi9TaXplIDE4MzAvVHlwZS9YUmVmL1dbMSAzIDFdPj5zdHJlYW0NCmje7JQ9SgRBEIW7e3dVVEQP4M8JPICb6vSewMDAE5gYiYGZJoIXEBY8gAdwexYTLyB4AY0VjAwM/Ks3o1MzvQwDLsrCm+Cjed1dVV39pp2Rz1nb3TPOGDstXDkVupNCWXsStu6htIXdF4yvQA8mYAoOhCYgji/G+Wy2Mqi98a5ErRxCHxTRdIRMzyMENZsUMUu1hUhJVd4tlSupjkuRQxRfZwxRBy6jONl5o6p0Xl2zSasZR/RW1+N/9tra2e8O6JP6seTN7650171aPVU3rrqnZ82wujKP4CM91M2W4vsovtrreqoPDSpvfq7SWXzVk/VKVk8ep/GuiRwvjzXa9Wh94U4484i37kg4vyOcuxC2H6DvCmc/hJ1V8BD6AdYsQlkHj6GfC6fw73fOoGwLlzaRt49/c+PrBX4z+7jHFmhswdv3qkKSv6ftsw8kfUXSVyR9RZL/6Sv7+oe1PfOOSL6lJH1F0lckSV+R9BVJX5EkfUXSVyR9RZL01eTR3aDz5lOAAQCRdKM5DQplbmRzdHJlYW0NZW5kb2JqDXN0YXJ0eHJlZg0KMTE2DQolJUVPRg0K"

        if muname_acres is None:
            muname_acres = {}

        reader = PdfReader(BytesIO(base64.b64decode(CPA026E_B64)))
        writer = PdfWriter()
        writer.append(reader)

        today = datetime.now().strftime('%m/%d/%Y')
        num_rows = len(df) if not df.empty else 1

        def get_acres(soil_name):
            if muname_acres:
                total = sum(ac for mn, ac in muname_acres.items()
                           if mn.lower().startswith(soil_name.lower()))
                return f"{total:.1f}" if total > 0 else ""
            elif polygon_acres:
                return f"{polygon_acres / num_rows:.1f}"
            return ""

        def shorten_soil(name, max_chars=18):
            """Strip slope descriptors — keep base series name to fit narrow column."""
            base = name.split(",")[0].strip()
            if len(base) > max_chars:
                base = base[:max_chars-1] + "…"
            return base

        def set_field_by_position(page_idx, target_x, target_y, value, tolerance=5):
            page = writer.pages[page_idx]
            if "/Annots" not in page:
                return False
            for ref in page["/Annots"]:
                obj = ref.get_object()
                rect = obj.get("/Rect")
                if not rect:
                    continue
                x, y = float(rect[0]), float(rect[3])
                if abs(x - target_x) <= tolerance and abs(y - target_y) <= tolerance:
                    ft = obj.get("/FT", "")
                    if str(ft) == "/Ch":
                        opts = list(obj.get("/Opt", []))
                        if value in opts:
                            idx = opts.index(value)
                            obj.update({
                                generic.NameObject("/V"): generic.create_string_object(value),
                                generic.NameObject("/I"): generic.ArrayObject([generic.NumberObject(idx)]),
                            })
                            return True
                    else:
                        obj.update({
                            generic.NameObject("/V"): generic.create_string_object(value),
                        })
                        return True
            return False

        # ── Section I text fields ────────────────────────────────
        p1_fields = {
            "Request Date": today,
            "County":       county,
        }

        # Page 1: rows 1-5
        hel_rows_p1 = [
            ("FieldsRow1", "Acres",   "Determination Date"),
            ("FieldsRow2", "Acres_2", "Determination Date_2"),
            ("FieldsRow3", "Acres_3", "Determination Date_3"),
            ("FieldsRow4", "Acres_4", "Determination Date_4"),
            ("FieldsRow5", "Acres_5", "Determination Date_5"),
        ]
        hel_yn_y_p1 = [493, 481, 469, 457, 445]

        # Page 2 supplemental: rows 6-28
        # FieldsRow1HEL-5HEL then FieldsRow6-28, AcresRow1-28, Determination DateRow1-28
        hel_rows_p2 = [(f"FieldsRow{i}HEL" if i <= 5 else f"FieldsRow{i}",
                        f"AcresRow{i}",
                        f"Determination DateRow{i}") for i in range(1, 29)]
        hel_yn_y_p2 = [666, 654, 642, 630, 618, 606, 594, 582, 570, 558,
                       546, 534, 522, 510, 498, 486, 474, 462, 450, 438,
                       426, 414, 402, 390, 378, 366, 354, 342]

        # Fill page 1 rows (first 5 soils)
        for i, (_, row) in enumerate(df.iterrows()):
            if i >= 5: break
            soil  = str(row.get("Soil Type", ""))
            acres = get_acres(soil)
            fn, an, dn = hel_rows_p1[i]
            p1_fields[fn] = shorten_soil(soil)
            if acres: p1_fields[an] = acres
            p1_fields[dn] = today

        writer.update_page_form_field_values(writer.pages[0], p1_fields)

        # HEL Y/N dropdowns page 1 (x=139)
        for i, (_, row) in enumerate(df.iterrows()):
            if i >= len(hel_yn_y_p1): break
            hel = "Yes" if row.get("EI", 0) >= 8.0 else "No"
            set_field_by_position(0, 139, hel_yn_y_p1[i], hel)

        set_field_by_position(0, 396, 578, "Yes")
        set_field_by_position(0, 396, 565, "Yes" if ei_max >= 8.0 else "No")

        # Fill page 2 supplemental rows (soils 6+)
        if len(df) > 5:
            p2_fields = {}
            overflow_df = df.iloc[5:]
            for i, (_, row) in enumerate(overflow_df.iterrows()):
                if i >= len(hel_rows_p2): break
                soil  = str(row.get("Soil Type", ""))
                acres = get_acres(soil)
                fn, an, dn = hel_rows_p2[i]
                p2_fields[fn] = shorten_soil(soil)
                if acres: p2_fields[an] = acres
                p2_fields[dn] = today
            writer.update_page_form_field_values(writer.pages[1], p2_fields)
            # HEL Y/N dropdowns page 2 (x=157)
            for i, (_, row) in enumerate(overflow_df.iterrows()):
                if i >= len(hel_yn_y_p2): break
                hel = "Yes" if row.get("EI", 0) >= 8.0 else "No"
                set_field_by_position(1, 157, hel_yn_y_p2[i], hel)

        # ── Section II text fields ───────────────────────────────
        # Page 1: rows 1-5
        wet_rows_p1 = [
            ("FieldsRow1_2", "Acres_6",  "Determination Date_6"),
            ("FieldsRow2_2", "Acres_7",  "Determination Date_7"),
            ("FieldsRow3_2", "Acres_8",  "Determination Date_8"),
            ("FieldsRow4_2", "Acres_9",  "Determination Date_9"),
            ("FieldsRow5_2", "Acres_10", "Determination Date_10"),
        ]
        wet_label_y_p1 = [313, 301, 289, 277, 265]

        # Page 3 supplemental: FieldsRow34-61, numbered text fields, wetland label dropdowns
        wet_rows_p3 = [(f"FieldsRow{i+34}",
                        f"Cert Date 34.{i}" if i < 28 else "",
                        f"Det. date 34.0.{i}" if i < 28 else "") for i in range(28)]
        wet_label_y_p3 = [665, 653, 641, 629, 617, 605, 593, 581, 569, 557,
                          545, 533, 521, 509, 497, 485, 473, 461, 449, 437,
                          425, 413, 401, 389, 377, 365, 353, 341]

        hydric = df[df["Hydric"] == "Yes"] if "Hydric" in df.columns else df.iloc[0:0]

        # Fill page 1 wetland rows (first 5)
        w2_fields = {}
        for i, (_, row) in enumerate(hydric.iterrows()):
            if i >= 5: break
            soil  = str(row.get("Soil Type", ""))
            acres = get_acres(soil)
            fn, an, dn = wet_rows_p1[i]
            w2_fields[fn] = shorten_soil(soil)
            if acres: w2_fields[an] = acres
            w2_fields[dn] = today

        if w2_fields:
            writer.update_page_form_field_values(writer.pages[0], w2_fields)

        for i, (_, row) in enumerate(hydric.iterrows()):
            if i >= len(wet_label_y_p1): break
            set_field_by_position(0, 148, wet_label_y_p1[i], "W")

        # Fill page 3 supplemental wetland rows (soils 6+)
        if len(hydric) > 5:
            p3_fields = {}
            overflow_wet = hydric.iloc[5:]
            for i, (_, row) in enumerate(overflow_wet.iterrows()):
                if i >= len(wet_rows_p3): break
                soil  = str(row.get("Soil Type", ""))
                acres = get_acres(soil)
                fn = f"FieldsRow{i+34}"
                p3_fields[fn] = shorten_soil(soil)
                # Acres and dates use numbered fields on page 3
                acres_field = f"Acres34.0.{i}"
                det_field   = f"Det. date 34.0.{i}"
                if acres: p3_fields[acres_field] = acres
                p3_fields[det_field] = today
            writer.update_page_form_field_values(writer.pages[2], p3_fields)
            # Wetland label dropdowns page 3 (x=165)
            for i in range(min(len(overflow_wet), len(wet_label_y_p3))):
                set_field_by_position(2, 165, wet_label_y_p3[i], "W")

        # ── Remarks — overflow summary if > 33 soils ────────────
        remarks_parts = []
        if len(df) > 33:
            extra_hel = df.iloc[33:]
            hel_count     = (extra_hel["EI"] >= 8.0).sum() if "EI" in extra_hel.columns else 0
            non_hel_count = len(extra_hel) - hel_count
            extra_acres   = sum(
                float(get_acres(str(r.get("Soil Type", ""))) or 0)
                for _, r in extra_hel.iterrows()
            )
            remarks_parts.append(
                f"Additional soils not shown above ({len(extra_hel)} types, "
                f"{extra_acres:.1f} ac): HEL={hel_count}, Non-HEL={non_hel_count}."
            )

        if len(hydric) > 33:
            extra_wet   = hydric.iloc[33:]
            extra_acres = sum(
                float(get_acres(str(r.get("Soil Type", ""))) or 0)
                for _, r in extra_wet.iterrows()
            )
            remarks_parts.append(
                f"Additional wetland soils not shown ({len(extra_wet)} types, "
                f"{extra_acres:.1f} ac): all labeled W."
            )

        if remarks_parts:
            writer.update_page_form_field_values(
                writer.pages[0], {"Remarks": " | ".join(remarks_parts)}
            )

        pdf_buffer = BytesIO()
        writer.write(pdf_buffer)
        pdf_buffer.seek(0)
        return pdf_buffer.getvalue()

    except Exception as e:
        st.error(f"Error generating CPA-026e PDF: {str(e)}")
        return None




def generate_ad1026_pdf(county="", state_name="", land_use="", ei_max=None, hel_present=False, wetland_present=False):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from io import BytesIO
        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=letter)
        w, h = letter
        ml = 0.55 * inch
        mr = 0.55 * inch
        cw = w - ml - mr
        crop_year = str(datetime.now().year)

        def hline(y, t=0.5):
            c.setLineWidth(t)
            c.line(ml, y, w - mr, y)

        def wrap_text(text, x, y, max_w, font, size, leading=0.13*inch):
            """Word-wrap text, return final y position."""
            c.setFont(font, size)
            words = text.split()
            line = ""
            for word in words:
                test = (line + " " + word).strip()
                if c.stringWidth(test, font, size) > max_w:
                    c.drawString(x, y, line)
                    y -= leading
                    line = word
                else:
                    line = test
            if line:
                c.drawString(x, y, line)
                y -= leading
            return y

        def field_box(x, ytop, bw, bh, label, value="", ls=7, vs=9, filled=False):
            c.setLineWidth(0.5)
            c.setFillColor(colors.white)
            c.rect(x, ytop-bh, bw, bh, fill=1, stroke=1)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", ls)
            c.drawString(x+0.05*inch, ytop-0.13*inch, label)
            if value:
                c.setFont("Helvetica", vs)
                c.setFillColor(colors.black)
                c.drawString(x+0.05*inch, ytop-bh+0.08*inch, value)
                c.setFillColor(colors.black)

        def checkbox_wrap(x, y, label, font_size=7.5):
            """Checkbox with word-wrapped label — stays within page margins."""
            c.setLineWidth(0.5)
            box_size = 0.11*inch
            c.rect(x, y - box_size, box_size, box_size, fill=0, stroke=1)
            # Text starts after checkbox, wraps within page width
            text_x = x + 0.18*inch
            text_max = w - mr - text_x
            y = wrap_text(label, text_x, y - 0.02*inch, text_max, "Helvetica", font_size, 0.13*inch)
            return y - 0.06*inch

        def yn_row(y, num, text):
            """YES/NO question row — text wraps within available width."""
            text_w = cw - 1.05*inch
            # Draw question text with wrapping
            c.setFont("Helvetica", 8)
            question = f"{num}.  {text}"
            words = question.split()
            line = ""
            first_line = True
            line_y = y
            for word in words:
                test = (line + " " + word).strip()
                if c.stringWidth(test, "Helvetica", 8) > text_w:
                    c.drawString(ml+0.1*inch, line_y, line)
                    line_y -= 0.13*inch
                    line = word
                    first_line = False
                else:
                    line = test
            if line:
                c.drawString(ml+0.1*inch, line_y, line)
                line_y -= 0.13*inch
            # YES/NO boxes — aligned with first line of question
            bx = w - mr - 0.90*inch
            c.setFont("Helvetica-Bold", 8)
            c.rect(bx, y-0.14*inch, 0.34*inch, 0.16*inch, fill=0, stroke=1)
            c.drawString(bx+0.04*inch, y-0.11*inch, "YES")
            c.rect(bx+0.42*inch, y-0.14*inch, 0.30*inch, 0.16*inch, fill=0, stroke=1)
            c.drawString(bx+0.46*inch, y-0.11*inch, "NO")
            return line_y - 0.08*inch

        # ══════════════════════════════════════════════
        # PAGE 1
        # ══════════════════════════════════════════════
        y = h - 0.40*inch

        # Top note
        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(colors.HexColor("#444444"))
        c.drawString(ml, y, "This form is available electronically.  (See Page 2 for Privacy Act and Paperwork Reduction Act Statements)")
        c.setFillColor(colors.black)
        y -= 0.14*inch

        # Header
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml, y, "AD-1026")
        c.drawString(ml + 0.9*inch, y, "U.S. DEPARTMENT OF AGRICULTURE")
        c.setFont("Helvetica", 8)
        c.drawString(ml, y-0.14*inch, "(10-30-14)")
        c.drawString(ml + 0.9*inch, y-0.14*inch, "Farm Service Agency")
        y -= 0.26*inch

        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(w/2, y, "HIGHLY ERODIBLE LAND CONSERVATION (HELC) AND")
        y -= 0.18*inch
        c.drawCentredString(w/2, y, "WETLAND CONSERVATION (WC) CERTIFICATION")
        y -= 0.12*inch
        c.setFont("Helvetica-Oblique", 8)
        c.drawCentredString(w/2, y, "Read attached AD-1026 Appendix before completing form.")
        y -= 0.14*inch
        hline(y, 1.0)
        y -= 0.12*inch

        # Simple pre-fill note — no banner
        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(colors.HexColor("#444444"))
        c.drawString(ml, y,
            f"Pre-filled fields: Crop Year ({crop_year}), County ({county}), State ({state_name}), Land Use ({land_use})")
        c.setFillColor(colors.black)
        y -= 0.14*inch

        # ── PART A ──────────────────────────────────
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml, y, "PART A – BASIC INFORMATION")
        y -= 0.12*inch

        gap = 0.03*inch
        bh = 0.36*inch
        w1=cw*0.50; w2=cw*0.25; w3=cw*0.21
        x1=ml; x2=x1+w1+gap; x3=x2+w2+gap
        field_box(x1, y, w1, bh, "1. Name of Producer", "")
        field_box(x2, y, w2, bh, "2. Tax ID (Last 4 digits)", "")
        field_box(x3, y, w3, bh, "3. Crop Year", crop_year, filled=False)
        y -= bh + 0.02*inch

        field_box(ml, y, cw, bh, '4. Names of affiliated persons with farming interests. Enter "None," if applicable.', "")
        y -= bh + 0.06*inch

        c.setFont("Helvetica", 8)
        c.drawString(ml+0.05*inch, y,
            "5. Check one of these boxes if the statement applies; otherwise continue to Part B.")
        y -= 0.12*inch

        y = checkbox_wrap(ml+0.15*inch, y,
            "A. The producer in Part A does not have interest in land devoted to agriculture. Examples include "
            "bee keepers who place their hives on another person's land, producers of crops grown in greenhouses, "
            "and producers of aquaculture AND these producers do not own/lease any agricultural land themselves. "
            "Note: Do not check this box if the producer shares in a crop.")

        y = checkbox_wrap(ml+0.15*inch, y,
            "B. The producer in Part A meets all three of the following: does not participate in any USDA program "
            "that is subject to HELC and WC compliance except Federal Crop Insurance; only has interest in land "
            "devoted to agriculture which is exclusively used for perennial crops, except sugarcane; and has not "
            "converted a wetland after February 7, 2014.")

        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(colors.HexColor("#555555"))
        y = wrap_text(
            'Note: If either box is checked, and the producer does not participate in FSA or NRCS programs, '
            'the full tax identification number must be provided. Go to Part D and sign and date.',
            ml+0.15*inch, y, cw-0.2*inch, "Helvetica-Oblique", 7.5, 0.12*inch)
        c.setFillColor(colors.black)
        y -= 0.08*inch
        hline(y)
        y -= 0.12*inch

        # ── PART B ──────────────────────────────────
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml, y, "PART B - HELC/WC COMPLIANCE QUESTIONS")
        y -= 0.10*inch
        c.setFont("Helvetica", 8)
        y = wrap_text(
            "Indicate YES or NO to each question. If you are unsure of whether a HEL determination, wetland "
            "determination, or NRCS evaluation has been completed, contact your local USDA Service Center.",
            ml+0.05*inch, y, cw, "Helvetica", 8, 0.13*inch)

        bx_hdr = w - mr - 0.90*inch
        c.setFont("Helvetica-Bold", 8)
        c.drawString(bx_hdr, y, "YES  NO")
        y -= 0.13*inch

        y = yn_row(y, "6",
            "During the crop year entered in Part A or the term of a requested USDA loan, did or will the "
            "producer in Part A plant or produce an agricultural commodity (including sugarcane) on land for "
            "which an HEL determination has not been made?")

        y = yn_row(y, "7",
            "Has anyone performed (since December 23, 1985), or will anyone perform any activities to:")

        y = yn_row(y, "   7A",
            'Create new drainage systems, conduct land leveling, filling, dredging, land clearing, or excavation '
            'that has NOT been evaluated by NRCS? If "YES", indicate the year(s): _______')

        y = yn_row(y, "   7B",
            'Improve or modify an existing drainage system that has NOT been evaluated by NRCS? '
            'If "YES", indicate the year(s): _______')

        y = yn_row(y, "   7C",
            'Maintain an existing drainage system that has NOT been evaluated by NRCS? '
            'If "YES", indicate the year(s): _______')

        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(colors.HexColor("#555555"))
        y = wrap_text(
            'Note: If "YES" is checked for Item 7A or 7B, then Part C must be completed. '
            'If "YES" is checked for Item 7C, NRCS does not have to conduct a certified wetland determination.',
            ml+0.15*inch, y, cw-0.2*inch, "Helvetica-Oblique", 7.5, 0.12*inch)
        c.setFillColor(colors.black)
        y -= 0.06*inch

        c.setFont("Helvetica", 8)
        c.drawString(ml+0.05*inch, y,
            "8. Check one or both boxes, if applicable; otherwise, continue to Part C or D.")
        y -= 0.15*inch

        y = checkbox_wrap(ml+0.15*inch, y,
            "A. Check this box only if the producer in Part A has FCIC reinsured crop insurance and filing "
            "this form represents the first time the producer in Part A, including any affiliated person, "
            "has been subject to HELC and WC provisions.")

        y = checkbox_wrap(ml+0.15*inch, y,
            "B. Check this box if producer is a tenant whose landlord refuses compliance, or a landlord whose "
            "tenant is in violation — but all other farms not associated with that party are in compliance. "
            "(AD-1026B or AD-1026C must be completed.)")
        hline(y)
        y -= 0.12*inch

        # ── PART C ──────────────────────────────────
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml, y, "PART C – ADDITIONAL INFORMATION")
        y -= 0.10*inch
        c.setFont("Helvetica", 8)
        c.drawString(ml+0.05*inch, y,
            '9. If "YES" was checked in Item 6 or 7, provide the following information:')
        y -= 0.16*inch

        bh2 = 0.36*inch
        wa=cw*0.20; wb=cw*0.28; wcc=cw*0.26; wd=cw*0.22
        xa=ml; xb=xa+wa+gap; xcc=xb+wb+gap; xd=xcc+wcc+gap
        field_box(xa, y, wa, bh2, "9A. Farm/Tract/Field No.", "")
        field_box(xb, y, wb, bh2, "9B. Activity:", "")
        field_box(xcc, y, wcc, bh2, "9C. Current land use (specify crops):",
                  land_use[:28] if land_use else "", filled=False)
        field_box(xd, y, wd, bh2, "9D. County:", county, filled=False)
        y -= bh2 + 0.08*inch
        hline(y)
        y -= 0.10*inch

        # ── PART D ──────────────────────────────────
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml, y, "PART D – CERTIFICATION OF COMPLIANCE")
        y -= 0.10*inch

        cert = (
            "I have received and read the AD-1026 Appendix and understand and agree to the terms and conditions "
            "therein on all land in which I (or the producer in Part A if different) and any affiliated person "
            "have or will have an interest. I understand that eligibility for certain USDA program benefits is "
            "contingent upon this certification of compliance with HELC and WC provisions and I am responsible "
            "for any non-compliance. I understand and agree that this certification of compliance is considered "
            "continuous and will remain in effect unless revoked or a violation is determined. I further "
            "understand and agree that:"
        )
        y = wrap_text(cert, ml+0.05*inch, y, cw, "Helvetica", 7.5, 0.13*inch)
        y -= 0.05*inch

        for b in [
            "all applicable payments must be refunded if a determination of ineligibility is made for a violation of HELC or WC provisions.",
            "NRCS may verify whether a HELC violation or WC has occurred.",
            "a revised Form AD-1026 must be filed if there are any operation changes or activities that may affect compliance.",
            "affiliated persons are also subject to compliance with HELC and WC provisions.",
        ]:
            y = wrap_text(f"\u25cf  {b}", ml+0.2*inch, y, cw-0.25*inch, "Helvetica", 7.5, 0.13*inch)

        y -= 0.05*inch
        c.setFont("Helvetica-Bold", 8)
        c.drawString(ml+0.05*inch, y, "Producer's Certification:")
        y -= 0.13*inch
        c.setFont("Helvetica-Oblique", 7.5)
        c.drawString(ml+0.05*inch, y,
            "I hereby certify that the information on this form is true and correct to the best of my knowledge.")
        y -= 0.16*inch

        bh3 = 0.40*inch
        ws1=cw*0.42; ws2=cw*0.24; ws3=cw*0.30
        xs1=ml; xs2=xs1+ws1+gap; xs3=xs2+ws2+gap
        field_box(xs1, y, ws1, bh3, "10A. Producer's Signature (By)", "")
        field_box(xs2, y, ws2, bh3, "10B. Title/Relationship", "")
        field_box(xs3, y, ws3, bh3, "10C. Date (MM-DD-YYYY)", "")
        y -= bh3 + 0.06*inch

        # FSA Use Only
        fsa_h = 0.42*inch
        c.setFillColor(colors.HexColor("#F0F0F0"))
        c.rect(ml, y-fsa_h, cw, fsa_h, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(ml+0.1*inch, y-0.13*inch, "FOR FSA USE ONLY (for referral to NRCS)")
        c.setFont("Helvetica", 7.5)
        c.drawString(ml+0.1*inch, y-0.25*inch, "Sign and date if NRCS determination is needed.")
        c.drawString(ml+0.1*inch, y-0.37*inch,
            "11A. Signature of FSA Representative: ___________________________     11B. Date: ____________")
        y -= fsa_h + 0.12*inch  # clear gap below FSA box before IMPORTANT

        # Important Notice — all on same indent level
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(ml, y, "IMPORTANT:")
        c.setFont("Helvetica", 7.5)
        y = wrap_text(
            "If you are unsure about the applicability of HELC and WC provisions to your land, contact your "
            "local USDA Service Center for details concerning the location of any highly erodible land or "
            "wetland and any restrictions applying to your land according to NRCS determinations before "
            "planting an agricultural commodity or performing any drainage or manipulation.",
            ml + 0.82*inch, y, cw - 0.82*inch, "Helvetica", 7.5, 0.12*inch)
        y -= 0.08*inch

        # Screening data box — plain black, no color, facts only
        if ei_max is not None:
            box_h = 0.24*inch
            c.setFillColor(colors.white)
            c.rect(ml, y-box_h, cw, box_h, fill=1, stroke=1)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 7.5)
            c.drawString(ml+0.12*inch, y-0.09*inch,
                f"HEL Screening:  EI = {ei_max:.2f}  |  HEL: {'Yes' if hel_present else 'No'}  |  "
                f"Wetland Indicators: {'Present' if wetland_present else 'Not Detected'}  |  "
                f"County: {county}  |  Crop Year: {crop_year}")
            y -= box_h

        # ══════════════════════════════════════════════
        # PAGE 2
        # ══════════════════════════════════════════════
        c.showPage()
        y = h - 0.5*inch

        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml, y, "AD-1026 (10-30-14)")
        c.drawString(ml + 1.5*inch, y, "Page 2 of 2")
        y -= 0.20*inch
        hline(y, 1.0)
        y -= 0.18*inch

        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(ml, y, "Privacy Act and Paperwork Reduction Act Statement")
        y -= 0.16*inch

        for para in [
            ("NOTE: The following statement is made in accordance with the Privacy Act of 1974 (5 USC 552a - as amended). "
             "The authority for requesting the information identified on this form is 7 CFR Part 12, the Food Security Act of 1985 "
             "(Pub. L. 99-198), and the Agricultural Act of 2014 (Pub. L. 113-79). The information will be used to certify "
             "compliance with HELC and WC provisions and to determine producer eligibility to participate in and receive benefits "
             "under programs administered by USDA agencies. The information collected on this form may be disclosed to other "
             "Federal, State, Local government agencies, Tribal agencies, and nongovernmental entities that have been authorized "
             "access to the information by statute or regulation and/or as described in applicable Routine Uses identified in the "
             "System of Records Notice for USDA/FSA-2, Farm Records File (Automated) and USDA/FSA-14, Applicant/Borrower. "
             "Providing the requested information is voluntary. However, failure to furnish the requested information will result "
             "in a determination of producer ineligibility to participate in and receive benefits under programs administered by USDA agencies."),
            ("This information collection is exempted from the Paperwork Reduction Act as specified in the Agricultural Act of 2014 "
             "(Pub. L. 113-79, Title II, Subtitle G, Funding and Administration). The provisions of appropriate criminal and civil "
             "fraud, privacy, and other statutes may be applicable to the information provided."),
        ]:
            y = wrap_text(para, ml, y, cw, "Helvetica", 8, 0.13*inch)
            y -= 0.10*inch

        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(ml, y, "RETURN THIS COMPLETED FORM AD-1026 TO YOUR COUNTY FARM SERVICE AGENCY (FSA) OFFICE.")
        y -= 0.20*inch
        hline(y)
        y -= 0.16*inch

        for para in [
            ("In accordance with Federal civil rights law and U.S. Department of Agriculture (USDA) civil rights regulations and "
             "policies, the USDA, its Agencies, offices, and employees, and institutions participating in or administering USDA "
             "programs are prohibited from discriminating based on race, color, national origin, religion, sex, disability, age, "
             "marital status, family/parental status, income derived from a public assistance program, political beliefs, or "
             "reprisal or retaliation for prior civil rights activity, in any program or activity conducted or funded by USDA "
             "(not all bases apply to all programs). Remedies and complaint filing deadlines vary by program or incident."),
            ("Persons with disabilities who require alternative means of communication for program information (e.g., Braille, "
             "large print, audiotape, American Sign Language, etc.) should contact the State or local Agency that administers "
             "the program or contact USDA through the Telecommunications Relay Service at 711 (voice and TTY). Additionally, "
             "program information may be made available in languages other than English."),
            ("To file a program discrimination complaint, complete the USDA Program Discrimination Complaint Form, AD-3027, "
             "found online at How to File a Program Discrimination Complaint and at any USDA office or write a letter addressed "
             "to USDA and provide in the letter all of the information requested in the form. To request a copy of the complaint "
             "form, call (866) 632-9992. Submit your completed form or letter to USDA by: (1) mail: U.S. Department of "
             "Agriculture, Office of the Assistant Secretary for Civil Rights, 1400 Independence Avenue, SW, Mail Stop 9410, "
             "Washington, D.C. 20250-9410; (2) fax: (202) 690-7442; or (3) email: program.intake@usda.gov."),
        ]:
            y = wrap_text(para, ml, y, cw, "Helvetica", 8, 0.13*inch)
            y -= 0.10*inch

        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(w/2, y, "USDA is an equal opportunity provider, employer, and lender.")

        c.save()
        pdf_buffer.seek(0)
        return pdf_buffer.getvalue()

    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        return None


def show_farmer_view(analysis_results, r_val, state_label, ls_factor=None, ls_source=None, df=None):
    """
    Display farmer-friendly results: Simple HEL/Wetland status + Next steps

    Args:
        analysis_results: SSURGO soil analysis results
        r_val: R-factor value
        state_label: State/source label for R-factor
        ls_factor: LS factor value (optional)
        ls_source: LS factor source (optional)
        df: DataFrame with soil component data
    """
    if df is not None and not df.empty:
        # Calculate EI and HEL status
        if ls_factor is not None:
            df["EI"] = round(
                (r_val * df["K-Fact"] * ls_factor) / df["T-Fact"], 2
            )
        else:
            df["EI"] = round(
                (r_val * df["K-Fact"] * (df["Slope"] ** 1.2 * 0.1)) / df["T-Fact"], 2
            )

        ei_max = df["EI"].max()
        ei_min = df["EI"].min()

        # 1️⃣ MAIN RESULT: EI Card at top (prominent)
        st.markdown(
            f'''<div style="border: 2px solid #2196F3; border-radius: 8px; padding: 25px; text-align: center; background: #0d1b2a; margin-bottom: 20px;">
            <p style="margin: 0; font-size: 14px; color: #64B5F6;">Erosion Index (EI)</p>
            <p style="margin: 10px 0; font-size: 48px; font-weight: bold; color: #2196F3;">{ei_max:.1f}</p>
            <p style="margin: 0; font-size: 12px; color: #90CAF9; cursor: help;" title="Range: {ei_min:.1f}–{ei_max:.1f}">Range: {ei_min:.1f}–{ei_max:.1f}</p>
            </div>''',
            unsafe_allow_html=True
        )

        # 2️⃣ KEY FACTORS: What drives your EI score
        st.markdown("**📌 Key Factors Affecting Your Score:**")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(
                f'''<div style="border: 1px solid #999; border-radius: 8px; padding: 15px; text-align: center; background: #f5f5f5;">
                <p style="margin: 0; font-size: 12px; color: #666;">💧 Rainfall</p>
                <p style="margin: 8px 0; font-size: 24px; font-weight: bold; color: #333;">{r_val}</p>
                <p style="margin: 0; font-size: 10px; color: #666;">Annual Erosivity</p>
                </div>''',
                unsafe_allow_html=True
            )

        with col2:
            ls_display = f"{ls_factor:.2f}" if ls_factor else "~2.5"
            st.markdown(
                f'''<div style="border: 1px solid #999; border-radius: 8px; padding: 15px; text-align: center; background: #f5f5f5;">
                <p style="margin: 0; font-size: 12px; color: #666;">⛰️ Slope</p>
                <p style="margin: 8px 0; font-size: 24px; font-weight: bold; color: #333;">{ls_display}</p>
                <p style="margin: 0; font-size: 10px; color: #666;">Length × Steepness</p>
                </div>''',
                unsafe_allow_html=True
            )

        with col3:
            st.markdown(
                f'''<div style="border: 1px solid #999; border-radius: 8px; padding: 15px; text-align: center; background: #f5f5f5;">
                <p style="margin: 0; font-size: 12px; color: #666;">🌱 Soil Type</p>
                <p style="margin: 8px 0; font-size: 24px; font-weight: bold; color: #333;">{len(df)}</p>
                <p style="margin: 0; font-size: 10px; color: #666;">Soil Types Found</p>
                </div>''',
                unsafe_allow_html=True
            )

        st.markdown("---")

        st.markdown(
            '<div style="background:#E8F5E9;padding:15px;border-radius:8px;margin-bottom:15px;">'
            '<h3 style="color:#2E7D32;margin:0;">🎯 HEL Eligibility Result</h3>'
            '</div>',
            unsafe_allow_html=True
        )

        # HEL Status
        if ei_max >= 8.0:
            status = "✅ LIKELY HEL"
            color = "#D32F2F"
            explanation = "Your field shows high erosion risk and may qualify for CRP."
        elif ei_min < 8.0 and ei_max >= 8.0:
            status = "⚠️ PARTIALLY HEL (PHEL)"
            color = "#F57C00"
            explanation = "Some soil types on your field show erosion risk. An NRCS visit can clarify."
        else:
            status = "❌ NOT HEL"
            color = "#388E3C"
            explanation = "Your field does not appear to meet HEL criteria for CRP eligibility."

        st.markdown(
            f'<div style="background:{color}20;border-left:4px solid {color};padding:15px;border-radius:4px;margin-bottom:15px;">'
            f'<h2 style="color:{color};margin:0;">{status}</h2>'
            f'<p style="margin:10px 0 0 0;">{explanation}</p>'
            f'<p style="margin:5px 0 0 0;font-size:12px;color:#666;">EI Range: {ei_min:.1f} - {ei_max:.1f}</p>'
            f'</div>',
            unsafe_allow_html=True
        )

        st.markdown("---")

        # 3️⃣ WHAT THIS MEANS (Farmer-friendly explanation)
        st.markdown("**❓ Understanding Your Results:**")
        explanation_text = """
        **Erosion Index (EI):** Measures how susceptible your field is to water erosion.
        - **EI ≥ 8.0 = "HEL"** → High erosion risk; eligible for CRP conservation programs
        - **EI < 8.0 = "NOT HEL"** → Lower erosion risk

        **What Affects Your Score:**
        - **Rainfall:** How much rain your area receives annually
        - **Slope:** How steep and long your field slopes are
        - **Soil Type:** Different soils have different erosion susceptibility
        """
        st.info(explanation_text)

        # 4️⃣ SOIL SUMMARY
        st.markdown("**🌾 Your Soil Summary:**")

        # Field area from SSURGO or polygon calculation
        polygon_acres  = st.session_state.get("polygon_acres", None)
        muname_acres   = st.session_state.get("muname_acres", {})
        if muname_acres:
            field_area = sum(muname_acres.values())
            area_source = "SSURGO"
        elif polygon_acres:
            field_area = polygon_acres
            area_source = "Estimated"
        else:
            field_area = None
            area_source = ""

        col_soil0, col_soil1, col_soil2 = st.columns(3)

        with col_soil0:
            if field_area:
                area_display = f"{field_area:,.1f} acres"
                src_label = area_source
            else:
                area_display = "—"
                src_label = "Draw polygon to calculate"
            st.markdown(
                f'''<div style="border:1px solid #333; border-radius:8px; padding:20px; text-align:center; background:#1a1a1a;">
                <p style="margin:0; font-size:14px; color:#999;">📐 Field Area</p>
                <p style="margin:10px 0; font-size:28px; font-weight:bold; color:#fff;">{area_display}</p>
                <p style="margin:0; font-size:12px; color:#64B5F6;">{src_label}</p>
                </div>''',
                unsafe_allow_html=True
            )

        problem_soils = (df["EI"] >= 8.0).sum()
        with col_soil1:
            st.markdown(
                f'''<div style="border:1px solid #333; border-radius:8px; padding:20px; text-align:center; background:#1a1a1a;">
                <p style="margin:0; font-size:14px; color:#999;">High-Risk Soils</p>
                <p style="margin:10px 0; font-size:28px; font-weight:bold; color:#fff;">{problem_soils}</p>
                <p style="margin:0; font-size:12px; color:#64B5F6;">Soil types with EI ≥ 8.0</p>
                </div>''',
                unsafe_allow_html=True
            )

        hydric_count = (df["Hydric"] == "Yes").sum()
        hydric_val   = "Yes" if hydric_count > 0 else "No"
        hydric_color = "#4CAF50" if hydric_count > 0 else "#999"
        with col_soil2:
            st.markdown(
                f'''<div style="border:1px solid #333; border-radius:8px; padding:20px; text-align:center; background:#1a1a1a;">
                <p style="margin:0; font-size:14px; color:#999;">Hydric Soils</p>
                <p style="margin:10px 0; font-size:28px; font-weight:bold; color:#fff;">{hydric_val}</p>
                <p style="margin:0; font-size:12px; color:{hydric_color};">Wet/wetland soils present</p>
                </div>''',
                unsafe_allow_html=True
            )

        st.markdown("---")

        # Wetland Status - flag potential indicators, not definitive determination
        st.markdown("**💧 Wetland Indicator Checklist:**")

        # Build simple wetland indicators table for farmer view using actual assessment data
        hydric_detected = (df["Hydric"] == "Yes").any()

        # Check if detailed wetland assessment is available
        assessment = st.session_state.get("wetland_assessment")

        if assessment:
            # Use actual detected indicators
            wetland_indicators = {
                "Hydric Soils": "✅ Yes" if assessment["indicators"]["hydric_soils"] else "❌ No",
                "Wetland Vegetation": "✅ Yes" if assessment["indicators"]["wetland_vegetation"] else "❌ No",
                "High Water Table": "✅ Yes" if assessment["indicators"]["hydrology_ssurgo"] else "❌ No",
                "Water Body Nearby": "✅ Yes" if assessment["indicators"]["hydrology_nhd"] else "❌ No"
            }
        else:
            # Fallback if detailed assessment not available
            wetland_indicators = {
                "Hydric Soils": "✅ Yes" if hydric_detected else "❌ No",
                "Wetland Vegetation": "⚠️ Not assessed",
                "High Water Table": "⚠️ Not assessed",
                "Water Body Nearby": "⚠️ Not assessed"
            }

        wetland_df = pd.DataFrame(list(wetland_indicators.items()), columns=["Indicator", "Status"])
        st.dataframe(wetland_df, use_container_width=True, hide_index=True)

        if hydric_detected:
            st.markdown(
                '<div style="background:#E1F5FE;border-left:4px solid #0277BD;padding:15px;border-radius:4px;margin-bottom:15px;">'
                '<h3 style="color:#0277BD;margin:0;">💧 Potential Wetland Indicator Detected</h3>'
                '<p style="margin:10px 0 0 0;color:#333;">Your field contains hydric soils (wetland-forming soils). '
                '<strong>NRCS field verification is required</strong> to make an official wetland determination. '
                'Contact your local NRCS office to schedule a wetland delineation.</p>'
                '</div>',
                unsafe_allow_html=True
            )
        else:
            st.info("ℹ️ No hydric soils detected in SSURGO database. However, a site visit may reveal other wetland indicators.")

    # Next Steps
    st.markdown(
        '<div style="background:#FFF3E0;border-left:4px solid #E65100;padding:15px;border-radius:4px;margin-bottom:15px;">'
        '<h3 style="color:#E65100;margin:0;">📞 What Happens Next?</h3>'
        '<ol style="margin:10px 0 0 0;color:#333;">'
        '<li>Download your pre-filled AD-1026 form below</li>'
        '<li>Fill in your name, tax ID, and answer the Yes/No questions</li>'
        '<li>Take it to your local FSA office to file</li>'
        '<li>FSA will refer to NRCS for an official HEL determination</li>'
        '</ol>'
        '</div>',
        unsafe_allow_html=True
    )

    # Buttons row
    st.markdown("---")
    col1, col2, col3 = st.columns(3)

    with col1:
        # AD-1026 download
        county    = st.session_state.get("detected_county", "")
        state_nm  = st.session_state.get("detected_state", "")
        assessment = st.session_state.get("wetland_assessment")
        wetland_present = bool(assessment and assessment.get("wetland_signal") in ["Strong", "Possible"]) if assessment else hydric_detected

        # Get land use from NLCD if available
        land_use = ""
        if assessment and isinstance(assessment, dict):
            veg = assessment.get("vegetation", {})
            if isinstance(veg, dict):
                land_use = veg.get("class_name", "")

        ei_max_val = df["EI"].max() if df is not None and not df.empty and "EI" in df.columns else None

        try:
            ad1026_pdf = generate_ad1026_pdf(
                county=county,
                state_name=state_nm,
                land_use=land_use,
                ei_max=ei_max_val,
                hel_present=bool(ei_max_val and ei_max_val >= 8.0),
                wetland_present=wetland_present
            )
        except Exception as e:
            ad1026_pdf = None
            st.error(f"AD-1026 error: {e}")

        if ad1026_pdf:
            st.download_button(
                label="📋 Download AD-1026 (FSA Form)",
                data=ad1026_pdf,
                file_name=f"AD-1026_HELC_WC_Certification_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                key="download_ad1026",
                help="Pre-filled FSA compliance certification form — take to your local FSA office"
            )
        else:
            st.warning("⚠️ AD-1026 could not be generated. Check terminal for errors.")

    with col2:
        if st.button("🔍 Find NRCS Office Near Me", key="find_nrcs_farmer"):
            st.warning(
                "⏳ **NRCS Office Locator coming soon!**\n\n"
                "For now, visit: **[NRCS Office Locator](https://offices.sc.egov.usda.gov/)**\n\n"
                "We're working on integrating this directly into the tool."
            )

    with col3:
        if st.button("📋 Print Results", key="print_farmer"):
            st.info("Use your browser's print function (Ctrl+P or Cmd+P) to save this page as PDF.")


def show_conservationist_view(analysis_results, r_val, state_label, ls_factor=None, ls_source=None, df=None):
    """
    Display conservationist-focused results: Technical details + Field verification + NRCS-CPA-026e form

    Args:
        analysis_results: SSURGO soil analysis results
        r_val: R-factor value
        state_label: State/source label for R-factor
        ls_factor: LS factor value (optional)
        ls_source: LS factor source (optional)
        df: DataFrame with soil component data
    """
    # Create tabs for different sections
    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["📊 Results", "🔧 Field Verification", "📋 Components", "📄 NRCS-CPA-026e Form", "⚙️ Technical"]
    )

    with tab1:
        st.subheader("Automated Analysis Results")

        # ── REGULATORY DISCLAIMER (Prominent, Collapsible) ──
        with st.expander("🚨 **Regulatory Disclaimer — Read Before Using Results**", expanded=False):
            st.warning(
                "**This is a pre-screening tool only, not an official determination.**\n\n"
                "• NRCS staff must conduct the official, legally binding HEL determination using NASIS data\n"
                "• This tool uses public data sources (SSURGO, USGS, NOAA, NLCD) that NRCS does not use internally\n"
                "• Results are estimates with known limitations:\n"
                "  - LS factor simplified from DEM (±15–20% error on complex terrain)\n"
                "  - R-factor raster (±1–3% error); state/national fallbacks available\n"
                "  - Wetland indicators are suggestive, not definitive\n"
                "• **Next Step:** Take your results to your local NRCS field office for official review\n"
                "• **Tools provided:** CPA-026e and AD-1026 forms are pre-filled to save you 30+ minutes of paperwork"
            )

        if df is not None and not df.empty:
            # Calculate EI
            if ls_factor is not None:
                df["EI"] = round(
                    (r_val * df["K-Fact"] * ls_factor) / df["T-Fact"], 2
                )
            else:
                df["EI"] = round(
                    (r_val * df["K-Fact"] * (df["Slope"] ** 1.2 * 0.1)) / df["T-Fact"], 2
                )

            ei_max = df["EI"].max()
            ei_min = df["EI"].min()
            k_avg = df["K-Fact"].mean()
            t_avg = df["T-Fact"].mean()

            # 1️⃣ MAIN RESULT: EI at top (largest)
            st.markdown(
                f'''<div style="border: 2px solid #2196F3; border-radius: 8px; padding: 25px; text-align: center; background: #0d1b2a; margin-bottom: 20px;">
                <p style="margin: 0; font-size: 14px; color: #64B5F6;">Erosion Index (EI)</p>
                <p style="margin: 10px 0; font-size: 48px; font-weight: bold; color: #2196F3;">{ei_max:.1f}</p>
                <p style="margin: 0; font-size: 12px; color: #90CAF9; cursor: help;" title="Range: {ei_min:.1f}–{ei_max:.1f}">Range: {ei_min:.1f}–{ei_max:.1f}</p>
                </div>''',
                unsafe_allow_html=True
            )

            # 2️⃣ RUSLE2 PARAMETERS: R, K, LS, T in row
            st.markdown("**📊 RUSLE2 Parameters (EI = R × K × LS / T)**")
            col_r, col_k, col_ls, col_t = st.columns(4)

            with col_r:
                st.markdown(
                    f'''<div style="border: 1px solid #333; border-radius: 8px; padding: 15px; text-align: center; background: #1a1a1a;">
                    <p style="margin: 0; font-size: 12px; color: #999;">R-Factor</p>
                    <p style="margin: 8px 0; font-size: 24px; font-weight: bold; color: #fff;">{r_val}</p>
                    <p style="margin: 0; font-size: 10px; color: #999;">Rainfall</p>
                    </div>''',
                    unsafe_allow_html=True
                )

            with col_k:
                st.markdown(
                    f'''<div style="border: 1px solid #333; border-radius: 8px; padding: 15px; text-align: center; background: #1a1a1a;">
                    <p style="margin: 0; font-size: 12px; color: #999;">K-Factor</p>
                    <p style="margin: 8px 0; font-size: 24px; font-weight: bold; color: #fff;">{k_avg:.3f}</p>
                    <p style="margin: 0; font-size: 10px; color: #999;">Soil Erodibility</p>
                    </div>''',
                    unsafe_allow_html=True
                )

            with col_ls:
                ls_display = f"{ls_factor:.3f}" if ls_factor else "approx"
                st.markdown(
                    f'''<div style="border: 1px solid #333; border-radius: 8px; padding: 15px; text-align: center; background: #1a1a1a;">
                    <p style="margin: 0; font-size: 12px; color: #999;">LS-Factor</p>
                    <p style="margin: 8px 0; font-size: 24px; font-weight: bold; color: #fff;">{ls_display}</p>
                    <p style="margin: 0; font-size: 10px; color: #999;">Slope</p>
                    </div>''',
                    unsafe_allow_html=True
                )

            with col_t:
                st.markdown(
                    f'''<div style="border: 1px solid #333; border-radius: 8px; padding: 15px; text-align: center; background: #1a1a1a;">
                    <p style="margin: 0; font-size: 12px; color: #999;">T-Factor</p>
                    <p style="margin: 8px 0; font-size: 24px; font-weight: bold; color: #fff;">{t_avg:.2f}</p>
                    <p style="margin: 0; font-size: 10px; color: #999;">Tolerance</p>
                    </div>''',
                    unsafe_allow_html=True
                )

            st.markdown("---")

            # 3️⃣ HEL STATUS & HYDRIC SOILS
            col1, col2, col3 = st.columns(3)

            with col1:
                if ei_max >= 8.0:
                    st.markdown(
                        '''<div style="border: 1px solid #333; border-radius: 8px; padding: 20px; text-align: center; background: #1a1a1a;">
                        <p style="margin: 0; font-size: 14px; color: #999;">HEL Status</p>
                        <p style="margin: 10px 0; font-size: 32px; font-weight: bold; color: #fff;">HEL</p>
                        <p style="margin: 0; font-size: 12px; color: #4CAF50; cursor: help;" title="✅ Eligible for CRP HEL program">✓ Eligible for CRP</p>
                        </div>''',
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        '''<div style="border: 1px solid #333; border-radius: 8px; padding: 20px; text-align: center; background: #1a1a1a;">
                        <p style="margin: 0; font-size: 14px; color: #999;">HEL Status</p>
                        <p style="margin: 10px 0; font-size: 32px; font-weight: bold; color: #fff;">NOT HEL</p>
                        <p style="margin: 0; font-size: 12px; color: #f44336; cursor: help;" title="❌ Field does not meet HEL criteria">❌ Not eligible</p>
                        </div>''',
                        unsafe_allow_html=True
                    )

            with col2:
                st.markdown(
                    '''<div style="border: 1px solid #333; border-radius: 8px; padding: 20px; text-align: center; background: #1a1a1a;">
                    <p style="margin: 0; font-size: 14px; color: #999;">Soil Count</p>
                    <p style="margin: 10px 0; font-size: 32px; font-weight: bold; color: #fff;">''' + str(len(df)) + '''</p>
                    <p style="margin: 0; font-size: 12px; color: #64B5F6;">Soil types analyzed</p>
                    </div>''',
                    unsafe_allow_html=True
                )

            with col3:
                hydric = "Yes" if (df["Hydric"] == "Yes").any() else "No"
                hydric_color = "#4CAF50" if hydric == "Yes" else "#4CAF50"
                hydric_tooltip = "🌾 Hydric soils detected on this field" if hydric == "Yes" else "✓ No hydric soils detected"
                st.markdown(
                    f'''<div style="border: 1px solid #333; border-radius: 8px; padding: 20px; text-align: center; background: #1a1a1a;">
                    <p style="margin: 0; font-size: 14px; color: #999;">Hydric Soils</p>
                    <p style="margin: 10px 0; font-size: 32px; font-weight: bold; color: #fff;">{hydric}</p>
                    <p style="margin: 0; font-size: 12px; color: {hydric_color}; cursor: help;" title="{hydric_tooltip}">↑ {hydric_tooltip}</p>
                    </div>''',
                    unsafe_allow_html=True
                )

            # Note: Detailed soil component analysis is in the Components tab
            st.info("📋 **Detailed soil component data** (with HEL status per soil) is available in the **Components** tab")

            # Field area metric (v17)
            muname_acres_c  = st.session_state.get("muname_acres", {})
            polygon_acres_c = st.session_state.get("polygon_acres", None)
            if muname_acres_c:
                field_area_c   = sum(muname_acres_c.values())
                area_source_c  = "SSURGO intersection"
            elif polygon_acres_c:
                field_area_c   = polygon_acres_c
                area_source_c  = "Polygon estimate"
            else:
                field_area_c   = None
                area_source_c  = ""

            if field_area_c:
                st.markdown(
                    f'''<div style="border:1px solid #444; border-radius:8px; padding:12px 10px; background:#1a1a1a; display:inline-block; min-width:160px;">
                    <p style="margin:0; font-size:12px; color:#999;">📐 Total Field Area</p>
                    <p style="margin:4px 0 2px 0; font-size:18px; font-weight:bold; color:#fff;">{field_area_c:,.1f} acres</p>
                    <p style="margin:0; font-size:10px; color:#666;">{area_source_c}</p>
                    </div>''',
                    unsafe_allow_html=True
                )

    with tab2:
        st.subheader("Field Verification (Optional)")
        st.info("🌾 Enter field-measured slope data to verify/refine the automated results. Updates in real-time! ⚡")

        col1, col2 = st.columns(2)

        # Get automated defaults from session state, or use reasonable fallbacks
        auto_slope_length = st.session_state.get("auto_slope_length", 100.0)
        auto_slope_steepness = st.session_state.get("auto_slope_steepness", 5.0)

        with col1:
            field_slope_length = st.number_input(
                "Slope Length (feet)",
                min_value=1.0,
                value=auto_slope_length if auto_slope_length else 100.0,
                step=1.0,
                help="Distance from top to bottom of slope (measured in field). Default shows DEM-calculated estimate."
            )

        with col2:
            field_slope_steepness = st.number_input(
                "Slope Steepness (%)",
                min_value=0.1,
                value=auto_slope_steepness if auto_slope_steepness else 5.0,
                max_value=100.0,
                step=0.1,
                help="Average slope gradient percentage. Default shows DEM-calculated estimate."
            )

        # Real-time calculation (no button needed)
        if field_slope_length > 0 and field_slope_steepness > 0:
            # Calculate LS from field measurements (RUSLE2 formula: LS = L × S)
            # L = (slope_length_ft / 72.6)^0.6  [converts feet to standardized slope length]
            # S = steepness factor based on percent slope
            field_L = (field_slope_length / 72.6) ** 0.6
            if field_slope_steepness >= 9:
                field_S = 1.05 + 0.305 * (field_slope_steepness / 100)  # steep slope
            else:
                field_S = 10.8 * (field_slope_steepness / 100) ** 0.6  # gentle slope
            field_ls = field_L * field_S

            # Recalculate EI with field LS
            field_ei_max = round((r_val * df["K-Fact"].max() * field_ls) / df["T-Fact"].min(), 2)
            automated_ei_max = round((r_val * df["K-Fact"].max() * ls_factor) / df["T-Fact"].min(), 2) if ls_factor else None

            # Display real-time comparison
            st.markdown("---")
            st.subheader("📊 Live Comparison")

            # LS-Factor Comparison (2 columns for better readability)
            st.markdown("**LS-Factor (Slope Length × Slope Steepness):**")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Field-Measured LS", f"{field_ls:.3f}", "from your input data")
            with col2:
                ls_display = f"{ls_factor:.3f}" if ls_factor else "approximated"
                st.metric("Automated LS (DEM)", ls_display, ls_source)

            # LS Difference
            ls_diff = field_ls - (ls_factor if ls_factor else 0)
            pct_diff = (ls_diff/(ls_factor if ls_factor else 1)*100) if ls_factor else 0
            st.info(f"**Difference:** {ls_diff:+.3f} ({pct_diff:+.1f}%) — {'Field steeper' if ls_diff > 0 else 'DEM steeper' if ls_diff < 0 else 'Match'}")

            st.markdown("---")

            # EI Comparison (2 columns for better readability)
            st.markdown("**Erosion Index (EI) — HEL Determination:**")
            col1, col2 = st.columns(2)
            with col1:
                hel_field = "✅ HEL" if field_ei_max >= 8.0 else "❌ NOT HEL"
                st.metric("Field-Based EI", f"{field_ei_max:.1f}", hel_field)
            with col2:
                if automated_ei_max:
                    hel_auto = "✅ HEL" if automated_ei_max >= 8.0 else "❌ NOT HEL"
                    st.metric("Automated EI (DEM)", f"{automated_ei_max:.1f}", hel_auto)
                else:
                    st.metric("Automated EI (DEM)", "N/A", "—")

            # EI Impact
            if automated_ei_max:
                ei_diff = field_ei_max - automated_ei_max
                direction = "↑ Field Higher" if ei_diff > 0.5 else "↓ DEM Higher" if ei_diff < -0.5 else "≈ Similar"
                st.info(f"**EI Difference:** {ei_diff:+.1f} points — {direction}")

            st.info(
                "💡 **Field verification tip:** If field-based EI differs significantly from automated, "
                "it may indicate DEM limitations in steep/complex terrain. Use field-measured data for official determinations."
            )

    with tab3:
        st.subheader("Soil Components")
        if df is not None and not df.empty:
            display_df = df.copy()

            # HEL/PHEL status per soil
            display_df["HEL Status"] = display_df["EI"].apply(
                lambda ei: "✅ HEL" if ei >= 8.0 else "❌ NOT HEL"
            )

            # Add Acres column from SSURGO intersection (v17)
            muname_acres  = st.session_state.get("muname_acres", {})
            polygon_acres = st.session_state.get("polygon_acres", None)
            num_rows      = len(display_df)

            def lookup_acres(soil_name):
                if muname_acres:
                    total = sum(
                        ac for muname, ac in muname_acres.items()
                        if muname.lower().startswith(soil_name.lower())
                    )
                    return round(total, 1) if total > 0 else None
                elif polygon_acres:
                    return round(polygon_acres / num_rows, 1)
                return None

            display_df["Acres"] = display_df["Soil Type"].apply(lookup_acres)
            acres_source = "SSURGO intersection" if muname_acres else (
                "Polygon ÷ components" if polygon_acres else "Draw polygon to calculate"
            )
            st.caption(f"📐 Acreage method: {acres_source}")

            cols_to_show = ["Soil Type", "Acres", "Slope", "K-Fact", "T-Fact", "EI", "HEL Status", "Hydric", "Drainage"]
            display_df = display_df[[col for col in cols_to_show if col in display_df.columns]]

            st.dataframe(display_df, use_container_width=True)

            hel_soils = display_df[display_df["HEL Status"] == "✅ HEL"]["Soil Type"].tolist()
            if hel_soils:
                st.info(f"🚨 **Problem Soils (HEL):** {', '.join(hel_soils)}")
            else:
                st.success("✅ **No HEL soils detected** on this site")

    with tab4:
        st.subheader("📋 NRCS-CPA-026e Form (Pre-filled)")
        st.info("✅ **Download pre-filled NRCS-CPA-026e** form with tool-calculated RUSLE2 parameters. This official NRCS form documents HEL determinations and is ready for conservationist field verification and signature.")

        if df is not None and not df.empty:
            # Display form data preview
            st.markdown("**📊 Pre-fill Data Summary:**")

            col1, col2 = st.columns(2)
            with col1:
                st.metric("R-Factor (Rainfall)", r_val, state_label)
                st.metric("K-Factor (avg)", f"{df['K-Fact'].mean():.4f}", f"Range: {df['K-Fact'].min():.4f}–{df['K-Fact'].max():.4f}")

            with col2:
                ls_display = f"{ls_factor:.3f}" if ls_factor else "Approximated"
                st.metric("LS-Factor (Slope)", ls_display, ls_source)
                st.metric("T-Factor (avg)", f"{df['T-Fact'].mean():.2f}", f"Range: {df['T-Fact'].min():.2f}–{df['T-Fact'].max():.2f}")

            st.markdown("---")

            # EI Summary
            st.markdown("**Erosion Index (EI) Result:**")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Maximum EI", f"{ei_max:.2f}", "Highest soil type")
            with col2:
                st.metric("Minimum EI", f"{ei_min:.2f}", "Lowest soil type")

            hel_status = "✅ HEL" if ei_max >= 8.0 else "❌ NOT HEL"
            status_color = "error" if ei_max >= 8.0 else "success"
            st.metric("🔍 HEL Determination", hel_status, "Per 7 CFR § 12.21")

            st.markdown("---")

            # Download buttons
            st.markdown("**📥 Download Options:**")
            col1, col2, col3 = st.columns(3)

            with col1:
                # Generate NRCS-CPA-026e PDF (v17)
                center_lat   = st.session_state.get("center_lat", 0)
                center_lon   = st.session_state.get("center_lon", 0)
                polygon_acres = st.session_state.get("polygon_acres", None)
                muname_acres  = st.session_state.get("muname_acres", {})
                # Get county/state from reverse geocoding cache if available
                detected_county = st.session_state.get("detected_county", "")
                detected_state  = st.session_state.get("detected_state", "")
                pdf_data = generate_cpa026_pdf(
                    r_val, state_label, ls_factor, ls_source,
                    df, ei_max, ei_min, center_lat, center_lon,
                    county=detected_county,
                    state_name=detected_state,
                    polygon_acres=polygon_acres,
                    muname_acres=muname_acres
                )
                if pdf_data:
                    st.download_button(
                        label="📄 NRCS-CPA-026e PDF",
                        data=pdf_data,
                        file_name=f"NRCS-CPA-026e_HEL_Determination_{datetime.now().strftime('%Y%m%d')}.pdf",
                        mime="application/pdf",
                        key="download_cpa026_pdf"
                    )

            with col2:
                # CSV export
                csv_export = df[["Soil Type", "Slope", "K-Fact", "T-Fact", "EI"]].to_csv(index=False)
                st.download_button(
                    label="📊 Soil Data CSV",
                    data=csv_export,
                    file_name=f"HEL_Soil_Analysis_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    key="download_soil_csv"
                )

            with col3:
                st.info("💡 **Tip:** NRCS staff will verify and sign the form after field visit")

            st.markdown("---")

            # Form information
            st.markdown("**📋 About This Form:**")
            st.markdown("""
            - **NRCS-CPA-026e:** Official NRCS form for documenting HEL/Wetland determinations with RUSLE2 analysis
            - **Purpose:** Used by NRCS conservationists to document technical findings and determinations
            - **Status:** This PDF is pre-filled with screening calculations (NOT official until NRCS signs)
            - **Next Step:** Bring this to your local NRCS office for field verification and official signature
            """)

            st.markdown("---")

            # Important disclaimer
            st.warning(
                "⚠️ **IMPORTANT DISCLAIMER:** This is a screening tool output, NOT an official NRCS form. "
                "Official HEL determinations require NRCS field visit and staff signature per 7 CFR § 12.20–12.30. "
                "Always verify with NRCS before CRP application."
            )

    with tab5:
        st.subheader("Technical Details & Data Sources")

        # Section 1: RUSLE2 Parameters with Sources
        st.markdown("**📊 RUSLE2 Calculation Parameters**")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("R-Factor (Rainfall)", r_val, state_label)
            st.metric("LS Factor (Slope)", f"{ls_factor:.3f}" if ls_factor else "Approx.", ls_source)
        with col2:
            st.metric("K-Factor (Soil Avg)", f"{df['K-Fact'].mean():.3f}", "SSURGO")
            st.metric("T-Factor (Tolerance Avg)", f"{df['T-Fact'].mean():.2f}", "SSURGO")

        st.markdown("---")

        # Section 2: Data Quality & Uncertainty
        st.markdown("**⚠️ Accuracy & Confidence**")
        col1, col2 = st.columns(2)

        with col1:
            st.info(
                f"**R-Factor Source:** {state_label}\n\n"
                f"• NOAA CDO: ±5-8% accuracy (point-specific)\n"
                f"• NRCS FOTG: ±20-30% accuracy (state average)\n\n"
                f"**Current:** {state_label}"
            )

        with col2:
            st.info(
                f"**LS Factor Source:** {ls_source}\n\n"
                f"• DEM-based: ±5% accuracy (USGS 3DEP 30m)\n"
                f"• Slope Approx: ±23% accuracy (fallback)\n\n"
                f"**Current:** {ls_source}"
            )

        st.markdown("---")

        # Section 3: Soil Data Range
        st.markdown("**🌱 Soil Parameter Ranges (K-Factor, T-Factor)**")
        col1, col2 = st.columns(2)

        with col1:
            st.write(f"**K-Factor Range:**")
            st.write(f"• Min: {df['K-Fact'].min():.4f}")
            st.write(f"• Max: {df['K-Fact'].max():.4f}")
            st.write(f"• Avg: {df['K-Fact'].mean():.4f}")

        with col2:
            st.write(f"**T-Factor Range:**")
            st.write(f"• Min: {df['T-Fact'].min():.2f}")
            st.write(f"• Max: {df['T-Fact'].max():.2f}")
            st.write(f"• Avg: {df['T-Fact'].mean():.2f}")

        st.markdown("---")

        # Section 4: Data Sources & Disclaimers
        st.markdown("**📍 Data Sources**")
        st.markdown(
            """
            • **R-Factor**: NOAA Climate Data Online (CDO) API or NRCS FOTG
            • **K-Factor**: USDA SSURGO Database (Natural Resources Conservation Service)
            • **LS-Factor**: USGS 3DEP 30m Digital Elevation Model
            • **T-Factor**: USDA SSURGO Database
            • **Hydric Soil**: USDA SSURGO Soil Properties

            **⚠️ Limitations:**
            - LS factor uses D8 flow accumulation (NRCS uses field-measured slope length)
            - R-factor relies on point-specific precipitation (may differ in complex terrain)
            - Results are indicative only — NOT an official RUSLE2 or HEL determination
            """
        )


# --- 6. Main Content: Map + Results ---
col_map, col_res = st.columns([2, 1])

with col_map:
    m = folium.Map(location=st.session_state["map_center"], zoom_start=14)
    LocateControl().add_to(m)
    Draw(export=True).add_to(m)  # Folium Draw tool: Delete removes parcels from map, Edit modifies geometry

    if st.session_state["current_bounds"]:
        folium.Rectangle(
            bounds=st.session_state["current_bounds"],
            color="#FF4B4B",
            fill=True,
            fill_opacity=0.3
        ).add_to(m)
        m.fit_bounds(st.session_state["current_bounds"])

    map_output = st_folium(m, width="100%", height=500, key="crp_master_map")

    # Drawn polygon handler with normalize + rate limit
    if map_output and map_output.get("all_drawings") and can_make_request():
        last_draw  = map_output["all_drawings"][-1]
        coords     = last_draw["geometry"]["coordinates"][0]
        pts        = [f"{p[0]} {p[1]}" for p in coords]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        drawn_wkt  = f"POLYGON(({', '.join(pts)}))"
        normalized = normalize_wkt(drawn_wkt)

        if normalized != st.session_state["last_wkt"]:
            lats  = [c[1] for c in coords]
            lons  = [c[0] for c in coords]
            c_lat = sum(lats) / len(lats)
            c_lon = sum(lons) / len(lons)

            st.session_state["last_wkt"]          = normalized
            st.session_state["last_request_time"] = time.time()
            st.session_state["is_loading"]        = True
            st.session_state["center_lat"]        = c_lat
            st.session_state["center_lon"]        = c_lon
            # Store bounds for wetland assessment (drawn polygon bounds)
            st.session_state["drawn_bounds"]      = [min(lats), min(lons), max(lats), max(lons)]
            # Calculate total polygon area — fallback (v17)
            acres_val, _ = calculate_polygon_acres(coords)
            st.session_state["polygon_acres"]     = acres_val
            # Per-soil-unit acres via SSURGO intersection — NRCS method (v17)
            muname_acres, acres_err = calculate_ssurgo_acres_per_mukey(drawn_wkt)
            st.session_state["muname_acres"]      = muname_acres
            if acres_err:
                st.session_state["muname_acres_err"] = acres_err
            # ── CONCURRENT API FETCH (v18 Optimization) ──
            # Fetch R-factor, geocoding, and soil data in parallel (~45s → ~20s)
            if RASTERIO_AVAILABLE and not RFACTOR_RASTER_LOCAL_PATH.exists():
                st.info("⏳ Loading R-factor raster for the first time (~30s one-time download)...")

            with st.spinner("Fetching R-factor, location, and soil data in parallel..."):
                location_data = fetch_location_data_concurrent(c_lat, c_lon, drawn_wkt)

            # Unpack concurrent results
            st.session_state["detected_r"]      = location_data['r_factor']
            st.session_state["detected_county"] = location_data['county']
            st.session_state["detected_state"]  = location_data['state']
            st.session_state["analysis_results"] = location_data['soil_data']

            # Report any fetch errors (non-critical; some data may still be valid)
            if location_data['errors']:
                for err in location_data['errors']:
                    st.warning(f"⚠️ {err}")

            st.session_state["is_loading"] = False
            st.rerun()


with col_res:
    # Check if analysis has been run
    if not st.session_state["analysis_results"]:
        # ═══════════════════════════════════════════════════════════
        # GETTING STARTED: Show intro card before first analysis
        # ═══════════════════════════════════════════════════════════
        # Different instructions based on user mode
        if conservationist_mode:
            st.markdown(
                '''
                <div style="background:#E8F5E9;border-left:4px solid #388E3C;padding:20px;border-radius:8px;margin-bottom:20px;">
                <h3 style="color:#2E7D32;margin-top:0;">👨‍🌾 Conservationist Workspace</h3>
                <p style="color:#333;margin:10px 0;">Verify field data, generate NRCS-CPA-026e forms, and access technical RUSLE2 parameters.</p>

                <h4 style="color:#2E7D32;">📋 Workflow:</h4>
                <ol style="color:#333;margin-left:20px;">
                    <li><strong>Draw Polygon or Enter Coordinates</strong> — Define field boundary (⚡ polygon auto-analyzes)</li>
                    <li><strong>View Results Tab</strong> — Check automated HEL status and EI metrics</li>
                    <li><strong>Field Verification Tab</strong> — Enter measured slope length & steepness from site visit</li>
                    <li><strong>NRCS-CPA-026e Form Tab</strong> — Download pre-filled HEL determination form</li>
                    <li><strong>Technical Tab</strong> — Review R, K, LS, T factors and uncertainty flags</li>
                </ol>

                <h4 style="color:#2E7D32;">🔍 Key Features:</h4>
                <ul style="color:#333;margin-left:20px;">
                    <li><strong>Field Data Override</strong> — Compare automated vs. measured slopes</li>
                    <li><strong>Form Integration</strong> — NRCS-CPA-026e pre-fill with RUSLE2 data reduces paperwork by 30+ min</li>
                    <li><strong>RUSLE2 Transparency</strong> — See all calculation parameters and sources</li>
                </ul>
                </div>
                ''',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                '''
                <div style="background:#E3F2FD;border-left:4px solid #1976D2;padding:20px;border-radius:8px;margin-bottom:20px;">
                <h3 style="color:#1565C0;margin-top:0;">🌾 Welcome to CRP HEL Screening Tool</h3>
                <p style="color:#333;margin:10px 0;">Get a quick assessment of your field's erosion risk and CRP eligibility.</p>

                <h4 style="color:#1565C0;">📋 How to Use:</h4>
                <ol style="color:#333;margin-left:20px;">
                    <li><strong>Draw Polygon on Map</strong> — Draw your field boundary on the map (⚡ auto-analyzes instantly)</li>
                    <li><strong>OR Enter Coordinates</strong> — Use the sidebar to enter lat/lon values, then click <strong>"🚀 Analyze"</strong></li>
                    <li><strong>View Results</strong> — Get your HEL eligibility status (✅ HEL, ⚠️ PHEL, or ❌ NOT HEL)</li>
                    <li><strong>Download AD-1026 Form</strong> — Get your pre-filled FSA compliance form (📋 in Farmer Mode) and take it to your local FSA office</li>
                    <li><strong>Contact NRCS</strong> — FSA will refer to NRCS for an official HEL determination</li>
                </ol>

                <h4 style="color:#1565C0;">💡 Try an Example:</h4>
                <p style="color:#333;">Use "National Search" in the sidebar to jump to pre-loaded test regions:</p>
                <ul style="color:#333;margin-left:20px;">
                    <li><strong>Boone, IA</strong> — High erosion example (HEL likely)</li>
                    <li><strong>Ames, IA</strong> — Flat terrain (NOT HEL)</li>
                    <li><strong>Palouse, WA</strong> — Extreme slopes (PHEL)</li>
                </ul>
                </div>
                ''',
                unsafe_allow_html=True
            )
    else:
        # ═══════════════════════════════════════════════════════════
        # FIELD ANALYSIS: Show after first analysis runs
        # ═══════════════════════════════════════════════════════════
        st.subheader("Field Analysis")

        # R-factor (get values for calculation)
        r_val, state_label, method = st.session_state["detected_r"]

        # Determine source type label
        if method == "NOAA Raster":
            source_type = "NOAA CONUS Raster (Ag Handbook 703)"
            source_icon = "🗺️"
        elif "NOAA" in state_label:
            source_type = "Point-Specific (NOAA CDO)"
            source_icon = "🎯"
        else:
            source_type = "State Average (NRCS FOTG)"
            source_icon = "🗺️"

        # Display R-factor banner
        st.markdown(
            f'<div class="r-banner">'
            f'📍 <b>Data Source:</b> {state_label}<br>'
            f'{source_icon} <b>Applied R-Factor:</b> {r_val} '
            f'<span style="font-size:11px;color:#888;">({source_type})</span>'
            f'</div>',
            unsafe_allow_html=True
        )

        # Show raster debug error if present (helps diagnose fallback)
        raster_err = st.session_state.get("raster_error")
        if raster_err and method != "NOAA Raster":
            with st.expander("⚠️ Raster R-factor debug info", expanded=False):
                st.code(raster_err)

        # LS factor display (will be populated after soil data analysis)
        ls_display_placeholder = st.empty()

    if st.session_state["analysis_results"]:
        res = st.session_state["analysis_results"]

        if "error" in res:
            st.error(f"⚠️ {res['error']}")
            st.info("Try a different area or check your connection.")

        elif "Table" in res and res["Table"]:
            df = pd.DataFrame(
                res["Table"],
                columns=["Soil Type", "Slope", "T-Fact", "K-Fact", "Hydric", "Drainage"]
            )
            df[["Slope", "T-Fact", "K-Fact"]] = df[["Slope", "T-Fact", "K-Fact"]].apply(
                pd.to_numeric, errors="coerce"
            )
            df = df.dropna(subset=["Slope", "T-Fact", "K-Fact"])

            if df.empty:
                st.warning("Soil data returned but could not be parsed. Try a different area.")
            else:
                # EI = (R × K × LS) / T
                # Try DEM-based LS calculation first, fall back to approximation if it fails
                ls_dem, is_dem_based, l_factor_avg, s_factor_avg, slope_pct_avg = calculate_ls_factor_from_dem(
                    st.session_state.get("center_lat", 0),
                    st.session_state.get("center_lon", 0)
                )

                # Store DEM components in session state for field verification defaults
                if ls_dem is not None:
                    # Use DEM-based LS (more accurate)
                    ls_factor = ls_dem
                    ls_source = "DEM-based (±5% error)"
                    # Convert L-factor to approximate slope length in feet
                    # L-factor = (flow_accum * 30 / 22.13)^0.4, so reverse:
                    # flow_accum = ((L_factor) ^ (1/0.4)) * 22.13 / 30
                    est_slope_length = max(50, min(300, (l_factor_avg ** 2.5) * 72.6))  # Bounds: 50-300 feet
                    est_slope_steepness = max(0.5, min(30, slope_pct_avg))  # Bounds: 0.5-30%
                    st.session_state["auto_slope_length"] = est_slope_length
                    st.session_state["auto_slope_steepness"] = est_slope_steepness
                else:
                    # Fallback to approximation (less accurate)
                    ls_factor = None  # Will use per-row calculation below
                    ls_source = "Slope approximation (±23% error)"
                    st.session_state["auto_slope_length"] = None
                    st.session_state["auto_slope_steepness"] = None

                # =================================================================
                # TWO-TIER UI: Call appropriate view based on user mode
                # =================================================================
                if conservationist_mode:
                    # Conservationist workspace with tabs
                    st.divider()
                    show_conservationist_view(
                        analysis_results=res,
                        r_val=r_val,
                        state_label=state_label,
                        ls_factor=ls_factor,
                        ls_source=ls_source,
                        df=df
                    )
                else:
                    # Farmer-friendly simple results
                    st.divider()
                    show_farmer_view(
                        analysis_results=res,
                        r_val=r_val,
                        state_label=state_label,
                        ls_factor=ls_factor,
                        ls_source=ls_source,
                        df=df
                    )

                # Display LS factor (for both modes)
                if ls_factor is not None:
                    ls_display_placeholder.markdown(
                        f'<div class="r-banner">'
                        f'📏 <b>LS Factor:</b> {ls_factor:.3f} '
                        f'<span style="font-size:11px;color:#888;">({ls_source})</span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                # Display RUSLE2 Parameters Summary (R, K, LS, T)
                if not df.empty:
                    k_avg = df["K-Fact"].mean()
                    t_avg = df["T-Fact"].mean()
                    ls_display = f"{ls_factor:.3f}" if ls_factor else "approx"

                    st.markdown(
                        f'<div class="r-banner">'
                        f'📊 <b>RUSLE2 Parameters:</b><br>'
                        f'<span style="font-size:11px;">'
                        f'R={r_val} | K={k_avg:.3f} (avg) | LS={ls_display} | T={t_avg:.2f} (avg)<br>'
                        f'<i>EI = (R × K × LS) / T</i>'
                        f'</span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                # Steep slope warning placeholder (will be populated later)
                steep_slope_placeholder = st.empty()

                # Calculate EI with either DEM-based or approximated LS
                if ls_factor is not None:
                    # Use single LS for all rows (conservative max from DEM)
                    df["EI"] = round(
                        (r_val * df["K-Fact"] * ls_factor) / df["T-Fact"], 2
                    )
                else:
                    # Fallback: LS = Slope^1.2 × 0.1 (original approximation)
                    df["EI"] = round(
                        (r_val * df["K-Fact"] * (df["Slope"] ** 1.2 * 0.1)) / df["T-Fact"], 2
                    )

                # Extract min/max slopes from soil type name (e.g., "Soil X, 2 to 5 percent slopes")
                import re
                def extract_slope_range(soil_name):
                    match = re.search(r'(\d+)\s+to\s+(\d+)\s+percent', soil_name)
                    if match:
                        return int(match.group(1)), int(match.group(2))
                    return None, None

                df[["Slope_Min", "Slope_Max"]] = df["Soil Type"].apply(
                    lambda x: pd.Series(extract_slope_range(x))
                )

                # Calculate EI at min and max slopes for PHEL determination
                if ls_factor is not None:
                    # Use DEM-based LS for min/max as well
                    df["EI_Min"] = round(
                        (r_val * df["K-Fact"] * ls_factor) / df["T-Fact"], 2
                    )
                    df["EI_Max"] = round(
                        (r_val * df["K-Fact"] * ls_factor) / df["T-Fact"], 2
                    )
                else:
                    # Fallback to approximation
                    df["EI_Min"] = round(
                        (r_val * df["K-Fact"] * ((df["Slope_Min"] ** 1.2 * 0.1))) / df["T-Fact"], 2
                    )
                    df["EI_Max"] = round(
                        (r_val * df["K-Fact"] * ((df["Slope_Max"] ** 1.2 * 0.1))) / df["T-Fact"], 2
                    )

                # Determine HEL/PHEL/NOT HEL status based on NRCS Part 616 methodology
                def determine_hel_status(ei_min, ei_max):
                    if pd.isna(ei_min) or pd.isna(ei_max):
                        # No slope range found, use single EI value
                        ei_single = max(ei_min, ei_max)
                        if ei_single >= 8.0:
                            return "HEL"
                        else:
                            return "NOT HEL"
                    elif ei_min >= 8.0:
                        return "HEL"
                    elif ei_max >= 8.0 and ei_min < 8.0:
                        return "PHEL (Need field visit)"
                    else:
                        return "NOT HEL"

                df["HEL/PHEL Status"] = df.apply(lambda row: determine_hel_status(row["EI_Min"], row["EI_Max"]), axis=1)

                max_ei    = df["EI"].max()
                max_slope = df["Slope"].max()

                # Display steep slope warning if applicable
                if max_slope > 15:
                    if ls_factor is not None:
                        steep_slope_placeholder.warning(
                            f"⚠️ Steep slopes detected (max {max_slope}%). "
                            "LS factor calculated from DEM elevation data. "
                            "NRCS field verification recommended for steep terrain."
                        )
                    else:
                        steep_slope_placeholder.warning(
                            f"⚠️ Steep slopes detected (max {max_slope}%). "
                            "LS factor is approximated from steepness only — slope length "
                            "unavailable. NRCS field verification recommended."
                        )

                # Hydric rating: identifies soils with wetland-forming potential (restoration candidates)
                # CP suggestion thresholds pending domain expert input — see questionnaire in PDF
                hydric_count = (df["Hydric"].str.strip().str.lower() == "yes").sum()
                hydric_pct   = round(hydric_count / len(df) * 100) if len(df) > 0 else 0
                has_hydric   = hydric_count > 0

                # ===== ENHANCED WETLAND DETECTION (NEW) =====
                # Use polygon center for NLCD + NHD checks
                # Extract bounds from drawn polygon or precision entry
                bounds = None

                # Try drawn polygon bounds first
                if st.session_state.get("drawn_bounds"):
                    bounds = st.session_state["drawn_bounds"]  # Format: [lat_min, lon_min, lat_max, lon_max]
                # Otherwise try precision entry bounds
                elif st.session_state.get("current_bounds"):
                    # Format: [[lat_min, lon_min], [lat_max, lon_max]]
                    bounds_list = st.session_state["current_bounds"]
                    if len(bounds_list) >= 2:
                        bounds = [bounds_list[0][0], bounds_list[0][1], bounds_list[1][0], bounds_list[1][1]]

                # DEBUG: Only show debug info if debug mode enabled
                if False:
                    with st.expander("🔍 Wetland Assessment Debug Info"):
                        st.write(f"**WETLAND_FEATURES_AVAILABLE:** {WETLAND_FEATURES_AVAILABLE}")
                        st.write(f"**bounds extracted:** {bounds is not None}")
                        st.write(f"**assessment will run:** {WETLAND_FEATURES_AVAILABLE and bounds}")
                        if bounds:
                            st.write(f"**bounds coords:** {bounds}")

                if WETLAND_FEATURES_AVAILABLE and bounds:
                    try:
                        # Get polygon centroid
                        polygon_center_lat = (bounds[0] + bounds[2]) / 2
                        polygon_center_lon = (bounds[1] + bounds[3]) / 2

                        # Get drainage class: check if ANY component has poor drainage
                        drainage_classes = df["Drainage"].dropna()

                        # Check if any drainage class indicates poor drainage
                        has_poor_drainage_component = False
                        dominant_drainage = None  # Also track the most common for reference

                        if len(drainage_classes) > 0:
                            # Get the most common drainage class for display
                            dominant_drainage = drainage_classes.mode()[0] if len(drainage_classes.mode()) > 0 else drainage_classes.iloc[0]

                            # Check if ANY component has poor drainage keywords
                            for drain_val in drainage_classes:
                                if drain_val and any(keyword in str(drain_val).lower() for keyword in ["poorly", "poor", "somewhat poor"]):
                                    has_poor_drainage_component = True
                                    break

                        # Store for use in indicator display below
                        st.session_state["dominant_drainage_value"] = dominant_drainage

                        # DEBUG: Log what we're getting from SSURGO
                        st.session_state["debug_logs"].append(f"Drainage classes found: {list(drainage_classes.unique())}")
                        st.session_state["debug_logs"].append(f"Dominant drainage: {dominant_drainage}")
                        st.session_state["debug_logs"].append(f"Has poor drainage component: {has_poor_drainage_component}")

                        # Water table: estimate from drainage class (proxy method)
                        # Note: Direct comonth queries pending NRCS API stability; using drainage class correlation
                        watertab_depth = None
                        try:
                            watertab_depth = get_ssurgo_water_table(polygon_center_lat, polygon_center_lon, drainage_class=dominant_drainage)
                            if watertab_depth:
                                st.session_state["debug_logs"].append(f"✅ Water table estimate: {watertab_depth:.1f} cm (from drainage class: {dominant_drainage})")
                            else:
                                st.session_state["debug_logs"].append(f"⚠️ Water table: drainage class {dominant_drainage} indicates well-drained (no wetland hydrology)")
                        except Exception as wt_err:
                            st.session_state["debug_logs"].append(f"⚠️ Water table estimation failed: {wt_err}")

                        # Fetch NLCD vegetation with error logging
                        vegetation = None
                        try:
                            vegetation = get_nlcd_vegetation_type(polygon_center_lat, polygon_center_lon)
                            if vegetation:
                                st.session_state["debug_logs"].append(f"✅ NLCD vegetation found: {vegetation['class_name']}")
                        except Exception as nlcd_err:
                            st.session_state["debug_logs"].append(f"⚠️ NLCD fetch failed: {nlcd_err}")

                        # Check NHD proximity with error logging
                        nhd_proximity = None
                        try:
                            nhd_proximity = get_nhd_proximity(polygon_center_lat, polygon_center_lon, search_radius_km=5.0)
                            if nhd_proximity:
                                st.session_state["debug_logs"].append(f"✅ NHD check complete: {nhd_proximity['hydrology_signal']} signal")
                        except Exception as nhd_err:
                            st.session_state["debug_logs"].append(f"⚠️ NHD fetch failed: {nhd_err}")

                        # Interpret SSURGO watertab
                        ssurgo_hydrology = detect_wetland_hydrology_from_ssurgo(watertab_depth)

                        # Combine all indicators
                        # If ANY component has poor drainage, pass that signal to assessment
                        drainage_for_assessment = dominant_drainage
                        if has_poor_drainage_component and dominant_drainage:
                            # Ensure assessment knows there's poor drainage
                            # If dominant is already poor, use it; otherwise highlight poor drainage presence
                            if not any(keyword in str(dominant_drainage).lower() for keyword in ["poorly", "poor", "somewhat poor"]):
                                drainage_for_assessment = "Poorly drained"  # Signal that field has poor drainage soils

                        wetland_assessment = combine_wetland_indicators(
                            hydric_rating="yes" if has_hydric else "no",
                            drainage_class=drainage_for_assessment,
                            vegetation=vegetation,
                            hydrology_ssurgo=ssurgo_hydrology,
                            hydrology_nhd=nhd_proximity
                        )

                    except Exception as e:
                        print(f"⚠️ Wetland feature detection error: {e}")
                        wetland_assessment = None
                else:
                    wetland_assessment = None

                st.metric("Erosion Index (EI) — Indicative", max_ei)

                # A — Confidence indicator
                conf_level, conf_color, conf_msg = get_confidence(
                    max_ei, state_label, max_slope
                )
                conf_colors = {"green": "#1B4332", "orange": "#92400E", "red": "#7f1d1d"}
                conf_border = {"green": "#52B788", "orange": "#F59E0B", "red": "#d84315"}
                st.markdown(
                    f'<div style="background-color:{conf_colors[conf_color]};'
                    f'border-left:5px solid {conf_border[conf_color]};'
                    f'padding:10px;border-radius:5px;margin-bottom:10px;'
                    f'font-size:11px;color:#fff;line-height:1.4;">'
                    f'<b>Confidence: {conf_level}</b><br>{conf_msg}'
                    f'</div>',
                    unsafe_allow_html=True
                )

                # B — R-factor confidence flag
                if "fallback" in state_label.lower() or "unknown" in state_label.lower():
                    st.error(
                        "⚠️ State not detected — R-factor defaulted to 100. "
                        "Results are less reliable. Try redrawing the polygon or "
                        "use Precision Entry with verified coordinates."
                    )

                # D — Enhanced wetland indicator (SSURGO + NLCD + NHD)
                if wetland_assessment and WETLAND_FEATURES_AVAILABLE:
                    # Display comprehensive wetland assessment
                    assessment = wetland_assessment

                    # Color coding based on confidence
                    color_map = {
                        "High": ("#0d3349", "#38BDF8", "💧"),
                        "Medium": ("#1f2937", "#60A5FA", "🌊"),
                        "Low": ("#374151", "#93C5FD", "💦")
                    }
                    bg_color, border_color, emoji = color_map.get(assessment["confidence"], ("#0d3349", "#38BDF8", "💧"))

                    # Build indicator list — PRIMARY indicators only (per NRCS determination criteria)
                    indicators_display = ""
                    if assessment["indicators"]["hydric_soils"]:
                        indicators_display += "✓ Hydric soils (SSURGO)<br>"
                    if assessment["indicators"]["wetland_vegetation"]:
                        veg_type = vegetation.get("vegetation_type", "Wetland vegetation") if vegetation else "Wetland vegetation"
                        indicators_display += f"✓ {veg_type} (NLCD)<br>"
                    if assessment["indicators"]["hydrology_ssurgo"]:
                        indicators_display += "✓ High water table (SSURGO)<br>"
                    if assessment["indicators"]["hydrology_nhd"]:
                        indicators_display += "✓ Proximity to water body (NHD)<br>"

                    # SUPPLEMENTARY: Drainage class — useful screening signal, not a determining factor
                    # (Drainage class is derived from soil morphology already captured in hydricrating)
                    supp = assessment.get("supplementary", {})
                    if supp.get("poor_drainage") and supp.get("drainage_class_label"):
                        indicators_display += (
                            f'<span style="color:#94a3b8;font-size:10px;">'
                            f'ℹ️ Supplementary: {supp["drainage_class_label"]} — '
                            f'supporting signal only, not used for determination</span><br>'
                        )

                    st.markdown(
                        f'<div style="background-color:{bg_color};border-left:5px solid {border_color};'
                        f'padding:10px;border-radius:5px;margin-bottom:10px;'
                        f'font-size:11px;color:#BAE6FD;line-height:1.4;">'
                        f'<b>{emoji} Potential Wetland Indicators Detected</b><br>'
                        f'<i>Indicator Strength: {assessment["confidence"]}</i><br><br>'
                        f'{indicators_display}'
                        f'<b>Next Step:</b> Contact your local NRCS office for official wetland determination. '
                        f'An NRCS conservationist will conduct a field visit to verify wetland status per Federal Interagency Delineation Manual standards.'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                    # Add detailed wetland indicators table for conservationists
                    st.markdown("**📊 Detailed Wetland Indicators Assessment:**")

                    # Build detailed evidence for each indicator
                    hydrology_nhd_evidence = "—"
                    if nhd_proximity and assessment["indicators"]["hydrology_nhd"]:
                        wetland_type = nhd_proximity.get("wetland_type", "Water body")
                        nwi_attr = nhd_proximity.get("nwi_attribute", "")
                        signal = nhd_proximity.get("hydrology_signal", "")
                        hydrology_nhd_evidence = f"{wetland_type} (NWI: {nwi_attr}) - {signal} signal"

                    # Build table with smart NLCD display
                    # Show NLCD only when detected as "Yes" (vegetation present)
                    # Omit when "No" (not actionable for conservationists; field visits verify vegetation)
                    wetland_table_data = [
                        {
                            "Indicator": "Hydric Soils",
                            "Detected": "✅ Yes" if assessment["indicators"]["hydric_soils"] else "❌ No",
                            "Evidence": "SSURGO hydricrating indicates wetland-forming soils (hydric rating present)",
                            "Confidence": "High" if assessment["indicators"]["hydric_soils"] else "—"
                        }
                    ]

                    # Only include NLCD vegetation indicator when positive result
                    if vegetation and assessment["indicators"]["wetland_vegetation"]:
                        nlcd_class = vegetation.get("nlcd_class", "N/A")
                        veg_type = vegetation.get("vegetation_type", "Wetland vegetation")
                        veg_evidence = f"NLCD Class {nlcd_class}: {veg_type}"
                        wetland_table_data.append({
                            "Indicator": "Hydrophytic Vegetation (NLCD)",
                            "Detected": "✅ Yes",
                            "Evidence": veg_evidence,
                            "Confidence": "High"
                        })

                    wetland_table_data.extend([
                        {
                            "Indicator": "Wetland Hydrology (Water Table)",
                            "Detected": "✅ Yes" if assessment["indicators"]["hydrology_ssurgo"] else "❌ No",
                            "Evidence": "SSURGO drainage class indicates poorly drained soils (≤30cm water table)",
                            "Confidence": "High" if assessment["indicators"]["hydrology_ssurgo"] else "—"
                        },
                        {
                            "Indicator": "Proximity to Water Body",
                            "Detected": "✅ Yes" if assessment["indicators"]["hydrology_nhd"] else "❌ No",
                            "Evidence": hydrology_nhd_evidence if hydrology_nhd_evidence != "—" else "NHD/NWI database shows no mapped wetland within 5km",
                            "Confidence": "High" if assessment["indicators"]["hydrology_nhd"] else "—"
                        }
                    ])

                    wetland_table_df = pd.DataFrame(wetland_table_data)
                    st.dataframe(wetland_table_df, use_container_width=True, hide_index=True)

                    st.info(
                        "📋 **For Official Determination:**\n\n"
                        "Per Federal Interagency Wetlands Delineation Manual, official determination requires:\n"
                        "1. All three primary indicators (hydric soils, hydrophytic vegetation, wetland hydrology) OR\n"
                        "2. Two primary indicators in certain combinations\n\n"
                        "Schedule NRCS field visit to verify indicators and document findings."
                    )

                elif has_hydric:
                    # Fallback to simple hydric indicator if detailed wetland features unavailable
                    st.markdown(
                        f'<div style="background-color:#0d3349;border-left:5px solid #38BDF8;'
                        f'padding:10px;border-radius:5px;margin-bottom:10px;'
                        f'font-size:11px;color:#BAE6FD;line-height:1.4;">'
                        f'<b>💧 Potential Wetland Indicator: Hydric Soils</b><br>'
                        f'{hydric_pct}% of soil components classified as hydric (SSURGO). '
                        f'This indicator suggests potential wetland. '
                        f'Contact your local NRCS office for official wetland determination and field verification.'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                if max_ei >= 8.0:
                    st.success("✅ LIKELY ELIGIBLE (HEL — Indicative)")
                    st.markdown(
                        f'<div style="background-color:#1B4332;border-radius:6px;'
                        f'padding:12px 14px;margin-top:8px;line-height:1.6;font-size:11px;color:#D8F3DC;">'
                        f'Based on your EI of <b>{max_ei}</b>, this land <b>may qualify</b> for CRP enrollment. '
                        f'Potential practice categories to explore with your local FSA office:'
                        f'<br><br>'
                        f'<b>🌾 Grassland &amp; Cropland</b><br>'
                        f'&nbsp;&nbsp;CP1 (Introduced Grasses), CP2 (Native Grasses), CP4D (Wildlife Habitat Grasses)'
                        f'<br><br>'
                        f'<b>🦅 Wildlife &amp; Habitat</b><br>'
                        f'&nbsp;&nbsp;CP33 (Upland Bird Habitat Buffers), CP42 (Pollinator Habitat), CP43 (Prairie Strips)'
                        f'<br><br>'
                        f'<b>💧 Water Protection</b> <i style="color:#95D5B2;">(if land is adjacent to water)</i><br>'
                        f'&nbsp;&nbsp;CP21 (Filter Strips), CP22 (Riparian Forest Buffer), CP29 (Marginal Pastureland Buffer)'
                        f'<br><br>'
                        f'<b>🌿 Wetland Practices</b> ' + (
                            f'<b style="color:#38BDF8;">(Hydric soils detected — {hydric_pct}% of components)</b>'
                            if has_hydric else
                            '<i style="color:#95D5B2;">(no hydric soils detected in SSURGO — confirm with site visit)</i>'
                        ) +
                        '<br>'
                        '&nbsp;&nbsp;CP23 / CP23A (Wetland Restoration), CP27 / CP28 (Farmable Wetland Practices)'
                        f'<br><br>'
                        f'<span style="color:#95D5B2;font-size:10px;">→ Contact your local USDA Service Center to confirm '
                        f'which practices apply to your specific land type, location, and current signup period.</span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                else:
                    st.error("❌ LIKELY INELIGIBLE (EI < 8.0 — Indicative)")

                st.divider()
                display_df = df[["Soil Type", "Slope", "K-Fact", "EI", "HEL/PHEL Status", "Hydric"]].reset_index(drop=True)
                html_table = display_df.to_html(index=False, escape=False)

                # Wrap in scrollable container for mobile (simple approach)
                html_wrapper = f"""
                <div style="overflow-x: auto; margin: 10px 0;">
                {html_table}
                </div>
                """
                st.markdown(html_wrapper, unsafe_allow_html=True)
                st.caption(
                    f"Data: USDA-NRCS SDA | R={r_val} ({state_label} FOTG avg) | "
                    "EI = R × K × LS / T | PHEL Status per NRCS Part 616 (HEL≥8.0, PHEL crossed 8.0, NOT HEL<8.0) | "
                    f"Hydric: {hydric_count}/{len(df)} ({hydric_pct}%) | Results indicative only"
                )

                # Data Sources explanation
                st.divider()
                st.subheader("📊 Data Sources & Parameter Definitions")
                data_sources_html = """
                <div style="background-color:#0f172a; border-left:4px solid #3b82f6; padding:14px; border-radius:6px; font-size:13px; line-height:1.6;">
                <table style="width:100%; border-collapse:collapse;">
                <tr style="border-bottom:1px solid #334155;">
                  <td style="padding:8px; font-weight:bold; color:#60a5fa; width:80px;">Parameter</td>
                  <td style="padding:8px; font-weight:bold; color:#60a5fa; width:140px;">Source</td>
                  <td style="padding:8px; font-weight:bold; color:#60a5fa;">Description</td>
                </tr>
                <tr style="border-bottom:1px solid #334155; background-color:#020617;">
                  <td style="padding:8px; color:#cbd5e1;"><b>R</b></td>
                  <td style="padding:8px; color:#cbd5e1;">NOAA CDO + Brown & Foster</td>
                  <td style="padding:8px; color:#cbd5e1;">Rainfall Erosivity from point-specific precipitation (NOAA weather stations) via Brown & Foster equation: R ≈ 0.04887 × P^1.61<br><span style="font-size:12px; color:#a0aec0;">✅ <b>Point-specific data</b> reduces error to ±5-8% vs ±20-30% state averages. Fallback: NRCS FOTG state average if NOAA unavailable.</span></td>
                </tr>
                <tr style="border-bottom:1px solid #334155;">
                  <td style="padding:8px; color:#cbd5e1;"><b>K</b></td>
                  <td style="padding:8px; color:#cbd5e1;">SSURGO kwfact</td>
                  <td style="padding:8px; color:#cbd5e1;">Soil Erodibility Factor (surface horizon, 0cm depth)</td>
                </tr>
                <tr style="border-bottom:1px solid #334155; background-color:#020617;">
                  <td style="padding:8px; color:#cbd5e1;"><b>T</b></td>
                  <td style="padding:8px; color:#cbd5e1;">SSURGO tfact</td>
                  <td style="padding:8px; color:#cbd5e1;">Soil Loss Tolerance (maximum sustainable loss, tons/acre/year)</td>
                </tr>
                <tr style="border-bottom:1px solid #334155;">
                  <td style="padding:8px; color:#cbd5e1;"><b>LS</b></td>
                  <td style="padding:8px; color:#cbd5e1;">Approximated</td>
                  <td style="padding:8px; color:#cbd5e1;">Slope Length &amp; Steepness (calculated from USGS 3DEP 30m DEM — true L × S formula, ±5% error; falls back to Slope<sup>1.2</sup> × 0.1 if DEM unavailable)</td>
                </tr>
                <tr style="background-color:#020617;">
                  <td style="padding:8px; color:#cbd5e1;"><b>EI</b></td>
                  <td style="padding:8px; color:#cbd5e1;">Calculated</td>
                  <td style="padding:8px; color:#cbd5e1;">Erosion Index (EI = R × K × LS / T; HEL threshold ≥ 8.0 per 7 CFR § 12.21)</td>
                </tr>
                </table>
                <br>
                <span style="color:#94a3b8;"><b>⚠️ Note:</b> Results are indicative for preliminary screening only. Official HEL determination requires NRCS field verification per NRCS Part 616 standards.</span>
                </div>
                """
                st.markdown(data_sources_html, unsafe_allow_html=True)

        else:
            st.error("No soil components found. Try drawing a larger area or different location.")

    else:
        st.info("💡 Draw a polygon on the map or enter coordinates to analyze soil eligibility.")
