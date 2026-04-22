"""파일에서 텍스트를 추출하는 모듈.

확장자를 보고 적절한 파서를 골라 순수 텍스트 문자열로 돌려준다.

- .txt, .md : UTF-8(또는 CP949) 그대로 읽기
- .pdf      : 1순위 PyMuPDF(fitz) — 한글 레이아웃 읽기 순서 품질이 가장 좋음.
              실패 시 pdfplumber 폴백(표 마크다운 변환 지원).
              그래도 실패하면 pypdf 최후 폴백.
- .docx     : python-docx로 문단 + 표 모두 추출.

표는 마크다운 형식( | col | col | )으로 변환해서 본문에 끼워넣는다.
LLM이 마크다운 표를 자연스럽게 이해하므로 RAG 검색 품질이 향상된다.
"""

import logging
from pathlib import Path
from typing import List

import fitz  # PyMuPDF
import pdfplumber
from pypdf import PdfReader
from docx import Document as DocxDocument


logger = logging.getLogger(__name__)


class UnsupportedFileType(Exception):
    """지원하지 않는 확장자일 때."""


class EmptyTextError(Exception):
    """파일은 읽었는데 추출된 텍스트가 비어있을 때."""


def extract_text(file_obj, original_name: str) -> str:
    """파일에서 텍스트를 꺼낸다."""
    ext = Path(original_name).suffix.lower()

    if ext in ('.txt', '.md'):
        text = _extract_plain(file_obj)
    elif ext == '.pdf':
        text = _extract_pdf(file_obj)
    elif ext == '.docx':
        text = _extract_docx(file_obj)
    else:
        raise UnsupportedFileType(f'지원하지 않는 확장자: {ext}')

    text = text.strip()
    if not text:
        raise EmptyTextError('파일에서 추출된 텍스트가 없습니다.')
    return text


# ----------------------------------------------------------------------------
# TXT / MD
# ----------------------------------------------------------------------------

def _extract_plain(file_obj) -> str:
    """평문 파일 읽기. UTF-8 우선, 실패 시 CP949로 재시도."""
    file_obj.seek(0)
    raw = file_obj.read()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp949', errors='replace')


# ----------------------------------------------------------------------------
# PDF — pdfplumber 우선, 실패 시 pypdf 폴백
# ----------------------------------------------------------------------------

def _extract_pdf(file_obj) -> str:
    """PDF에서 텍스트를 추출. PyMuPDF → pdfplumber → pypdf 순서로 폴백.

    PyMuPDF는 한국어·복잡 레이아웃 PDF에서 읽기 순서를 가장 잘 보존한다.
    표가 많은 PDF가 PyMuPDF에서 실패하면 pdfplumber(마크다운 표)로 폴백.
    """
    try:
        file_obj.seek(0)
        text = _extract_pdf_with_fitz(file_obj)
        if text.strip():
            return text
        raise RuntimeError('fitz returned empty')
    except Exception as e1:
        logger.warning('PyMuPDF 실패, pdfplumber로 폴백: %s', e1)
        try:
            file_obj.seek(0)
            return _extract_pdf_with_plumber(file_obj)
        except Exception as e2:
            logger.warning('pdfplumber 실패, pypdf로 폴백: %s', e2)
            file_obj.seek(0)
            return _extract_pdf_with_pypdf(file_obj)


def _extract_pdf_with_fitz(file_obj) -> str:
    """PyMuPDF로 PDF 텍스트 추출. 읽기 순서(reading order)를 자연스레 복원."""
    data = file_obj.read()
    doc = fitz.open(stream=data, filetype='pdf')
    try:
        pages_text = []
        for page in doc:
            # 'text' 모드: 블록 단위로 읽기 순서대로 텍스트 반환
            text = page.get_text('text') or ''
            if text.strip():
                pages_text.append(text)
        return '\n\n'.join(pages_text)
    finally:
        doc.close()


def _extract_pdf_with_plumber(file_obj) -> str:
    pages_output: List[str] = []
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            # 1) 먼저 이 페이지의 표를 마크다운으로 변환
            tables = page.extract_tables() or []
            table_markdowns = [_table_to_markdown(t) for t in tables]

            # 2) 페이지 본문 텍스트
            text = page.extract_text() or ''

            # 3) 텍스트 뒤에 표를 이어붙임 (표 위치 보존은 복잡해서 생략)
            parts = []
            if text.strip():
                parts.append(text.strip())
            parts.extend(table_markdowns)

            if parts:
                pages_output.append('\n\n'.join(parts))

    return '\n\n'.join(pages_output)


def _extract_pdf_with_pypdf(file_obj) -> str:
    reader = PdfReader(file_obj)
    pages = [page.extract_text() or '' for page in reader.pages]
    return '\n\n'.join(p for p in pages if p.strip())


# ----------------------------------------------------------------------------
# DOCX — 문단 + 표 추출
# ----------------------------------------------------------------------------

def _extract_docx(file_obj) -> str:
    """DOCX의 문단과 표를 모두 추출.

    python-docx는 문서를 순회할 때 문단·표를 순서대로 방문할 수 있도록
    element.body.iter() 같은 API를 제공하지만 복잡하므로,
    여기서는 단순화를 위해 '문단 전체 → 표 전체' 순서로 이어붙인다.
    (완벽한 원본 순서는 아니지만 대부분 내용은 보존됨)
    """
    file_obj.seek(0)
    doc = DocxDocument(file_obj)

    # 1) 본문 문단
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

    # 2) 표 (각각 마크다운으로)
    table_markdowns: List[str] = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
            rows.append(cells)
        md = _table_to_markdown(rows)
        if md:
            table_markdowns.append(md)

    parts: List[str] = []
    if paragraphs:
        parts.append('\n'.join(paragraphs))
    parts.extend(table_markdowns)

    return '\n\n'.join(parts)


# ----------------------------------------------------------------------------
# 표 → 마크다운 공용 변환
# ----------------------------------------------------------------------------

def _table_to_markdown(rows) -> str:
    """2차원 리스트(행 x 셀)를 마크다운 표로 변환.

    빈 셀은 '-'로 치환, 빈 표는 빈 문자열 반환.
    """
    if not rows:
        return ''

    # 셀 정리 (None, 개행 등)
    def clean(cell):
        if cell is None:
            return ''
        return str(cell).replace('\n', ' ').replace('|', '/').strip()

    cleaned = [[clean(c) for c in row] for row in rows]
    cleaned = [r for r in cleaned if any(cell for cell in r)]  # 빈 행 제거
    if not cleaned:
        return ''

    col_count = max(len(r) for r in cleaned)
    # 열 개수 맞추기
    normalized = [r + [''] * (col_count - len(r)) for r in cleaned]

    # 마크다운 행 조립
    lines = []
    header = normalized[0]
    lines.append('| ' + ' | '.join(header) + ' |')
    lines.append('|' + '|'.join([' --- '] * col_count) + '|')
    for row in normalized[1:]:
        lines.append('| ' + ' | '.join(row) + ' |')

    return '\n'.join(lines)
