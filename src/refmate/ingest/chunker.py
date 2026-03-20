"""Chunker: divide Markdown jerárquico en chunks semánticos por unidad normativa.

Responsabilidad única: tomar `data/structured/{doc_id}.md`, dividirlo en
unidades normativas (subreglas, secciones, artículos), asignar metadatos,
construir el grafo bidireccional de referencias cruzadas y generar el
índice jerárquico en `data/index/hierarchical_index.json`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from loguru import logger

from refmate.config import RefMateConfig, get_project_root
from refmate.core.models import Chunk

# ---------------------------------------------------------------------------
# Patrones regex
# ---------------------------------------------------------------------------

# reglas-de-juego: H1 y H2 tienen formatos específicos
_H1_RDJ = re.compile(r"^# (Regla \d+[^\n]*)")
_H2_RDJ = re.compile(r"^## (\d+:\d+(?:\s+[^\n]*)?)")

# Genéricos para rgc-fabm y add-fabm
# Estos patrones son mutuamente excluyentes (H4 requiere 4 #, H3 requiere 3, etc.)
_H1_GEN = re.compile(r"^# ([^#\n].+)")
_H2_GEN = re.compile(r"^## ([^#\n].+)")
_H3_GEN = re.compile(r"^### ([^#\n].+)")
_H4_GEN = re.compile(r"^#### ([^#\n].+)")

# Referencias cruzadas en el texto
_REF_PATTERN = re.compile(r"\[REF:([^:\]]+):([^\]\n]+)\]")

# Número de artículo en cuerpo de texto (para lookup rgc-fabm)
_ART_IN_TEXT = re.compile(r"Art[íi]culo\s+(\d+)", re.IGNORECASE)

# Extractores de número/numeral para IDs concisos
_REGLA_NUM = re.compile(r"Regla\s+(\d+)", re.IGNORECASE)
_CAPITULO_NUM = re.compile(r"Cap[íi]tulo\s+(\w+)", re.IGNORECASE)
_SECCION_NUM = re.compile(r"Secci[oó]n\s+(\w+)", re.IGNORECASE)
_ARTICULO_NUM = re.compile(r"Art[íi]culo\s+(\d+)", re.IGNORECASE)

_MAX_SECTION_TOKENS = 2_000   # Threshold de subdivisión para rgc-fabm
_MAX_CHUNK_TOKENS = 3_000     # Límite de aceptación (criterio de la fase)


# ---------------------------------------------------------------------------
# Utilidades de módulo
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convierte texto a slug (minúsculas, sin acentos, solo alfanum y guiones).

    Args:
        text: Texto de entrada.

    Returns:
        Slug normalizado.
    """
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    lower = ascii_text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lower)
    return slug.strip("-")


def _approx_tokens(text: str) -> int:
    """Estimación del número de tokens: 4 chars ≈ 1 token.

    Args:
        text: Texto a estimar.

    Returns:
        Número aproximado de tokens.
    """
    return len(text) // 4


def _heading_slug(title: str, pattern: re.Pattern[str], prefix: str) -> str:
    """Genera un slug conciso extrayendo el número/numeral del título.

    Ejemplo: "Capítulo II — Disposiciones" → "capitulo-ii"
             "Artículo 12 — Infracciones" → "articulo-12"

    Si no se encuentra el patrón, usa los primeros 40 caracteres slugificados.

    Args:
        title: Título del heading.
        pattern: Regex para extraer el número (grupo 1).
        prefix: Prefijo del slug (ej. 'capitulo', 'articulo').

    Returns:
        Slug conciso.
    """
    m = pattern.search(title)
    if m:
        return f"{prefix}-{_slugify(m.group(1))}"
    return _slugify(title[:40])


def _make_raw_chunk(
    *,
    chunk_id: str,
    doc_id: str,
    doc_nombre: str,
    doc_fuente: str,
    jerarquia: list[str],
    nivel: str,
    titulo_seccion: str,
    texto: str,
) -> dict[str, Any]:
    """Crea un dict raw mutable con todos los campos del Chunk excepto refs.

    Las referencias se rellenan en la fase de resolución (`_resolve_references`).

    Args:
        chunk_id: ID único del chunk.
        doc_id: ID del documento fuente.
        doc_nombre: Nombre legible del documento.
        doc_fuente: Organismo emisor.
        jerarquia: Lista de títulos de la jerarquía completa.
        nivel: Nivel normativo ('subregla', 'seccion', 'capitulo', 'articulo').
        titulo_seccion: Título de esta unidad normativa.
        texto: Texto íntegro del chunk.

    Returns:
        Dict con todos los campos del DTO Chunk (sin refs resueltas).
    """
    breadcrumb = " > ".join(jerarquia)
    texto_con_contexto = f"> {breadcrumb}\n\n{texto}"
    return {
        "chunk_id": chunk_id,
        "documento_id": doc_id,
        "documento_nombre": doc_nombre,
        "fuente": doc_fuente,
        "jerarquia": jerarquia,
        "nivel": nivel,
        "titulo_seccion": titulo_seccion,
        "texto": texto,
        "texto_con_contexto": texto_con_contexto,
        "referencias_salientes": [],
        "referencias_entrantes": [],
        "num_tokens_approx": _approx_tokens(texto),
    }


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------


class Chunker:
    """Divide documentos Markdown en chunks semánticos por unidad normativa.

    Genera los siguientes artefactos en disco:
    - `data/chunks/{doc_id}_chunks.json`: lista de Chunk por documento.
    - `data/chunks/cross_references_graph.json`: grafo bidireccional de refs.
    - `data/index/hierarchical_index.json`: índice de títulos sin texto.
    """

    def __init__(self, config: RefMateConfig) -> None:
        """Inicializa el chunker.

        Args:
            config: Configuración completa del sistema RefMate.
        """
        self._config = config
        self._root = get_project_root()

    # ------------------------------------------------------------------
    # Entry point público
    # ------------------------------------------------------------------

    def run_all(self) -> dict[str, Any]:
        """Procesa todos los documentos y genera los artefactos de chunking.

        Returns:
            Dict con estadísticas por documento y totales de referencias cruzadas.
        """
        raw_chunks_by_doc: dict[str, list[dict]] = {}
        lookups_by_doc: dict[str, dict[str, str]] = {}
        stats: dict[str, Any] = {}

        for doc_id in self._config.documents:
            md_path = self._root / "data" / "structured" / f"{doc_id}.md"
            if not md_path.exists():
                logger.warning(
                    f"Markdown estructurado no encontrado: {md_path} — saltando. "
                    f"Ejecuta primero la FASE 4."
                )
                continue

            logger.info(f"Chunkeando {doc_id}...")
            raw_chunks, lookup = self._chunk_document(doc_id, md_path)
            raw_chunks_by_doc[doc_id] = raw_chunks
            lookups_by_doc[doc_id] = lookup

            tokens = [_approx_tokens(c["texto"]) for c in raw_chunks]
            over_limit = [t for t in tokens if t > _MAX_CHUNK_TOKENS]
            if over_limit:
                logger.warning(
                    f"{doc_id}: {len(over_limit)} chunks superan {_MAX_CHUNK_TOKENS} tokens"
                )
            stats[doc_id] = {
                "chunks": len(raw_chunks),
                "tokens_max": max(tokens) if tokens else 0,
                "tokens_avg": sum(tokens) // len(tokens) if tokens else 0,
            }
            logger.info(
                f"{doc_id}: {len(raw_chunks)} chunks, "
                f"max={stats[doc_id]['tokens_max']} tokens, "
                f"avg={stats[doc_id]['tokens_avg']} tokens"
            )

        if not raw_chunks_by_doc:
            logger.error("No se encontró ningún Markdown estructurado. Abortando.")
            return stats

        # Resolver referencias y construir Chunks finales
        chunks_by_doc, edges, unresolved = self._resolve_references(
            raw_chunks_by_doc, lookups_by_doc
        )

        # Construir artefactos
        graph = self._build_cross_ref_graph(edges, unresolved)
        index = self._build_hierarchical_index(chunks_by_doc)

        # Persistir
        self._save_outputs(chunks_by_doc, graph, index)

        stats["cross_refs"] = graph["stats"]
        return stats

    # ------------------------------------------------------------------
    # Parsing por tipo de jerarquía
    # ------------------------------------------------------------------

    def _chunk_document(
        self, doc_id: str, md_path: Path
    ) -> tuple[list[dict], dict[str, str]]:
        """Parsea el Markdown del documento y genera raw_chunks + section_lookup.

        Delega en el parser específico según `tipo_jerarquia`.

        Args:
            doc_id: Identificador del documento.
            md_path: Ruta al fichero Markdown estructurado.

        Returns:
            Tupla (raw_chunks, section_lookup):
            - raw_chunks: lista de dicts mutables con todos los campos salvo refs.
            - section_lookup: dict {section_key → chunk_id} para resolución de refs.
        """
        tipo = self._config.documents[doc_id].tipo_jerarquia
        lines = md_path.read_text(encoding="utf-8").splitlines(keepends=True)

        if tipo == "regla_subregla":
            return self._parse_regla_subregla(doc_id, lines)
        elif tipo == "titulo_capitulo_seccion":
            return self._parse_titulo_capitulo_seccion(doc_id, lines)
        elif tipo == "titulo_capitulo_articulo":
            return self._parse_titulo_capitulo_articulo(doc_id, lines)
        else:
            logger.warning(f"tipo_jerarquia desconocido '{tipo}' para {doc_id} — saltando")
            return [], {}

    def _parse_regla_subregla(
        self, doc_id: str, lines: list[str]
    ) -> tuple[list[dict], dict[str, str]]:
        """Parsea documentos regla/subregla (reglas-de-juego).

        Chunk level: H2 (`## N:M Descripción`).
        H1 (`# Regla N: Título`) proporciona contexto de jerarquía.

        Args:
            doc_id: Identificador del documento.
            lines: Líneas del fichero Markdown.

        Returns:
            (raw_chunks, section_lookup)
        """
        doc_cfg = self._config.documents[doc_id]
        raw_chunks: list[dict] = []
        lookup: dict[str, str] = {}

        current_h1 = ""
        current_h2 = ""
        current_lines: list[str] = []
        h1_slug = ""

        def flush() -> None:
            nonlocal current_lines
            if not current_h2:
                current_lines = []
                return
            texto = "".join(current_lines).strip()
            if not texto:
                current_lines = []
                return

            # Extraer número de subregla para el slug
            m_num = re.match(r"(\d+:\d+)", current_h2)
            h2_num = m_num.group(1) if m_num else current_h2
            h2_slug = _slugify(h2_num)
            chunk_id = f"{doc_id}:{h1_slug}:{h2_slug}" if h1_slug else f"{doc_id}:{h2_slug}"

            jerarquia = [x for x in [current_h1, current_h2] if x]
            raw = _make_raw_chunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                doc_nombre=doc_cfg.nombre,
                doc_fuente=doc_cfg.fuente,
                jerarquia=jerarquia,
                nivel="subregla",
                titulo_seccion=current_h2,
                texto=texto,
            )
            raw_chunks.append(raw)

            # Registrar claves de lookup para resolución de refs
            # El formato de refs es [REF:reglas-de-juego:regla-N-M]
            if m_num:
                num = m_num.group(1)           # "4:1"
                h2_slug_clean = _slugify(num)  # "4-1"
                lookup[num] = chunk_id         # exacto: "4:1"
                lookup[h2_slug_clean] = chunk_id  # slugificado: "4-1"
                parts = num.split(":")
                if len(parts) == 2:
                    # Formato usado en refs: "regla-N-M"
                    lookup[f"regla-{parts[0]}-{parts[1]}"] = chunk_id

            current_lines.clear()

        for line in lines:
            stripped = line.rstrip("\n")
            m_h1 = _H1_RDJ.match(stripped)
            m_h2 = _H2_RDJ.match(stripped)

            if m_h1:
                flush()
                current_h1 = m_h1.group(1).strip()
                current_h2 = ""
                m_rnum = _REGLA_NUM.search(current_h1)
                h1_slug = f"regla-{m_rnum.group(1)}" if m_rnum else _slugify(current_h1[:20])
            elif m_h2:
                flush()
                current_h2 = m_h2.group(1).strip()
            else:
                if current_h2:
                    current_lines.append(line)

        flush()
        return raw_chunks, lookup

    def _parse_titulo_capitulo_articulo(
        self, doc_id: str, lines: list[str]
    ) -> tuple[list[dict], dict[str, str]]:
        """Parsea documentos título/capítulo/artículo (add-fabm).

        Chunk level: H3 (`### Artículo N — Título`).

        Args:
            doc_id: Identificador del documento.
            lines: Líneas del fichero Markdown.

        Returns:
            (raw_chunks, section_lookup)
        """
        doc_cfg = self._config.documents[doc_id]
        raw_chunks: list[dict] = []
        lookup: dict[str, str] = {}

        current_h1 = ""
        current_h2 = ""
        current_h3 = ""
        current_lines: list[str] = []
        h2_slug = ""

        def flush() -> None:
            nonlocal current_lines
            if not current_h3:
                current_lines.clear()
                return
            texto = "".join(current_lines).strip()
            if not texto:
                current_lines.clear()
                return

            h3_slug = _heading_slug(current_h3, _ARTICULO_NUM, "articulo")
            chunk_id = f"{doc_id}:{h2_slug}:{h3_slug}" if h2_slug else f"{doc_id}:{h3_slug}"
            jerarquia = [x for x in [current_h1, current_h2, current_h3] if x]

            raw = _make_raw_chunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                doc_nombre=doc_cfg.nombre,
                doc_fuente=doc_cfg.fuente,
                jerarquia=jerarquia,
                nivel="articulo",
                titulo_seccion=current_h3,
                texto=texto,
            )
            raw_chunks.append(raw)

            # Registrar claves: "art-N", "articulo-N", "N"
            m_num = _ARTICULO_NUM.search(current_h3)
            if m_num:
                n = m_num.group(1)
                lookup[f"art-{n}"] = chunk_id
                lookup[f"articulo-{n}"] = chunk_id
                lookup[n] = chunk_id

            current_lines.clear()

        for line in lines:
            stripped = line.rstrip("\n")
            # Detectar nivel: de más específico a menos (H3 antes de H2 antes de H1)
            m_h3 = _H3_GEN.match(stripped)
            m_h2 = _H2_GEN.match(stripped) if not m_h3 else None
            m_h1 = _H1_GEN.match(stripped) if not m_h3 and not m_h2 else None

            if m_h3:
                flush()
                current_h3 = m_h3.group(1).strip()
            elif m_h2:
                flush()
                current_h2 = m_h2.group(1).strip()
                current_h3 = ""
                h2_slug = _heading_slug(current_h2, _CAPITULO_NUM, "capitulo")
            elif m_h1:
                flush()
                current_h1 = m_h1.group(1).strip()
                current_h2 = ""
                current_h3 = ""
                h2_slug = ""
            else:
                if current_h3:
                    current_lines.append(line)

        flush()
        return raw_chunks, lookup

    def _parse_titulo_capitulo_seccion(
        self, doc_id: str, lines: list[str]
    ) -> tuple[list[dict], dict[str, str]]:
        """Parsea documentos título/capítulo/sección (rgc-fabm).

        Chunk level: H3 (Sección). Si un capítulo carece de secciones, el texto
        del capítulo forma su propio chunk de nivel 'capitulo'. Las secciones que
        superan 2000 tokens se subdividen en partes por párrafos.
        H4 (Subsección) se incluye como body text del H3 sin crear chunk propio.

        Args:
            doc_id: Identificador del documento.
            lines: Líneas del fichero Markdown.

        Returns:
            (raw_chunks, section_lookup)
        """
        doc_cfg = self._config.documents[doc_id]
        raw_chunks: list[dict] = []
        lookup: dict[str, str] = {}

        current_h1 = ""
        current_h2 = ""
        current_h3 = ""
        current_lines: list[str] = []
        h2_slug = ""
        h2_has_sections = False  # ¿El H2 actual ha visto algún H3?

        def flush(force_nivel: str | None = None) -> None:
            nonlocal current_lines, h2_has_sections
            texto = "".join(current_lines).strip()
            current_lines.clear()
            if not texto:
                return

            if current_h3:
                titulo_seccion = current_h3
                h3_slug = _heading_slug(current_h3, _SECCION_NUM, "seccion")
                chunk_id_base = (
                    f"{doc_id}:{h2_slug}:{h3_slug}" if h2_slug else f"{doc_id}:{h3_slug}"
                )
                nivel = force_nivel or "seccion"
                jerarquia = [x for x in [current_h1, current_h2, current_h3] if x]
            elif current_h2:
                titulo_seccion = current_h2
                chunk_id_base = f"{doc_id}:{h2_slug}" if h2_slug else f"{doc_id}:intro"
                nivel = force_nivel or "capitulo"
                jerarquia = [x for x in [current_h1, current_h2] if x]
            else:
                return  # Contenido antes de cualquier H2 (preámbulo): ignorar

            tokens = _approx_tokens(texto)
            if tokens > _MAX_SECTION_TOKENS and not force_nivel:
                sub_chunks = self._split_large_section(
                    texto=texto,
                    base_chunk_id=chunk_id_base,
                    doc_id=doc_id,
                    doc_nombre=doc_cfg.nombre,
                    doc_fuente=doc_cfg.fuente,
                    jerarquia=jerarquia,
                    nivel=nivel,
                    titulo_seccion=titulo_seccion,
                )
                for sc in sub_chunks:
                    raw_chunks.append(sc)
                    _register_lookup_rgc(lookup, sc["titulo_seccion"], sc["chunk_id"], texto)
            else:
                raw = _make_raw_chunk(
                    chunk_id=chunk_id_base,
                    doc_id=doc_id,
                    doc_nombre=doc_cfg.nombre,
                    doc_fuente=doc_cfg.fuente,
                    jerarquia=jerarquia,
                    nivel=nivel,
                    titulo_seccion=titulo_seccion,
                    texto=texto,
                )
                raw_chunks.append(raw)
                _register_lookup_rgc(lookup, titulo_seccion, chunk_id_base, texto)

        for line in lines:
            stripped = line.rstrip("\n")
            m_h4 = _H4_GEN.match(stripped)
            m_h3 = _H3_GEN.match(stripped) if not m_h4 else None
            m_h2 = _H2_GEN.match(stripped) if not m_h3 and not m_h4 else None
            m_h1 = _H1_GEN.match(stripped) if not m_h2 and not m_h3 and not m_h4 else None

            if m_h1:
                flush()
                current_h1 = m_h1.group(1).strip()
                current_h2 = ""
                current_h3 = ""
                h2_slug = ""
                h2_has_sections = False
            elif m_h2:
                flush()
                current_h2 = m_h2.group(1).strip()
                current_h3 = ""
                h2_slug = _heading_slug(current_h2, _CAPITULO_NUM, "capitulo")
                h2_has_sections = False
            elif m_h3:
                flush()
                current_h3 = m_h3.group(1).strip()
                h2_has_sections = True
            elif m_h4:
                # H4 no crea chunk: se incluye como body del H3 (o H2) actual
                if current_h3 or current_h2:
                    current_lines.append(line)
            else:
                if current_h3 or current_h2:
                    current_lines.append(line)

        flush()
        return raw_chunks, lookup

    def _split_large_section(
        self,
        *,
        texto: str,
        base_chunk_id: str,
        doc_id: str,
        doc_nombre: str,
        doc_fuente: str,
        jerarquia: list[str],
        nivel: str,
        titulo_seccion: str,
    ) -> list[dict]:
        """Divide una sección grande en sub-chunks por párrafos.

        Args:
            texto: Texto completo de la sección.
            base_chunk_id: ID base; los sub-chunks usan '{base}-parte-{n}'.
            doc_id: ID del documento.
            doc_nombre: Nombre del documento.
            doc_fuente: Organismo emisor.
            jerarquia: Jerarquía completa.
            nivel: Nivel normativo.
            titulo_seccion: Título de la sección original.

        Returns:
            Lista de raw_chunks. Si el texto no se puede subdividir,
            devuelve un único chunk con el texto completo.
        """
        paragraphs = [p.strip() for p in texto.split("\n\n") if p.strip()]
        parts: list[str] = []
        current_paras: list[str] = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = _approx_tokens(para)
            if current_tokens + para_tokens > _MAX_SECTION_TOKENS and current_paras:
                parts.append("\n\n".join(current_paras))
                current_paras = []
                current_tokens = 0
            current_paras.append(para)
            current_tokens += para_tokens

        if current_paras:
            parts.append("\n\n".join(current_paras))

        if len(parts) <= 1:
            return [
                _make_raw_chunk(
                    chunk_id=base_chunk_id,
                    doc_id=doc_id,
                    doc_nombre=doc_nombre,
                    doc_fuente=doc_fuente,
                    jerarquia=jerarquia,
                    nivel=nivel,
                    titulo_seccion=titulo_seccion,
                    texto=texto,
                )
            ]

        result = []
        total = len(parts)
        for i, part_text in enumerate(parts, start=1):
            chunk_id = f"{base_chunk_id}-parte-{i}"
            titulo_part = f"{titulo_seccion} (parte {i}/{total})"
            result.append(
                _make_raw_chunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    doc_nombre=doc_nombre,
                    doc_fuente=doc_fuente,
                    jerarquia=jerarquia,
                    nivel=nivel,
                    titulo_seccion=titulo_part,
                    texto=part_text,
                )
            )
        logger.debug(f"Sección dividida en {total} partes: {base_chunk_id}")
        return result

    # ------------------------------------------------------------------
    # Resolución de referencias cruzadas
    # ------------------------------------------------------------------

    def _resolve_references(
        self,
        raw_chunks_by_doc: dict[str, list[dict]],
        lookups_by_doc: dict[str, dict[str, str]],
    ) -> tuple[dict[str, list[Chunk]], list[dict], list[dict]]:
        """Resuelve [REF:doc_id:section_id] y construye el grafo bidireccional.

        Estrategias de resolución (en orden):
        1. Lookup exacto del section_id.
        2. Lookup por slug del section_id.
        3. Lookup por número extraído del section_id.

        Args:
            raw_chunks_by_doc: Chunks sin resolver por documento.
            lookups_by_doc: Section lookups por documento.

        Returns:
            (chunks_by_doc, edges, unresolved) donde:
            - chunks_by_doc: Chunk inmutables finales por documento.
            - edges: Lista de refs resueltas {from, to, raw}.
            - unresolved: Lista de refs no resueltas {from, raw}.
        """
        salientes_map: dict[str, list[str]] = {}
        entrantes_map: dict[str, list[str]] = {}
        edges: list[dict] = []
        unresolved: list[dict] = []

        # Primera pasada: extraer refs y construir salientes
        for doc_id, raw_chunks in raw_chunks_by_doc.items():
            for raw in raw_chunks:
                chunk_id = raw["chunk_id"]
                refs_salientes: list[str] = []

                for match in _REF_PATTERN.finditer(raw["texto"]):
                    ref_doc = match.group(1)
                    ref_section = match.group(2)
                    raw_ref = match.group(0)

                    target_id = self._resolve_single_ref(
                        ref_doc, ref_section, lookups_by_doc
                    )
                    if target_id and target_id != chunk_id:  # Ignorar auto-referencias
                        if target_id not in refs_salientes:
                            refs_salientes.append(target_id)
                        edges.append({"from": chunk_id, "to": target_id, "raw": raw_ref})
                    elif not target_id:
                        unresolved.append({"from": chunk_id, "raw": raw_ref})
                        logger.debug(f"Ref no resuelta: {raw_ref} en {chunk_id}")

                salientes_map[chunk_id] = refs_salientes

        # Construir entrantes (bidireccional)
        for chunk_id, targets in salientes_map.items():
            for target in targets:
                entrantes_map.setdefault(target, [])
                if chunk_id not in entrantes_map[target]:
                    entrantes_map[target].append(chunk_id)

        # Segunda pasada: construir Chunk inmutables con refs resueltas
        chunks_by_doc: dict[str, list[Chunk]] = {}
        for doc_id, raw_chunks in raw_chunks_by_doc.items():
            final_chunks = []
            for raw in raw_chunks:
                cid = raw["chunk_id"]
                chunk = Chunk(
                    chunk_id=cid,
                    documento_id=raw["documento_id"],
                    documento_nombre=raw["documento_nombre"],
                    fuente=raw["fuente"],
                    jerarquia=raw["jerarquia"],
                    nivel=raw["nivel"],
                    titulo_seccion=raw["titulo_seccion"],
                    texto=raw["texto"],
                    texto_con_contexto=raw["texto_con_contexto"],
                    referencias_salientes=salientes_map.get(cid, []),
                    referencias_entrantes=entrantes_map.get(cid, []),
                    num_tokens_approx=raw["num_tokens_approx"],
                )
                final_chunks.append(chunk)
            chunks_by_doc[doc_id] = final_chunks

        return chunks_by_doc, edges, unresolved

    def _resolve_single_ref(
        self,
        ref_doc: str,
        ref_section: str,
        lookups_by_doc: dict[str, dict[str, str]],
    ) -> str | None:
        """Intenta resolver una referencia a un chunk_id.

        Args:
            ref_doc: Documento referenciado (ej. 'reglas-de-juego').
            ref_section: Sección referenciada (ej. 'regla-4-1', 'art-12').
            lookups_by_doc: Lookups de todos los documentos.

        Returns:
            chunk_id resuelto, o None si no se pudo resolver.
        """
        if ref_doc not in lookups_by_doc:
            return None
        lookup = lookups_by_doc[ref_doc]

        # 1. Exacto
        if ref_section in lookup:
            return lookup[ref_section]

        # 2. Slug
        slug = _slugify(ref_section)
        if slug in lookup:
            return lookup[slug]

        # 3. Número extraído
        m = re.search(r"\d+", ref_section)
        if m and m.group(0) in lookup:
            return lookup[m.group(0)]

        return None

    # ------------------------------------------------------------------
    # Construcción de artefactos finales
    # ------------------------------------------------------------------

    def _build_cross_ref_graph(
        self, edges: list[dict], unresolved: list[dict]
    ) -> dict[str, Any]:
        """Construye el dict del grafo de referencias cruzadas.

        Args:
            edges: Refs resueltas con {from, to, raw}.
            unresolved: Refs no resueltas con {from, raw}.

        Returns:
            Dict con 'edges', 'unresolved' y 'stats'.
        """
        total = len(edges) + len(unresolved)
        logger.info(
            f"Grafo de refs: {len(edges)}/{total} resueltas, "
            f"{len(unresolved)} sin resolver"
        )
        return {
            "edges": edges,
            "unresolved": unresolved,
            "stats": {
                "total": total,
                "resolved": len(edges),
                "unresolved": len(unresolved),
            },
        }

    def _build_hierarchical_index(
        self, chunks_by_doc: dict[str, list[Chunk]]
    ) -> dict[str, Any]:
        """Construye el índice jerárquico (solo títulos, sin texto).

        Args:
            chunks_by_doc: Chunks finales por documento.

        Returns:
            Dict indexado por doc_id con nombre, fuente y lista de chunks (sin texto).
        """
        index: dict[str, Any] = {}
        for doc_id, chunks in chunks_by_doc.items():
            doc_cfg = self._config.documents[doc_id]
            index[doc_id] = {
                "nombre": doc_cfg.nombre,
                "fuente": doc_cfg.fuente,
                "chunks": [
                    {
                        "chunk_id": c.chunk_id,
                        "titulo_seccion": c.titulo_seccion,
                        "jerarquia": c.jerarquia,
                        "nivel": c.nivel,
                    }
                    for c in chunks
                ],
            }
        return index

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _save_outputs(
        self,
        chunks_by_doc: dict[str, list[Chunk]],
        graph: dict,
        index: dict,
    ) -> None:
        """Guarda todos los artefactos de chunking en disco.

        Args:
            chunks_by_doc: Chunks finales por documento.
            graph: Grafo de referencias cruzadas.
            index: Índice jerárquico.
        """
        chunks_dir = self._root / "data" / "chunks"
        index_dir = self._root / "data" / "index"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        index_dir.mkdir(parents=True, exist_ok=True)

        for doc_id, chunks in chunks_by_doc.items():
            out_path = chunks_dir / f"{doc_id}_chunks.json"
            data = [c.model_dump() for c in chunks]
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info(f"Guardado: {out_path.name} ({len(chunks)} chunks)")

        graph_path = chunks_dir / "cross_references_graph.json"
        graph_path.write_text(
            json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            f"Guardado: cross_references_graph.json "
            f"({graph['stats']['resolved']}/{graph['stats']['total']} refs resueltas)"
        )

        index_path = index_dir / "hierarchical_index.json"
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Guardado: hierarchical_index.json")


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------


def _register_lookup_rgc(
    lookup: dict[str, str],
    titulo: str,
    chunk_id: str,
    texto: str,
) -> None:
    """Registra claves de lookup para documentos rgc-fabm.

    Además de los slugs del título, escanea el cuerpo del texto para
    registrar artículos mencionados (útil para resolver [REF:rgc-fabm:art-N]).

    Args:
        lookup: Dict de lookup a actualizar in-place.
        titulo: Título de la sección o capítulo.
        chunk_id: ID del chunk al que apuntan las claves.
        texto: Texto completo del chunk (para escanear artículos).
    """
    slug = _slugify(titulo[:60])
    lookup[slug] = chunk_id
    m_num = re.search(r"\d+", titulo)
    if m_num:
        lookup[m_num.group(0)] = chunk_id

    # Escanear artículos en el cuerpo (solo registrar si no existe ya una clave más específica)
    for m in _ART_IN_TEXT.finditer(texto):
        art_num = m.group(1)
        key_art = f"art-{art_num}"
        key_art2 = f"articulo-{art_num}"
        if key_art not in lookup:
            lookup[key_art] = chunk_id
        if key_art2 not in lookup:
            lookup[key_art2] = chunk_id


# ---------------------------------------------------------------------------
# Entry point para pruebas directas
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from refmate.config import get_config

    config = get_config()
    chunker = Chunker(config)
    stats = chunker.run_all()

    print("\n=== Resultados del Chunker ===")
    for doc_id, doc_stats in stats.items():
        if doc_id == "cross_refs":
            continue
        print(
            f"  {doc_id}: {doc_stats['chunks']} chunks, "
            f"max={doc_stats['tokens_max']} tokens, "
            f"avg={doc_stats['tokens_avg']} tokens"
        )
    if "cross_refs" in stats:
        cr = stats["cross_refs"]
        print(
            f"\n  Referencias cruzadas: {cr['resolved']}/{cr['total']} resueltas "
            f"({cr['unresolved']} sin resolver)"
        )
