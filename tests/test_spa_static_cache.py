from pathlib import Path

from starlette.responses import Response

from app.main import SPAStaticFiles, frontend_entry_asset, resolve_vue_dist_path


def test_spa_index_paths_are_not_cached():
    for path in ("", ".", "/", "index.html"):
        response = Response()

        SPAStaticFiles._apply_cache_headers(response, SPAStaticFiles._normalize_cache_path(path))

        assert response.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"


def test_spa_vite_assets_are_cached_immutably():
    response = Response()

    SPAStaticFiles._apply_cache_headers(
        response,
        SPAStaticFiles._normalize_cache_path("assets/index-COhvugVM.js"),
    )

    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_spa_fallback_does_not_swallow_static_asset_404s():
    assert SPAStaticFiles._should_serve_spa_fallback("skills/detail/abc")
    assert not SPAStaticFiles._should_serve_spa_fallback("assets/missing.js")
    assert not SPAStaticFiles._should_serve_spa_fallback("favicon.svg")


def test_release_checkout_always_serves_its_own_frontend(tmp_path):
    release_root = tmp_path / "skillforge-releases" / "21b0aa43"

    resolved, source = resolve_vue_dist_path("/srv/skillforge/shared-old-dist", release_root)

    assert resolved == release_root / "web" / "dist"
    assert source == "release_local"


def test_non_release_checkout_can_use_configured_frontend(tmp_path):
    project_root = tmp_path / "skillforge"

    resolved, source = resolve_vue_dist_path("/srv/skillforge/frontend", project_root)

    assert resolved == Path("/srv/skillforge/frontend")
    assert source == "configured"


def test_frontend_entry_asset_reads_the_built_hash(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/index-current.js"></script>',
        encoding="utf-8",
    )

    assert frontend_entry_asset(tmp_path) == "index-current.js"
