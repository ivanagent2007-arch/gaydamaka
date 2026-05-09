import hashlib
import hmac
from urllib.parse import urlencode

import config


def test_validate_init_data_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "BOT_TOKEN", "test_token")
    user_json = '{"id":42,"first_name":"A","username":"u"}'
    fields = {"user": user_json, "auth_date": "9999999999", "query_id": "qq"}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(
        b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    hsh = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    init = urlencode({**fields, "hash": hsh})

    from utils.webapp import validate_init_data

    out = validate_init_data(init)
    assert out["telegram_id"] == 42
