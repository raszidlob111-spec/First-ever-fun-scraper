from flask import Flask, jsonify, request, send_from_directory

import counties
import storage


def create_app(db_path: str) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="")

    def get_conn():
        # A fresh short-lived connection per request keeps SQLite happy under
        # concurrent access from Flask's request threads and the watcher's
        # own background thread (both share the same WAL-mode db file).
        return storage.init_db(db_path)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/counties")
    def api_counties():
        return jsonify({"counties": counties.all_counties()})

    @app.get("/api/categories")
    def api_categories():
        conn = get_conn()
        return jsonify({"categories": storage.get_categories(conn)})

    @app.get("/api/models")
    def api_models():
        conn = get_conn()
        rows = storage.get_model_summary(
            conn,
            category_key=request.args.get("category") or None,
            q=request.args.get("q") or None,
        )
        return jsonify({"models": rows})

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
