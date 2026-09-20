def test_openapi_schema_resolves_cached_routes_and_project_service_invoke():
    from app.main import app

    schema = app.openapi()

    assert "/api/projects/{project_id}/service/invoke" in schema["paths"]
    assert "post" in schema["paths"]["/api/projects/{project_id}/service/invoke"]
    assert "/api/codex/projects/{project_id}/service/invoke" in schema["paths"]
    assert "post" in schema["paths"]["/api/codex/projects/{project_id}/service/invoke"]
    assert "/api/todos/bulk-summary" in schema["paths"]
    assert "BulkSummaryRequest" in schema["components"]["schemas"]
