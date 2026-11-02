"""config.json: invalid values are replaced by defaults and reported, one line per value."""
import json

import common


def _load_raw(cfg: dict):
    with open(common.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    common._config_cache["stamp"] = None
    return common.load_config()


def test_bad_values_fall_back_and_are_reported(client, capsys):
    cfg = _load_raw({
        "DEV_MODE": "yes", "PROJECT_COST": "ten", "INITIAL_TOKENS": 15.0, "TRASH_DAYS": 0,
        "DATE_FORMAT": "ymd", "TG_CLEANUP": "always", "ASK_SPENT_TIME": 1,
        "NOTIFY": {"stagnation_days": 500, "friday_digest": "no"},
        "SCHEDULE": {"backup_time": "25:00", "notify_days": {"mon": {"enabled": True, "time": "7pm"}}},
    })
    assert cfg["DEV_MODE"] is False and cfg["PROJECT_COST"] == 10 and cfg["INITIAL_TOKENS"] == 15
    assert cfg["TRASH_DAYS"] == 30 and cfg["DATE_FORMAT"] == "dmy" and cfg["TG_CLEANUP"] == "keep_46h"
    assert cfg["ASK_SPENT_TIME"] is True
    assert cfg["NOTIFY"]["stagnation_days"] == 10 and cfg["NOTIFY"]["friday_digest"] is True
    assert cfg["SCHEDULE"]["backup_time"] == "03:00"
    assert cfg["SCHEDULE"]["notify_days"]["mon"]["time"] == "18:05"
    out = capsys.readouterr().out
    for key in ("DEV_MODE", "PROJECT_COST", "TRASH_DAYS", "DATE_FORMAT", "TG_CLEANUP", "ASK_SPENT_TIME",
                "NOTIFY.stagnation_days", "NOTIFY.friday_digest", "SCHEDULE.backup_time",
                "SCHEDULE.notify_days.mon.time"):
        assert f"config.json: {key} = " in out, key
    assert "INITIAL_TOKENS" not in out                 # 15.0 -> 15 is a silent clean-up
    assert '"7pm" is invalid, using "18:05"' in out


def test_good_config_is_silent(client, capsys):
    _load_raw({"DEV_MODE": True, "PROJECT_COST": 5, "LANGUAGE": "en-US"})
    assert "[CONFIG]" not in capsys.readouterr().out


def test_reported_once_per_file_change(client, capsys):
    _load_raw({"TRASH_DAYS": -1})
    common.load_config()
    common.load_config()
    assert capsys.readouterr().out.count("TRASH_DAYS") == 1
