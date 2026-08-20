"""Route-shape tests.

These exist because of a bug that was invisible from the code: /api/bots/live/trades
was declared after /api/bots/{bot_id}/trades, so FastAPI matched the parameterised
route first and bound bot_id="live". The query went to Postgres as
bot_instance_id=eq.live, which is not a uuid, and the Live bot tab was simply
empty. Nothing in either function was wrong -- only their order.
"""

from __future__ import annotations

import pytest

from app.api.main import create_app


def _walk(routes):
    """Flatten routes in matching order.

    Recent FastAPI keeps an included router as a single wrapper object rather
    than splicing its routes into app.routes, so a flat read of app.routes finds
    only the built-ins. Follow original_router to see what the app will actually
    match, in the order it will try them.
    """
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _walk(nested.routes)
        elif getattr(route, "methods", None):
            yield route.path, sorted(route.methods)


@pytest.fixture(scope="module")
def routes():
    found = list(_walk(create_app().routes))
    # Guard the guard: if the walk stops finding routes after a FastAPI upgrade,
    # every ordering assertion below would vacuously pass.
    assert len(found) > 20, f"route walk found only {len(found)}; it is not reaching the routers"
    return found


def index_of(routes, path, method="GET"):
    for i, (p, methods) in enumerate(routes):
        if p == path and method in methods:
            return i
    raise AssertionError(f"{method} {path} is not registered")


@pytest.mark.parametrize(
    "literal,parameterised",
    [
        ("/api/bots/live/trades", "/api/bots/{bot_id}/trades"),
        ("/api/backtests/jobs", "/api/backtests/{run_id}"),
        ("/api/backtests/failed", "/api/backtests/{run_id}"),
    ],
)
def test_literal_routes_are_not_shadowed(routes, literal, parameterised):
    method = "DELETE" if literal.endswith("/failed") else "GET"
    assert index_of(routes, literal, method) < index_of(routes, parameterised, method), (
        f"{literal} is declared after {parameterised}, so requests for it will bind "
        "the wrong path parameter instead of reaching their handler"
    )


def test_no_two_routes_share_a_path_and_method(routes):
    seen: dict[tuple[str, str], int] = {}
    for path, methods in routes:
        for method in methods:
            key = (path, method)
            seen[key] = seen.get(key, 0) + 1
    duplicates = [k for k, n in seen.items() if n > 1]
    assert not duplicates, f"duplicate route registrations: {duplicates}"


def test_a_parameterised_segment_never_precedes_a_literal_sibling(routes):
    """Catch this class of bug anywhere it appears, not just where we knew to look."""
    problems = []
    for i, (path_a, methods_a) in enumerate(routes):
        segments_a = path_a.strip("/").split("/")
        for path_b, methods_b in routes[i + 1:]:
            segments_b = path_b.strip("/").split("/")
            if len(segments_a) != len(segments_b):
                continue
            if not set(methods_a) & set(methods_b):
                continue
            # Same shape, and every segment either matches exactly or is a
            # placeholder in A standing where B has a literal -- so A wins the
            # match and B is unreachable.
            shadows = all(
                a == b or (a.startswith("{") and not b.startswith("{"))
                for a, b in zip(segments_a, segments_b)
            )
            if shadows and path_a != path_b:
                problems.append(f"{path_a} shadows {path_b}")
    assert not problems, "unreachable routes: " + "; ".join(problems)


def test_crossed_literals_do_not_collide(routes):
    """/accounts/validations/{id} and /accounts/{id}/validations are both safe.

    Each holds a literal where the other holds a placeholder, so neither can
    match the other's requests and their order does not matter. Recorded here
    because they look like the bug above and are not.
    """
    a = "/api/accounts/validations/{run_id}"
    b = "/api/accounts/{account_id}/validations"
    assert index_of(routes, a) >= 0 and index_of(routes, b) >= 0
    for path in (a, b):
        segments = path.strip("/").split("/")
        other = b if path == a else a
        other_segments = other.strip("/").split("/")
        assert any(
            not s1.startswith("{") and not s2.startswith("{") and s1 != s2
            or (s1.startswith("{") != s2.startswith("{")
                and not s1.startswith("{"))
            for s1, s2 in zip(segments, other_segments)
        ), f"{path} and {other} may actually collide"
