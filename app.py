import logging
import os
import threading
import time

import storage
import watcher
import web


def _watcher_loop(config: dict) -> None:
    conn = storage.init_db(config["db_path"])
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

    thread = threading.Thread(target=_watcher_loop, args=(config,), daemon=True)
    thread.start()

    app = web.create_app(config["db_path"])
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
