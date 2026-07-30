"""Round-trip a `RenderedClause` through a workspace file.

The workspace holds text, but provenance is metadata: `clause_id`, `version`, `source_sha`.
Storing rendered clauses as Markdown with frontmatter means `validate_draft` can reload
exactly what was rendered — including the sha of the approved template — without consulting
the drafting agent or trusting anything it wrote.
"""

from __future__ import annotations

import frontmatter

from backend.schemas.clause import RenderedClause

__all__ = ["clause_path", "dumps_rendered", "loads_rendered"]


def clause_path(clause_id: str) -> str:
    """Workspace path for a rendered clause. Under the read-only prefix by construction."""
    return f"clauses/{clause_id}.md"


def dumps_rendered(clause: RenderedClause) -> str:
    post = frontmatter.Post(
        clause.text,
        clause_id=clause.clause_id,
        version=clause.version,
        title=clause.title,
        order=clause.order,
        source_sha=clause.source_sha,
    )
    return frontmatter.dumps(post)


def loads_rendered(text: str) -> RenderedClause:
    post = frontmatter.loads(text)
    return RenderedClause(
        clause_id=str(post["clause_id"]),
        version=int(post["version"]),  # type: ignore[call-overload]
        title=str(post["title"]),
        order=int(post["order"]),  # type: ignore[call-overload]
        text=post.content,
        source_sha=str(post["source_sha"]),
    )
