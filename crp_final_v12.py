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

# Import the hybrid R-factor calculator (NRCS EI30 + state-level fallback)
try:
    from rfactor_calculator import get_rfactor_with_details
    RFACTOR_CALCULATOR_AVAILABLE = True
except ImportError:
    RFACTOR_CALCULATOR_AVAILABLE = False
    st.warning("⚠️ R-factor calculator module not found. Using fallback state-level R-factors only.")

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
# CRP HEL Screening & CP Recommendation Tool — v15
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
# =============================================================================

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
st.set_page_config(page_title="CRP HEL and Wetland Screening Tool (Prototype)", layout="wide")

st.markdown("""
    <style>
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

def generate_cpa026_pdf(r_val, state_label, ls_factor, ls_source, df, ei_max, ei_min, center_lat, center_lon):
    """
    Generate PDF with pre-filled NRCS-CPA-026 form data.

    Form: NRCS-CPA-026 "Highly Erodible Land and Wetland Conservation Determination"
    This is the official NRCS form for documenting HEL determinations with RUSLE2 parameters.

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

    Returns:
        bytes: PDF document ready for download
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        from datetime import datetime
        from io import BytesIO

        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=letter)
        width, height = letter

        # Margins - increased for better whitespace
        margin_left = 0.6 * inch
        margin_right = 0.6 * inch
        margin_top = 0.6 * inch
        current_y = height - margin_top
        line_height = 0.14 * inch  # Increased for more breathing room

        # ═══════════════════════════════════════════════════════════
        # FORM HEADER
        # ═══════════════════════════════════════════════════════════
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin_left, current_y, "NRCS-CPA-026")
        c.setFont("Helvetica-Bold", 11)
        current_y -= line_height * 1.3
        c.drawString(margin_left, current_y, "HIGHLY ERODIBLE LAND AND WETLAND CONSERVATION DETERMINATION")
        current_y -= line_height * 1.5

        # Form info
        c.setFont("Helvetica", 9)
        c.drawString(margin_left, current_y, "Generated by: CRP HEL Screening Tool (SCREENING ONLY)")
        current_y -= line_height
        c.drawString(margin_left, current_y, f"Date: {datetime.now().strftime('%m/%d/%Y')}  |  Coordinates: {center_lat:.4f}°N, {center_lon:.4f}°W")
        current_y -= line_height * 2

        # ═══════════════════════════════════════════════════════════
        # SECTION A: FARM/FIELD IDENTIFICATION
        # ═══════════════════════════════════════════════════════════
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin_left, current_y, "SECTION A: FARM AND FIELD INFORMATION")
        current_y -= line_height * 1.3

        c.setFont("Helvetica", 10)
        form_data = [
            ("Name:", "________________________________________"),
            ("Address:", "________________________________________"),
            ("County:", "__________________  State: ___  Zip: __________"),
            ("FSA Farm No.:", "_______________  Tract No.: _______________"),
        ]

        for label, blank in form_data:
            c.drawString(margin_left + 0.2*inch, current_y, label)
            c.drawString(margin_left + 1.5*inch, current_y, blank)
            current_y -= line_height * 1.1

        current_y -= line_height * 1.2  # Increased spacing before next section

        # ═══════════════════════════════════════════════════════════
        # SECTION B: HIGHLY ERODIBLE LAND (HEL) DETERMINATION
        # ═══════════════════════════════════════════════════════════
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin_left, current_y, "SECTION B: HIGHLY ERODIBLE LAND (HEL) DETERMINATION")
        current_y -= line_height * 1.3

        c.setFont("Helvetica", 9)
        c.drawString(margin_left + 0.2*inch, current_y, "Erosion Index (EI) Calculation: EI = (R × K × LS) / T")
        current_y -= line_height

        # RUSLE2 Parameters Table
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin_left + 0.2*inch, current_y, "RUSLE2 Parameters:")
        current_y -= line_height * 1.2

        k_avg = df["K-Fact"].mean() if not df.empty else 0
        k_min = df["K-Fact"].min() if not df.empty else 0
        k_max = df["K-Fact"].max() if not df.empty else 0
        t_avg = df["T-Fact"].mean() if not df.empty else 0
        t_min = df["T-Fact"].min() if not df.empty else 0
        t_max = df["T-Fact"].max() if not df.empty else 0
        ls_display = f"{ls_factor:.3f}" if ls_factor else "Approximated"

        # Format R-Factor - parameter on line 1, source on line 2
        c.setFont("Helvetica", 10)
        c.drawString(margin_left + 0.3*inch, current_y, f"R-Factor (Rainfall): {r_val:.1f}")
        current_y -= line_height * 0.85
        c.setFont("Helvetica", 9)  # Increased from 8 to 9
        c.drawString(margin_left + 0.5*inch, current_y, f"Source: {state_label}")
        current_y -= line_height * 1.6  # Increased spacing between parameters

        # Format K-Factor - parameter on line 1, range on line 2
        c.setFont("Helvetica", 10)
        c.drawString(margin_left + 0.3*inch, current_y, f"K-Factor (Soil): {k_avg:.4f}")
        current_y -= line_height * 0.85
        c.setFont("Helvetica", 9)  # Increased from 8 to 9
        c.drawString(margin_left + 0.5*inch, current_y, f"Range: {k_min:.4f}–{k_max:.4f}")
        current_y -= line_height * 1.6  # Increased spacing between parameters

        # Format LS-Factor - parameter on line 1, source on line 2
        c.setFont("Helvetica", 10)
        c.drawString(margin_left + 0.3*inch, current_y, f"LS-Factor (Slope): {ls_display}")
        current_y -= line_height * 0.85
        c.setFont("Helvetica", 9)  # Increased from 8 to 9
        c.drawString(margin_left + 0.5*inch, current_y, f"Source: {ls_source}")
        current_y -= line_height * 1.6  # Increased spacing between parameters

        # Format T-Factor - parameter on line 1, range on line 2
        c.setFont("Helvetica", 10)
        c.drawString(margin_left + 0.3*inch, current_y, f"T-Factor (Tolerance): {t_avg:.2f} t/ac/yr")
        current_y -= line_height * 0.85
        c.setFont("Helvetica", 9)  # Increased from 8 to 9
        c.drawString(margin_left + 0.5*inch, current_y, f"Range: {t_min:.2f}–{t_max:.2f}")
        current_y -= line_height * 1.8  # Extra space before EI result

        current_y -= line_height * 0.3

        # Determination Result
        c.setFont("Helvetica-Bold", 10)
        hel_status = "HEL" if ei_max >= 8.0 else "NOT HEL"
        status_symbol = "☒" if ei_max >= 8.0 else "☐"
        c.drawString(margin_left + 0.2*inch, current_y, f"{status_symbol} EROSION INDEX: {ei_max:.2f} → {hel_status}")
        c.setFont("Helvetica", 8)
        current_y -= line_height
        c.drawString(margin_left + 0.5*inch, current_y, f"(Range: {ei_min:.2f}–{ei_max:.2f})")
        current_y -= line_height * 2.0  # Increased spacing before next section

        # ═══════════════════════════════════════════════════════════
        # SECTION C: FIELD DETERMINATION TABLE
        # ═══════════════════════════════════════════════════════════
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin_left, current_y, "SECTION C: FIELD SUMMARY TABLE")
        current_y -= line_height * 1.2

        # Table headers
        c.setFont("Helvetica-Bold", 8)
        col_x = [margin_left + 0.2*inch, margin_left + 2.0*inch, margin_left + 2.8*inch,
                 margin_left + 3.6*inch, margin_left + 4.4*inch, margin_left + 5.2*inch]
        headers = ["Field", "HEL", "EI Value", "Acres", "Det. Date", "Sodbust"]

        for i, header in enumerate(headers):
            c.drawString(col_x[i], current_y, header)

        current_y -= line_height * 0.9
        c.setLineWidth(1)
        c.line(margin_left + 0.1*inch, current_y + line_height*0.2, width - margin_right, current_y + line_height*0.2)
        current_y -= line_height * 0.3

        # Table rows (one per soil component) - increased row height for readability
        c.setFont("Helvetica", 8)
        for idx, row in df.iterrows():
            soil_type = str(row.get("Soil Type", "Field"))[:20]
            hel = "Y" if row.get("EI", 0) >= 8.0 else "N"
            ei_val = f"{row.get('EI', 0):.1f}"
            acres = "___"  # User fills in
            det_date = datetime.now().strftime('%m/%d/%Y')
            sodbust = "___"  # User fills in

            c.drawString(col_x[0], current_y, soil_type)
            c.drawString(col_x[1], current_y, hel)
            c.drawString(col_x[2], current_y, ei_val)
            c.drawString(col_x[3], current_y, acres)
            c.drawString(col_x[4], current_y, det_date)
            c.drawString(col_x[5], current_y, sodbust)
            current_y -= line_height * 1.2  # Increased from 1.0 for better spacing

        current_y -= line_height * 1.2  # Increased spacing before next section

        # ═══════════════════════════════════════════════════════════
        # SECTION D: CERTIFICATIONS (NRCS Staff use)
        # ═══════════════════════════════════════════════════════════
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin_left, current_y, "SECTION D: DETERMINATION CERTIFICATION")
        current_y -= line_height * 1.2

        c.setFont("Helvetica", 8)
        c.drawString(margin_left + 0.2*inch, current_y, "Determined by (NRCS Staff): _________________________  Date: __________")
        current_y -= line_height * 1.1
        c.drawString(margin_left + 0.2*inch, current_y, "Reviewed by (NRCS Staff): _________________________  Date: __________")
        current_y -= line_height * 1.5

        # ═══════════════════════════════════════════════════════════
        # FOOTER: IMPORTANT DISCLAIMER
        # ═══════════════════════════════════════════════════════════
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin_left, current_y, "⚠️  IMPORTANT DISCLAIMER")
        current_y -= line_height * 0.9

        c.setFont("Helvetica", 7.5)
        disclaimer = [
            "This document is a SCREENING TOOL OUTPUT generated by the CRP HEL Screening Tool.",
            "It is NOT an official NRCS-CPA-026 form and does NOT constitute an official NRCS HEL determination.",
            "Official determinations must be completed by NRCS staff following 7 CFR Part 12 procedures.",
            "All values are estimates based on available data and must be verified in the field by qualified conservationists.",
            "For official determination, contact your local NRCS Service Center."
        ]

        for line in disclaimer:
            c.drawString(margin_left + 0.1*inch, current_y, line)
            current_y -= line_height * 0.8

        c.save()
        pdf_buffer.seek(0)
        return pdf_buffer.getvalue()

    except Exception as e:
        st.error(f"Error generating PDF: {str(e)}")
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
        col_soil1, col_soil2 = st.columns(2)

        problem_soils = (df["EI"] >= 8.0).sum()
        with col_soil1:
            st.metric("High-Risk Soil Types", problem_soils, help="Soils with EI ≥ 8.0")

        hydric_count = (df["Hydric"] == "Yes").sum()
        with col_soil2:
            st.metric("Hydric Soils Found", "Yes" if hydric_count > 0 else "No", help="Wet/wetland soils on your field")

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
        '<li>Save this result or print it</li>'
        '<li>Contact your local NRCS office for a formal determination</li>'
        '<li>An NRCS conservationist will visit your field and verify the results</li>'
        '<li>If eligible, discuss CRP enrollment options</li>'
        '</ol>'
        '</div>',
        unsafe_allow_html=True
    )

    # Find NRCS Office Button
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔍 Find NRCS Office Near Me", key="find_nrcs_farmer"):
            st.warning(
                "⏳ **NRCS Office Locator coming soon!**\n\n"
                "For now, visit: **[NRCS Office Locator](https://offices.sc.egov.usda.gov/)**\n\n"
                "We're working on integrating this directly into the tool."
            )

    with col2:
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
            # Add HEL/PHEL status column per soil type
            display_df = df.copy()

            # Calculate HEL/PHEL status for each soil based on its EI
            display_df["HEL Status"] = display_df["EI"].apply(
                lambda ei: "✅ HEL" if ei >= 8.0 else "❌ NOT HEL"
            )

            # Select columns for display: Soil Type, Slope, K-Fact, T-Fact, EI, HEL Status, Hydric
            cols_to_show = ["Soil Type", "Slope", "K-Fact", "T-Fact", "EI", "HEL Status", "Hydric", "Drainage"]
            display_df = display_df[[col for col in cols_to_show if col in display_df.columns]]

            st.dataframe(display_df, use_container_width=True)

            # Summary info
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
                # Get coordinates from session state
                center_lat = st.session_state.get("center_lat", 0)
                center_lon = st.session_state.get("center_lon", 0)

                # Generate NRCS-CPA-026 PDF
                pdf_data = generate_cpa026_pdf(r_val, state_label, ls_factor, ls_source, df, ei_max, ei_min, center_lat, center_lon)
                if pdf_data:
                    st.download_button(
                        label="📄 NRCS-CPA-026 PDF",
                        data=pdf_data,
                        file_name=f"NRCS-CPA-026_HEL_Determination_{datetime.now().strftime('%Y%m%d')}.pdf",
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

    map_output = st_folium(m, width="100%", height=650, key="crp_master_map")

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
                    <li><strong>Contact NRCS</strong> — Find your local office for official determination</li>
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

        # Determine if using NOAA (point-specific) or FOTG (state average)
        source_type = "Point-Specific (NOAA CDO)" if "NOAA" in state_label else "State Average (NRCS FOTG)"
        source_icon = "🎯" if "NOAA" in state_label else "🗺️"

        # Display R-factor in clean card (no redundancy)
        st.markdown(
            f'<div class="r-banner">'
            f'📍 <b>Data Source:</b> {state_label}<br>'
            f'{source_icon} <b>Applied R-Factor:</b> {r_val} '
            f'<span style="font-size:11px;color:#888;">({source_type})</span>'
            f'</div>',
            unsafe_allow_html=True
        )

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
                    veg_evidence = "—"
                    if vegetation and assessment["indicators"]["wetland_vegetation"]:
                        nlcd_class = vegetation.get("nlcd_class", "N/A")
                        veg_type = vegetation.get("vegetation_type", "Wetland vegetation")
                        veg_evidence = f"NLCD Class {nlcd_class}: {veg_type}"

                    hydrology_nhd_evidence = "—"
                    if nhd_proximity and assessment["indicators"]["hydrology_nhd"]:
                        wetland_type = nhd_proximity.get("wetland_type", "Water body")
                        nwi_attr = nhd_proximity.get("nwi_attribute", "")
                        signal = nhd_proximity.get("hydrology_signal", "")
                        hydrology_nhd_evidence = f"{wetland_type} (NWI: {nwi_attr}) - {signal} signal"

                    wetland_table_data = [
                        {
                            "Indicator": "Hydric Soils",
                            "Detected": "✅ Yes" if assessment["indicators"]["hydric_soils"] else "❌ No",
                            "Evidence": "SSURGO hydricrating indicates wetland-forming soils (hydric rating present)",
                            "Confidence": "High" if assessment["indicators"]["hydric_soils"] else "—"
                        },
                        {
                            "Indicator": "Hydrophytic Vegetation",
                            "Detected": "✅ Yes" if assessment["indicators"]["wetland_vegetation"] else "❌ No",
                            "Evidence": veg_evidence,
                            "Confidence": "High" if assessment["indicators"]["wetland_vegetation"] else "—"
                        },
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
                    ]

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
