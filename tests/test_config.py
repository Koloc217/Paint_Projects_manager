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




def test_wrong_types_never_crash_the_loader(client, capsys):
    # a list or a dict where a string is expected used to raise TypeError and take the whole app down
    cfg = _load_raw({"DATE_FORMAT": [], "LANGUAGE": {"a": 1}, "TG_CLEANUP": ["off"], "NOTIFY": [], "ICS_FEED": "x",
                     "DEVICE_API": {"hot_days": 5000, "key": 7}})
    assert cfg["DATE_FORMAT"] == "dmy" and cfg["LANGUAGE"] == "ru-RU" and cfg["TG_CLEANUP"] == "keep_46h"
    assert cfg["NOTIFY"]["stagnation_days"] == 10 and cfg["ICS_FEED"]["enabled"] is False
    assert cfg["DEVICE_API"]["hot_days"] == 3 and cfg["DEVICE_API"]["key"] == ""
    assert "[CONFIG]" in capsys.readouterr().out


def _settings(**fields) -> dict:
    return {"dev_mode": False, "project_cost": 10, **fields}


def test_dev_mode_switch_is_announced_in_the_bot_once(client, config, monkeypatch):
    from hub.jobs import notify
    sent = []
    monkeypatch.setattr(notify, "BOT_TOKEN", "x")
    monkeypatch.setattr(notify, "send_tg_message", lambda chat, text, markup=None, kind="digest": sent.append((chat, text, kind)) or True)
    config(TG_CHAT_ID="42")
    assert client.post("/api/config", json=_settings(dev_mode=True)).json()["status"] == "ok"
    assert len(sent) == 1 and sent[0][0] == "42" and "DEV MODE" in sent[0][1] and sent[0][2] == "bot"
    client.post("/api/config", json=_settings(dev_mode=True, project_cost=12))        # saved again, still on: silence
    assert len(sent) == 1
    client.post("/api/config", json=_settings(dev_mode=False))
    assert len(sent) == 2


def test_dev_mode_switch_without_a_linked_chat_sends_nothing(client, monkeypatch):
    from hub.jobs import notify
    sent = []
    monkeypatch.setattr(notify, "BOT_TOKEN", "x")
    monkeypatch.setattr(notify, "send_tg_message", lambda *a, **k: sent.append(a) or True)
    assert client.post("/api/config", json=_settings(dev_mode=True)).json()["status"] == "ok"
    assert sent == []
