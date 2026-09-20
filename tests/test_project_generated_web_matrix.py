import json
import importlib.util
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from app.projects import service as project_service

GITHUB_CORPUS_PATH = Path(__file__).resolve().parents[1] / "docs" / "examples" / "projects" / "github-codex-web-corpus.json"
REGISTER_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "register_github_codex_project_samples.py"


def _github_corpus() -> dict:
    return json.loads(GITHUB_CORPUS_PATH.read_text(encoding="utf-8"))


def _register_script_module():
    spec = importlib.util.spec_from_file_location("register_github_codex_project_samples_under_test", REGISTER_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: str | bytes = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _target_for_ref(root: Path, entry: str, ref: str, *, root_level: bool) -> Path:
    rel = urlsplit(ref).path.lstrip("/")
    if root_level:
        return root / rel
    parent = PurePosixPath(entry).parent
    if str(parent) in {"", "."}:
        return root / rel
    return root / parent / rel


def test_generated_web_rewrite_matrix_supports_100_common_static_variants(tmp_path):
    """Compatibility harness for generated static apps from Vite/Next/CRA/etc.

    The matrix intentionally combines common output roots, framework asset
    prefixes and syntaxes Codex-generated pages use.  It guards the upload
    adapter against regressing from "many generated webpages run as services"
    back to one happy-path Vite fixture.
    """

    corpus = _github_corpus()
    dimensions = corpus["adapter_signature_dimensions"]
    entries = dimensions["entry_roots"]
    filenames = [
        "app.js",
        "index.css?v=1",
        "main.js",
        "main.css",
        "chunk.js",
        "app.css",
        "page.mjs",
        "start.js",
        "logo.png",
        "inter.woff2",
    ]
    refs = [
        f"{prefix}{filenames[index % len(filenames)]}"
        for index, prefix in enumerate(dimensions["asset_prefixes"])
    ] + [
        "/runtime.js",
        "/manifest.webmanifest",
        "/site.webmanifest",
        "/robots.txt",
    ]

    def html_quoted(ref: str, scan: Path) -> list[str]:
        scan.write_text(f'<script type="module" src="{ref}"></script>', encoding="utf-8")
        return [ref]

    def html_unquoted(ref: str, scan: Path) -> list[str]:
        scan.write_text(f"<link rel=modulepreload href={ref}>", encoding="utf-8")
        return [ref]

    def html_srcset(ref: str, scan: Path) -> list[str]:
        first = "/images/photo.png"
        second = "/images/photo@2x.png"
        scan.write_text(f'<img srcset="{first} 1x, {second} 2x">', encoding="utf-8")
        return [first, second]

    def css_url(ref: str, scan: Path) -> list[str]:
        scan.write_text(f"body{{background:url('{ref}')}}", encoding="utf-8")
        return [ref]

    def css_import(ref: str, scan: Path) -> list[str]:
        scan.write_text(f'@import "{ref}";', encoding="utf-8")
        return [ref]

    def js_dynamic_import(ref: str, scan: Path) -> list[str]:
        scan.write_text(f"export const run = () => import('{ref}')", encoding="utf-8")
        return [ref]

    syntaxes = [
        ("quoted", ".html", html_quoted),
        ("unquoted", ".html", html_unquoted),
        ("srcset", ".html", html_srcset),
        ("css-url", ".css", css_url),
        ("css-import", ".css", css_import),
        ("js-import", ".js", js_dynamic_import),
    ]

    variant_count = 100
    for index in range(variant_count):
        entry = entries[index % len(entries)]
        ref = refs[index % len(refs)]
        syntax_name, suffix, writer = syntaxes[index % len(syntaxes)]
        root = tmp_path / f"variant-{index:03d}-{syntax_name}"
        entry_path = root / entry
        _write(entry_path, "<!doctype html><title>Generated</title><div id=root></div>")

        if suffix in {".html", ".htm"}:
            scan_path = entry_path
        else:
            scan_path = root / "scanned" / f"asset-{index}{suffix}"
            scan_path.parent.mkdir(parents=True, exist_ok=True)
        referenced_urls = writer(ref, scan_path)

        root_level = index % 2 == 0
        for url in referenced_urls:
            _write(_target_for_ref(root, entry, url, root_level=root_level), b"asset")

        result = project_service._rewrite_project_root_absolute_asset_refs(  # noqa: SLF001
            root,
            "matrix_project",
            f"ver{index:03d}",
            entry,
        )

        assert result["replacement_count"] >= len(referenced_urls), f"variant {index} did not rewrite enough refs"
        combined_text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*") if path.suffix.lower() in {".html", ".css", ".js"})
        for url in referenced_urls:
            rewritten_rel = project_service._local_project_asset_for_root_ref(root, entry, url)  # noqa: SLF001
            assert rewritten_rel
            assert f"/api/projects/assets/matrix_project/ver{index:03d}/{rewritten_rel}" in combined_text


def test_github_codex_web_corpus_documents_real_sources_and_drives_matrix():
    corpus = _github_corpus()
    repos = corpus["source_repositories"]
    repo_names = {item["repo"] for item in repos}
    assert len(repos) >= 8
    assert "cruzyjapan/Codex-CLI-UI" in repo_names
    assert "sangay-yonten/OpenAI-Codex" in repo_names
    assert "tidewave-ai/tidewave_js" in repo_names
    assert "autohandai/commander" in repo_names
    assert any(item["source"] == "openai/codex#14785" for item in corpus["codex_generated_issue_signatures"])

    all_signatures = {
        signature
        for item in repos + corpus["codex_generated_issue_signatures"]
        for signature in item["stack_signatures"]
    }
    assert {"vite", "react", "nextjs", "electron_vite", "monorepo_frontend_dir", "react19"} <= all_signatures

    dimensions = corpus["adapter_signature_dimensions"]
    assert len(dimensions["entry_roots"]) >= 10
    assert len(dimensions["asset_prefixes"]) >= 10
    assert len(dimensions["reference_syntaxes"]) >= 10
    assert len(dimensions["framework_outputs"]) >= 10
    assert (
        len(dimensions["entry_roots"])
        * len(dimensions["asset_prefixes"])
        * len(dimensions["reference_syntaxes"])
    ) >= corpus["minimum_tested_variants"]


def test_github_registration_script_builds_safe_project_packages_from_downloaded_signatures(tmp_path):
    corpus = _github_corpus()
    script = _register_script_module()
    for index, item in enumerate(corpus["source_repositories"][:6], start=1):
        project_id = script.project_id_for_repo(item["repo"], index)
        package_bytes, manifest = script.build_sample_package(item, project_id=project_id, index=index, cache_dir=tmp_path / project_id)
        assert manifest["project_id"] == project_id
        assert manifest["metadata"]["github_corpus"]["repo"] == item["repo"]
        assert manifest["entry"] in corpus["adapter_signature_dimensions"]["entry_roots"]
        with project_service.tarfile.open(fileobj=project_service.io.BytesIO(package_bytes), mode="r:gz") as tar:
            names = set(tar.getnames())
        assert "projectforge.yaml" in names
        assert manifest["entry"] in names
        assert any(name.endswith("assets/github-sample.js") for name in names)
        assert any(name.endswith("_next/static/chunks/app.js") for name in names)


def test_github_registration_script_expands_real_repos_to_100_adapter_variants():
    corpus = _github_corpus()
    script = _register_script_module()

    items = script.expand_corpus_items(corpus, 100)
    variants = [item["adapter_variant"] for item in items]

    assert len(items) == 100
    assert len({item["repo"] for item in items}) >= 8
    assert len({variant["reference_syntax"] for variant in variants}) >= 10
    assert len({variant["entry_root"] for variant in variants}) >= 10
    assert len({variant["asset_prefix"] for variant in variants}) >= 10
    assert len({variant["app_shape"] for variant in variants}) >= 5


def test_github_registration_service_payload_and_summary_cover_gateway_service():
    corpus = _github_corpus()
    script = _register_script_module()
    item = script.expand_corpus_items(corpus, 1)[0]
    project_id = script.project_id_for_repo(item["repo"], 1)
    payload = script.service_invoke_payload_for_github_sample(
        item,
        project_id=project_id,
        batch_id="batch-test",
        download_status="downloaded_root_contents",
    )

    assert payload["request_id"].startswith("github-corpus-service-batch-test-")
    assert payload["input"]["department"] == "AI小组"
    assert payload["input"]["github_repo"] == item["repo"]
    assert payload["output"]["reports"]
    assert payload["output"]["todos"][0]["kind"] in {"review", "dispatch", "train_model"}
    assert payload["output"]["proofs"][0]["credential_location"] == "platform_only"

    rows = [
        {
            "repo": item["repo"],
            "runtime_status": "pass",
            "normal_run_ready": True,
            "result_eval_status": "pass",
            "service_status": "converted",
            "gateway_injected": True,
            "service_invoked": True,
            "service_ok": True,
            "service_result_ready": True,
            "adapter_variant": item["adapter_variant"],
        }
        for _ in range(100)
    ]
    summary = script.summarize_registered(rows, items=[item] * 100, corpus=corpus)
    assert summary["registered_count"] == 100
    assert summary["normal_runtime_ready_count"] == 100
    assert summary["result_eval_pass_count"] == 100
    assert summary["service_converted_count"] == 100
    assert summary["gateway_injected_count"] == 100
    assert summary["service_invoked_count"] == 100
    assert summary["service_result_ready_count"] == 100
