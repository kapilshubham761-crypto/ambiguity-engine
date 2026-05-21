import logging
import os
from datetime import date

_LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')


def get_logger(name: str) -> logging.Logger:
    os.makedirs(_LOG_DIR, exist_ok=True)
    log_path = os.path.join(_LOG_DIR, f"{date.today().isoformat()}.log")

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter('%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
                            datefmt='%H:%M:%S')
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
