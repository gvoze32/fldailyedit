"""Integration-style coverage for the scrape-to-roster planning pipeline."""

from argparse import Namespace
from types import SimpleNamespace

from editor.models import TeamData
from scraper.models import Transfer


def test_cmd_run_dry_run_resolves_stale_loan_chain(monkeypatch, tmp_path, capsys):
    import run

    psg, tottenham, juventus = 114, 179, 120
    player_id = 115254
    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"test")
    decrypted = tmp_path / "decrypted"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"test")

    transfers = [
        Transfer(
            "Randal Kolo Muani",
            "PSG",
            "Tottenham",
            date="2025-09-01T19:27:00Z",
            transfer_type="loan",
            is_loan=True,
            from_club_full_name="Paris Saint-Germain",
            to_club_full_name="Tottenham Hotspur",
        ),
        Transfer(
            "Randal Kolo Muani",
            "PSG",
            "Juventus",
            date="2026-08-02T18:40:10Z",
            from_club_full_name="Paris Saint-Germain",
            to_club_full_name="Juventus FC",
        ),
    ]

    class FakeEditFile:
        def load(self, _):
            return None

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def get_all_players(self):
            return {
                player_id: SimpleNamespace(
                    name="Randal Kolo Muani",
                    position="CF",
                    nationality="France",
                    age=27,
                )
            }

        def get_all_team_info(self):
            return {
                psg: SimpleNamespace(name="Paris Saint-Germain"),
                tottenham: SimpleNamespace(name="Tottenham Hotspur"),
                juventus: SimpleNamespace(name="Juventus FC"),
            }

        def get_club_team_ids(self):
            return {psg, tottenham, juventus}

        def get_all_rosters(self):
            return {
                psg: TeamData(psg),
                tottenham: TeamData(tottenham, [player_id] + [0] * 39),
                juventus: TeamData(juventus),
            }

        def find_overflow_release_candidate(self, *_, **__):
            return 39, 0

        def get_player_shirt_number(self, *_):
            return None

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run.crypto, "decrypt", lambda _: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _: None)
    monkeypatch.setattr(
        run,
        "fetch_transfers_for_club_names",
        lambda *_, **__: transfers,
    )
    monkeypatch.setattr(run.transfer_logger, "read_log", lambda *_, **__: [])

    run.cmd_run(Namespace(
        dry_run=True,
        edit_file=str(edit_path),
        threshold=80,
        output=None,
        in_place=False,
        popular=False,
        window="auto",
        since=None,
        club="Paris Saint-Germain,Tottenham Hotspur,Juventus",
        deep=False,
        allow_overflow_release=False,
    ))

    output = capsys.readouterr().out
    assert "ALREADY CURRENT" in output
    assert "PSG → Juventus" in output
    assert "WOULD MOVE" in output
    assert "safety-skipped: 0" in output


def test_scheduler_survives_fail_closed_run(monkeypatch, capsys):
    import run

    attempts = []

    def abort_once(_):
        attempts.append(1)
        raise SystemExit(2)

    monkeypatch.setattr(run, "cmd_run", abort_once)
    monkeypatch.setattr(
        run.time,
        "sleep",
        lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    run.cmd_schedule(Namespace(interval_hours=1))

    assert len(attempts) == 1
    assert "aborted safely" in capsys.readouterr().out
