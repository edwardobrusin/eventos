"""
Modelos Pydantic v2 y Guardrails anti-alucinación para extracción de eventos con IA.
"""

from __future__ import annotations

import re
from datetime import date
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from geo_utils import get_mexico_today

CATEGORIAS_OFICIALES = Literal[
    "Comercio Exterior",
    "Automotriz y Aeroespacial",
    "Manufactura Avanzada",
    "Semiconductores y Tech",
    "Agroindustria",
    "Turismo",
    "Dispositivos Médicos",
    "Plásticos y Químicos",
    "Electromovilidad",
    "Equipos de Generación y Distribución Eléctrica",
]

DOMINIOS_ACORTADORES_PROHIBIDOS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "ow.ly",
    "is.gd",
    "buff.ly",
    "rb.gy",
    "cutt.ly",
}

DOMINIOS_REDES_NO_SITIO_OFICIAL = {
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "open.spotify.com",
}

TERMINOS_NO_ESTRATEGICOS_B2C = (
    "concierto",
    "boda",
    "quinceañera",
    "quinceanera",
    "graduacion",
    "graduación",
    "bazar navideño",
    "bazar holistico",
    "bazar holístico",
    "show comico",
    "stand up",
    "festival musical",
    "lucha libre",
    "expo publicidad",
    "exposicion internacional de publicidad",
    "exposición internacional de publicidad",
    "confuror",
    "la conve",
    "expo tu casa",
    "motorart",
    "paraiso del juguete",
    "paraíso del juguete",
    "expo monedas",
    "iglesia apostolica",
    "iglesia apostólica",
    "horror fest",
    "artes marciales",
    "expo guau",
    "fit weekend",
    "trading show",
    "moroleon en cintermex",
    "moroleón en cintermex",
    "autoagencias",
    "feria del empleo",
    "congreso emprendete",
    "congreso empréndete",
    "expociencias",
    "congreso nacional de oncologia",
    "congreso nacional de oncología",
    "notariado mexicano",
    "nutricion y endocrinologia",
    "nutrición y endocrinología",
    "medicina general",
    "salon del chocolate",
    "salón del chocolate",
)

ACRONYMS_UPPER = {
    "b2b",
    "comce",
    "ina",
    "amia",
    "canieti",
    "canacintra",
    "aniq",
    "amid",
    "cna",
    "cnet",
    "anipac",
    "nafin",
    "bancomext",
    "cdmx",
    "wtc",
    "qcc",
    "cintermex",
    "t-mec",
    "tmec",
    "ia",
    "ti",
    "iot",
    "ev",
    "cnc",
    "oem",
    "mice",
    "aiag",
    "claut",
    "senaproa",
    "itb",
    "re+",
    "exitep",
}

LOWERCASE_CONNECTORS = {
    "de",
    "del",
    "la",
    "las",
    "el",
    "los",
    "en",
    "y",
    "e",
    "o",
    "para",
    "por",
    "con",
    "a",
    "al",
    "and",
    "of",
    "for",
    "in",
}


def _smart_title_case(raw_name: str) -> str:
    txt = (raw_name or "").strip()
    if not txt:
        return txt
    txt = re.sub(r"\s+", " ", txt)
    if txt == txt.lower() or txt == txt.upper():
        words = txt.split()
        out_words = []
        for idx, w in enumerate(words):
            clean_w = w.strip("()[].,:;—-")
            low_w = clean_w.lower()
            if low_w in ACRONYMS_UPPER:
                out_words.append(w.upper())
            elif idx > 0 and low_w in LOWERCASE_CONNECTORS:
                out_words.append(w.lower())
            else:
                out_words.append(w[:1].upper() + w[1:].lower())
        return " ".join(out_words)
    return txt


def sanitize_single_line_text(raw_text: str) -> str:
    """
    Limpia fragmentos extraídos de Markdown (remueve sintaxis [texto](url), etiquetas auxiliares
    de Sitio oficial, encabezados ###, saltos de línea y espacios múltiples) para garantizar
    texto plano limpio en 1 sola línea.
    """
    if not raw_text:
        return ""
    txt = str(raw_text)
    # Reemplazar enlaces Markdown [texto](url) por su texto visible
    txt = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", txt)
    # Remover anotaciones auxiliares "(Sitio oficial: https://...)" o "Sitio oficial: https://..."
    txt = re.sub(r"\(\s*Sitio oficial:\s*https?://[^\)]+\)", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"Sitio oficial:\s*https?://\S+", "", txt, flags=re.IGNORECASE)
    txt = txt.replace("Register Now! ↗", "").replace("Register Now!", "")
    # Eliminar marcadores de encabezado o énfasis Markdown
    txt = re.sub(r"(^|\s)#+\s*", " ", txt)
    txt = txt.replace("**", "").replace("__", "")
    # Reemplazar saltos de línea por separador limpio
    txt = re.sub(r"[\r\n]+", " · ", txt)
    txt = re.sub(r"(?:\s*·\s*)+", " · ", txt)
    txt = re.sub(r"\s+", " ", txt).strip(" ·\"'")
    return txt


class EventoCandidatoIA(BaseModel):
    nombre: str = Field(..., min_length=8, max_length=180)
    descripcion: str = Field(..., min_length=20, max_length=650)
    categoria: CATEGORIAS_OFICIALES
    fecha_inicio: date = Field(..., description="Formato YYYY-MM-DD")
    fecha_fin: date = Field(
        ...,
        description="Formato YYYY-MM-DD. Si es de 1 solo día, igual a fecha_inicio.",
    )
    horario: Optional[str] = Field(
        default=None,
        description="Ej. '09:00 - 18:00 hrs'. Null si no aparece explícito.",
    )
    modalidad: Literal["Presencial", "Virtual", "Híbrido"] = "Presencial"
    sede_nombre: Optional[str] = Field(
        default=None,
        description="Nombre del recinto, centro de convenciones u hotel. Null si no consta.",
    )
    direccion_explicita: Optional[str] = Field(
        default=None,
        description="Calle y número solo si aparecen literalmente en el texto.",
    )
    estado_nombre: Optional[str] = Field(
        default=None,
        description="Estado de la República Mexicana donde ocurre el evento.",
    )
    municipio: Optional[str] = Field(default=None)
    codigo_postal_explicito: Optional[str] = Field(
        default=None,
        description="CP de 5 dígitos solo si aparece explícito en el texto.",
    )
    promotor: Optional[str] = Field(
        default=None,
        description="Organizador, cámara o institución convocante.",
    )
    sitio_web_evento: Optional[str] = Field(
        default=None,
        description="URL directa del evento SOLO si aparece literalmente en el Markdown.",
    )
    evidencia_texto: str = Field(
        ...,
        min_length=20,
        description="Fragmento textual copiado literalmente del Markdown que comprueba el nombre y la fecha del evento.",
    )

    @field_validator("nombre")
    @classmethod
    def normalizar_nombre(cls, v: str) -> str:
        return _smart_title_case(sanitize_single_line_text(v))

    @field_validator("descripcion", "evidencia_texto")
    @classmethod
    def limpiar_texto_multilinea(cls, v: str) -> str:
        return sanitize_single_line_text(v)

    @field_validator("sitio_web_evento")
    @classmethod
    def limpiar_y_validar_url(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        clean_v = v.strip()
        if clean_v.startswith("http://https://"):
            clean_v = clean_v.replace("http://https://", "https://", 1)
        parsed = urlparse(clean_v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        dominio = parsed.netloc.lower().replace("www.", "")
        if dominio in DOMINIOS_ACORTADORES_PROHIBIDOS:
            raise ValueError(
                f"URL bloqueada por política de seguridad institucional (acortador: {dominio})"
            )
        if dominio in DOMINIOS_REDES_NO_SITIO_OFICIAL:
            return None
        path_part = parsed.path or ""
        return f"{parsed.scheme}://{parsed.netloc}{path_part}".rstrip("/")

    @model_validator(mode="after")
    def validar_reglas_temporales_y_evidencia(self) -> "EventoCandidatoIA":
        hoy = get_mexico_today()
        if self.fecha_inicio < hoy:
            raise ValueError(f"Evento pasado descartado ({self.fecha_inicio} < {hoy})")
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser menor que fecha_inicio")
        if (self.fecha_fin - self.fecha_inicio).days > 14:
            raise ValueError(
                "Duración mayor a 14 días indica diplomado/curso, no evento estratégico."
            )
        anio_str = str(self.fecha_inicio.year)
        if anio_str not in self.evidencia_texto and anio_str not in self.nombre:
            raise ValueError(
                f"Alucinación temporal bloqueada: el año {anio_str} no consta explícitamente en la evidencia."
            )
        low_name = self.nombre.lower()
        for bad_term in TERMINOS_NO_ESTRATEGICOS_B2C:
            if bad_term in low_name:
                raise ValueError(
                    f"Evento descartado por filtro sectorial B2B (término no estratégico: {bad_term})"
                )
        return self


class ListaEventosExtraidos(BaseModel):
    eventos: List[EventoCandidatoIA] = Field(default_factory=list)
