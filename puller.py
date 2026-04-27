import requests
import time
import os

# GitHub personal access token - required for higher rate limits
# Create one at: https://github.com/settings/tokens (no special scopes needed)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    **({"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}),
}

OUTPUT_FILE = "threading_repos.txt"

def search_repos(page: int) -> list[dict]:
    """Fetch one page (100 repos) from the GitHub search API."""
    url = "https://api.github.com/search/repositories"
    params = {
        # Repos that mention "threading" and are written in Python
        "q": "language:python threading in:code",
        "sort": "stars",
        "order": "desc",
        "per_page": 100,
        "page": page,
    }
    resp = requests.get(url, headers=HEADERS, params=params)

    # Surface rate-limit errors clearly
    if resp.status_code == 403:
        reset = resp.headers.get("X-RateLimit-Reset", "unknown")
        raise RuntimeError(f"Rate limited. Resets at unix timestamp {reset}. Set GITHUB_TOKEN to raise limits.")
    resp.raise_for_status()
    return resp.json()["items"]


def main():
    all_urls: list[str] = []
    total_pages = 10  # 10 × 100 = 1,000 (GitHub's hard cap)

    for page in range(1, total_pages + 1):
        print(f"Fetching page {page}/{total_pages}...")
        try:
            items = search_repos(page)
        except RuntimeError as e:
            print(f"Stopping early: {e}")
            break

        if not items:
            print("No more results.")
            break

        for repo in items:
            all_urls.append(repo["clone_url"])

        print(f"  → {len(items)} repos fetched (total so far: {len(all_urls)})")

        # Respect GitHub's secondary rate limit: max 10 req/min for search
        if page < total_pages:
            time.sleep(6)

    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(all_urls) + "\n")

    print(f"\nDone! {len(all_urls)} repo URLs written to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    main()