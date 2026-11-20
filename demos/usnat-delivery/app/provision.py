"""Idempotently initialize the Search index, knowledge source, and knowledge base."""

import logging
import time

import mcp_client
import search_index

MAX_ATTEMPTS = 30
RETRY_SECONDS = 10


def provision() -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            search_index.ensure_index()
            mcp_client.ensure_knowledge_base()
            logging.info(
                "Azure AI Search index '%s' and knowledge base '%s' are ready",
                search_index.SEARCH_INDEX_NAME,
                mcp_client.KNOWLEDGE_BASE_NAME,
            )
            return
        except Exception:
            if attempt == MAX_ATTEMPTS:
                raise
            logging.exception(
                "Search provisioning attempt %s/%s failed; retrying in %s seconds",
                attempt,
                MAX_ATTEMPTS,
                RETRY_SECONDS,
            )
            time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    provision()
