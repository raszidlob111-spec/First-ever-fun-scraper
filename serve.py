"""Web UI entrypoint, without the watcher.

Split out from app.py so the two halves can be deployed as separate Railway
services: this one is a long-running web server that may sleep between visits,
while the watcher is a cron job that runs a cycle and exits. Railway can't do
both in one service -- a cron service has to terminate, and a web server never
does. app.py still runs both together for local use.
"""
import logging
import os
import sys

import storage
import web


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)

    # Bring the schema up to date before serving. The watcher migrates too, but on a
    # brand-new database whichever service starts first has to do it, or the API
    # answers 500 until the first cron run lands.
    storage.init_db().close()

    port = int(os.environ.get("PORT", 8000))
    web.create_app().run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
