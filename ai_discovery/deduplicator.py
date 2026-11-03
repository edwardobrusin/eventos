"""
Embudo de deduplicación de 3 niveles con pgvector (768 dimensiones) y Memoria Negativa
sobre Neon Serverless PostgreSQL.
"""

from __future__ import annotations

import difflib
import hashlib
import math
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import text

import database as db
from geo_utils import strip_accents


def normalize_canonical_url(url: Optional[str]) -> str:
    if not url:
        return ""
    raw = url.strip()
    try:
        parsed = urlparse(raw)
        if not parsed.netloc:
            return raw.lower().rstrip("/")
        netloc = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme.lower()}://{netloc}{path}"
    except Exception:
        return raw.lower().rstrip("/")


def build_huella_text(
    categoria: str,
    sede: Optional[str],
    promotor: Optional[str],
    nombre: str,
) -> str:
    """
    Construye la cadena normalizada para el vector de 768 dimensiones:
    f"{categoria} | {sede or ''} | {promotor or ''} | {nombre}".lower().strip()
    """
    return f"{categoria} | {sede or ''} | {promotor or ''} | {nombre}".lower().strip()


def _deterministic_fallback_embedding(text_input: str, dim: int = 768) -> List[float]:
    """
    Generador determinístico de 768 dimensiones por n-gramas (fallback sin red/API).
    """
    cleaned = strip_accents(text_input)
    vec = [0.0] * dim
    tokens = cleaned.split()
    features = list(tokens)
    for i in range(len(cleaned) - 2):
        features.append(cleaned[i : i + 3])
    if not features:
        features = ["bancomext"]

    for feat in features:
        digest = hashlib.sha256(feat.encode("utf-8")).digest()
        for j in range(0, 16, 2):
            idx = ((digest[j] << 8) | digest[j + 1]) % dim
            sign = 1.0 if (digest[(j + 2) % 32] % 2 == 0) else -1.0
            vec[idx] += sign

    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def generate_semantic_embedding(texto_huella: str) -> List[float]:
    """
    Genera el embedding semántico de 768 dimensiones usando Google GenAI SDK.
    Usa `gemini-embedding-001` con `output_dimensionality=768` (reemplazo vigente de
    `text-embedding-004` deprecado en el API) y fallback determinístico local.
    """
    api_key = db.get_secret("GEMINI_API_KEY") or db.get_secret("GOOGLE_API_KEY")
    if api_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=15000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            for model_name in ("gemini-embedding-001", "text-embedding-004"):
                try:
                    resp = client.models.embed_content(
                        model=model_name,
                        contents=texto_huella,
                        config=types.EmbedContentConfig(output_dimensionality=768),
                    )
                    if resp and resp.embeddings and resp.embeddings[0].values:
                        vals = list(resp.embeddings[0].values)
                        if len(vals) == 768:
                            return [float(x) for x in vals]
                except Exception:
                    continue
        except Exception:
            pass

    return _deterministic_fallback_embedding(texto_huella, dim=768)


def compute_string_similarity(
    nombre_a: str,
    sede_a: Optional[str],
    nombre_b: str,
    sede_b: Optional[str],
) -> tuple[float, float]:
    """
    Retorna (sim_nombre, sim_compuesta_nombre_sede).
    """
    na = strip_accents(nombre_a or "")
    nb = strip_accents(nombre_b or "")
    sa = strip_accents(sede_a or "")
    sb = strip_accents(sede_b or "")

    name_sim = difflib.SequenceMatcher(None, na, nb).ratio() if (na and nb) else 0.0
    if sa and sb:
        sede_sim = difflib.SequenceMatcher(None, sa, sb).ratio()
        return round(name_sim, 4), round(0.80 * name_sim + 0.20 * sede_sim, 4)
    return round(name_sim, 4), round(name_sim, 4)


def evaluate_candidate_deduplication(candidato: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ejecuta el embudo de deduplicación de 3 niveles sobre Neon PostgreSQL:
    Retorna un diccionario con:
      - action: 'insert' | 'skip_duplicate' | 'skip_negative_memory'
      - status_reason: explicación detallada
      - huella_semantica: List[float] (768 dims)
      - posible_duplicado_id: Optional[str] (si cayó en Zona Gris 0.70 <= sim < 0.86)
      - similitud_maxima: float
    """
    engine = db.get_engine()
    sitio_canon = normalize_canonical_url(candidato.get("sitio_web"))
    fuente_canon = normalize_canonical_url(candidato.get("fuente_url"))
    fecha_ini_str = str(candidato["fecha_inicio"])[:10]
    estado_id = candidato.get("estado_id") or "MX-CMX"
    modalidad = candidato.get("modalidad") or "Presencial"

    texto_huella = build_huella_text(
        categoria=candidato.get("categoria", ""),
        sede=candidato.get("sede"),
        promotor=candidato.get("promotor"),
        nombre=candidato.get("nombre", ""),
    )

    huella_vec = candidato.get("huella_semantica")
    if not huella_vec or len(huella_vec) != 768:
        huella_vec = generate_semantic_embedding(texto_huella)

    vec_literal = "[" + ",".join(f"{float(x):.6f}" for x in huella_vec) + "]"

    with engine.connect() as conn:
        # -------------------------------------------------------------------
        # NIVEL 1: Coincidencia Exacta de URL Canónica del Evento + Fecha
        # -------------------------------------------------------------------
        url_check_rows = conn.execute(
            text(
                """
                SELECT id AS ref_id, nombre, 'eventos' AS tabla, 'aprobado' AS estatus, sitio_web, '' AS fuente_url
                FROM eventos
                WHERE CAST(fecha_inicio AS DATE) = CAST(:fecha_ini AS DATE)
                UNION ALL
                SELECT staging_id AS ref_id, nombre, 'eventos_pendientes_ia' AS tabla, estatus, sitio_web, fuente_url
                FROM eventos_pendientes_ia
                WHERE fecha_inicio = CAST(:fecha_ini AS DATE)
                """
            ),
            {"fecha_ini": fecha_ini_str},
        ).mappings().all()

        for row in url_check_rows:
            ex_sitio = normalize_canonical_url(row["sitio_web"])
            ex_fuente = normalize_canonical_url(row["fuente_url"])
            name_sim_l1, _ = compute_string_similarity(
                candidato.get("nombre", ""),
                None,
                row["nombre"],
                None,
            )
            # Si sitio_canon es específico del evento (distinto de la cartelera fuente_canon),
            # basta con que coincida ex_sitio. Si sitio_canon es igual a la cartelera general,
            # exigir además similitud de nombre >= 0.70 para no bloquear dos eventos distintos de la misma cartelera.
            is_specific_event_url = bool(sitio_canon and sitio_canon != fuente_canon)
            url_matched = False
            if is_specific_event_url and sitio_canon == ex_sitio:
                url_matched = True
            elif sitio_canon and sitio_canon in (ex_sitio, ex_fuente) and name_sim_l1 >= 0.70:
                url_matched = True

            if url_matched:
                if row["estatus"] == "descartado":
                    return {
                        "action": "skip_negative_memory",
                        "status_reason": f"Nivel 1 URL+Fecha descartada previamente ({row['ref_id']})",
                        "huella_semantica": huella_vec,
                        "posible_duplicado_id": row["ref_id"],
                        "similitud_maxima": 1.0,
                    }
                return {
                    "action": "skip_duplicate",
                    "status_reason": f"Nivel 1 URL+Fecha ya registrada ({row['ref_id']})",
                    "huella_semantica": huella_vec,
                    "posible_duplicado_id": row["ref_id"],
                    "similitud_maxima": 1.0,
                }

        # -------------------------------------------------------------------
        # NIVEL 2 (Hard Blocking Espaciotemporal ±4 días + Estado/Virtual)
        # + NIVEL 3 (Similitud Coseno pgvector + Similitud Cadena)
        # -------------------------------------------------------------------
        candidates_rows = conn.execute(
            text(
                """
                SELECT
                    id AS ref_id,
                    nombre,
                    sede,
                    'aprobado' AS estatus,
                    'eventos' AS origen_tabla,
                    CASE
                        WHEN huella_semantica IS NOT NULL
                        THEN 1 - (huella_semantica <=> CAST(:vec AS vector))
                        ELSE 0.0
                    END AS sim_vector
                FROM eventos
                WHERE (
                    estado_id = :estado_id
                    OR (:modalidad IN ('Virtual', 'En Línea') AND modalidad IN ('Virtual', 'En Línea'))
                )
                  AND CAST(fecha_inicio AS DATE) BETWEEN CAST(:fecha_ini AS DATE) - INTERVAL '4 days'
                                                     AND CAST(:fecha_ini AS DATE) + INTERVAL '4 days'

                UNION ALL

                SELECT
                    staging_id AS ref_id,
                    nombre,
                    sede,
                    estatus,
                    'eventos_pendientes_ia' AS origen_tabla,
                    CASE
                        WHEN huella_semantica IS NOT NULL
                        THEN 1 - (huella_semantica <=> CAST(:vec AS vector))
                        ELSE 0.0
                    END AS sim_vector
                FROM eventos_pendientes_ia
                WHERE (
                    estado_id = :estado_id
                    OR (:modalidad IN ('Virtual', 'En Línea') AND modalidad IN ('Virtual', 'En Línea'))
                )
                  AND fecha_inicio BETWEEN CAST(:fecha_ini AS DATE) - INTERVAL '4 days'
                                       AND CAST(:fecha_ini AS DATE) + INTERVAL '4 days'
                """
            ),
            {
                "vec": vec_literal,
                "estado_id": estado_id,
                "modalidad": modalidad,
                "fecha_ini": fecha_ini_str,
            },
        ).mappings().all()

    best_sim = 0.0
    best_ref_id: Optional[str] = None
    best_estatus: Optional[str] = None

    for row in candidates_rows:
        sim_vec = max(0.0, min(1.0, float(row["sim_vector"] or 0.0)))
        sim_nombre, sim_str = compute_string_similarity(
            candidato.get("nombre", ""),
            candidato.get("sede"),
            row["nombre"],
            row["sede"],
        )
        if sim_nombre < 0.45:
            # Eventos con títulos claramente distintos aunque compartan recinto, promotor y semana
            # (ej. RE+ Mexico vs Ecomondo Mexico, o Smart Technology Expo vs otro evento en Expo Guadalajara)
            sim_combinada = 0.45 * sim_vec + 0.55 * sim_nombre
        elif sim_vec >= 0.86:
            # Coincidencia vectorial fuerte respaldada por similitud de nombre (mismo evento o Memoria Negativa)
            sim_combinada = max(sim_vec, sim_str)
        else:
            # Similitud moderada/alta de título + similitud semántica vectorial
            sim_combinada = max(0.60 * sim_vec + 0.40 * sim_str, sim_str)

        sim_combinada = round(min(1.0, sim_combinada), 4)
        if sim_combinada > best_sim:
            best_sim = sim_combinada
            best_ref_id = str(row["ref_id"])
            best_estatus = str(row["estatus"])

    if best_sim >= 0.86:
        if best_estatus == "descartado":
            return {
                "action": "skip_negative_memory",
                "status_reason": f"Memoria Negativa activada: coincide ({best_sim:.2f}) con descartado {best_ref_id}",
                "huella_semantica": huella_vec,
                "posible_duplicado_id": best_ref_id,
                "similitud_maxima": best_sim,
            }
        return {
            "action": "skip_duplicate",
            "status_reason": f"Duplicado semántico ({best_sim:.2f}) de {best_ref_id}",
            "huella_semantica": huella_vec,
            "posible_duplicado_id": best_ref_id,
            "similitud_maxima": best_sim,
        }

    if 0.70 <= best_sim < 0.86:
        return {
            "action": "insert",
            "status_reason": f"Zona Gris ({best_sim:.2f}) respecto a {best_ref_id}",
            "huella_semantica": huella_vec,
            "posible_duplicado_id": best_ref_id,
            "similitud_maxima": best_sim,
        }

    return {
        "action": "insert",
        "status_reason": "Evento nuevo limpio",
        "huella_semantica": huella_vec,
        "posible_duplicado_id": None,
        "similitud_maxima": best_sim,
    }
