"""Document processor for parsing, chunking, and extracting metadata from legal documents.

Supports PDF (pdfplumber), DOCX (python-docx), and TXT formats with Chinese
text-optimized splitting at sentence boundaries.
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Parses and chunks legal documents for RAG ingestion.

    Supports multiple document formats with Chinese-optimized text splitting
    that respects sentence boundaries and semantic coherence.

    Attributes:
        chunk_size: Target size of each text chunk in characters.
        chunk_overlap: Number of characters to overlap between chunks.
        separators: Ordered list of separators for recursive splitting.
    """

    # Chinese sentence-ending punctuation and common separators
    SENTENCE_ENDS = re.compile(r"[。！？；\n](?![」）】])")
    CHINESE_PUNCTUATION = re.compile(r"[，。！？；：、（）【】《》「」『』""'']")

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> None:
        """Initialize the document processor.

        Args:
            chunk_size: Target size of each chunk in characters (default: 1000).
            chunk_overlap: Overlap between consecutive chunks in characters (default: 200).
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]

    async def parse_file(self, file_path: str) -> dict[str, Any]:
        """Parse a document file and return its content and metadata.

        Args:
            file_path: Path to the document file.

        Returns:
            Dict with keys: text, metadata, chunks.

        Raises:
            ValueError: If the file format is not supported.
            FileNotFoundError: If the file does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        suffix = path.suffix.lower()

        if suffix == ".pdf":
            text = await self._parse_pdf(str(path))
        elif suffix == ".docx":
            text = await self._parse_docx(str(path))
        elif suffix == ".doc":
            text = await self._parse_doc(str(path))
        elif suffix in (".txt", ".md", ".markdown"):
            text = await self._parse_txt(str(path))
        else:
            raise ValueError(f"Unsupported file format: {suffix}. Supported: .pdf, .docx, .doc, .txt, .md, .markdown")

        if not text or not text.strip():
            raise ValueError(f"No extractable text found in: {file_path}")

        metadata = self.extract_metadata(text, file_path)
        chunks = self.chunk_text(text)

        logger.info(
            "Parsed '%s': %d chars, %d chunks, type=%s",
            path.name, len(text), len(chunks), suffix,
        )

        return {
            "text": text,
            "metadata": metadata,
            "chunks": chunks,
        }

    async def _parse_pdf(self, file_path: str) -> str:
        """Extract text from a PDF file using pdfplumber.

        Args:
            file_path: Path to the PDF file.

        Returns:
            Extracted text content.
        """
        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber not installed. Run: pip install pdfplumber")
            raise

        text_parts: list[str] = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
        except Exception as exc:
            logger.error("Failed to parse PDF '%s': %s", file_path, exc)
            raise

        return "\n".join(text_parts)

    async def _parse_docx(self, file_path: str) -> str:
        """Extract text from a DOCX file.

        Args:
            file_path: Path to the DOCX file.

        Returns:
            Extracted text content.
        """
        try:
            from docx import Document
        except ImportError:
            logger.error("python-docx not installed. Run: pip install python-docx")
            raise

        try:
            doc = Document(file_path)
            text_parts: list[str] = []
            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)
            return "\n".join(text_parts)
        except Exception as exc:
            logger.error("Failed to parse DOCX '%s': %s", file_path, exc)
            raise

    async def _parse_doc(self, file_path: str) -> str:
        """Extract text from a legacy binary Word (.doc) file.

        `.doc` is not OOXML — it is an OLE2 compound file, so python-docx raises
        `PackageNotFoundError` on it. Routing `.doc` into `_parse_docx` meant the
        upload endpoint accepted the extension and then failed with
        `parse_success: false` on every genuine legacy file.

        Order matters here: a `.doc` that python-docx *can* open is really a
        renamed `.docx`, which is common and should keep working.

        Returns:
            Extracted text content.

        Raises:
            ValueError: If no text could be recovered by any strategy.
        """
        # 1. Renamed OOXML (.docx saved with a .doc extension).
        try:
            return await self._parse_docx(file_path)
        except Exception:
            logger.debug("'%s' is not OOXML; treating as OLE2 legacy .doc", file_path)

        # 2. OLE2 WordDocument stream. The stream carries UTF-16LE text once the
        #    FIB/structure overhead is skipped, so decode and keep the runs of
        #    readable text. This is lossy (no styling, tables come out flat) but
        #    it recovers the actual document wording, unlike a raw byte decode.
        text = self._extract_text_from_ole2(file_path)
        if text.strip():
            return text

        # 3. Last resort: whole-file decode, for a .doc that is really a plain
        #    text file under the wrong extension. The result is validated before
        #    being returned — an unvalidated decode turned a 2 KB junk file into
        #    101 characters of confident-looking noise, which would then be
        #    indexed as legal document text.
        logger.warning(
            "OLE2 text extraction found nothing usable in '%s'; "
            "attempting raw byte decode",
            file_path,
        )
        raw = Path(file_path).read_bytes()
        # gb18030 is deliberately excluded: it maps almost any byte pair to a
        # valid character, so random binary scores ~92% "document-like" and slips
        # past any ratio gate. utf-8 and utf-16-le have real structural
        # constraints. A GBK-encoded text file renamed to .doc is therefore not
        # recovered here — the raised error tells the user to re-save the file.
        for encoding in ("utf-8", "utf-16-le"):
            try:
                decoded = raw.decode(encoding, errors="ignore")
            except Exception:
                continue
            printable = "".join(
                ch for ch in decoded
                if (ch.isprintable() or ch in "\n\r\t")
                and not ("" <= ch <= "")
            )
            if DocumentProcessor._looks_like_document_text(printable):
                return printable

        raise ValueError(
            "无法从该 .doc 文件中提取文本。它是旧版二进制格式，"
            "建议用 Word 另存为 .docx 后重新上传。"
        )

    @classmethod
    def _looks_like_document_text(cls, text: str, *, min_chars: int = 20) -> bool:
        """Whether decoded bytes plausibly are document text.

        Gates the last-resort raw decode: the ratio of document characters must
        be high, so binary scaffolding that happens to decode into printable
        glyphs is rejected instead of being returned as content.
        """
        stripped = text.strip()
        if len(stripped) < min_chars:
            return False
        good = sum(1 for ch in stripped if cls._is_document_char(ch))
        return good / len(stripped) > 0.9

    @staticmethod
    def _extract_text_from_ole2(file_path: str) -> str:
        """Pull readable text out of the WordDocument stream of an OLE2 .doc."""
        try:
            import olefile
        except ImportError:
            logger.warning("olefile not installed; cannot read legacy .doc")
            return ""

        if not olefile.isOleFile(file_path):
            return ""

        try:
            ole = olefile.OleFileIO(file_path)
        except Exception as exc:
            logger.warning("olefile could not open '%s': %s", file_path, exc)
            return ""

        try:
            if not ole.exists("WordDocument"):
                return ""
            data = ole.openstream("WordDocument").read()
        except Exception as exc:
            logger.warning("Could not read WordDocument stream: %s", exc)
            return ""
        finally:
            ole.close()

        # Word stores body text as UTF-16LE. Decode tolerantly, then drop the
        # binary scaffolding, which decodes into control characters, Private Use
        # Area markers, and (most awkwardly) Latin-Extended/IPA glyphs that look
        # alphanumeric to `str.isalnum()` but are not document text.
        decoded = data.decode("utf-16-le", errors="ignore")
        cleaned = "".join(
            ch
            if (ch.isprintable() or ch in "\n\r\t")
            # Private Use Area (U+E000-U+F8FF) decodes from Word's internal
            # markers; keeping it would inject invisible glyphs into the corpus.
            and not ("\ue000" <= ch <= "\uf8ff")
            else "\n"
            for ch in decoded
        )

        # Binary scaffolding runs straight into prose with no separator, so cut
        # the string at each run of 2+ characters outside the classes a Chinese
        # legal document actually uses. A single stray glyph is kept -- real
        # documents do occasionally contain one.
        text_chunks = re.split(
            r"[^\x00-\x7f\u3000-\u303f\u4e00-\u9fff\u3400-\u4dbf\uff00-\uffef]{2,}",
            cleaned,
        )

        lines: list[str] = []
        for chunk in text_chunks:
            for line in chunk.splitlines():
                line = line.strip()
                if len(line) < 2:
                    continue
                good = sum(1 for ch in line if DocumentProcessor._is_document_char(ch))
                if good / max(len(line), 1) > 0.7:
                    lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _is_document_char(ch: str) -> bool:
        """Whether `ch` plausibly belongs to Chinese legal prose."""
        if ch.isascii():
            return ch.isalnum() or ch in " 	.,;:!?(){}[]'-_—…%&@#*+=<>/"
        # CJK ideographs (incl. extension A) and CJK / fullwidth punctuation.
        return (
            "\u4e00" <= ch <= "\u9fff"
            or "\u3400" <= ch <= "\u4dbf"
            or "\u3000" <= ch <= "\u303f"
            or "\uff00" <= ch <= "\uffef"
        )

    async def _parse_txt(self, file_path: str) -> str:
        """Read plain text from a TXT file.

        Args:
            file_path: Path to the TXT file.

        Returns:
            File content as string.
        """
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Try common Chinese encodings
            for encoding in ("gbk", "gb2312", "gb18030", "latin-1"):
                try:
                    return Path(file_path).read_text(encoding=encoding)
                except (UnicodeDecodeError, LookupError):
                    continue
            raise ValueError(f"Unable to decode text file: {file_path}")

    def chunk_text(self, text: str) -> list[str]:
        """Split text into chunks optimized for Chinese legal documents.

        Uses recursive sentence-boundary-aware splitting to produce chunks
        of approximately chunk_size characters with chunk_overlap overlap.

        Args:
            text: The full document text to split.

        Returns:
            List of text chunks.
        """
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []

        chunks = self._recursive_split(text, self.separators)
        return self._merge_splits(chunks)

    def _recursive_split(self, text: str, separators: list[str]) -> list[str]:
        """Recursively split text using the given separators.

        Args:
            text: Text to split.
            separators: Ordered list of separators to try.

        Returns:
            List of text segments.
        """
        if not separators:
            return [text]

        separator = separators[0]
        remaining_separators = separators[1:]

        if separator == "":
            # Character-level split
            return list(text)

        splits = re.split(f"({re.escape(separator)})", text)

        # Rejoin delimiter with preceding segment
        merged = []
        for i in range(0, len(splits) - 1, 2):
            merged.append(splits[i] + splits[i + 1])
        if len(splits) % 2 == 1:
            merged.append(splits[-1])

        # If splitting by this separator didn't help, try next
        if len(merged) == 1:
            return self._recursive_split(text, remaining_separators)

        final_chunks: list[str] = []
        for segment in merged:
            if len(segment) <= self.chunk_size:
                final_chunks.append(segment)
            else:
                final_chunks.extend(self._recursive_split(segment, remaining_separators))

        return final_chunks

    def _merge_splits(self, splits: list[str]) -> list[str]:
        """Merge small splits into chunks of approximately chunk_size.

        Args:
            splits: List of text segments to merge.

        Returns:
            List of merged text chunks.
        """
        if not splits:
            return []

        chunks: list[str] = []
        current_chunk = ""
        current_len = 0

        for split in splits:
            split_len = len(split)

            if current_len + split_len <= self.chunk_size:
                current_chunk += split
                current_len += split_len
            else:
                if current_chunk:
                    chunks.append(current_chunk)

                # If the split itself is larger than chunk_size, keep it as-is
                if split_len > self.chunk_size:
                    chunks.append(split)
                    current_chunk = ""
                    current_len = 0
                else:
                    # Start new chunk with overlap from previous
                    if self.chunk_overlap > 0 and current_chunk:
                        overlap_text = current_chunk[-self.chunk_overlap:]
                        current_chunk = overlap_text + split
                        current_len = len(current_chunk)
                    else:
                        current_chunk = split
                        current_len = split_len

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def extract_metadata(self, text: str, file_path: str = "") -> dict[str, Any]:
        """Extract metadata from document text and file path.

        Attempts to identify title, date, source, and document type from
        the document content and filename.

        Args:
            text: The document text.
            file_path: Path to the source file (optional).

        Returns:
            Dictionary with metadata keys: title, date, source, doc_type, file_path.
        """
        path = Path(file_path)
        metadata: dict[str, Any] = {
            "file_path": str(path) if file_path else "",
            "filename": path.name if file_path else "",
            "file_type": path.suffix.lower().lstrip(".") if file_path else "",
            "title": "",
            "date": "",
            "source": "",
            "doc_type": "unknown",
            "char_count": len(text),
            "processed_at": datetime.utcnow().isoformat(),
        }

        # Try to extract date from text
        date_patterns = [
            r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}[日]?)",
            r"(\d{4}年\d{1,2}月\d{1,2}日)",
            r"(\d{4}-\d{2}-\d{2})",
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text[:500])
            if match:
                metadata["date"] = match.group(1)
                break

        # Try to extract title from first non-empty line
        lines = text.strip().split("\n")
        for line in lines[:5]:
            line = line.strip()
            if line and len(line) < 200:
                metadata["title"] = line
                break

        # Detect document type from filename patterns
        filename_lower = path.name.lower()
        if any(kw in filename_lower for kw in ["合同", "contract", "协议", "agreement"]):
            metadata["doc_type"] = "contract"
        elif any(kw in filename_lower for kw in ["判决", "裁定", "judgment", "ruling", "判决书"]):
            metadata["doc_type"] = "judgment"
        elif any(kw in filename_lower for kw in ["起诉", "起诉书", "indictment", "complaint"]):
            metadata["doc_type"] = "complaint"
        elif any(kw in filename_lower for kw in ["法律", "法规", "条例", "law", "regulation", "statute"]):
            metadata["doc_type"] = "law"
        elif any(kw in filename_lower for kw in ["意见", "通知", "opinion", "notice"]):
            metadata["doc_type"] = "legal_opinion"
        elif any(kw in filename_lower for kw in ["证据", "evidence", "exhibit"]):
            metadata["doc_type"] = "evidence"

        return metadata

    def clean_text(self, text: str) -> str:
        """Clean and normalize text for better embedding quality.

        Removes excessive whitespace, normalizes punctuation, and strips
        non-content headers/footers.

        Args:
            text: Raw text to clean.

        Returns:
            Cleaned text.
        """
        # Remove excessive whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Normalize Chinese punctuation
        text = text.replace("＂", '"').replace("＇", "'")

        # Remove common page artifacts
        text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)

        return text.strip()