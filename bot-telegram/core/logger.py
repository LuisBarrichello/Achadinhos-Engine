import logging
import sys
import re


class SecretRedactionFormatter(logging.Formatter):
    """Filtra e mascara credenciais, tokens e chaves privadas nos logs."""

    # Regex para capturar padrões de Tokens, Bearers e Hashs longos
    SECRET_PATTERN = re.compile(
        r'(Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*)|'
        r'([a-zA-Z0-9]{32,})'  # Qualquer hash gigante solto
    )

    def format(self, record):
        msg = super().format(record)
        # Substitui matches por [REDACTED]
        return self.SECRET_PATTERN.sub('[REDACTED_SECRET]', msg)


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        # Adiciona o formatador seguro
        formatter = SecretRedactionFormatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger