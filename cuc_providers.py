"""Provider contracts and registry for CUC update sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator


CheckFunction = Callable[[Any], Any]
BuildFunction = Callable[[Any, bool], list[str] | None]
DisabledResultFactory = Callable[[], Any]


@dataclass(slots=True)
class UpdateProvider:
    """One update source behind a frontend-independent contract."""

    source: str
    display_name: str
    check: CheckFunction
    build_command: BuildFunction
    disabled_result: DisabledResultFactory


class ProviderRegistry:
    """Ordered provider lookup for frontend-independent update sources."""

    def __init__(self, providers: Iterable[UpdateProvider]) -> None:
        self._providers = {provider.source: provider for provider in providers}

    def __iter__(self) -> Iterator[str]:
        return iter(self._providers)

    def __len__(self) -> int:
        return len(self._providers)

    def __getitem__(self, source: str) -> UpdateProvider:
        return self._providers[source]

    def get(self, source: str) -> UpdateProvider | None:
        return self._providers.get(source)

    def values(self) -> tuple[UpdateProvider, ...]:
        return tuple(self._providers.values())

    def sources(self) -> tuple[str, ...]:
        return tuple(self._providers)
