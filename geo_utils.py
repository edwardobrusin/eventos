"""
Utilidades geográficas, buscador/parser de Google Maps, calibración de polígonos estatales,
formato estricto de fechas (DD/MM/AAAA) y generador de invitaciones de Google Calendar (.ics).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests

# Reloj institucional conectado en tiempo real al huso horario del Centro de México (America/Mexico_City)
def get_mexico_today() -> dt.date:
    """Devuelve la fecha actual real en huso horario del Centro de México (America/Mexico_City, UTC-6)."""
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("America/Mexico_City")).date()
    except Exception:
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=-6))).date()


SYSTEM_TODAY = get_mexico_today()

# Catálogo oficial de las 32 Entidades Federativas sincronizado con data/mexico_states.geojson
MEXICO_STATES: Dict[str, Dict[str, Any]] = {
    "Aguascalientes": {"id": "MX-AGU", "lat": 21.8853, "lon": -102.2916, "capital": "Aguascalientes"},
    "Baja California": {"id": "MX-BCN", "lat": 32.5149, "lon": -117.0382, "capital": "Mexicali"},
    "Baja California Sur": {"id": "MX-BCS", "lat": 24.1426, "lon": -110.3128, "capital": "La Paz"},
    "Campeche": {"id": "MX-CAM", "lat": 19.8301, "lon": -90.5349, "capital": "Campeche"},
    "Chiapas": {"id": "MX-CHP", "lat": 16.7569, "lon": -93.1292, "capital": "Tuxtla Gutiérrez"},
    "Chihuahua": {"id": "MX-CHH", "lat": 28.6330, "lon": -106.0691, "capital": "Chihuahua"},
    "Ciudad de México": {"id": "MX-CMX", "lat": 19.3600, "lon": -99.1332, "capital": "Ciudad de México"},
    "Coahuila": {"id": "MX-COA", "lat": 25.4232, "lon": -101.0053, "capital": "Saltillo"},
    "Colima": {"id": "MX-COL", "lat": 19.2452, "lon": -103.7241, "capital": "Colima"},
    "Durango": {"id": "MX-DUR", "lat": 24.0277, "lon": -104.6532, "capital": "Durango"},
    "Guanajuato": {"id": "MX-GUA", "lat": 21.0190, "lon": -101.2574, "capital": "Guanajuato"},
    "Guerrero": {"id": "MX-GRO", "lat": 17.5506, "lon": -99.5024, "capital": "Chilpancingo"},
    "Hidalgo": {"id": "MX-HID", "lat": 20.1011, "lon": -98.7591, "capital": "Pachuca"},
    "Jalisco": {"id": "MX-JAL", "lat": 20.6597, "lon": -103.3496, "capital": "Guadalajara"},
    "México": {"id": "MX-MEX", "lat": 19.2826, "lon": -99.6557, "capital": "Toluca"},
    "Michoacán": {"id": "MX-MIC", "lat": 19.7060, "lon": -101.1950, "capital": "Morelia"},
    "Morelos": {"id": "MX-MOR", "lat": 18.9242, "lon": -99.2216, "capital": "Cuernavaca"},
    "Nayarit": {"id": "MX-NAY", "lat": 21.5041, "lon": -104.8946, "capital": "Tepic"},
    "Nuevo León": {"id": "MX-NLE", "lat": 25.6866, "lon": -100.3161, "capital": "Monterrey"},
    "Oaxaca": {"id": "MX-OAX", "lat": 17.0732, "lon": -96.7266, "capital": "Oaxaca"},
    "Puebla": {"id": "MX-PUE", "lat": 19.0414, "lon": -98.2063, "capital": "Puebla"},
    "Querétaro": {"id": "MX-QUE", "lat": 20.5888, "lon": -100.3899, "capital": "Querétaro"},
    "Quintana Roo": {"id": "MX-ROO", "lat": 21.1619, "lon": -86.8515, "capital": "Chetumal"},
    "San Luis Potosí": {"id": "MX-SLP", "lat": 22.1565, "lon": -100.9855, "capital": "San Luis Potosí"},
    "Sinaloa": {"id": "MX-SIN", "lat": 24.8091, "lon": -107.3940, "capital": "Culiacán"},
    "Sonora": {"id": "MX-SON", "lat": 29.0729, "lon": -110.9559, "capital": "Hermosillo"},
    "Tabasco": {"id": "MX-TAB", "lat": 17.9895, "lon": -92.9475, "capital": "Villahermosa"},
    "Tamaulipas": {"id": "MX-TAM", "lat": 23.7369, "lon": -99.1411, "capital": "Ciudad Victoria"},
    "Tlaxcala": {"id": "MX-TLA", "lat": 19.3139, "lon": -98.2404, "capital": "Tlaxcala"},
    "Veracruz": {"id": "MX-VER", "lat": 19.1738, "lon": -96.1342, "capital": "Xalapa"},
    "Yucatán": {"id": "MX-YUC", "lat": 20.9674, "lon": -89.5926, "capital": "Mérida"},
    "Zacatecas": {"id": "MX-ZAC", "lat": 22.7709, "lon": -102.5832, "capital": "Zacatecas"},
}

STATE_ALIASES: Dict[str, str] = {
    "cdmx": "Ciudad de México",
    "ciudad de mexico": "Ciudad de México",
    "distrito federal": "Ciudad de México",
    "df": "Ciudad de México",
    "mexico city": "Ciudad de México",
    "estado de mexico": "México",
    "edomex": "México",
    "mexico": "México",
    "nuevo leon": "Nuevo León",
    "nl": "Nuevo León",
    "queretaro": "Querétaro",
    "queretaro de arteaga": "Querétaro",
    "san luis potosi": "San Luis Potosí",
    "slp": "San Luis Potosí",
    "michoacan": "Michoacán",
    "michoacan de ocampo": "Michoacán",
    "yucatan": "Yucatán",
    "veracruz de ignacio de la llave": "Veracruz",
    "coahuila de zaragoza": "Coahuila",
    "baja california norte": "Baja California",
    "bc": "Baja California",
    "bcs": "Baja California Sur",
    "qroo": "Quintana Roo",
    "quintana roo": "Quintana Roo",
    "ags": "Aguascalientes",
    "gto": "Guanajuato",
    "chih": "Chihuahua",
    "jal": "Jalisco",
}

KNOWN_VENUES: List[Dict[str, Any]] = [
    {
        "keywords": ["presidente intercontinental monterrey", "san pedro garza", "comce", "real san agustin"],
        "sede": "Hotel Presidente InterContinental Monterrey",
        "direccion": "Av. Real San Agustín 301, Zona San Agustín, 66260 San Pedro Garza García, N.L.",
        "estado": "Nuevo León",
        "municipio": "San Pedro Garza García",
        "codigo_postal": "66260",
        "latitud": 25.6488,
        "longitud": -100.3382,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Hotel+Presidente+InterContinental+Monterrey+San+Pedro+Garza+Garcia",
    },
    {
        "keywords": ["paradisus cancun", "world meetings forum", "wmf cancun", "kukulcan"],
        "sede": "Paradisus Cancún Convention Center",
        "direccion": "Blvd. Kukulcán Km 16.5, Zona Hotelera, 77500 Cancún, Q.R.",
        "estado": "Quintana Roo",
        "municipio": "Benito Juárez (Cancún)",
        "codigo_postal": "77500",
        "latitud": 21.0748,
        "longitud": -86.7764,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Paradisus+Cancun+Blvd+Kukulcan+Km+16.5",
    },
    {
        "keywords": ["poliforum leon", "wire tech", "wiretech", "arnes electrico"],
        "sede": "Poliforum León",
        "direccion": "Blvd. Adolfo López Mateos Esq. Blvd. Francisco Villa s/n, Oriental, 37510 León, Gto.",
        "estado": "Guanajuato",
        "municipio": "León",
        "codigo_postal": "37510",
        "latitud": 21.1165,
        "longitud": -101.6525,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Poliforum+Leon+Guanajuato",
    },
    {
        "keywords": ["expo chihuahua", "nearshoring summit chihuahua"],
        "sede": "Centro de Exposiciones y Convenciones Expo Chihuahua",
        "direccion": "Av. Tecnológico 1900, Santo Niño, 31200 Chihuahua, Chih.",
        "estado": "Chihuahua",
        "municipio": "Chihuahua",
        "codigo_postal": "31200",
        "latitud": 28.6531,
        "longitud": -106.0853,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+Chihuahua+Centro+de+Convenciones",
    },
    {
        "keywords": ["agroalimentaria", "irapuato", "abasolo"],
        "sede": "Recinto Expo AgroAlimentaria Guanajuato",
        "direccion": "Carretera Irapuato - Abasolo Km 6.5, 36557 Irapuato, Gto.",
        "estado": "Guanajuato",
        "municipio": "Irapuato",
        "codigo_postal": "36557",
        "latitud": 20.6311,
        "longitud": -101.4119,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+AgroAlimentaria+Guanajuato+Irapuato",
    },
    {
        "keywords": ["centro citibanamex", "citibanamex", "plastimagen", "conscripto"],
        "sede": "Centro Citibanamex",
        "direccion": "Av. del Conscripto 311, Lomas de Sotelo, Hipódromo de las Américas, Miguel Hidalgo, 11200 CDMX",
        "estado": "Ciudad de México",
        "municipio": "Miguel Hidalgo",
        "codigo_postal": "11200",
        "latitud": 19.4280,
        "longitud": -99.1480,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+Citibanamex+CDMX",
    },
    {
        "keywords": ["expo guadalajara", "smart technology expo", "mariano otero"],
        "sede": "Expo Guadalajara",
        "direccion": "Av. Mariano Otero 1499, Verde Valle, 44550 Guadalajara, Jal.",
        "estado": "Jalisco",
        "municipio": "Guadalajara",
        "codigo_postal": "44550",
        "latitud": 20.6534,
        "longitud": -103.3913,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+Guadalajara+Mariano+Otero",
    },
    {
        "keywords": ["centro de negocios potosi", "cnp", "automotive connection potosi", "convenciones san luis"],
        "sede": "Centro de Negocios Potosí (CNP)",
        "direccion": "Blvd. Antonio Rocha Cordero 125, Desarrollo del Pedregal, 78395 San Luis Potosí, S.L.P.",
        "estado": "San Luis Potosí",
        "municipio": "San Luis Potosí",
        "codigo_postal": "78395",
        "latitud": 22.1194,
        "longitud": -101.0028,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+de+Convenciones+de+San+Luis+Potosi",
    },
    {
        "keywords": ["ficotrece", "aguascalientes", "automotive b2b meetings"],
        "sede": "Complejo Tres Centurias (Ficotrece)",
        "direccion": "Av. Manuel Gómez Morín s/n, Barrio de la Estación, 20259 Aguascalientes, Ags.",
        "estado": "Aguascalientes",
        "municipio": "Aguascalientes",
        "codigo_postal": "20259",
        "latitud": 21.8891,
        "longitud": -102.2798,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Complejo+Tres+Centurias+Ficotrece+Aguascalientes",
    },
    {
        "keywords": ["cintermex", "aiag", "parque fundidora"],
        "sede": "CINTERMEX Monterrey",
        "direccion": "Av. Fundidora 501, Col. Obrera, 64010 Monterrey, N.L.",
        "estado": "Nuevo León",
        "municipio": "Monterrey",
        "codigo_postal": "64010",
        "latitud": 25.6782,
        "longitud": -100.2877,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=CINTERMEX+Monterrey+Nuevo+Leon",
    },
    {
        "keywords": ["club de industriales de queretaro", "surface finishing", "queretaro convention"],
        "sede": "Club de Industriales de Querétaro (CIQ)",
        "direccion": "Av. Estadio 112, Colinas del Cimatario, 76090 Santiago de Querétaro, Qro.",
        "estado": "Querétaro",
        "municipio": "Querétaro",
        "codigo_postal": "76090",
        "latitud": 20.5694,
        "longitud": -100.3712,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Club+de+Industriales+de+Queretaro",
    },
    {
        "keywords": ["quartz hotel", "dispositivos medicos", "tijuana", "baja medical"],
        "sede": "Quartz Hotel & Spa — Zona Río Tijuana",
        "direccion": "Paseo del Centenario 9580, Zona Urbana Río Tijuana, 22010 Tijuana, B.C.",
        "estado": "Baja California",
        "municipio": "Tijuana",
        "codigo_postal": "22010",
        "latitud": 32.5225,
        "longitud": -117.0189,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Quartz+Hotel+Zona+Rio+Tijuana",
    },
    {
        "keywords": ["wtc", "world trade center cdmx", "pepsico", "napoles"],
        "sede": "World Trade Center Ciudad de México",
        "direccion": "Montecito 38, Nápoles, Benito Juárez, 03810 Ciudad de México, CDMX",
        "estado": "Ciudad de México",
        "municipio": "Benito Juárez",
        "codigo_postal": "03810",
        "latitud": 19.3946,
        "longitud": -99.1643,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=World+Trade+Center+Ciudad+de+Mexico",
    },
    {
        "keywords": ["autodromo", "hermanos rodriguez", "gran premio", "formula 1", "f1", "magdalena mixhuca", "estadio gnp", "viaducto rio de la piedad"],
        "sede": "Autódromo Hermanos Rodríguez",
        "direccion": "Av. Viaducto Río de la Piedad S/N, Granjas México, Iztacalco, 08400 Ciudad de México, CDMX",
        "estado": "Ciudad de México",
        "municipio": "Iztacalco",
        "codigo_postal": "08400",
        "latitud": 19.4042,
        "longitud": -99.0905,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Autodromo+Hermanos+Rodriguez+CDMX",
    },
]


def get_known_venues_minified() -> List[Dict[str, Any]]:
    """Devuelve el catálogo de recintos verificados en formato ligero para autocomplete live."""
    return [
        {
            "name": v["sede"],
            "address": v["direccion"],
            "state": v["estado"],
            "municipio": v["municipio"],
            "cp": v.get("codigo_postal", ""),
            "lat": v["latitud"],
            "lon": v["longitud"],
            "gmaps": v["google_maps_url"],
            "keywords": v.get("keywords", []),
        }
        for v in KNOWN_VENUES
    ]


def _pt_in_ring(lon: float, lat: float, ring: List[List[float]]) -> bool:
    inside = False
    n = len(ring)
    p1x, p1y = ring[0][0], ring[0][1]
    for i in range(1, n + 1):
        p2x, p2y = ring[i % n][0], ring[i % n][1]
        if min(p1y, p2y) < lat <= max(p1y, p2y) and lon <= max(p1x, p2x):
            if p1y != p2y:
                xinters = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
            else:
                xinters = p1x
            if p1x == p2x or lon <= xinters:
                inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def is_point_in_geometry(lat: float, lon: float, geom: Dict[str, Any]) -> bool:
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "Polygon" and coords:
        return _pt_in_ring(lon, lat, coords[0])
    if gtype == "MultiPolygon":
        return any(_pt_in_ring(lon, lat, poly[0]) for poly in coords if poly)
    return True


def get_geometry_centroid_and_bounds(geom: Dict[str, Any]) -> Tuple[float, float, float, float, float, float]:
    """Devuelve (center_lat, center_lon, min_lat, max_lat, min_lon, max_lon) del polígono principal."""
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    main_ring: List[List[float]] = []
    if gtype == "Polygon" and coords:
        main_ring = coords[0]
    elif gtype == "MultiPolygon" and coords:
        main_ring = max((poly[0] for poly in coords if poly), key=len, default=[])

    if not main_ring:
        return 19.4326, -99.1332, 14.5, 32.7, -118.4, -86.7

    lons = [p[0] for p in main_ring]
    lats = [p[1] for p in main_ring]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    c_lat = sum(lats) / len(lats)
    c_lon = sum(lons) / len(lons)
    return c_lat, c_lon, min_lat, max_lat, min_lon, max_lon


_POINT_IN_STATE_CACHE: Dict[Tuple[float, float, str], Tuple[float, float]] = {}
_STATE_ZOOM_BOUNDS_CACHE: Dict[str, Tuple[List[float], List[float]]] = {}
_STATE_CENTER_ZOOM_CACHE: Dict[str, Tuple[Dict[str, float], float]] = {}


def ensure_point_inside_state(
    lat: float,
    lon: float,
    state_id: str,
    geojson_data: Dict[str, Any],
) -> Tuple[float, float]:
    """
    Garantiza que las coordenadas (lat, lon) caigan estrictamente dentro del polígono
    del estado en el GeoJSON (evitando que pines fronterizos como Citibanamex caigan en un estado vecino).
    """
    cache_key = (round(float(lat), 5), round(float(lon), 5), str(state_id))
    if cache_key in _POINT_IN_STATE_CACHE:
        return _POINT_IN_STATE_CACHE[cache_key]

    feat = next(
        (f for f in geojson_data.get("features", []) if f.get("properties", {}).get("id") == state_id),
        None,
    )
    if not feat:
        _POINT_IN_STATE_CACHE[cache_key] = (lat, lon)
        return lat, lon

    geom = feat.get("geometry", {})
    if is_point_in_geometry(lat, lon, geom):
        _POINT_IN_STATE_CACHE[cache_key] = (lat, lon)
        return lat, lon

    c_lat, c_lon, _, _, _, _ = get_geometry_centroid_and_bounds(geom)
    for step in range(1, 21):
        alpha = step / 20.0
        cand_lat = lat + alpha * (c_lat - lat)
        cand_lon = lon + alpha * (c_lon - lon)
        if is_point_in_geometry(cand_lat, cand_lon, geom):
            # Empujar un 10% adicional hacia el interior para no quedar sobre la línea divisoria
            inner_alpha = min(1.0, alpha + 0.12)
            res = (
                round(lat + inner_alpha * (c_lat - lat), 4),
                round(lon + inner_alpha * (c_lon - lon), 4),
            )
            _POINT_IN_STATE_CACHE[cache_key] = res
            return res
    res = (round(c_lat, 4), round(c_lon, 4))
    _POINT_IN_STATE_CACHE[cache_key] = res
    return res


def get_state_zoom_bounds(state_id: str, geojson_data: Dict[str, Any]) -> Tuple[List[float], List[float]]:
    """Devuelve ([min_lat, max_lat], [min_lon, max_lon]) con margen para zoom al estado."""
    if state_id in _STATE_ZOOM_BOUNDS_CACHE:
        return _STATE_ZOOM_BOUNDS_CACHE[state_id]

    feat = next(
        (f for f in geojson_data.get("features", []) if f.get("properties", {}).get("id") == state_id),
        None,
    )
    if not feat:
        return [13.5, 34.5], [-118.8, -85.5]
    _, _, min_lat, max_lat, min_lon, max_lon = get_geometry_centroid_and_bounds(feat.get("geometry", {}))
    lat_span = max(0.45, max_lat - min_lat)
    lon_span = max(0.55, max_lon - min_lon)
    pad_lat = lat_span * 0.28
    pad_lon = lon_span * 0.28
    res = ([min_lat - pad_lat, max_lat + pad_lat], [min_lon - pad_lon, max_lon + pad_lon])
    _STATE_ZOOM_BOUNDS_CACHE[state_id] = res
    return res


def get_state_map_center_and_zoom(state_id: str, geojson_data: Dict[str, Any]) -> Tuple[Dict[str, float], float]:
    """Devuelve (center_dict, zoom_level) para centrar un mapa interactivo (MapLibre) en el estado."""
    if state_id in _STATE_CENTER_ZOOM_CACHE:
        return _STATE_CENTER_ZOOM_CACHE[state_id]

    feat = next(
        (f for f in geojson_data.get("features", []) if f.get("properties", {}).get("id") == state_id),
        None,
    )
    if not feat:
        return {"lat": 23.6, "lon": -102.5}, 4.05

    c_lat, c_lon, min_lat, max_lat, min_lon, max_lon = get_geometry_centroid_and_bounds(feat.get("geometry", {}))
    span = max(max_lat - min_lat, max_lon - min_lon)
    if span < 0.8:
        zoom = 8.5
    elif span < 2.0:
        zoom = 7.0
    elif span < 4.0:
        zoom = 6.0
    else:
        zoom = 5.2
    res = ({"lat": round(c_lat, 4), "lon": round(c_lon, 4)}, zoom)
    _STATE_CENTER_ZOOM_CACHE[state_id] = res
    return res


def strip_accents(text: str) -> str:
    if not text:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", text.strip().lower())
        if unicodedata.category(c) != "Mn"
    )


def normalize_state_name(raw_state: str) -> str:
    if not raw_state:
        return "Ciudad de México"
    cleaned = strip_accents(raw_state)
    if cleaned in STATE_ALIASES:
        return STATE_ALIASES[cleaned]
    for canonical in MEXICO_STATES:
        if strip_accents(canonical) == cleaned or strip_accents(canonical) in cleaned:
            return canonical
    return raw_state.strip()


def get_state_id(state_name: str) -> str:
    canonical = normalize_state_name(state_name)
    return MEXICO_STATES.get(canonical, {}).get("id", "MX-CMX")


def format_date_mx(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        d = dt.date.fromisoformat(str(date_str)[:10])
        return d.strftime("%d/%m/%Y")
    except ValueError:
        return str(date_str)


def format_date_range_mx(start_iso: str, end_iso: str) -> str:
    s = format_date_mx(start_iso)
    e = format_date_mx(end_iso)
    if not e or s == e:
        return s
    return f"{s} – {e}"


def compute_temporal_status(
    start_iso: str,
    end_iso: str,
    reference_date: Optional[dt.date] = None,
) -> Dict[str, Any]:
    if reference_date is None:
        reference_date = get_mexico_today()
    try:
        start_d = dt.date.fromisoformat(str(start_iso)[:10])
        end_d = dt.date.fromisoformat(str(end_iso or start_iso)[:10])
    except ValueError:
        start_d = reference_date
        end_d = reference_date

    days_to_start = (start_d - reference_date).days
    days_since_end = (reference_date - end_d).days
    duration_days = max(1, (end_d - start_d).days + 1)

    if end_d < reference_date:
        return {
            "code": "EXPIRADO",
            "label": "EXPIRADO",
            "short_delta": f"Hace {days_since_end}d",
            "color": "#64748B",
            "bg": "#F1F5F9",
            "border": "#CBD5E1",
            "sort_rank": 4,
            "days_to_start": days_to_start,
            "duration_days": duration_days,
        }
    if start_d <= reference_date <= end_d:
        return {
            "code": "VIGENTE",
            "label": "HOY · VIGENTE",
            "short_delta": "EN CURSO HOY",
            "color": "#059669",
            "bg": "#ECFDF5",
            "border": "#059669",
            "sort_rank": 1,
            "days_to_start": 0,
            "duration_days": duration_days,
        }
    if 1 <= days_to_start <= 7:
        return {
            "code": "PROXIMO",
            "label": f"PRÓXIMO · D-{days_to_start}",
            "short_delta": f"En {days_to_start}d",
            "color": "#D97706",
            "bg": "#FFFBEB",
            "border": "#D97706",
            "sort_rank": 2,
            "days_to_start": days_to_start,
            "duration_days": duration_days,
        }
    return {
        "code": "FUTURO",
        "label": f"FUTURO · D-{days_to_start}",
        "short_delta": f"En {days_to_start}d",
        "color": "#2563EB",
        "bg": "#EFF6FF",
        "border": "#2563EB",
        "sort_rank": 3,
        "days_to_start": days_to_start,
        "duration_days": duration_days,
    }


def parse_google_maps_or_search(query_or_url: str) -> Dict[str, Any]:
    raw = (query_or_url or "").strip()
    if not raw:
        return {
            "ok": False,
            "error": "Ingresa el nombre del recinto, dirección o URL de Google Maps.",
        }

    norm_query = strip_accents(raw)
    for venue in KNOWN_VENUES:
        if any(k in norm_query for k in venue["keywords"]):
            return {
                "ok": True,
                "source": "Catálogo Verificado Bancomext",
                **venue,
            }

    extracted_coords: Optional[Tuple[float, float]] = None
    extracted_place = raw

    if "google.com/maps" in raw or "maps.app.goo.gl" in raw or "goo.gl/maps" in raw:
        coord_match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", raw)
        if not coord_match:
            coord_match = re.search(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)", raw)
        if coord_match:
            extracted_coords = (float(coord_match.group(1)), float(coord_match.group(2)))

        place_match = re.search(r"/place/([^/@?]+)", raw)
        if place_match:
            extracted_place = urllib.parse.unquote_plus(place_match.group(1))
        else:
            q_match = re.search(r"[?&](?:q|query)=([^&]+)", raw)
            if q_match:
                extracted_place = urllib.parse.unquote_plus(q_match.group(1))

    headers = {"User-Agent": "BancomextEventosDashboard/1.0 (ejecutivo@bancomext.gob.mx)"}

    if extracted_coords:
        lat, lon = extracted_coords
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "accept-language": "es",
                },
                headers=headers,
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                addr = data.get("address", {})
                raw_state = addr.get("state") or addr.get("region") or "Ciudad de México"
                estado = normalize_state_name(raw_state)
                municipio = (
                    addr.get("county")
                    or addr.get("municipality")
                    or addr.get("city_district")
                    or addr.get("borough")
                    or addr.get("city")
                    or addr.get("town")
                    or MEXICO_STATES.get(estado, {}).get("capital", "")
                )
                sede = (
                    data.get("name")
                    or addr.get("amenity")
                    or addr.get("building")
                    or extracted_place[:65]
                )
                return {
                    "ok": True,
                    "source": "Google Maps URL + Geocodificación",
                    "sede": sede,
                    "direccion": data.get("display_name", extracted_place),
                    "estado": estado,
                    "municipio": municipio,
                    "codigo_postal": addr.get("postcode", ""),
                    "latitud": round(lat, 5),
                    "longitud": round(lon, 5),
                    "google_maps_url": raw if raw.startswith("http") else f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
                }
        except Exception:
            pass

    try:
        search_term = extracted_place
        if "mexico" not in strip_accents(search_term):
            search_term = f"{extracted_place}, México"
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": search_term,
                "format": "jsonv2",
                "addressdetails": 1,
                "countrycodes": "mx",
                "limit": 1,
                "accept-language": "es",
            },
            headers=headers,
            timeout=8,
        )
        if resp.status_code == 200 and resp.json():
            hit = resp.json()[0]
            addr = hit.get("address", {})
            lat = float(hit["lat"])
            lon = float(hit["lon"])
            raw_state = addr.get("state") or addr.get("ISO3166-2-lvl4") or "Ciudad de México"
            estado = normalize_state_name(raw_state)
            municipio = (
                addr.get("county")
                or addr.get("municipality")
                or addr.get("city_district")
                or addr.get("borough")
                or addr.get("city")
                or addr.get("town")
                or MEXICO_STATES.get(estado, {}).get("capital", "")
            )
            sede = hit.get("name") or extracted_place.split(",")[0].strip()
            encoded_q = urllib.parse.quote_plus(f"{sede}, {municipio}, {estado}, México")
            gmaps_url = (
                raw
                if raw.startswith("http")
                else f"https://www.google.com/maps/search/?api=1&query={encoded_q}"
            )
            return {
                "ok": True,
                "source": "Geocodificador Nacional (Google Maps Link)",
                "sede": sede,
                "direccion": hit.get("display_name", extracted_place),
                "estado": estado,
                "municipio": municipio,
                "codigo_postal": addr.get("postcode", ""),
                "latitud": round(lat, 5),
                "longitud": round(lon, 5),
                "google_maps_url": gmaps_url,
            }
    except Exception:
        pass

    detected_state = "Ciudad de México"
    for st_name in MEXICO_STATES:
        if strip_accents(st_name) in norm_query:
            detected_state = st_name
            break
    else:
        for alias, canon in STATE_ALIASES.items():
            if alias in norm_query.split():
                detected_state = canon
                break

    st_meta = MEXICO_STATES[detected_state]
    encoded_q = urllib.parse.quote_plus(raw)
    return {
        "ok": True,
        "source": "Inferencia Geográfica Estatal",
        "sede": raw.split(",")[0].strip(),
        "direccion": raw,
        "estado": detected_state,
        "municipio": st_meta["capital"],
        "codigo_postal": "",
        "latitud": st_meta["lat"],
        "longitud": st_meta["lon"],
        "google_maps_url": (
            raw
            if raw.startswith("http")
            else f"https://www.google.com/maps/search/?api=1&query={encoded_q}"
        ),
    }


def _extract_hours(horario_str: str) -> Tuple[int, int, int, int]:
    matches = re.findall(r"(\d{1,2}):(\d{2})", horario_str or "")
    if len(matches) >= 2:
        return int(matches[0][0]), int(matches[0][1]), int(matches[1][0]), int(matches[1][1])
    if len(matches) == 1:
        h, m = int(matches[0][0]), int(matches[0][1])
        return h, m, min(23, h + 2), m
    return 9, 0, 18, 0


def build_google_calendar_url(ev: Dict[str, Any]) -> str:
    default_iso = get_mexico_today().isoformat()
    start_iso = str(ev.get("fecha_inicio", default_iso))[:10]
    end_iso = str(ev.get("fecha_fin", start_iso))[:10]
    h1, m1, h2, m2 = _extract_hours(ev.get("horario", ""))

    start_dt = dt.datetime.fromisoformat(start_iso).replace(hour=h1, minute=m1)
    end_dt = dt.datetime.fromisoformat(end_iso).replace(hour=h2, minute=m2)

    dates_param = f"{start_dt.strftime('%Y%m%dT%H%M%S')}/{end_dt.strftime('%Y%m%dT%H%M%S')}"
    location = (
        f"{ev.get('sede', '')}, {ev.get('municipio', '')}, {ev.get('estado', '')}"
        if ev.get("modalidad") != "En Línea"
        else "Sesión Virtual (En Línea)"
    )
    details = (
        f"[{ev.get('id', '')}] {ev.get('categoria', '')} | Promotor: {ev.get('promotor', 'NAFIN / BANCOMEXT')}\n"
        f"Horario: {ev.get('horario', '')}\n"
        f"Ubicación Maps: {ev.get('google_maps_url', '')}\n\n"
        f"{ev.get('descripcion', '')}"
    )

    params = {
        "action": "TEMPLATE",
        "text": f"[Bancomext] {ev.get('nombre', '')}",
        "dates": dates_param,
        "ctz": "America/Mexico_City",
        "details": details,
        "location": location,
    }
    return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"


def build_ics_content(ev: Dict[str, Any]) -> str:
    default_iso = get_mexico_today().isoformat()
    start_iso = str(ev.get("fecha_inicio", default_iso))[:10]
    end_iso = str(ev.get("fecha_fin", start_iso))[:10]
    h1, m1, h2, m2 = _extract_hours(ev.get("horario", ""))

    start_dt = dt.datetime.fromisoformat(start_iso).replace(hour=h1, minute=m1)
    end_dt = dt.datetime.fromisoformat(end_iso).replace(hour=h2, minute=m2)
    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    location = (
        f"{ev.get('sede', '')}, {ev.get('direccion', '')}"
        if ev.get("modalidad") != "En Línea"
        else "En Línea"
    )
    desc = (
        f"ID: {ev.get('id', '')}\\n"
        f"Categoría: {ev.get('categoria', '')}\\n"
        f"Promotor: {ev.get('promotor', 'NAFIN / BANCOMEXT')}\\n"
        f"Horario: {ev.get('horario', '')}\\n"
        f"Google Maps: {ev.get('google_maps_url', '')}\\n\\n"
        f"{(ev.get('descripcion', '') or '').replace(chr(10), '\\n')}"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//BANCOMEXT-NAFIN//Calendario Ejecutivo de Eventos//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{ev.get('id', 'EVT')}@eventos.bancomext.gob.mx",
        f"DTSTAMP:{now_utc}",
        f"DTSTART;TZID=America/Mexico_City:{start_dt.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID=America/Mexico_City:{end_dt.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:[NAFIN/BANCOMEXT] {ev.get('nombre', '')}",
        f"LOCATION:{location}",
        f"DESCRIPTION:{desc}",
        "STATUS:CONFIRMED",
        "BEGIN:VALARM",
        "TRIGGER:-PT24H",
        "ACTION:DISPLAY",
        "DESCRIPTION:Recordatorio Ejecutivo 24h antes",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines)
