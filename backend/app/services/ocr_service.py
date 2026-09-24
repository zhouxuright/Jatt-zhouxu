"""OCR service for extracting text from scanned documents and images.

Supports:
- PDF with scanned pages (using pdf2image + pytesseract or PaddleOCR)
- Image files (JPG, PNG) with legal document content
- Mixed PDF (some pages scanned, some text)

Engines are lazy-loaded so the system works without OCR libraries installed,
with graceful degradation.
"""

import logging
import os
import tempfile
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class OCRService:
    """OCR service for extracting text from scanned documents and images.

    Supports:
    - PDF with scanned pages (using pdf2image + pytesseract or PaddleOCR)
    - Image files (JPG, PNG) with legal document content
    - Mixed PDF (some pages scanned, some text)

    The OCR engine is lazy-loaded on first use.  PaddleOCR is preferred for
    Chinese text; if unavailable the service falls back to pytesseract.
    """

    def __init__(self) -> None:
        self._ocr_engine: Any = None
        self._engine_type: str = ""

    # ------------------------------------------------------------------
    # Engine initialisation
    # ------------------------------------------------------------------

    def _init_engine(self) -> None:
        """Lazy-load OCR engine.  Prefer PaddleOCR for Chinese, fallback to tesseract."""
        if self._ocr_engine is not None:
            return

        engine_pref = settings.OCR_ENGINE.lower()

        if engine_pref == "paddle" or engine_pref == "auto":
            try:
                from paddleocr import PaddleOCR
                self._ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
                self._engine_type = "paddle"
                logger.info("OCR engine: PaddleOCR (Chinese)")
                return
            except ImportError:
                if engine_pref == "paddle":
                    logger.error("PaddleOCR requested but not installed")
                    raise

        if engine_pref == "tesseract" or engine_pref == "auto":
            try:
                import pytesseract  # noqa: F401
                self._ocr_engine = pytesseract
                self._engine_type = "tesseract"
                logger.info("OCR engine: Tesseract")
                return
            except ImportError:
                if engine_pref == "tesseract":
                    logger.error("pytesseract requested but not installed")
                    raise

        logger.warning("No OCR engine available. Install paddleocr or pytesseract.")

    def _ensure_engine(self) -> None:
        """Ensure the OCR engine is loaded, raising a clear error if not."""
        if self._ocr_engine is None:
            self._init_engine()
        if self._ocr_engine is None:
            raise RuntimeError(
                "OCR is not available. Install optional dependencies: "
                "paddleocr (preferred for Chinese) or pytesseract + pdf2image."
            )

    @property
    def available(self) -> bool:
        """Return True if at least one OCR engine can be loaded."""
        try:
            self._ensure_engine()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract_text_from_pdf(self, file_content: bytes) -> dict[str, Any]:
        """Extract text from PDF, handling both text and scanned pages.

        Returns:
            {
                'text': full extracted text,
                'pages': [{'page_num': 1, 'text': '...', 'is_scanned': True/False}],
                'metadata': {'total_pages': N, 'scanned_pages': M}
            }
        """
        self._ensure_engine()

        # First pass: extract text normally via pdfplumber
        page_texts = await self._extract_pdf_text(file_content)

        # Determine which pages are scanned (very little or no text)
        scanned_threshold = 100  # characters
        pages: list[dict[str, Any]] = []
        scanned_count = 0

        for idx, text in enumerate(page_texts):
            is_scanned = len(text.strip()) < scanned_threshold
            if is_scanned:
                scanned_count += 1
            pages.append({
                "page_num": idx + 1,
                "text": text.strip(),
                "is_scanned": is_scanned,
            })

        # Second pass: OCR the scanned pages
        if scanned_count > 0:
            ocr_texts = await self._ocr_pdf_pages(file_content, pages)
            for page_info, ocr_text in zip(pages, ocr_texts):
                if page_info["is_scanned"] and ocr_text:
                    page_info["text"] = ocr_text.strip()

        full_text = "\n\n".join(p["text"] for p in pages if p["text"])

        return {
            "text": full_text,
            "pages": pages,
            "metadata": {
                "total_pages": len(pages),
                "scanned_pages": scanned_count,
            },
        }

    async def extract_text_from_image(self, file_content: bytes) -> str:
        """Extract text from an image file.

        Args:
            file_content: Raw image bytes (JPG, PNG, etc.).

        Returns:
            Extracted text string.
        """
        self._ensure_engine()

        if self._engine_type == "paddle":
            return self._ocr_image_paddle(file_content)
        else:
            return self._ocr_image_tesseract(file_content)

    async def process_contract_upload(self, file_content: bytes, filename: str) -> dict[str, Any]:
        """Process a contract file that may be scanned.

        1. Try normal text extraction first
        2. If text is too short (< 100 chars), assume scanned
        3. Run OCR on scanned pages
        4. Combine text from all pages

        Args:
            file_content: Raw file bytes.
            filename: Original filename (used to determine format).

        Returns:
            Dict with 'text', 'pages', 'metadata', and 'used_ocr' flag.
        """
        ext = os.path.splitext(filename)[1].lower()

        if ext == ".pdf":
            result = await self.extract_text_from_pdf(file_content)
            result["used_ocr"] = result["metadata"]["scanned_pages"] > 0
            return result

        if ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"):
            text = await self.extract_text_from_image(file_content)
            return {
                "text": text,
                "pages": [{"page_num": 1, "text": text, "is_scanned": True}],
                "metadata": {"total_pages": 1, "scanned_pages": 1},
                "used_ocr": True,
            }

        raise ValueError(f"Unsupported file format for OCR: {ext}")

    # ------------------------------------------------------------------
    # Internal: PDF text extraction (pdfplumber)
    # ------------------------------------------------------------------

    async def _extract_pdf_text(self, file_content: bytes) -> list[str]:
        """Extract text from each page of a PDF using pdfplumber.

        Returns:
            List of text strings, one per page.
        """
        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber not installed")
            raise

        page_texts: list[str] = []
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        try:
            with pdfplumber.open(tmp_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    page_texts.append(text or "")
        finally:
            os.unlink(tmp_path)

        return page_texts

    # ------------------------------------------------------------------
    # Internal: OCR scanned PDF pages
    # ------------------------------------------------------------------

    async def _ocr_pdf_pages(
        self, file_content: bytes, pages: list[dict[str, Any]],
    ) -> list[str]:
        """Run OCR on pages that were identified as scanned.

        Returns:
            List of OCR text strings (empty string for non-scanned pages).
        """
        try:
            from pdf2image import convert_from_bytes
        except ImportError:
            logger.error("pdf2image not installed. Run: pip install pdf2image")
            return [""] * len(pages)

        try:
            images = convert_from_bytes(file_content)
        except Exception as exc:
            logger.error("pdf2image failed to convert PDF: %s", exc)
            return [""] * len(pages)

        results: list[str] = []
        for idx, page_info in enumerate(pages):
            if not page_info["is_scanned"]:
                results.append("")
                continue
            if idx >= len(images):
                results.append("")
                continue

            img = images[idx]
            ocr_text = self._ocr_pil_image(img)
            results.append(ocr_text)

        return results

    # ------------------------------------------------------------------
    # Internal: Image OCR
    # ------------------------------------------------------------------

    def _ocr_image_paddle(self, file_content: bytes) -> str:
        """OCR an image using PaddleOCR."""
        import numpy as np
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(file_content))
        img_array = np.array(img)
        result = self._ocr_engine.ocr(img_array, cls=True)

        text_parts: list[str] = []
        if result and result[0]:
            for line in result[0]:
                text_parts.append(line[1][0])
        return "\n".join(text_parts)

    def _ocr_image_tesseract(self, file_content: bytes) -> str:
        """OCR an image using Tesseract."""
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(file_content))
        text = self._ocr_engine.image_to_string(img, lang="chi_sim+eng")
        return text

    def _ocr_pil_image(self, img: Any) -> str:
        """OCR a PIL Image object using the active engine."""
        if self._engine_type == "paddle":
            import numpy as np
            img_array = np.array(img)
            result = self._ocr_engine.ocr(img_array, cls=True)
            text_parts: list[str] = []
            if result and result[0]:
                for line in result[0]:
                    text_parts.append(line[1][0])
            return "\n".join(text_parts)
        else:
            return self._ocr_engine.image_to_string(img, lang="chi_sim+eng")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_ocr_service: OCRService | None = None


def get_ocr_service() -> OCRService:
    """Return a singleton OCRService instance."""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
    return _ocr_service
