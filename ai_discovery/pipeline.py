"""
Orquestador principal del pipeline agéntico de descubrimiento de eventos (Carril 1 y Carril 2).
100% basado en extracción en vivo desde fuentes verificadas con certificado SSL válido y HTTP 200 OK.
Ejecutable desde CLI (`python -m ai_discovery.pipeline`), GitHub Actions o desde el botón
exclusivo de Admin en Streamlit (`app.py`).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable, Dict, List, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import database as db
from ai_discovery.crawler import (
    crawl_seed_sources,
    discover_web_urls_for_category,
    verify_live_url,
)
from ai_discovery.deduplicator import evaluate_candidate_deduplication
from ai_discovery.extractor import extract_events_from_markdown


def run_discovery_pipeline(
    sector_especifico: str = "TODOS",
    max_urls_por_sector: int = 8,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Ejecuta el ciclo completo de descubrimiento en vivo (Carril 1 + Carril 2), extracción con guardrails,
    verificación estricta de URLs (HTTP 200 + SSL válido + anti-Soft-404/PHP), enriquecimiento geográfico
    determinístico, deduplicación vectorial de 3 niveles con Memoria Negativa e inserción en
    `eventos_pendientes_ia` con `estatus = 'pendiente'`.
    """
    def _notify(msg: str) -> None:
        print(f"[AI-Discovery] {msg}")
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    db.init_db()

    stats: Dict[str, Any] = {
        "documentos_procesados": 0,
        "candidatos_extraidos": 0,
        "insertados_pendientes": 0,
        "omitidos_duplicados": 0,
        "bloqueados_memoria_negativa": 0,
        "alertas_zona_gris": 0,
        "nuevos_ids": [],
    }

    _notify("Carril 1: Consultando en vivo fuentes semilla oficiales (recintos feriales y cámaras)...")
    seed_docs = crawl_seed_sources(
        sector_filter=sector_especifico,
        max_sources=max(6, max_urls_por_sector),
    )
    _notify(f"Carril 1 completado: {len(seed_docs)} portales oficiales verificados con SSL y HTTP 200.")

    categorias_a_buscar = (
        db.CATEGORIES
        if sector_especifico.upper() == "TODOS"
        else [c for c in db.CATEGORIES if c.lower() == sector_especifico.lower()]
    )

    web_docs: List[Dict[str, Any]] = []
    for cat in categorias_a_buscar[:2]:
        _notify(f"Carril 2: Ejecutando búsqueda web verificada para '{cat}'...")
        cat_docs = discover_web_urls_for_category(
            categoria=cat,
            max_results=min(2, max_urls_por_sector),
        )
        web_docs.extend(cat_docs)

    all_docs = seed_docs + web_docs
    stats["documentos_procesados"] = len(all_docs)

    for idx, doc in enumerate(all_docs, 1):
        fuente_url = doc["fuente_url"]
        ok_fuente, canonical_fuente = verify_live_url(fuente_url, timeout_sec=8.0)
        if not ok_fuente or not canonical_fuente:
            _notify(f"  ⚠️ Fuente descartada por fallo de verificación HTTP/SSL: {fuente_url}")
            continue
        fuente_url = canonical_fuente

        origen_carril = doc.get("origen_carril", "semilla_oficial")
        _notify(
            f"Extrayendo y validando eventos ({idx}/{len(all_docs)}): {doc.get('nombre_fuente', fuente_url)[:65]}..."
        )
        extracted_list = extract_events_from_markdown(
            markdown_text=doc.get("markdown", ""),
            fuente_url=fuente_url,
            origen_carril=origen_carril,
            default_estado=doc.get("default_estado"),
            categoria_sugerida=doc.get("categoria_sugerida"),
            verify_urls_live=True,
        )

        for candidate in extracted_list:
            # Garantía final de que tanto fuente_url como sitio_web respondan HTTP 200 + SSL válido
            ok_sitio, canonical_sitio = verify_live_url(candidate.get("sitio_web") or fuente_url, timeout_sec=7.0)
            candidate["fuente_url"] = fuente_url
            candidate["sitio_web"] = canonical_sitio if (ok_sitio and canonical_sitio) else fuente_url

            stats["candidatos_extraidos"] += 1
            dedup_res = evaluate_candidate_deduplication(candidate)
            action = dedup_res["action"]

            if action == "skip_negative_memory":
                stats["bloqueados_memoria_negativa"] += 1
                _notify(f"  ⛔ Memoria Negativa: '{candidate['nombre'][:45]}' bloqueado.")
                continue

            if action == "skip_duplicate":
                stats["omitidos_duplicados"] += 1
                _notify(f"  ⏭️ Duplicado omitido: '{candidate['nombre'][:45]}'.")
                continue

            candidate["huella_semantica"] = dedup_res["huella_semantica"]
            candidate["posible_duplicado_id"] = dedup_res["posible_duplicado_id"]
            candidate["similitud_maxima"] = dedup_res["similitud_maxima"]

            if candidate["posible_duplicado_id"]:
                stats["alertas_zona_gris"] += 1

            staging_id = db.insert_staging_event(candidate)
            stats["insertados_pendientes"] += 1
            stats["nuevos_ids"].append(staging_id)
            _notify(
                f"  ✅ Registrado en Bandeja IA: {staging_id} · {candidate['nombre'][:50]} "
                f"(Score: {candidate['confianza_score']:.2f})"
            )

    _notify(
        f"Búsqueda finalizada: {stats['insertados_pendientes']} nuevos en bandeja, "
        f"{stats['omitidos_duplicados']} duplicados omitidos, "
        f"{stats['bloqueados_memoria_negativa']} bloqueados por Memoria Negativa."
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline Agéntico de Descubrimiento de Eventos NAFIN / BANCOMEXT"
    )
    parser.add_argument(
        "--sector",
        default=os.environ.get("SECTOR_ESPECIFICO", "TODOS"),
        help="Sector específico o TODOS",
    )
    parser.add_argument(
        "--max-urls",
        type=int,
        default=int(os.environ.get("MAX_URLS_POR_SECTOR", "8")),
        help="Máximo de URLs por sector",
    )
    args = parser.parse_args()
    run_discovery_pipeline(
        sector_especifico=args.sector,
        max_urls_por_sector=args.max_urls,
    )


if __name__ == "__main__":
    main()
