"""Prompt context: all page text if it fits the budget, else BM25-ranked chunks."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from rank_bm25 import BM25Okapi

from .fetch import Page

CHUNK_CHARS = 2000
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_WORD = re.compile(r"\w+")


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


@dataclass(frozen=True)
class Chunk:
    url: str
    heading: str
    text: str


@dataclass(frozen=True)
class Context:
    text: str
    sources: tuple[str, ...]
    mode: Literal["full", "retrieved"]


def _sections(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading, buf = "", []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append((heading, text))

    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            heading, buf = match.group(1).strip(), [line]
        else:
            buf.append(line)
    flush()
    return sections


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        while len(para) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(para[:max_chars])
            para = para[max_chars:]
        if current and len(current) + 2 + len(para) > max_chars:
            pieces.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        pieces.append(current)
    return pieces


def chunk_page(page: Page, max_chars: int = CHUNK_CHARS) -> list[Chunk]:
    chunks = []
    for heading, text in _sections(page.markdown):
        for piece in _split_long(text, max_chars):
            if _HEADING.fullmatch(piece.strip()):
                continue  # a lone heading carries no content; it survives in Chunk.heading
            chunks.append(Chunk(page.url, heading, piece))
    return chunks


def _clean(text: str) -> str:
    return text.replace("</page>", "<\\/page>").replace("</excerpt>", "<\\/excerpt>")


def _quote(text: str) -> str:
    return text.replace('"', "'")


def _page_block(page: Page) -> str:
    return f'<page url="{_quote(page.url)}" title="{_quote(page.title)}">\n{_clean(page.markdown)}\n</page>'


def _excerpt_block(chunk: Chunk) -> str:
    return f'<excerpt url="{_quote(chunk.url)}" section="{_quote(chunk.heading)}">\n{_clean(chunk.text)}\n</excerpt>'


def _tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def build(pages: Sequence[Page], question: str, token_budget: int) -> Context:
    pages = [p for p in pages if p.markdown.strip()]
    if not pages:
        return Context("", (), "full")
    if sum(estimate_tokens(p.markdown) for p in pages) <= token_budget:
        return Context("\n\n".join(_page_block(p) for p in pages), tuple(p.url for p in pages), "full")
    return _retrieve(pages, question, token_budget)


def _retrieve(pages: Sequence[Page], question: str, token_budget: int) -> Context:
    chunks = [c for page in pages for c in chunk_page(page)]
    tokens = [_tokenize(f"{c.heading} {c.text}") for c in chunks]
    keep = [i for i, toks in enumerate(tokens) if toks]  # BM25 divides by average length: no empty docs
    if not keep:
        return Context("", (), "retrieved")
    chunks = [chunks[i] for i in keep]
    tokens = [tokens[i] for i in keep]
    query = _tokenize(question)
    scores = BM25Okapi(tokens).get_scores(query) if query else [0.0] * len(chunks)
    order = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))

    picked: list[int] = []
    used = 0
    for i in order:
        cost = estimate_tokens(_excerpt_block(chunks[i]))
        if used + cost <= token_budget:
            picked.append(i)
            used += cost
    if not picked:
        best = order[0]
        overhead = estimate_tokens(_excerpt_block(replace(chunks[best], text="")))
        limit = max(80, (token_budget - overhead) * 4)
        chunks[best] = replace(chunks[best], text=chunks[best].text[:limit])
        picked = [best]

    picked.sort()  # back to document order
    text = "\n\n".join(_excerpt_block(chunks[i]) for i in picked)
    sources = tuple(dict.fromkeys(chunks[i].url for i in picked))
    return Context(text, sources, "retrieved")
