"""
Crawler híbrido en dos carriles (Carril 1: semilla_oficial, Carril 2: descubrimiento_web).
Soporta Crawl4AI (Playwright headless) con fallback automático a httpx + BeautifulSoup4 + markdownify,
validación estricta de certificados SSL (`verify=True`), detección de soft-404 / errores PHP y preservación
de enlaces oficiales de cada evento.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from bs4 import BeautifulSoup
from markdownify import markdownify as md

import database as db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEED_SOURCES_PATH = os.path.join(BASE_DIR, "seed_sources.yaml")

INSTITUTIONAL_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
    "BancomextNafinEventosBot/2.0"
)

# Firmas de páginas de error (Soft-404, errores de PHP/CodeIgniter, WordPress 404, etc.)
BODY_ERROR_SIGNATURES = (
    "this is somewhat embarrassing",
    "it looks like nothing was found at this location",
    "trying to get property of non-object",
    "a php error was encountered",
    "filename: models/pagina_model.php",
    "fatal error:",
    "error establishing a database connection",
    "you don't have permission to access this resource",
)

TITLE_ERROR_SIGNATURES = (
    "not found",
    "404",
    "página no encontrada",
    "pagina no encontrada",
    "page not found",
    "403 forbidden",
    "access denied",
    "just a moment...",
)

_URL_VERIFICATION_CACHE: Dict[str, Tuple[bool, Optional[str]]] = {}


def _has_error_page_content(html_text: str) -> bool:
    """Detecta si el HTML devuelto con HTTP 200 es en realidad un Soft-404 o un error PHP del servidor."""
    if not html_text or len(html_text.strip()) < 60:
        return True
    low_body = html_text.lower()
    for sig in BODY_ERROR_SIGNATURES:
        if sig in low_body:
            return True

    try:
        soup = BeautifulSoup(html_text[:12000], "html.parser")
        title_txt = (soup.title.string or "").strip().lower() if soup.title else ""
        h1_txt = " ".join(h.get_text(" ", strip=True).lower() for h in soup.find_all("h1")[:2])
        header_combined = f"{title_txt} {h1_txt}"
        for t_sig in TITLE_ERROR_SIGNATURES:
            if t_sig in header_combined:
                return True
    except Exception:
        pass
    return False


def verify_live_url(url: str, timeout_sec: float = 8.0) -> Tuple[bool, Optional[str]]:
    """
    Verifica en vivo que una URL:
    1) Tenga esquema http/https válido.
    2) Tenga un certificado SSL/TLS válido (`verify=True` estricto, sin advertencias de seguridad en navegador).
    3) Responda con HTTP 200 OK tras redirecciones.
    4) No contenga errores PHP (ej. CodeIgniter backtrace) ni páginas Soft-404 ("Not Found").
    Retorna `(True, canonical_url)` o `(False, None)`.
    """
    if not url or not isinstance(url, str):
        return False, None

    clean_u = url.strip()
    if clean_u.startswith("http://https://"):
        clean_u = clean_u.replace("http://https://", "https://", 1)

    parsed = urlparse(clean_u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, None

    if clean_u in _URL_VERIFICATION_CACHE:
        return _URL_VERIFICATION_CACHE[clean_u]

    headers = {
        "User-Agent": INSTITUTIONAL_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    }
    try:
        with httpx.Client(
            headers=headers,
            timeout=timeout_sec,
            follow_redirects=True,
            verify=True,  # Validación estricta de certificado SSL/TLS
        ) as client:
            resp = client.get(clean_u)
            if resp.status_code != 200:
                _URL_VERIFICATION_CACHE[clean_u] = (False, None)
                return False, None
            if _has_error_page_content(resp.text):
                _URL_VERIFICATION_CACHE[clean_u] = (False, None)
                return False, None
            final_url = str(resp.url)
            _URL_VERIFICATION_CACHE[clean_u] = (True, final_url)
            _URL_VERIFICATION_CACHE[final_url] = (True, final_url)
            return True, final_url
    except Exception:
        _URL_VERIFICATION_CACHE[clean_u] = (False, None)
        return False, None


def load_seed_sources() -> List[Dict[str, Any]]:
    """
    Lee seed_sources.yaml y devuelve una lista plana de fuentes con su metadato de tipo
    ('recintos_feriales' o 'camaras_empresariales').
    """
    if not os.path.exists(SEED_SOURCES_PATH):
        return []
    with open(SEED_SOURCES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    sources: List[Dict[str, Any]] = []
    for group_key in ("recintos_feriales", "camaras_empresariales"):
        for item in data.get(group_key, []) or []:
            sources.append(
                {
                    **item,
                    "grupo": group_key,
                    "origen_carril": "semilla_oficial",
                }
            )
    return sources


def build_discovery_query(categoria: str, reference_date: Optional[dt.date] = None) -> str:
    """
    Genera la consulta oficial de búsqueda para el Carril 2 (descubrimiento_web):
    f'congreso OR expo OR foro OR cumbre OR encuentro "{categoria}" México ({anio_actual} OR {anio_siguiente}) -curso -diplomado -vacante -empleo -webinar'
    """
    hoy = reference_date or dt.date.today()
    anio_actual = hoy.year
    anio_siguiente = hoy.year + 1
    return (
        f'congreso OR expo OR foro OR cumbre OR encuentro "{categoria}" México '
        f"({anio_actual} OR {anio_siguiente}) -curso -diplomado -vacante -empleo -webinar"
    )


def _html_to_clean_markdown(html_text: str, base_url: str = "") -> str:
    """
    Convierte HTML crudo en Markdown limpio:
    - Rechaza páginas de error (Soft-404 / errores PHP).
    - Convierte rutas relativas `<a href="...">` en URLs absolutas usando `base_url`.
    - Preserva el texto `alt`/`title` de imágenes dentro de enlaces `<a>` y conserva enlaces
      con íconos sin texto (como los botones de sitio web oficial en CINTERMEX).
    """
    if not html_text or _has_error_page_content(html_text):
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "footer", "nav"]):
        tag.decompose()

    # Preservar alt/title de imágenes dentro de enlaces y resolver hrefs absolutos
    for a in soup.find_all("a", href=True):
        raw_href = (a.get("href") or "").strip()
        if not raw_href or raw_href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        if raw_href.startswith("http://https://"):
            raw_href = raw_href.replace("http://https://", "https://", 1)
        full_url = urljoin(base_url, raw_href) if base_url else raw_href
        a["href"] = full_url

        if not a.get_text(strip=True):
            img_labels = []
            for img in a.find_all("img"):
                alt_txt = (img.get("alt") or img.get("title") or "").strip()
                if alt_txt:
                    alt_txt = re.sub(r"\s+logo$/i", "", alt_txt, flags=re.IGNORECASE).strip()
                    if alt_txt.lower().endswith(" logo"):
                        alt_txt = alt_txt[:-5].strip()
                    img_labels.append(alt_txt)
            if img_labels:
                label_joined = " ".join(img_labels)
                year_in_url = re.search(r"/(202[6-9])(?:/|$)", full_url)
                if year_in_url and year_in_url.group(1) not in label_joined:
                    label_joined = f"{label_joined} {year_in_url.group(1)}"
                a.string = f"{label_joined} (Sitio oficial: {full_url})"
            else:
                a.string = f"Sitio oficial: {full_url}"

    markdown_raw = md(str(soup), heading_style="ATX", strip=["img"])
    lines = [line.strip() for line in markdown_raw.splitlines()]
    compact_lines = [line for line in lines if line and len(line) > 1]
    cleaned = "\n".join(compact_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned[:24000]


async def _crawl_with_crawl4ai(url: str, timeout_sec: int = 25) -> Optional[str]:
    """
    Intenta extraer Markdown limpio con Crawl4AI (AsyncWebCrawler + PruningContentFilter).
    Si Crawl4AI o el binario de Playwright no están disponibles, retorna None para activar el fallback.
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        browser_cfg = BrowserConfig(
            headless=True,
            user_agent=INSTITUTIONAL_USER_AGENT,
            verbose=False,
        )
        md_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.45, threshold_type="fixed")
        )
        run_cfg = CrawlerRunConfig(
            markdown_generator=md_generator,
            page_timeout=timeout_sec * 1000,
        )
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
            if result and getattr(result, "success", False):
                md_obj = getattr(result, "markdown", None)
                if md_obj:
                    fit_md = getattr(md_obj, "fit_markdown", None) or str(md_obj)
                    if fit_md and len(fit_md.strip()) > 80:
                        return fit_md[:24000]
    except Exception:
        return None
    return None


def fetch_url_markdown(url: str, timeout_sec: int = 12, prefer_crawl4ai: bool = False) -> str:
    """
    Descarga una URL con validación estricta de SSL (`verify=True`) y devuelve su contenido en Markdown limpio.
    Si el certificado SSL es inválido o la página es un Soft-404 / error PHP, retorna cadena vacía.
    """
    if prefer_crawl4ai:
        try:
            import importlib.util

            if importlib.util.find_spec("crawl4ai") is not None:
                c4_md = asyncio.run(_crawl_with_crawl4ai(url, timeout_sec=timeout_sec))
                if c4_md and not _has_error_page_content(c4_md):
                    return c4_md
        except Exception:
            pass

    headers = {
        "User-Agent": INSTITUTIONAL_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    }
    try:
        with httpx.Client(
            headers=headers,
            timeout=float(timeout_sec),
            follow_redirects=True,
            verify=True,  # Nunca permitir sitios con certificado SSL defectuoso
        ) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.text and not _has_error_page_content(resp.text):
                final_u = str(resp.url)
                _URL_VERIFICATION_CACHE[url] = (True, final_u)
                _URL_VERIFICATION_CACHE[final_u] = (True, final_u)
                return _html_to_clean_markdown(resp.text, base_url=final_u)
    except Exception:
        pass
    _URL_VERIFICATION_CACHE[url] = (False, None)
    return ""


def crawl_seed_sources(
    sector_filter: Optional[str] = None,
    max_sources: int = 8,
) -> List[Dict[str, Any]]:
    """
    Carril 1 (`semilla_oficial`):
    Recorre las fuentes declaradas en seed_sources.yaml y extrae su Markdown verificado.
    """
    sources = load_seed_sources()
    if sector_filter and sector_filter.upper() != "TODOS":
        sources = [
            s
            for s in sources
            if s.get("categoria_principal", "").lower() == sector_filter.lower()
            or s.get("grupo") == "recintos_feriales"
        ]

    priority_ids = [
        "cintermex_mty_2026",
        "cintermex_mty_2027",
        "expo_guadalajara",
        "igeco_hannover_fairs",
        "mexico_business_events",
        "expo_manufactura_oficial",
        "ina_autopartes",
        "comce_nacional",
    ]
    priority_rank = {pid: idx for idx, pid in enumerate(priority_ids)}
    sources.sort(key=lambda s: priority_rank.get(s.get("id", ""), 99))

    documents: List[Dict[str, Any]] = []
    use_c4 = os.environ.get("USE_CRAWL4AI", "0") == "1"
    for src in sources:
        if len(documents) >= max_sources:
            break
        url = src.get("url", "").strip()
        if not url:
            continue
        md_content = fetch_url_markdown(url, timeout_sec=10, prefer_crawl4ai=use_c4)
        if md_content and len(md_content.strip()) >= 150 and (
            "2026" in md_content or "2027" in md_content
        ):
            _, resolved_url = verify_live_url(url)
            documents.append(
                {
                    "fuente_url": resolved_url or url,
                    "nombre_fuente": src.get("nombre", url),
                    "categoria_sugerida": src.get("categoria_principal", "Comercio Exterior"),
                    "default_estado": src.get("estado"),
                    "origen_carril": "semilla_oficial",
                    "markdown": md_content,
                }
            )
    return documents


def discover_web_urls_for_category(
    categoria: str,
    max_results: int = 3,
    reference_date: Optional[dt.date] = None,
) -> List[Dict[str, Any]]:
    """
    Carril 2 (`descubrimiento_web`):
    Descubre URLs de eventos estratégicos en México para la categoría indicada.
    Solo acepta URLs que respondan en vivo con HTTP 200 y certificado SSL válido.
    """
    query = build_discovery_query(categoria, reference_date=reference_date)
    tavily_key = db.get_secret("TAVILY_API_KEY")
    results_docs: List[Dict[str, Any]] = []

    if tavily_key:
        try:
            from tavily import TavilyClient

            t_client = TavilyClient(api_key=tavily_key)
            search_resp = t_client.search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
            )
            for item in search_resp.get("results", [])[:max_results]:
                u = (item.get("url") or "").strip()
                if not u:
                    continue
                ok_url, final_u = verify_live_url(u, timeout_sec=8.0)
                if not ok_url or not final_u:
                    continue
                md_text = fetch_url_markdown(final_u, timeout_sec=10, prefer_crawl4ai=False)
                if len(md_text) >= 120:
                    results_docs.append(
                        {
                            "fuente_url": final_u,
                            "nombre_fuente": item.get("title") or final_u,
                            "categoria_sugerida": categoria,
                            "default_estado": None,
                            "origen_carril": "descubrimiento_web",
                            "markdown": md_text[:24000],
                        }
                    )
            if results_docs:
                return results_docs
        except Exception:
            pass

    gemini_key = db.get_secret("GEMINI_API_KEY") or db.get_secret("GOOGLE_API_KEY")
    if gemini_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=gemini_key,
                http_options=types.HttpOptions(
                    timeout=20000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            prompt = (
                f"Realiza una búsqueda web con Google Search para: {query}\n"
                "Lista los próximos congresos, exposiciones, foros o cumbres empresariales en México "
                "indicando nombre exacto del evento, fechas exactas (con año 2026 o 2027), recinto/ciudad, "
                "organizador y URL directa verificada de cada evento."
            )
            resp = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            grounded_text = getattr(resp, "text", "") or ""
            discovered_urls: List[str] = []

            for u_match in re.findall(r"https?://[^\s\)\]\">]+", grounded_text):
                u_clean = u_match.rstrip(".,;)")
                if (
                    "vertexaisearch.cloud.google.com" not in u_clean
                    and u_clean not in discovered_urls
                ):
                    discovered_urls.append(u_clean)

            for u in discovered_urls:
                if len(results_docs) >= max_results:
                    break
                ok_url, final_u = verify_live_url(u, timeout_sec=8.0)
                if not ok_url or not final_u:
                    continue
                page_md = fetch_url_markdown(final_u, timeout_sec=8, prefer_crawl4ai=False)
                if page_md and len(page_md.strip()) >= 120:
                    results_docs.append(
                        {
                            "fuente_url": final_u,
                            "nombre_fuente": f"Búsqueda Web Verificada · {categoria}",
                            "categoria_sugerida": categoria,
                            "default_estado": None,
                            "origen_carril": "descubrimiento_web",
                            "markdown": page_md[:24000],
                        }
                    )
        except Exception:
            pass

    return results_docs
