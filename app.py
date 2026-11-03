"""
Agenda Ejecutiva de Eventos — NAFIN / BANCOMEXT
"""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import html
import json
import math
import os
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import database as db
from geo_utils import (
    MEXICO_STATES,
    build_google_calendar_url,
    build_ics_content,
    compute_temporal_status,
    ensure_point_inside_state,
    format_date_mx,
    format_date_range_mx,
    get_mexico_today,
    get_state_map_center_and_zoom,
    get_state_zoom_bounds,
    parse_google_maps_or_search,
)
from pdf_utils import generate_agenda_pdf, generate_calendar_grid_pdf

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA E INICIALIZACIÓN
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Eventos · NAFIN / BANCOMEXT",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_resource(show_spinner=False)
def ensure_db_ready() -> None:
    try:
        db.init_db()
    except Exception:
        pass


ensure_db_ready()


@st.cache_data(show_spinner=False, ttl=60)
def get_cached_events() -> List[Dict[str, Any]]:
    try:
        return db.list_events()
    except Exception:
        return []

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GEOJSON_PATH = os.path.join(BASE_DIR, "data", "mexico_states.geojson")
LOGO_BANCOMEXT = os.path.join(BASE_DIR, "assets", "logos", "bancomext.png")
LOGO_NAFIN = os.path.join(BASE_DIR, "assets", "logos", "nafin.png")

MESES_ES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}


@st.cache_data(show_spinner=False)
def load_mexico_geojson() -> Dict[str, Any]:
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def load_logo_b64(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


@st.cache_data(show_spinner=False, ttl=120)
def cached_agenda_pdf(events_json: str, title: str, logo_nafin: str, logo_bancomext: str) -> bytes:
    evs = json.loads(events_json)
    return generate_agenda_pdf(evs, title=title, logo_nafin=logo_nafin, logo_bancomext=logo_bancomext)


@st.cache_data(show_spinner=False, ttl=120)
def cached_calendar_grid_pdf(
    events_json: str, year: int, month: int, month_name: str, logo_nafin: str, logo_bancomext: str
) -> bytes:
    evs = json.loads(events_json)
    return generate_calendar_grid_pdf(
        evs,
        year=year,
        month=month,
        month_name=month_name,
        logo_nafin=logo_nafin,
        logo_bancomext=logo_bancomext,
    )


# ---------------------------------------------------------------------------
# ESTADO DE SESIÓN (CONECTADO EN TIEMPO REAL A FECHA CDMX)
# ---------------------------------------------------------------------------
_TODAY_MX = get_mexico_today()

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None
st.session_state.reference_date = _TODAY_MX
if "parsed_geo" not in st.session_state:
    st.session_state.parsed_geo = None
if "cal_year" not in st.session_state:
    st.session_state.cal_year = _TODAY_MX.year
if "cal_month" not in st.session_state:
    st.session_state.cal_month = _TODAY_MX.month
if "cal_view_mode" not in st.session_state:
    st.session_state.cal_view_mode = "calendario"  # "calendario", "mensual", "completa"
if "zoomed_state_id" not in st.session_state:
    st.session_state.zoomed_state_id = None
if "map_nonce" not in st.session_state:
    st.session_state.map_nonce = 0


def get_active_role() -> str:
    if st.session_state.auth_user:
        return st.session_state.auth_user.get("rol", "visualizador")
    return "visualizador"


def get_active_role_meta() -> Dict[str, Any]:
    role_key = get_active_role()
    return db.ROLES_CONFIG.get(role_key, db.ROLES_CONFIG["visualizador"])


def shift_month(delta: int) -> None:
    m = st.session_state.cal_month + delta
    y = st.session_state.cal_year
    if m < 1:
        m = 12
        y -= 1
    elif m > 12:
        m = 1
        y += 1
    st.session_state.cal_month = m
    st.session_state.cal_year = y


def reset_map_zoom() -> None:
    st.session_state.zoomed_state_id = None
    st.session_state.map_nonce += 1


# ---------------------------------------------------------------------------
# ESTILOS CSS
# ---------------------------------------------------------------------------
def inject_executive_css() -> None:
    st.markdown(
        """
        <style>
        html, body, [class*="css"], .stApp {
            font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important;
            color: #0F172A;
            background-color: #F8FAFC !important;
        }

        /* 100% del ancho disponible en cualquier pantalla */
        .block-container {
            padding-top: 0.9rem !important;
            padding-bottom: 1.0rem !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
            max-width: 100% !important;
            width: 100% !important;
        }

        header[data-testid="stHeader"] {
            height: 0px !important;
            background: transparent !important;
        }

        /* Cabecera Institucional */
        .exec-topbar {
            background: #FFFFFF;
            border: 1px solid #E2E8F0;
            border-top: 3px solid #007A78;
            border-radius: 6px;
            padding: 10px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 10px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
        }

        .exec-brand {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .exec-logos {
            display: flex;
            align-items: center;
            gap: 10px;
            padding-right: 12px;
            border-right: 1px solid #E2E8F0;
        }

        .exec-logos img {
            height: 26px;
            width: auto;
            object-fit: contain;
        }

        .exec-title {
            font-size: 1.05rem;
            font-weight: 700;
            color: #0F172A;
            letter-spacing: -0.01em;
            margin: 0;
        }

        /* Franja KPI Ejecutiva */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 8px;
            margin-bottom: 10px;
        }

        .kpi-card {
            background: #FFFFFF;
            border: 1px solid #E2E8F0;
            border-left: 4px solid #0F172A;
            border-radius: 5px;
            padding: 10px 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .kpi-label {
            font-size: 0.74rem;
            font-weight: 700;
            color: #334155;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }

        .kpi-val {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 1.4rem;
            font-weight: 700;
            color: #0F172A;
            font-variant-numeric: tabular-nums;
            line-height: 1;
        }

        /* Encabezado de días de semana del calendario */
        .cal-weekday-bar {
            display: grid;
            grid-template-columns: repeat(7, minmax(0, 1fr));
            gap: 8px;
            margin-top: 6px;
            margin-bottom: 6px;
        }

        .cal-weekday-item {
            background: #0F172A;
            color: #FFFFFF;
            font-size: 0.73rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-align: center;
            padding: 6px 0;
            border-radius: 4px;
        }

        /* Título del mes centrado */
        .month-nav-title {
            text-align: center;
            font-size: 1.08rem;
            font-weight: 700;
            color: #0F172A;
            letter-spacing: -0.01em;
            padding: 4px 0;
            margin: 0;
            user-select: none;
        }

        /* Número de día (01, 02...) en bloque delimitado con borde inferior y margen limpio */
        .day-num-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.78rem;
            font-weight: 700;
            color: #1E293B;
            padding-top: 1px;
            padding-bottom: 4px;
            margin-top: 0px !important;
            margin-bottom: 4px !important;
            border-bottom: 1px solid #E2E8F0;
            line-height: 1.25;
            min-height: 22px;
            width: 100%;
            box-sizing: border-box;
            flex-shrink: 0;
            position: relative;
            z-index: 1;
        }

        .today-pill {
            background: #059669;
            color: #FFFFFF;
            font-size: 0.60rem;
            padding: 1px 5px;
            border-radius: 3px;
            font-weight: 700;
        }

        /* Filas de Agenda Completa */
        .ev-row-card {
            background: #FFFFFF;
            border: 1px solid #E2E8F0;
            border-left: 4px solid #0F172A;
            border-radius: 5px;
            padding: 8px 12px;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }

        .ev-badge {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.68rem;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 3px;
            border: 1px solid;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }

        .mono-date {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.78rem;
            font-weight: 600;
            color: #0F172A;
            font-variant-numeric: tabular-nums;
        }

        /* Botones generales */
        .stButton > button {
            font-family: 'IBM Plex Sans', sans-serif !important;
            font-weight: 600 !important;
            font-size: 0.78rem !important;
            padding: 0.26rem 0.7rem !important;
            border-radius: 4px !important;
            min-height: 34px !important;
            cursor: pointer !important;
        }

        div[data-testid="stTabs"] button {
            font-weight: 600 !important;
            font-size: 0.84rem !important;
            padding-top: 6px !important;
            padding-bottom: 6px !important;
            cursor: pointer !important;
        }

        /* Mapa adaptativo al 100% del ancho disponible */
        div[data-testid="stPlotlyChart"] {
            width: 100% !important;
        }

        /* Botón oculto de doble clic para reset de zoom estatal */
        div[class*="st-key-hidden_reset_map_btn"] {
            display: none !important;
        }

        /* Celdas del calendario interactivo: flujo vertical compacto y altura dinámica semanal */
        div[class*="cal_day_"],
        div[class*="st-key-cal_day_"],
        div[data-testid="stVerticalBlock"][class*="cal_day_"],
        .stVerticalBlock[class*="st-key-cal_day_"],
        div[data-testid="stColumn"] > div > div[class*="st-key-cal_day_"],
        div[class*="cal_day_"] [data-testid="stVerticalBlock"] {
            gap: 4px !important;
            row-gap: 4px !important;
            overflow: visible !important;
            overflow-y: visible !important;
        }

        div[class*="cal_day_"] {
            background: #FFFFFF !important;
            border: 1px solid #CBD5E1 !important;
            border-radius: 5px !important;
            padding: 6px 6px !important;
            box-sizing: border-box !important;
            margin-bottom: 4px !important;
        }

        div[class*="cal_day_empty_"],
        div[class*="cal_day_"][class*="_empty_"] {
            background: #F8FAFC !important;
            border: 1px dashed #CBD5E1 !important;
            border-radius: 5px !important;
            opacity: 0.5;
            box-sizing: border-box !important;
            margin-bottom: 4px !important;
        }

        div[class*="cal_day_past_"],
        div[class*="cal_day_"][class*="_past_"] {
            background: #F8FAFC !important;
            border: 1px solid #E2E8F0 !important;
            border-radius: 5px !important;
            padding: 6px 6px !important;
            box-sizing: border-box !important;
            margin-bottom: 4px !important;
        }

        div[class*="cal_day_today_"],
        div[class*="cal_day_"][class*="_today_"] {
            background: #ECFDF5 !important;
            border: 2px solid #059669 !important;
            border-radius: 5px !important;
            padding: 6px 6px !important;
            box-sizing: border-box !important;
            margin-bottom: 4px !important;
        }

        /* Eliminar márgenes de los contenedores de elementos directos dentro de cada celda */
        div[class*="cal_day_"] > div[data-testid="stElementContainer"],
        div[class*="st-key-cal_day_"] > div[data-testid="stElementContainer"] {
            margin-top: 0px !important;
            margin-bottom: 0px !important;
            padding-top: 0px !important;
            padding-bottom: 0px !important;
        }

        /* CRÍTICO: Neutralizar el margin-bottom: -1rem (-16px) nativo de Streamlit en stMarkdownContainer */
        div[class*="cal_day_"] [data-testid="stMarkdown"],
        div[class*="st-key-cal_day_"] [data-testid="stMarkdown"],
        div[class*="cal_day_"] [data-testid="stMarkdownContainer"],
        div[class*="st-key-cal_day_"] [data-testid="stMarkdownContainer"] {
            margin-top: 0px !important;
            margin-bottom: 0px !important;
            padding-top: 0px !important;
            padding-bottom: 0px !important;
        }

        /* Evitar que párrafos markdown agreguen margen inferior dentro de la celda */
        div[class*="cal_day_"] [data-testid="stMarkdownContainer"] p,
        div[class*="st-key-cal_day_"] [data-testid="stMarkdownContainer"] p {
            margin: 0 !important;
            padding: 0 !important;
        }

        /* Botones de evento dentro de celdas: espacio reducido al mínimo estricto */
        div[class*="cal_day_"] div[class*="st-key-calbtn_"],
        div[class*="st-key-cal_day_"] div[class*="st-key-calbtn_"] {
            margin-top: 0px !important;
            margin-bottom: 0px !important;
            padding: 0 !important;
            width: 100% !important;
            display: block !important;
            flex-shrink: 0 !important;
        }

        div[class*="st-key-calbtn_"] button,
        div[class*="st-key-calbtn_"] .stButton > button {
            min-height: 24px !important;
            height: 24px !important;
            padding: 1px 6px !important;
            font-size: 0.70rem !important;
            font-weight: 600 !important;
            border-radius: 4px !important;
            justify-content: flex-start !important;
            text-align: left !important;
            width: 100% !important;
            box-shadow: none !important;
            margin: 0 !important;
            overflow: hidden !important;
            white-space: nowrap !important;
            text-overflow: ellipsis !important;
            line-height: 1.1 !important;
        }

        div[class*="st-key-calbtn_"] button p {
            font-size: 0.70rem !important;
            font-weight: 600 !important;
            margin: 0 !important;
            overflow: hidden !important;
            white-space: nowrap !important;
            text-overflow: ellipsis !important;
            line-height: 1.1 !important;
        }

        /* Contorno Verde: HOY / VIGENTE */
        div[class*="st-key-calbtn_VIGENTE_"] button,
        div[class*="st-key-calbtn_VIGENTE_"] button[data-testid^="stBaseButton"] {
            background-color: #ECFDF5 !important;
            color: #065F46 !important;
            border: 2px solid #059669 !important;
            border-left: 5px solid #059669 !important;
        }

        /* Contorno Ámbar: PRÓXIMO (1 a 7 días) */
        div[class*="st-key-calbtn_PROXIMO_"] button,
        div[class*="st-key-calbtn_PROXIMO_"] button[data-testid^="stBaseButton"] {
            background-color: #FFFBEB !important;
            color: #92400E !important;
            border: 2px solid #D97706 !important;
            border-left: 5px solid #D97706 !important;
        }

        /* Contorno Azul: FUTURO (> 7 días) */
        div[class*="st-key-calbtn_FUTURO_"] button,
        div[class*="st-key-calbtn_FUTURO_"] button[data-testid^="stBaseButton"] {
            background-color: #EFF6FF !important;
            color: #1E40AF !important;
            border: 2px solid #2563EB !important;
            border-left: 5px solid #2563EB !important;
        }

        /* Contorno Gris: EXPIRADO */
        div[class*="st-key-calbtn_EXPIRADO_"] button,
        div[class*="st-key-calbtn_EXPIRADO_"] button[data-testid^="stBaseButton"] {
            background-color: #F1F5F9 !important;
            color: #475569 !important;
            border: 2px solid #94A3B8 !important;
            border-left: 5px solid #64748B !important;
        }

        div[class*="st-key-calbtn_"] button:hover {
            filter: brightness(0.95);
            transform: translateY(-1px);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_executive_css()

# Script JS: Permite que el doble clic regrese a la vista nacional cuando se hizo zoom a un estado.
# La sensibilidad del zoom del mapa se mantiene 100% nativa.
st.html(
    """
    <script>
    (function() {
        var doc = window.parent.document || document;
        function bindMapEnhancements() {
            var plots = doc.querySelectorAll('.js-plotly-plot');
            plots.forEach(function(gd) {
                if (gd.dataset.dblClickBound === "1") return;
                gd.dataset.dblClickBound = "1";

                // Doble clic: si hay zoom de estado, regresar al mapa nacional
                gd.addEventListener('dblclick', function(e) {
                    try {
                        var resetBtnContainer = doc.querySelector('div[class*="st-key-hidden_reset_map_btn"] button');
                        if (resetBtnContainer) {
                            resetBtnContainer.click();
                        }
                    } catch (err) {}
                }, { capture: true });
            });
        }
        var obs = new MutationObserver(bindMapEnhancements);
        obs.observe(doc.body, { childList: true, subtree: true });
        bindMapEnhancements();
    })();
    </script>
    """,
    unsafe_allow_javascript=True,
)


# ---------------------------------------------------------------------------
# MODALES (@st.dialog): LOGIN Y FICHA INDIVIDUAL DE EVENTO
# ---------------------------------------------------------------------------
@st.dialog("Control de Acceso", width="small")
def open_login_dialog() -> None:
    role_meta = get_active_role_meta()
    active_user = st.session_state.auth_user

    if active_user:
        st.markdown(
            f"**Usuario:** `{active_user['nombre_completo']}`  \n"
            f"**Cargo:** {active_user['cargo']}  \n"
            f"**Rol:** `{role_meta['nombre'].upper()}`"
        )
        if st.button("Cerrar sesión", width="stretch", type="primary"):
            st.session_state.auth_user = None
            st.rerun()
        st.divider()

    tab_cred, tab_fast = st.tabs(["Credenciales", "Cambio Rápido"])

    with tab_cred:
        with st.form("login_credentials_form", clear_on_submit=False):
            u_input = st.text_input("Usuario o Correo", placeholder="admin.general / director.promocion")
            p_input = st.text_input("Contraseña", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("Entrar", width="stretch", type="primary")
            if submitted:
                user_row = db.authenticate_user(u_input, p_input)
                if user_row:
                    st.session_state.auth_user = user_row
                    st.rerun()
                else:
                    st.error("Credenciales inválidas.")

    with tab_fast:
        users = db.list_users()
        for u in users:
            r_cfg = db.ROLES_CONFIG.get(u["rol"], db.ROLES_CONFIG["visualizador"])
            btn_label = f"Nivel {r_cfg['nivel']} · {r_cfg['nombre'].upper()} ({u['username']})"
            if st.button(btn_label, key=f"fast_role_{u['id']}", width="stretch"):
                st.session_state.auth_user = u
                st.rerun()

        if st.button("Nivel 1 · VISUALIZADOR (Público)", width="stretch"):
            st.session_state.auth_user = None
            st.rerun()


@st.dialog("Ficha Ejecutiva de Evento", width="large")
def open_event_detail_dialog(event_id: str) -> None:
    ev = db.get_event_by_id(event_id)
    if not ev:
        st.warning("Evento no encontrado.")
        return

    ref_date: dt.date = st.session_state.reference_date
    status = compute_temporal_status(ev["fecha_inicio"], ev["fecha_fin"], ref_date)
    rango_mx = format_date_range_mx(ev["fecha_inicio"], ev["fecha_fin"])
    gcal_url = build_google_calendar_url(ev)
    ics_data = build_ics_content(ev)

    st.markdown(
        f"""
        <div style="border-left: 5px solid {status['color']}; padding-left: 12px; margin-bottom: 12px;">
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:4px;">
                <span class="ev-badge" style="color:{status['color']}; background:{status['bg']}; border-color:{status['border']};">
                    {status['label']}
                </span>
                <span class="ev-badge" style="color:#0F172A; background:#F8FAFC; border-color:#CBD5E1;">
                    {ev['id']}
                </span>
                <span class="ev-badge" style="color:#005F73; background:#ECFEFF; border-color:#A5F3FC;">
                    {ev['categoria']}
                </span>
                <span class="ev-badge" style="color:#334155; background:#F1F5F9; border-color:#CBD5E1;">
                    {ev['modalidad']}
                </span>
            </div>
            <h3 style="margin:2px 0 4px 0; font-size:1.22rem; color:#0F172A;">{ev['nombre']}</h3>
            <div style="font-size:0.80rem; color:#475569; font-weight:600;">
                Promotor: {ev.get('promotor', 'NAFIN / BANCOMEXT')}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Fechas**")
        st.markdown(
            f"<span class='mono-date'>{rango_mx}</span> ({status['duration_days']}d)",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown("**Horario**")
        st.markdown(f"<span class='mono-date'>{ev['horario']}</span>", unsafe_allow_html=True)
    with c3:
        st.markdown("**Ubicación**")
        st.markdown(f"**{ev['estado']}** · {ev['municipio'] or 'N/A'}")

    st.markdown("---")
    st.markdown("**Descripción**")
    st.write(ev["descripcion"])

    if ev["modalidad"] != "En Línea":
        st.markdown(
            f"**Sede:** {ev['sede']} — {ev['direccion']} (C.P. {ev['codigo_postal'] or 'S/N'})"
        )

    st.markdown("---")
    act1, act2, act3 = st.columns(3)
    with act1:
        if ev.get("google_maps_url"):
            st.link_button("Abrir en Google Maps", ev["google_maps_url"], width="stretch")
    with act2:
        st.link_button("Agregar a Google Calendar", gcal_url, width="stretch")
    with act3:
        st.download_button(
            "Descargar Invitación (.ics)",
            data=ics_data.encode("utf-8"),
            file_name=f"{ev['id']}.ics",
            mime="text/calendar",
            width="stretch",
        )

    role_meta = get_active_role_meta()
    if role_meta["puede_registrar_eventos"]:
        st.markdown("---")
        col_del, _ = st.columns([1, 2])
        with col_del:
            if st.button("Eliminar Evento", key=f"del_ev_{ev['id']}", width="stretch"):
                db.delete_event(ev["id"])
                get_cached_events.clear()
                st.rerun()


def _clean_html_text(raw_val: Any) -> str:
    """Limpia sintaxis Markdown residual, colapsa saltos de línea y escapa caracteres HTML."""
    if raw_val is None:
        return ""
    txt = str(raw_val)
    txt = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", txt)
    txt = re.sub(r"(^|\s)#+\s*", " ", txt)
    txt = txt.replace("**", "").replace("__", "")
    txt = re.sub(r"[\r\n]+", " · ", txt)
    txt = re.sub(r"(?:\s*·\s*)+", " · ", txt)
    txt = re.sub(r"\s+", " ", txt).strip(" ·\"'")
    return html.escape(txt)


@st.dialog("Revisar y Publicar Evento Institucional", width="large")
def open_review_staging_dialog(staging_id: str) -> None:
    stg = db.get_staging_event_by_id(staging_id)
    if not stg:
        st.warning("Candidato IA no encontrado.")
        return

    ev_quote_clean = _clean_html_text(stg.get("evidencia_texto", ""))
    dialog_header_html = (
        f'<div style="background:#F8FAFC; border:1px solid #CBD5E1; border-left:4px solid #007A78; '
        f'padding:10px 14px; border-radius:5px; margin-bottom:12px;">'
        f'<div style="font-size:0.78rem; color:#475569; font-weight:600;">'
        f'ID Staging: <code>{html.escape(str(stg["staging_id"]))}</code> · '
        f'Confianza IA: <b>{float(stg.get("confianza_score", 0))*100:.0f}%</b>'
        f'</div>'
        f'<blockquote style="margin:6px 0 0 0; padding-left:10px; border-left:3px solid #94A3B8; '
        f'font-size:0.82rem; color:#1E293B;">'
        f'"{ev_quote_clean}"'
        f'</blockquote>'
        f'</div>'
    )
    st.markdown(dialog_header_html, unsafe_allow_html=True)

    geo_override_key = f"stg_geo_{staging_id}"
    g_c1, g_c2 = st.columns([4.0, 1.5])
    with g_c1:
        recalc_q = st.text_input(
            "Recalcular Geocodificación (Recinto, Dirección o Google Maps)",
            value=stg.get("sede") or stg.get("direccion") or "",
            key=f"recalc_input_{staging_id}",
        )
    with g_c2:
        st.write("")
        if st.button("Recalcular con geo_utils", key=f"btn_recalc_{staging_id}", width="stretch"):
            res_geo = parse_google_maps_or_search(recalc_q)
            if res_geo.get("ok"):
                st.session_state[geo_override_key] = res_geo
                st.success(
                    f"Ubicación actualizada: {res_geo['municipio']}, {res_geo['estado']} "
                    f"({res_geo['latitud']}, {res_geo['longitud']})"
                )

    geo_cur = st.session_state.get(geo_override_key) or {}
    default_sede = geo_cur.get("sede", stg.get("sede") or "")
    default_dir = geo_cur.get("direccion", stg.get("direccion") or "")
    default_estado = geo_cur.get("estado", stg.get("estado") or "Ciudad de México")
    default_muni = geo_cur.get("municipio", stg.get("municipio") or "")
    default_cp = geo_cur.get("codigo_postal", stg.get("codigo_postal") or "")
    default_lat = float(geo_cur.get("latitud") or stg.get("latitud") or 19.4280)
    default_lon = float(geo_cur.get("longitud") or stg.get("longitud") or -99.1480)
    default_gmaps = geo_cur.get("google_maps_url", stg.get("google_maps_url") or "")

    try:
        d_ini_val = dt.date.fromisoformat(str(stg.get("fecha_inicio", "2026-11-10"))[:10])
    except Exception:
        d_ini_val = dt.date(2026, 11, 10)
    try:
        d_fin_val = dt.date.fromisoformat(str(stg.get("fecha_fin", d_ini_val.isoformat()))[:10])
    except Exception:
        d_fin_val = d_ini_val

    with st.form(f"approve_stg_form_{staging_id}", clear_on_submit=False):
        next_official_id = db.generate_next_event_id()
        c_id, c_nom, c_cat = st.columns([1.2, 3.2, 1.6])
        with c_id:
            in_id = st.text_input("ID Oficial", value=next_official_id)
        with c_nom:
            in_nombre = st.text_input("Nombre del Evento *", value=stg.get("nombre") or "")
        with c_cat:
            cat_idx = db.CATEGORIES.index(stg["categoria"]) if stg.get("categoria") in db.CATEGORIES else 0
            in_cat = st.selectbox("Categoría *", options=db.CATEGORIES, index=cat_idx)

        c_d1, c_d2, c_hor, c_mod = st.columns([1.3, 1.3, 1.4, 1.3])
        with c_d1:
            in_d_ini = st.date_input("Fecha Inicio *", value=d_ini_val, format="DD/MM/YYYY")
        with c_d2:
            in_d_fin = st.date_input("Fecha Fin *", value=d_fin_val, format="DD/MM/YYYY")
        with c_hor:
            in_horario = st.text_input("Horario *", value=stg.get("horario") or "09:00 - 18:00 hrs")
        with c_mod:
            mod_opts = ["Presencial", "En Línea", "Híbrido"]
            raw_mod = "En Línea" if stg.get("modalidad") == "Virtual" else (stg.get("modalidad") or "Presencial")
            mod_idx = mod_opts.index(raw_mod) if raw_mod in mod_opts else 0
            in_mod = st.selectbox("Modalidad *", options=mod_opts, index=mod_idx)

        state_keys = list(MEXICO_STATES.keys())
        st_idx = state_keys.index(default_estado) if default_estado in state_keys else state_keys.index("Ciudad de México")
        c_l1, c_l2, c_l3, c_l4 = st.columns([1.8, 1.4, 1.4, 0.9])
        with c_l1:
            in_sede = st.text_input("Recinto / Sede", value=default_sede)
        with c_l2:
            in_estado = st.selectbox("Estado", options=state_keys, index=st_idx)
        with c_l3:
            in_muni = st.text_input("Municipio / Alcaldía", value=default_muni)
        with c_l4:
            in_cp = st.text_input("C.P.", value=default_cp)

        c_a1, c_a2, c_a3, c_a4 = st.columns([2.4, 2.0, 0.9, 0.9])
        with c_a1:
            in_dir = st.text_input("Dirección", value=default_dir)
        with c_a2:
            in_gmaps = st.text_input("Link Google Maps", value=default_gmaps)
        with c_a3:
            in_lat = st.number_input("Latitud", value=default_lat, format="%.4f")
        with c_a4:
            in_lon = st.number_input("Longitud", value=default_lon, format="%.4f")

        c_p1, c_p2 = st.columns([2, 2])
        with c_p1:
            in_promotor = st.text_input("Promotor", value=stg.get("promotor") or "NAFIN / BANCOMEXT")
        with c_p2:
            in_web = st.text_input("Sitio Web", value=stg.get("sitio_web") or stg.get("fuente_url") or "")

        in_desc = st.text_area("Descripción *", value=stg.get("descripcion") or "", height=90)

        confirm_btn = st.form_submit_button(
            "Confirmar Publicación Oficial",
            type="primary",
            width="stretch",
        )
        if confirm_btn:
            if not in_nombre.strip() or not in_desc.strip():
                st.error("El nombre y la descripción son obligatorios.")
            elif in_d_fin < in_d_ini:
                st.error("La fecha fin no puede ser menor que la fecha inicio.")
            else:
                final_gmaps = in_gmaps.strip()
                if not final_gmaps and in_mod != "En Línea":
                    final_gmaps = f"https://www.google.com/maps/search/?api=1&query={in_lat},{in_lon}"

                current_username = (st.session_state.auth_user or {}).get("username", "director")
                official_id = db.approve_staging_event(
                    staging_id=staging_id,
                    event_payload={
                        "id": in_id.strip(),
                        "nombre": in_nombre.strip(),
                        "descripcion": in_desc.strip(),
                        "fecha_inicio": in_d_ini.isoformat(),
                        "fecha_fin": in_d_fin.isoformat(),
                        "horario": in_horario.strip(),
                        "modalidad": in_mod,
                        "sede": in_sede.strip(),
                        "direccion": in_dir.strip(),
                        "estado": in_estado,
                        "municipio": in_muni.strip(),
                        "codigo_postal": in_cp.strip(),
                        "latitud": in_lat,
                        "longitud": in_lon,
                        "google_maps_url": final_gmaps,
                        "categoria": in_cat,
                        "promotor": in_promotor.strip(),
                        "sitio_web": in_web.strip(),
                    },
                    reviewed_by=current_username,
                )
                st.session_state.pop(geo_override_key, None)
                get_cached_events.clear()
                st.success(f"Evento publicado oficialmente como {official_id} y alertas (.ics) despachadas.")
                st.rerun()


# ---------------------------------------------------------------------------
# PREPARACIÓN DE DATOS Y ENRIQUECIMIENTO TEMPORAL
# ---------------------------------------------------------------------------
ref_date: dt.date = st.session_state.reference_date
raw_events = get_cached_events()

enriched_events: List[Dict[str, Any]] = []
for item in raw_events:
    st_info = compute_temporal_status(item["fecha_inicio"], item["fecha_fin"], ref_date)
    enriched_events.append(
        {
            **item,
            "fecha_inicio_mx": format_date_mx(item["fecha_inicio"]),
            "fecha_fin_mx": format_date_mx(item["fecha_fin"]),
            "rango_fechas_mx": format_date_range_mx(item["fecha_inicio"], item["fecha_fin"]),
            "status_code": st_info["code"],
            "status_label": st_info["label"],
            "status_delta": st_info["short_delta"],
            "status_color": st_info["color"],
            "status_bg": st_info["bg"],
            "status_border": st_info["border"],
            "sort_rank": st_info["sort_rank"],
            "duration_days": st_info["duration_days"],
        }
    )

total_count = len(enriched_events)
vigentes_count = sum(1 for e in enriched_events if e["status_code"] == "VIGENTE")
proximos_count = sum(1 for e in enriched_events if e["status_code"] == "PROXIMO")
futuros_count = sum(1 for e in enriched_events if e["status_code"] == "FUTURO")
expirados_count = sum(1 for e in enriched_events if e["status_code"] == "EXPIRADO")

# ---------------------------------------------------------------------------
# CABECERA LIMPIA + LOGIN
# ---------------------------------------------------------------------------
b64_bancomext = load_logo_b64(LOGO_BANCOMEXT)
b64_nafin = load_logo_b64(LOGO_NAFIN)
role_meta = get_active_role_meta()
active_user = st.session_state.auth_user

top_left, top_right = st.columns([5.5, 1.0])
with top_left:
    logos_html = ""
    if b64_nafin and b64_bancomext:
        logos_html = (
            f'<div class="exec-logos">'
            f'<img src="data:image/png;base64,{b64_nafin}" alt="NAFIN"/>'
            f'<img src="data:image/png;base64,{b64_bancomext}" alt="BANCOMEXT"/>'
            f"</div>"
        )
    st.markdown(
        f"""
        <div class="exec-topbar">
            <div class="exec-brand">
                {logos_html}
                <div class="exec-title">CALENDARIO CENTRALIZADO DE EVENTOS</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with top_right:
    login_btn_text = active_user["username"] if active_user else "Login"
    if st.button(login_btn_text, width="stretch", type="primary" if not active_user else "secondary"):
        open_login_dialog()

# ---------------------------------------------------------------------------
# FRANJA DE INDICADORES EJECUTIVOS
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="kpi-grid">
        <div class="kpi-card" style="border-left-color:#0F172A;">
            <div class="kpi-label">Total Eventos</div>
            <div class="kpi-val">{total_count}</div>
        </div>
        <div class="kpi-card" style="border-left-color:#059669; background:#F0FDF4;">
            <div class="kpi-label" style="color:#065F46;">Vigentes Hoy</div>
            <div class="kpi-val" style="color:#059669;">{vigentes_count}</div>
        </div>
        <div class="kpi-card" style="border-left-color:#D97706;">
            <div class="kpi-label" style="color:#92400E;">Próximos ≤ 7 Días</div>
            <div class="kpi-val" style="color:#D97706;">{proximos_count}</div>
        </div>
        <div class="kpi-card" style="border-left-color:#2563EB;">
            <div class="kpi-label" style="color:#1E40AF;">Futuros &gt; 7 Días</div>
            <div class="kpi-val" style="color:#2563EB;">{futuros_count}</div>
        </div>
        <div class="kpi-card" style="border-left-color:#64748B;">
            <div class="kpi-label" style="color:#475569;">Expirados</div>
            <div class="kpi-val" style="color:#64748B;">{expirados_count}</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# BARRA DE FILTROS GLOBALES
# ---------------------------------------------------------------------------
f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1.4, 1.4, 1.5, 1.1, 1.6])

with f_col1:
    status_filter = st.selectbox(
        "Estatus Temporal",
        options=[
            "TODOS",
            "VIGENTES Y FUTUROS",
            "HOY · VIGENTE",
            "PRÓXIMOS (≤ 7 DÍAS)",
            "FUTUROS (> 7 DÍAS)",
            "EXPIRADOS",
        ],
        index=0,
    )

with f_col2:
    all_states_in_events = sorted({e["estado"] for e in enriched_events})
    state_filter = st.selectbox("Entidad Federativa", options=["TODOS"] + all_states_in_events, index=0)

with f_col3:
    cat_filter = st.selectbox("Categoría Sectorial", options=["TODAS"] + db.CATEGORIES, index=0)

with f_col4:
    mod_filter = st.selectbox("Modalidad", options=["TODAS", "Presencial", "En Línea", "Híbrido"], index=0)

with f_col5:
    search_filter = st.text_input("Filtro Rápido", placeholder="Evento, sede, municipio, ID...")


def apply_filters(events_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    q = (search_filter or "").strip().lower()
    for ev in events_list:
        if status_filter == "VIGENTES Y FUTUROS" and ev["status_code"] == "EXPIRADO":
            continue
        if status_filter == "HOY · VIGENTE" and ev["status_code"] != "VIGENTE":
            continue
        if status_filter == "PRÓXIMOS (≤ 7 DÍAS)" and ev["status_code"] != "PROXIMO":
            continue
        if status_filter == "FUTUROS (> 7 DÍAS)" and ev["status_code"] != "FUTURO":
            continue
        if status_filter == "EXPIRADOS" and ev["status_code"] != "EXPIRADO":
            continue
        if state_filter != "TODOS" and ev["estado"] != state_filter:
            continue
        if cat_filter != "TODAS" and ev["categoria"] != cat_filter:
            continue
        if mod_filter != "TODAS" and ev["modalidad"] != mod_filter:
            continue
        if q:
            haystack = f"{ev['id']} {ev['nombre']} {ev['sede']} {ev['municipio']} {ev['estado']} {ev['categoria']}".lower()
            if q not in haystack:
                continue
        out.append(ev)
    return out


filtered_events = apply_filters(enriched_events)


# ---------------------------------------------------------------------------
# PESTAÑAS SEGÚN ROL
# ---------------------------------------------------------------------------
tab_titles = [
    "Calendario",
    "Mapa Nacional",
]

if role_meta["recibe_alertas"]:
    tab_titles.append("Notificaciones")

if role_meta["puede_registrar_eventos"]:
    tab_titles.append("Registrar Evento")
    staging_kpis_nav = db.get_staging_kpis()
    tab_titles.append(f"🤖 Bandeja IA ({staging_kpis_nav['total_pendientes']})")

if role_meta["puede_administrar_usuarios"]:
    tab_titles.append("Administración")

tabs = st.tabs(tab_titles)
tab_idx = 0


# ===========================================================================
# VISTA 1: CALENDARIO INTERACTIVO (ALTO HOMOGÉNEO POR MES + NÚMERO 01, 02...)
# ===========================================================================
with tabs[tab_idx]:
    tab_idx += 1

    cur_mode = st.session_state.get("cal_view_mode", "calendario")
    year_num = st.session_state.cal_year
    month_num = st.session_state.cal_month
    nombre_mes = MESES_ES.get(month_num, "")

    # Barra de Controles:
    # 0% - 33%: Botones Calendario, Agenda Mensual, Agenda Completa
    # 33% - 66%: Buscador por mes (visible en Calendario y Agenda Mensual; invisible en Agenda Completa)
    # 66% - 100%: Botón Descargar PDF (se descarga el reporte que el usuario está viendo)
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([1, 1, 1])

    with ctrl_col1:
        sub_c1, sub_c2, sub_c3 = st.columns(3)
        with sub_c1:
            if st.button("Calendario", key="btn_mode_cal", width="stretch", type="primary" if cur_mode == "calendario" else "secondary"):
                st.session_state.cal_view_mode = "calendario"
                st.rerun()
        with sub_c2:
            if st.button("Agenda Mensual", key="btn_mode_mensual", width="stretch", type="primary" if cur_mode == "mensual" else "secondary"):
                st.session_state.cal_view_mode = "mensual"
                st.rerun()
        with sub_c3:
            if st.button("Agenda Completa", key="btn_mode_completa", width="stretch", type="primary" if cur_mode == "completa" else "secondary"):
                st.session_state.cal_view_mode = "completa"
                st.rerun()

    with ctrl_col2:
        # En la Agenda Completa, el mes y las flechas son invisibles para evitar confusión
        if cur_mode in ("calendario", "mensual"):
            b_prev, b_title, b_next = st.columns([0.8, 3.2, 0.8])
            with b_prev:
                st.button("◀", key="btn_prev_month", width="stretch", on_click=shift_month, args=(-1,))
            with b_title:
                st.markdown(
                    f'<div class="month-nav-title">{nombre_mes} {year_num}</div>',
                    unsafe_allow_html=True,
                )
            with b_next:
                st.button("▶", key="btn_next_month", width="stretch", on_click=shift_month, args=(1,))
        else:
            st.write("")

    with ctrl_col3:
        # Generación del reporte en PDF correspondiente al modo actual (con caché en memoria)
        if cur_mode == "completa":
            pdf_bytes = cached_agenda_pdf(
                json.dumps(filtered_events, ensure_ascii=False, sort_keys=True),
                title="AGENDA COMPLETA DE EVENTOS",
                logo_nafin=LOGO_NAFIN,
                logo_bancomext=LOGO_BANCOMEXT,
            )
            pdf_fname = "Agenda_Completa_Bancomext_NAFIN.pdf"
        elif cur_mode == "mensual":
            m_start = dt.date(year_num, month_num, 1)
            _, last_d = calendar.monthrange(year_num, month_num)
            m_end = dt.date(year_num, month_num, last_d)
            monthly_evs = [
                ev
                for ev in filtered_events
                if dt.date.fromisoformat(ev["fecha_inicio"]) <= m_end and dt.date.fromisoformat(ev["fecha_fin"]) >= m_start
            ]
            pdf_bytes = cached_agenda_pdf(
                json.dumps(monthly_evs, ensure_ascii=False, sort_keys=True),
                title=f"AGENDA DE EVENTOS · {nombre_mes.upper()} {year_num}",
                logo_nafin=LOGO_NAFIN,
                logo_bancomext=LOGO_BANCOMEXT,
            )
            pdf_fname = f"Agenda_{nombre_mes}_{year_num}_Bancomext.pdf"
        else:  # cur_mode == "calendario"
            pdf_bytes = cached_calendar_grid_pdf(
                json.dumps(filtered_events, ensure_ascii=False, sort_keys=True),
                year=year_num,
                month=month_num,
                month_name=nombre_mes,
                logo_nafin=LOGO_NAFIN,
                logo_bancomext=LOGO_BANCOMEXT,
            )
            pdf_fname = f"Calendario_{nombre_mes}_{year_num}_Bancomext.pdf"

        st.download_button(
            "Descargar PDF",
            data=pdf_bytes,
            file_name=pdf_fname,
            mime="application/pdf",
            width="stretch",
            key=f"btn_pdf_dl_{cur_mode}",
        )

    # -----------------------------------------------------------------------
    # VISTA 1A: AGENDA COMPLETA (TODOS LOS MESES, FLECHAS INVISIBLES)
    # -----------------------------------------------------------------------
    if cur_mode == "completa":
        if not filtered_events:
            st.info("Sin registros.")
        else:
            for ev in filtered_events:
                r_col1, r_col2 = st.columns([6.3, 0.9])
                with r_col1:
                    opacity_style = "opacity: 0.72;" if ev["status_code"] == "EXPIRADO" else ""
                    st.markdown(
                        f"""
                        <div class="ev-row-card" style="border-left-color:{ev['status_color']}; {opacity_style}">
                            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; min-width:0;">
                                <span class="ev-badge" style="color:{ev['status_color']}; background:{ev['status_bg']}; border-color:{ev['status_border']};">
                                    {ev['status_label']}
                                </span>
                                <span class="mono-date">{ev['rango_fechas_mx']}</span>
                                <span style="font-weight:700; font-size:0.88rem; color:#0F172A;">{ev['nombre']}</span>
                            </div>
                            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                                <span class="ev-badge" style="color:#0F172A; background:#F8FAFC; border-color:#CBD5E1;">
                                    {ev['municipio']}, {ev['estado']}
                                </span>
                                <span class="ev-badge" style="color:#005F73; background:#ECFEFF; border-color:#A5F3FC;">
                                    {ev['categoria']}
                                </span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with r_col2:
                    if st.button("Ficha", key=f"row_btn_completa_{ev['id']}", width="stretch"):
                        open_event_detail_dialog(ev["id"])

    # -----------------------------------------------------------------------
    # VISTA 1B: AGENDA MENSUAL (CONECTADA AL BUSCADOR Y FLECHAS DE MES)
    # -----------------------------------------------------------------------
    elif cur_mode == "mensual":
        m_start = dt.date(year_num, month_num, 1)
        _, last_d = calendar.monthrange(year_num, month_num)
        m_end = dt.date(year_num, month_num, last_d)
        monthly_events = [
            ev
            for ev in filtered_events
            if dt.date.fromisoformat(ev["fecha_inicio"]) <= m_end and dt.date.fromisoformat(ev["fecha_fin"]) >= m_start
        ]
        if not monthly_events:
            st.info(f"Sin eventos programados para {nombre_mes} {year_num}.")
        else:
            for ev in monthly_events:
                r_col1, r_col2 = st.columns([6.3, 0.9])
                with r_col1:
                    opacity_style = "opacity: 0.72;" if ev["status_code"] == "EXPIRADO" else ""
                    st.markdown(
                        f"""
                        <div class="ev-row-card" style="border-left-color:{ev['status_color']}; {opacity_style}">
                            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; min-width:0;">
                                <span class="ev-badge" style="color:{ev['status_color']}; background:{ev['status_bg']}; border-color:{ev['status_border']};">
                                    {ev['status_label']}
                                </span>
                                <span class="mono-date">{ev['rango_fechas_mx']}</span>
                                <span style="font-weight:700; font-size:0.88rem; color:#0F172A;">{ev['nombre']}</span>
                            </div>
                            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                                <span class="ev-badge" style="color:#0F172A; background:#F8FAFC; border-color:#CBD5E1;">
                                    {ev['municipio']}, {ev['estado']}
                                </span>
                                <span class="ev-badge" style="color:#005F73; background:#ECFEFF; border-color:#A5F3FC;">
                                    {ev['categoria']}
                                </span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with r_col2:
                    if st.button("Ficha", key=f"row_btn_mensual_{ev['id']}", width="stretch"):
                        open_event_detail_dialog(ev["id"])

    # -----------------------------------------------------------------------
    # VISTA 1C: CALENDARIO INTERACTIVO (CÁLCULO SEMANAL DINÁMICO DE LUNES A DOMINGO)
    # -----------------------------------------------------------------------
    else:
        cal_matrix = calendar.Calendar(firstweekday=0).monthdayscalendar(year_num, month_num)

        # Agrupar eventos por día
        events_by_day: Dict[int, List[Dict[str, Any]]] = {}
        for ev in filtered_events:
            try:
                d_ini = dt.date.fromisoformat(ev["fecha_inicio"])
                d_fin = dt.date.fromisoformat(ev["fecha_fin"])
            except Exception:
                continue
            c_iter = d_ini
            while c_iter <= d_fin:
                if c_iter.year == year_num and c_iter.month == month_num:
                    events_by_day.setdefault(c_iter.day, []).append(ev)
                c_iter += dt.timedelta(days=1)

        # Cálculo dinámico de altura SEMANAL (de lunes a domingo):
        # Tamaño mínimo de 72px para semanas sin eventos; contempla cabecera .day-num-bar (30px) + padding/bordes + botones (29px c/u)
        week_css_rules: List[str] = []
        for w_idx, week in enumerate(cal_matrix):
            max_evs_in_week = max([len(events_by_day.get(d, [])) for d in week if d != 0] or [0])
            if max_evs_in_week == 0:
                h_px = 72
            else:
                h_px = max(78, 48 + max_evs_in_week * 29)
            week_css_rules.append(
                f'div[class*="cal_day_"][class*="_w{w_idx}_"], div[class*="cal_day_w{w_idx}_"] {{ min-height: {h_px}px !important; }}'
            )

        st.markdown(
            f"<style>{''.join(week_css_rules)}</style>",
            unsafe_allow_html=True,
        )

        days_header = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
        st.markdown(
            '<div class="cal-weekday-bar">'
            + "".join(f'<div class="cal-weekday-item">{d}</div>' for d in days_header)
            + "</div>",
            unsafe_allow_html=True,
        )

        for w_idx, week in enumerate(cal_matrix):
            cols = st.columns(7, gap="small")
            for d_idx, day_num in enumerate(week):
                with cols[d_idx]:
                    if day_num == 0:
                        with st.container(key=f"cal_day_w{w_idx}_empty_{d_idx}"):
                            st.write("")
                        continue

                    cell_date = dt.date(year_num, month_num, day_num)
                    day_str_short = f"{day_num:02d}"
                    is_today = cell_date == ref_date
                    is_past = cell_date < ref_date

                    if is_today:
                        cell_key = f"cal_day_w{w_idx}_today_{day_num}"
                    elif is_past:
                        cell_key = f"cal_day_w{w_idx}_past_{day_num}"
                    else:
                        cell_key = f"cal_day_w{w_idx}_norm_{day_num}"

                    today_badge = '<span class="today-pill">HOY</span>' if is_today else ""
                    day_events = events_by_day.get(day_num, [])

                    with st.container(key=cell_key):
                        st.markdown(
                            f'<div class="day-num-bar"><span>{day_str_short}</span>{today_badge}</div>',
                            unsafe_allow_html=True,
                        )
                        for ev in day_events:
                            short_title = ev["nombre"][:24] + ("…" if len(ev["nombre"]) > 24 else "")
                            btn_key = f"calbtn_{ev['status_code']}_{cell_date.isoformat()}_{ev['id']}"
                            if st.button(
                                short_title,
                                key=btn_key,
                                help=f"{ev['rango_fechas_mx']} · {ev['nombre']} ({ev['municipio']}, {ev['estado']})",
                                width="stretch",
                            ):
                                open_event_detail_dialog(ev["id"])


# ===========================================================================
# VISTA 2: MAPA NACIONAL SEAMLESS (100% ANCHO, SIN EXPIRADOS, ZOOM POR ESTADO)
# ===========================================================================
with tabs[tab_idx]:
    tab_idx += 1

    # Botón oculto activado por doble clic en JS para regresar al panorama nacional
    st.button("ResetMap", key="hidden_reset_map_btn", on_click=reset_map_zoom)

    mexico_geojson = load_mexico_geojson()

    # Excluir eventos expirados del mapa nacional (solo se muestran en el calendario)
    presencial_events = [
        e
        for e in filtered_events
        if e["modalidad"] != "En Línea" and e["status_code"] != "EXPIRADO"
    ]

    state_counts: Dict[str, int] = {meta["id"]: 0 for meta in MEXICO_STATES.values()}
    state_names_by_id: Dict[str, str] = {meta["id"]: name for name, meta in MEXICO_STATES.items()}

    for ev in presencial_events:
        st_id = ev.get("estado_id", "MX-CMX")
        if st_id in state_counts:
            state_counts[st_id] += 1

    active_state_ids = [sid for sid, cnt in state_counts.items() if cnt > 0]
    empty_state_ids = [sid for sid, cnt in state_counts.items() if cnt == 0]
    max_concentration = max(state_counts.values(), default=1)
    if max_concentration < 1:
        max_concentration = 1

    if st.session_state.zoomed_state_id:
        st_name = state_names_by_id.get(st.session_state.zoomed_state_id, st.session_state.zoomed_state_id)
        col_st1, col_st2 = st.columns([5, 1.4])
        with col_st1:
            st.markdown(f"**Estado enfocado:** `{st_name}`")
        with col_st2:
            if st.button("Vista Nacional", key="btn_back_national", width="stretch", type="primary"):
                reset_map_zoom()
                st.rerun()

    fig_map = go.Figure()

    # 1A. Estados con eventos: Coloreado vivo (Teal intenso por concentración de reuniones, sin leyenda de escala)
    if active_state_ids:
        active_z = [state_counts[sid] for sid in active_state_ids]
        active_names = [state_names_by_id[sid] for sid in active_state_ids]
        active_custom = [["STATE", sid, state_names_by_id[sid], state_counts[sid]] for sid in active_state_ids]

        fig_map.add_trace(
            go.Choroplethmap(
                geojson=mexico_geojson,
                locations=active_state_ids,
                z=active_z,
                featureidkey="properties.id",
                colorscale=[
                    [0.0, "#5EEAD4"],
                    [0.35, "#14B8A6"],
                    [0.7, "#0D9488"],
                    [1.0, "#115E59"],
                ],
                zmin=1,
                zmax=max(2, max_concentration),
                showscale=False,
                customdata=active_custom,
                hovertext=active_names,
                hovertemplate=(
                    "<b>%{hovertext}</b><br>"
                    "<span style='color:#7dd3c8; font-weight:700;'>Reuniones activas/próximas:</span> %{customdata[3]}"
                    "<extra></extra>"
                ),
                marker_line_color="#FFFFFF",
                marker_line_width=1.5,
                hoverlabel=dict(
                    bgcolor="#0F172A",
                    font_size=13,
                    font_family="IBM Plex Sans, sans-serif",
                    font_color="#F8FAFC",
                    bordercolor="#0F172A",
                    align="left",
                ),
            )
        )

    # 1B. Estados sin eventos: Gris claro neutro
    if empty_state_ids:
        empty_names = [state_names_by_id[sid] for sid in empty_state_ids]
        empty_custom = [["STATE", sid, state_names_by_id[sid], 0] for sid in empty_state_ids]

        fig_map.add_trace(
            go.Choroplethmap(
                geojson=mexico_geojson,
                locations=empty_state_ids,
                z=[0] * len(empty_state_ids),
                featureidkey="properties.id",
                colorscale=[[0.0, "#E2E8F0"], [1.0, "#E2E8F0"]],
                showscale=False,
                customdata=empty_custom,
                hovertext=empty_names,
                hovertemplate="<b>%{hovertext}</b><br>Sin reuniones programadas<extra></extra>",
                marker_line_color="#FFFFFF",
                marker_line_width=1.2,
                hoverlabel=dict(
                    bgcolor="#0F172A",
                    font_size=13,
                    font_family="IBM Plex Sans, sans-serif",
                    font_color="#F8FAFC",
                    bordercolor="#0F172A",
                    align="left",
                ),
            )
        )

    # 2. Capa de pines (Solo Vigentes, Próximos y Futuros; Expirados excluidos)
    status_groups = [
        ("VIGENTE", "#059669", 18),
        ("PROXIMO", "#D97706", 16),
        ("FUTURO", "#2563EB", 15),
    ]

    seen_coords: Dict[str, int] = {}

    for code_key, hex_color, marker_sz in status_groups:
        grp_events = [e for e in presencial_events if e["status_code"] == code_key]
        if not grp_events:
            continue

        lats, lons, hover_names, custom_rows = [], [], [], []
        for ev in grp_events:
            st_id = ev.get("estado_id", "MX-CMX")
            # Calibrar punto base dentro del polígono de su estado
            base_lat, base_lon = ensure_point_inside_state(
                float(ev["latitud"]),
                float(ev["longitud"]),
                st_id,
                mexico_geojson,
            )

            coord_key = f"{round(base_lat, 2)}_{round(base_lon, 2)}"
            idx_dup = seen_coords.get(coord_key, 0)
            seen_coords[coord_key] = idx_dup + 1

            if idx_dup > 0:
                angle = idx_dup * (2 * math.pi / 5)
                cand_lat = base_lat + 0.035 * math.cos(angle)
                cand_lon = base_lon + 0.035 * math.sin(angle)
                base_lat, base_lon = ensure_point_inside_state(cand_lat, cand_lon, st_id, mexico_geojson)

            lats.append(base_lat)
            lons.append(base_lon)
            hover_names.append(ev["nombre"])
            custom_rows.append(
                [
                    "EVENT",
                    ev["id"],
                    ev["rango_fechas_mx"],
                    ev["horario"],
                    ev["sede"],
                    f"{ev['municipio']}, {ev['estado']}",
                    ev["categoria"],
                ]
            )

        fig_map.add_trace(
            go.Scattermap(
                lat=lats,
                lon=lons,
                mode="markers",
                hovertext=hover_names,
                customdata=custom_rows,
                hovertemplate=(
                    "<b>%{hovertext}</b><br><br>"
                    "<span style='color:#7dd3c8; font-weight:700;'>Fechas:</span> %{customdata[2]}<br>"
                    "<span style='color:#7dd3c8; font-weight:700;'>Horario:</span> %{customdata[3]}<br>"
                    "<span style='color:#7dd3c8; font-weight:700;'>Sede:</span> %{customdata[4]}<br>"
                    "<span style='color:#7dd3c8; font-weight:700;'>Ubicación:</span> %{customdata[5]}<br>"
                    "<span style='color:#7dd3c8; font-weight:700;'>Categoría:</span> %{customdata[6]}"
                    "<extra></extra>"
                ),
                marker=dict(
                    size=marker_sz,
                    color=hex_color,
                    opacity=0.98,
                    allowoverlap=True,
                ),
                hoverlabel=dict(
                    bgcolor="#0F172A",
                    font_size=13,
                    font_family="IBM Plex Sans, sans-serif",
                    font_color="#F8FAFC",
                    bordercolor="#0F172A",
                    align="left",
                ),
                showlegend=False,
            )
        )

    # Encuadre responsivo y centrado nacional con margen superior para Baja California
    if st.session_state.zoomed_state_id:
        map_center, map_zoom = get_state_map_center_and_zoom(st.session_state.zoomed_state_id, mexico_geojson)
    else:
        map_center, map_zoom = {"lat": 23.6, "lon": -102.5}, 4.05

    fig_map.update_layout(
        map=dict(
            style="white-bg",
            center=map_center,
            zoom=map_zoom,
        ),
        margin={"r": 0, "t": 15, "l": 0, "b": 0, "pad": 0},
        height=720,
        autosize=True,
        paper_bgcolor="#F8FAFC",
        showlegend=False,
    )

    map_key = (
        f"mapa_nacional_mx__{status_filter}__{state_filter}__{cat_filter}__"
        f"{mod_filter}__{search_filter}__{st.session_state.zoomed_state_id}__{st.session_state.map_nonce}"
    )

    map_event = st.plotly_chart(
        fig_map,
        width="stretch",
        config={
            "displayModeBar": False,
            "scrollZoom": True,
            "doubleClick": False,
        },
        on_select="rerun",
        selection_mode="points",
        key=map_key,
    )

    raw_selection = {}
    if map_event and hasattr(map_event, "selection"):
        raw_selection = map_event.selection
        if not isinstance(raw_selection, dict) and hasattr(raw_selection, "items"):
            raw_selection = dict(raw_selection)
    elif isinstance(map_event, dict):
        raw_selection = map_event.get("selection", {})

    puntos = raw_selection.get("points", []) if isinstance(raw_selection, dict) else []
    if puntos:
        # Priorizar clic en Pin de Evento si ambos coincidieron, o clic en Estado para acercar
        clicked_event_id = None
        clicked_state_id = None

        for punto in puntos:
            cd = punto.get("customdata")
            if isinstance(cd, (list, tuple)) and len(cd) >= 2:
                kind = str(cd[0])
                val = str(cd[1])
                if kind == "EVENT":
                    clicked_event_id = val
                    break
                if kind == "STATE":
                    clicked_state_id = val

        if clicked_event_id:
            open_event_detail_dialog(clicked_event_id)
        elif clicked_state_id:
            if st.session_state.zoomed_state_id == clicked_state_id:
                st.session_state.zoomed_state_id = None
            else:
                st.session_state.zoomed_state_id = clicked_state_id
            st.session_state.map_nonce += 1
            st.rerun()


# ===========================================================================
# VISTA COMPLEMENTARIA (RECEPTOR / DIRECTOR / ADMIN): NOTIFICACIONES
# ===========================================================================
if role_meta["recibe_alertas"]:
    with tabs[tab_idx]:
        tab_idx += 1
        col_sub1, col_sub2 = st.columns([2.2, 3.8])
        with col_sub1:
            st.markdown("##### Suscripción Activa")
            u_info = st.session_state.auth_user or {}
            st.markdown(
                f"- **Titular:** {u_info.get('nombre_completo', 'Ejecutivo')}  \n"
                f"- **Correo (.ics):** `{u_info.get('email', '')}`  \n"
                f"- **WhatsApp:** `{u_info.get('telefono_whatsapp', 'No registrado')}`"
            )
            ev_sync_map = {f"{e['rango_fechas_mx']} · {e['nombre']}": e for e in enriched_events}
            chosen_sync_label = st.selectbox("Evento", options=list(ev_sync_map.keys()))
            chosen_ev = ev_sync_map[chosen_sync_label]
            st.link_button(
                "Enviar a Google Calendar",
                build_google_calendar_url(chosen_ev),
                width="stretch",
            )
            st.download_button(
                "Descargar (.ics)",
                data=build_ics_content(chosen_ev).encode("utf-8"),
                file_name=f"{chosen_ev['id']}.ics",
                mime="text/calendar",
                width="stretch",
            )

        with col_sub2:
            st.markdown("##### Bitácora de Alertas")
            logs = db.list_notification_logs(limit=15)
            if logs:
                df_logs = pd.DataFrame(logs)[
                    ["fecha_envio", "evento_id", "evento_nombre", "destinatario_email", "canal", "tipo_disparo", "estatus_envio"]
                ]
                df_logs.columns = ["Fecha Envío", "ID", "Evento", "Destinatario", "Canal", "Disparo", "Estatus"]
                st.dataframe(df_logs, width="stretch", hide_index=True)


# ===========================================================================
# VISTA 3: REGISTRO DE EVENTOS + BUSCADOR GOOGLE MAPS (DIRECTOR / ADMIN)
# ===========================================================================
if role_meta["puede_registrar_eventos"]:
    with tabs[tab_idx]:
        tab_idx += 1

        g_col1, g_col2 = st.columns([4.2, 1.3])
        with g_col1:
            gmaps_query = st.text_input(
                "Buscador Google Maps",
                placeholder="Recinto, dirección o enlace de Google Maps...",
            )
        with g_col2:
            st.write("")
            if st.button("Extraer Ubicación", width="stretch", type="primary"):
                res = parse_google_maps_or_search(gmaps_query)
                if res.get("ok"):
                    st.session_state.parsed_geo = res
                    st.success(
                        f"{res['sede']} -> {res['municipio']}, {res['estado']} ({res['latitud']}, {res['longitud']})"
                    )
                else:
                    st.warning(res.get("error", "No fue posible geocodificar la ubicación."))

        geo_defaults = st.session_state.parsed_geo or {
            "sede": "",
            "direccion": "",
            "estado": "Ciudad de México",
            "municipio": "Miguel Hidalgo",
            "codigo_postal": "11200",
            "latitud": 19.4280,
            "longitud": -99.1480,
            "google_maps_url": "",
        }

        with st.form("register_event_form", clear_on_submit=False):
            fc1, fc2, fc3 = st.columns([1.2, 3.2, 1.6])
            with fc1:
                next_id = db.generate_next_event_id()
                ev_id_in = st.text_input("ID", value=next_id)
            with fc2:
                ev_nombre_in = st.text_input("Nombre del Evento *")
            with fc3:
                ev_cat_in = st.selectbox("Categoría *", options=db.CATEGORIES)

            fd1, fd2, fd3, fd4 = st.columns([1.3, 1.3, 1.4, 1.3])
            with fd1:
                d_start = st.date_input("Fecha Inicio *", value=ref_date, format="DD/MM/YYYY")
            with fd2:
                d_end = st.date_input("Fecha Fin *", value=ref_date + dt.timedelta(days=1), format="DD/MM/YYYY")
            with fd3:
                ev_horario_in = st.text_input("Horario *", value="09:00 - 18:00 hrs")
            with fd4:
                ev_mod_in = st.selectbox("Modalidad *", options=["Presencial", "En Línea", "Híbrido"])

            fl1, fl2, fl3, fl4 = st.columns([1.8, 1.4, 1.4, 0.9])
            state_keys = list(MEXICO_STATES.keys())
            default_st_idx = (
                state_keys.index(geo_defaults["estado"])
                if geo_defaults.get("estado") in state_keys
                else state_keys.index("Ciudad de México")
            )
            with fl1:
                ev_sede_in = st.text_input("Recinto / Sede", value=geo_defaults.get("sede", ""))
            with fl2:
                ev_estado_in = st.selectbox("Estado", options=state_keys, index=default_st_idx)
            with fl3:
                ev_muni_in = st.text_input("Municipio / Alcaldía", value=geo_defaults.get("municipio", ""))
            with fl4:
                ev_cp_in = st.text_input("C.P.", value=geo_defaults.get("codigo_postal", ""))

            fa1, fa2, fa3, fa4 = st.columns([2.4, 2.0, 0.9, 0.9])
            with fa1:
                ev_dir_in = st.text_input("Dirección", value=geo_defaults.get("direccion", ""))
            with fa2:
                ev_gmaps_in = st.text_input("Link Google Maps", value=geo_defaults.get("google_maps_url", ""))
            with fa3:
                ev_lat_in = st.number_input("Latitud", value=float(geo_defaults.get("latitud", 19.4280)), format="%.4f")
            with fa4:
                ev_lon_in = st.number_input("Longitud", value=float(geo_defaults.get("longitud", -99.1480)), format="%.4f")

            fp1, fp2 = st.columns([2, 2])
            with fp1:
                ev_promotor_in = st.text_input("Promotor", value="NAFIN / BANCOMEXT")
            with fp2:
                ev_web_in = st.text_input("Sitio Web", placeholder="https://...")

            ev_desc_in = st.text_area("Descripción *", height=90)

            save_ev_btn = st.form_submit_button("Guardar Evento", type="primary", width="stretch")
            if save_ev_btn:
                if not ev_nombre_in.strip() or not ev_desc_in.strip():
                    st.error("Nombre y descripción son obligatorios.")
                elif d_end < d_start:
                    st.error("La fecha fin no puede ser anterior a la fecha inicio.")
                else:
                    final_gmaps = ev_gmaps_in.strip()
                    if not final_gmaps and ev_mod_in != "En Línea":
                        final_gmaps = f"https://www.google.com/maps/search/?api=1&query={ev_lat_in},{ev_lon_in}"

                    created_id = db.create_event(
                        {
                            "id": ev_id_in.strip(),
                            "nombre": ev_nombre_in,
                            "descripcion": ev_desc_in,
                            "fecha_inicio": d_start.isoformat(),
                            "fecha_fin": d_end.isoformat(),
                            "horario": ev_horario_in,
                            "modalidad": ev_mod_in,
                            "sede": ev_sede_in,
                            "direccion": ev_dir_in,
                            "estado": ev_estado_in,
                            "municipio": ev_muni_in,
                            "codigo_postal": ev_cp_in,
                            "latitud": ev_lat_in,
                            "longitud": ev_lon_in,
                            "google_maps_url": final_gmaps,
                            "categoria": ev_cat_in,
                            "promotor": ev_promotor_in,
                            "sitio_web": ev_web_in,
                        },
                        created_by=(st.session_state.auth_user or {}).get("username", "director"),
                    )
                    st.session_state.parsed_geo = None
                    get_cached_events.clear()
                    st.success(f"Evento {created_id} guardado.")
                    st.rerun()

    # =======================================================================
    # VISTA 3B: BANDEJA DE ENTRADA IA (HITL — EXCLUSIVO DIRECTOR L3 / ADMIN L4)
    # =======================================================================
    with tabs[tab_idx]:
        tab_idx += 1

        ia_kpis = db.get_staging_kpis()
        pending_items = db.list_staging_events(estatus="pendiente")

        # 1. KPIs en tarjetas ejecutivas
        st.markdown(
            f"""
            <div class="kpi-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
                <div class="kpi-card" style="border-left-color:#0F172A;">
                    <div class="kpi-label">Total Pendientes</div>
                    <div class="kpi-val">{ia_kpis['total_pendientes']}</div>
                </div>
                <div class="kpi-card" style="border-left-color:#059669; background:#F0FDF4;">
                    <div class="kpi-label" style="color:#065F46;">Confianza Alta (≥80%)</div>
                    <div class="kpi-val" style="color:#059669;">{ia_kpis['confianza_alta']}</div>
                </div>
                <div class="kpi-card" style="border-left-color:#D97706; background:#FFFBEB;">
                    <div class="kpi-label" style="color:#92400E;">Con Alertas de Duplicado</div>
                    <div class="kpi-val" style="color:#D97706;">{ia_kpis['alertas_duplicado']}</div>
                </div>
                <div class="kpi-card" style="border-left-color:#007A78;">
                    <div class="kpi-label" style="color:#0F766E;">Aprobados este Mes</div>
                    <div class="kpi-val" style="color:#007A78;">{ia_kpis['aprobados_mes']}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 2. Barra de Filtros Rápidos + Botón exclusivo para Admin (L4)
        is_admin = get_active_role() == "admin"
        if is_admin:
            ia_f1, ia_f2, ia_f3, ia_f4, ia_f5 = st.columns([1.4, 1.3, 1.3, 1.3, 1.5])
        else:
            ia_f1, ia_f2, ia_f3, ia_f4 = st.columns([1.5, 1.5, 1.5, 1.5])
            ia_f5 = None

        with ia_f1:
            ia_cat_filter = st.selectbox(
                "Categoría Sectorial (IA)",
                options=["TODAS"] + db.CATEGORIES,
                key="ia_filter_cat",
            )
        with ia_f2:
            ia_states = sorted({str(it.get("estado") or "Ciudad de México") for it in pending_items})
            ia_state_filter = st.selectbox(
                "Entidad Federativa (IA)",
                options=["TODOS"] + ia_states,
                key="ia_filter_state",
            )
        with ia_f3:
            ia_score_min = st.slider(
                "Confianza Mínima",
                min_value=0.0,
                max_value=1.0,
                value=0.0,
                step=0.05,
                key="ia_filter_score",
            )
        with ia_f4:
            ia_carril_map = {
                "TODOS": "TODOS",
                "🏛️ Fuente Oficial (semilla_oficial)": "semilla_oficial",
                "🌐 Hallazgo Web (descubrimiento_web)": "descubrimiento_web",
            }
            ia_carril_label = st.selectbox(
                "Carril de Origen",
                options=list(ia_carril_map.keys()),
                key="ia_filter_carril",
            )
            ia_carril_val = ia_carril_map[ia_carril_label]

        if is_admin and ia_f5 is not None:
            with ia_f5:
                st.write("")
                run_now_clicked = st.button(
                    "🔄 Ejecutar Búsqueda IA Ahora",
                    type="primary",
                    width="stretch",
                    key="btn_run_ai_discovery_now",
                )
        else:
            run_now_clicked = False

        if run_now_clicked and is_admin:
            import ai_discovery.pipeline as ai_pipeline

            with st.status(
                "Buscando eventos estratégicos en fuentes oficiales...",
                expanded=True,
            ) as status_box:
                res_stats = ai_pipeline.run_discovery_pipeline(
                    sector_especifico=ia_cat_filter if ia_cat_filter != "TODAS" else "TODOS",
                    max_urls_por_sector=5,
                    progress_callback=lambda msg: status_box.write(msg),
                )
                status_box.update(
                    label=(
                        f"Búsqueda completada: {res_stats['insertados_pendientes']} nuevos candidatos en bandeja "
                        f"({res_stats['omitidos_duplicados']} duplicados omitidos, "
                        f"{res_stats['bloqueados_memoria_negativa']} bloqueados por Memoria Negativa)."
                    ),
                    state="complete",
                    expanded=False,
                )
            st.rerun()

        # Filtrar candidatos pendientes
        filtered_pending: List[Dict[str, Any]] = []
        for stg in pending_items:
            if ia_cat_filter != "TODAS" and stg.get("categoria") != ia_cat_filter:
                continue
            if ia_state_filter != "TODOS" and stg.get("estado") != ia_state_filter:
                continue
            if float(stg.get("confianza_score") or 0.0) < ia_score_min:
                continue
            if ia_carril_val != "TODOS" and stg.get("origen_carril") != ia_carril_val:
                continue
            filtered_pending.append(stg)

        if not filtered_pending:
            st.info("No hay eventos pendientes de revisión en la Bandeja de Entrada IA con los filtros actuales.")
        else:
            motivos_map = {
                "Duplicado": "duplicado",
                "Irrelevante / Curso comercial": "irrelevante",
                "Fecha errónea": "fecha_erronea",
                "Fuente dudosa": "fuente_dudosa",
            }
            for stg in filtered_pending:
                stg_id = stg["staging_id"]
                score_val = float(stg.get("confianza_score") or 0.0)
                if score_val >= 0.80:
                    sc_color, sc_bg, sc_border = "#065F46", "#ECFDF5", "#059669"
                elif score_val >= 0.60:
                    sc_color, sc_bg, sc_border = "#92400E", "#FFFBEB", "#D97706"
                else:
                    sc_color, sc_bg, sc_border = "#991B1B", "#FEF2F2", "#DC2626"

                carril_badge = (
                    "🏛️ Fuente Oficial"
                    if stg.get("origen_carril") == "semilla_oficial"
                    else "🌐 Hallazgo Web"
                )
                rango_stg = format_date_range_mx(
                    str(stg.get("fecha_inicio", "")),
                    str(stg.get("fecha_fin", "")),
                )
                dup_id = stg.get("posible_duplicado_id")
                sim_max = float(stg.get("similitud_maxima") or 0.0)
                faltantes = stg.get("campos_faltantes") or []

                dup_banner_html = ""
                if dup_id:
                    dup_banner_html = (
                        f'<div style="background:#FFFBEB; border:1px solid #F59E0B; border-left:4px solid #D97706; '
                        f'color:#92400E; padding:6px 10px; border-radius:4px; font-size:0.78rem; font-weight:600; margin-top:6px;">'
                        f"⚠️ Posible duplicado del evento registrado <code>{dup_id}</code> "
                        f"(Similitud: {sim_max:.0%}). Verifica antes de aprobar."
                        f"</div>"
                    )

                missing_html = ""
                if faltantes:
                    pills = " ".join(
                        f'<span class="ev-badge" style="color:#92400E; background:#FEF3C7; border-color:#F59E0B;">'
                        f"Falta: {campo}</span>"
                        for campo in faltantes
                    )
                    missing_html = f'<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">{pills}</div>'

                fuente_url = (stg.get("fuente_url") or "#").strip()
                sitio_url = (stg.get("sitio_web") or fuente_url).strip()
                fuente_host = urlparse(fuente_url).netloc.replace("www.", "") or "fuente"
                sitio_host = urlparse(sitio_url).netloc.replace("www.", "") or "sitio oficial"

                if sitio_url.rstrip("/") != fuente_url.rstrip("/"):
                    links_footer_html = (
                        f'🔗 <a href="{html.escape(fuente_url)}" target="_blank" rel="noopener noreferrer">'
                        f'Cartelera auditada ({html.escape(fuente_host)})</a> &nbsp;|&nbsp; '
                        f'🌐 <a href="{html.escape(sitio_url)}" target="_blank" rel="noopener noreferrer">'
                        f'Sitio oficial del evento ({html.escape(sitio_host)})</a>'
                    )
                else:
                    links_footer_html = (
                        f'🌐 <a href="{html.escape(sitio_url)}" target="_blank" rel="noopener noreferrer">'
                        f'Portal y fuente oficial verificada ({html.escape(sitio_host)})</a>'
                    )

                safe_cat = _clean_html_text(stg.get("categoria", ""))
                safe_muni = _clean_html_text(stg.get("municipio") or "")
                safe_estado = _clean_html_text(stg.get("estado") or "")
                safe_est_id = _clean_html_text(stg.get("estado_id") or "")
                safe_nombre = _clean_html_text(stg.get("nombre", ""))
                safe_sede = _clean_html_text(stg.get("sede") or "Por confirmar")
                safe_promotor = _clean_html_text(stg.get("promotor") or "Por confirmar")
                safe_evidencia = _clean_html_text(stg.get("evidencia_texto", ""))

                card_html = (
                    f'<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:4px;">'
                    f'<span class="ev-badge" style="color:{sc_color}; background:{sc_bg}; border-color:{sc_border};">'
                    f'Confianza: {score_val:.0%}</span>'
                    f'<span class="ev-badge" style="color:#005F73; background:#ECFEFF; border-color:#A5F3FC;">'
                    f'{safe_cat}</span>'
                    f'<span class="ev-badge" style="color:#1E293B; background:#F1F5F9; border-color:#CBD5E1;">'
                    f'{carril_badge}</span>'
                    f'<span class="mono-date">{rango_stg}</span>'
                    f'<span class="ev-badge" style="color:#334155; background:#F8FAFC; border-color:#CBD5E1;">'
                    f'{safe_muni}, {safe_estado} ({safe_est_id})</span>'
                    f'</div>'
                    f'<div style="font-size:1.0rem; font-weight:700; color:#0F172A; margin-bottom:3px;">'
                    f'{safe_nombre}</div>'
                    f'<div style="font-size:0.80rem; color:#475569; margin-bottom:6px;">'
                    f'<b>Sede:</b> {safe_sede} · '
                    f'<b>Promotor:</b> {safe_promotor} · '
                    f'<b>ID Staging:</b> <code>{html.escape(stg_id)}</code>'
                    f'</div>'
                    f'{dup_banner_html}'
                    f'<blockquote style="margin:6px 0; padding:6px 10px; background:#F8FAFC; border-left:3px solid #007A78; font-size:0.79rem; color:#1E293B;">'
                    f'"{safe_evidencia}"'
                    f'<div style="margin-top:4px; font-size:0.74rem;">{links_footer_html}</div>'
                    f'</blockquote>'
                    f'{missing_html}'
                )

                with st.container(border=True):
                    c_info, c_actions = st.columns([4.8, 1.4])
                    with c_info:
                        st.markdown(card_html, unsafe_allow_html=True)
                    with c_actions:
                        if st.button(
                            "✅ Revisar y Aprobar",
                            key=f"btn_approve_stg_{stg_id}",
                            type="primary",
                            width="stretch",
                        ):
                            open_review_staging_dialog(stg_id)

                        with st.popover("🗑️ Descartar", width="stretch"):
                            motivo_label = st.selectbox(
                                "Motivo de descarte",
                                options=list(motivos_map.keys()),
                                key=f"sel_discard_{stg_id}",
                            )
                            if st.button(
                                "Confirmar Descarte",
                                key=f"btn_confirm_discard_{stg_id}",
                                type="secondary",
                                width="stretch",
                            ):
                                current_username = (st.session_state.auth_user or {}).get("username", "director")
                                db.discard_staging_event(
                                    staging_id=stg_id,
                                    motivo_descarte=motivos_map[motivo_label],
                                    reviewed_by=current_username,
                                )
                                get_cached_events.clear()
                                st.rerun()


# ===========================================================================
# VISTA 4: ADMINISTRACIÓN DE USUARIOS (EXCLUSIVO ADMIN)
# ===========================================================================
if role_meta["puede_administrar_usuarios"]:
    with tabs[tab_idx]:
        tab_idx += 1

        adm_col1, adm_col2 = st.columns([2.3, 3.7])

        with adm_col1:
            st.markdown("##### Nuevo Usuario")
            with st.form("admin_create_user_form", clear_on_submit=True):
                nu_username = st.text_input("Usuario *")
                nu_nombre = st.text_input("Nombre Completo *")
                nu_cargo = st.text_input("Área / Cargo *")
                nu_email = st.text_input("Correo *")
                nu_tel = st.text_input("WhatsApp (E.164)")
                nu_rol = st.selectbox(
                    "Rol *",
                    options=["visualizador", "receptor", "director", "admin"],
                    format_func=lambda r: f"Nivel {db.ROLES_CONFIG[r]['nivel']} · {db.ROLES_CONFIG[r]['nombre']}",
                )
                nu_pwd = st.text_input("Contraseña *", value="Bancomext2026!", type="password")

                c_ch1, c_ch2, c_ch3 = st.columns(3)
                with c_ch1:
                    nu_gcal = st.checkbox("Invitación .ics", value=True)
                with c_ch2:
                    nu_mail = st.checkbox("Correo SMTP", value=True)
                with c_ch3:
                    nu_wa = st.checkbox("WhatsApp", value=False)

                sub_user = st.form_submit_button("Registrar Usuario", type="primary", width="stretch")
                if sub_user:
                    ok, msg = db.create_user_one_by_one(
                        {
                            "username": nu_username,
                            "nombre_completo": nu_nombre,
                            "cargo": nu_cargo,
                            "email": nu_email,
                            "telefono_whatsapp": nu_tel,
                            "rol": nu_rol,
                            "password_plain": nu_pwd,
                            "recibe_gcal": nu_gcal,
                            "recibe_email": nu_mail,
                            "recibe_whatsapp": nu_wa,
                        }
                    )
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

        with adm_col2:
            st.markdown("##### Usuarios y Permisos")
            all_users = db.list_users()
            for u in all_users:
                uc1, uc2, uc3 = st.columns([3.2, 1.8, 1.1])
                with uc1:
                    st.markdown(
                        f"**{u['nombre_completo']}** (`{u['username']}`)  \n"
                        f"<span style='font-size:0.76rem; color:#475569;'>{u['cargo']} · {u['email']}</span>",
                        unsafe_allow_html=True,
                    )
                with uc2:
                    role_keys = ["visualizador", "receptor", "director", "admin"]
                    new_role_sel = st.selectbox(
                        "Rol",
                        options=role_keys,
                        index=role_keys.index(u["rol"]),
                        key=f"role_sel_{u['id']}",
                        label_visibility="collapsed",
                        format_func=lambda x: f"N{db.ROLES_CONFIG[x]['nivel']} · {db.ROLES_CONFIG[x]['nombre']}",
                    )
                with uc3:
                    if st.button("Actualizar", key=f"upd_u_{u['id']}", width="stretch"):
                        db.update_user_role(u["id"], new_role_sel, u["activo"])
                        st.rerun()

            st.markdown("---")
            d_col1, d_col2 = st.columns([3.5, 1.5])
            with d_col1:
                ev_dispatch_options = {f"{e['id']} · {e['rango_fechas_mx']} · {e['nombre']}": e["id"] for e in enriched_events}
                ev_disp_sel = st.selectbox("Despacho Invitación (.ics)", options=list(ev_dispatch_options.keys()))
            with d_col2:
                if st.button("Enviar .ics", width="stretch", type="primary"):
                    target_ev_id = ev_dispatch_options[ev_disp_sel]
                    recipients = [u for u in all_users if db.ROLES_CONFIG.get(u["rol"], {}).get("recibe_alertas")]
                    for rec in recipients:
                        db.log_notification_dispatch(
                            evento_id=target_ev_id,
                            usuario_id=rec["id"],
                            destinatario_email=rec["email"],
                            canal="Google Calendar (.ics)",
                            tipo_disparo="Invitación Directa Calendario",
                        )
                    st.success(f"Enviado a {len(recipients)} destinatarios.")
