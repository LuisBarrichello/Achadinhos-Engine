import logging
import sys


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Previne duplicação se rodar múltiplas vezes
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        # Log estruturado chave-valor (fácil de ler no Dozzle e exportar no futuro)
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger