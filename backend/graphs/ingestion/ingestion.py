"""Fetch an SEC inline-XBRL filing and ingest RAG chunks into Qdrant.

Input is one filing record, e.g.
    {"ticker": "AAPL", "company": "Apple Inc", "form_type": "10-K", "url": "https://...htm"}

Pipeline (LangGraph): fetch -> normalize -> split -> store, traced in Langfuse as one
trace per ingestion run with a span per step. The store node can be skipped (store=False).
Splitting is done by LangChain's HTMLSemanticPreservingSplitter; normalize only
turns the filing's bold-span "headings" into real <h1>-<h4> tags so it can work.
"""

import re
import uuid
import warnings
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import TypedDict, cast

import httpx
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from langchain_core.documents import Document
from langchain_text_splitters import HTMLSemanticPreservingSplitter
from langfuse.decorators import langfuse_context, observe
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from qdrant_client import QdrantClient, models

from shared.config import get_settings
from shared.llms import get_embedding_model
from shared.models import IngestionRequest

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

USER_AGENT = "Practice RAG research bd738ft@example.com"  # SEC requires a contact UA
EMBED_BATCH_SIZE = 16  # 16 x ~800 tokens must fit the embedding deployment's tokens-per-minute quota
ProgressCallback = Callable[[int], None]

MAX_CHUNK_SIZE = 3200  # ~800 tokens
CHUNK_OVERLAP = 300
MIN_CHARS = (
    150  # fragments below this are merged into the previous chunk of the same section
)

PART_RE = re.compile(r"^PART\s+(I{1,3}|IV)\b\.?", re.IGNORECASE)
ITEM_RE = re.compile(r"^Item\s+\d+[A-C]?\.", re.IGNORECASE)
NOTE_RE = re.compile(r"^Note\s+\d+\b", re.IGNORECASE)
FOOTER_RE = re.compile(
    r"^.{0,60}\|\s*(Q\d\s+)?\d{4}\s+Form\s+10-[QK]\s*\|\s*\d+$", re.IGNORECASE
)
RUNNING_PART_RE = re.compile(r"^PART\s+(I{1,3}|IV)$", re.IGNORECASE)
RUNNING_ITEM_RE = re.compile(r"^Item\s+\d+[A-C]?$", re.IGNORECASE)
BOLD_RE = re.compile(r"font-weight:\s*(700|bold)", re.IGNORECASE)
SKIP_SECTIONS = re.compile(r"signature|exhibit index", re.IGNORECASE)

HEADERS = [("h1", "part"), ("h2", "item"), ("h3", "section"), ("h4", "subsection")]


class State(TypedDict, total=False):
    filing: IngestionRequest
    html: str
    meta: dict[str, str]
    clean_html: str
    chunks: list[dict[str, str | int]]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def is_bold(tag: Tag) -> bool:
    spans = [s for s in tag.find_all("span") if clean(s.get_text())]
    return bool(spans) and all(BOLD_RE.search(s.get("style") or "") for s in spans)


def blocks(el: Tag) -> Iterator[Tag]:
    """Yield leaf blocks, descending into page-container divs (one div per page, p children)."""
    for c in el.find_all(True, recursive=False):
        if (
            c.name in ("div", "ix:continuation")
            and c.find(["p", "div"], recursive=False)
            and not c.find("table", recursive=False)
        ):
            yield from blocks(c)
        else:
            yield c


def table_handler(table: Tag) -> str:
    """Render a table as markdown rows, gluing split '$' / ')' / '%' cells to their numbers."""
    rows = []
    for tr in table.find_all("tr"):
        cells: list[str] = []
        for td in tr.find_all(["td", "th"]):
            t = clean(td.get_text(" "))
            if not t:
                continue
            if (
                cells
                and t in {")", "%", ")%"}
                or cells
                and cells[-1] in {"$", "(", "$("}
            ):
                cells[-1] += t
            else:
                cells.append(t)
        if cells:
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def parse_period_end(raw: str, file: str) -> str:
    for fmt in ("%B %d, %Y", "%B %d %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.replace(" ,", ","), fmt).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"-(\d{8})\.htm", file)  # EDGAR file names end in the period end date
    return datetime.strptime(m.group(1), "%Y%m%d").date().isoformat() if m else raw


@observe(capture_input=False)
def extract_meta(soup: BeautifulSoup, filing: IngestionRequest) -> dict:
    facts: dict[str, str] = {}
    for el in soup.find_all("ix:nonnumeric"):
        facts.setdefault(el.get("name", ""), clean(el.get_text()))
    file = filing.url.rstrip("/").rsplit("/", 1)[-1]
    fiscal_year = facts.get("dei:DocumentFiscalYearFocus", "")
    fiscal_period = facts.get("dei:DocumentFiscalPeriodFocus", "")
    return {
        "ticker": filing.ticker or facts.get("dei:TradingSymbol", ""),
        "company": (filing.company or facts.get("dei:EntityRegistrantName", ""))
        .rstrip(".")
        .lower(),
        "form_type": filing.form_type or facts.get("dei:DocumentType", ""),
        "period_end": parse_period_end(
            facts.get("dei:DocumentPeriodEndDate", ""), file
        ),
        "file": file,
        # Only used for the chunk context header, not stored as chunk fields.
        "display_company": filing.company or facts.get("dei:EntityRegistrantName", ""),
        "fiscal_label": (
            f"fiscal year {fiscal_year}"
            if fiscal_period in ("", "FY")
            else f"{fiscal_period} fiscal year {fiscal_year}"
        ),
    }


@observe(capture_input=False, capture_output=False)
def fetch(state: State) -> State:
    url = state["filing"].url
    r = httpx.get(
        url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60
    )
    r.raise_for_status()
    langfuse_context.update_current_observation(
        input={"url": url}, output={"status": r.status_code, "html_chars": len(r.text)}
    )
    return {"html": r.text}


@observe(capture_input=False, capture_output=False)
def normalize(state: State) -> State:
    soup = BeautifulSoup(state["html"], "lxml")
    meta = extract_meta(soup, state["filing"])
    for t in soup(["script", "style", "ix:header"]):
        t.decompose()
    for t in soup.find_all(style=re.compile(r"display:\s*none")):
        t.decompose()
    for t in soup.find_all(re.compile(r"^ix:")):
        t.unwrap()  # inline XBRL wrappers make the splitter emit their text out of order

    in_body = (
        False  # drop cover page + table of contents before the first real Part heading
    )
    in_note = False
    path: list[
        str
    ] = []  # heading text carries its full ancestry; the splitter drops empty parents
    assert soup.body is not None  # lxml always creates a body element
    for el in list(blocks(soup.body)):
        text = clean(el.get_text(" "))
        has_table = el.find("table") is not None
        if (
            not text
            or FOOTER_RE.match(text)
            or re.fullmatch(r"\d+|table of contents", text, re.IGNORECASE)
        ):
            el.decompose()
            continue
        heading = None
        bold = not has_table and is_bold(el)
        if not bold and RUNNING_ITEM_RE.match(text):
            el.decompose()  # page running header
            continue
        if not bold and RUNNING_PART_RE.match(text):
            if path and path[0].upper() == text.upper():
                el.decompose()  # page running header for the current part
                continue
            bold = True  # first running header of a new part acts as the Part heading
        if bold:
            if PART_RE.match(text) and len(text) < 40:
                heading, in_body, in_note = "h1", True, False
            elif ITEM_RE.match(text) and len(text) < 120:
                heading, in_note = "h2", False
            elif len(text) <= 150 and not text.endswith("."):
                if NOTE_RE.match(text):
                    heading, in_note = "h3", True
                else:
                    heading = "h4" if in_note else "h3"
        elif (
            not has_table
            and text.isupper()
            and len(text) <= 80
            and not text.endswith(".")
        ):
            heading = "h4" if in_note else "h3"
        if not in_body:
            el.decompose()
        elif heading:
            level = int(heading[1])
            del path[level - 1 :]
            # spans can split words mid-way, so join heading text without separators
            title = clean(el.get_text()) or text
            title = re.sub(
                r"^(Item\s+\d+[A-C]?\.)(?=\S)", r"\1 ", title, flags=re.IGNORECASE
            )
            path.append(title.rstrip(".") if level == 1 else title)
            h = soup.new_tag(heading)
            h.string = " > ".join(path)
            el.replace_with(h)
    clean_html = str(soup.body)
    langfuse_context.update_current_observation(
        input={"html_chars": len(state["html"])},
        output={"meta": meta, "clean_html_chars": len(clean_html)},
    )
    return {"meta": meta, "clean_html": clean_html}


@observe(capture_input=False, capture_output=False)
def split(state: State) -> State:
    meta = state["meta"]
    splitter = HTMLSemanticPreservingSplitter(
        headers_to_split_on=HEADERS,
        max_chunk_size=MAX_CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", "; ", ", ", " "],
        elements_to_preserve=["table"],
        custom_handlers={"table": table_handler},
        denylist_tags=["script", "style", "head"],
        preserve_parent_metadata=True,
    )
    raw = splitter.split_text(state["clean_html"])

    # Merge tiny fragments into the previous chunk when they share the same section.
    merged: list[Document] = []
    for d in raw:
        text = d.page_content.strip()
        if not text:
            continue
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and len(text) < MIN_CHARS
            and prev.metadata == d.metadata
            and len(prev.page_content) + len(text) <= MAX_CHUNK_SIZE
        ):
            prev.page_content += "\n\n" + text
        else:
            merged.append(Document(page_content=text, metadata=dict(d.metadata)))

    # Remaining tiny fragments (e.g. a lead-in line before the first sub-heading) are prepended
    # to the next chunk of the same Item.
    for i in range(len(merged) - 2, -1, -1):
        d, nxt = merged[i], merged[i + 1]
        if (
            len(d.page_content) < MIN_CHARS
            and d.metadata.get("item") == nxt.metadata.get("item")
            and len(d.page_content) + len(nxt.page_content) <= MAX_CHUNK_SIZE
        ):
            nxt.page_content = d.page_content + "\n\n" + nxt.page_content
            del merged[i]

    chunks: list[dict[str, str | int]] = []
    for d in merged:
        section_path = next(
            (d.metadata[k] for _, k in reversed(HEADERS) if d.metadata.get(k)), ""
        )
        if any(SKIP_SECTIONS.search(p) for p in section_path.split(" > ")):
            continue
        body = d.page_content
        if len(body) < 40 and not re.search(
            r"none|not applicable", body, re.IGNORECASE
        ):
            continue  # heading-only stub
        header = " | ".join(
            p
            for p in [
                f"{meta['display_company']} ({meta['ticker']})",
                f"Form {meta['form_type']}",
                f"{meta['fiscal_label']}, period ended {meta['period_end']}",
                section_path,
            ]
            if p
        )
        chunks.append(
            {
                "ticker": meta["ticker"],
                "company": meta["company"],
                "form_type": meta["form_type"],
                "period_end": meta["period_end"],
                "file": meta["file"],
                "chunk_num": len(chunks),
                "data": f"{header}\n\n{body}",
            }
        )
    langfuse_context.update_current_observation(
        input={"clean_html_chars": len(state["clean_html"])},
        output={"raw_splits": len(raw), "merged": len(merged), "chunks": len(chunks)},
    )
    return {"chunks": chunks}


@observe(capture_input=False, capture_output=False)
def store(state: State, progress_callback: ProgressCallback | None = None) -> State:
    cfg = get_settings()
    chunks = state["chunks"]
    embeddings = get_embedding_model()
    client = QdrantClient(
        url=cfg.qdrant_url,
        # Without port=None the client appends :6333 to portless (hosted https) URLs.
        port=None,
        timeout=cfg.qdrant_timeout,
        api_key=cfg.qdrant_api_key.get_secret_value() if cfg.qdrant_api_key else None,
    )
    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i : i + EMBED_BATCH_SIZE]
        vectors = embeddings.embed_documents([str(c["data"]) for c in batch])
        if i == 0 and not client.collection_exists(cfg.qdrant_collection):
            client.create_collection(
                cfg.qdrant_collection,
                vectors_config=models.VectorParams(
                    size=len(vectors[0]), distance=models.Distance.COSINE
                ),
            )
        client.upsert(
            cfg.qdrant_collection,
            points=[
                models.PointStruct(
                    # deterministic id: re-ingesting a filing overwrites its chunks
                    id=str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"{c['file']}#{c['chunk_num']}")
                    ),
                    vector=v,
                    payload=c,
                )
                for c, v in zip(batch, vectors, strict=False)
            ],
        )
        if progress_callback is not None:
            processed = min(i + len(batch), len(chunks))
            progress_callback(50 + 45 * processed // max(len(chunks), 1))
    if not chunks and progress_callback is not None:
        progress_callback(95)
    langfuse_context.update_current_observation(
        input={"chunks": len(chunks)}, output={"collection": cfg.qdrant_collection}
    )
    return {}


def build_graph(
    store_chunks: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> CompiledStateGraph[State, None, State, State]:
    g = StateGraph(State)

    def fetch_step(state: State) -> State:
        result = cast(State, fetch(state))
        if progress_callback is not None:
            progress_callback(15)
        return result

    def normalize_step(state: State) -> State:
        result = cast(State, normalize(state))
        if progress_callback is not None:
            progress_callback(35)
        return result

    def split_step(state: State) -> State:
        result = cast(State, split(state))
        if progress_callback is not None:
            progress_callback(50)
        return result

    def store_step(state: State) -> State:
        return cast(State, store(state, progress_callback))

    g.add_node("fetch", fetch_step)
    g.add_node("normalize", normalize_step)
    g.add_node("split", split_step)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "normalize")
    g.add_edge("normalize", "split")
    if store_chunks:
        g.add_node("store", store_step)
        g.add_edge("split", "store")
        g.add_edge("store", END)
    else:
        g.add_edge("split", END)
    return g.compile()


@observe(name="ingestion", capture_output=False)
def ingest(
    filing: IngestionRequest,
    store_chunks: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, str | int]]:
    """Run one ingestion for a single filing record; returns the chunks.

    Set store_chunks=False to skip the Qdrant node (e.g. in experiments).
    """
    file = filing.url.rstrip("/").rsplit("/", 1)[-1]
    langfuse_context.update_current_trace(
        name="ingestion",
        input=filing.model_dump(),
        tags=["ingestion", filing.ticker],
        metadata={"file": file},
    )
    if progress_callback is not None:
        progress_callback(5)
    result = build_graph(store_chunks, progress_callback).invoke({"filing": filing})
    chunks: list[dict[str, str | int]] = result["chunks"]
    langfuse_context.update_current_observation(
        output={
            "chunks": len(chunks),
            "first_chunk": chunks[0] if chunks else None,
        }
    )
    return chunks
