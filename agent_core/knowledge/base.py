from dataclasses import dataclass, field
from typing import Optional, Any

@dataclass(frozen=True)
class SecurityContext:
    user_id: str = "anonymous"
    roles: list[str] = field(default_factory=list)
    clearance_level: int = 0
    authenticated: bool = False

    @classmethod
    def from_user(
        cls,
        user_id: Optional[str] = None,
        roles: Optional[list[str]] = None,
        clearance_level: Optional[int] = None,
    ) -> "SecurityContext":
        clean_user_id = user_id or "anonymous"
        clean_roles = [str(r).lower().strip() for r in (roles or []) if r]
        
        # Calculate clearance if not provided
        if clearance_level is not None:
            eff_clearance = int(clearance_level)
        else:
            if any(r in ("admin", "it_admin", "security_admin", "super_admin") for r in clean_roles):
                eff_clearance = 3
            elif any(r in ("hr_admin", "finance_admin", "compliance_officer", "legal_counsel", "hr_manager", "finance_manager") for r in clean_roles):
                eff_clearance = 2
            elif clean_roles:
                eff_clearance = 1
            else:
                eff_clearance = 0
                
        is_auth = bool(user_id and user_id != "anonymous" and (clean_roles or eff_clearance > 0))
        return cls(
            user_id=clean_user_id,
            roles=clean_roles,
            clearance_level=eff_clearance,
            authenticated=is_auth,
        )

    @classmethod
    def anonymous(cls) -> "SecurityContext":
        return cls(user_id="anonymous", roles=[], clearance_level=0, authenticated=False)

    @classmethod
    def admin(cls, user_id: str = "system-admin") -> "SecurityContext":
        return cls(user_id=user_id, roles=["admin", "it_admin", "support_agent"], clearance_level=3, authenticated=True)

