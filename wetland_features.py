#!/usr/bin/env python3
"""
Wetland Feature Detection for CRP HEL Tool
- Hydrophytic Vegetation: NLCD land cover classification
- Wetland Hydrology: SSURGO water table + NHD proximity
"""

import requests
import json
from typing import Optional, Tuple, Dict

# NLCD Wetland Classes — ONLY classes 90 and 95 are true wetland vegetation
# per USGS NLCD classification system
NLCD_WETLAND_CLASSES = {
    90: "Woody Wetlands",
    95: "Emergent Herbaceous Wetlands"
}

NLCD_AQUATIC_CLASSES = {
    11: "Open Water",
    12: "Perennial Snow/Ice"
}

# All NLCD classes for display label lookup
NLCD_ALL_CLASSES = {
    11: "Open Water", 12: "Perennial Snow/Ice",
    21: "Developed, Open Space", 22: "Developed, Low Intensity",
    23: "Developed, Medium Intensity", 24: "Developed, High Intensity",
    31: "Barren Land", 41: "Deciduous Forest", 42: "Evergreen Forest",
    43: "Mixed Forest", 52: "Shrub/Scrub", 71: "Grassland/Herbaceous",
    81: "Pasture/Hay", 82: "Cultivated Crops",
    90: "Woody Wetlands", 95: "Emergent Herbaceous Wetlands"
}


def get_nlcd_vegetation_type(lat: float, lon: float) -> Optional[Dict]:
    """
    Fetch NLCD land cover classification for a point.
    Identifies wetland vegetation types.
    Source: MRLC (Multi-Resolution Land Characteristics Consortium) WMS/WCS
    Endpoint: https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2019_Land_Cover_L48/wms

    Returns: {
        'nlcd_class': int,
        'class_name': str,
        'is_wetland_vegetation': bool,
        'vegetation_type': str (e.g., 'Herbaceous Wetland', 'Woody Wetland')
    }
    """
    try:
        # MRLC WMS GetFeatureInfo (confirmed working 2026-05)
        url = "https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2019_Land_Cover_L48/wms"

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetFeatureInfo",
            "LAYERS": "NLCD_2019_Land_Cover_L48",
            "QUERY_LAYERS": "NLCD_2019_Land_Cover_L48",
            "INFO_FORMAT": "application/json",
            "BBOX": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
            "SRS": "EPSG:4326",
            "WIDTH": 101,
            "HEIGHT": 101,
            "X": 50,
            "Y": 50
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "features" in data and len(data["features"]) > 0:
            pixel_value = int(data["features"][0]["properties"]["PALETTE_INDEX"])

            # Only classes 90 (Woody Wetlands) and 95 (Emergent Herbaceous Wetlands)
            # are true wetland vegetation per USGS NLCD classification
            is_wetland = pixel_value in NLCD_WETLAND_CLASSES
            class_label = NLCD_ALL_CLASSES.get(pixel_value, f"NLCD Class {pixel_value}")

            return {
                "nlcd_class": pixel_value,
                "class_name": class_label,
                "is_wetland_vegetation": is_wetland,
                "vegetation_type": NLCD_WETLAND_CLASSES[pixel_value] if is_wetland else "Non-wetland"
            }

        return None

    except Exception as e:
        print(f"⚠️ NLCD lookup failed: {e}")
        return None


def get_nhd_proximity(lat: float, lon: float, search_radius_km: float = 5.0) -> Optional[Dict]:
    """
    Check if a point falls within a mapped wetland using FWS National Wetlands
    Inventory (NWI) via WMS GetFeatureInfo — the authoritative US wetland dataset.

    Source: FWS NWI WMS (confirmed working 2026-05)
    Endpoint: https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/services/Wetlands/MapServer/WMSServer

    Returns Cowardin wetland code in ATTRIBUTE field (e.g. "PFO1A", "R2UBH").
    First letter = system: P=Palustrine, R=Riverine, E=Estuarine, L=Lacustrine, M=Marine

    Returns: {
        'has_nearby_water': bool,
        'wetland_type': str (NWI WETLAND_TYPE),
        'nwi_attribute': str (Cowardin code, e.g. 'PFO1A'),
        'hydrology_signal': str ('Strong', 'Possible', 'None')
    }
    """
    try:
        url = "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/services/Wetlands/MapServer/WMSServer"

        # bbox centred on the point (0.01 deg ≈ 1 km — large enough to hit NWI polygons)
        delta = 0.005
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetFeatureInfo",
            "LAYERS": "0",
            "QUERY_LAYERS": "0",
            # NWI WMS supports: application/geo+json, application/vnd.esri.wms_raw_xml,
            # text/xml, text/html, text/plain  (NOT application/json)
            "INFO_FORMAT": "application/geo+json",
            "BBOX": f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}",
            "SRS": "EPSG:4326",
            "WIDTH": 101,
            "HEIGHT": 101,
            "X": 50,
            "Y": 50
        }

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        features = data.get("features", [])

        if features:
            attrs = features[0].get("properties", {})
            nwi_attr    = attrs.get("ATTRIBUTE", "")
            wetland_type = attrs.get("WETLAND_TYPE", "")

            # Cowardin system: P=Palustrine, R=Riverine, L=Lacustrine → Strong
            #                  E=Estuarine, M=Marine → Possible
            signal = "Strong" if nwi_attr and nwi_attr[0].upper() in ("P", "R", "L") else "Possible"

            system_map = {
                "P": "Palustrine (freshwater)",
                "R": "Riverine",
                "L": "Lacustrine",
                "E": "Estuarine",
                "M": "Marine"
            }
            system_label = system_map.get(nwi_attr[0].upper() if nwi_attr else "", "Wetland")

            return {
                "has_nearby_water": True,
                "wetland_type": wetland_type or system_label,
                "nwi_attribute": nwi_attr,
                "hydrology_signal": signal
            }
        else:
            return {
                "has_nearby_water": False,
                "wetland_type": "None",
                "nwi_attribute": "",
                "hydrology_signal": "None"
            }

    except Exception as e:
        print(f"⚠️ NWI wetland check failed: {e}")
        return None


def detect_wetland_hydrology_from_ssurgo(watertab_depth_cm: Optional[float]) -> Dict:
    """
    Interpret SSURGO watertab field to determine wetland hydrology.

    Args:
        watertab_depth_cm: Water table depth from SSURGO in cm

    Returns: {
        'has_hydrology': bool,
        'watertab_depth_cm': float,
        'hydrology_signal': str ('Strong', 'Possible', 'None')
    }
    """
    if watertab_depth_cm is None:
        return {
            "has_hydrology": False,
            "watertab_depth_cm": None,
            "hydrology_signal": "Unknown"
        }

    # NRCS definition: wetland hydrology typically requires watertable within:
    # - 30 cm (strong signal) for poorly drained soils
    # - 30-60 cm (possible signal) for very poorly drained soils

    if watertab_depth_cm <= 30:
        signal = "Strong"
    elif watertab_depth_cm <= 60:
        signal = "Possible"
    else:
        signal = "None"

    return {
        "has_hydrology": signal in ["Strong", "Possible"],
        "watertab_depth_cm": watertab_depth_cm,
        "hydrology_signal": signal
    }


def combine_wetland_indicators(
    hydric_rating: Optional[str],
    drainage_class: Optional[str],
    vegetation: Optional[Dict],
    hydrology_ssurgo: Optional[Dict],
    hydrology_nhd: Optional[Dict]
) -> Dict:
    """
    Combine multiple wetland indicators into a comprehensive assessment per NRCS standards.

    NRCS Criteria (Federal Interagency Wetlands Delineation Manual):
    Wetland = Hydric Soils + (Wetland Hydrology OR Wetland Vegetation)

    PRIMARY indicators (used for determination):
      - hydric_soils: soil formed under prolonged saturation (SSURGO hydricrating)
      - wetland_vegetation: hydrophytic vegetation (NLCD)
      - hydrology_ssurgo: high water table (SSURGO comonth)
      - hydrology_nhd: proximity to water body (NHD)

    SUPPLEMENTARY indicators (supporting context, NOT counted toward determination):
      - poor_drainage: drainage class from SSURGO (derived from morphological features;
        useful screening signal but NOT a primary determining factor per NRCS Field
        Indicators of Hydric Soils guidance. Official determination uses soil color,
        mottling, gleying — already captured in hydricrating field.)

    Returns: {
        'is_likely_wetland': bool,
        'confidence': str ('High', 'Medium', 'Low'),
        'primary_indicators': {
            'hydric_soils': bool,
            'wetland_vegetation': bool,
            'hydrology_ssurgo': bool,
            'hydrology_nhd': bool
        },
        'supplementary': {
            'poor_drainage': bool,
            'drainage_class_label': str
        },
        'wetland_type': str,
        'recommendation': str
    }
    """

    # --- Primary indicators (count toward wetland determination) ---
    indicators = {
        "hydric_soils": hydric_rating and ("hydric" in str(hydric_rating).lower() or "yes" in str(hydric_rating).lower()),
        "wetland_vegetation": vegetation and vegetation.get("is_wetland_vegetation", False),
        "hydrology_ssurgo": hydrology_ssurgo and hydrology_ssurgo.get("hydrology_signal") in ["Strong", "Possible"],
        "hydrology_nhd": hydrology_nhd and hydrology_nhd.get("hydrology_signal") in ["Strong", "Possible"]
    }

    # --- Supplementary indicator (informational only, NOT counted) ---
    is_poor_drainage = False
    if drainage_class:
        drainage_lower = str(drainage_class).lower()
        is_poor_drainage = any(kw in drainage_lower for kw in ["poorly", "somewhat poor", "very poor"])
    supplementary = {
        "poor_drainage": is_poor_drainage,
        "drainage_class_label": drainage_class or ""
    }

    # Count ONLY primary indicators toward confidence
    positive_count = sum(1 for v in indicators.values() if v)

    # Determine confidence based on evidence convergence
    # Per NRCS: Need hydric soils + (hydrology OR vegetation)
    if positive_count >= 3:
        confidence = "High"
        is_likely_wetland = True
    elif positive_count >= 2:
        confidence = "Medium"
        is_likely_wetland = True
    elif positive_count >= 1:
        confidence = "Low"
        is_likely_wetland = True
    else:
        confidence = "Low"
        is_likely_wetland = False

    # Determine wetland type
    if indicators["wetland_vegetation"]:
        if "Herbaceous" in (vegetation.get("vegetation_type") if vegetation else ""):
            wetland_type = "Herbaceous Wetland"
        elif "Woody" in (vegetation.get("vegetation_type") if vegetation else ""):
            wetland_type = "Woody Wetland"
        else:
            wetland_type = "Wetland (type unknown)"
    elif indicators["hydric_soils"] and indicators["hydrology_nhd"]:
        wetland_type = "Hydric Soil — Wetland Present (NWI)"
    elif indicators["hydric_soils"] and indicators["hydrology_ssurgo"]:
        wetland_type = "Hydric Soil — High Water Table"
    elif indicators["hydric_soils"]:
        # Hydric soils only — likely drained/converted (e.g. prairie pothole)
        wetland_type = "Hydric Soil — Potential Restoration Site"
    else:
        wetland_type = "No wetland indicators detected"

    recommendation = ""
    if is_likely_wetland:
        if confidence == "High":
            recommendation = "Strong candidate for wetland restoration (CP23, CP28)"
        elif confidence == "Medium":
            recommendation = "Possible wetland restoration opportunity - field verification recommended"
        else:
            recommendation = "Potential wetland - further investigation needed"
    else:
        recommendation = "Does not appear to be wetland-forming soil"

    return {
        "is_likely_wetland": is_likely_wetland,
        "confidence": confidence,
        "indicators": indicators,
        "supplementary": supplementary,
        "wetland_type": wetland_type,
        "recommendation": recommendation
    }
