import httpx
import pytest

from sad import Analyzer, Resource
from sad.cli import main
from sad.crawler import CrawlConfig, Crawler
from sad.html import HTMLCollector


@pytest.mark.parametrize(
    "kwargs",
    [
        {"depth": -1},
        {"max_pages": 0},
        {"max_resources": 0},
        {"max_bytes": 0},
        {"max_total_bytes": 0},
        {"timeout": 0},
        {"max_redirects": -1},
    ],
)
def test_invalid_crawl_limits(kwargs):
    with pytest.raises(ValueError):
        CrawlConfig(**kwargs)


def test_invalid_seed_scheme():
    with pytest.raises(ValueError):
        Crawler().collect(["file:///etc/passwd"])


def test_redirect_then_source_only_collection():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/page"})
        if request.url.path == "/page":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<script src="/assets/app.js"></script>',
            )
        assert request.url.path == "/assets/app.js"
        return httpx.Response(
            200,
            headers={"content-type": "application/javascript"},
            text='fetch("api/delete-all",{method:"POST"});navigator.sendBeacon("/collect");new WebSocket("wss://e.test/ws")',
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dataset = Crawler(client=client).collect(["https://e.test/"])
    result = Analyzer().analyze_dataset(dataset)
    assert seen == ["https://e.test/", "https://e.test/page", "https://e.test/assets/app.js"]
    assert "https://e.test/api/delete-all" in {e.url for e in result.endpoints}


@pytest.mark.parametrize(
    ("seed", "canonical"),
    [
        ("https://intuity.it/", "https://www.intuity.it/"),
        ("https://www.intuity.it/", "https://intuity.it/"),
        ("http://intuity.it/", "https://www.intuity.it/"),
        ("http://intuity.it/", "https://intuity.it/"),
    ],
)
def test_seed_canonical_redirect_collects_assets_and_resolves_urls(seed, canonical):
    seen = []
    asset = canonical + "app.js"

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == seed:
            return httpx.Response(301, headers={"location": canonical})
        if url == canonical:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<script src="app.js"></script>',
            )
        assert url == asset
        return httpx.Response(
            200, headers={"content-type": "text/javascript"}, text='fetch("/api/users")'
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dataset = Crawler(client=client).collect([seed])
    assert seen == [seed, canonical, asset]
    assert not dataset.diagnostics
    assert Analyzer().analyze_dataset(dataset).endpoints[0].url == canonical + "api/users"


@pytest.mark.parametrize(
    "target",
    [
        "https://other.it/",
        "https://api.intuity.it/",
        "https://www.intuity.it.evil.test/",
        "https://www.intuity.it:8443/",
        "http://www.intuity.it/",
        "https://user@www.intuity.it/",
    ],
)
def test_seed_redirect_rejects_other_hosts_ports_downgrades_and_credentials(target):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": target})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dataset = Crawler(client=client).collect(["https://intuity.it/"])
    assert seen == ["https://intuity.it/"]
    assert not dataset.resources
    assert any("blocked redirect" in d for d in dataset.diagnostics)


def test_canonical_seed_redirect_still_respects_exclusions_and_redirect_budget():
    for config in (
        CrawlConfig(exclusions=("https://www.intuity.it/*",)),
        CrawlConfig(max_redirects=0),
    ):
        seen = []

        def handler(request, seen=seen):
            seen.append(str(request.url))
            return httpx.Response(301, headers={"location": "https://www.intuity.it/"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            dataset = Crawler(config, client=client).collect(["https://intuity.it/"])
        assert seen == ["https://intuity.it/"]
        assert any("blocked redirect" in d for d in dataset.diagnostics)


def test_asset_redirect_cannot_expand_canonical_scope():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<script src="/app.js"></script>',
            )
        return httpx.Response(302, headers={"location": "https://www.intuity.it/app.js"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dataset = Crawler(client=client).collect(["https://intuity.it/"])
    assert seen == ["https://intuity.it/", "https://intuity.it/app.js"]
    assert any("blocked redirect" in d for d in dataset.diagnostics)


def test_seed_redirect_chain_cannot_add_repeated_www_prefixes():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        target = "https://www." + request.url.host + "/"
        return httpx.Response(302, headers={"location": target})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dataset = Crawler(client=client).collect(["https://intuity.it/"])
    assert seen == ["https://intuity.it/", "https://www.intuity.it/"]
    assert any("blocked redirect" in d for d in dataset.diagnostics)


def test_analyze_url_and_cli_use_only_collector(monkeypatch, capsys):
    created = []
    original = httpx.Client

    def factory(**kwargs):
        client = original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "application/javascript"}, text='fetch("/api")'
                )
            ),
            **kwargs,
        )
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", factory)
    assert Analyzer().analyze_url("https://e.test/a.js").endpoints[0].url == "https://e.test/api"
    assert created[0].is_closed
    assert main(["crawl", "https://e.test/a.js", "--format", "json"]) == 0
    assert "/api" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["resources", "pages", "redirects", "total-bytes", "http-error"])
def test_crawl_failure_limits(kind):
    config = {
        "resources": CrawlConfig(max_resources=1),
        "pages": CrawlConfig(max_pages=1),
        "redirects": CrawlConfig(max_redirects=0),
        "total-bytes": CrawlConfig(max_total_bytes=4),
        "http-error": CrawlConfig(),
    }[kind]

    def handler(request):
        if kind == "redirects":
            return httpx.Response(302, headers={"location": "/again"})
        if kind == "http-error":
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/next">next</a><script src="/a.js"></script>',
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = Crawler(config, client=client).collect(["https://e.test/"])
    if kind != "pages":
        assert result.diagnostics
    assert len(result.resources) <= config.max_resources


def test_explicit_origin_scope_and_fragments():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        text = (
            '<script src="https://cdn.test/a.js#one"></script><script src="https://cdn.test/a.js#two"></script>'
            if request.url.host == "e.test"
            else 'fetch("/api")'
        )
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html" if request.url.host == "e.test" else "text/javascript"
            },
            text=text,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dataset = Crawler(
            CrawlConfig(allowed_origins=("https://cdn.test",)), client=client
        ).collect(["https://e.test"])
    assert sum("cdn.test" in x for x in seen) == 1
    assert len(dataset.resources) == 2


def test_html_bases_controls_and_non_script_preloads():
    parser = HTMLCollector(Resource("https://e.test/page", "", "text/html"))
    parser.feed("""<base href="/root/"><base href="/ignored/">
    <link rel="modulepreload" href="a.js"><link rel="preload" as="script" href="b.js"><link rel="preload" as="style" href="a.css">
    <form action="find"><input name="q" value="a"><input name="disabled" disabled></form>
    <form method="dialog" action="/not-network"></form>""")
    assert parser.base == "https://e.test/root/"
    assert parser.scripts == ["https://e.test/root/a.js", "https://e.test/root/b.js"]
    assert len(parser.forms) == 1
    assert parser.forms[0].query_parameters == [{"name": "q", "value": "a"}]
