import json

from polyarb.cli import main
from polyarb.config import Config
from polyarb.http import cache_key
from polyarb.scanner import scan
from tests.fakes import FakeFetcher, nfl_world


def test_cli_replays_recorded_responses(tmp_path, capsys):
    # Record what a scan requests, in the HttpFetcher(record_dir=...) format.
    fake = FakeFetcher()
    nfl_world(fake)
    scan(Config(leagues=["nfl"]), fake, politics=False)
    for url, params in list(fake.calls):
        try:
            data = fake.get_json(url, params)
        except KeyError:
            continue  # unrecorded -> replay raises -> scanner falls back to top-of-book
        (tmp_path / f"{cache_key(url, params)}.json").write_text(json.dumps({"url": url, "params": params, "data": data}))

    out_json = tmp_path / "out.json"
    rc = main(["--replay", str(tmp_path), "--sports-only", "--leagues", "nfl", "--mode", "arb",
               "--show-matches", "--json", str(out_json), "--csv", str(tmp_path / "out.csv")])
    assert rc == 0
    text = capsys.readouterr().out
    assert "HEDGED ARB" in text and "tokBUF" in text
    rows = json.loads(out_json.read_text())
    assert rows[0]["kind"] == "hedged_arb" and rows[0]["contracts"] == 150
    assert (tmp_path / "out.csv").read_text().startswith("kind,")


def test_config_file_overrides(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"min_arb_edge": 0.02, "kalshi_fees": {"series_rates": {"KXINX": 0.035}}}))
    cfg = Config.load(p)
    assert cfg.min_arb_edge == 0.02 and cfg.kalshi_fees.rate_for("KXINX-X") == 0.035
    p.write_text(json.dumps({"typo_key": 1}))
    try:
        Config.load(p)
    except ValueError as exc:
        assert "typo_key" in str(exc)
    else:
        raise AssertionError("unknown keys must be rejected")
