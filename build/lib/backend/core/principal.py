"""Who is asking.

Memory needs an owner. The system has no authentication — a documented MVP decision — so this
module is the **seam** where authentication will land, and the only place a `Principal` may be
constructed.

Today it returns a fixed development principal. That is honest: a single-tenant deployment with
no auth has exactly one user, and pretending otherwise by reading a client-supplied header would
manufacture an isolation that does not exist. A `X-User-Id` header a client can set is a
user-switching primitive, not an identity.

`04-security-and-tenancy.md` replaces the body of `current_principal()` with JWT verification.
Nothing above this module changes when it does — which is the point of putting it here now
rather than threading `user_id` through later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

__all__ = ["Principal", "current_principal"]

#: Stable across restarts, so a fact remembered yesterday is still yours today.
_DEV_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
_DEV_USER = uuid.UUID("00000000-0000-0000-0000-000000000002")


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    user_id: uuid.UUID


def current_principal() -> Principal:
    """The only function permitted to construct a `Principal`.

    Asserted by `tests/test_memory.py::test_principal_is_constructed_in_one_place`.
    """
    return Principal(tenant_id=_DEV_TENANT, user_id=_DEV_USER)
