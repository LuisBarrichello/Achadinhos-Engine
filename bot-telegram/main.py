import asyncio
import logging

from core.config import Config
from worker.garimpeiro import Garimpeiro

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-18s │ %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("garimpeiro")


async def main() -> None:
    Config.validate()
    await Garimpeiro(Config).run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Garimpeiro encerrado (Ctrl+C)")
    except EnvironmentError as exc:
        log.critical(str(exc))
        raise SystemExit(1)
