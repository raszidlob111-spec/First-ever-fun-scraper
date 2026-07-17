import json

from flask import Flask, g, jsonify, request, send_from_directory

import counties
import storage


def _load_category_order(path: str) -> list:
    """The order the category tabs should appear in, taken from config.json.

    seen_ads has no notion of order, so SELECT DISTINCT hands back the categories
    however Postgres feels like it that day. config.json already lists them in the
    intended order (GPU first), which makes it the natural source of truth -- and the
    UI defaults to the first tab, so this decides the landing category too.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [c["key"] for c in json.load(f)["categories"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _order_categories(rows: list, order: list) -> list:
    """Sort rows by `order`; anything not listed keeps a stable spot at the end."""
    def rank(row):
        key = row.get("category_key")
        return (order.index(key) if key in order else len(order), key or "")
    return sorted(rows, key=rank)


def create_app(config_path: str = "config.json") -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="")
    category_order = _load_category_order(config_path)

    def get_conn():
        # One connection per request, closed on teardown. It has to be closed: even a
        # read leaves the connection "idle in transaction", which pins a snapshot --
        # enough of them and Postgres runs out of connections, and meanwhile the open
        # locks stall vacuum and any ALTER TABLE a deploy tries to run.
        # Readers skip the migrations; startup has already run them.
        if "conn" not in g:
            g.conn = storage.connect()
        return g.conn

    @app.teardown_appcontext
    def close_conn(exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/counties")
    def api_counties():
        return jsonify({"counties": counties.all_counties()})

    @app.get("/api/categories")
    def api_categories():
        conn = get_conn()
        cats = _order_categories(storage.get_categories(conn), category_order)
        return jsonify({"categories": cats})

    @app.get("/api/models")
    def api_models():
        conn = get_conn()
        rows = storage.get_model_summary(
            conn,
            category_key=request.args.get("category") or None,
            q=request.args.get("q") or None,
            order=request.args.get("order") or "count",
        )
        return jsonify({"models": rows})

    @app.get("/api/closures")
    def api_closures():
        conn = get_conn()
        rows = storage.get_recent_closures(
            conn,
            category_key=request.args.get("category") or None,
            limit=int(request.args.get("limit", 100)),
        )
        return jsonify({"closures": rows})

    @app.get("/api/listings")
    def api_listings():
        conn = get_conn()
        rows = storage.get_listings(
            conn,
            category_key=request.args.get("category") or None,
            model_key=request.args.get("model") or None,
            county=request.args.get("county") or None,
            q=request.args.get("q") or None,
            limit=int(request.args.get("limit", 300)),
        )
        return jsonify({"listings": rows})

    @app.get("/api/listings/<ad_id>")
    def api_listing_detail(ad_id):
        conn = get_conn()
        listing = storage.get_listing(conn, ad_id)
        if not listing:
            return jsonify({"error": "not found"}), 404

        similar = storage.get_similar_by_county(
            conn, listing["category_key"], listing["model_key"], exclude_ad_id=ad_id
        )
        return jsonify({"listing": listing, "similar": similar})

    return app
