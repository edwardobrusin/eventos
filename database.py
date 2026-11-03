"""
Capa de persistencia Neon Serverless PostgreSQL + pgvector para el Calendario Centralizado
de Eventos NAFIN / BANCOMEXT.
Mantiene compatibilidad 100% hacia atrás con los contratos consumidos por app.py,
e incorpora la tabla de staging Human-in-the-Loop (eventos_pendientes_ia).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import time
import tomllib
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import QueuePool

from geo_utils import get_mexico_today, get_state_id, normalize_state_name

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "eventos_bancomext.db")
SEED_JSON_PATH = os.path.join(DATA_DIR, "eventos_seed.json")
SECRETS_TOML_PATH = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")

CATEGORIES = [
    "Comercio Exterior",
    "Automotriz y Aeroespacial",
    "Manufactura Avanzada",
    "Semiconductores y Tech",
    "Agroindustria",
    "Turismo",
    "Dispositivos Médicos",
    "Plásticos y Químicos",
]

ROLES_CONFIG = {
    "visualizador": {
        "nombre": "Visualizador",
        "nivel": 1,
        "badge_color": "#475569",
        "badge_bg": "#F1F5F9",
        "permisos": "Consulta de calendario, mapa nacional y fichas individuales.",
        "recibe_alertas": False,
        "puede_registrar_eventos": False,
        "puede_administrar_usuarios": False,
    },
    "receptor": {
        "nombre": "Receptor",
        "nivel": 2,
        "badge_color": "#0369A1",
        "badge_bg": "#E0F2FE",
        "permisos": "Visualización completa + recepción de invitaciones (.ics) y alertas.",
        "recibe_alertas": True,
        "puede_registrar_eventos": False,
        "puede_administrar_usuarios": False,
    },
    "director": {
        "nombre": "Director",
        "nivel": 3,
        "badge_color": "#0F766E",
        "badge_bg": "#CCFBF1",
        "permisos": "Recepción de notificaciones + registro y edición de eventos institucionales.",
        "recibe_alertas": True,
        "puede_registrar_eventos": True,
        "puede_administrar_usuarios": False,
    },
    "admin": {
        "nombre": "Admin",
        "nivel": 4,
        "badge_color": "#9A3412",
        "badge_bg": "#FFEDD5",
        "permisos": "Control total: eventos, despacho de alertas y alta/gestión de usuarios y roles.",
        "recibe_alertas": True,
        "puede_registrar_eventos": True,
        "puede_administrar_usuarios": True,
    },
}


def hash_password(raw_password: str) -> str:
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


def _read_local_secrets_toml() -> Dict[str, Any]:
    if os.path.exists(SECRETS_TOML_PATH):
        try:
            with open(SECRETS_TOML_PATH, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}
    return {}


def get_secret(key: str, default: str = "") -> str:
    """
    Lee un secreto primero desde variables de entorno (GitHub Actions / CLI),
    luego desde st.secrets (Streamlit Cloud / Local) y finalmente desde .streamlit/secrets.toml.
    """
    val = os.environ.get(key)
    if val:
        return val.strip()
    try:
        import streamlit as st

        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    local_sec = _read_local_secrets_toml()
    if key in local_sec and local_sec[key]:
        return str(local_sec[key]).strip()
    return default


def normalize_neon_database_url(raw_url: str) -> str:
    """
    Normaliza el esquema hacia postgresql+psycopg2:// y garantiza ?sslmode=require.
    """
    url = (raw_url or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL no está configurada en variables de entorno ni en .streamlit/secrets.toml"
        )
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]

    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "sslmode" not in query_params:
        query_params["sslmode"] = "require"
    new_query = urlencode(query_params)
    return urlunparse(parsed._replace(query=new_query))


_ENGINE: Optional[Engine] = None
_DB_INITIALIZED: bool = False
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS: float = 45.0


def invalidate_cache() -> None:
    """Invalida de inmediato el caché en memoria tras cualquier escritura."""
    _CACHE.clear()


def _cache_get(key: str) -> Any:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, val = entry
    if (time.monotonic() - ts) > _CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_set(key: str, val: Any) -> Any:
    _CACHE[key] = (time.monotonic(), val)
    return val


def get_engine() -> Engine:
    """
    Devuelve el Engine singleton de SQLAlchemy configurado con protección contra
    cold-starts y suspensión de cómputo de Neon Serverless PostgreSQL.
    """
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    raw_url = get_secret("DATABASE_URL")
    DATABASE_URL = normalize_neon_database_url(raw_url)

    _ENGINE = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,        # CRÍTICO para Neon: verifica que la conexión esté viva antes de cada query
        pool_recycle=300,          # Recicla conexiones antes del corte de inactividad de 5 min de Neon
        connect_args={
            "connect_timeout": 15, # Tolera el cold-start (~300-800ms) de Neon
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )
    return _ENGINE


def get_sqlite_connection() -> sqlite3.Connection:
    """Conexión directa al archivo SQLite legado (utilizado para migraciones)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_connection():
    """Mantiene compatibilidad devolviendo una conexión activa del pool de SQLAlchemy."""
    return get_engine().connect()


def _format_vector_literal(vec: Optional[List[float]]) -> Optional[str]:
    if not vec:
        return None
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


EVENTOS_PUBLIC_COLUMNS = (
    "id, nombre, descripcion, fecha_inicio, fecha_fin, horario, "
    "modalidad, sede, direccion, estado, estado_id, municipio, "
    "codigo_postal, latitud, longitud, google_maps_url, "
    "categoria, promotor, sitio_web, creado_por, created_at, updated_at"
)


def _serialize_row(row_mapping: Any, exclude_keys: Tuple[str, ...] = ("huella_semantica",)) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in dict(row_mapping).items():
        if k in exclude_keys:
            continue
        if isinstance(v, dt.datetime):
            out[k] = v.strftime("%Y-%m-%dT%H:%M:%S")
        elif isinstance(v, dt.date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


INITIAL_EVENTS: List[Dict[str, Any]] = [
    {
        "id": "EVT-2026-001",
        "nombre": "Mexico Tech Week — Foro Inaugural de Semiconductores y Diseño de Chips",
        "descripcion": (
            "Sesión de apertura del circuito de hardware y microelectrónica dentro de Mexico Tech Week 2026. "
            "Analiza la integración de México en la cadena de suministro norteamericana de semiconductores (CHIPS Act), "
            "capacidades de empaquetado avanzado (ATP), centros de diseño en el país y esquemas de financiamiento "
            "estructurado de NAFIN/BANCOMEXT para proveedores tecnológicos Tier 1 y Tier 2."
        ),
        "fecha_inicio": "2026-10-26",
        "fecha_fin": "2026-10-27",
        "horario": "09:30 - 17:30 hrs",
        "modalidad": "Presencial",
        "sede": " Tecnológico de Monterrey Campus Santa Fe — Hall de Innovación",
        "direccion": "Av. Carlos Lazo 100, Santa Fe, La Loma, Álvaro Obregón, 01389 Ciudad de México, CDMX",
        "estado": "Ciudad de México",
        "municipio": "Álvaro Obregón",
        "codigo_postal": "01389",
        "latitud": 19.3350,
        "longitud": -99.1950,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Tecnologico+de+Monterrey+Campus+Santa+Fe+CDMX",
        "categoria": "Semiconductores y Tech",
        "promotor": "NAFIN / BANCOMEXT · Mexico Tech Week",
        "sitio_web": "https://www.mexicotechweek.mx",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-002",
        "nombre": "32º Congreso del Comercio Exterior Mexicano (COMCE)",
        "descripcion": (
            "Cumbre anual del Consejo Empresarial Mexicano de Comercio Exterior, Inversión y Tecnología (COMCE). "
            "Reúne a líderes empresariales, secretarios de Estado, embajadores y banca de desarrollo (BANCOMEXT / NAFIN) "
            "para revisar la competitividad exportadora de México rumbo a la revisión del T-MEC 2026, logística "
            "transfronteriza, relocalización de cadenas de valor (nearshoring) y mesas de negocios B2B con Pabellón Internacional."
        ),
        "fecha_inicio": "2026-10-27",
        "fecha_fin": "2026-10-29",
        "horario": "08:30 - 18:30 hrs",
        "modalidad": "Presencial",
        "sede": "Hotel Presidente InterContinental Monterrey",
        "direccion": "Av. Real San Agustín 301, Zona San Agustín, 66260 San Pedro Garza García, N.L.",
        "estado": "Nuevo León",
        "municipio": "San Pedro Garza García",
        "codigo_postal": "66260",
        "latitud": 25.6488,
        "longitud": -100.3382,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Hotel+Presidente+InterContinental+Monterrey+San+Pedro+Garza+Garcia",
        "categoria": "Comercio Exterior",
        "promotor": "BANCOMEXT / NAFIN · COMCE Nacional",
        "sitio_web": "https://congresocomce.org.mx",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-003",
        "nombre": "Mexico Tech Week — Deep Tech House: Semiconductores en México",
        "descripcion": (
            "Encuentro especializado dentro de la Deep Tech House de Mexico Tech Week en Ciudad de México. "
            "Incluye el panel estratégico 'Semiconductores: oportunidades reales en México', vinculación entre fondos "
            "de capital, banca de desarrollo (NAFIN / BANCOMEXT), laboratorios de validación y startups de hardware, "
            "automatización y sensores industriales."
        ),
        "fecha_inicio": "2026-10-27",
        "fecha_fin": "2026-10-29",
        "horario": "10:00 - 19:00 hrs",
        "modalidad": "Presencial",
        "sede": "Mexico Tech Town — Sede Condesa / Roma",
        "direccion": "Av. Michoacán 78, Hipódromo Condesa, Cuauhtémoc, 06170 Ciudad de México, CDMX",
        "estado": "Ciudad de México",
        "municipio": "Cuauhtémoc",
        "codigo_postal": "06170",
        "latitud": 19.4119,
        "longitud": -99.1731,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Hipodromo+Condesa+Cuauhtemoc+CDMX",
        "categoria": "Semiconductores y Tech",
        "promotor": "NAFIN / BANCOMEXT · Mexico Tech Week",
        "sitio_web": "https://www.mexicotechweek.mx",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-004",
        "nombre": "Cocktail Privado — Clúster de Dispositivos Médicos de Baja California",
        "descripcion": (
            "Encuentro ejecutivo a puerta cerrada con directores de plantas OEM, proveedores certificados ISO 13485 y "
            "consejeros del Baja's Medical Device Cluster en Tijuana. Enfocado en financiamiento a la expansión de "
            "cuartos limpios, proveeduría nacional de grado médico, esterilización y programas de factoraje internacional "
            "de BANCOMEXT para el corredor médico Tijuana–San Diego."
        ),
        "fecha_inicio": "2026-10-29",
        "fecha_fin": "2026-10-29",
        "horario": "18:30 - 22:00 hrs",
        "modalidad": "Presencial",
        "sede": "Quartz Hotel & Spa — Salón Ejecutivo Zona Río",
        "direccion": "Paseo del Centenario 9580, Zona Urbana Río Tijuana, 22010 Tijuana, B.C.",
        "estado": "Baja California",
        "municipio": "Tijuana",
        "codigo_postal": "22010",
        "latitud": 32.5225,
        "longitud": -117.0189,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Quartz+Hotel+Zona+Rio+Tijuana",
        "categoria": "Dispositivos Médicos",
        "promotor": "BANCOMEXT / NAFIN · Clúster de Dispositivos Médicos BC",
        "sitio_web": "https://medsummit.org",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-005",
        "nombre": "Jalisco Tech Week — Foro de Semiconductores y Microelectrónica",
        "descripcion": (
            "Agenda especializada en diseño de circuitos integrados, pruebas, validación (OSAT) y manufactura electrónica "
            "de alta complejidad durante Jalisco Tech Week 2026 en Guadalajara ('Silicon Valley Mexicano'). "
            "Participan centros de ingeniería, CANIETI Occidente y promotores de NAFIN/BANCOMEXT para estructurar "
            "financiamiento de infraestructura tecnológica y cadenas de suministro electrónicas."
        ),
        "fecha_inicio": "2026-11-02",
        "fecha_fin": "2026-11-07",
        "horario": "09:30 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "MIND México Innovación y Diseño",
        "direccion": "Av. Faro 2350, Verde Valle, 44550 Guadalajara, Jal.",
        "estado": "Jalisco",
        "municipio": "Guadalajara",
        "codigo_postal": "44550",
        "latitud": 20.6548,
        "longitud": -103.3941,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=MIND+Mexico+Innovacion+y+Diseno+Guadalajara",
        "categoria": "Semiconductores y Tech",
        "promotor": "NAFIN / BANCOMEXT · Jalisco Tech Week / CANIETI",
        "sitio_web": "https://jaliscotechweek.com",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-006",
        "nombre": "World Meetings Forum (WMF Global Cancún) — Turismo",
        "descripcion": (
            "Cumbre global de la industria de turismo de reuniones (MICE), grupos, convenciones e infraestructura "
            "hotelera en el Caribe Mexicano. Participación estratégica de BANCOMEXT (banca sectorial de Turismo) en "
            "mesas de inversión hotelera sostenible, remodelación de activos turísticos y encadenamiento productivo "
            "de proveedores del sector hospitalidad."
        ),
        "fecha_inicio": "2026-11-03",
        "fecha_fin": "2026-11-03",
        "horario": "09:00 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Paradisus Cancún Convention Center",
        "direccion": "Blvd. Kukulcán Km 16.5, Zona Hotelera, 77500 Cancún, Q.R.",
        "estado": "Quintana Roo",
        "municipio": "Benito Juárez (Cancún)",
        "codigo_postal": "77500",
        "latitud": 21.0748,
        "longitud": -86.7764,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Paradisus+Cancun+Blvd+Kukulcan+Km+16.5",
        "categoria": "Turismo",
        "promotor": "BANCOMEXT Turismo · World Meetings Forum",
        "sitio_web": "https://wmforum.org",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-007",
        "nombre": "Wire Tech México — Expo Arnés Eléctrico León 2026",
        "descripcion": (
            "Exposición y encuentro técnico líder en América Latina para el procesamiento de cables, arneses eléctricos, "
            "conectores, sistemas de distribución eléctrica para vehículos eléctricos (EV) y automatización industrial. "
            "Foco de NAFIN/BANCOMEXT en sustitución de importaciones de componentes eléctricos para el corredor automotriz del Bajío."
        ),
        "fecha_inicio": "2026-11-04",
        "fecha_fin": "2026-11-05",
        "horario": "10:00 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Poliforum León",
        "direccion": "Blvd. Adolfo López Mateos Esq. Blvd. Francisco Villa s/n, Oriental, 37510 León, Gto.",
        "estado": "Guanajuato",
        "municipio": "León",
        "codigo_postal": "37510",
        "latitud": 21.1165,
        "longitud": -101.6525,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Poliforum+Leon+Guanajuato",
        "categoria": "Manufactura Avanzada",
        "promotor": "NAFIN / BANCOMEXT · WireTech México",
        "sitio_web": "https://wiretechmx.com",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-008",
        "nombre": "Automotive & Aerospace Nearshoring Summit 2026",
        "descripcion": (
            "Cumbre binacional de vinculación industrial para los sectores automotriz y aeroespacial en el norte de México. "
            "Integra citas B2B preagendadas entre compradores OEM/Tier 1 y manufactura avanzada en maquinados CNC, "
            "estampado, fundición y aviónica, con apoyo de financiamiento a naves industriales y equipamiento por NAFIN/BANCOMEXT."
        ),
        "fecha_inicio": "2026-11-10",
        "fecha_fin": "2026-11-11",
        "horario": "08:30 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Centro de Exposiciones y Convenciones Expo Chihuahua",
        "direccion": "Av. Tecnológico 1900, Santo Niño, 31200 Chihuahua, Chih.",
        "estado": "Chihuahua",
        "municipio": "Chihuahua",
        "codigo_postal": "31200",
        "latitud": 28.6531,
        "longitud": -106.0853,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+Chihuahua+Centro+de+Convenciones",
        "categoria": "Automotriz y Aeroespacial",
        "promotor": "BANCOMEXT / NAFIN · Index / Clúster Aeroespacial Chihuahua",
        "sitio_web": "https://expochihuahua.com.mx",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-009",
        "nombre": "Expo AgroAlimentaria Guanajuato 2026",
        "descripcion": (
            "Plataforma agroindustrial y de agrotecnología más importante de América Latina. Cuenta con parcelas "
            "demostrativas de invernaderos de alta tecnología, sistemas de riego, maquinaria agrícola, empaque "
            "agroexportador y centro de negocios internacional con cobertura de financiamiento agroexportador BANCOMEXT/NAFIN."
        ),
        "fecha_inicio": "2026-11-10",
        "fecha_fin": "2026-11-13",
        "horario": "10:00 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Recinto Expo AgroAlimentaria Guanajuato",
        "direccion": "Carretera Irapuato - Abasolo Km 6.5, 36557 Irapuato, Gto.",
        "estado": "Guanajuato",
        "municipio": "Irapuato",
        "codigo_postal": "36557",
        "latitud": 20.6311,
        "longitud": -101.4119,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+AgroAlimentaria+Guanajuato+Irapuato",
        "categoria": "Agroindustria",
        "promotor": "NAFIN / BANCOMEXT · Patronato para el Desarrollo Agropecuario de Guanajuato",
        "sitio_web": "https://www.expoagrogto.com",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-010",
        "nombre": "PLASTIMAGEN México 2026",
        "descripcion": (
            "Exposición internacional líder de la industria del plástico en Latinoamérica con más de 870 expositores. "
            "Presenta maquinaria de inyección y extrusión, resinas de ingeniería, moldes de precisión, reciclaje "
            "circular y soluciones de empaque para las industrias automotriz, médica y de bienes de consumo, con "
            "líneas de arrendamiento y crédito de maquinaria industrial NAFIN/BANCOMEXT."
        ),
        "fecha_inicio": "2026-11-10",
        "fecha_fin": "2026-11-13",
        "horario": "12:00 - 19:00 hrs",
        "modalidad": "Presencial",
        "sede": "Centro Citibanamex",
        "direccion": "Av. del Conscripto 311, Lomas de Sotelo, Hipódromo de las Américas, Miguel Hidalgo, 11200 CDMX",
        "estado": "Ciudad de México",
        "municipio": "Miguel Hidalgo",
        "codigo_postal": "11200",
        "latitud": 19.4280,
        "longitud": -99.1480,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+Citibanamex+CDMX",
        "categoria": "Plásticos y Químicos",
        "promotor": "NAFIN / BANCOMEXT · ANIPAC / Tarsus",
        "sitio_web": "https://www.plastimagen.com.mx",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-011",
        "nombre": "Automotive Connection Potosí 2026",
        "descripcion": (
            "Encuentro de vinculación de negocios del Clúster Automotriz de San Luis Potosí que conecta armadoras (BMW, GM) "
            "y empresas Tier 1 con proveedores nacionales de manufactura, logística y servicios especializados. "
            "Incluye pabellón financiero para cadenas productivas y factoraje de proveedores NAFIN/BANCOMEXT."
        ),
        "fecha_inicio": "2026-11-11",
        "fecha_fin": "2026-11-12",
        "horario": "09:00 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Centro de Negocios Potosí (CNP)",
        "direccion": "Blvd. Antonio Rocha Cordero 125, Desarrollo del Pedregal, 78395 San Luis Potosí, S.L.P.",
        "estado": "San Luis Potosí",
        "municipio": "San Luis Potosí",
        "codigo_postal": "78395",
        "latitud": 22.1194,
        "longitud": -101.0028,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Centro+de+Convenciones+de+San+Luis+Potosi",
        "categoria": "Automotriz y Aeroespacial",
        "promotor": "NAFIN / BANCOMEXT · Clúster Automotriz de San Luis Potosí",
        "sitio_web": "https://clusterautomotrizslp.com",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-012",
        "nombre": "North America Automotive B2B Meetings 2026",
        "descripcion": (
            "Encuentro internacional de negocios B2B de la cadena de suministro automotriz de Norteamérica organizado en "
            "conjunto con CAPIM e INA. Enfocado en el cumplimiento de valor de contenido regional bajo el T-MEC, "
            "localización de autopartes críticas y financiamiento a proveedores nacionales con programas NAFIN/BANCOMEXT."
        ),
        "fecha_inicio": "2026-11-11",
        "fecha_fin": "2026-11-12",
        "horario": "09:00 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Complejo Tres Centurias (Ficotrece)",
        "direccion": "Av. Manuel Gómez Morín s/n, Barrio de la Estación, 20259 Aguascalientes, Ags.",
        "estado": "Aguascalientes",
        "municipio": "Aguascalientes",
        "codigo_postal": "20259",
        "latitud": 21.8891,
        "longitud": -102.2798,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Complejo+Tres+Centurias+Ficotrece+Aguascalientes",
        "categoria": "Automotriz y Aeroespacial",
        "promotor": "NAFIN / BANCOMEXT · CAPIM / Gobierno de Aguascalientes",
        "sitio_web": "https://www.capim.com.mx",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-013",
        "nombre": "Smart Technology Expo 2026",
        "descripcion": (
            "Exposición especializada en Industria 4.0, automatización industrial, robótica colaborativa, ciberseguridad "
            "industrial, inteligencia artificial aplicada a manufactura e IoT en Expo Guadalajara. Espacio estratégico "
            "para modernización tecnológica de plantas industriales respaldada por NAFIN/BANCOMEXT."
        ),
        "fecha_inicio": "2026-11-18",
        "fecha_fin": "2026-11-20",
        "horario": "11:00 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Expo Guadalajara",
        "direccion": "Av. Mariano Otero 1499, Verde Valle, 44550 Guadalajara, Jal.",
        "estado": "Jalisco",
        "municipio": "Guadalajara",
        "codigo_postal": "44550",
        "latitud": 20.6534,
        "longitud": -103.3913,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Expo+Guadalajara+Mariano+Otero",
        "categoria": "Semiconductores y Tech",
        "promotor": "NAFIN / BANCOMEXT · Smart Technology Expo",
        "sitio_web": "https://smarttechnologyexpo.mx",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-014",
        "nombre": "AIAG México Automotive Forum 2026",
        "descripcion": (
            "Foro ejecutivo de la Automotive Industry Action Group (AIAG) en colaboración con Plexus y el Clúster "
            "Automotriz de Nuevo León (CLAUT). Aborda estándares globales de calidad automotriz IATF 16949, "
            "descarbonización de alcance 3 (ESG), debida diligencia en cadena de suministro y financiamiento verde "
            "de BANCOMEXT para proveedores automotrices."
        ),
        "fecha_inicio": "2026-11-19",
        "fecha_fin": "2026-11-19",
        "horario": "08:30 - 17:30 hrs",
        "modalidad": "Presencial",
        "sede": "CINTERMEX Monterrey",
        "direccion": "Av. Fundidora 501, Col. Obrera, 64010 Monterrey, N.L.",
        "estado": "Nuevo León",
        "municipio": "Monterrey",
        "codigo_postal": "64010",
        "latitud": 25.6782,
        "longitud": -100.2877,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=CINTERMEX+Monterrey+Nuevo+Leon",
        "categoria": "Automotriz y Aeroespacial",
        "promotor": "BANCOMEXT / NAFIN · AIAG / CLAUT Nuevo León",
        "sitio_web": "https://www.aiag.org",
        "creado_por": "sistema.semilla",
    },
    {
        "id": "EVT-2026-015",
        "nombre": "Surface Finishing México (SFM) 2026",
        "descripcion": (
            "Congreso y exposición de la Asociación Mexicana de Industriales de Acabados Superficiales (AMAS). "
            "Especializado en galvanoplastia, anodizado, pintura electrostática, recubrimientos anticorrosivos "
            "y tratamientos superficiales de alta especificación para la industria automotriz, aeroespacial y de electrodomésticos."
        ),
        "fecha_inicio": "2026-11-24",
        "fecha_fin": "2026-11-25",
        "horario": "09:00 - 18:00 hrs",
        "modalidad": "Presencial",
        "sede": "Club de Industriales de Querétaro (CIQ)",
        "direccion": "Av. Estadio 112, Colinas del Cimatario, 76090 Santiago de Querétaro, Qro.",
        "estado": "Querétaro",
        "municipio": "Querétaro",
        "codigo_postal": "76090",
        "latitud": 20.5694,
        "longitud": -100.3712,
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Club+de+Industriales+de+Queretaro",
        "categoria": "Manufactura Avanzada",
        "promotor": "NAFIN / BANCOMEXT · AMAS Surface Finishing",
        "sitio_web": "https://surfacefinishingmx.com",
        "creado_por": "sistema.semilla",
    },
]

INITIAL_USERS: List[Dict[str, Any]] = [
    {
        "username": "admin.general",
        "nombre_completo": "Dir. Gral. Adjunto de Coordinación Institucional",
        "cargo": "Administrador Central de Plataforma",
        "email": "coordinacion.eventos@bancomext.gob.mx",
        "telefono_whatsapp": "+525554499000",
        "password_plain": "Admin2026!",
        "rol": "admin",
        "recibe_email": 1,
        "recibe_gcal": 1,
        "recibe_whatsapp": 1,
        "filtro_estados": "TODOS",
        "filtro_categorias": "TODAS",
        "anticipacion_dias": "7,1,0",
    },
    {
        "username": "director.promocion",
        "nombre_completo": "Director Ejecutivo de Promoción Sectorial y Regional",
        "cargo": "Director de Banca de Empresas",
        "email": "direccion.promocion@bancomext.gob.mx",
        "telefono_whatsapp": "+525554499100",
        "password_plain": "Director2026!",
        "rol": "director",
        "recibe_email": 1,
        "recibe_gcal": 1,
        "recibe_whatsapp": 1,
        "filtro_estados": "Nuevo León,Ciudad de México,Jalisco,Guanajuato,Querétaro",
        "filtro_categorias": "Comercio Exterior,Automotriz y Aeroespacial,Semiconductores y Tech",
        "anticipacion_dias": "7,1",
    },
    {
        "username": "receptor.norte",
        "nombre_completo": "Subdirector Regional Norte y Frontera",
        "cargo": "Promotor Ejecutivo Regional",
        "email": "promocion.norte@nafin.gob.mx",
        "telefono_whatsapp": "+528183180000",
        "password_plain": "Receptor2026!",
        "rol": "receptor",
        "recibe_email": 1,
        "recibe_gcal": 1,
        "recibe_whatsapp": 0,
        "filtro_estados": "Nuevo León,Chihuahua,Baja California,San Luis Potosí",
        "filtro_categorias": "Automotriz y Aeroespacial,Dispositivos Médicos,Comercio Exterior",
        "anticipacion_dias": "7,1",
    },
    {
        "username": "consulta.ejecutiva",
        "nombre_completo": "Enlace de Consulta Institucional",
        "cargo": "Analista de Seguimiento Sectorial",
        "email": "consulta.agenda@bancomext.gob.mx",
        "telefono_whatsapp": "",
        "password_plain": "Consulta2026!",
        "rol": "visualizador",
        "recibe_email": 0,
        "recibe_gcal": 0,
        "recibe_whatsapp": 0,
        "filtro_estados": "TODOS",
        "filtro_categorias": "TODAS",
        "anticipacion_dias": "",
    },
]


def init_db(force: bool = False) -> None:
    """
    Inicializa de forma idempotente en Neon PostgreSQL:
    1) Extensión pgvector (CREATE EXTENSION IF NOT EXISTS vector;)
    2) Tablas productivas (eventos, usuarios, bitacora_notificaciones)
    3) Tabla de staging HITL (eventos_pendientes_ia) e índices HNSW
    4) Poblado semilla si las tablas están vacías
    Usa cortocircuito en memoria (_DB_INITIALIZED) y verificación rápida de esquema
    para evitar ejecutar 16 consultas DDL en cada recarga de Streamlit.
    """
    global _DB_INITIALIZED
    if _DB_INITIALIZED and not force:
        return

    engine = get_engine()
    if not force:
        try:
            with engine.connect() as conn:
                chk = conn.execute(
                    text(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM information_schema.tables
                             WHERE table_schema = 'public'
                               AND table_name IN ('eventos', 'usuarios', 'bitacora_notificaciones', 'eventos_pendientes_ia')) AS tables_ready,
                            (SELECT COUNT(*) FROM eventos) AS ev_cnt,
                            (SELECT COUNT(*) FROM usuarios) AS usr_cnt
                        """
                    )
                ).mappings().first()
                if chk and int(chk["tables_ready"] or 0) == 4 and int(chk["ev_cnt"] or 0) > 0 and int(chk["usr_cnt"] or 0) > 0:
                    _DB_INITIALIZED = True
                    return
        except Exception:
            pass

    now_str = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS eventos (
                    id TEXT PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    descripcion TEXT NOT NULL,
                    fecha_inicio TEXT NOT NULL,
                    fecha_fin TEXT NOT NULL,
                    horario TEXT NOT NULL,
                    modalidad TEXT NOT NULL,
                    sede TEXT DEFAULT '',
                    direccion TEXT DEFAULT '',
                    estado TEXT DEFAULT 'Ciudad de México',
                    estado_id TEXT DEFAULT 'MX-CMX',
                    municipio TEXT DEFAULT '',
                    codigo_postal TEXT DEFAULT '',
                    latitud DOUBLE PRECISION DEFAULT 19.4326,
                    longitud DOUBLE PRECISION DEFAULT -99.1332,
                    google_maps_url TEXT DEFAULT '',
                    categoria TEXT NOT NULL,
                    promotor TEXT DEFAULT 'NAFIN / BANCOMEXT',
                    sitio_web TEXT DEFAULT '',
                    creado_por TEXT DEFAULT 'sistema',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    huella_semantica vector(768)
                );
                """
            )
        )

        conn.execute(
            text("ALTER TABLE eventos ADD COLUMN IF NOT EXISTS huella_semantica vector(768);")
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    nombre_completo TEXT NOT NULL,
                    cargo TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    telefono_whatsapp TEXT DEFAULT '',
                    password_hash TEXT NOT NULL,
                    rol TEXT NOT NULL CHECK(rol IN ('visualizador', 'receptor', 'director', 'admin')),
                    recibe_email INTEGER DEFAULT 1,
                    recibe_gcal INTEGER DEFAULT 1,
                    recibe_whatsapp INTEGER DEFAULT 0,
                    filtro_estados TEXT DEFAULT 'TODOS',
                    filtro_categorias TEXT DEFAULT 'TODAS',
                    anticipacion_dias TEXT DEFAULT '7,1,0',
                    activo INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS bitacora_notificaciones (
                    id SERIAL PRIMARY KEY,
                    evento_id TEXT NOT NULL,
                    usuario_id INTEGER NOT NULL,
                    destinatario_email TEXT NOT NULL,
                    canal TEXT NOT NULL,
                    tipo_disparo TEXT NOT NULL,
                    estatus_envio TEXT NOT NULL,
                    hash_version TEXT NOT NULL,
                    fecha_envio TEXT NOT NULL
                );
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS eventos_pendientes_ia (
                    staging_id TEXT PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    descripcion TEXT,
                    categoria TEXT NOT NULL,
                    fecha_inicio DATE NOT NULL,
                    fecha_fin DATE NOT NULL,
                    horario TEXT,
                    modalidad TEXT NOT NULL DEFAULT 'Presencial',
                    sede TEXT,
                    direccion TEXT,
                    estado TEXT,
                    estado_id TEXT,
                    municipio TEXT,
                    codigo_postal TEXT,
                    latitud DOUBLE PRECISION,
                    longitud DOUBLE PRECISION,
                    google_maps_url TEXT,
                    promotor TEXT,
                    sitio_web TEXT NOT NULL,
                    fuente_url TEXT NOT NULL,
                    origen_carril TEXT NOT NULL,
                    evidencia_texto TEXT NOT NULL,
                    confianza_score DOUBLE PRECISION NOT NULL,
                    campos_faltantes JSONB NOT NULL DEFAULT '[]'::jsonb,
                    huella_semantica vector(768),
                    posible_duplicado_id TEXT,
                    similitud_maxima DOUBLE PRECISION DEFAULT 0.0,
                    estatus TEXT NOT NULL DEFAULT 'pendiente',
                    motivo_descarte TEXT,
                    revisado_por TEXT,
                    evento_oficial_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMPTZ
                );
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_staging_estatus_fecha
                    ON eventos_pendientes_ia(estatus, fecha_inicio, estado_id);
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_staging_fuente_fecha_unica
                    ON eventos_pendientes_ia(fuente_url, fecha_inicio);
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_staging_vector_hnsw
                    ON eventos_pendientes_ia USING hnsw (huella_semantica vector_cosine_ops);
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_eventos_vector_hnsw
                    ON eventos USING hnsw (huella_semantica vector_cosine_ops);
                """
            )
        )

        ev_count = conn.execute(text("SELECT COUNT(*) FROM eventos")).scalar() or 0
        if ev_count == 0:
            for ev in INITIAL_EVENTS:
                st_norm = normalize_state_name(ev["estado"])
                st_id = get_state_id(st_norm)
                conn.execute(
                    text(
                        """
                        INSERT INTO eventos (
                            id, nombre, descripcion, fecha_inicio, fecha_fin, horario,
                            modalidad, sede, direccion, estado, estado_id, municipio,
                            codigo_postal, latitud, longitud, google_maps_url,
                            categoria, promotor, sitio_web, creado_por, created_at, updated_at
                        ) VALUES (
                            :id, :nombre, :descripcion, :fecha_inicio, :fecha_fin, :horario,
                            :modalidad, :sede, :direccion, :estado, :estado_id, :municipio,
                            :codigo_postal, :latitud, :longitud, :google_maps_url,
                            :categoria, :promotor, :sitio_web, :creado_por, :created_at, :updated_at
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": ev["id"],
                        "nombre": ev["nombre"].strip(),
                        "descripcion": ev["descripcion"].strip(),
                        "fecha_inicio": ev["fecha_inicio"],
                        "fecha_fin": ev["fecha_fin"],
                        "horario": ev["horario"],
                        "modalidad": ev["modalidad"],
                        "sede": ev["sede"].strip(),
                        "direccion": ev["direccion"].strip(),
                        "estado": st_norm,
                        "estado_id": st_id,
                        "municipio": ev["municipio"].strip(),
                        "codigo_postal": ev["codigo_postal"],
                        "latitud": float(ev["latitud"]),
                        "longitud": float(ev["longitud"]),
                        "google_maps_url": ev["google_maps_url"],
                        "categoria": ev["categoria"],
                        "promotor": ev["promotor"],
                        "sitio_web": ev["sitio_web"],
                        "creado_por": ev["creado_por"],
                        "created_at": now_str,
                        "updated_at": now_str,
                    },
                )

        u_count = conn.execute(text("SELECT COUNT(*) FROM usuarios")).scalar() or 0
        if u_count == 0:
            for u in INITIAL_USERS:
                conn.execute(
                    text(
                        """
                        INSERT INTO usuarios (
                            username, nombre_completo, cargo, email, telefono_whatsapp,
                            password_hash, rol, recibe_email, recibe_gcal, recibe_whatsapp,
                            filtro_estados, filtro_categorias, anticipacion_dias, activo, created_at
                        ) VALUES (
                            :username, :nombre_completo, :cargo, :email, :telefono_whatsapp,
                            :password_hash, :rol, :recibe_email, :recibe_gcal, :recibe_whatsapp,
                            :filtro_estados, :filtro_categorias, :anticipacion_dias, 1, :created_at
                        ) ON CONFLICT (username) DO NOTHING
                        """
                    ),
                    {
                        "username": u["username"],
                        "nombre_completo": u["nombre_completo"],
                        "cargo": u["cargo"],
                        "email": u["email"],
                        "telefono_whatsapp": u["telefono_whatsapp"],
                        "password_hash": hash_password(u["password_plain"]),
                        "rol": u["rol"],
                        "recibe_email": int(u["recibe_email"]),
                        "recibe_gcal": int(u["recibe_gcal"]),
                        "recibe_whatsapp": int(u["recibe_whatsapp"]),
                        "filtro_estados": u["filtro_estados"],
                        "filtro_categorias": u["filtro_categorias"],
                        "anticipacion_dias": u["anticipacion_dias"],
                        "created_at": now_str,
                    },
                )

        log_count = conn.execute(text("SELECT COUNT(*) FROM bitacora_notificaciones")).scalar() or 0
        if log_count == 0:
            sample_logs = [
                ("EVT-2026-002", 2, "direccion.promocion@bancomext.gob.mx", "Google Calendar (.ics)", "Recordatorio T-24h", "Aceptado en Calendario", "v1-EVT-2026-002", "26/10/2026 08:30"),
                ("EVT-2026-002", 3, "promocion.norte@nafin.gob.mx", "Google Calendar (.ics)", "Recordatorio T-24h", "Aceptado en Calendario", "v1-EVT-2026-002", "26/10/2026 08:30"),
                ("EVT-2026-003", 2, "direccion.promocion@bancomext.gob.mx", "Correo Institucional", "Inicio de Evento (Hoy)", "Entregado", "v1-EVT-2026-003", "27/10/2026 07:00"),
                ("EVT-2026-004", 3, "promocion.norte@nafin.gob.mx", "Google Calendar (.ics)", "Recordatorio T-24h", "Entregado", "v1-EVT-2026-004", "28/10/2026 08:00"),
                ("EVT-2026-005", 2, "direccion.promocion@bancomext.gob.mx", "Google Calendar (.ics)", "Recordatorio T-7d", "Aceptado en Calendario", "v1-EVT-2026-005", "26/10/2026 09:00"),
            ]
            for row in sample_logs:
                conn.execute(
                    text(
                        """
                        INSERT INTO bitacora_notificaciones (
                            evento_id, usuario_id, destinatario_email, canal, tipo_disparo,
                            estatus_envio, hash_version, fecha_envio
                        ) VALUES (
                            :evento_id, :usuario_id, :destinatario_email, :canal, :tipo_disparo,
                            :estatus_envio, :hash_version, :fecha_envio
                        )
                        """
                    ),
                    {
                        "evento_id": row[0],
                        "usuario_id": row[1],
                        "destinatario_email": row[2],
                        "canal": row[3],
                        "tipo_disparo": row[4],
                        "estatus_envio": row[5],
                        "hash_version": row[6],
                        "fecha_envio": row[7],
                    },
                )

        # Asegurar calibración de coordenadas dentro de polígonos de CDMX
        conn.execute(
            text("UPDATE eventos SET latitud = 19.3350, longitud = -99.1950 WHERE id = 'EVT-2026-001'")
        )
        conn.execute(
            text("UPDATE eventos SET latitud = 19.4280, longitud = -99.1480 WHERE id = 'EVT-2026-010'")
        )

    _DB_INITIALIZED = True
    invalidate_cache()
    export_events_json_snapshot()


def export_events_json_snapshot() -> None:
    """Mantiene una copia espejo legible en JSON de todos los eventos registrados."""
    events = list_events()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEED_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def list_events() -> List[Dict[str, Any]]:
    cached = _cache_get("list_events")
    if cached is not None:
        return [dict(r) for r in cached]

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT {EVENTOS_PUBLIC_COLUMNS} FROM eventos "
                "ORDER BY fecha_inicio ASC, fecha_fin ASC, id ASC"
            )
        ).mappings().all()
    res = [_serialize_row(r) for r in rows]
    _cache_set("list_events", res)
    return [dict(r) for r in res]


def get_event_by_id(event_id: str) -> Optional[Dict[str, Any]]:
    cached_list = _cache_get("list_events")
    if cached_list is not None:
        for r in cached_list:
            if r.get("id") == event_id:
                return dict(r)

    cache_key = f"event:{event_id}"
    cached_item = _cache_get(cache_key)
    if cached_item is not None:
        return dict(cached_item)

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {EVENTOS_PUBLIC_COLUMNS} FROM eventos WHERE id = :id"),
            {"id": event_id},
        ).mappings().first()
    if not row:
        return None
    res = _serialize_row(row)
    _cache_set(cache_key, res)
    return dict(res)


def generate_next_event_id() -> str:
    events = list_events()
    max_num = 15
    for r in events:
        parts = str(r.get("id", "")).split("-")
        if parts and parts[-1].isdigit():
            max_num = max(max_num, int(parts[-1]))
    year_mx = get_mexico_today().year
    return f"EVT-{year_mx}-{max_num + 1:03d}"


def create_event(
    payload: Dict[str, Any],
    created_by: str = "director",
    huella_semantica: Optional[List[float]] = None,
) -> str:
    event_id = payload.get("id") or generate_next_event_id()
    now_str = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    modalidad = payload.get("modalidad", "Presencial")
    if modalidad in ("En Línea", "Virtual"):
        st_norm = "En Línea" if modalidad == "En Línea" else "Ciudad de México"
        st_id = "ONLINE"
    else:
        st_norm = normalize_state_name(payload.get("estado", "Ciudad de México"))
        st_id = get_state_id(st_norm)

    vec_literal = _format_vector_literal(huella_semantica)
    engine = get_engine()
    with engine.begin() as conn:
        if vec_literal:
            conn.execute(
                text(
                    """
                    INSERT INTO eventos (
                        id, nombre, descripcion, fecha_inicio, fecha_fin, horario,
                        modalidad, sede, direccion, estado, estado_id, municipio,
                        codigo_postal, latitud, longitud, google_maps_url,
                        categoria, promotor, sitio_web, creado_por, created_at, updated_at,
                        huella_semantica
                    ) VALUES (
                        :id, :nombre, :descripcion, :fecha_inicio, :fecha_fin, :horario,
                        :modalidad, :sede, :direccion, :estado, :estado_id, :municipio,
                        :codigo_postal, :latitud, :longitud, :google_maps_url,
                        :categoria, :promotor, :sitio_web, :creado_por, :created_at, :updated_at,
                        CAST(:huella_semantica AS vector)
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        nombre = EXCLUDED.nombre,
                        descripcion = EXCLUDED.descripcion,
                        fecha_inicio = EXCLUDED.fecha_inicio,
                        fecha_fin = EXCLUDED.fecha_fin,
                        horario = EXCLUDED.horario,
                        modalidad = EXCLUDED.modalidad,
                        sede = EXCLUDED.sede,
                        direccion = EXCLUDED.direccion,
                        estado = EXCLUDED.estado,
                        estado_id = EXCLUDED.estado_id,
                        municipio = EXCLUDED.municipio,
                        codigo_postal = EXCLUDED.codigo_postal,
                        latitud = EXCLUDED.latitud,
                        longitud = EXCLUDED.longitud,
                        google_maps_url = EXCLUDED.google_maps_url,
                        categoria = EXCLUDED.categoria,
                        promotor = EXCLUDED.promotor,
                        sitio_web = EXCLUDED.sitio_web,
                        creado_por = EXCLUDED.creado_por,
                        updated_at = EXCLUDED.updated_at,
                        huella_semantica = EXCLUDED.huella_semantica
                    """
                ),
                {
                    "id": event_id,
                    "nombre": str(payload["nombre"]).strip(),
                    "descripcion": str(payload["descripcion"]).strip(),
                    "fecha_inicio": str(payload["fecha_inicio"])[:10],
                    "fecha_fin": str(payload["fecha_fin"])[:10],
                    "horario": str(payload.get("horario") or "09:00 - 18:00 hrs").strip(),
                    "modalidad": modalidad,
                    "sede": str(payload.get("sede") or "").strip(),
                    "direccion": str(payload.get("direccion") or "").strip(),
                    "estado": st_norm,
                    "estado_id": st_id,
                    "municipio": str(payload.get("municipio") or "").strip(),
                    "codigo_postal": str(payload.get("codigo_postal") or "").strip(),
                    "latitud": float(payload.get("latitud", 19.4326)),
                    "longitud": float(payload.get("longitud", -99.1332)),
                    "google_maps_url": str(payload.get("google_maps_url") or "").strip(),
                    "categoria": payload.get("categoria", CATEGORIES[0]),
                    "promotor": str(payload.get("promotor") or "NAFIN / BANCOMEXT").strip(),
                    "sitio_web": str(payload.get("sitio_web") or "").strip(),
                    "creado_por": created_by,
                    "created_at": now_str,
                    "updated_at": now_str,
                    "huella_semantica": vec_literal,
                },
            )
        else:
            conn.execute(
                text(
                    """
                    INSERT INTO eventos (
                        id, nombre, descripcion, fecha_inicio, fecha_fin, horario,
                        modalidad, sede, direccion, estado, estado_id, municipio,
                        codigo_postal, latitud, longitud, google_maps_url,
                        categoria, promotor, sitio_web, creado_por, created_at, updated_at
                    ) VALUES (
                        :id, :nombre, :descripcion, :fecha_inicio, :fecha_fin, :horario,
                        :modalidad, :sede, :direccion, :estado, :estado_id, :municipio,
                        :codigo_postal, :latitud, :longitud, :google_maps_url,
                        :categoria, :promotor, :sitio_web, :creado_por, :created_at, :updated_at
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        nombre = EXCLUDED.nombre,
                        descripcion = EXCLUDED.descripcion,
                        fecha_inicio = EXCLUDED.fecha_inicio,
                        fecha_fin = EXCLUDED.fecha_fin,
                        horario = EXCLUDED.horario,
                        modalidad = EXCLUDED.modalidad,
                        sede = EXCLUDED.sede,
                        direccion = EXCLUDED.direccion,
                        estado = EXCLUDED.estado,
                        estado_id = EXCLUDED.estado_id,
                        municipio = EXCLUDED.municipio,
                        codigo_postal = EXCLUDED.codigo_postal,
                        latitud = EXCLUDED.latitud,
                        longitud = EXCLUDED.longitud,
                        google_maps_url = EXCLUDED.google_maps_url,
                        categoria = EXCLUDED.categoria,
                        promotor = EXCLUDED.promotor,
                        sitio_web = EXCLUDED.sitio_web,
                        creado_por = EXCLUDED.creado_por,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "id": event_id,
                    "nombre": str(payload["nombre"]).strip(),
                    "descripcion": str(payload["descripcion"]).strip(),
                    "fecha_inicio": str(payload["fecha_inicio"])[:10],
                    "fecha_fin": str(payload["fecha_fin"])[:10],
                    "horario": str(payload.get("horario") or "09:00 - 18:00 hrs").strip(),
                    "modalidad": modalidad,
                    "sede": str(payload.get("sede") or "").strip(),
                    "direccion": str(payload.get("direccion") or "").strip(),
                    "estado": st_norm,
                    "estado_id": st_id,
                    "municipio": str(payload.get("municipio") or "").strip(),
                    "codigo_postal": str(payload.get("codigo_postal") or "").strip(),
                    "latitud": float(payload.get("latitud", 19.4326)),
                    "longitud": float(payload.get("longitud", -99.1332)),
                    "google_maps_url": str(payload.get("google_maps_url") or "").strip(),
                    "categoria": payload.get("categoria", CATEGORIES[0]),
                    "promotor": str(payload.get("promotor") or "NAFIN / BANCOMEXT").strip(),
                    "sitio_web": str(payload.get("sitio_web") or "").strip(),
                    "creado_por": created_by,
                    "created_at": now_str,
                    "updated_at": now_str,
                },
            )
    invalidate_cache()
    export_events_json_snapshot()
    return event_id


def delete_event(event_id: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM eventos WHERE id = :id"), {"id": event_id})
    invalidate_cache()
    export_events_json_snapshot()


def authenticate_user(username_or_email: str, password_plain: str) -> Optional[Dict[str, Any]]:
    cleaned = (username_or_email or "").strip().lower()
    pwd_hash = hash_password(password_plain or "")
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT * FROM usuarios
                WHERE (LOWER(username) = :cleaned OR LOWER(email) = :cleaned)
                  AND password_hash = :pwd_hash
                  AND activo = 1
                """
            ),
            {"cleaned": cleaned, "pwd_hash": pwd_hash},
        ).mappings().first()
    return _serialize_row(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    cached = _cache_get("list_users")
    if cached is not None:
        return [dict(r) for r in cached]

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM usuarios ORDER BY id ASC")).mappings().all()
    res = [_serialize_row(r) for r in rows]
    _cache_set("list_users", res)
    return [dict(r) for r in res]


def create_user_one_by_one(payload: Dict[str, Any]) -> Tuple[bool, str]:
    username = payload.get("username", "").strip().lower()
    email = payload.get("email", "").strip().lower()
    nombre = payload.get("nombre_completo", "").strip()
    cargo = payload.get("cargo", "").strip() or "Ejecutivo Institucional"
    rol = payload.get("rol", "visualizador").strip().lower()
    password_plain = payload.get("password_plain", "").strip() or "Bancomext2026!"

    if not username or not email or not nombre:
        return False, "Completa usuario, nombre completo y correo institucional."
    if rol not in ROLES_CONFIG:
        return False, "Nivel de permiso inválido."

    now_str = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO usuarios (
                        username, nombre_completo, cargo, email, telefono_whatsapp,
                        password_hash, rol, recibe_email, recibe_gcal, recibe_whatsapp,
                        filtro_estados, filtro_categorias, anticipacion_dias, activo, created_at
                    ) VALUES (
                        :username, :nombre, :cargo, :email, :telefono_whatsapp,
                        :password_hash, :rol, :recibe_email, :recibe_gcal, :recibe_whatsapp,
                        :filtro_estados, :filtro_categorias, :anticipacion_dias, 1, :created_at
                    )
                    """
                ),
                {
                    "username": username,
                    "nombre": nombre,
                    "cargo": cargo,
                    "email": email,
                    "telefono_whatsapp": payload.get("telefono_whatsapp", "").strip(),
                    "password_hash": hash_password(password_plain),
                    "rol": rol,
                    "recibe_email": int(bool(payload.get("recibe_email", rol != "visualizador"))),
                    "recibe_gcal": int(bool(payload.get("recibe_gcal", rol != "visualizador"))),
                    "recibe_whatsapp": int(bool(payload.get("recibe_whatsapp", False))),
                    "filtro_estados": payload.get("filtro_estados", "TODOS"),
                    "filtro_categorias": payload.get("filtro_categorias", "TODAS"),
                    "anticipacion_dias": payload.get(
                        "anticipacion_dias", "7,1" if rol != "visualizador" else ""
                    ),
                    "created_at": now_str,
                },
            )
        invalidate_cache()
        return True, f"Usuario '{username}' registrado con rol {ROLES_CONFIG[rol]['nombre']}."
    except IntegrityError:
        return False, "El nombre de usuario o correo ya existe en el padrón."


def update_user_role(user_id: int, new_rol: str, activo: int = 1) -> None:
    if new_rol not in ROLES_CONFIG:
        return
    recibe_default = 1 if ROLES_CONFIG[new_rol]["recibe_alertas"] else 0
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE usuarios
                SET rol = :rol, activo = :activo, recibe_email = :recibe, recibe_gcal = :recibe
                WHERE id = :id
                """
            ),
            {
                "rol": new_rol,
                "activo": int(activo),
                "recibe": recibe_default,
                "id": int(user_id),
            },
        )
    invalidate_cache()


def log_notification_dispatch(
    evento_id: str,
    usuario_id: int,
    destinatario_email: str,
    canal: str,
    tipo_disparo: str = "Despacho Manual (.ics)",
) -> None:
    now_mx = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bitacora_notificaciones (
                    evento_id, usuario_id, destinatario_email, canal,
                    tipo_disparo, estatus_envio, hash_version, fecha_envio
                ) VALUES (
                    :evento_id, :usuario_id, :destinatario_email, :canal,
                    :tipo_disparo, 'Entregado', :hash_version, :fecha_envio
                )
                """
            ),
            {
                "evento_id": evento_id,
                "usuario_id": int(usuario_id),
                "destinatario_email": destinatario_email,
                "canal": canal,
                "tipo_disparo": tipo_disparo,
                "hash_version": f"v1-{evento_id}",
                "fecha_envio": now_mx,
            },
        )
    invalidate_cache()


def list_notification_logs(limit: int = 25) -> List[Dict[str, Any]]:
    cache_key = f"logs:{int(limit)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return [dict(r) for r in cached]

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT b.*, e.nombre AS evento_nombre, u.nombre_completo, u.rol
                FROM bitacora_notificaciones b
                LEFT JOIN eventos e ON e.id = b.evento_id
                LEFT JOIN usuarios u ON u.id = b.usuario_id
                ORDER BY b.id DESC
                LIMIT :limit
                """
            ),
            {"limit": int(limit)},
        ).mappings().all()
    res = [_serialize_row(r) for r in rows]
    _cache_set(cache_key, res)
    return [dict(r) for r in res]


# ===========================================================================
# FUNCIONES HUMAN-IN-THE-LOOP (STAGING IA: eventos_pendientes_ia)
# ===========================================================================
def generate_staging_id(nombre: str, fecha_inicio: str, fuente_url: str = "") -> str:
    """Genera ID de staging determinístico con formato STG-YYYYMMDD-XXXX."""
    date_part = str(fecha_inicio).replace("-", "")[:8]
    if len(date_part) != 8 or not date_part.isdigit():
        date_part = get_mexico_today().strftime("%Y%m%d")
    raw_seed = f"{nombre.strip().lower()}|{fecha_inicio}|{fuente_url.strip().lower()}"
    short_hash = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:4].upper()
    return f"STG-{date_part}-{short_hash}"


def list_staging_events(estatus: Optional[str] = "pendiente") -> List[Dict[str, Any]]:
    """
    Lista los eventos en la bandeja de entrada IA ordenados por confianza_score DESC, fecha_inicio ASC.
    """
    cache_key = f"staging_list:{estatus or 'ALL'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return [dict(r) for r in cached]

    engine = get_engine()
    with engine.connect() as conn:
        if estatus:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        staging_id, nombre, descripcion, categoria,
                        fecha_inicio, fecha_fin, horario, modalidad,
                        sede, direccion, estado, estado_id, municipio,
                        codigo_postal, latitud, longitud, google_maps_url,
                        promotor, sitio_web, fuente_url, origen_carril,
                        evidencia_texto, confianza_score, campos_faltantes,
                        posible_duplicado_id, similitud_maxima, estatus,
                        motivo_descarte, revisado_por, evento_oficial_id,
                        created_at, reviewed_at
                    FROM eventos_pendientes_ia
                    WHERE estatus = :estatus
                    ORDER BY confianza_score DESC, fecha_inicio ASC, staging_id ASC
                    """
                ),
                {"estatus": estatus},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        staging_id, nombre, descripcion, categoria,
                        fecha_inicio, fecha_fin, horario, modalidad,
                        sede, direccion, estado, estado_id, municipio,
                        codigo_postal, latitud, longitud, google_maps_url,
                        promotor, sitio_web, fuente_url, origen_carril,
                        evidencia_texto, confianza_score, campos_faltantes,
                        posible_duplicado_id, similitud_maxima, estatus,
                        motivo_descarte, revisado_por, evento_oficial_id,
                        created_at, reviewed_at
                    FROM eventos_pendientes_ia
                    ORDER BY confianza_score DESC, fecha_inicio ASC, staging_id ASC
                    """
                )
            ).mappings().all()

    result = []
    for r in rows:
        item = _serialize_row(r)
        cf = item.get("campos_faltantes")
        if isinstance(cf, str):
            try:
                item["campos_faltantes"] = json.loads(cf)
            except Exception:
                item["campos_faltantes"] = []
        elif cf is None:
            item["campos_faltantes"] = []
        result.append(item)
    _cache_set(cache_key, result)
    return [dict(r) for r in result]


def get_staging_event_by_id(staging_id: str) -> Optional[Dict[str, Any]]:
    for list_key in ("staging_list:pendiente", "staging_list:ALL"):
        cached_list = _cache_get(list_key)
        if cached_list is not None:
            for r in cached_list:
                if r.get("staging_id") == staging_id:
                    return dict(r)

    cache_key = f"staging_item:{staging_id}"
    cached_item = _cache_get(cache_key)
    if cached_item is not None:
        return dict(cached_item)

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    staging_id, nombre, descripcion, categoria,
                    fecha_inicio, fecha_fin, horario, modalidad,
                    sede, direccion, estado, estado_id, municipio,
                    codigo_postal, latitud, longitud, google_maps_url,
                    promotor, sitio_web, fuente_url, origen_carril,
                    evidencia_texto, confianza_score, campos_faltantes,
                    posible_duplicado_id, similitud_maxima, estatus,
                    motivo_descarte, revisado_por, evento_oficial_id,
                    created_at, reviewed_at
                FROM eventos_pendientes_ia
                WHERE staging_id = :staging_id
                """
            ),
            {"staging_id": staging_id},
        ).mappings().first()
    if not row:
        return None
    item = _serialize_row(row)
    cf = item.get("campos_faltantes")
    if isinstance(cf, str):
        try:
            item["campos_faltantes"] = json.loads(cf)
        except Exception:
            item["campos_faltantes"] = []
    elif cf is None:
        item["campos_faltantes"] = []
    _cache_set(cache_key, item)
    return dict(item)


def get_staging_kpis() -> Dict[str, int]:
    """
    Calcula los 4 KPIs ejecutivos de la Bandeja de Entrada IA:
    - total_pendientes
    - confianza_alta (score >= 0.80 y pendiente)
    - alertas_duplicado (posible_duplicado_id IS NOT NULL y pendiente)
    - aprobados_mes (estatus = 'aprobado' en el mes actual)
    """
    cached = _cache_get("staging_kpis")
    if cached is not None:
        return dict(cached)

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE estatus = 'pendiente') AS total_pendientes,
                    COUNT(*) FILTER (WHERE estatus = 'pendiente' AND confianza_score >= 0.80) AS confianza_alta,
                    COUNT(*) FILTER (WHERE estatus = 'pendiente' AND posible_duplicado_id IS NOT NULL) AS alertas_duplicado,
                    COUNT(*) FILTER (
                        WHERE estatus = 'aprobado'
                          AND reviewed_at >= date_trunc('month', CURRENT_TIMESTAMP)
                    ) AS aprobados_mes
                FROM eventos_pendientes_ia
                """
            )
        ).mappings().first()
    if not row:
        res = {
            "total_pendientes": 0,
            "confianza_alta": 0,
            "alertas_duplicado": 0,
            "aprobados_mes": 0,
        }
    else:
        res = {
            "total_pendientes": int(row["total_pendientes"] or 0),
            "confianza_alta": int(row["confianza_alta"] or 0),
            "alertas_duplicado": int(row["alertas_duplicado"] or 0),
            "aprobados_mes": int(row["aprobados_mes"] or 0),
        }
    _cache_set("staging_kpis", res)
    return dict(res)


def insert_staging_event(payload: Dict[str, Any]) -> str:
    """
    Inserta un candidato validado en eventos_pendientes_ia con estatus = 'pendiente'.
    """
    staging_id = payload.get("staging_id") or generate_staging_id(
        payload["nombre"],
        str(payload["fecha_inicio"]),
        payload.get("fuente_url", ""),
    )
    campos_faltantes = payload.get("campos_faltantes", [])
    if not isinstance(campos_faltantes, str):
        campos_faltantes_json = json.dumps(campos_faltantes, ensure_ascii=False)
    else:
        campos_faltantes_json = campos_faltantes

    vec_literal = _format_vector_literal(payload.get("huella_semantica"))
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO eventos_pendientes_ia (
                    staging_id, nombre, descripcion, categoria,
                    fecha_inicio, fecha_fin, horario, modalidad,
                    sede, direccion, estado, estado_id, municipio,
                    codigo_postal, latitud, longitud, google_maps_url,
                    promotor, sitio_web, fuente_url, origen_carril,
                    evidencia_texto, confianza_score, campos_faltantes,
                    huella_semantica, posible_duplicado_id, similitud_maxima,
                    estatus
                ) VALUES (
                    :staging_id, :nombre, :descripcion, :categoria,
                    CAST(:fecha_inicio AS DATE), CAST(:fecha_fin AS DATE), :horario, :modalidad,
                    :sede, :direccion, :estado, :estado_id, :municipio,
                    :codigo_postal, :latitud, :longitud, :google_maps_url,
                    :promotor, :sitio_web, :fuente_url, :origen_carril,
                    :evidencia_texto, :confianza_score, CAST(:campos_faltantes AS JSONB),
                    CAST(:huella_semantica AS vector), :posible_duplicado_id, :similitud_maxima,
                    'pendiente'
                )
                ON CONFLICT (fuente_url, fecha_inicio) DO NOTHING
                """
            ),
            {
                "staging_id": staging_id,
                "nombre": str(payload["nombre"]).strip(),
                "descripcion": str(payload.get("descripcion") or "").strip(),
                "categoria": payload["categoria"],
                "fecha_inicio": str(payload["fecha_inicio"])[:10],
                "fecha_fin": str(payload["fecha_fin"])[:10],
                "horario": payload.get("horario"),
                "modalidad": payload.get("modalidad", "Presencial"),
                "sede": payload.get("sede"),
                "direccion": payload.get("direccion"),
                "estado": payload.get("estado"),
                "estado_id": payload.get("estado_id"),
                "municipio": payload.get("municipio"),
                "codigo_postal": payload.get("codigo_postal"),
                "latitud": float(payload["latitud"]) if payload.get("latitud") is not None else None,
                "longitud": float(payload["longitud"]) if payload.get("longitud") is not None else None,
                "google_maps_url": payload.get("google_maps_url"),
                "promotor": payload.get("promotor"),
                "sitio_web": str(payload.get("sitio_web") or payload.get("fuente_url") or "").strip(),
                "fuente_url": str(payload["fuente_url"]).strip(),
                "origen_carril": payload.get("origen_carril", "semilla_oficial"),
                "evidencia_texto": str(payload["evidencia_texto"]).strip(),
                "confianza_score": float(payload.get("confianza_score", 0.5)),
                "campos_faltantes": campos_faltantes_json,
                "huella_semantica": vec_literal,
                "posible_duplicado_id": payload.get("posible_duplicado_id"),
                "similitud_maxima": float(payload.get("similitud_maxima", 0.0)),
            },
        )
    invalidate_cache()
    return staging_id


def approve_staging_event(
    staging_id: str,
    event_payload: Dict[str, Any],
    reviewed_by: str = "director",
) -> str:
    """
    Aprueba un evento de eventos_pendientes_ia (Flujo HITL L3 Director / L4 Admin):
    1) Genera el nuevo ID oficial EVT-YYYY-NNN.
    2) Inserta el registro definitivo en eventos conservando su huella_semantica.
    3) Actualiza eventos_pendientes_ia con estatus = 'aprobado', revisado_por, evento_oficial_id y reviewed_at.
    4) Dispara el flujo institucional de notificación (.ics y correo) a usuarios con recibe_alertas (Receptor L2+, etc.).
    """
    new_event_id = (event_payload.get("id") or "").strip() or generate_next_event_id()
    event_payload = {**event_payload, "id": new_event_id}

    created_id = create_event(event_payload, created_by=reviewed_by)

    engine = get_engine()
    with engine.begin() as conn:
        # Copiar huella_semantica desde staging hacia el evento oficial
        conn.execute(
            text(
                """
                UPDATE eventos e
                SET huella_semantica = s.huella_semantica
                FROM eventos_pendientes_ia s
                WHERE e.id = :evento_id
                  AND s.staging_id = :staging_id
                  AND s.huella_semantica IS NOT NULL
                """
            ),
            {"evento_id": created_id, "staging_id": staging_id},
        )

        # Marcar candidato en staging como aprobado
        conn.execute(
            text(
                """
                UPDATE eventos_pendientes_ia
                SET estatus = 'aprobado',
                    revisado_por = :revisado_por,
                    evento_oficial_id = :evento_oficial_id,
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE staging_id = :staging_id
                """
            ),
            {
                "staging_id": staging_id,
                "revisado_por": reviewed_by,
                "evento_oficial_id": created_id,
            },
        )

    # Disparar flujo institucional de notificación (.ics y correo) a usuarios suscritos
    users = list_users()
    for u in users:
        if int(u.get("activo", 1)) == 1 and ROLES_CONFIG.get(u.get("rol", ""), {}).get("recibe_alertas"):
            if int(u.get("recibe_gcal", 1)) == 1:
                log_notification_dispatch(
                    evento_id=created_id,
                    usuario_id=int(u["id"]),
                    destinatario_email=u["email"],
                    canal="Google Calendar (.ics)",
                    tipo_disparo="Alta Curaduría IA (HITL)",
                )
            if int(u.get("recibe_email", 1)) == 1:
                log_notification_dispatch(
                    evento_id=created_id,
                    usuario_id=int(u["id"]),
                    destinatario_email=u["email"],
                    canal="Correo Institucional",
                    tipo_disparo="Alta Curaduría IA (HITL)",
                )

    invalidate_cache()
    return created_id


def discard_staging_event(
    staging_id: str,
    motivo_descarte: str,
    reviewed_by: str = "director",
) -> None:
    """
    Marca un evento candidato como 'descartado' en eventos_pendientes_ia sin eliminarlo (DELETE prohibido),
    preservando su huella_semantica para que la Memoria Negativa bloquee futuras sugerencias similares.
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE eventos_pendientes_ia
                SET estatus = 'descartado',
                    motivo_descarte = :motivo_descarte,
                    revisado_por = :revisado_por,
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE staging_id = :staging_id
                """
            ),
            {
                "staging_id": staging_id,
                "motivo_descarte": motivo_descarte,
                "revisado_por": reviewed_by,
            },
        )
    invalidate_cache()
