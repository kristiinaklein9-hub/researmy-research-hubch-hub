# EZproxy PDF Access

EZproxy support is opt-in and only affects `paper attach-pdfs`.

1. Configure your institution's EZproxy. Two modes — pick one:

   **Mode A — hostname rewriting (recommended).** Most modern institutions
   rewrite the publisher host (e.g. Lehigh: `www.nature.com` →
   `www-nature-com.ezproxy.lib.lehigh.edu`). Set the EZproxy host suffix:

   ```bash
   research-hub config set ezproxy_host_suffix "ezproxy.lib.lehigh.edu"
   ```

   This is preferred: PDFs are fetched directly from the rewritten host with no
   `/login?qurl=` `EZproxyCheckBack` JavaScript interstitial (which a non-browser
   HTTP client cannot follow, so PDF downloads would otherwise fail). To find
   your suffix, click a publisher link from your library portal and read it off
   the rewritten address bar (everything after the first host label).

   **Mode B — login template (legacy fallback).** Only if your institution does
   not support hostname rewriting. The template must contain `{encoded_url}`:

   ```bash
   research-hub config set ezproxy_url_template "https://login.example.edu/login?qurl={encoded_url}"
   ```

   If both are set, hostname rewriting takes priority.

2. Run the browser login once:

   ```bash
   research-hub ezproxy login
   ```

3. Verify state:

   ```bash
   research-hub ezproxy status
   ```

4. Re-run `paper attach-pdfs`. Paywalled publisher PDF URLs now try the proxy
   first and fall back to the direct URL on any proxy failure.

Cookies usually last 1-4 weeks before your institution requires re-login.
