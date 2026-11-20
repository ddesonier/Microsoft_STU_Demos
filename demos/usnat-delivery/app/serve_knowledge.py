"""Production entry point for the public Knowledge Base frontend."""

import os

from waitress import serve

from knowledge_webapp import app

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    threads = int(os.environ.get("WEB_THREADS", "4"))
    serve(app, host=host, port=port, threads=threads)
