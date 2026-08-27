from typing import Optional, Literal
from pydantic import BaseModel, Field

SystemType = Literal["ERP", "HRM", "CRM", "ALL"]

class KnowledgeArticle(BaseModel):
    id: str
    system: SystemType
    title: str
    category: str
    content: str
    keywords: list[str] = Field(default_factory=list)

class SearchResult(BaseModel):
    article_id: str
    system: SystemType
    title: str
    snippet: str
    relevance_score: float

class DocumentSummary(BaseModel):
    system: str
    title: str
    key_points: list[str]
    action_items: list[str]
    suggested_email_draft: Optional[str] = None
