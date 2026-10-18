"""Heading-aware markdown chunking for the knowledge base.

Each chunk keeps its `doc_id` and heading path. Sections are split by markdown headings; a
section longer than TARGET_MAX_TOKENS is split on paragraph boundaries with an overlap carried
from the previous chunk. Token counts are approximated (words x 1.3), which is good enough for
sizing and avoids a tokenizer dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

TARGET_MAX_TOKENS = 450
OVERLAP_TOKENS = 50
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


class Chunk(BaseModel):
    chunk_id: str  # citation id: "<doc_id>#<heading-slug>[-n]"
    doc_id: str
    title: str
    heading_path: list[str]
    text: str


def approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3) + 1


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    m = _FRONT_MATTER.match(raw.replace("\r\n", "\n"))
    if not m:
        raise ValueError("knowledge document is missing its front matter block")
    meta = yaml.safe_load(m.group(1)) or {}
    for key in ("doc_id", "title", "owner", "last_reviewed"):
        if key not in meta:
            raise ValueError(f"front matter missing '{key}'")
    body = raw.replace("\r\n", "\n")[m.end() :]
    return {k: str(v) for k, v in meta.items()}, body


def _split_long(text: str) -> list[str]:
    """Split on blank-line paragraphs into <= TARGET_MAX_TOKENS pieces with a small overlap."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for para in paragraphs:
        t = approx_tokens(para)
        if current and size + t > TARGET_MAX_TOKENS:
            piece = "\n\n".join(current)
            pieces.append(piece)
            # carry the last ~OVERLAP_TOKENS tokens (as words) into the next piece
            words = piece.split()
            tail = " ".join(words[-int(OVERLAP_TOKENS / 1.3) :])
            current = [tail]
            size = approx_tokens(tail)
        current.append(para)
        size += t
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_markdown(raw: str) -> list[Chunk]:
    meta, body = parse_front_matter(raw)
    doc_id, title = meta["doc_id"], meta["title"]

    sections: list[tuple[list[str], list[str]]] = []  # (heading path, lines)
    path: list[str] = []
    lines: list[str] = []
    in_code = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            if any(s.strip() for s in lines):
                sections.append((list(path), lines))
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2)]
            lines = []
        else:
            lines.append(line)
    if any(s.strip() for s in lines):
        sections.append((list(path), lines))

    chunks: list[Chunk] = []
    used: dict[str, int] = {}
    for heading_path, sec_lines in sections:
        text = "\n".join(sec_lines).strip()
        pieces = _split_long(text) if approx_tokens(text) > TARGET_MAX_TOKENS else [text]
        for piece in pieces:
            slug = slugify(heading_path[-1]) if heading_path else "intro"
            n = used.get(slug, 0)
            used[slug] = n + 1
            chunk_id = f"{doc_id}#{slug}" + (f"-{n + 1}" if n else "")
            context = " > ".join(heading_path) if heading_path else title
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    title=title,
                    heading_path=heading_path,
                    text=f"{title} | {context}\n{piece}",
                )
            )
    return chunks


def chunk_directory(directory: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8")))
    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids across knowledge documents")
    return chunks
