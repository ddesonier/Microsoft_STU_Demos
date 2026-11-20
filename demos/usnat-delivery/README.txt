USNat delivery bundle
=====================

Target runtime:
  CPython 3.13 on Linux x86_64
  manylinux2014-compatible platform

Python dependencies:
  wheelhouse/ contains all direct and transitive dependencies.
  requirements.lock.txt pins every package and SHA-256 hash.
  requirements.txt forces offline, hash-verified installation.

Offline installation:
  chmod +x install-offline.sh
  ./install-offline.sh

Microsoft Learn URL conversion:
  Set CONTENT_SOURCE_HOST to the host represented by the input sitemap:
    export CONTENT_SOURCE_HOST=<APPROVED-SOURCE-HOST>

  python app/usnat_url_converter.py \
    --sitemap-index https://<APPROVED-SOURCE-HOST>/_sitemaps/sitemapindex.xml \
    --target-origin https://<USNAT-LEARN-HOST> \
    --output usnat-url-map.csv

  The converter corrects a duplicated https:// prefix, recursively traverses
  the root sitemap index and every nested sitemap, and writes every unique
  source URL with its USNat equivalent to CSV. Paths, queries, and fragments
  are preserved.

  To limit conversion to Azure English pages:
    python app/usnat_url_converter.py \
      --target-origin https://<USNAT-LEARN-HOST> \
      --sitemap-index https://<APPROVED-SOURCE-HOST>/_sitemaps/sitemapindex.xml \
      --sitemap-filter azure_en-us \
      --include-prefix https://<APPROVED-SOURCE-HOST>/en-us/azure/ \
      --output usnat-azure-url-map.csv

Bulk Microsoft Learn indexing:
  Sign in to the Admin application and use "Bulk sitemap crawl". The deployment
  requires an explicit approved content-source origin and derives the sitemap
  and allowed Azure prefix from that origin. It selects Azure English
  sub-sitemaps, resumes from pages already stored in Blob Storage, and
  batch-uploads changed pages to Search.

  To broaden the scope to all English Microsoft Learn products, use:
    Child sitemap filter:  en-us
    Allowed URL prefix:    https://<APPROVED-SOURCE-HOST>/en-us/

  Keep a non-empty sitemap filter and URL prefix. The Admin maximum is 250,000
  pages and concurrency is capped at 20 to avoid overwhelming Microsoft Learn
  or the embedding service. Bulk jobs run in the background and can be resumed
  because successfully crawled pages are persisted in Blob Storage.

Retrieval quality:
  Extractor version 2 preserves H1-H4 section hierarchy and anchors. Search
  indexes approximately 875-token chunks with overlap, page/section/product
  metadata, semantic ranking, and 1,024-dimension embeddings in
  msdocs-content-v2. Existing flattened Blob pages can be indexed immediately
  with fixed-size fallback chunks; recrawling upgrades them to heading-aware
  chunks. The Admin page provides "Index stored pages as chunks" for this
  side-by-side rebuild.
  Blob page reads use bounded parallel prefetch (12 workers by default) so
  indexing can sustain the configured embedding throughput.

Air-gap content transfer:
  python app/content_bundle.py export --output mslearn-content.jsonl.gz
  python app/content_bundle.py import \
    --input mslearn-content.jsonl.gz \
    --target-origin https://<USNAT-LEARN-HOST>

  CONTENT_SOURCE_HOST must identify the source host used in the exported
  documents before running import.

Azure deployment:
  infra/deploy.ps1 includes the wheelhouse and lock files in the App Service
  ZIP package. Oryx installs only from the bundled wheels and does not contact
  PyPI.
  The deployment provisions both the embedding and chat model deployments,
  configures grounded answers, and enables Always On for background crawl jobs.

Before deploying in USNat:
  1. Configure Azure CLI for the authorized USNat cloud environment.
  2. Obtain regions, endpoint suffixes, token audiences, API versions, model
     versions/SKUs, and the approved content-source origin from the USNat team.
  3. Select Standard or DataZone Standard for text-embedding-3-large v1.
  4. Confirm Python 3.13 App Service runtime availability. If unavailable,
     rebuild this wheelhouse for the supported Python version.
  5. Supply Entra credentials at deployment time. No secrets are included.
  6. Run infra/deploy.ps1 with explicit cloud values. Commercial-cloud defaults
     are intentionally not present:

       .\infra\deploy.ps1 `
         -Location <APP-REGION> `
         -SearchLocation <SEARCH-REGION> `
         -AiServicesLocation <AI-REGION> `
         -SearchEndpointSuffix <USNAT-SEARCH-DNS-SUFFIX> `
         -AiServicesEndpointSuffix <USNAT-AI-DNS-SUFFIX> `
         -SearchAudience https://<USNAT-SEARCH-AUDIENCE> `
         -StorageAudience https://<USNAT-STORAGE-AUDIENCE> `
         -CognitiveServicesTokenScope https://<USNAT-AI-AUDIENCE>/.default `
         -ContentSourceOrigin https://<APPROVED-SOURCE-HOST> `
         -SearchSkuName <SEARCH-SKU> `
         -AppServicePlanSku <APP-SERVICE-SKU> `
         -EmbeddingDeploymentSku <EMBEDDING-SKU> `
         -EmbeddingDeploymentCapacity <EMBEDDING-CAPACITY> `
         -ChatDeploymentSku <CHAT-SKU> `
         -ChatDeploymentCapacity <CHAT-CAPACITY> `
         -ChatModelName <CHAT-MODEL> `
         -ChatModelVersion <CHAT-MODEL-VERSION> `
         -McpApiVersion <MCP-API-VERSION> `
         -AoaiApiVersion <AZURE-OPENAI-API-VERSION>

  The deployment derives the ARM, Microsoft Graph, and Entra authority endpoints
  from the active `az cloud` profile. Use -ResourceManagerEndpoint,
  -MicrosoftGraphEndpoint, or -EntraAuthorityHost only when the USNat team
  requires an explicit override.

Security:
  No API keys, client secrets, access tokens, tenant IDs, subscription IDs,
  resource endpoints, or account names are included in this bundle. The
  deployment creates a random Flask secret and obtains/creates the Entra client
  secret at deployment time. Azure CLI errors are sanitized so secret-bearing
  command arguments are not echoed.
