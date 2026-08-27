import logging
import sys
from fastmcp import FastMCP
from dotenv import load_dotenv

try:
    from knowledge_store import KnowledgeStore
    from rag_models import SearchResult, DocumentSummary
except ImportError:
    from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStore
    from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import SearchResult, DocumentSummary

def _initialize_console_logging(min_level: int = logging.INFO):
    # Logs MUST go to stderr to prevent breaking the stdio MCP protocol
    handler = logging.StreamHandler(sys.stderr)
    logging.basicConfig(level=min_level, handlers=[handler], force=True)

store = KnowledgeStore()
mcp = FastMCP(name="EnterpriseKnowledgeRAG")

@mcp.tool()
def search_enterprise_knowledge(query: str, system: str = "ALL") -> list[dict]:
    """
    Searches enterprise knowledge base for ERP, HRM, and CRM systems.
    - system: 'ERP', 'HRM', 'CRM', or 'ALL'
    """
    results = store.search(query=query, system=system, limit=3)
    return [r.model_dump() for r in results]

@mcp.tool()
def get_system_manual(article_id: str) -> dict:
    """
    Retrieves the complete technical manual or troubleshooting guide for a specific article ID.
    """
    article = store.get_article_by_id(article_id)
    if not article:
        return {"status": "error", "message": f"Article '{article_id}' not found."}
    return {"status": "success", "article": article.model_dump()}

@mcp.tool()
def summarize_long_document(document_text: str, system_name: str = "Enterprise System") -> dict:
    """
    Extracts key points, prerequisites, and action items from long enterprise documents.
    """
    lines = [line.strip() for line in document_text.strip().split("\n") if line.strip()]
    key_points = [l for l in lines if l.startswith(("-", "*", "1.", "2.", "3.", "4.", "5."))] or lines[:3]
    action_items = [l for l in lines if any(w in l.lower() for w in ["bước", "step", "yêu cầu", "khắc phục", "action", "quy trình"])]
    
    return {
        "status": "success",
        "system": system_name,
        "summary": {
            "total_length": len(document_text),
            "key_points": key_points[:5],
            "action_items": action_items[:5] or ["Tuân thủ quy trình bảo mật IT."],
        }
    }

@mcp.tool()
def draft_email_response(
    user_name: str,
    ticket_id: str,
    issue_summary: str,
    solution_steps: str,
    urgency: str = "Normal"
) -> dict:
    """
    Drafts a standardized, polite, and professional email response to update the user regarding their ticket.
    """
    email_subject = f"[IT Helpdesk - {ticket_id}] Cập nhật xử lý: {issue_summary}"
    email_body = f"""Kính gửi Anh/Chị {user_name},

Bộ phận IT Helpdesk xin thông báo về tiến độ xử lý yêu cầu hỗ trợ của Anh/Chị:
- Mã Ticket: {ticket_id}
- Vấn đề ghi nhận: {issue_summary}
- Mức độ ưu tiên: {urgency}

--- HƯỚNG DẪN XỬ LÝ / KẾT QUẢ ---
{solution_steps}

Nếu Anh/Chị cần hỗ trợ thêm hoặc sự cố chưa được giải quyết triệt để, vui lòng phản hồi trực tiếp email này hoặc liên hệ hotline IT Helpdesk (Ext: 1080).

Trân trọng,
Đội ngũ IT Helpdesk & Enterprise Support
"""
    return {
        "status": "success",
        "subject": email_subject,
        "body": email_body
    }

if __name__ == "__main__":
    load_dotenv()
    _initialize_console_logging()
    mcp.run(transport="stdio")
