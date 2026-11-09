"""
Módulo de extracción estructurada con Gemini (google-genai SDK) + enriquecimiento
determinístico con KNOWN_VENUES_MEXICO / geo_utils.py, verificación en vivo de URLs (HTTP 200 + SSL válido)
y cálculo determinístico de confianza_score.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import database as db
from ai_discovery.crawler import verify_live_url
from ai_discovery.schemas import (
    EventoCandidatoIA,
    ListaEventosExtraidos,
    sanitize_single_line_text,
)
from ai_discovery.venues_catalog import resolve_candidate_geography
from geo_utils import get_mexico_today

REQUIRED_METADATA_FIELDS = [
    "horario",
    "sede",
    "direccion",
    "codigo_postal",
    "promotor",
    "latitud",
    "longitud",
]


def build_system_prompt(fecha_actual_servidor: Optional[str] = None) -> str:
    hoy_iso = fecha_actual_servidor or get_mexico_today().isoformat()
    return f"""Eres un analista institucional de inteligencia sectorial de NAFIN y BANCOMEXT.
FECHA_ACTUAL_SERVIDOR = {hoy_iso}

Tu única tarea es extraer hechos textuales explícitos sobre eventos empresariales estratégicos B2B
(congresos industriales, exposiciones de manufactura, cumbres de inversión, foros de comercio exterior
y encuentros de negocios B2B) a partir del documento Markdown proporcionado.

REGLAS ESTRICTAS DE GOBERNANZA Y ANTI-ALUCINACIÓN:
1. SOLO extrae eventos cuya `fecha_inicio` sea mayor o igual a FECHA_ACTUAL_SERVIDOR ({hoy_iso}).
2. RECHAZA y OMITE cualquier taller de capacitación, curso universitario, diplomado, vacante laboral,
   feria de empleo, webinar comercial, concierto, boda, quinceañera, graduación, convención de cómics/anime,
   exposición de mascotas, bazar de consumo o evento fuera de México (a menos que sea un pabellón oficial de
   México en el extranjero promovido explícitamente por BANCOMEXT o COMCE).
3. TIENES PROHIBIDO inventar coordenadas (`latitud`, `longitud`), códigos postales no explícitos,
   `estado_id` ISO, `google_maps_url` o calcular `confianza_score`.
4. TIENES PROHIBIDO inventar o adivinar URLs. El campo `sitio_web_evento` SOLO puede contener una URL
   que aparezca LITERALMENTE escrita en el Markdown junto al evento (por ejemplo en `Sitio oficial: https://...`
   o en el enlace Markdown del título del evento). Si el evento tiene su propio enlace externo o subpágina
   explícita en el Markdown, úsala en `sitio_web_evento`; si no aparece ninguna URL específica para ese evento,
   deja `sitio_web_evento` como null.
5. El campo `evidencia_texto` DEBE ser una cita textual limpia en una sola línea (sin saltos de línea ni
   URLs ni sintaxis Markdown) copiada del texto donde consten explícitamente el nombre del evento, el día/mes
   y el año de 4 dígitos (ej. "Expo Carnes y Lácteos 2027 · 9-11 Feb 2027 · Exposición Internacional de la Industria Cárnica").
6. Clasifica cada evento exclusivamente en una de las 11 categorías oficiales:
   - "Comercio Exterior"
   - "Automotriz y Aeroespacial"
   - "Manufactura Avanzada"
   - "Semiconductores y Tech"
   - "Agroindustria"
   - "Turismo"
   - "Turismo Médico"
   - "Dispositivos Médicos"
   - "Plásticos y Químicos"
   - "Electromovilidad"
   - "Equipos de Generación y Distribución Eléctrica"
"""


def compute_missing_fields_and_score(
    enriched_record: Dict[str, Any],
    origen_carril: str,
    matched_catalog: bool,
    resolved_full_coords: bool,
) -> tuple[List[str], float]:
    """
    Determina `campos_faltantes` y calcula `confianza_score` de forma 100% determinística en Python:
    - +0.40 si `origen_carril == 'semilla_oficial'`, o +0.20 si `origen_carril == 'descubrimiento_web'`.
    - +0.30 si el recinto hizo match en KNOWN_VENUES_MEXICO o tiene coordenadas y CP completos;
      +0.15 si solo resolvió a nivel estado/municipio.
    - +0.20 * (1 - len(campos_faltantes) / 7.0) por completitud de metadatos.
    - +0.10 si `promotor` no es nulo.
    - Redondea a 2 decimales (`min(1.0, round(score, 2))`).
    """
    campos_faltantes: List[str] = []
    for field_name in REQUIRED_METADATA_FIELDS:
        val = enriched_record.get(field_name)
        if val is None or (isinstance(val, str) and not val.strip()):
            campos_faltantes.append(field_name)

    score = 0.40 if origen_carril == "semilla_oficial" else 0.20
    if matched_catalog or (
        resolved_full_coords
        and enriched_record.get("codigo_postal")
        and enriched_record.get("latitud") is not None
        and enriched_record.get("longitud") is not None
    ):
        score += 0.30
    else:
        score += 0.15

    score += 0.20 * (1.0 - (len(campos_faltantes) / 7.0))

    if enriched_record.get("promotor") and str(enriched_record["promotor"]).strip():
        score += 0.10

    return campos_faltantes, min(1.0, round(score, 2))


def _url_appears_in_markdown(candidate_url: str, markdown_context: str) -> bool:
    """
    Comprueba que la URL extraída por el LLM realmente exista en el documento Markdown
    de origen y no sea un slug inventado/alucinado por el modelo.
    """
    if not candidate_url or not markdown_context:
        return True
    low_md = markdown_context.lower()
    parsed = urlparse(candidate_url.strip())
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.rstrip("/").lower()
    if not host:
        return False
    if path and len(path) > 1:
        return (host in low_md) and (path in low_md)
    return host in low_md


def enrich_validated_candidate(
    candidato: EventoCandidatoIA,
    fuente_url: str,
    origen_carril: str = "semilla_oficial",
    default_estado: Optional[str] = None,
    markdown_context: str = "",
    verify_urls_live: bool = False,
) -> Dict[str, Any]:
    """
    Toma un `EventoCandidatoIA` validado por Pydantic, resuelve determinísticamente su geografía
    con `KNOWN_VENUES_MEXICO` y `geo_utils.py`, verifica contra alucinación de URLs y calcula
    `campos_faltantes` y `confianza_score`.
    """
    geo_info = resolve_candidate_geography(
        sede_nombre=candidato.sede_nombre,
        direccion_explicita=candidato.direccion_explicita,
        estado_nombre=candidato.estado_nombre,
        municipio=candidato.municipio,
        codigo_postal_explicito=candidato.codigo_postal_explicito,
        modalidad=candidato.modalidad,
        nombre_evento=candidato.nombre,
        default_estado=default_estado,
    )

    clean_fuente_url = (fuente_url or "").strip()
    candidate_web = (candidato.sitio_web_evento or "").strip()

    # 1. Bloquear URLs inventadas por el LLM que no consten en el Markdown fuente
    if candidate_web and markdown_context:
        if not _url_appears_in_markdown(candidate_web, markdown_context):
            candidate_web = ""

    # 2. Verificar en vivo (HTTP 200 + SSL válido + sin errores PHP/Soft-404) cuando está activo
    if verify_urls_live and candidate_web:
        ok_web, resolved_web = verify_live_url(candidate_web, timeout_sec=7.0)
        if ok_web and resolved_web:
            candidate_web = resolved_web
        else:
            candidate_web = ""

    sitio_web_final = candidate_web or clean_fuente_url

    record: Dict[str, Any] = {
        "nombre": sanitize_single_line_text(candidato.nombre),
        "descripcion": sanitize_single_line_text(candidato.descripcion),
        "categoria": candidato.categoria,
        "fecha_inicio": candidato.fecha_inicio.isoformat(),
        "fecha_fin": candidato.fecha_fin.isoformat(),
        "horario": (candidato.horario or "").strip() or None,
        "modalidad": candidato.modalidad,
        "sede": (geo_info.get("sede") or "").strip() or None,
        "direccion": (geo_info.get("direccion") or "").strip() or None,
        "estado": geo_info.get("estado") or "Ciudad de México",
        "estado_id": geo_info.get("estado_id") or "MX-CMX",
        "municipio": (geo_info.get("municipio") or "").strip() or None,
        "codigo_postal": (geo_info.get("codigo_postal") or "").strip() or None,
        "latitud": geo_info.get("latitud"),
        "longitud": geo_info.get("longitud"),
        "google_maps_url": (geo_info.get("google_maps_url") or "").strip() or None,
        "promotor": (candidato.promotor or "").strip() or None,
        "sitio_web": sitio_web_final,
        "fuente_url": clean_fuente_url,
        "origen_carril": origen_carril,
        "evidencia_texto": sanitize_single_line_text(candidato.evidencia_texto),
    }

    campos_faltantes, confianza_score = compute_missing_fields_and_score(
        enriched_record=record,
        origen_carril=origen_carril,
        matched_catalog=bool(geo_info.get("matched_catalog")),
        resolved_full_coords=bool(geo_info.get("resolved_full_coords")),
    )
    record["campos_faltantes"] = campos_faltantes
    record["confianza_score"] = confianza_score
    return record


def extract_events_from_markdown(
    markdown_text: str,
    fuente_url: str,
    origen_carril: str = "semilla_oficial",
    default_estado: Optional[str] = None,
    categoria_sugerida: Optional[str] = None,
    verify_urls_live: bool = True,
) -> List[Dict[str, Any]]:
    """
    Envía el Markdown limpio a Gemini con `response_mime_type="application/json"` y
    `response_schema=ListaEventosExtraidos`, valida cada evento con los guardrails de Pydantic v2,
    verifica en vivo las URLs extraídas (anti-alucinación + SSL + HTTP 200) y devuelve la lista
    de candidatos enriquecidos.
    """
    if not markdown_text or len(markdown_text.strip()) < 40:
        return []

    api_key = db.get_secret("GEMINI_API_KEY") or db.get_secret("GOOGLE_API_KEY")
    if not api_key:
        return []

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=20000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    sys_instruction = build_system_prompt()
    user_prompt = (
        f"URL de origen: {fuente_url}\n"
        f"Categoría sectorial de referencia: {categoria_sugerida or 'General'}\n"
        f"Estado de referencia del recinto: {default_estado or 'No especificado'}\n\n"
        "Extrae todos los eventos empresariales e industriales estratégicos futuros en México "
        "presentes en el siguiente Markdown (incluyendo su URL específica de `Sitio oficial:` o enlace "
        "directo si aparece en el texto):\n\n"
        f"{markdown_text[:18000]}"
    )

    raw_payload: Optional[Dict[str, Any]] = None
    for model_name in ("gemini-3.5-flash-lite", "gemini-3.8-flash"):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=sys_instruction,
                    response_mime_type="application/json",
                    response_schema=ListaEventosExtraidos,
                    temperature=0.0,
                ),
            )
            if response and response.text:
                raw_payload = json.loads(response.text)
                break
        except Exception:
            continue

    if not raw_payload or not isinstance(raw_payload, dict):
        return []

    raw_items = raw_payload.get("eventos", [])
    if not isinstance(raw_items, list):
        return []

    valid_enriched: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            candidato = EventoCandidatoIA.model_validate(item)
            enriched = enrich_validated_candidate(
                candidato=candidato,
                fuente_url=fuente_url,
                origen_carril=origen_carril,
                default_estado=default_estado,
                markdown_context=markdown_text,
                verify_urls_live=verify_urls_live,
            )
            valid_enriched.append(enriched)
        except Exception:
            continue

    return valid_enriched
