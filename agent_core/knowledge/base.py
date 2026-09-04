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
            try:
                from agent_core.app_utils.system_config import compute_user_clearance
                eff_clearance = compute_user_clearance(clean_roles)
            except Exception:
                if any(r in ("admin", "super_admin") for r in clean_roles):
                    eff_clearance = 3
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

