from hub.bot import telegram


def test_urls():
    assert telegram.api_url("T", "sendMessage") == "https://api.telegram.org/botT/sendMessage"
    assert telegram.file_url("T", "a/b.jpg") == "https://api.telegram.org/file/botT/a/b.jpg"


def test_text_payload():
    p = telegram.text_payload(1, "hi")
    assert p == {"chat_id": 1, "text": "hi", "parse_mode": "HTML"}
    p = telegram.text_payload(1, "hi", {"k": 1}, message_id=5)
    assert p["reply_markup"] == {"k": 1} and p["message_id"] == 5
