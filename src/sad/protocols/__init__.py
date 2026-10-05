"""Extensible network sink registry. Unknown names are never sinks by themselves."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SinkRegistry:
    packages: dict[str, str] = field(
        default_factory=lambda: {
            name: name
            for name in (
                "axios",
                "ky",
                "got",
                "superagent",
                "request",
                "wretch",
                "ofetch",
                "unfetch",
                "jquery",
                "socket.io-client",
                "graphql-request",
                "@apollo/client",
                "urql",
                "@urql/core",
                "@angular/common/http",
                "@connectrpc/connect-web",
                "@connectrpc/connect",
                "@trpc/client",
                "grpc-web",
            )
        }
    )
    globals: dict[str, str] = field(
        default_factory=lambda: {
            "fetch": "fetch",
            "axios": "axios",
            "$": "jquery",
            "jQuery": "jquery",
            "XMLHttpRequest": "xhr",
            "WebSocket": "websocket",
            "EventSource": "sse",
        }
    )

    def register_package(self, package: str, framework: str = "axios") -> None:
        """Register a trusted package with a known calling convention."""
        self.packages[package] = framework
