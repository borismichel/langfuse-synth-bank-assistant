"""Private process capability, never a browser token or model-controlled argument."""
import base64
import hashlib
import hmac
import json


def sign(binding: dict, secret: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(binding, sort_keys=True).encode()).decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature


def verify(token: str, secret: str) -> dict:
    try:
        payload, supplied = token.split('.', 1)
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(supplied, expected):
            raise ValueError('Invalid banking binding')
        result = json.loads(base64.urlsafe_b64decode(payload))
        if not isinstance(result, dict):
            raise TypeError('Invalid banking binding')
        return result
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid banking binding') from None
