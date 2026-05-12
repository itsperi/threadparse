import requests
import time
import os
import sys

# GitHub personal access token - required for higher rate limits
# Create one at: https://github.com/settings/tokens (no special scopes needed)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
"Accept": "application/vnd.github+json",
"X-GitHub-Api-Version": "2022-11-28",
**({"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}),
}

DEFAULT_QUERY = "language:python threading in:code"

def parse_args() -> tuple[str, str]:
   args = sys.argv[1:]

   if not args:
      print("Usage: puller.py [-q | --query <search>] <dest>")
      print("   -q | --query <search>     Designate a specific search query to the API request")
      print("                             (Defaults to \"language:python threading in:code\")")
      print("   <dest>                    Designate the file to place URL results into")
      sys.exit(1)

   query = DEFAULT_QUERY
   if args[0] in ("-q", "--query"):
      if len(args) < 3:
         print("Usage: puller.py [-q | --query <search>] <dest>")
         print("   -q | --query <search>     Designate a specific search query to the API request")
         print("   <dest>                    Designate the file to place URL results into")
         sys.exit(1)
      query = args[1]
      dest = args[2]
   else:
      dest = args[0]

   return query, dest


def search_repos(query: str, page: int) -> list[dict]:
   """Fetch one page (100 repos) from the GitHub search API."""
   url = "https://api.github.com/search/repositories"
   params = {
      "q": query,
      "sort": "stars",
      "order": "desc",
      "per_page": 100,
      "page": page,
   }
   resp = requests.get(url, headers=HEADERS, params=params)

   # Surface rate-limit errors clearly
   if resp.status_code == 403:
      reset = resp.headers.get("X-RateLimit-Reset", "unknown")
      raise RuntimeError(
         f"Rate limited. Resets at unix timestamp {reset}. "
         "Set GITHUB_TOKEN to raise limits."
      )
   resp.raise_for_status()
   return resp.json()["items"]


def main():
   query, dest = parse_args()

   all_urls: list[str] = []
   total_pages = 10

   print(f'Query : "{query}"')
   print(f"Output: {dest}\n")

   for page in range(1, total_pages + 1):
      print(f"Fetching page {page}/{total_pages}...")
      try:
         items = search_repos(query, page)
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

   with open(dest, "w") as f:
      f.write("\n".join(all_urls) + "\n")

   print(f"\nDone! {len(all_urls)} repo URLs written to '{dest}'")


if __name__ == "__main__":
   main()
   