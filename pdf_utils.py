"""
Módulo de generación de reportes ejecutivos en PDF para NAFIN / BANCOMEXT.
Produce reportes PDF profesionales, limpios y estáticos sin desbordes de texto,
sin colores temporales de proximidad y sin conteos relativos t-minus.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
from typing import Any, Dict, List
from fpdf import FPDF, XPos, YPos


_FONT_PATHS_CANDIDATES = [
    (
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\ariali.ttf",
    ),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    ),
    (
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansOblique.ttf",
    ),
]


def _setup_windows_or_core_font(pdf: FPDF) -> str:
    """Carga fuente TrueType UTF-8 (Windows Arial o Linux DejaVu/Liberation) o Helvetica como respaldo."""
    for regular, bold, italic in _FONT_PATHS_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdf.add_font("ExecFont", "", regular)
                pdf.add_font("ExecFont", "B", bold)
                if os.path.exists(italic):
                    pdf.add_font("ExecFont", "I", italic)
                else:
                    pdf.add_font("ExecFont", "I", regular)
                return "ExecFont"
            except Exception:
                continue
    return "Helvetica"


_UNICODE_REPLACEMENTS = str.maketrans({
    "\u2014": "-",   # em-dash
    "\u2013": "-",   # en-dash
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2022": "-",   # bullet
    "\u2026": "...", # ellipsis
    "\u00a0": " ",   # non-breaking space
})


def _safe_str(val: Any) -> str:
    """Sanea cadenas de texto para PDF garantizando compatibilidad multiplataforma (Windows/Linux)."""
    if val is None:
        return ""
    s = str(val).strip().replace("\r\n", " ").replace("\n", " ")
    s = s.translate(_UNICODE_REPLACEMENTS)
    return s.encode("latin-1", errors="replace").decode("latin-1")


# ===========================================================================
# 1. REPORTE TIPO AGENDA (MENSUAL O COMPLETA) EN FORMATO VERTICAL
# ===========================================================================
class ExecutiveAgendaDoc(FPDF):
    """Documento PDF institucional para agendas en formato lista/fichas ejecutivas."""

    def __init__(
        self,
        title_doc: str,
        subtitle_doc: str = "",
        logo_nafin: str = "",
        logo_bancomext: str = "",
    ):
        super().__init__(orientation="portrait", unit="mm", format="Letter")
        self.title_doc = _safe_str(title_doc)
        self.subtitle_doc = _safe_str(subtitle_doc)
        self.logo_nafin = logo_nafin
        self.logo_bancomext = logo_bancomext
        self.set_auto_page_break(auto=True, margin=15)
        self.font_name = _setup_windows_or_core_font(self)

    def header(self) -> None:
        has_l1 = bool(self.logo_nafin and os.path.exists(self.logo_nafin))
        has_l2 = bool(self.logo_bancomext and os.path.exists(self.logo_bancomext))

        if has_l1 and has_l2:
            try:
                self.image(self.logo_nafin, x=14, y=10, h=8)
                self.image(self.logo_bancomext, x=44, y=10, h=8)
            except Exception:
                pass
            y_text = 21.0
        elif has_l1:
            try:
                self.image(self.logo_nafin, x=14, y=10, h=8)
            except Exception:
                pass
            y_text = 21.0
        else:
            y_text = 12.0

        self.set_xy(14, y_text)
        self.set_font(self.font_name, "B", 13)
        self.set_text_color(15, 23, 42)  # #0F172A
        self.cell(0, 6, self.title_doc, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        if self.subtitle_doc:
            self.set_font(self.font_name, "", 8.5)
            self.set_text_color(71, 85, 105)  # #475569
            self.cell(0, 4.5, self.subtitle_doc, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        cur_y = self.get_y() + 1.5
        self.set_draw_color(203, 213, 225)  # #CBD5E1
        self.set_line_width(0.35)
        self.line(14, cur_y, 202, cur_y)
        self.set_y(cur_y + 3.5)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_draw_color(226, 232, 240)
        self.set_line_width(0.3)
        self.line(14, self.get_y(), 202, self.get_y())
        self.set_y(-9.5)
        self.set_font(self.font_name, "", 7.5)
        self.set_text_color(100, 116, 139)
        self.cell(
            0,
            4,
            f"NAFIN / BANCOMEXT · Documento Informativo Oficial · Página {self.page_no()}/{{nb}}",
            align="C",
        )


def generate_agenda_pdf(
    events: List[Dict[str, Any]],
    title: str = "AGENDA INSTITUCIONAL DE EVENTOS",
    subtitle: str = "Banco Nacional de Comercio Exterior / Nacional Financiera",
    logo_nafin: str = "",
    logo_bancomext: str = "",
) -> bytes:
    """
    Genera un reporte PDF estático en fichas ejecutivas (Letter vertical).
    Resuelve desbordes mediante multi_cell y saltos de página atómicos.
    Omite completamente 'Reporte Estático para Fines Informativos', colores y conteos t-minus.
    """
    pdf = ExecutiveAgendaDoc(
        title_doc=title,
        subtitle_doc=subtitle,
        logo_nafin=logo_nafin,
        logo_bancomext=logo_bancomext,
    )
    pdf.alias_nb_pages()
    pdf.add_page()
    fn = pdf.font_name

    total_evs = len(events)
    pdf.set_font(fn, "B", 9)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 5, f"REGISTROS TOTALES: {total_evs}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2.0)

    if not events:
        pdf.set_font(fn, "I", 10)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 10, "Sin eventos programados para este período.", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        return bytes(pdf.output())

    page_w = 202.0 - 14.0  # 188 mm de ancho útil

    for idx, ev in enumerate(events, 1):
        duracion_dias = ev.get("duration_days") or 1
        dias_label = f"{duracion_dias} día" if duracion_dias == 1 else f"{duracion_dias} días"

        rango_fechas = _safe_str(
            ev.get("rango_fechas_mx") or f"{ev.get('fecha_inicio_mx', '')} - {ev.get('fecha_fin_mx', '')}"
        )
        horario = _safe_str(ev.get("horario", "Por confirmar"))
        nombre = _safe_str(ev.get("nombre", "Sin título"))
        modalidad = _safe_str(ev.get("modalidad", "Presencial"))
        categoria = _safe_str(ev.get("categoria", "General"))
        sede = _safe_str(ev.get("sede", "Sede no especificada"))
        direccion = _safe_str(ev.get("direccion", ""))
        municipio = _safe_str(ev.get("municipio", ""))
        estado = _safe_str(ev.get("estado", ""))
        desc = _safe_str(ev.get("descripcion", ""))

        loc_text = f"Ubicación: {sede}"
        if direccion:
            loc_text += f" - {direccion}"
        if municipio or estado:
            loc_text += f" ({', '.join(filter(None, [municipio, estado]))})"
        loc_text = _safe_str(loc_text)

        meta_line_1 = _safe_str(
            f"Fechas: {rango_fechas} ({dias_label})    |    Horario: {horario}    |    Modalidad: {modalidad}    |    Categoría: {categoria}"
        )

        # Salto de página atómico preventivo: Si la tarjeta requiere > 32mm y no cabe en la página, iniciar página nueva
        card_est_h = 32.0 + (10.0 if desc else 0.0)
        if pdf.get_y() + card_est_h > 255.0:
            pdf.add_page()

        pdf.set_fill_color(248, 250, 252)  # #F8FAFC
        pdf.set_draw_color(203, 213, 225)  # #CBD5E1
        pdf.set_line_width(0.3)

        # 1. Título del evento
        pdf.set_font(fn, "B", 9.5)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(page_w, 6.0, f" {idx}. {nombre}", border="LTR", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # 2. Fechas, Duración y Horario con multi_cell para evitar desbordes
        pdf.set_font(fn, "", 8.0)
        pdf.set_text_color(30, 41, 59)
        pdf.multi_cell(page_w, 4.6, f" {meta_line_1}", border="LR", fill=False, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # 3. Ubicación con multi_cell para evitar que municipios/direcciones largas salgan del margen
        pdf.set_font(fn, "", 8.0)
        pdf.set_text_color(71, 85, 105)
        pdf.multi_cell(page_w, 4.6, f" {loc_text}", border="LR", fill=False, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # 4. Síntesis / Descripción
        if desc:
            pdf.set_font(fn, "I", 7.5)
            pdf.set_text_color(100, 116, 139)
            pdf.multi_cell(
                page_w,
                4.2,
                f" Síntesis: {desc[:250]}{'...' if len(desc) > 250 else ''}",
                border="LRB",
                fill=False,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        else:
            pdf.cell(page_w, 1.0, "", border="T", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.ln(3.0)

    return bytes(pdf.output())


# ===========================================================================
# 2. REPORTE TIPO CALENDARIO IMPRIMIBLE (CUADRÍCULA MENSUAL EN LANDSCAPE)
# ===========================================================================
class ExecutiveCalendarGridDoc(FPDF):
    """Documento PDF institucional para cuadrícula de calendario mensual apaisada (Landscape)."""

    def __init__(
        self,
        year: int,
        month: int,
        month_name: str,
        logo_nafin: str = "",
        logo_bancomext: str = "",
    ):
        super().__init__(orientation="landscape", unit="mm", format="Letter")
        self.year = year
        self.month = month
        self.month_name = month_name
        self.logo_nafin = logo_nafin
        self.logo_bancomext = logo_bancomext
        self.set_auto_page_break(auto=False)
        self.font_name = _setup_windows_or_core_font(self)

    def header(self) -> None:
        has_l1 = bool(self.logo_nafin and os.path.exists(self.logo_nafin))
        has_l2 = bool(self.logo_bancomext and os.path.exists(self.logo_bancomext))

        if has_l1 and has_l2:
            try:
                self.image(self.logo_nafin, x=12, y=8, h=8)
                self.image(self.logo_bancomext, x=40, y=8, h=8)
            except Exception:
                pass
            y_text = 18.0
        elif has_l1:
            try:
                self.image(self.logo_nafin, x=12, y=8, h=8)
            except Exception:
                pass
            y_text = 18.0
        else:
            y_text = 10.0

        self.set_xy(12, y_text)
        self.set_font(self.font_name, "B", 13)
        self.set_text_color(15, 23, 42)
        self.cell(
            0,
            5.5,
            f"CALENDARIO INSTITUCIONAL DE EVENTOS · {self.month_name.upper()} {self.year}",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

        self.set_font(self.font_name, "", 8.5)
        self.set_text_color(71, 85, 105)
        self.cell(
            0,
            4.0,
            "Banco Nacional de Comercio Exterior / Nacional Financiera",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

        cur_y = self.get_y() + 1.5
        self.set_draw_color(203, 213, 225)
        self.set_line_width(0.35)
        self.line(12, cur_y, 267, cur_y)
        self.set_y(cur_y + 3.0)

    def footer(self) -> None:
        self.set_y(-10)
        self.set_draw_color(226, 232, 240)
        self.set_line_width(0.3)
        self.line(12, self.get_y(), 267, self.get_y())
        self.set_y(-8.5)
        self.set_font(self.font_name, "", 7.5)
        self.set_text_color(100, 116, 139)
        self.cell(
            0,
            4,
            f"NAFIN / BANCOMEXT · Calendario Mensual Oficial · Página {self.page_no()}/{{nb}}",
            align="C",
        )


def generate_calendar_grid_pdf(
    events: List[Dict[str, Any]],
    year: int,
    month: int,
    month_name: str,
    logo_nafin: str = "",
    logo_bancomext: str = "",
) -> bytes:
    """
    Genera un reporte PDF con la cuadrícula mensual exacta (Letter Horizontal / Landscape).
    Presenta los días de lunes a domingo con los eventos de cada día, sin colores ni t-minus.
    """
    pdf = ExecutiveCalendarGridDoc(
        year=year,
        month=month,
        month_name=month_name,
        logo_nafin=logo_nafin,
        logo_bancomext=logo_bancomext,
    )
    pdf.alias_nb_pages()
    pdf.add_page()
    fn = pdf.font_name

    # Agrupar eventos por día del mes
    events_by_day: Dict[int, List[Dict[str, Any]]] = {}
    for ev in events:
        try:
            d_ini = dt.date.fromisoformat(ev["fecha_inicio"])
            d_fin = dt.date.fromisoformat(ev["fecha_fin"])
        except Exception:
            continue
        c_iter = d_ini
        while c_iter <= d_fin:
            if c_iter.year == year and c_iter.month == month:
                events_by_day.setdefault(c_iter.day, []).append(ev)
            c_iter += dt.timedelta(days=1)

    cal_matrix = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    num_weeks = len(cal_matrix)

    # Dimensiones de la cuadrícula
    margin_x = 12.0
    total_w = 267.0 - margin_x  # 255 mm
    col_w = total_w / 7.0       # ~36.4 mm por columna
    top_y = pdf.get_y()
    bottom_avail_y = 205.0      # Límite inferior antes del pie
    avail_h = bottom_avail_y - top_y - 7.0  # restar 7mm para cabecera de días
    row_h = min(34.0, max(24.0, avail_h / float(num_weeks)))

    # Encabezado de días de la semana
    pdf.set_fill_color(15, 23, 42)  # #0F172A
    pdf.set_text_color(255, 255, 255)
    pdf.set_draw_color(15, 23, 42)
    pdf.set_font(fn, "B", 8.0)

    days_hdr = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
    for d in days_hdr:
        pdf.cell(col_w, 6.0, d, border=1, align="C", fill=True)
    pdf.ln(6.0)

    # Celdas semanales
    pdf.set_draw_color(203, 213, 225)
    pdf.set_line_width(0.3)

    for week in cal_matrix:
        start_y = pdf.get_y()
        for d_idx, day_num in enumerate(week):
            cell_x = margin_x + d_idx * col_w

            if day_num == 0:
                # Día fuera del mes: fondo tenue
                pdf.set_fill_color(248, 250, 252)
                pdf.rect(cell_x, start_y, col_w, row_h, style="FD")
            else:
                # Celda del día
                pdf.set_fill_color(255, 255, 255)
                pdf.rect(cell_x, start_y, col_w, row_h, style="FD")

                # Número de fecha (01, 02...)
                pdf.set_xy(cell_x + 1.5, start_y + 1.5)
                pdf.set_font(fn, "B", 8.0)
                pdf.set_text_color(30, 41, 59)
                pdf.cell(col_w - 3.0, 4.0, f"{day_num:02d}", align="L")

                # Listar eventos dentro de la celda del día
                day_evs = events_by_day.get(day_num, [])
                ev_y = start_y + 6.0
                max_display = 3 if row_h > 27 else 2

                for ev_idx, ev in enumerate(day_evs[:max_display]):
                    pdf.set_xy(cell_x + 1.2, ev_y)
                    pdf.set_font(fn, "", 6.2)
                    pdf.set_text_color(15, 23, 42)
                    pdf.set_fill_color(241, 245, 249)  # fondo gris neutro sin colores de estatus
                    short_name = _safe_str(ev.get("nombre", ""))[:24]
                    pdf.cell(col_w - 2.4, 4.2, f" {short_name}", border=1, fill=True)
                    ev_y += 4.6

                if len(day_evs) > max_display:
                    rem = len(day_evs) - max_display
                    pdf.set_xy(cell_x + 1.2, ev_y)
                    pdf.set_font(fn, "I", 6.0)
                    pdf.set_text_color(100, 116, 139)
                    pdf.cell(col_w - 2.4, 3.5, f"+ {rem} más...", align="C")

        pdf.set_y(start_y + row_h)

    return bytes(pdf.output())
