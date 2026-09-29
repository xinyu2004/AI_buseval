"""Shared topology graph helpers used by lint and the predictor."""
from __future__ import annotations


def normalize_source(source) -> list[str]:
    if source is None:
        return []
    if isinstance(source, str):
        return [source]
    return list(source)


def isp_multi_source_message(name: str, src_list: list[str]) -> str:
    return f"pipeline '{name}': ISP does not support multi-source (got {src_list})."


def source_not_found_message(name: str, src_name: str) -> str:
    return (
        f"pipeline '{name}': source '{src_name}' not found among masters or pipelines."
    )


def cycle_message(path: list[str]) -> str:
    return f"cyclic pipeline dependency: {' -> '.join(path)}"


def find_cycle(pipelines) -> list[str] | None:
    """Return a cycle path if pipeline sources loop, else None."""
    by_name = {p.name: p for p in pipelines}
    visited: dict[str, int] = {}

    def visit(name: str, stack: list[str]) -> list[str] | None:
        state = visited.get(name)
        if state == 1:
            return None
        if state == 0:
            idx = stack.index(name) if name in stack else 0
            return stack[idx:] + [name]
        visited[name] = 0
        stack.append(name)
        node = by_name.get(name)
        if node is not None:
            for src in normalize_source(node.source):
                if src in by_name:
                    found = visit(src, stack)
                    if found:
                        return found
        stack.pop()
        visited[name] = 1
        return None

    for node in pipelines:
        found = visit(node.name, [])
        if found:
            return found
    return None


def topo_sort_pipelines(pipelines) -> list:
    """Order pipelines so each source pipeline is computed first.

    Raises ValueError on a cycle. Unknown source names are ignored here;
    the predictor reports them when it resolves bandwidth.
    """
    by_name = {p.name: p for p in pipelines}
    visited: dict[str, int] = {}
    order: list = []

    def visit(name: str, stack: list[str]) -> None:
        state = visited.get(name)
        if state == 1:
            return
        if state == 0:
            raise ValueError(cycle_message(stack + [name]))
        node = by_name.get(name)
        if node is None:
            return
        visited[name] = 0
        stack.append(name)
        for src in normalize_source(node.source):
            if src in by_name:
                visit(src, stack)
        stack.pop()
        visited[name] = 1
        order.append(node)

    for node in pipelines:
        visit(node.name, [])
    return order
