"""Tests for the `soul` CLI.

These call cli.main() directly (it runs asyncio.run internally, so the tests are sync)
against a temp SQLite file, proving state PERSISTS across separate CLI invocations —
which is the whole point of a soul on disk.
"""

from soul_framework import cli


def test_version(capsys):
    """`soul --version` exits 0 and prints the package name."""
    rc = None
    try:
        cli.main(["--version"])
    except SystemExit as e:  # argparse action="version" raises SystemExit(0)
        rc = e.code
    out = capsys.readouterr().out
    assert rc in (0, None)
    assert "soul-framework" in out


def test_no_args_prints_help(capsys):
    """Bare `soul` prints help and exits 0 (no crash)."""
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_lifecycle_persists_across_invocations(tmp_path, capsys):
    """create -> remember -> recall -> boot -> snapshot, each a SEPARATE main() call
    sharing one --db file. Proves persistence."""
    db = str(tmp_path / "maya.db")

    assert cli.main(["create", "Maya", "--db", db, "--ocean", "0.8,0.9,0.6,0.7,0.2"]) == 0
    assert "Created soul 'Maya'" in capsys.readouterr().out

    assert cli.main(["remember", "Maya", "William prefers short answers",
                     "--db", db, "--importance", "8"]) == 0
    assert "Remembered" in capsys.readouterr().out

    # Fresh invocation must find the memory stored by the previous one.
    assert cli.main(["recall", "Maya", "how should I answer William", "--db", db]) == 0
    assert "short answers" in capsys.readouterr().out

    assert cli.main(["boot", "Maya", "--db", db]) == 0
    boot_out = capsys.readouterr().out
    assert "Maya" in boot_out
    assert "OCEAN" in boot_out  # identity set by create persisted

    assert cli.main(["reflect", "Maya", "I learned to be concise",
                     "--db", db, "--mood", "calm"]) == 0
    assert "Reflected" in capsys.readouterr().out

    assert cli.main(["snapshot", "Maya", "--db", db]) == 0
    snap_out = capsys.readouterr().out
    assert "Maya" in snap_out


def test_remember_without_create_still_works(tmp_path, capsys):
    """remember on a fresh db (no explicit create) works — Soul.create initializes it."""
    db = str(tmp_path / "adhoc.db")
    assert cli.main(["remember", "Bob", "the sky is blue", "--db", db]) == 0
    assert cli.main(["recall", "Bob", "sky color", "--db", db]) == 0
    assert "sky is blue" in capsys.readouterr().out


def test_bad_ocean_count_is_error(tmp_path, capsys):
    """--ocean with the wrong number of values exits 2 (validation error, not a crash)."""
    db = str(tmp_path / "x.db")
    assert cli.main(["create", "X", "--db", db, "--ocean", "0.5,0.5,0.5"]) == 2
    assert "ocean" in capsys.readouterr().err.lower()


def test_bad_ocean_range_is_error(tmp_path, capsys):
    """--ocean values outside 0..1 exit 2."""
    db = str(tmp_path / "y.db")
    assert cli.main(["create", "Y", "--db", db, "--ocean", "1,2,3,4,5"]) == 2
    assert "ocean" in capsys.readouterr().err.lower()


def test_importance_out_of_range_is_error(tmp_path, capsys):
    """--importance outside 1..10 must be rejected (exit 2), not silently stored."""
    db = str(tmp_path / "imp.db")
    rc = None
    try:
        cli.main(["remember", "Maya", "too important", "--db", db, "--importance", "999"])
    except SystemExit as e:  # argparse ArgumentTypeError -> SystemExit(2)
        rc = e.code
    assert rc == 2
    assert "importance" in capsys.readouterr().err.lower()


def test_importance_valid_bounds_accepted(tmp_path, capsys):
    """The documented bounds (1 and 10) are accepted."""
    db = str(tmp_path / "impok.db")
    assert cli.main(["remember", "Maya", "low", "--db", db, "--importance", "1"]) == 0
    assert cli.main(["remember", "Maya", "high", "--db", db, "--importance", "10"]) == 0


def test_recall_empty_reports_no_match(tmp_path, capsys):
    """recall against an empty soul reports no matches (exit 0)."""
    db = str(tmp_path / "empty.db")
    assert cli.main(["create", "Empty", "--db", db]) == 0
    capsys.readouterr()
    assert cli.main(["recall", "Empty", "anything", "--db", db]) == 0
    assert "No memories matched" in capsys.readouterr().out
