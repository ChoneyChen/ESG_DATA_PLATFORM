#!/bin/sh
# Install CrawlESG dependencies outside the protected CrawlESG tree and print
# the read-only commands the console expects. This script never edits CrawlESG,
# never writes its index, and never imports a package into the PDF chain.
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
workspace_root=$(CDPATH= cd -- "$repo_root/.." && pwd)
venv="$workspace_root/.local/crawl-venv"
work="$workspace_root/.local/crawl-work"

mkdir -p "$workspace_root/.local" "$work/packages"
if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv"
fi
"$venv/bin/python" -m pip install --disable-pip-version-check --no-input -r "$repo_root/CrawlESG/requirements.txt"

echo "CrawlESG environment: $venv"
echo "Index (read-only):    $repo_root/CrawlESG/index/gb_filings_primary.csv"
echo "Download work dir:    $work"
echo
echo "Plan (no download):"
echo "  $venv/bin/python $repo_root/CrawlESG/src/esef_crawler.py plan --index $repo_root/CrawlESG/index/gb_filings_primary.csv --years 2024 --out-dir $work --pkg-dir $work/packages"
echo
echo "Download one package:"
echo "  $venv/bin/python $repo_root/CrawlESG/src/esef_crawler.py download --index $repo_root/CrawlESG/index/gb_filings_primary.csv --years 2024 --limit 1 --yes --out-dir $work --pkg-dir $work/packages"
echo
echo "The console reads $work/download_ledger.csv and $work/packages through the read-only CrawlESG adapter."
