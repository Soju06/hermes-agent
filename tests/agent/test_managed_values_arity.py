"""Invariant: every ``_managed_values(...)`` unpacking matches its return arity.

Regression guard for the ADR-004 notes/curator middleware sites. The
2026-08-04 base bump changed ``_run_agent_tool_execution_middleware`` to
return a ``_ManagedToolResult`` unwrapped by ``_managed_values`` (5-tuple);
fork patch sites that kept an older, narrower unpack crashed every
``notes_write`` / ``notes_read`` / ``memory_propose`` / ``curator_verdict``
call with ``ValueError: too many values to unpack`` (2026-08-21 incident:
the broken notes lane pushed probe traffic into the prod memory graph).

This test is intentionally static (AST-based) so it fails on the ASSEMBLED
stack whenever ANY patch reintroduces a stale-arity call site, regardless
of which patch owns the surrounding code.
"""

import ast
from pathlib import Path

TOOL_EXECUTOR = Path(__file__).resolve().parents[2] / "agent" / "tool_executor.py"


def _managed_values_return_arity(tree: ast.Module) -> int:
    """Arity of the tuple returned by ``_managed_values`` itself."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_managed_values":
            returns = [
                n for n in ast.walk(node)
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)
            ]
            assert returns, "_managed_values must return a literal tuple"
            arities = {len(r.value.elts) for r in returns}
            assert len(arities) == 1, f"_managed_values returns mixed arities: {arities}"
            return arities.pop()
    raise AssertionError("_managed_values not found in tool_executor.py")


def _is_managed_values_call(value: ast.expr) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "_managed_values"
    )


def test_every_managed_values_unpack_matches_return_arity():
    tree = ast.parse(TOOL_EXECUTOR.read_text(encoding="utf-8"))
    expected = _managed_values_return_arity(tree)

    call_sites = []
    mismatches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not _is_managed_values_call(node.value):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Tuple):
            call_sites.append(node.lineno)
            if len(target.elts) != expected:
                mismatches.append((node.lineno, len(target.elts)))

    assert call_sites, "no _managed_values() unpacking sites found — test is stale"
    assert not mismatches, (
        f"_managed_values returns a {expected}-tuple but these call sites unpack "
        f"a different arity (line, arity): {mismatches}. A stale-arity unpack "
        "crashes the tool loop for that tool (see 2026-08-21 notes-lane incident)."
    )
