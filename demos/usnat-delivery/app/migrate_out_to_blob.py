"""One-time migration: upload existing app/out/*.json crawl artifacts to the
crawled-pages Blob Storage container, byte-for-byte (no re-parsing), so the
pre-existing local crawl history isn't lost now that test_crawl.py and
search_index.py read and write exclusively through blob_store.py.

Run this once, from a context with network access to the storage account's
private endpoint (see blob_store.py's module docstring for the VNet/private
endpoint details) -- e.g. a VM or Cloud Shell connected to
vnet-msdocs-search-app, or the App Service itself once VNet-integrated.
After a successful run you can delete app/out/ locally; it's no longer read
by anything in this app.

    python migrate_out_to_blob.py
"""

import concurrent.futures
import glob
import os

import blob_store

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CONCURRENCY = 16


def upload_one(path: str) -> str:
    name = os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    blob_store.get_container_client().upload_blob(name=name, data=data, overwrite=True)
    return name


def main():
    paths = sorted(glob.glob(os.path.join(OUT_DIR, "*.json")))
    total = len(paths)
    print(f"[migrate] uploading {total} file(s) from {OUT_DIR} to "
          f"blob container '{blob_store.BLOB_CONTAINER_NAME}' ({blob_store.BLOB_ACCOUNT_URL})")

    uploaded = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(upload_one, p): p for p in paths}
        for i, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            path = futures[future]
            try:
                future.result()
                uploaded += 1
            except Exception as e:
                failed += 1
                print(f"[fail] {path}  ({e})")
            if i % 500 == 0 or i == total:
                print(f"[progress] {i}/{total} (failed: {failed})")

    print(f"[migrate] done: uploaded {uploaded}/{total}, failed {failed}")


if __name__ == "__main__":
    main()
