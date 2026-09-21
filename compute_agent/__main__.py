"""Entry-Point: python -m compute_agent"""

import logging

import uvicorn

from . import config

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    uvicorn.run("compute_agent.main:app", host=config.HOST, port=config.PORT,
                log_level="info")
