"""Central logging setup"""

import logging
from pathlib import Path


def configure() -> Path:
    log_path = Path.home() / ".osaeru" / "osaeru.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode="a"),
            logging.StreamHandler()
        ],
    )
    
    return log_path
