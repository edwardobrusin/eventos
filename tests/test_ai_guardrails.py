"""
Suite de pruebas de aceptación (Fase 5 — Definition of Done):
1. Integridad de Migración a Neon PostgreSQL + pgvector
2. Guardrails Pydantic v2 (fechas pasadas 2025, alucinación de año en evidencia, acortadores bit.ly/tinyurl)
3. Memoria Negativa en deduplicator.py (bloqueo cuando estatus='descartado' y similitud >= 0.86 en ±4 días)
4. Compatibilidad de contratos de database.py, geo_utils.py (.ics) y pdf_utils.py (PDF)
5. Flujo HITL Completo (aprobación desde Bandeja IA -> eventos EVT-YYYY-NNN + bitácora .ics)
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import pytest
from pydantic import ValidationError
from sqlalchemy import text

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import database as db
from ai_discovery.deduplicator import (
    build_huella_text,
    evaluate_candidate_deduplication,
    generate_semantic_embedding,
)
from ai_discovery.extractor import enrich_validated_candidate
from ai_discovery.schemas import EventoCandidatoIA
from geo_utils import build_google_calendar_url, build_ics_content, compute_temporal_status
from pdf_utils import generate_agenda_pdf, generate_calendar_grid_pdf


def test_1_neon_migration_and_schema_integrity():
    """
    1. Integridad de Migración: Verifica conexión a Neon, extensión vector y existencia de las 4 tablas.
    """
    db.init_db()
    engine = db.get_engine()
    with engine.connect() as conn:
        ext = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        assert ext == "vector"

        tables = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            ).fetchall()
        }
        assert "eventos" in tables
        assert "eventos_pendientes_ia" in tables
        assert "usuarios" in tables
        assert "bitacora_notificaciones" in tables

        ev_count = conn.execute(text("SELECT COUNT(*) FROM eventos")).scalar()
        assert ev_count >= 15


def test_2_pydantic_guardrails():
    """
    2. Prueba de Guardrails Pydantic:
       - Evento con fecha de 2025 es rechazado automáticamente.
       - Evento cuyo año (2026 o 2027) no aparece en evidencia_texto (ni nombre) es rechazado.
       - URL con bit.ly o tinyurl.com es bloqueada.
    """
    # 2a. Evento pasado (2025) rechazado
    with pytest.raises(ValidationError) as exc_past:
        EventoCandidatoIA(
            nombre="Congreso Nacional de Comercio Exterior 2025",
            descripcion="Cumbre anual de comercio exterior y logística aduanera en México.",
            categoria="Comercio Exterior",
            fecha_inicio=dt.date(2025, 11, 10),
            fecha_fin=dt.date(2025, 11, 12),
            evidencia_texto="El Congreso Nacional de Comercio Exterior 2025 se realizó del 10 al 12 de noviembre de 2025.",
        )
    assert "Evento pasado descartado" in str(exc_past.value)

    # 2b. Año ausente en evidencia_texto y nombre -> Alucinación temporal bloqueada
    future_year = dt.date.today().year + 1
    with pytest.raises(ValidationError) as exc_year:
        EventoCandidatoIA(
            nombre="Foro Internacional de Semiconductores de Occidente",
            descripcion="Encuentro de diseño de chips y empaquetado avanzado en Guadalajara.",
            categoria="Semiconductores y Tech",
            fecha_inicio=dt.date(future_year, 5, 14),
            fecha_fin=dt.date(future_year, 5, 15),
            evidencia_texto="El Foro Internacional de Semiconductores se llevará a cabo el 14 y 15 de mayo en Expo Guadalajara.",
        )
    assert "Alucinación temporal bloqueada" in str(exc_year.value)

    # 2c. Acortadores de URL (bit.ly y tinyurl.com) bloqueados
    for short_url in ("https://bit.ly/evento-bancomext", "https://tinyurl.com/foro-2027"):
        with pytest.raises(ValidationError) as exc_url:
            EventoCandidatoIA(
                nombre=f"Cumbre de Manufactura Avanzada {future_year}",
                descripcion="Exposición industrial de automatización y robótica en Monterrey.",
                categoria="Manufactura Avanzada",
                fecha_inicio=dt.date(future_year, 6, 10),
                fecha_fin=dt.date(future_year, 6, 12),
                sitio_web_evento=short_url,
                evidencia_texto=f"Cumbre de Manufactura Avanzada {future_year} del 10 al 12 de junio de {future_year}.",
            )
        assert "URL bloqueada por política de seguridad institucional" in str(exc_url.value)


def test_3_negative_memory_blocking():
    """
    3. Prueba de Memoria Negativa:
       Si un evento en eventos_pendientes_ia tiene estatus = 'descartado', un nuevo intento
       de inserción con el mismo estado, fecha (±4 días) y similitud semántica >= 0.86 es bloqueado.
    """
    engine = db.get_engine()
    test_stg_id = "STG-99991201-TEST"
    future_date = dt.date.today() + dt.timedelta(days=45)
    shifted_date = future_date + dt.timedelta(days=2)  # Dentro de la ventana de ±4 días

    nombre_test = "Foro Binacional de Electromovilidad y Autopartes Monterrey"
    categoria_test = "Automotriz y Aeroespacial"
    sede_test = "CINTERMEX Monterrey"
    promotor_test = "INA / CLAUT Nuevo León"

    huella_txt = build_huella_text(categoria_test, sede_test, promotor_test, nombre_test)
    vec = generate_semantic_embedding(huella_txt)

    try:
        # Insertar en staging y descartarlo (Memoria Negativa)
        db.insert_staging_event(
            {
                "staging_id": test_stg_id,
                "nombre": nombre_test,
                "descripcion": "Evento de prueba para validación de Memoria Negativa con pgvector.",
                "categoria": categoria_test,
                "fecha_inicio": future_date.isoformat(),
                "fecha_fin": future_date.isoformat(),
                "horario": "09:00 - 18:00 hrs",
                "modalidad": "Presencial",
                "sede": sede_test,
                "direccion": "Av. Fundidora 501, Monterrey, N.L.",
                "estado": "Nuevo León",
                "estado_id": "MX-NLE",
                "municipio": "Monterrey",
                "codigo_postal": "64010",
                "latitud": 25.6782,
                "longitud": -100.2877,
                "google_maps_url": "https://www.google.com/maps/search/?api=1&query=CINTERMEX",
                "promotor": promotor_test,
                "sitio_web": "https://electromovilidad-mty-test-1.mx",
                "fuente_url": "https://fuente-test-1.mx/evento",
                "origen_carril": "semilla_oficial",
                "evidencia_texto": f"{nombre_test} confirmado el {future_date.isoformat()}.",
                "confianza_score": 0.95,
                "campos_faltantes": [],
                "huella_semantica": vec,
            }
        )
        db.discard_staging_event(
            staging_id=test_stg_id,
            motivo_descarte="irrelevante",
            reviewed_by="admin.general",
        )

        # Nuevo intento con URL distinta, fecha desplazada +2 días (dentro de ±4 días), mismo estado y alta similitud
        nuevo_intento = {
            "nombre": "Foro Binacional de Electromovilidad y Autopartes en Monterrey",
            "categoria": categoria_test,
            "fecha_inicio": shifted_date.isoformat(),
            "fecha_fin": shifted_date.isoformat(),
            "modalidad": "Presencial",
            "sede": sede_test,
            "estado_id": "MX-NLE",
            "promotor": promotor_test,
            "sitio_web": "https://otra-url-distinta-test-2.mx",
            "fuente_url": "https://otra-fuente-distinta-2.mx/nota",
            "huella_semantica": vec,
        }
        dedup_res = evaluate_candidate_deduplication(nuevo_intento)
        assert dedup_res["action"] == "skip_negative_memory"
        assert dedup_res["similitud_maxima"] >= 0.86
        assert dedup_res["posible_duplicado_id"] == test_stg_id
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM eventos_pendientes_ia WHERE staging_id = :id"),
                {"id": test_stg_id},
            )


def test_4_backward_compatibility_contracts_pdf_and_ics():
    """
    4. Compatibilidad de app.py, pdf_utils.py y geo_utils.py tras migración a Neon.
    """
    events = db.list_events()
    assert len(events) >= 15
    first_ev = events[0]
    assert isinstance(first_ev["fecha_inicio"], str)
    assert isinstance(first_ev["fecha_fin"], str)

    st_info = compute_temporal_status(first_ev["fecha_inicio"], first_ev["fecha_fin"])
    assert "code" in st_info and "duration_days" in st_info

    ics_str = build_ics_content(first_ev)
    assert "BEGIN:VCALENDAR" in ics_str and "END:VCALENDAR" in ics_str

    gcal_url = build_google_calendar_url(first_ev)
    assert gcal_url.startswith("https://calendar.google.com/calendar/render?")

    pdf_agenda = generate_agenda_pdf(events[:3], title="AGENDA TEST")
    assert isinstance(pdf_agenda, bytes) and pdf_agenda.startswith(b"%PDF")

    pdf_grid = generate_calendar_grid_pdf(events, year=2026, month=11, month_name="Noviembre")
    assert isinstance(pdf_grid, bytes) and pdf_grid.startswith(b"%PDF")


def test_5_full_hitl_approval_flow():
    """
    5. Flujo HITL Completo:
       Verifica que un usuario Director o Admin puede aprobar un evento desde la Bandeja IA,
       que este pasa a `eventos` con formato `EVT-YYYY-NNN` y que desaparece de la lista de pendientes.
    """
    engine = db.get_engine()
    test_stg_id = "STG-99991215-HITL"
    future_date = dt.date.today() + dt.timedelta(days=60)
    created_official_id = None

    cand_model = EventoCandidatoIA(
        nombre=f"Cumbre Agroexportadora del Pacífico {future_date.year}",
        descripcion="Encuentro de productores agroindustriales y logística de cadena de frío para exportación.",
        categoria="Agroindustria",
        fecha_inicio=future_date,
        fecha_fin=future_date + dt.timedelta(days=1),
        horario="09:00 - 17:30 hrs",
        modalidad="Presencial",
        sede_nombre="Expo Guadalajara",
        estado_nombre="Jalisco",
        municipio="Guadalajara",
        promotor="CNA / BANCOMEXT",
        sitio_web_evento="https://cumbreagropacifico.mx",
        evidencia_texto=f"Cumbre Agroexportadora del Pacífico {future_date.year} confirmada el {future_date.isoformat()} en Expo Guadalajara.",
    )
    enriched = enrich_validated_candidate(
        candidato=cand_model,
        fuente_url="https://expoguadalajara.mx/cumbre-agro-hitl-test",
        origen_carril="semilla_oficial",
    )
    enriched["staging_id"] = test_stg_id
    enriched["huella_semantica"] = generate_semantic_embedding(
        build_huella_text(
            enriched["categoria"],
            enriched.get("sede"),
            enriched.get("promotor"),
            enriched["nombre"],
        )
    )

    try:
        db.insert_staging_event(enriched)
        pending_before = {item["staging_id"] for item in db.list_staging_events(estatus="pendiente")}
        assert test_stg_id in pending_before

        created_official_id = db.approve_staging_event(
            staging_id=test_stg_id,
            event_payload={
                "nombre": enriched["nombre"],
                "descripcion": enriched["descripcion"],
                "fecha_inicio": enriched["fecha_inicio"],
                "fecha_fin": enriched["fecha_fin"],
                "horario": enriched["horario"] or "09:00 - 18:00 hrs",
                "modalidad": enriched["modalidad"],
                "sede": enriched["sede"] or "",
                "direccion": enriched["direccion"] or "",
                "estado": enriched["estado"],
                "municipio": enriched["municipio"] or "",
                "codigo_postal": enriched["codigo_postal"] or "",
                "latitud": enriched["latitud"],
                "longitud": enriched["longitud"],
                "google_maps_url": enriched["google_maps_url"] or "",
                "categoria": enriched["categoria"],
                "promotor": enriched["promotor"] or "NAFIN / BANCOMEXT",
                "sitio_web": enriched["sitio_web"],
            },
            reviewed_by="director.promocion",
        )

        assert created_official_id.startswith("EVT-2026-")
        official_ev = db.get_event_by_id(created_official_id)
        assert official_ev is not None
        assert official_ev["nombre"] == enriched["nombre"]

        pending_after = {item["staging_id"] for item in db.list_staging_events(estatus="pendiente")}
        assert test_stg_id not in pending_after

        stg_after = db.get_staging_event_by_id(test_stg_id)
        assert stg_after is not None
        assert stg_after["estatus"] == "aprobado"
        assert stg_after["evento_oficial_id"] == created_official_id
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM eventos_pendientes_ia WHERE staging_id = :id"),
                {"id": test_stg_id},
            )
            if created_official_id:
                conn.execute(
                    text("DELETE FROM bitacora_notificaciones WHERE evento_id = :id"),
                    {"id": created_official_id},
                )
                conn.execute(
                    text("DELETE FROM eventos WHERE id = :id"),
                    {"id": created_official_id},
                )
        db.export_events_json_snapshot()


def test_6_anti_hallucination_urls_ssl_and_markdown_sanitization():
    """
    6. Prueba de Anti-Alucinación de URLs, Validación SSL/HTTP 200 y Limpieza de Evidencia:
       - Rechaza URLs con error 404/Soft-404, errores PHP de servidor y certificados SSL inválidos (qcc.mx).
       - Bloquea URLs alucinadas por el LLM que no existen en el Markdown fuente.
       - Limpia saltos de línea y sintaxis Markdown en evidencia_texto para evitar rupturas de HTML/blockquote.
    """
    from ai_discovery.crawler import verify_live_url
    from ai_discovery.extractor import _url_appears_in_markdown
    from ai_discovery.schemas import sanitize_single_line_text

    # 6a. Limpieza de saltos de línea y enlaces Markdown en evidencia_texto
    raw_evid = '### [ITB Americas](https://expoguadalajara.mx/events/itb-americas/)\n10 noviembre, 2026 - 12 noviembre, 2026'
    cleaned = sanitize_single_line_text(raw_evid)
    assert "\n" not in cleaned
    assert "](" not in cleaned
    assert "ITB Americas · 10 noviembre, 2026 - 12 noviembre, 2026" == cleaned

    # 6b. Bloqueo de URL alucinada no presente en el Markdown original
    md_sample = "Expo Carnes y Lácteos 2027 del 9 al 11 de febrero de 2027. Sitio oficial: https://expocarnes.com/"
    assert _url_appears_in_markdown("https://expocarnes.com/", md_sample) is True
    assert _url_appears_in_markdown("https://www.cintermex.com/calendario-de-eventos/expo-carnes-2027", md_sample) is False

    # 6c. Verificación en vivo rechaza 404, errores PHP y SSL inválido (qcc.mx)
    ok_404, _ = verify_live_url("https://expoguadalajara.mx/logistica-2026", timeout_sec=6.0)
    assert ok_404 is False

    ok_php, _ = verify_live_url("https://www.cintermex.com/calendario-de-eventos/expo-manufactura-monterrey-2027", timeout_sec=6.0)
    assert ok_php is False

    ok_ssl, _ = verify_live_url("https://qcc.mx/eventos/mexico-aerospace-summit-queretaro-2026", timeout_sec=6.0)
    assert ok_ssl is False


def test_7_new_requirements_turismo_medico_responsable_edicion_and_maps():
    """
    7. Pruebas de verificación de las 4 nuevas modificaciones solicitadas:
       1) Categoría 'Turismo Médico' en catálogo y esquema Pydantic.
       2) Campo 'responsable' obligatorio y persistido en BD (registro manual y bandeja IA).
       3) Sistema de edición (update_event) para corregir typos, sedes, fechas.
       4) Catálogo enriquecido y estructura live maps (Autódromo Hermanos Rodríguez).
    """
    from geo_utils import get_known_venues_minified

    # 1. Categoría "Turismo Médico"
    assert "Turismo Médico" in db.CATEGORIES

    cand = EventoCandidatoIA(
        nombre="Cumbre Internacional de Turismo Médico Cancún 2027",
        descripcion="Foro especializado en infraestructura hospitalaria, certificación Joint Commission y atracción de pacientes.",
        categoria="Turismo Médico",
        fecha_inicio=dt.date(2027, 4, 15),
        fecha_fin=dt.date(2027, 4, 17),
        modalidad="Presencial",
        sitio_web="https://turismomedicomx.com",
        fuente_url="https://turismomedicomx.com/agenda",
        origen_carril="semilla_oficial",
        evidencia_texto="Cumbre Internacional de Turismo Médico Cancún 2027 del 15 al 17 de abril de 2027.",
    )
    assert cand.categoria == "Turismo Médico"

    # 2. Creación con Responsable
    test_evt_id = "EVT-TEST-TM-01"
    try:
        created_id = db.create_event(
            {
                "id": test_evt_id,
                "nombre": "Cumbre Internacional de Turismo Médico Cancún 2027",
                "descripcion": "Foro especializado en infraestructura hospitalaria y atracción de pacientes.",
                "categoria": "Turismo Médico",
                "fecha_inicio": "2027-04-15",
                "fecha_fin": "2027-04-17",
                "responsable": "Lic. Ana Elena Ramos - Promoción Sectorial",
                "sede": "Cancún Center",
                "estado": "Quintana Roo",
            },
            created_by="aramos",
        )
        assert created_id == test_evt_id
        ev = db.get_event_by_id(test_evt_id)
        assert ev is not None
        assert ev["categoria"] == "Turismo Médico"
        assert ev["responsable"] == "Lic. Ana Elena Ramos - Promoción Sectorial"

        # 3. Edición del evento (corrección de typo, cambio de sede y fechas)
        ok_update = db.update_event(
            test_evt_id,
            {
                "nombre": "Cumbre Global de Turismo Médico & Salud Cancún 2027",
                "descripcion": "Descripción corregida: Foro especializado con mesas de financiamiento Bancomext.",
                "categoria": "Turismo Médico",
                "fecha_inicio": "2027-04-16",
                "fecha_fin": "2027-04-18",
                "sede": "Paradisus Cancún Convention Center",
                "responsable": "Lic. Ana Elena Ramos (Actualizado)",
            },
            updated_by="director.general",
        )
        assert ok_update is True
        ev_mod = db.get_event_by_id(test_evt_id)
        assert ev_mod["nombre"] == "Cumbre Global de Turismo Médico & Salud Cancún 2027"
        assert ev_mod["fecha_inicio"] == "2027-04-16"
        assert ev_mod["sede"] == "Paradisus Cancún Convention Center"
        assert ev_mod["responsable"] == "Lic. Ana Elena Ramos (Actualizado)"

    finally:
        engine = db.get_engine()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM eventos WHERE id = :id"), {"id": test_evt_id})
        db.export_events_json_snapshot()

    # 4. Catálogo minificado para Maps Autocomplete con Autódromo
    from geo_utils import strip_accents
    venues = get_known_venues_minified()
    autodromo = next((v for v in venues if "autodromo" in strip_accents(v["name"]).lower()), None)
    assert autodromo is not None
    assert "Hermanos Rodríguez" in autodromo["name"]
    assert autodromo["lat"] > 19.0
    assert autodromo["lon"] < -99.0



