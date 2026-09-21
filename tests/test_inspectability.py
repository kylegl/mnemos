from __future__ import annotations

from pathlib import Path

import pytest

from mnemos.config import MnemosConfig, SleepConfig, SpreadingConfig, SurprisalConfig
from mnemos.engine import MnemosEngine
from mnemos.inspectability import build_chunk_inspection
from mnemos.modules.mutable_rag import MutableRAG
from mnemos.types import Interaction, MemoryChunk
from mnemos.utils.embeddings import SimpleEmbeddingProvider
from mnemos.utils.llm import MockLLMProvider
from mnemos.utils.storage import InMemoryStore, SQLiteStore


@pytest.fixture
def engine() -> MnemosEngine:
    return MnemosEngine(
        config=MnemosConfig(
            surprisal=SurprisalConfig(threshold=0.0, min_content_length=0),
            sleep=SleepConfig(
                min_episodes_before_consolidation=3,
                consolidation_interval_seconds=0,
            ),
        ),
        llm=MockLLMProvider(),
        embedder=SimpleEmbeddingProvider(dim=64),
        store=InMemoryStore(),
    )


@pytest.mark.asyncio
async def test_build_chunk_inspection_reports_scope_provenance_and_graph_context(
    engine: MnemosEngine,
) -> None:
    first = await engine.process(
        Interaction(role="user", content="We use FastAPI for internal APIs."),
        scope="project",
        scope_id="repo-alpha",
    )
    second = await engine.process(
        Interaction(role="user", content="FastAPI services deploy with Docker."),
        scope="project",
        scope_id="repo-alpha",
    )

    assert first.chunk is not None
    assert second.chunk is not None

    engine.spreading_activation.add_edge(first.chunk.id, second.chunk.id, 0.91)

    payload = build_chunk_inspection(engine, first.chunk.id)

    assert payload is not None
    assert payload["scope"] == "project"
    assert payload["scope_id"] == "repo-alpha"
    assert payload["provenance"]["stored_by"] == "surprisal_gate"
    assert payload["provenance"]["ingest_channel"] == "manual"
    assert "High surprisal" in payload["provenance"]["encoding_reason"]
    assert payload["graph"]["neighbor_count"] == 1
    assert payload["graph"]["present"] is True


def test_build_chunk_inspection_reports_empty_graph_when_node_is_hydrated(
    tmp_path: Path,
) -> None:
    embedder = SimpleEmbeddingProvider(dim=64)
    store = SQLiteStore(str(tmp_path / "memory.db"))
    chunk = MemoryChunk(
        id="chunk-empty",
        content="A single hydrated chunk has no graph neighbors yet.",
        embedding=embedder.embed("A single hydrated chunk has no graph neighbors yet."),
    )
    store.store(chunk)

    engine = MnemosEngine(
        config=MnemosConfig(
            surprisal=SurprisalConfig(threshold=0.0, min_content_length=0),
            spreading=SpreadingConfig(startup_hydration_limit=1, startup_auto_connect=False),
            sleep=SleepConfig(
                min_episodes_before_consolidation=3,
                consolidation_interval_seconds=0,
            ),
        ),
        llm=MockLLMProvider(),
        embedder=embedder,
        store=store,
    )

    try:
        payload = build_chunk_inspection(engine, chunk.id)
    finally:
        engine.store.close()

    assert payload is not None
    assert payload["graph"]["present"] is True
    assert payload["graph"]["neighbor_count"] == 0


def test_build_chunk_inspection_reports_graph_unavailable_when_chunk_is_not_hydrated(
    tmp_path: Path,
) -> None:
    embedder = SimpleEmbeddingProvider(dim=64)
    store = SQLiteStore(str(tmp_path / "memory.db"))
    first = MemoryChunk(
        id="chunk-loaded",
        content="Hydrated chunk stays in the startup snapshot.",
        embedding=embedder.embed("Hydrated chunk stays in the startup snapshot."),
    )
    second = MemoryChunk(
        id="chunk-hidden",
        content="This chunk exists in storage but falls outside the hydration limit.",
        embedding=embedder.embed(
            "This chunk exists in storage but falls outside the hydration limit."
        ),
    )
    store.store(first)
    store.store(second)

    engine = MnemosEngine(
        config=MnemosConfig(
            surprisal=SurprisalConfig(threshold=0.0, min_content_length=0),
            spreading=SpreadingConfig(startup_hydration_limit=1, startup_auto_connect=False),
            sleep=SleepConfig(
                min_episodes_before_consolidation=3,
                consolidation_interval_seconds=0,
            ),
        ),
        llm=MockLLMProvider(),
        embedder=embedder,
        store=store,
    )

    try:
        payload = build_chunk_inspection(engine, second.id)
    finally:
        engine.store.close()

    assert payload is not None
    assert payload["graph"]["present"] is False
    assert payload["graph"]["neighbor_count"] == 0


@pytest.mark.asyncio
async def test_build_chunk_inspection_can_explain_retrieval_relevance(
    engine: MnemosEngine,
) -> None:
    first = await engine.process(
        Interaction(role="user", content="Use uv and Ruff for Python tooling in this repo."),
        scope="project",
        scope_id="repo-alpha",
    )
    second = await engine.process(
        Interaction(role="user", content="Deployment uses GitHub Actions and Docker."),
        scope="project",
        scope_id="repo-alpha",
    )

    assert first.chunk is not None
    assert second.chunk is not None

    engine.spreading_activation.add_edge(first.chunk.id, second.chunk.id, 0.88)

    payload = build_chunk_inspection(
        engine,
        first.chunk.id,
        query="python tooling",
        current_scope="project",
        scope_id="repo-alpha",
        allowed_scopes=("project", "global"),
    )

    assert payload is not None
    assert payload["retrieval"] is not None
    assert payload["retrieval"]["scope_match"] is True
    assert payload["retrieval"]["in_semantic_candidates"] is True
    assert payload["retrieval"]["semantic_rank"] is not None
    assert payload["retrieval"]["explanation"]


@pytest.mark.asyncio
async def test_reconsolidation_appends_revision_history() -> None:
    embedder = SimpleEmbeddingProvider(dim=64)
    store = InMemoryStore()
    rag = MutableRAG(
        llm=MockLLMProvider(
            responses={"has this stored memory": "CHANGED: User migrated from React to Svelte"}
        ),
        embedder=embedder,
        store=store,
    )

    chunk = MemoryChunk(
        content="User uses React",
        embedding=embedder.embed("User uses React"),
    )
    store.store(chunk)

    updated_chunk, changed = await rag.reconsolidate(
        chunk,
        "User mentioned migrating to Svelte this week",
    )

    assert changed is True
    history = updated_chunk.metadata["revision_history"]
    assert history[0]["from_version"] == 1
    assert history[0]["to_version"] == 2
    assert history[0]["previous_content"] == "User uses React"
    assert history[0]["new_content"] == "User migrated from React to Svelte"
    assert "Svelte" in history[0]["context"]
