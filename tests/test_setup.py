from graphql_api.app import create_app


def test_app_creates_without_error():
    app = create_app()
    assert app is not None


def test_app_has_graphql_route():
    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/graphql" in routes


def test_app_has_health_route():
    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/health" in routes
