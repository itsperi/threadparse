"""
analyze_results.py

Produces two CSVs from the analyzer's JSON output:
- summary.csv   : one row per file (or per repo with --by-repo)
- targets.csv   : one row per thread target per file

Usage:
python analyze_results.py results.json [--out-dir ./out] [--by-repo]
"""

import sys, json, csv, os
from collections import defaultdict

VIOLATION_KINDS = ["SHARED LIST", "SHARED DICT", "SHARED SET", "SC"]
VAR_TYPES       = ["list", "dict", "set"]
CLASSIFICATIONS = ["GLOBAL", "NONLOCAL", "CROSS_THREAD", "MAIN_SCOPE", "CLASS_ATTR", "OTHER"]

# ── helpers ───────────────────────────────────────────────────────────────────

def _unprotected_count(shared_map: dict) -> int:
   return sum(
      len(info.get("unprotected", []))
      for info in shared_map.values()
   )

def _repo_key(filepath: str) -> str:
   """Use the top two path components as the repo identifier."""
   parts = filepath.replace("\\", "/").strip("/").split("/")
   return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]

# ── per-file summary ──────────────────────────────────────────────────────────

def _file_summary(filepath: str, entry: dict) -> dict:
   detail   = entry.get("detail") or {}
   shared   = detail.get("shared_vars", {})
   targets  = detail.get("thread_targets", [])
   violations = entry.get("violations", [])

   type_counts  = defaultdict(int)
   class_counts = defaultdict(int)
   for v in shared.values():
      type_counts[v.get("type") or "unknown"] += 1
      class_counts[v.get("classification", "OTHER")] += 1

   total_unprotected = sum(
      _unprotected_count(t.get("shared_reads", {})) +
      _unprotected_count(t.get("shared_writes", {})) +
      sum(len(i.get("unprotected", [])) for i in t.get("mutating_calls", {}).values())
      for t in targets
   )

   return {
      "file":                       filepath,
      "unsafe":                     int(entry.get("unsafe", False)),
      "n_shared_vars":              len(shared),
      "n_thread_targets":           len(targets),
      "n_indirect_targets":         sum(1 for t in targets if t.get("parent_target")),
      "total_unprotected_accesses": total_unprotected,
      **{f"shared_{t}s": type_counts.get(t, 0)      for t in VAR_TYPES},
      **{f"cls_{c.lower()}": class_counts.get(c, 0) for c in CLASSIFICATIONS},
      **{f"viol_{k.lower().replace(' ', '_')}": int(k in violations) for k in VIOLATION_KINDS},
   }

def build_summary_rows(data: dict) -> list[dict]:
   return [_file_summary(fp, entry) for fp, entry in data.items()]

# ── repo aggregation ──────────────────────────────────────────────────────────

# Fields where 100×mean is a genuine percentage (values are 0 or 1)
_BINARY_FIELDS = (
   {"unsafe"}
   | {f"viol_{k.lower().replace(' ', '_')}" for k in VIOLATION_KINDS}
)

def build_repo_rows(data: dict) -> list[dict]:
   """Collapse file-level summaries into one row per repo (base directory)."""
   buckets: dict[str, list[dict]] = defaultdict(list)
   for fp, entry in data.items():
      buckets[_repo_key(fp)].append(_file_summary(fp, entry))

   rows = []
   numeric_fields = None
   for repo, file_rows in sorted(buckets.items()):
      if numeric_fields is None:
         numeric_fields = [k for k in file_rows[0] if k != "file"]

      n   = len(file_rows)
      row = {"repo": repo, "n_files": n}
      for field in numeric_fields:
         values = [r[field] for r in file_rows]
         total  = sum(values)
         row[f"sum_{field}"] = total
         if field in _BINARY_FIELDS:
            # pct_ only meaningful for 0/1 flags: % of files where flag is set
            row[f"pct_{field}"] = round(100 * total / n, 1) if n else 0.0
         else:
            # avg_ for counts — 100×mean was the bug
            row[f"avg_{field}"] = round(total / n, 2) if n else 0.0
      rows.append(row)
   return rows

# ── per-target rows ───────────────────────────────────────────────────────────

def build_target_rows(data: dict) -> list[dict]:
   rows = []
   for filepath, entry in data.items():
      for target in (entry.get("detail") or {}).get("thread_targets", []):
         shared_reads   = target.get("shared_reads", {})
         shared_writes  = target.get("shared_writes", {})
         mutating_calls = target.get("mutating_calls", {})

         rows.append({
               "file":                   filepath,
               "target":                 target.get("name"),
               "class":                  target.get("class") or "",
               "is_indirect":            int(target.get("parent_target") is not None),
               "n_shared_read_vars":     len(shared_reads),
               "n_shared_write_vars":    len(shared_writes),
               "n_unprotected_reads":    _unprotected_count(shared_reads),
               "n_unprotected_writes":   _unprotected_count(shared_writes),
               "n_unprotected_calls":    sum(len(i.get("unprotected", [])) for i in mutating_calls.values()),
               "any_unprotected":        int(
                  _unprotected_count(shared_reads) +
                  _unprotected_count(shared_writes) +
                  sum(len(i.get("unprotected", [])) for i in mutating_calls.values()) > 0
               ),
         })
   return rows

# ── output ────────────────────────────────────────────────────────────────────

def write_csv(rows: list[dict], path: str):
   if not rows:
      print(f"  No data for {path}, skipping.")
      return
   with open(path, "w", newline="") as f:
      writer = csv.DictWriter(f, fieldnames=rows[0].keys())
      writer.writeheader()
      writer.writerows(rows)
   print(f"  Written: {path} ({len(rows)} rows)")

def print_aggregate(summary_rows: list[dict], repo_rows: list[dict] | None = None):
   total  = len(summary_rows)
   unsafe = sum(r["unsafe"] for r in summary_rows)

   print(f"\n{'='*45}")

   if repo_rows is not None:
      n_repos        = len(repo_rows)
      unsafe_repos   = sum(1 for r in repo_rows if r["sum_unsafe"] > 0)
      print(f"  Repos:                {n_repos}")
      print(f"  Repos with unsafe:    {unsafe_repos} ({100*unsafe_repos/n_repos:.1f}%)" if n_repos else "")
      print(f"  Files with threading: {total}")
      print(f"  Unsafe files:         {unsafe} ({100*unsafe/total:.1f}%)" if total else "")
      print()
      print(f"  {'Repo':<35} {'Files':>5} {'Unsafe':>6} {'%':>6}")
      print(f"  {'-'*35} {'-----':>5} {'------':>6} {'------':>6}")
      for r in sorted(repo_rows, key=lambda x: x["sum_unsafe"], reverse=True):
         n     = r["n_files"]
         u     = r["sum_unsafe"]
         pct   = f"{100*u/n:.1f}" if n else "—"
         print(f"  {r['repo']:<35} {n:>5} {u:>6} {pct:>6}%")
   else:
      print(f"  Files with threading: {total}")
      print(f"  Unsafe: {unsafe} ({100*unsafe/total:.1f}%)" if total else "  No data.")

   print()
   print(f"  Violation breakdown:")
   for k in VIOLATION_KINDS:
      col = f"viol_{k.lower().replace(' ', '_')}"
      print(f"    {k:<15}: {sum(r[col] for r in summary_rows)}")
   print()

# ── main ──────────────────────────────────────────────────────────────────────

def main():
   args = sys.argv[1:]
   if not args:
      print("Usage: analyze_results.py results.json [--out-dir <dir>] [--by-repo]")
      sys.exit(1)

   json_path  = args[0]
   out_dir    = "."
   by_repo    = "--by-repo" in args

   if "--out-dir" in args:
      out_dir = args[args.index("--out-dir") + 1]
   os.makedirs(out_dir, exist_ok=True)

   with open(json_path) as f:
      data = json.load(f)
   print(f"Loaded {len(data)} entries from {json_path}")

   summary_rows = build_summary_rows(data)
   repo_rows    = build_repo_rows(data) if by_repo else None
   target_rows  = build_target_rows(data)  # unchanged

   csv_rows = repo_rows if by_repo else summary_rows
   label    = "repo" if by_repo else "summary"
   write_csv(csv_rows,     os.path.join(out_dir, f"{label}.csv"))
   write_csv(target_rows,  os.path.join(out_dir, "targets.csv"))

   print_aggregate(summary_rows, repo_rows=repo_rows)

if __name__ == "__main__":
   main()