from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from it_helpdesk_agent.app_utils.system_config import get_valid_system_filters
except ImportError:
    try:
        from app_utils.system_config import get_valid_system_filters
    except ImportError:
        def get_valid_system_filters() -> set[str]:
            return {"ERP", "HRM", "CRM", "ALL"}


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    domain: str
    key: str
    value: str
    value_type: str  # 'int' | 'float' | 'string' | 'bool'
    unit: Optional[str] = None
    source_document: Optional[str] = None
    date_updated: str
    updated_by: str = "human"  # 'human' | 'agent'
    status: str = "active"  # 'active' | 'deprecated' | 'superseded'
    superseded_by: Optional[str] = None
    notes: Optional[str] = None

    def typed_value(self) -> Any:
        vt = (self.value_type or "string").lower().strip()
        if vt == "int":
            return int(self.value)
        elif vt == "float":
            return float(self.value)
        elif vt == "bool":
            return str(self.value).lower() in ("true", "1", "yes")
        return self.value


class Obligation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obligation_id: str
    source_id: str
    source_title: str
    authority: str
    article: Optional[str] = None
    description: str
    severity: str  # 'critical' | 'high' | 'medium' | 'low'
    applies_to: str  # 'vendor' | 'customer' | 'both'
    date_added: str  # YYYY-MM-DD
    date_effective: str  # YYYY-MM-DD
    date_expires: Optional[str] = None  # YYYY-MM-DD
    status: str = "active"  # 'active' | 'superseded' | 'expired'
    source_document_path: Optional[str] = None


class SectionHierarchy(BaseModel):
    """Hierarchical document section path (H1/H2/H3)."""
    h1: Optional[str] = None
    h2: Optional[str] = None
    h3: Optional[str] = None

    def format_path(self) -> str:
        parts = [p.strip() for p in (self.h1, self.h2, self.h3) if p and p.strip()]
        return " > ".join(parts) if parts else ""


class KnowledgeArticle(BaseModel):
    id: str
    system: str
    title: str
    category: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    section_h1: Optional[str] = None
    section_h2: Optional[str] = None
    section_h3: Optional[str] = None
    section_hierarchy: Optional[SectionHierarchy] = None
    parent_doc_id: Optional[str] = None
    chunk_index: Optional[int] = 0
    allowed_roles: list[str] = Field(default_factory=list)
    sensitivity: Optional[str] = "INTERNAL"
    clearance_level: Optional[int] = None
    source_uri: Optional[str] = None
    owner: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    is_deleted: bool = False
    deleted_at: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if self.section_hierarchy is None and any([self.section_h1, self.section_h2, self.section_h3]):
            self.section_hierarchy = SectionHierarchy(
                h1=self.section_h1,
                h2=self.section_h2,
                h3=self.section_h3,
            )
        elif self.section_hierarchy is not None:
            if not self.section_h1:
                self.section_h1 = self.section_hierarchy.h1
            if not self.section_h2:
                self.section_h2 = self.section_hierarchy.h2
            if not self.section_h3:
                self.section_h3 = self.section_hierarchy.h3

        if self.clearance_level is None:
            sens = (self.sensitivity or "INTERNAL").upper()
            if sens == "PUBLIC":
                self.clearance_level = 0
            elif sens == "CONFIDENTIAL":
                self.clearance_level = 2
            elif sens == "RESTRICTED":
                self.clearance_level = 3
            else:
                self.clearance_level = 1

    @field_validator("system")
    @classmethod
    def validate_system(cls, v: str) -> str:
        clean_v = v.strip().upper() if v else ""
        valid_set = get_valid_system_filters()
        if clean_v not in valid_set:
            raise ValueError(f"Hệ thống '{v}' không hợp lệ. Các hệ thống được hỗ trợ: {sorted(list(valid_set))}")
        return clean_v


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: str
    system: str
    title: str
    snippet: str
    relevance_score: float
    section_h1: Optional[str] = None
    section_h2: Optional[str] = None
    section_h3: Optional[str] = None
    section_hierarchy: Optional[SectionHierarchy] = None
    context_path: Optional[str] = None
    parent_doc_id: Optional[str] = None
    chunk_index: Optional[int] = 0
    allowed_roles: list[str] = Field(default_factory=list)
    sensitivity: Optional[str] = "INTERNAL"
    clearance_level: Optional[int] = None
    source_uri: Optional[str] = None
    category: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    owner: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    is_deleted: bool = False
    is_truncated: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.section_hierarchy is None and any([self.section_h1, self.section_h2, self.section_h3]):
            self.section_hierarchy = SectionHierarchy(
                h1=self.section_h1,
                h2=self.section_h2,
                h3=self.section_h3,
            )
        elif self.section_hierarchy is not None:
            if not self.section_h1:
                self.section_h1 = self.section_hierarchy.h1
            if not self.section_h2:
                self.section_h2 = self.section_hierarchy.h2
            if not self.section_h3:
                self.section_h3 = self.section_hierarchy.h3

        if self.clearance_level is None:
            sens = (self.sensitivity or "INTERNAL").upper()
            if sens == "PUBLIC":
                self.clearance_level = 0
            elif sens == "CONFIDENTIAL":
                self.clearance_level = 2
            elif sens == "RESTRICTED":
                self.clearance_level = 3
            else:
                self.clearance_level = 1
        if not self.context_path and self.section_hierarchy:
            self.context_path = self.section_hierarchy.format_path()

    @field_validator("system")
    @classmethod
    def validate_system(cls, v: str) -> str:
        clean_v = v.strip().upper() if v else ""
        valid_set = get_valid_system_filters()
        if clean_v not in valid_set:
            raise ValueError(f"Hệ thống '{v}' không hợp lệ. Các hệ thống được hỗ trợ: {sorted(list(valid_set))}")
        return clean_v


class DocumentSummary(BaseModel):
    system: str
    title: str
    key_points: list[str]
    action_items: list[str]
    suggested_email_draft: Optional[str] = None
