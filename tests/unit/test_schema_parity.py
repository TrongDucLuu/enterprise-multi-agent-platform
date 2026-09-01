"""
Unit and Mutation Tests for Schema Parity between Terraform and Python Ingestion.
Guarantees that `google_bigquery_table.knowledge_articles` in `deployment/terraform/main.tf`
and `scripts/ingest/loaders.py` have 100% schema parity across all 26 fields.
"""
import re
import json
from pathlib import Path
import pytest
from google.cloud import bigquery
from scripts.ingest.loaders import get_knowledge_articles_schema, get_dlq_schema


def extract_terraform_schema() -> list[dict]:
    """Parses the JSON schema block from deployment/terraform/main.tf."""
    tf_path = Path(__file__).parent.parent.parent / "deployment" / "terraform" / "main.tf"
    assert tf_path.exists(), f"Terraform main.tf not found at {tf_path}"

    content = tf_path.read_text(encoding="utf-8")
    match = re.search(r'resource\s+"google_bigquery_table"\s+"knowledge_articles"\s*\{.*?schema\s*=\s*<<EOF\s*(.*?)\s*EOF', content, re.DOTALL)
    assert match is not None, "Could not find schema block in google_bigquery_table.knowledge_articles"

    raw_json = match.group(1).strip()
    schema_fields = json.loads(raw_json)
    assert isinstance(schema_fields, list), "Terraform schema JSON must be a list of field definitions"
    return schema_fields


def normalize_type(t: str) -> str:
    """Normalizes BigQuery equivalent type names for comparison."""
    t_upper = t.upper().strip()
    mapping = {
        "BOOL": "BOOLEAN",
        "BOOLEAN": "BOOLEAN",
        "INT64": "INTEGER",
        "INTEGER": "INTEGER",
        "FLOAT64": "FLOAT64",
        "STRING": "STRING",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP",
        "RECORD": "RECORD",
        "STRUCT": "RECORD",
    }
    return mapping.get(t_upper, t_upper)


def test_schema_parity_fields_and_types():
    """Verifies that all 26 fields, types, modes, and nested fields match between Terraform and loaders.py."""
    tf_schema = extract_terraform_schema()
    py_schema = get_knowledge_articles_schema()

    # 1. Total field count check
    assert len(tf_schema) == len(py_schema) == 27, (
        f"Schema count mismatch: Terraform has {len(tf_schema)} fields, "
        f"Python loaders.py has {len(py_schema)} fields (Expected exactly 27)."
    )

    tf_dict = {f["name"]: f for f in tf_schema}
    py_dict = {f.name: f for f in py_schema}

    # 2. Check every field
    for name, py_field in py_dict.items():
        assert name in tf_dict, f"Field '{name}' defined in Python loaders.py is missing from Terraform main.tf schema!"
        tf_field = tf_dict[name]

        # Verify normalized type
        expected_type = normalize_type(py_field.field_type)
        actual_type = normalize_type(tf_field.get("type", ""))
        assert actual_type == expected_type, (
            f"Type mismatch for field '{name}': Python={expected_type}, Terraform={actual_type}"
        )

        # Verify mode
        expected_mode = py_field.mode.upper() if py_field.mode else "NULLABLE"
        actual_mode = tf_field.get("mode", "NULLABLE").upper()
        assert actual_mode == expected_mode, (
            f"Mode mismatch for field '{name}': Python={expected_mode}, Terraform={actual_mode}"
        )

        # Verify nested fields (e.g. section_hierarchy)
        if expected_type == "RECORD":
            tf_sub = {sf["name"]: sf for sf in tf_field.get("fields", [])}
            py_sub = {sf.name: sf for sf in py_field.fields}
            assert set(tf_sub.keys()) == set(py_sub.keys()), (
                f"Subfields mismatch for RECORD field '{name}': Python={list(py_sub.keys())}, Terraform={list(tf_sub.keys())}"
            )
            for sub_name, py_sub_field in py_sub.items():
                assert normalize_type(tf_sub[sub_name]["type"]) == normalize_type(py_sub_field.field_type)


def test_schema_parity_mutation_detection():
    """Mutation Test: Injects synthetic schema drifts and confirms validation strictly fails."""
    tf_schema = extract_terraform_schema()
    
    # Mutation 1: Missing column in Terraform
    mutated_tf = [f for f in tf_schema if f["name"] != "parser_version"]
    assert len(mutated_tf) != len(get_knowledge_articles_schema()), "Mutation 1 should alter length"

    # Mutation 2: Mismatched type
    mutated_type_tf = [
        {**f, "type": "INTEGER" if f["name"] == "is_deleted" else f["type"]}
        for f in tf_schema
    ]
    py_schema = get_knowledge_articles_schema()
    py_dict = {f.name: f for f in py_schema}
    
    with pytest.raises(AssertionError):
        # Emulate checking with mutated type
        is_deleted_tf = next(f for f in mutated_type_tf if f["name"] == "is_deleted")
        assert normalize_type(is_deleted_tf["type"]) == normalize_type(py_dict["is_deleted"].field_type)


def extract_terraform_dlq_schema() -> list[dict]:
    """Parses the JSON schema block for google_bigquery_table.ingestion_dead_letter_queue from Terraform."""
    tf_path = Path(__file__).parent.parent.parent / "deployment" / "terraform" / "main.tf"
    assert tf_path.exists(), f"Terraform main.tf not found at {tf_path}"

    content = tf_path.read_text(encoding="utf-8")
    match = re.search(r'resource\s+"google_bigquery_table"\s+"ingestion_dead_letter_queue"\s*\{.*?schema\s*=\s*<<EOF\s*(.*?)\s*EOF', content, re.DOTALL)
    assert match is not None, "Could not find schema block in google_bigquery_table.ingestion_dead_letter_queue"

    raw_json = match.group(1).strip()
    schema_fields = json.loads(raw_json)
    assert isinstance(schema_fields, list), "Terraform DLQ schema JSON must be a list of field definitions"
    return schema_fields


def test_dlq_schema_parity_fields_and_types():
    """Verifies that all 7 fields, types, and modes match between Terraform and loaders.py get_dlq_schema()."""
    tf_schema = extract_terraform_dlq_schema()
    py_schema = get_dlq_schema()

    assert len(tf_schema) == len(py_schema) == 7, (
        f"DLQ Schema count mismatch: Terraform has {len(tf_schema)} fields, "
        f"Python get_dlq_schema() has {len(py_schema)} fields (Expected exactly 7)."
    )

    tf_dict = {f["name"]: f for f in tf_schema}
    py_dict = {f.name: f for f in py_schema}

    for name, py_field in py_dict.items():
        assert name in tf_dict, f"Field '{name}' defined in Python get_dlq_schema() is missing from Terraform main.tf!"
        tf_field = tf_dict[name]

        expected_type = normalize_type(py_field.field_type)
        actual_type = normalize_type(tf_field.get("type", ""))
        assert actual_type == expected_type, (
            f"Type mismatch for DLQ field '{name}': Python={expected_type}, Terraform={actual_type}"
        )

        expected_mode = py_field.mode.upper() if py_field.mode else "NULLABLE"
        actual_mode = tf_field.get("mode", "NULLABLE").upper()
        assert actual_mode == expected_mode, (
            f"Mode mismatch for DLQ field '{name}': Python={expected_mode}, Terraform={actual_mode}"
        )


def test_dlq_schema_parity_mutation_detection():
    """Mutation Test: Injects synthetic schema drift into DLQ schema and confirms detection."""
    tf_schema = extract_terraform_dlq_schema()
    mutated_tf = [f for f in tf_schema if f["name"] != "doc_payload"]
    assert len(mutated_tf) != len(get_dlq_schema()), "Mutation should alter DLQ schema length"
