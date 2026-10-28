"""Frontend-independent update orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

from cuc_providers import ProviderRegistry


ResultCallback = Callable[[str, Any], None]
ErrorResultFactory = Callable[[str, str, str], Any]


@dataclass(slots=True)
class ExecutionOutcome:
    """Normalized result from one update-source command."""

    source: str
    status: str
    message: str
    exit_code: int | None = None


CommandRunner = Callable[[str, list[str]], ExecutionOutcome]


class UpdateExecutor:
    """Apply dependency-aware continuation policy to desktop installs."""

    dependencies: dict[str, set[str]] = {}
    stop_statuses = {"cancelled", "timed-out"}

    def execute(
        self,
        plan: Iterable[tuple[str, list[str]]],
        runner: CommandRunner,
    ) -> list[ExecutionOutcome]:
        entries = [(source, list(command)) for source, command in plan]
        outcomes: list[ExecutionOutcome] = []
        failed_sources: set[str] = set()

        for index, (source, command) in enumerate(entries):
            blockers = self.dependencies.get(source, set()) & failed_sources
            if blockers:
                outcomes.append(
                    ExecutionOutcome(
                        source,
                        "skipped",
                        "Skipped because a required source failed: "
                        + ", ".join(sorted(blockers))
                        + ".",
                    )
                )
                continue

            try:
                outcome = runner(source, command)
            except Exception as exc:  # runners are a frontend boundary
                outcome = ExecutionOutcome(source, "failed", f"Unexpected failure: {exc}")
            outcomes.append(outcome)

            if outcome.status != "succeeded":
                failed_sources.add(source)
            if outcome.status in self.stop_statuses:
                reason = "cancellation" if outcome.status == "cancelled" else "timeout"
                outcomes.extend(
                    ExecutionOutcome(remaining_source, "skipped", f"Skipped after {reason}.")
                    for remaining_source, _remaining_command in entries[index + 1 :]
                )
                break

        return outcomes


class UpdateController:
    """Run provider checks and build update plans without GTK code."""

    def __init__(
        self,
        providers: ProviderRegistry,
        error_result_factory: ErrorResultFactory,
    ) -> None:
        self.providers = providers
        self.error_result_factory = error_result_factory

    def check(
        self,
        config: Any,
        *,
        sources: Iterable[str] | None = None,
        prior_results: dict[str, Any] | None = None,
        on_result: ResultCallback | None = None,
    ) -> dict[str, Any]:
        requested = set(sources) if sources is not None else set(self.providers)
        selected = [
            source
            for source in self.providers
            if source in requested and config.enabled_sources.get(source, True)
        ]
        results = dict(prior_results or {})
        for provider in self.providers.values():
            if not config.enabled_sources.get(provider.source, True):
                results[provider.source] = provider.disabled_result()

        with ThreadPoolExecutor(max_workers=max(1, len(selected))) as executor:
            futures = {
                executor.submit(self.providers[source].check, config): source
                for source in selected
            }
            for future in as_completed(futures):
                source = futures[future]
                provider = self.providers[source]
                try:
                    result = future.result()
                except Exception as exc:
                    result = self.error_result_factory(
                        source,
                        provider.display_name,
                        str(exc),
                    )
                result.checked_at = datetime.now().isoformat(timespec="seconds")
                results[source] = result
                if on_result:
                    on_result(source, result)

        return {
            source: results[source]
            for source in self.providers
            if source in results
        }

    def retry_sources(self, results: dict[str, Any]) -> list[str]:
        return [
            source
            for source, result in results.items()
            if result.enabled and result.available_on_system and result.error
        ]

    def build_plan(
        self,
        targets: Iterable[str],
        config: Any,
        *,
        noninteractive: bool = False,
    ) -> list[tuple[str, list[str] | None]]:
        return [
            (
                source,
                self.providers[source].build_command(config, noninteractive),
            )
            for source in targets
            if source in self.providers
        ]
