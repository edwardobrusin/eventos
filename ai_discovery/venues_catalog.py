"""
Catálogo determinístico de los principales recintos feriales y centros de convenciones
de México con dirección, código postal, estado_id ISO y coordenadas exactas verificadas.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, Optional

from geo_utils import MEXICO_STATES, get_state_id, normalize_state_name, parse_google_maps_or_search


def _norm(text: str) -> str:
    if not text:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", text.strip().lower())
        if unicodedata.category(c) != "Mn"
    )


KNOWN_VENUES_MEXICO: Dict[str, Dict[str, Any]] = {
    "cintermex": {
        "aliases": [
            "cintermex",
            "centro internacional de negocios monterrey",
            "parque fundidora monterrey",
        ],
        "sede": "CINTERMEX Monterrey",
        "direccion": "Av. Fundidora 501, Col. Obrera, 64010 Monterrey, N.L.",
        "estado": "Nuevo León",
        "estado_id": "MX-NLE",
        "municipio": "Monterrey",
        "codigo_postal": "64010",
        "latitud": 25.6782,
        "longitud": -100.2877,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=CINTERMEX+Monterrey+Nuevo+Leon",
    },
    "expo_guadalajara": {
        "aliases": [
            "expo guadalajara",
            "expoguadalajara",
            "mariano otero 1499",
        ],
        "sede": "Expo Guadalajara",
        "direccion": "Av. Mariano Otero 1499, Verde Valle, 44550 Guadalajara, Jal.",
        "estado": "Jalisco",
        "estado_id": "MX-JAL",
        "municipio": "Guadalajara",
        "codigo_postal": "44550",
        "latitud": 20.6534,
        "longitud": -103.3913,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+Guadalajara+Mariano+Otero",
    },
    "centro_citibanamex": {
        "aliases": [
            "centro citibanamex",
            "citibanamex",
            "centro banamex",
            "av. del conscripto 311",
            "conscripto 311",
        ],
        "sede": "Centro Citibanamex",
        "direccion": "Av. del Conscripto 311, Lomas de Sotelo, Hipódromo de las Américas, Miguel Hidalgo, 11200 CDMX",
        "estado": "Ciudad de México",
        "estado_id": "MX-CMX",
        "municipio": "Miguel Hidalgo",
        "codigo_postal": "11200",
        "latitud": 19.4280,
        "longitud": -99.1480,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+Citibanamex+CDMX",
    },
    "wtc_cdmx": {
        "aliases": [
            "wtc ciudad de mexico",
            "world trade center ciudad de mexico",
            "world trade center cdmx",
            "wtc cdmx",
            "wtc mex",
            "centro internacional de exposiciones y convenciones wtc",
            "montecito 38",
        ],
        "sede": "World Trade Center Ciudad de México",
        "direccion": "Montecito 38, Nápoles, Benito Juárez, 03810 Ciudad de México, CDMX",
        "estado": "Ciudad de México",
        "estado_id": "MX-CMX",
        "municipio": "Benito Juárez",
        "codigo_postal": "03810",
        "latitud": 19.3946,
        "longitud": -99.1643,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=World+Trade+Center+Ciudad+de+Mexico",
    },
    "expo_santa_fe": {
        "aliases": [
            "expo santa fe",
            "expo santa fe mexico",
            "av. santa fe 270",
        ],
        "sede": "Expo Santa Fe México",
        "direccion": "Av. Santa Fe 270, Santa Fe, Zedec Sta Fé, Álvaro Obregón, 01210 Ciudad de México, CDMX",
        "estado": "Ciudad de México",
        "estado_id": "MX-CMX",
        "municipio": "Álvaro Obregón",
        "codigo_postal": "01210",
        "latitud": 19.3693,
        "longitud": -99.2602,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+Santa+Fe+Mexico+CDMX",
    },
    "poliforum_leon": {
        "aliases": [
            "poliforum leon",
            "poliforum",
            "recinto ferial leon",
        ],
        "sede": "Poliforum León",
        "direccion": "Blvd. Adolfo López Mateos Esq. Blvd. Francisco Villa s/n, Oriental, 37510 León, Gto.",
        "estado": "Guanajuato",
        "estado_id": "MX-GUA",
        "municipio": "León",
        "codigo_postal": "37510",
        "latitud": 21.1165,
        "longitud": -101.6525,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Poliforum+Leon+Guanajuato",
    },
    "qcc_queretaro": {
        "aliases": [
            "queretaro centro de congresos",
            "qcc queretaro",
            "qcc",
            "centro de congresos queretaro",
            "paseo de las artes 1531",
        ],
        "sede": "Querétaro Centro de Congresos (QCC)",
        "direccion": "Paseo de las Artes 1531, Josefa Vergara, 76090 Santiago de Querétaro, Qro.",
        "estado": "Querétaro",
        "estado_id": "MX-QUE",
        "municipio": "Querétaro",
        "codigo_postal": "76090",
        "latitud": 20.5732,
        "longitud": -100.3679,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Queretaro+Centro+de+Congresos+QCC",
    },
    "centro_expositor_puebla": {
        "aliases": [
            "centro expositor puebla",
            "centro expositor y de convenciones de puebla",
            "zona de los fuertes puebla",
        ],
        "sede": "Centro Expositor Puebla",
        "direccion": "Av. Ejército de Oriente 100, Zona de los Fuertes, Cívica 5 de Mayo, 72260 Heroica Puebla de Zaragoza, Pue.",
        "estado": "Puebla",
        "estado_id": "MX-PUE",
        "municipio": "Puebla",
        "codigo_postal": "72260",
        "latitud": 19.0574,
        "longitud": -98.1811,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+Expositor+Puebla",
    },
    "cancun_center": {
        "aliases": [
            "cancun center",
            "cancun icc",
            "centro de convenciones cancun",
            "kukulcan km 9",
        ],
        "sede": "Cancún Center Conventions & Exhibitions",
        "direccion": "Blvd. Kukulcán Km. 9, Punta Cancún, Zona Hotelera, 77500 Cancún, Q.R.",
        "estado": "Quintana Roo",
        "estado_id": "MX-ROO",
        "municipio": "Benito Juárez (Cancún)",
        "codigo_postal": "77500",
        "latitud": 21.1328,
        "longitud": -86.7473,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Cancun+Center+Conventions+Exhibitions",
    },
    "expo_chihuahua": {
        "aliases": [
            "expo chihuahua",
            "centro de exposiciones y convenciones expo chihuahua",
        ],
        "sede": "Centro de Exposiciones y Convenciones Expo Chihuahua",
        "direccion": "Av. Tecnológico 1900, Santo Niño, 31200 Chihuahua, Chih.",
        "estado": "Chihuahua",
        "estado_id": "MX-CHH",
        "municipio": "Chihuahua",
        "codigo_postal": "31200",
        "latitud": 28.6531,
        "longitud": -106.0853,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+Chihuahua+Centro+de+Convenciones",
    },
    "centro_negocios_potosi": {
        "aliases": [
            "centro de negocios potosi",
            "centro de convenciones de san luis potosi",
            "cnp san luis",
        ],
        "sede": "Centro de Negocios Potosí (CNP)",
        "direccion": "Blvd. Antonio Rocha Cordero 125, Desarrollo del Pedregal, 78395 San Luis Potosí, S.L.P.",
        "estado": "San Luis Potosí",
        "estado_id": "MX-SLP",
        "municipio": "San Luis Potosí",
        "codigo_postal": "78395",
        "latitud": 22.1194,
        "longitud": -101.0028,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+de+Convenciones+de+San+Luis+Potosi",
    },
    "expo_agroalimentaria_gto": {
        "aliases": [
            "expo agroalimentaria guanajuato",
            "recinto expo agroalimentaria",
            "carretera irapuato - abasolo",
        ],
        "sede": "Recinto Expo AgroAlimentaria Guanajuato",
        "direccion": "Carretera Irapuato - Abasolo Km 6.5, 36557 Irapuato, Gto.",
        "estado": "Guanajuato",
        "estado_id": "MX-GUA",
        "municipio": "Irapuato",
        "codigo_postal": "36557",
        "latitud": 20.6311,
        "longitud": -101.4119,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+AgroAlimentaria+Guanajuato+Irapuato",
    },
    "ficotrece_ags": {
        "aliases": [
            "ficotrece",
            "complejo tres centurias",
            "tres centurias aguascalientes",
        ],
        "sede": "Complejo Tres Centurias (Ficotrece)",
        "direccion": "Av. Manuel Gómez Morín s/n, Barrio de la Estación, 20259 Aguascalientes, Ags.",
        "estado": "Aguascalientes",
        "estado_id": "MX-AGU",
        "municipio": "Aguascalientes",
        "codigo_postal": "20259",
        "latitud": 21.8891,
        "longitud": -102.2798,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Complejo+Tres+Centurias+Ficotrece+Aguascalientes",
    },
    "presidente_intercontinental_mty": {
        "aliases": [
            "presidente intercontinental monterrey",
            "real san agustin 301",
        ],
        "sede": "Hotel Presidente InterContinental Monterrey",
        "direccion": "Av. Real San Agustín 301, Zona San Agustín, 66260 San Pedro Garza García, N.L.",
        "estado": "Nuevo León",
        "estado_id": "MX-NLE",
        "municipio": "San Pedro Garza García",
        "codigo_postal": "66260",
        "latitud": 25.6488,
        "longitud": -100.3382,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Hotel+Presidente+InterContinental+Monterrey+San+Pedro+Garza+Garcia",
    },
    "siglo_xxi_merida": {
        "aliases": [
            "centro de convenciones yucatan siglo xxi",
            "siglo xxi merida",
            "centro internacional de congresos de yucatan",
            "cic yucatan",
        ],
        "sede": "Centro de Convenciones Yucatán Siglo XXI",
        "direccion": "Calle 60 Norte No. 299-E, Revolución, 97118 Mérida, Yuc.",
        "estado": "Yucatán",
        "estado_id": "MX-YUC",
        "municipio": "Mérida",
        "codigo_postal": "97118",
        "latitud": 21.0338,
        "longitud": -89.6290,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+de+Convenciones+Yucatan+Siglo+XXI+Merida",
    },
    "baja_california_center": {
        "aliases": [
            "baja california center",
            "bc center",
            "centro metropolitano de convenciones tijuana",
        ],
        "sede": "Baja California Center",
        "direccion": "Carretera Escénica Tijuana - Ensenada Ejido Mazatlán No. 1029, 22710 Playas de Rosarito, B.C.",
        "estado": "Baja California",
        "estado_id": "MX-BCN",
        "municipio": "Playas de Rosarito / Tijuana",
        "codigo_postal": "22710",
        "latitud": 32.3935,
        "longitud": -117.0642,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Baja+California+Center+Rosarito+Tijuana",
    },
    "marquis_reforma_cdmx": {
        "aliases": [
            "marquis reforma",
            "hotel marquis reforma",
            "marquis reforma mexico city",
            "paseo de la reforma 465",
        ],
        "sede": "Hotel Marquis Reforma Ciudad de México",
        "direccion": "Av. Paseo de la Reforma 465, Cuauhtémoc, 06500 Ciudad de México, CDMX",
        "estado": "Ciudad de México",
        "estado_id": "MX-CMX",
        "municipio": "Cuauhtémoc",
        "codigo_postal": "06500",
        "latitud": 19.4243,
        "longitud": -99.1718,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Hotel+Marquis+Reforma+Ciudad+de+Mexico",
    },
}


def match_known_venue(
    sede_nombre: Optional[str] = None,
    direccion_explicita: Optional[str] = None,
    nombre_evento: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Busca coincidencia determinística en KNOWN_VENUES_MEXICO.
    """
    combined = _norm(" ".join(filter(None, [sede_nombre, direccion_explicita, nombre_evento])))
    if not combined:
        return None

    for key, venue in KNOWN_VENUES_MEXICO.items():
        if any(_norm(alias) in combined for alias in venue["aliases"]):
            return {
                "matched_catalog": True,
                "venue_key": key,
                "sede": venue["sede"],
                "direccion": venue["direccion"],
                "estado": venue["estado"],
                "estado_id": venue["estado_id"],
                "municipio": venue["municipio"],
                "codigo_postal": venue["codigo_postal"],
                "latitud": venue["latitud"],
                "longitud": venue["longitud"],
                "google_maps_url": venue["google_maps_url"],
            }
    return None


def resolve_candidate_geography(
    sede_nombre: Optional[str],
    direccion_explicita: Optional[str],
    estado_nombre: Optional[str],
    municipio: Optional[str],
    codigo_postal_explicito: Optional[str],
    modalidad: str = "Presencial",
    nombre_evento: Optional[str] = None,
    default_estado: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resuelve de forma 100% determinística la geografía de un candidato IA:
    1) Primero consulta KNOWN_VENUES_MEXICO.
    2) En segundo lugar delega a geo_utils.py (parse_google_maps_or_search / normalize_state_name).
    """
    if modalidad in ("Virtual", "En Línea"):
        return {
            "matched_catalog": False,
            "resolved_full_coords": False,
            "sede": sede_nombre or "Plataforma Virtual",
            "direccion": direccion_explicita or "",
            "estado": "Ciudad de México",
            "estado_id": "ONLINE",
            "municipio": municipio or "Sesión Virtual",
            "codigo_postal": codigo_postal_explicito or "",
            "latitud": 19.4326,
            "longitud": -99.1332,
            "google_maps_url": "",
        }

    known = match_known_venue(sede_nombre, direccion_explicita, nombre_evento)
    if known:
        return {
            **known,
            "resolved_full_coords": True,
        }

    query_parts = [p for p in [sede_nombre, direccion_explicita, municipio, estado_nombre or default_estado] if p]
    if query_parts:
        geo_res = parse_google_maps_or_search(", ".join(query_parts))
        if geo_res.get("ok"):
            st_norm = normalize_state_name(estado_nombre or geo_res.get("estado") or default_estado or "Ciudad de México")
            st_id = get_state_id(st_norm)
            cp_val = (codigo_postal_explicito or geo_res.get("codigo_postal") or "").strip()
            lat_val = geo_res.get("latitud")
            lon_val = geo_res.get("longitud")
            is_full = bool(
                geo_res.get("source") in ("Catálogo Verificado Bancomext", "Google Maps URL + Geocodificación", "Geocodificador Nacional (Google Maps Link)")
                and cp_val
                and lat_val is not None
                and lon_val is not None
            )
            return {
                "matched_catalog": geo_res.get("source") == "Catálogo Verificado Bancomext",
                "resolved_full_coords": is_full,
                "sede": (sede_nombre or geo_res.get("sede") or "").strip(),
                "direccion": (direccion_explicita or geo_res.get("direccion") or "").strip(),
                "estado": st_norm,
                "estado_id": st_id,
                "municipio": (municipio or geo_res.get("municipio") or "").strip(),
                "codigo_postal": cp_val,
                "latitud": float(lat_val) if lat_val is not None else MEXICO_STATES[st_norm]["lat"],
                "longitud": float(lon_val) if lon_val is not None else MEXICO_STATES[st_norm]["lon"],
                "google_maps_url": geo_res.get("google_maps_url") or "",
            }

    st_norm = normalize_state_name(estado_nombre or default_estado or "Ciudad de México")
    st_id = get_state_id(st_norm)
    st_meta = MEXICO_STATES.get(st_norm, MEXICO_STATES["Ciudad de México"])
    return {
        "matched_catalog": False,
        "resolved_full_coords": False,
        "sede": (sede_nombre or "").strip(),
        "direccion": (direccion_explicita or "").strip(),
        "estado": st_norm,
        "estado_id": st_id,
        "municipio": (municipio or st_meta["capital"]).strip(),
        "codigo_postal": (codigo_postal_explicito or "").strip(),
        "latitud": float(st_meta["lat"]),
        "longitud": float(st_meta["lon"]),
        "google_maps_url": f"https://www.google.com/maps/search/?api=1&query={st_meta['lat']},{st_meta['lon']}",
    }

