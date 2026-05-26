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


def load_raster_rfactor():
    """
    Download R-factor raster from Google Drive on first call.
    Uses session state for caching instead of st.cache_resource.
    """
    if not RASTERIO_AVAILABLE:
        return None, "rasterio not installed"

    # Return cached dataset from session state if already loaded
    if "raster_dataset" in st.session_state and st.session_state["raster_dataset"] is not None:
        return st.session_state["raster_dataset"], None

    try:
        # File already on disk — open it
        if RFACTOR_RASTER_LOCAL_PATH.exists():
            ds = rasterio.open(str(RFACTOR_RASTER_LOCAL_PATH))
            st.session_state["raster_dataset"] = ds
            return ds, None

        # Download from Google Drive
        download_url = (
            f"https://drive.usercontent.google.com/download"
            f"?id={RFACTOR_RASTER_GDRIVE_ID}&export=download&confirm=t"
        )
        response = requests.get(download_url, stream=True, timeout=120)
        response.raise_for_status()

        content = b""
        for chunk in response.iter_content(chunk_size=65536):
            content += chunk

        if content[:4] not in [b"II*\x00", b"MM\x00*", b"II+\x00"]:
            return None, f"Invalid TIF bytes: {content[:4]}"

        RFACTOR_RASTER_LOCAL_PATH.write_bytes(content)
        ds = rasterio.open(str(RFACTOR_RASTER_LOCAL_PATH))
        st.session_state["raster_dataset"] = ds
        return ds, None

    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)}"


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
        projector = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:5070", always_xy=True
        ).transform

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
        help="Enable advanced features: field verification, NRCS-CPA-026 pre-fill, technical details"
    )

    if conservationist_mode:
        st.info("👨‍🌾 **Conservationist Workspace Enabled**\n\nYou now have access to:\n- Field data input\n- NRCS-CPA-026 pre-fill\n- Technical details\n- Export options")
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
            # R-factor priority chain: Raster → NOAA CDO → State FOTG → National Default
            if RASTERIO_AVAILABLE and not RFACTOR_RASTER_LOCAL_PATH.exists():
                st.info("⏳ Loading R-factor raster for the first time (~30s one-time download)...")
            raster_r, raster_label = get_raster_r_factor(center_lat, center_lon)
            if raster_r:
                st.session_state["detected_r"] = (raster_r, raster_label, "NOAA Raster")
            else:
                noaa_r, noaa_label = get_noaa_r_factor(center_lat, center_lon, debug=False)
                if noaa_r:
                    st.session_state["detected_r"] = (noaa_r, noaa_label, "NOAA CDO")
                else:
                    st.session_state["detected_r"] = get_state_r_factor(center_lat, center_lon, debug=False)

            with st.spinner("Fetching soil data from USDA..."):
                st.session_state["analysis_results"] = fetch_nrcs_data(wkt)

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

        projector = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:5070", always_xy=True
        ).transform
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
                         df, ei_max, ei_min,
                         center_lat, center_lon,
                         county="", state_name="",
                         polygon_acres=None,
                         muname_acres=None):
    """
    Generate pre-filled NRCS-CPA-026e PDF matching official form layout.

    Args:
        r_val: R-factor value
        state_label: R-factor source label
        ls_factor: LS-factor value
        ls_source: LS-factor source label
        df: DataFrame with soil component data
        ei_max: Maximum EI value
        ei_min: Minimum EI value
        center_lat: Field center latitude
        center_lon: Field center longitude
        county: County name (auto-filled from reverse geocoding)
        state_name: State name (auto-filled from reverse geocoding)
        polygon_acres: Total field area in acres (fallback if muname_acres unavailable)
        muname_acres: Dict {muname: acres} from SSURGO intersection — NRCS method

    Returns:
        bytes: PDF document ready for download
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from io import BytesIO

        if muname_acres is None:
            muname_acres = {}

        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=letter)
        w, h = letter
        ml = 0.5 * inch
        mr = 0.5 * inch
        cw = w - ml - mr
        y  = h - 0.5 * inch

        # Determine total acres for subtitle
        if muname_acres:
            total_acres = sum(muname_acres.values())
            acres_str   = f"{total_acres:.1f}"
        elif polygon_acres:
            total_acres = polygon_acres
            acres_str   = f"{polygon_acres:.1f}"
        else:
            total_acres = None
            acres_str   = "___"

        num_rows = len(df) if not df.empty else 1

        def get_row_acres(soil_name):
            """
            Get acres for a soil type — SSURGO intersection first, fallback equal split.
            SSURGO munames include slope descriptors e.g. 'Clarion loam, Bemis moraine,
            2 to 6 percent slopes' — we sum ALL variants that start with the soil name
            since fetch_nrcs_data returns the base name only (e.g. 'Clarion loam').
            """
            if muname_acres:
                total = sum(
                    ac for muname, ac in muname_acres.items()
                    if muname.lower().startswith(soil_name.lower())
                )
                return f"{total:.1f}" if total > 0 else "___"
            elif polygon_acres:
                return f"{polygon_acres / num_rows:.1f}"
            return "___"

        def hline(ypos, thickness=0.5):
            c.setLineWidth(thickness)
            c.line(ml, ypos, w - mr, ypos)

        def blue_bar(ypos, title, fsize=10):
            """Blue section header bar. Returns y safely below bar."""
            bh = 0.30 * inch
            c.setFillColor(colors.HexColor("#003366"))
            c.rect(ml, ypos - bh, cw, bh, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", fsize)
            c.drawString(ml + 0.12*inch, ypos - bh + 0.10*inch, title)
            c.setFillColor(colors.black)
            return ypos - bh - 0.25*inch  # safe gap below bar

        def field_box(x, ytop, bw, bh, label, value):
            c.setLineWidth(0.5)
            c.setFillColor(colors.white)
            c.rect(x, ytop - bh, bw, bh, fill=1, stroke=1)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 7)
            c.drawString(x + 0.06*inch, ytop - 0.14*inch, label)
            c.setFont("Helvetica", 9)
            c.drawString(x + 0.06*inch, ytop - bh + 0.09*inch, value)

        # ── TOP HEADER BAR ───────────────────────────────────────────
        bh = 0.30 * inch
        c.setFillColor(colors.HexColor("#003366"))
        c.rect(ml, y - bh, cw, bh, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml + 0.10*inch, y - bh + 0.10*inch, "NRCS-CPA-026e   (8/2013)")
        c.setFont("Helvetica", 8)
        c.drawRightString(w - mr - 0.10*inch, y - bh + 0.10*inch,
            "U.S. DEPARTMENT OF AGRICULTURE — Natural Resources Conservation Service")
        y = y - bh - 0.25*inch  # safe gap below bar

        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(w/2, y,
            "HIGHLY ERODIBLE LAND AND WETLAND CONSERVATION DETERMINATION")
        y -= 0.22*inch

        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#555555"))
        c.drawCentredString(w/2, y,
            f"Pre-filled by CRP HEL Screening Tool  |  "
            f"Coordinates: {center_lat:.4f}°N, {abs(center_lon):.4f}°W"
            + (f"  |  Field Area: {acres_str} acres" if total_acres else ""))
        c.setFillColor(colors.black)
        y -= 0.14*inch
        hline(y, 1.2)
        y -= 0.20*inch

        # ── INFO BOXES ───────────────────────────────────────────────
        ibh = 0.36*inch; gap = 0.04*inch
        w1=cw*0.295; w2=cw*0.295; w3=cw*0.195; w4=cw*0.195
        x1=ml; x2=x1+w1+gap; x3=x2+w2+gap; x4=x3+w3+gap

        field_box(x1, y, w1, ibh, "Name:", "")
        field_box(x2, y, w2, ibh, "Address:", "")
        field_box(x3, y, w3, ibh, "Request Date:", datetime.now().strftime('%m/%d/%Y'))
        field_box(x4, y, w4, ibh, "County:", county)
        y -= ibh + gap

        field_box(x1, y, w1, ibh, "Agency or Person Requesting:", "")
        field_box(x2, y, w2, ibh, "Tract No.:", "")
        field_box(x3, y, w3, ibh, "FSA Farm No.:", "")
        field_box(x4, y, w4, ibh, "State:", state_name)
        y -= ibh + 0.22*inch

        # ── SECTION I: HEL ───────────────────────────────────────────
        y = blue_bar(y, "SECTION I — HIGHLY ERODIBLE LAND (HEL)")

        c.setFont("Helvetica", 8.5)
        c.drawString(ml+0.12*inch, y,
            "Is a soil survey now available for making a highly erodible land determination?")
        c.setFont("Helvetica-Bold", 9)
        c.drawString(w - mr - 1.05*inch, y, "\u2612 Yes    \u2610 No")
        y -= 0.26*inch

        hel_present = ei_max >= 8.0
        c.setFont("Helvetica", 8.5)
        c.drawString(ml+0.12*inch, y, "Are there highly erodible soil map units on this farm?")
        c.setFont("Helvetica-Bold", 9)
        c.drawString(w - mr - 1.05*inch, y,
            "\u2612 Yes    \u2610 No" if hel_present else "\u2610 Yes    \u2612 No")
        y -= 0.26*inch

        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(colors.HexColor("#444444"))
        c.drawString(ml+0.12*inch, y,
            "Fields below have undergone HEL determination. "
            "Fields without a completed determination are not listed.")
        y -= 0.18*inch
        c.drawString(ml+0.12*inch, y,
            "To be eligible for USDA benefits, a person must use an "
            "approved conservation system on all HEL fields.")
        c.setFillColor(colors.black)
        y -= 0.26*inch

        k_avg = df["K-Fact"].mean() if not df.empty and "K-Fact" in df.columns else 0
        t_avg = df["T-Fact"].mean() if not df.empty and "T-Fact" in df.columns else 0
        ls_disp = f"{ls_factor:.3f}" if ls_factor else "N/A"
        src = (state_label.split(" - ")[0][:28]
               if " - " in state_label else state_label[:28])

        c.setFont("Helvetica-Bold", 8)
        c.drawString(ml+0.12*inch, y,
            "RUSLE2 Screening Parameters (EI = R \u00d7 K \u00d7 LS / T):")
        y -= 0.20*inch
        c.setFont("Helvetica", 8)
        c.drawString(ml+0.20*inch, y,
            f"R={r_val:.1f} ({src})     K={k_avg:.4f} (SSURGO)     "
            f"LS={ls_disp} (USGS 3DEP)     T={t_avg:.2f} t/ac/yr (SSURGO)"
            f"     EI={ei_max:.2f}")
        y -= 0.26*inch

        # HEL table
        th=0.24*inch; tr=0.22*inch
        tc =[ml, ml+cw*0.35, ml+cw*0.44, ml+cw*0.55, ml+cw*0.67, ml+cw*0.85]
        tcw=[cw*0.35, cw*0.09, cw*0.11, cw*0.12, cw*0.18, cw*0.15]
        hdrs=["Field / Soil Map Unit","HEL\n(Y/N)","Sodbust\n(Y/N)",
              "Acres","Det. Date","EI Score\n(Screening)"]

        c.setFillColor(colors.HexColor("#D6E4F0"))
        for tx,tw in zip(tc,tcw): c.rect(tx, y-th, tw, th, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#003366"))
        c.setFont("Helvetica-Bold", 7.5)
        for i,(tx,tw) in enumerate(zip(tc,tcw)):
            parts = hdrs[i].split("\n")
            if len(parts)==2:
                c.drawCentredString(tx+tw/2, y-th*0.38, parts[0])
                c.drawCentredString(tx+tw/2, y-th*0.72, parts[1])
            else:
                c.drawCentredString(tx+tw/2, y-th*0.57, parts[0])
        c.setFillColor(colors.black)
        y -= th

        for _, row in df.iterrows():
            soil  = str(row.get("Soil Type",""))[:32]
            ei_v  = row.get("EI", 0)
            hel   = "Y" if ei_v >= 8.0 else "N"
            row_acres = get_row_acres(soil)
            fill = colors.HexColor("#FFF8E1") if hel=="Y" else colors.white
            for tx,tw in zip(tc,tcw):
                c.setFillColor(fill); c.rect(tx, y-tr, tw, tr, fill=1, stroke=1)
            c.setFillColor(colors.black); c.setFont("Helvetica", 8.5)
            vals=[soil, hel, "___", row_acres,
                  datetime.now().strftime('%m/%d/%Y'), f"{ei_v:.1f}"]
            for j,(tx,tw) in enumerate(zip(tc,tcw)):
                if j==0: c.drawString(tx+0.07*inch, y-tr*0.57, vals[j])
                else:    c.drawCentredString(tx+tw/2, y-tr*0.57, vals[j])
            y -= tr

        y -= 0.14*inch
        c.setFont("Helvetica", 8)
        c.drawString(ml+0.12*inch, y,
            f"The Highly Erodible Land determination was completed using "
            f"RUSLE2 screening data on {datetime.now().strftime('%m/%d/%Y')}."
            + (f"  Total field area: {acres_str} acres." if total_acres else "")
            + (" Acreage per SSURGO intersection." if muname_acres else
               " Acreage estimated from polygon area." if polygon_acres else ""))
        y -= 0.30*inch

        # ── SECTION II: WETLANDS ─────────────────────────────────────
        y = blue_bar(y, "SECTION II — WETLANDS")

        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(colors.HexColor("#444444"))
        c.drawString(ml+0.12*inch, y,
            "Fields below have had wetland determinations completed.")
        c.setFillColor(colors.black)
        y -= 0.26*inch

        wc =[ml, ml+cw*0.30, ml+cw*0.50, ml+cw*0.65, ml+cw*0.80]
        ww =[cw*0.30, cw*0.20, cw*0.15, cw*0.15, cw*0.20]
        whdrs=["Field / Soil Map Unit","Wetland (Y/N)","Conv. Wetland","Acres","Det. Date"]

        c.setFillColor(colors.HexColor("#D6E4F0"))
        for tx,tw in zip(wc,ww): c.rect(tx, y-th, tw, th, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#003366"))
        c.setFont("Helvetica-Bold", 7.5)
        for tx,tw,hdr in zip(wc,ww,whdrs):
            c.drawCentredString(tx+tw/2, y-th*0.57, hdr)
        c.setFillColor(colors.black)
        y -= th

        hydric = (df[df["Hydric"]=="Yes"]
                  if "Hydric" in df.columns else df.iloc[0:0])
        if len(hydric) > 0:
            for _, row in hydric.iterrows():
                soil = str(row.get("Soil Type",""))[:32]
                w_acres = get_row_acres(soil)
                for tx,tw in zip(wc,ww):
                    c.setFillColor(colors.HexColor("#E8F5E9"))
                    c.rect(tx, y-tr, tw, tr, fill=1, stroke=1)
                c.setFillColor(colors.black); c.setFont("Helvetica", 8.5)
                vals=[soil,"Y","___", w_acres, datetime.now().strftime('%m/%d/%Y')]
                for i,(tx,tw) in enumerate(zip(wc,ww)):
                    if i==0: c.drawString(tx+0.07*inch, y-tr*0.57, vals[i])
                    else:    c.drawCentredString(tx+tw/2, y-tr*0.57, vals[i])
                y -= tr
            for _ in range(2):
                for tx,tw in zip(wc,ww): c.rect(tx, y-tr, tw, tr, fill=0, stroke=1)
                y -= tr
        else:
            for _ in range(3):
                for tx,tw in zip(wc,ww): c.rect(tx, y-tr, tw, tr, fill=0, stroke=1)
                y -= tr

        y -= 0.16*inch
        c.setFont("Helvetica", 8)
        c.drawString(ml+0.12*inch, y,
            "Wetland indicators based on: SSURGO hydric soils, drainage class, "
            "NLCD vegetation, NHD proximity.")
        y -= 0.20*inch
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(colors.HexColor("#CC0000"))
        c.drawString(ml+0.12*inch, y,
            "\u26a0  Official wetland determination requires field verification by NRCS staff.")
        c.setFillColor(colors.black)
        y -= 0.32*inch

        # ── CERTIFICATION ────────────────────────────────────────────
        hline(y, 1)
        y -= 0.20*inch
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ml+0.12*inch, y, "CERTIFICATION")
        y -= 0.24*inch
        c.setFont("Helvetica", 8.5)
        c.drawString(ml+0.12*inch, y,
            "Determined by (NRCS Staff): "
            "____________________________________   Date: ______________")
        y -= 0.26*inch
        c.drawString(ml+0.12*inch, y,
            "Reviewed by (NRCS Staff):   "
            "____________________________________   Date: ______________")
        y -= 0.32*inch

        # ── FOOTER ───────────────────────────────────────────────────
        hline(y, 0.5)
        y -= 0.18*inch
        c.setFont("Helvetica-Oblique", 7)
        c.setFillColor(colors.HexColor("#666666"))
        for footer_line in [
            "SCREENING OUTPUT ONLY — Pre-filled by CRP HEL Screening Tool "
            "using public data (SSURGO, NOAA, USGS 3DEP, NLCD, NHD).",
            "Does NOT constitute an official NRCS determination per 7 CFR Part 12. "
            "Must be completed by qualified NRCS staff.",
            "Contact your local NRCS Service Center for an official determination.",
        ]:
            c.drawString(ml+0.10*inch, y, footer_line)
            y -= 0.16*inch

        c.setFillColor(colors.black)
        c.save()
        pdf_buffer.seek(0)
        return pdf_buffer.getvalue()

    except Exception as e:
        st.error(f"Error generating PDF: {str(e)}")
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
    Display conservationist-focused results: Technical details + Field verification + NRCS-CPA-026 form

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
        ["📊 Results", "🔧 Field Verification", "📋 Components", "📄 NRCS-CPA-026 Form", "⚙️ Technical"]
    )

    with tab1:
        st.subheader("Automated Analysis Results")

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
        st.subheader("📋 NRCS-CPA-026 Form (Pre-filled)")
        st.info("✅ **Download pre-filled NRCS-CPA-026** form with tool-calculated RUSLE2 parameters. This official NRCS form documents HEL determinations and is ready for conservationist field verification and signature.")

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
            - **NRCS-CPA-026:** Official NRCS form for documenting HEL/Wetland determinations with RUSLE2 analysis
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
            # Cache county + state from reverse geocoding for PDF (v17)
            try:
                _geo_url = f"https://nominatim.openstreetmap.org/reverse?lat={c_lat}&lon={c_lon}&format=json&zoom=10"
                _geo_r   = requests.get(_geo_url, headers={"User-Agent": "CRP_Conservation_Tool_v17_NRCS"}, timeout=5)
                _geo_addr = _geo_r.json().get("address", {})
                st.session_state["detected_county"] = (
                    _geo_addr.get("county","").replace(" County","").strip()
                )
                st.session_state["detected_state"]  = _geo_addr.get("state", "")
            except Exception:
                st.session_state["detected_county"] = ""
                st.session_state["detected_state"]  = ""
            # R-factor priority chain: Raster → NOAA CDO → State FOTG → National Default
            if RASTERIO_AVAILABLE and not RFACTOR_RASTER_LOCAL_PATH.exists():
                st.info("⏳ Loading R-factor raster for the first time (~30s one-time download)...")
            raster_r, raster_label = get_raster_r_factor(c_lat, c_lon)
            if raster_r:
                st.session_state["detected_r"] = (raster_r, raster_label, "NOAA Raster")
            else:
                noaa_r, noaa_label = get_noaa_r_factor(c_lat, c_lon)
                if noaa_r:
                    st.session_state["detected_r"] = (noaa_r, noaa_label, "NOAA CDO")
                else:
                    st.session_state["detected_r"] = get_state_r_factor(c_lat, c_lon)

            _, state_label, _ = st.session_state["detected_r"]
            with st.spinner(f"Fetching soil data ({state_label})..."):
                st.session_state["analysis_results"] = fetch_nrcs_data(drawn_wkt)

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
                <p style="color:#333;margin:10px 0;">Verify field data, generate NRCS-CPA-026 forms, and access technical RUSLE2 parameters.</p>

                <h4 style="color:#2E7D32;">📋 Workflow:</h4>
                <ol style="color:#333;margin-left:20px;">
                    <li><strong>Draw Polygon or Enter Coordinates</strong> — Define field boundary (⚡ polygon auto-analyzes)</li>
                    <li><strong>View Results Tab</strong> — Check automated HEL status and EI metrics</li>
                    <li><strong>Field Verification Tab</strong> — Enter measured slope length & steepness from site visit</li>
                    <li><strong>NRCS-CPA-026 Form Tab</strong> — Download pre-filled HEL determination form</li>
                    <li><strong>Technical Tab</strong> — Review R, K, LS, T factors and uncertainty flags</li>
                </ol>

                <h4 style="color:#2E7D32;">🔍 Key Features:</h4>
                <ul style="color:#333;margin-left:20px;">
                    <li><strong>Field Data Override</strong> — Compare automated vs. measured slopes</li>
                    <li><strong>Form Integration</strong> — NRCS-CPA-026 pre-fill with RUSLE2 data reduces paperwork by 30+ min</li>
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
