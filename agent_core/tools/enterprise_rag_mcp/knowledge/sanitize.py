"""
Content sanitization, XML escaping, and document framing for RAG pipelines.
"""
import re
import html
from typing import Any


def escape_xml_attribute(val: Any) -> str:
    """
    Escapes special XML characters for attribute values (", <, >, &, ').
    Prevents attribute breakout and XML delimiter corruption.
    """
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def sanitize_retrieved_content(content: str) -> str:
    """
    Sanitizes raw document content to prevent delimiter injection attacks
    (e.g., embedding fake </retrieved_document> tags to break out of passive data boundary).
    Replaces any retrieved_document tag variations (case-insensitive, whitespace tolerant)
    with safe XML entity representations (&lt;...&gt;).
    """
    if not content:
        return ""
    return re.sub(
        r"<\s*(/)?\s*retrieved_document\b([^>]*)>",
        lambda m: f"&lt;{m.group(1) or ''}retrieved_document{m.group(2)}&gt;",
        content,
        flags=re.IGNORECASE
    )


def wrap_retrieved_document(content: str, doc_id: str, system: str, title: str) -> str:
    """
    Wraps retrieved document content in a secure structural XML boundary tag.
    Attributes and inner content are safely escaped to prevent delimiter and attribute injection.
    """
    safe_id = escape_xml_attribute(doc_id)
    safe_sys = escape_xml_attribute(system)
    safe_title = escape_xml_attribute(title)
    safe_content = sanitize_retrieved_content(content)
    return f'<retrieved_document id="{safe_id}" system="{safe_sys}" title="{safe_title}">\n{safe_content}\n</retrieved_document>'
