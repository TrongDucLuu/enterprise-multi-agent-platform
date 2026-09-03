#!/usr/bin/env python3
"""
Synthetic Enterprise Knowledge Base Generator
Generates realistic enterprise IT / Security / Operations knowledge chunks with:
- Multi-dimensional RBAC roles, sensitivity tags, clearance levels (0 to 3)
- Soft deletion tombstones (is_deleted=True, deleted_at)
- Time-based lifecycle validity (effective_date, expiry_date)
- 768-dimensional normalized embeddings (matching text-embedding-005)
- Document metadata, provenance URIs, and parser/chunker versions
"""

import argparse
import datetime
import json
import math
import os
import random
import sys
from typing import Any, Dict, List


CATEGORIES = [
    "network_vpn", "identity_sso", "security_compliance", "endpoint_hardware",
    "cloud_infrastructure", "developer_tooling", "collaboration_saas", "incident_response"
]

ROLES_DISTRIBUTION = [
    ["employee"],
    ["employee", "contractor"],
    ["it_admin"],
    ["it_admin", "security_auditor"],
    ["security_auditor"],
    ["executive", "it_admin"],
]

SENSITIVITY_CLEARANCE_MAP = [
    ("PUBLIC", 0),
    ("INTERNAL", 1),
    ("CONFIDENTIAL", 2),
    ("RESTRICTED", 3),
]

DEPARTMENTS = ["IT Operations", "InfoSec", "Cloud Engineering", "DevOps", "HR Tech", "Enterprise Architecture"]

SAMPLE_TOPICS = [
    ("GlobalProtect VPN Configuration and Troubleshooting", "Steps to configure and diagnose Palo Alto Networks GlobalProtect VPN client on macOS and Windows 11."),
    ("Okta SSO Multi-Factor Authentication Reset Procedure", "Official standard operating procedure for resetting employee FIDO2 WebAuthn keys and Okta Verify pushes."),
    ("Google Cloud IAM Least Privilege Policy Guidelines", "Mandatory requirements for assigning Cloud IAM roles, condition expressions, and temporary access elevation."),
    ("Zero-Trust Device Certificate Provisioning", "Enrolling enterprise laptops with device certificates for mutual TLS (mTLS) zero-trust network access."),
    ("Hardware Token YubiKey 5C NFC Enrollment Guide", "Step-by-step instructions for provisioning YubiKey 5C NFC hardware security keys for admin accounts."),
    ("GitLab CI/CD Runner Autoscaling on Google Kubernetes Engine", "Architecture and operational runbook for Kubernetes-based GitLab CI runner autoscaling with spot nodes."),
    ("Enterprise Wi-Fi 802.1X EAP-TLS Troubleshooting", "Diagnosing corporate 802.1X Wi-Fi connectivity drops, certificate renewals, and RADIUS authentication errors."),
    ("CrowdStrike Falcon Endpoint Sensor Deployment and Verification", "Verifying EDR sensor connectivity, kernel extensions, and security policy compliance on Linux servers."),
    ("Data Loss Prevention (DLP) Policy for Google Workspace Drive", "Rules and automated remediation for sensitive data sharing (PII, PCI, source code) via Google Drive."),
    ("SOC 2 Type II Compliance Evidence Collection SOP", "Procedures for gathering automated audit trails, change management logs, and CloudTrail/Cloud Audit Logs.")
]


def _generate_unit_vector(dim: int, seed_val: int) -> List[float]:
    """Generates a reproducible, normalized pseudo-random vector on the unit sphere."""
    rng = random.Random(seed_val)
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


def generate_synthetic_chunk(idx: int, seed: int, dim: int = 768) -> Dict[str, Any]:
    """Generates a single synthetic enterprise KB chunk."""
    rng = random.Random(seed + idx * 31)
    
    topic_title, topic_desc = SAMPLE_TOPICS[idx % len(SAMPLE_TOPICS)]
    doc_num = (idx // 5) + 1
    chunk_in_doc = (idx % 5) + 1
    doc_id = f"KB-ENT-{doc_num:05d}"
    chunk_id = f"{doc_id}-CHK-{chunk_in_doc:02d}"
    
    category = CATEGORIES[idx % len(CATEGORIES)]
    roles = ROLES_DISTRIBUTION[idx % len(ROLES_DISTRIBUTION)]
    sensitivity, clearance_level = SENSITIVITY_CLEARANCE_MAP[idx % len(SENSITIVITY_CLEARANCE_MAP)]
    owner = DEPARTMENTS[idx % len(DEPARTMENTS)]
    
    # Realistic dates
    base_date = datetime.date(2025, 1, 1)
    effective_date = base_date + datetime.timedelta(days=(idx % 180))
    
    # ~5% expired records
    is_expired = (idx % 20 == 0)
    if is_expired:
        expiry_date = base_date - datetime.timedelta(days=30 + (idx % 60))
    else:
        expiry_date = base_date + datetime.timedelta(days=730 + (idx % 365))
        
    # ~5% soft-deleted / tombstoned records
    is_deleted = (idx % 23 == 0)
    deleted_at = "2025-08-15T10:30:00Z" if is_deleted else None
    
    content_text = (
        f"{topic_title} (Section {chunk_in_doc}): {topic_desc} "
        f"Document ID: {doc_id}. Category: {category}. Owner: {owner}. "
        f"This policy applies to authorized personnel with {roles} roles and clearance level >= {clearance_level}. "
        f"In case of operational anomalies or security incidents, contact the {owner} on-call engineer."
    )
    
    embedding = _generate_unit_vector(dim=dim, seed_val=seed + idx * 17)
    
    return {
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "title": f"{topic_title} - Part {chunk_in_doc}",
        "content": content_text,
        "category": category,
        "roles": roles,
        "sensitivity": sensitivity,
        "clearance_level": clearance_level,
        "source_uri": f"gs://enterprise-kb-vault/docs/{doc_id.lower()}.pdf",
        "owner": owner,
        "effective_date": effective_date.isoformat(),
        "expiry_date": expiry_date.isoformat(),
        "is_deleted": is_deleted,
        "deleted_at": deleted_at,
        "parser_version": "1.0.0",
        "chunker_version": "1.0.0",
        "embedding_model": "text-embedding-005",
        "embedding_dim": dim,
        "embedding": embedding,
    }


def generate_synthetic_dataset(num_chunks: int = 5000, seed: int = 42, dim: int = 768) -> List[Dict[str, Any]]:
    """Generates a complete dataset of synthetic enterprise knowledge chunks."""
    dataset = []
    for i in range(num_chunks):
        dataset.append(generate_synthetic_chunk(i, seed=seed, dim=dim))
    return dataset


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic enterprise knowledge base chunks for retrieval benchmarking.")
    parser.add_argument("--num-chunks", type=int, default=5000, help="Number of chunks to generate (default: 5000)")
    parser.add_argument("--output", type=str, default="synthetic_kb_5000.jsonl", help="Output JSONL file path")
    parser.add_argument("--dim", type=int, default=768, help="Vector embedding dimension (default: 768)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic reproducibility")
    args = parser.parse_args()

    print(f"Generating {args.num_chunks} synthetic knowledge base chunks (dim={args.dim}, seed={args.seed})...")
    dataset = generate_synthetic_dataset(num_chunks=args.num_chunks, seed=args.seed, dim=args.dim)
    
    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        
    with open(args.output, "w", encoding="utf-8") as f:
        for record in dataset:
            f.write(json.dumps(record) + "\n")
            
    file_size_mb = os.path.getsize(args.output) / (1024 * 1024)
    print(f"Successfully generated {len(dataset)} chunks -> {args.output} ({file_size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
