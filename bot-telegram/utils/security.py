import hmac
import hashlib
import time
import json
from typing import Tuple


def generate_shopee_auth_headers(app_id: str, app_secret: str, payload_dict: dict) -> Tuple[dict, str]:
    """
    Gera os headers de autenticação oficiais para a API GraphQL da Shopee.
    Garante que o payload compactado corresponda exatamente ao hash gerado.
    """
    # 1. Compacta o JSON removendo espaços (crucial para o HMAC bater com o body enviado)
    payload_str = json.dumps(payload_dict, separators=(',', ':'))

    # 2. Gera o Timestamp UNIX
    timestamp = str(int(time.time()))

    # 3. Monta a string base: AppID + Timestamp + Payload
    factor = app_id + timestamp + payload_str

    # 4. Gera a assinatura HMAC-SHA256
    signature = hmac.new(
        key=app_secret.encode('utf-8'),
        msg=factor.encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()

    # 5. Constrói os Headers oficiais
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id}, Timestamp={timestamp}, Signature={signature}"
    }

    return headers, payload_str