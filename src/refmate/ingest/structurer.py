"""Structurer: convierte texto OCR plano en Markdown jerárquico.

Responsabilidad única: tomar `data/ocr/{doc_id}_raw.txt`, aplicar ventaneo
y llamar al TextGenerator para producir `data/structured/{doc_id}.md` y
`data/structured/{doc_id}_refs.json`.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from loguru import logger

from refmate.config import RefMateConfig, get_project_root
from refmate.core.protocols import TextGenerator

_WINDOW_SIZE_CHARS = 48_000  # ≈12 000 tokens a 4 chars/token
_PAGE_SEPARATOR = re.compile(r"<!-- PAGE \d+ -->")
_REF_PATTERN = re.compile(r"\[REF:([^:]+):([^\]]+)\]")

# Patrones para validar identificadores normativos por documento
_NORM_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "reglas-de-juego": re.compile(r"\d{1,2}:\d{1,2}"),
    "rgc-fabm": re.compile(r"[Aa]rt[ií]culo\s+\d+"),
    "add-fabm": re.compile(r"[Aa]rt[ií]culo\s+\d+"),
}


class Structurer:
    """Convierte texto OCR en Markdown estructurado usando un TextGenerator.

    Aplica un algoritmo de ventaneo con overlap para manejar documentos
    extensos respetando los límites de contexto del LLM.
    """

    def __init__(self, generator: TextGenerator, config: RefMateConfig) -> None:
        """Inicializa el structurer con inyección de dependencias.

        Args:
            generator: Implementación del protocolo TextGenerator.
            config: Configuración completa del sistema RefMate.
        """
        self._generator = generator
        self._config = config
        self._root = get_project_root()

    async def run(self, doc_id: str) -> dict:
        """Ejecuta el pipeline de estructuración para un documento.

        Carga el texto OCR, lo divide en ventanas, llama al LLM por cada
        ventana y combina el resultado en un único fichero Markdown.

        Args:
            doc_id: Identificador del documento (ej. 'reglas-de-juego').

        Returns:
            Diccionario con estadísticas: ventanas, chars_ocr, chars_structured, refs.

        Raises:
            FileNotFoundError: Si el fichero OCR no existe. Ejecuta primero
                `uv run python -m refmate.ingest.ocr_runner`.
        """
        ocr_path = self._root / "data" / "ocr" / f"{doc_id}_raw.txt"
        if not ocr_path.exists():
            raise FileNotFoundError(
                f"Fichero OCR no encontrado: {ocr_path}\n"
                f"Ejecuta primero la FASE 3: uv run python -m refmate.ingest.ocr_runner"
            )

        ocr_text = ocr_path.read_text(encoding="utf-8")
        logger.info(f"OCR cargado → {doc_id} ({len(ocr_text)} chars)")

        prompt = self._load_prompt(doc_id)
        windows = self._build_windows(ocr_text, doc_id)
        logger.info(f"Ventanas generadas: {len(windows)} para {doc_id}")

        structured_parts = await self._generate_windows(windows, doc_id, prompt)
        structured = self._combine_windows(structured_parts)

        self._validate_output(structured, ocr_text, doc_id)

        refs = self._extract_refs(structured)

        out_dir = self._root / "data" / "structured"
        out_dir.mkdir(parents=True, exist_ok=True)

        md_path = out_dir / f"{doc_id}.md"
        refs_path = out_dir / f"{doc_id}_refs.json"

        md_path.write_text(structured, encoding="utf-8")
        refs_path.write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(
            f"Estructuración completada → {md_path.name} "
            f"({len(structured)} chars, {len(refs)} refs)"
        )

        return {
            "ventanas": len(windows),
            "chars_ocr": len(ocr_text),
            "chars_structured": len(structured),
            "refs": len(refs),
        }

    # ------------------------------------------------------------------
    # Métodos privados
    # ------------------------------------------------------------------

    def _load_prompt(self, doc_id: str) -> str:
        """Carga el system prompt de estructuración para el documento.

        Args:
            doc_id: Identificador del documento.

        Returns:
            Contenido del fichero de prompt.

        Raises:
            FileNotFoundError: Si el prompt no existe para este doc_id.
        """
        prompt_path = (
            Path(__file__).resolve().parents[1]
            / "prompts"
            / "structuring"
            / f"{doc_id}.md"
        )
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"Prompt de estructuración no encontrado: {prompt_path}"
            )
        return prompt_path.read_text(encoding="utf-8")

    def _build_windows(self, ocr_text: str, doc_id: str) -> list[str]:
        """Divide el texto OCR en ventanas solapadas de páginas completas.

        Agrupa páginas (separadas por `<!-- PAGE N -->`) hasta alcanzar el
        tamaño de ventana. El overlap en chars se usa para mantener contexto
        entre ventanas.

        Args:
            ocr_text: Texto completo del fichero OCR.
            doc_id: Usado para logging.

        Returns:
            Lista de strings, una por ventana.
        """
        overlap_chars = (self._config.models.structuring.overlap_tokens or 500) * 4

        # Dividir en segmentos de página manteniendo el separador
        raw_pages = _PAGE_SEPARATOR.split(ocr_text)
        separators = _PAGE_SEPARATOR.findall(ocr_text)

        # Reconstruir páginas con sus separadores
        pages: list[str] = []
        for i, page_text in enumerate(raw_pages):
            if i == 0:
                pages.append(page_text)
            else:
                pages.append(separators[i - 1] + page_text)

        windows: list[str] = []
        current_parts: list[str] = []
        current_chars = 0

        for page in pages:
            page_chars = len(page)

            if current_chars + page_chars > _WINDOW_SIZE_CHARS and current_parts:
                windows.append("".join(current_parts))
                # Mantener overlap: páginas del final de la ventana actual
                overlap_parts: list[str] = []
                overlap_size = 0
                for part in reversed(current_parts):
                    if overlap_size + len(part) > overlap_chars:
                        break
                    overlap_parts.insert(0, part)
                    overlap_size += len(part)
                current_parts = overlap_parts
                current_chars = overlap_size

            current_parts.append(page)
            current_chars += page_chars

        if current_parts:
            windows.append("".join(current_parts))

        logger.debug(
            f"Ventaneo {doc_id}: {len(pages)} páginas → {len(windows)} ventanas "
            f"(overlap={overlap_chars} chars)"
        )
        return windows

    async def _generate_windows(
        self, windows: list[str], doc_id: str, prompt: str
    ) -> list[str]:
        """Llama al TextGenerator por cada ventana con rate limiting.

        Args:
            windows: Lista de textos de ventana.
            doc_id: Identificador del documento (para logging).
            prompt: System prompt de estructuración ya cargado.

        Returns:
            Lista de textos estructurados en el mismo orden.
        """
        results: list[str] = []
        total = len(windows)

        for i, window in enumerate(windows, start=1):
            logger.info(f"Ventana {i}/{total} — {doc_id}")
            result = await self._generator.generate(
                system_prompt=prompt,
                user_prompt=window,
            )
            results.append(result)

            if i < total:
                await asyncio.sleep(1)

        return results

    def _combine_windows(self, parts: list[str]) -> str:
        """Combina las ventanas estructuradas eliminando el overlap.

        Para cada ventana (excepto la primera), busca en el output previo el
        primer párrafo no-overlap y descarta el contenido duplicado.

        Args:
            parts: Lista de textos generados por ventana.

        Returns:
            Texto Markdown combinado.
        """
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]

        combined = parts[0]
        for part in parts[1:]:
            # Buscar el primer párrafo del part en el texto combinado acumulado
            first_para = self._first_paragraph(part)
            if first_para and first_para in combined:
                idx = combined.rfind(first_para)
                combined = combined[:idx] + part
            else:
                # Fallback: concatenar con separador de línea
                combined = combined.rstrip("\n") + "\n\n" + part.lstrip("\n")

        return combined

    @staticmethod
    def _first_paragraph(text: str) -> str | None:
        """Extrae el primer párrafo no vacío de un texto.

        Args:
            text: Texto de entrada.

        Returns:
            Primer párrafo o None si el texto está vacío.
        """
        for para in text.split("\n\n"):
            stripped = para.strip()
            if stripped:
                return stripped
        return None

    def _validate_output(self, structured: str, ocr_text: str, doc_id: str) -> None:
        """Valida la calidad del output estructurado (warnings, no excepciones).

        Comprueba el ratio de longitud y la presencia de identificadores
        normativos del documento original en el output.

        Args:
            structured: Texto Markdown generado.
            ocr_text: Texto OCR original.
            doc_id: Identificador del documento.
        """
        ratio = len(structured) / max(len(ocr_text), 1)
        if not 0.85 <= ratio <= 1.15:
            logger.warning(
                f"Ratio de longitud fuera de rango para {doc_id}: "
                f"{ratio:.2f} (esperado 0.85–1.15). "
                f"OCR={len(ocr_text)} chars, structured={len(structured)} chars"
            )

        pattern = _NORM_ID_PATTERNS.get(doc_id)
        if pattern:
            ids_in_ocr = pattern.findall(ocr_text)
            if ids_in_ocr:
                # Verificar que al menos el 80% de los identificadores aparecen en el output
                present = sum(1 for id_ in ids_in_ocr if id_ in structured)
                coverage = present / len(ids_in_ocr)
                if coverage < 0.8:
                    logger.warning(
                        f"Cobertura de identificadores normativos baja para {doc_id}: "
                        f"{coverage:.0%} ({present}/{len(ids_in_ocr)})"
                    )
                else:
                    logger.debug(
                        f"Cobertura de identificadores normativos: "
                        f"{coverage:.0%} para {doc_id}"
                    )

    @staticmethod
    def _extract_refs(structured: str) -> list[dict]:
        """Extrae referencias cruzadas del formato [REF:doc_id:section_id].

        Args:
            structured: Texto Markdown con referencias incrustadas.

        Returns:
            Lista de dicts con keys 'doc_id', 'section_id', 'raw'.
        """
        refs = []
        for match in _REF_PATTERN.finditer(structured):
            refs.append(
                {
                    "doc_id": match.group(1),
                    "section_id": match.group(2),
                    "raw": match.group(0),
                }
            )
        return refs
