"""
Production entry point for the MS Docs Search web UI.

`python webapp.py` uses Flask's built-in development server, which is single
threaded, auto-reloads on file changes, and explicitly warns it isn't meant
for production. This script instead serves the same Flask app behind
waitress -- a pure-Python, multi-threaded WSGI server with no C-extension
dependencies, which is why it's included in both platform wheelhouses for
the offline/sovereign-cloud install.

Run with:
    python app/serve.py

Environment variables (all optional):
    HOST         interface to bind to (default: 0.0.0.0, all interfaces)
    PORT         port to listen on (default: 5000)
    WEB_THREADS  worker thread count (default: 4)
"""

import os

from waitress import serve

from webapp import app

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    threads = int(os.environ.get("WEB_THREADS", "4"))
    print(f"Serving MS Docs Search on http://{host}:{port} (waitress, {threads} threads)")
    serve(app, host=host, port=port, threads=threads)
