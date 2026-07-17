"""Web UI and watcher in one process -- the convenient way to run the whole thing
locally with a single command.

Railway does not use this entrypoint. There the two halves are separate services,
because a cron job has to exit and a web server never does: `serve.py` (web, sleeps
between visits) and `watcher.py --once` (cron). See railway.json / railway.watcher.json.
"""
import logging
import os
import threading
import time

import storage
import watcher
import web


def _watcher_loop(config: dict) -> None:
    conn = storage.connect()
    log = logging.getLogger("watcher")
    while True:
        try:
            watcher.run_cycle(conn, config)
        except Exception:
            log.exception("Cycle failed")
        time.sleep(config["interval_minutes"] * 60)


def main() -> None:
    config = watcher.load_config()
    watcher.setup_logging(config["log_path"])

    # Migrate before anything can read: the web thread serves requests immediately
    # and the watcher thread assumes the schema is already in place.
    storage.init_db().close()

    thread = threading.Thread(target=_watcher_loop, args=(config,), daemon=True)
    thread.start()

    app = web.create_app()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
