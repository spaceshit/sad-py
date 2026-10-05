"""HTML resource and declarative form extraction using the standard HTML parser."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

from sad.models import Endpoint, Evidence, Resource


class HTMLCollector(HTMLParser):
    def __init__(self, resource: Resource) -> None:
        super().__init__(convert_charrefs=True)
        self.resource = resource
        self.base = resource.url
        self.links: list[str] = []
        self.scripts: list[str] = []
        self.inline: list[Resource] = []
        self.forms: list[Endpoint] = []
        self._script: list[str] | None = None
        self._line = 0
        self._form: Endpoint | None = None
        self._base_seen = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        line, col = self.getpos()
        if tag == "base" and values.get("href") and not self._base_seen:
            self.base = urljoin(self.resource.url, values["href"] or "")
            self._base_seen = True
        if tag == "a" and values.get("href"):
            self.links.append(urljoin(self.base, values["href"] or ""))
        if tag == "link" and values.get("rel") in ("modulepreload", "preload"):
            if values.get("as") == "script" or values.get("rel") == "modulepreload":
                self.scripts.append(urljoin(self.base, values.get("href") or ""))
        if tag == "script":
            script_type = (values.get("type") or "").lower()
            if script_type not in ("", "module", "text/javascript", "application/javascript"):
                return
            if values.get("src"):
                self.scripts.append(urljoin(self.base, values["src"] or ""))
            else:
                self._script, self._line = [], line - 1
        if tag == "form":
            method = (values.get("method") or "GET").upper()
            if method == "DIALOG":
                return
            self._form = Endpoint(
                method=method,
                url=urljoin(self.base, values.get("action") or self.resource.url),
                source_file=self.resource.source_file or self.resource.url,
                source_line=line,
                source_column=col + 1,
                framework="html-form",
                content_type=values.get("enctype") or "application/x-www-form-urlencoded",
                body={} if method != "GET" else None,
                evidence=[
                    Evidence(
                        "html-form",
                        self.get_starttag_text() or "",
                        self.resource.url,
                        line,
                        col + 1,
                    )
                ],
            )
            self.forms.append(self._form)
        if tag in ("input", "textarea", "select", "button") and self._form and values.get("name"):
            if "disabled" in values:
                return
            name = values["name"] or ""
            value = values.get("value")
            if self._form.method == "GET":
                self._form.query_parameters.append({"name": name, "value": value})
            else:
                self._form.body[name] = value

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.inline.append(
                Resource(
                    self.resource.url + f"#inline-{len(self.inline)}.js",
                    "".join(self._script),
                    source_file=self.resource.source_file or self.resource.url,
                    line_offset=self._line,
                )
            )
            self._script = None
        if tag == "form":
            self._form = None
