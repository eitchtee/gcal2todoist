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


def keep_running(delay: int, one_shot: bool = False):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if one_shot or not delay:
                return func(*args, **kwargs)

            seconds_slept = delay
            while True:
                if seconds_slept >= delay:
                    try:
                        func(*args, **kwargs)
                    except Exception as e:
                        logger.error(e, exc_info=True)
                    finally:
                        seconds_slept = 0
                else:
                    if seconds_slept == 0:
                        logger.info(f"Running again in {delay} seconds...")

                    sleep(1)
                    seconds_slept += 1

        return wrapper

    return decorator
