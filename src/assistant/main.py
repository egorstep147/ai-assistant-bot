"""Точка входа: `assistant [run|migrate]`."""

import argparse
import logging


def run() -> None:
    import uvicorn

    from assistant.app import create_app
    from assistant.config import settings

    uvicorn.run(create_app(), host=settings.host, port=settings.port, proxy_headers=True, log_level="info")


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    from assistant.config import ROOT

    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx пишет каждую строку запроса к Telegram и моделям — шумно, и в URL бывает токен
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="assistant", description="ИИ-ассистент в Telegram")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "migrate"])
    {"run": run, "migrate": migrate}[parser.parse_args().command]()
