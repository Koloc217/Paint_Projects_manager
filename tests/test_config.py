"""config.json: invalid values are replaced by defaults and reported, one line per value."""
import json

from hub.core import appconfig


def _load_raw(cfg: dict):
    with open(appconfig.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    appconfig._config_cache["stamp"] = None
    return appconfig.load_config()


def test_bad_values_fall_back_and_are_reported(client, capsys):
    cfg = _load_raw({
        "DEV_MODE": "yes", "PROJECT_COST": "ten", "INITIAL_TOKENS": 15.0, "TRASH_DAYS": 0,
        "DATE_FORMAT": "ymd", "TG_CLEANUP": "always", "ASK_SPENT_TIME": 1,
        "NOTIFY": {"stagnation_days": 500, "friday_digest": "no"},
        "SCHEDULE": {"notify_enabled": "yes", "notify_days": {"mon": {"enabled": True, "time": "7pm"}}},
    })
    assert cfg["DEV_MODE"] is False and cfg["PROJECT_COST"] == 10 and cfg["INITIAL_TOKENS"] == 15
    assert cfg["TRASH_DAYS"] == 30 and cfg["DATE_FORMAT"] == "dmy" and cfg["TG_CLEANUP"] == "keep_46h"
    assert cfg["ASK_SPENT_TIME"] is True
    assert cfg["NOTIFY"]["stagnation_days"] == 10 and cfg["NOTIFY"]["friday_digest"] is True
    assert cfg["SCHEDULE"]["notify_enabled"] is True
    assert cfg["SCHEDULE"]["notify_days"]["mon"]["time"] == "18:05"
    out = capsys.readouterr().out
    for key in ("DEV_MODE", "PROJECT_COST", "TRASH_DAYS", "DATE_FORMAT", "TG_CLEANUP", "ASK_SPENT_TIME",
                "NOTIFY.stagnation_days", "NOTIFY.friday_digest", "SCHEDULE.notify_enabled",
                "SCHEDULE.notify_days.mon.time"):
        assert f"config.json: {key} = " in out, key
    assert "INITIAL_TOKENS" not in out                 # 15.0 -> 15 is a silent clean-up
    assert '"7pm" is invalid, using "18:05"' in out


def test_good_config_is_silent(client, capsys):
    _load_raw({"DEV_MODE": True, "PROJECT_COST": 5, "LANGUAGE": "en-US"})
    assert "[CONFIG]" not in capsys.readouterr().out


def test_reported_once_per_file_change(client, capsys):
    _load_raw({"TRASH_DAYS": -1})
    appconfig.load_config()
    appconfig.load_config()
    assert capsys.readouterr().out.count("TRASH_DAYS") == 1


def test_old_backup_settings_are_dropped(client, capsys):
    cfg = _load_raw({"SCHEDULE": {"backup_enabled": True, "backup_time": "03:00"}})
    assert "backup_enabled" not in cfg["SCHEDULE"] and "backup_time" not in cfg["SCHEDULE"]
    assert "[CONFIG]" not in capsys.readouterr().out


def test_wrong_types_never_crash_the_loader(client, capsys):
    # a list or a dict where a string is expected used to raise TypeError and take the whole app down
    cfg = _load_raw({"DATE_FORMAT": [], "LANGUAGE": {"a": 1}, "TG_CLEANUP": ["off"], "NOTIFY": [], "ICS_FEED": "x",
                     "DEVICE_API": {"hot_days": 5000, "key": 7}})
    assert cfg["DATE_FORMAT"] == "dmy" and cfg["LANGUAGE"] == "ru-RU" and cfg["TG_CLEANUP"] == "keep_46h"
    assert cfg["NOTIFY"]["stagnation_days"] == 10 and cfg["ICS_FEED"]["enabled"] is False
    assert cfg["DEVICE_API"]["hot_days"] == 3 and cfg["DEVICE_API"]["key"] == ""
    assert "[CONFIG]" in capsys.readouterr().out
