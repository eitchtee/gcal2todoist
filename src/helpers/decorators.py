import functools
import logging
from time import sleep

logger = logging.getLogger()


def retry(tries: int = 15, retry_on: list[Exception] | None = None, delay: int = 60):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for _ in range(tries):
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as err:
                    if (retry_on and err in retry_on) or (not retry_on):
                        logger.error(
                            "Error when comunicating with Todoist", exc_info=True
                        )
                        if delay:
                            logger.info(f"Retrying in {delay} seconds")
                            sleep(delay)
                    else:
                        raise err

        return wrapper

    return decorator
