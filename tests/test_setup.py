from graphql_api.app import create_app


def _all_route_paths(routes, prefix=""):
    """Coleta os paths de todas as rotas, descendo em routers/mounts aninhados.

    A representação de `include_router(...)` em `app.routes` mudou entre versões
    do FastAPI/Starlette; este helper tolera todas:
      - rotas achatadas direto em `app.routes` (têm `.path`);
      - wrapper/Mount com `.routes` (+ opcional `.prefix`);
      - FastAPI >= 0.137: wrapper `_IncludedRouter` sem `.path`/`.routes`, com as
        rotas reais sob `.original_router.routes` e o prefixo em
        `.include_context.prefix`.
    """
    paths = []
    for r in routes:
        path = getattr(r, "path", None)
        if path is not None:
            paths.append(prefix + path)

        sub = getattr(r, "routes", None)
        sub_prefix = getattr(r, "prefix", "") or ""
        if sub is None:
            # _IncludedRouter (FastAPI >= 0.137): rotas em .original_router,
            # prefixo em .include_context.
            original = getattr(r, "original_router", None)
            if original is not None:
                sub = getattr(original, "routes", None)
                ctx = getattr(r, "include_context", None)
                sub_prefix = getattr(ctx, "prefix", "") or getattr(original, "prefix", "") or ""

        if sub:
            paths.extend(_all_route_paths(sub, prefix + sub_prefix))
    return paths


def test_app_creates_without_error():
    app = create_app()
    assert app is not None


def test_app_has_graphql_route():
    paths = _all_route_paths(create_app().routes)
    assert any(p.rstrip("/") == "/graphql" for p in paths)


def test_app_has_health_route():
    assert "/health" in _all_route_paths(create_app().routes)
