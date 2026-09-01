"""
Unit tests for Container Packaging, Dockerfile Integrity, and Fail-Closed Embedding Verification.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agent_core.app_utils.system_config import (
    load_system_config,
    SystemConfigurationError,
)
from agent_core.app_utils.embedding_utils import (
    generate_text_embedding,
    generate_batch_embeddings,
    EmbeddingGenerationError,
)


def test_dockerfile_copies_config_and_code():
    """Verifies that Dockerfile explicitly packages config/ and code directories."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    dockerfile_path = repo_root / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile must exist at repository root"

    dockerfile_content = dockerfile_path.read_text(encoding="utf-8")
    assert "COPY ./config ./config" in dockerfile_content, "Dockerfile must copy ./config directory to prevent container startup crash"
    assert "COPY ./agent_core ./agent_core" in dockerfile_content


def test_missing_config_fails_closed_in_simulated_container(tmp_path, monkeypatch):
    """Simulates container environment where config/ is missing and verifies fail-closed exception."""
    fake_code_dir = tmp_path / "code"
    fake_code_dir.mkdir()
    
    # Point SYSTEMS_CONFIG_PATH to missing file inside fake container
    missing_config = fake_code_dir / "config" / "systems.yaml"
    monkeypatch.setenv("SYSTEMS_CONFIG_PATH", str(missing_config))

    with pytest.raises(SystemConfigurationError, match="không tồn tại"):
        load_system_config(force_reload=True)


def test_custom_systems_config_path_respected(tmp_path, monkeypatch):
    """Verifies that custom SYSTEMS_CONFIG_PATH is properly respected."""
    custom_config = tmp_path / "custom_systems.yaml"
    custom_config.write_text(
        "systems:\n"
        "  MES:\n"
        "    name: 'Manufacturing Execution'\n"
        "    category: 'Production'\n"
        "    admin_roles: ['ROLE_MES_ADMIN']\n",
        encoding="utf-8"
    )
    monkeypatch.setenv("SYSTEMS_CONFIG_PATH", str(custom_config))

    config = load_system_config(force_reload=True)
    assert "MES" in config["systems"]
    assert "ERP" not in config["systems"]


def test_generate_text_embedding_fails_closed_in_production(monkeypatch):
    """Verifies that Vertex AI embedding failure in production mode raises EmbeddingGenerationError instead of silent fallback."""
    monkeypatch.setenv("USE_VERTEX_EMBEDDING", "true")

    # Mock vertexai to raise an API error
    mock_model_class = MagicMock()
    mock_model_class.from_pretrained.side_effect = RuntimeError("Vertex AI Quota Exceeded / Auth Failure")

    with patch("vertexai.language_models.TextEmbeddingModel", mock_model_class):
        with pytest.raises(EmbeddingGenerationError, match="Vertex AI embedding failed"):
            generate_text_embedding("Truy vấn tìm kiếm hướng dẫn kỹ thuật", use_vertex=True)


def test_generate_batch_embeddings_fails_closed_in_production(monkeypatch):
    """Verifies that batch embedding in production fails closed on Vertex AI errors."""
    monkeypatch.setenv("USE_VERTEX_EMBEDDING", "true")

    mock_model_class = MagicMock()
    mock_model_class.from_pretrained.side_effect = RuntimeError("Vertex AI Connection Timeout")

    with patch("vertexai.language_models.TextEmbeddingModel", mock_model_class):
        with pytest.raises(EmbeddingGenerationError, match="Batch Vertex AI embedding failed"):
            generate_batch_embeddings(["Bài viết 1", "Bài viết 2"], use_vertex=True)


def test_generate_text_embedding_offline_simulation_returns_pseudo_vector():
    """Verifies that offline dry-run (use_vertex=False) produces consistent 768-dim normalized pseudo-vectors."""
    vec = generate_text_embedding("Kiểm tra offline vector", use_vertex=False)
    assert len(vec) == 768
    assert any(x != 0.0 for x in vec)
    # Check norm is ~1.0
    norm = sum(x * x for x in vec)
    assert abs(norm - 1.0) < 1e-4
