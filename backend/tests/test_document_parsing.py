"""Tests for the document parsers behind the chat / batch upload endpoints.

These cover the format dispatch in `DocumentProcessor` and the text recovery
used for legacy binary `.doc`, which is the format most likely to regress: it is
an OLE2 compound file rather than OOXML, so it cannot go through python-docx.
"""

import struct

import pytest

from app.rag.document_processor import DocumentProcessor

SECTOR_SIZE = 512
_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_FATSECT = 0xFFFFFFFD


@pytest.fixture
def processor() -> DocumentProcessor:
    return DocumentProcessor()


def _write_docx(path: str, text: str) -> None:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph(text)
    doc.save(path)


def _write_ole2_doc(path: str, text: str) -> None:
    """Write a valid OLE2 compound file holding `text` in WordDocument.

    A real .doc carries far more structure, but `olefile` only needs a
    well-formed header, FAT, directory and stream. Fabricating the container
    (rather than hand-writing magic bytes) is what makes these tests exercise
    the actual OLE2 code path instead of silently falling through to the
    raw-byte fallback.
    """
    data = text.encode("utf-16-le")
    # Streams below the 4096-byte mini-stream cutoff are kept in the mini
    # stream; pad past it so the payload lives in ordinary FAT sectors.
    if len(data) < 4096:
        data += " ".encode("utf-16-le") * ((4096 - len(data)) // 2)
    n_data_sectors = max(1, (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE)
    data = data.ljust(n_data_sectors * SECTOR_SIZE, b"\x00")

    header = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
    header += bytes(16)                              # CLSID
    header += struct.pack("<HH", 0x003E, 0x0003)     # minor / major version
    header += struct.pack("<H", 0xFFFE)              # byte order
    header += struct.pack("<HH", 9, 6)               # sector / mini-sector shift
    header += bytes(6)                               # reserved
    header += struct.pack("<I", 0)                   # num directory sectors
    header += struct.pack("<I", 1)                   # num FAT sectors
    header += struct.pack("<I", 1)                   # first directory sector
    header += struct.pack("<I", 0)                   # transaction signature
    header += struct.pack("<I", 4096)                # mini stream cutoff
    header += struct.pack("<I", _ENDOFCHAIN)         # first mini FAT sector
    header += struct.pack("<I", 0)                   # num mini FAT sectors
    header += struct.pack("<I", _ENDOFCHAIN)         # first DIFAT sector
    header += struct.pack("<I", 0)                   # num DIFAT sectors
    header += struct.pack("<109I", *([0] + [_FREESECT] * 108))
    assert len(header) == SECTOR_SIZE

    fat = [_FREESECT] * 128
    fat[0] = _FATSECT                                 # sector 0 holds the FAT
    fat[1] = _ENDOFCHAIN                              # directory is one sector
    for index in range(n_data_sectors):
        sector = 2 + index
        fat[sector] = _ENDOFCHAIN if index == n_data_sectors - 1 else sector + 1
    fat_sector = struct.pack("<128I", *fat)

    def directory_entry(name: str, obj_type: int, start: int, size: int,
                        child: int = _FREESECT) -> bytes:
        encoded_name = name.encode("utf-16-le")
        entry = encoded_name + b"\x00\x00"
        entry = entry.ljust(64, b"\x00")
        entry += struct.pack("<H", len(encoded_name) + 2)   # name length
        entry += struct.pack("<B", obj_type)                # 5 = root, 2 = stream
        entry += struct.pack("<B", 1)                       # colour: black
        entry += struct.pack("<I", _FREESECT)               # left sibling
        entry += struct.pack("<I", _FREESECT)               # right sibling
        entry += struct.pack("<I", child)
        entry += bytes(16)                                  # CLSID
        entry += struct.pack("<I", 0)                       # state bits
        entry += struct.pack("<Q", 0) + struct.pack("<Q", 0)  # timestamps
        entry += struct.pack("<I", start)
        entry += struct.pack("<Q", size)
        return entry.ljust(128, b"\x00")

    directory = b"".join([
        directory_entry("Root Entry", 5, _ENDOFCHAIN, 0, child=1),
        directory_entry("WordDocument", 2, 2, len(data)),
        bytes(128),
        bytes(128),
    ])
    assert len(directory) == SECTOR_SIZE

    with open(path, "wb") as handle:
        handle.write(header + fat_sector + directory + data)


# ---------------------------------------------------------------------------
# Format dispatch
# ---------------------------------------------------------------------------


class TestFormatDispatch:
    """Each supported extension must reach its own parser."""

    async def test_txt_is_parsed(self, processor: DocumentProcessor, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("劳动合同纠纷，用人单位未签订书面劳动合同。", encoding="utf-8")

        result = await processor.parse_file(str(target))

        assert "劳动合同纠纷" in result["text"]

    async def test_markdown_is_parsed(self, processor: DocumentProcessor, tmp_path):
        target = tmp_path / "sample.md"
        target.write_text("# 标题\n\n加班费计算标准。", encoding="utf-8")

        result = await processor.parse_file(str(target))

        assert "加班费计算标准" in result["text"]

    async def test_docx_is_parsed(self, processor: DocumentProcessor, tmp_path):
        target = tmp_path / "sample.docx"
        _write_docx(str(target), "用人单位违法解除劳动合同。")

        result = await processor.parse_file(str(target))

        assert "违法解除劳动合同" in result["text"]

    async def test_legacy_doc_parses_ooxml_renamed_to_doc(
        self, processor: DocumentProcessor, tmp_path
    ):
        """A .docx saved with a .doc extension must keep working.

        This is the common real-world case, so the OLE2 path must not shadow it.
        """
        target = tmp_path / "renamed.doc"
        _write_docx(str(target), "劳务派遣单位应当履行用人单位义务。")

        result = await processor.parse_file(str(target))

        assert "劳务派遣" in result["text"]

    async def test_unknown_extension_is_rejected(
        self, processor: DocumentProcessor, tmp_path
    ):
        target = tmp_path / "payload.exe"
        target.write_bytes(b"MZ\x90\x00")

        with pytest.raises(ValueError, match="Unsupported file format"):
            await processor.parse_file(str(target))


# ---------------------------------------------------------------------------
# Legacy binary .doc
# ---------------------------------------------------------------------------


class TestLegacyDocParsing:
    """`.doc` is OLE2, not OOXML — it must not be routed to python-docx."""

    async def test_ole2_doc_recovers_utf16_prose(
        self, processor: DocumentProcessor, tmp_path
    ):
        target = tmp_path / "legacy.doc"
        _write_ole2_doc(
            str(target),
            "原告张某与被告某科技公司劳动争议一案，本院认为用人单位应当支付经济补偿。",
        )

        result = await processor.parse_file(str(target))

        assert "劳动争议" in result["text"]
        assert "经济补偿" in result["text"]

    async def test_ole2_doc_output_has_no_binary_scaffolding(
        self, processor: DocumentProcessor, tmp_path
    ):
        target = tmp_path / "legacy.doc"
        _write_ole2_doc(str(target), "本院认为被告应当承担违约责任。")

        result = await processor.parse_file(str(target))

        # Binary scaffolding decodes into Latin-Extended glyphs that look
        # printable; they must not survive into the corpus.
        for junk in ("þ", "Ā", "ก"):
            assert junk not in result["text"]

    async def test_binary_blob_does_not_become_document_text(
        self, processor: DocumentProcessor, tmp_path
    ):
        """Unrecoverable binary must fail loudly, not return decorative noise.

        The raw-decode fallback previously returned whatever survived an
        `errors="ignore"` decode, so a junk file yielded characters that passed
        as content and would then be indexed as legal document text.
        """
        payload = bytearray([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        # Deterministic high-entropy body: no long printable runs to latch onto.
        seed = 0x12345678
        for _ in range(3000):
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            payload.append(seed >> 16 & 0xFF)
        target = tmp_path / "blob.doc"
        target.write_bytes(bytes(payload))

        with pytest.raises(ValueError, match="docx"):
            await processor.parse_file(str(target))

    async def test_plain_text_misnamed_as_doc_is_recovered(
        self, processor: DocumentProcessor, tmp_path
    ):
        """A genuine text file with a .doc extension should still be readable."""
        target = tmp_path / "notes.doc"
        target.write_text(
            "本院认为，用人单位应当依法支付劳动者经济补偿。", encoding="utf-8"
        )

        result = await processor.parse_file(str(target))

        assert "经济补偿" in result["text"]


# ---------------------------------------------------------------------------
# Character classifier
# ---------------------------------------------------------------------------


class TestDocumentCharClassifier:
    """The filter that keeps prose and drops decoded binary noise."""

    @pytest.mark.parametrize("ch", ["a", "Z", "7", ",", " ", "劳", "，", "（", "《"])
    def test_accepts_document_characters(self, ch: str):
        assert DocumentProcessor._is_document_char(ch)

    @pytest.mark.parametrize("ch", ["þ", "Ā", "ก", "Ա", ""])
    def test_rejects_noise_characters(self, ch: str):
        assert not DocumentProcessor._is_document_char(ch)
