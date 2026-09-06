from __future__ import annotations

from pathlib import Path

import pytest

from local_update import (
    CancellationToken,
    LocalUpdateCancelled,
    LocalUpdateError,
    LocalUpdateProgress,
    LocalUpdateRequest,
    LocalUpdateResult,
    LocalUpdateService,
    LocalUpdateStage,
)
from scraper.models import CaptainUpdate, ScrapeResult


class FakeRuntime:
    def __init__(self, *, transfers=None, cancel_after: str | None = None) -> None:
        self.transfers = ["transfer"] if transfers is None else list(transfers)
        self.cancel_after = cancel_after
        self.apply_calls = 0
        self.publish_calls = 0
        self.preview_calls = 0
        self.cleaned = 0

    def scrape(self, request: LocalUpdateRequest, token: CancellationToken):
        return self.transfers

    def validate_and_prepare(
        self,
        request: LocalUpdateRequest,
        transfers,
        token: CancellationToken,
    ):
        return {"path": request.edit_path}

    def match_and_plan(
        self,
        request: LocalUpdateRequest,
        prepared,
        transfers,
        token: CancellationToken,
    ):
        if self.cancel_after == "matching":
            token.request()
        return {"planned": len(transfers)}

    def apply(
        self,
        request: LocalUpdateRequest,
        prepared,
        plan,
        token: CancellationToken,
    ):
        self.apply_calls += 1
        return {"transfer_applied": 1}

    def verify(
        self,
        request: LocalUpdateRequest,
        prepared,
        mutation,
        token: CancellationToken,
    ) -> None:
        return None

    def publish(
        self,
        request: LocalUpdateRequest,
        prepared,
        mutation,
        token: CancellationToken,
    ) -> LocalUpdateResult:
        self.publish_calls += 1
        return LocalUpdateResult(
            target_path=request.output_path or request.edit_path,
            backup_path=Path("backup/EDIT00000000.bak"),
            installed_sha256="a" * 64,
            transfer_applied=1,
            shirt_numbers_changed=0,
            unchanged=0,
            safety_skipped=0,
        )

    def preview(
        self,
        request: LocalUpdateRequest,
        prepared,
        plan,
        token: CancellationToken,
    ) -> LocalUpdateResult:
        self.preview_calls += 1
        return LocalUpdateResult(
            target_path=request.output_path or request.edit_path,
            backup_path=None,
            installed_sha256=None,
            transfer_applied=0,
            shirt_numbers_changed=0,
            unchanged=0,
            safety_skipped=0,
            no_changes=True,
        )

    def cleanup(self, prepared) -> None:
        self.cleaned += 1


def test_cancel_token_rejects_cancellation_after_commit() -> None:
    token = CancellationToken()
    assert token.request() is True
    assert token.requested is True

    token = CancellationToken()
    token.mark_commit_started()
    assert token.request() is False
    assert token.requested is False
    assert token.commit_started is True


def test_service_reports_ordered_progress_and_returns_result() -> None:
    events: list[LocalUpdateProgress] = []
    service = LocalUpdateService(FakeRuntime())

    result = service.execute(
        LocalUpdateRequest(Path("save/EDIT00000000")),
        progress=events.append,
    )

    assert [event.stage for event in events] == [
        LocalUpdateStage.SCRAPING,
        LocalUpdateStage.VALIDATING,
        LocalUpdateStage.MATCHING,
        LocalUpdateStage.APPLYING,
        LocalUpdateStage.VERIFYING,
        LocalUpdateStage.ENCRYPTING,
        LocalUpdateStage.COMPLETE,
    ]
    assert result.target_path == Path("save/EDIT00000000")
    assert result.transfer_applied == 1



def test_service_reports_scrape_failures_with_scraping_stage() -> None:
    class ScrapeFailureRuntime(FakeRuntime):
        def scrape(self, request, token):
            raise RuntimeError("deep-club index is empty")

    service = LocalUpdateService(ScrapeFailureRuntime())

    with pytest.raises(LocalUpdateError) as caught:
        service.execute(LocalUpdateRequest(Path("save/EDIT00000000")))

    assert caught.value.code == "scrape_failed"
    assert caught.value.stage is LocalUpdateStage.SCRAPING
    assert "deep-club index is empty" in str(caught.value)

def test_service_stops_before_apply_when_cancelled() -> None:
    token = CancellationToken()
    runtime = FakeRuntime(cancel_after="matching")
    service = LocalUpdateService(runtime)

    with pytest.raises(LocalUpdateCancelled):
        service.execute(
            LocalUpdateRequest(Path("save/EDIT00000000")),
            token=token,
        )

    assert runtime.apply_calls == 0
    assert runtime.publish_calls == 0
    assert runtime.cleaned == 1


def test_empty_scrape_returns_without_backup_or_publish() -> None:
    runtime = FakeRuntime(transfers=[])
    service = LocalUpdateService(runtime)

    result = service.execute(LocalUpdateRequest(Path("save/EDIT00000000")))

    assert result.no_changes is True
    assert result.backup_path is None
    assert runtime.apply_calls == 0
    assert runtime.publish_calls == 0
    assert runtime.cleaned == 0

def test_captain_only_scrape_is_not_treated_as_empty() -> None:
    class CaptainOnlyRuntime(FakeRuntime):
        def scrape(self, request, token):
            return ScrapeResult(
                [],
                [
                    CaptainUpdate(
                        club_name="Example FC",
                        team_id_fotmob=42,
                        player_name="Captain Player",
                        player_id_fotmob=987,
                    )
                ],
            )

        def apply(self, request, prepared, plan, token):
            self.apply_calls += 1
            return {"captains_changed": 1}

        def publish(self, request, prepared, mutation, token):
            self.publish_calls += 1
            return LocalUpdateResult(
                target_path=request.target_path,
                backup_path=Path("backup/EDIT00000000.bak"),
                installed_sha256="a" * 64,
                transfer_applied=0,
                shirt_numbers_changed=0,
                unchanged=0,
                safety_skipped=0,
                captains_changed=1,
            )

    runtime = CaptainOnlyRuntime()
    result = LocalUpdateService(runtime).execute(
        LocalUpdateRequest(Path("save/EDIT00000000"))
    )

    assert result.no_changes is False
    assert result.captains_changed == 1
    assert runtime.apply_calls == 1
    assert runtime.publish_calls == 1


def test_dry_run_uses_preview_without_mutating() -> None:
    runtime = FakeRuntime()
    service = LocalUpdateService(runtime)

    result = service.execute(
        LocalUpdateRequest(Path("save/EDIT00000000"), dry_run=True)
    )

    assert result.no_changes is True
    assert runtime.preview_calls == 1
    assert runtime.apply_calls == 0
    assert runtime.publish_calls == 0
    assert runtime.cleaned == 1


def test_no_actionable_plan_skips_verify_and_publish() -> None:
    class NoChangeRuntime(FakeRuntime):
        def apply(self, request, prepared, plan, token):
            self.apply_calls += 1
            return LocalUpdateResult(
                target_path=request.target_path,
                backup_path=None,
                installed_sha256=None,
                transfer_applied=0,
                shirt_numbers_changed=0,
                unchanged=2,
                safety_skipped=1,
                no_changes=True,
            )

    runtime = NoChangeRuntime()
    service = LocalUpdateService(runtime)

    result = service.execute(LocalUpdateRequest(Path("save/EDIT00000000")))

    assert result.no_changes is True
    assert runtime.apply_calls == 1
    assert runtime.publish_calls == 0


def test_cancellation_requested_during_commit_does_not_abort_publish() -> None:
    class CommitRuntime(FakeRuntime):
        def publish(self, request, prepared, mutation, token):
            assert token.request() is False
            return super().publish(request, prepared, mutation, token)

    token = CancellationToken()
    runtime = CommitRuntime()
    service = LocalUpdateService(runtime)
    result = service.execute(
        LocalUpdateRequest(Path("save/EDIT00000000")),
        token=token,
    )

    assert result.transfer_applied == 1
    assert runtime.publish_calls == 1
    assert token.commit_started is True
