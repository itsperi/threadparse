import sys, json, csv, os
from collections import defaultdict

# ── constants ─────────────────────────────────────────────────────────────────

VIOLATION_KINDS = ["SHARED LIST", "SHARED DICT", "SHARED SET", "SC"]
VAR_TYPES       = ["list", "dict", "set"]
CLASSIFICATIONS = ["GLOBAL", "NONLOCAL", "CROSS_THREAD", "MAIN_SCOPE", "CLASS_ATTR", "OTHER"]

# ── helpers ───────────────────────────────────────────────────────────────────

def _unprotected_count(shared_map: dict) -> int:
   return sum(len(info.get("unprotected", [])) for info in shared_map.values())

def _protected_count(shared_map: dict) -> int:
   return sum(len(info.get("protected", [])) for info in shared_map.values())

def _repo_key(filepath: str) -> str:
   """Use the top two path components as the repo identifier."""
   parts = filepath.replace("\\", "/").strip("/").split("/")
   return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]

def _pct(num, den):
   return f"{100 * num / den:.1f}%" if den else "—"

# ── per-file summary ──────────────────────────────────────────────────────────

def _file_summary(filepath: str, entry: dict) -> dict:
   detail     = entry.get("detail") or {}
   shared     = detail.get("shared_vars", {})
   targets    = detail.get("thread_targets", [])
   violations = entry.get("violations", [])

   # --- line counts ---
   total_lines    = int(detail.get("total_lines",    0))
   threaded_lines = int(detail.get("threaded_lines", 0))
   threaded_pct   = round(100 * threaded_lines / total_lines, 1) if total_lines else 0.0

   # --- shared-var type and classification counts ---
   type_counts  = defaultdict(int)
   class_counts = defaultdict(int)
   for v in shared.values():
      type_counts[v.get("type") or "unknown"] += 1
      class_counts[v.get("classification", "OTHER")] += 1

   # --- violation frequency ---
   viol_freq = defaultdict(int)
   for v in violations:
      viol_freq[v] += 1

   # --- per-target access protection breakdown ---
   total_unprotected_reads  = 0
   total_protected_reads    = 0
   total_unprotected_writes = 0
   total_protected_writes   = 0
   total_unprotected_calls  = 0
   total_protected_calls    = 0
   risky_threads            = 0

   for t in targets:
      sr = t.get("shared_reads",   {})
      sw = t.get("shared_writes",  {})
      mc = t.get("mutating_calls", {})

      unp_r = _unprotected_count(sr)
      pro_r = _protected_count(sr)
      unp_w = _unprotected_count(sw)
      pro_w = _protected_count(sw)
      unp_c = sum(len(i.get("unprotected", [])) for i in mc.values())
      pro_c = sum(len(i.get("protected",   [])) for i in mc.values())

      total_unprotected_reads  += unp_r
      total_protected_reads    += pro_r
      total_unprotected_writes += unp_w
      total_protected_writes   += pro_w
      total_unprotected_calls  += unp_c
      total_protected_calls    += pro_c

      if unp_r + unp_w + unp_c > 0:
         risky_threads += 1

   total_unprotected = total_unprotected_reads + total_unprotected_writes + total_unprotected_calls
   total_protected   = total_protected_reads   + total_protected_writes   + total_protected_calls
   total_accesses    = total_unprotected + total_protected
   protection_rate   = round(100 * total_protected / total_accesses, 1) if total_accesses else 0.0

   return {
      "file":                    filepath,
      "repo":                    _repo_key(filepath),
      # --- line counts ---
      "total_lines":             total_lines,
      "threaded_lines":          threaded_lines,
      "threaded_lines_pct":      threaded_pct,
      # --- high-level flags ---
      "unsafe":                  int(entry.get("unsafe", False)),
      # --- shared variable counts ---
      "n_shared_vars":           len(shared),
      **{f"shared_{t}s":         type_counts.get(t, 0) for t in VAR_TYPES},
      "shared_other":            sum(v for k, v in type_counts.items() if k not in VAR_TYPES),
      # --- classification of shared variables ---
      **{f"cls_{c.lower()}":     class_counts.get(c, 0) for c in CLASSIFICATIONS},
      # --- thread target counts ---
      "n_thread_targets":        len(targets),
      "n_indirect_targets":      sum(1 for t in targets if t.get("parent_target")),
      "n_risky_threads":         risky_threads,
      # --- access protection breakdown ---
      "unprotected_reads":       total_unprotected_reads,
      "protected_reads":         total_protected_reads,
      "unprotected_writes":      total_unprotected_writes,
      "protected_writes":        total_protected_writes,
      "unprotected_calls":       total_unprotected_calls,
      "protected_calls":         total_protected_calls,
      "total_unprotected":       total_unprotected,
      "total_protected":         total_protected,
      "total_accesses":          total_accesses,
      "protection_rate_pct":     protection_rate,
      # --- violation frequency ---
      **{f"viol_{k.lower().replace(' ', '_')}": viol_freq.get(k, 0) for k in VIOLATION_KINDS},
      "total_violations":        sum(viol_freq.values()),
   }


def build_summary_rows(data: dict) -> list[dict]:
   return [_file_summary(fp, entry) for fp, entry in data.items()]

# ── repo aggregation ──────────────────────────────────────────────────────────

# These fields get sum only (no avg); pct_<field> is also produced for binary flags.
_SUM_ONLY_FIELDS = {"unsafe", "n_risky_threads"}

def build_repo_rows(data: dict) -> list[dict]:
   """Collapse file-level summaries into one row per repo."""
   buckets: dict[str, list[dict]] = defaultdict(list)
   for fp, entry in data.items():
      key = _repo_key(fp)
      buckets[key].append(_file_summary(fp, entry))

   rows = []
   for repo, file_rows in sorted(buckets.items()):
      n = len(file_rows)
      numeric_fields = [k for k in file_rows[0] if k not in ("file", "repo")]

      row: dict = {"repo": repo, "n_files": n}

      for field in numeric_fields:
         values = [r[field] for r in file_rows]
         total  = sum(values)
         row[f"sum_{field}"] = total

         if field in _SUM_ONLY_FIELDS or field == "unsafe":
            row[f"pct_{field}"] = round(100 * total / n, 1) if n else 0.0
         elif field in ("protection_rate_pct", "threaded_lines_pct"):
            # These are already percentages — average them directly.
            # Also compute repo-level derived versions below.
            row[f"avg_{field}"] = round(total / n, 1) if n else 0.0
         else:
            row[f"avg_{field}"] = round(total / n, 2) if n else 0.0

      # Derived repo-level rates from raw totals (more accurate than avg of pcts)
      acc = row.get("sum_total_accesses", 0)
      pro = row.get("sum_total_protected", 0)
      row["repo_protection_rate_pct"] = round(100 * pro / acc, 1) if acc else 0.0

      tot_lines = row.get("sum_total_lines", 0)
      thr_lines = row.get("sum_threaded_lines", 0)
      row["repo_threaded_lines_pct"] = round(100 * thr_lines / tot_lines, 1) if tot_lines else 0.0

      rows.append(row)
   return rows

# ── per-target rows ───────────────────────────────────────────────────────────

def build_target_rows(data: dict) -> list[dict]:
   rows = []
   for filepath, entry in data.items():
      for target in (entry.get("detail") or {}).get("thread_targets", []):
         sr = target.get("shared_reads",   {})
         sw = target.get("shared_writes",  {})
         mc = target.get("mutating_calls", {})

         unp_r = _unprotected_count(sr)
         pro_r = _protected_count(sr)
         unp_w = _unprotected_count(sw)
         pro_w = _protected_count(sw)
         unp_c = sum(len(i.get("unprotected", [])) for i in mc.values())
         pro_c = sum(len(i.get("protected",   [])) for i in mc.values())
         total = unp_r + pro_r + unp_w + pro_w + unp_c + pro_c

         rows.append({
            "file":                 filepath,
            "repo":                 _repo_key(filepath),
            "target":               target.get("name"),
            "class":                target.get("class") or "",
            "is_indirect":          int(target.get("parent_target") is not None),
            "n_shared_read_vars":   len(sr),
            "n_shared_write_vars":  len(sw),
            "unprotected_reads":    unp_r,
            "protected_reads":      pro_r,
            "unprotected_writes":   unp_w,
            "protected_writes":     pro_w,
            "unprotected_calls":    unp_c,
            "protected_calls":      pro_c,
            "total_accesses":       total,
            "protection_rate_pct":  round(100 * (pro_r + pro_w + pro_c) / total, 1) if total else 0.0,
            "any_unprotected":      int(unp_r + unp_w + unp_c > 0),
         })
   return rows

# ── CSV output ────────────────────────────────────────────────────────────────

def write_csv(rows: list[dict], path: str):
   if not rows:
      print(f"  No data for {path}, skipping.")
      return
   with open(path, "w", newline="") as f:
      writer = csv.DictWriter(f, fieldnames=rows[0].keys())
      writer.writeheader()
      writer.writerows(rows)
   print(f"  Written: {path} ({len(rows)} rows)")

# ── console report ────────────────────────────────────────────────────────────

def _shared_prefix(names: list[str]) -> str:
   if not names:
      return ""
   parts  = [n.replace("\\", "/").split("/") for n in names]
   common = []
   for segs in zip(*parts):
      if len(set(segs)) == 1:
         common.append(segs[0])
      else:
         break
   return "/".join(common) + "/" if common else ""

def _display_name(repo: str, prefix: str, max_w: int) -> str:
   name = repo[len(prefix):] if repo.startswith(prefix) else repo
   if len(name) > max_w:
      name = name[:max_w - 1] + "…"
   return name

def print_aggregate(summary_rows: list[dict], repo_rows: list[dict] | None = None):
   total     = len(summary_rows)
   unsafe    = sum(r["unsafe"]            for r in summary_rows)
   risky_thr = sum(r["n_risky_threads"]   for r in summary_rows)
   n_threads = sum(r["n_thread_targets"]  for r in summary_rows)
   unp       = sum(r["total_unprotected"] for r in summary_rows)
   pro       = sum(r["total_protected"]   for r in summary_rows)
   accesses  = unp + pro
   tot_lines = sum(r["total_lines"]       for r in summary_rows)
   thr_lines = sum(r["threaded_lines"]    for r in summary_rows)

   W = 55
   def hdr(title):
      print(f"\n  {'─'*W}\n  {title}\n  {'─'*W}")

   print(f"\n  {'═'*W}")
   print(f"  OVERALL SUMMARY")
   print(f"  {'═'*W}")
   print(f"  Files with threading : {total}")
   print(f"  Unsafe files         : {unsafe} ({_pct(unsafe, total)})")
   print()
   print(f"  Thread targets total : {n_threads}")
   print(f"  Risky threads        : {risky_thr} ({_pct(risky_thr, n_threads)})")

   hdr("LINE COUNTS (overall)")
   print(f"  Total Python LOC     : {tot_lines:,}")
   print(f"  Threaded LOC         : {thr_lines:,} ({_pct(thr_lines, tot_lines)})")

   hdr("ACCESS PROTECTION (overall)")
   print(f"  Total accesses       : {accesses}")
   print(f"  ├─ Unprotected       : {unp} ({_pct(unp, accesses)})")
   print(f"  │    reads           : {sum(r['unprotected_reads']  for r in summary_rows)}")
   print(f"  │    writes          : {sum(r['unprotected_writes'] for r in summary_rows)}")
   print(f"  │    calls           : {sum(r['unprotected_calls']  for r in summary_rows)}")
   print(f"  └─ Protected         : {pro} ({_pct(pro, accesses)})")
   print(f"       reads           : {sum(r['protected_reads']  for r in summary_rows)}")
   print(f"       writes          : {sum(r['protected_writes'] for r in summary_rows)}")
   print(f"       calls           : {sum(r['protected_calls']  for r in summary_rows)}")

   hdr("VIOLATION FREQUENCY (overall)")
   total_viols = sum(r["total_violations"] for r in summary_rows)
   files_any   = sum(1 for r in summary_rows if r["total_violations"] > 0)
   print(f"  Total violation instances : {total_viols}  (in {files_any} files)")
   for k in VIOLATION_KINDS:
      col   = f"viol_{k.lower().replace(' ', '_')}"
      count = sum(r[col] for r in summary_rows)
      print(f"    {k:<20}: {count:>5}  ({_pct(count, total_viols)})")

   hdr("SHARED VAR CLASSIFICATIONS (overall)")
   total_shared = sum(r["n_shared_vars"] for r in summary_rows)
   for c in CLASSIFICATIONS:
      col   = f"cls_{c.lower()}"
      count = sum(r[col] for r in summary_rows)
      print(f"    {c:<20}: {count:>5}  ({_pct(count, total_shared)})")

   hdr("SHARED VAR TYPES (overall)")
   total_shared = sum(r["n_shared_vars"] for r in summary_rows)
   for t in VAR_TYPES:
      col   = f"shared_{t}s"
      count = sum(r[col] for r in summary_rows)
      print(f"    {t:<20}: {count:>5}  ({_pct(count, total_shared)})")
   other = sum(r["shared_other"] for r in summary_rows)
   print(f"    {'other':<20}: {other:>5}  ({_pct(other, total_shared)})")
   print(f"    {'TOTAL':<20}: {total_shared:>5}")

   if repo_rows is None:
      print(f"\n  {'═'*W}\n")
      return

   # ── per-repo tables ───────────────────────────────────────────────────────
   hdr("PER-REPO BREAKDOWN")
   n_repos      = len(repo_rows)
   unsafe_repos = sum(1 for r in repo_rows if r["sum_unsafe"] > 0)
   print(f"  Repos total          : {n_repos}")
   print(f"  Repos with unsafe    : {unsafe_repos} ({_pct(unsafe_repos, n_repos)})")
   print()

   col_w  = 30
   prefix = _shared_prefix([r["repo"] for r in repo_rows])
   if prefix:
      print(f"  (repo paths relative to: {prefix.rstrip('/')})\n")

   def dn(repo):
      return _display_name(repo, prefix, col_w)

   # Main per-repo table
   print(
      f"  {'Repo':<{col_w}} {'Files':>5} {'Unsafe':>6} "
      f"{'RiskyThr':>8} {'TotLines':>9} {'ThrdLines':>10} "
      f"{'Thrd%':>6} {'Unprotected':>11} {'Violations':>10}"
   )
   sep = (f"  {'-'*col_w} {'-----':>5} {'------':>6} {'--------':>8} "
         f"{'--------':>9} {'---------':>10} {'-----':>6} "
         f"{'----------':>11} {'----------':>10}")
   print(sep)

   for r in sorted(repo_rows, key=lambda x: x["sum_unsafe"], reverse=True):
      print(
         f"  {dn(r['repo']):<{col_w}} "
         f"{r['n_files']:>5} "
         f"{r['sum_unsafe']:>6} "
         f"{r['sum_n_risky_threads']:>8} "
         f"{r['sum_total_lines']:>9,} "
         f"{r['sum_threaded_lines']:>10,} "
         f"{r['repo_threaded_lines_pct']:>5.1f}% "
         f"{r['sum_total_unprotected']:>11} "
         f"{r['sum_total_violations']:>10}"
      )

   print(sep)
   tf   = sum(r["n_files"]                  for r in repo_rows)
   tu   = sum(r["sum_unsafe"]               for r in repo_rows)
   trt  = sum(r["sum_n_risky_threads"]      for r in repo_rows)
   ttl  = sum(r["sum_total_lines"]          for r in repo_rows)
   tthr = sum(r["sum_threaded_lines"]       for r in repo_rows)
   tunp = sum(r["sum_total_unprotected"]    for r in repo_rows)
   tv   = sum(r["sum_total_violations"]     for r in repo_rows)
   thr_pct = round(100 * tthr / ttl, 1) if ttl else 0.0
   print(
      f"  {'TOTAL':<{col_w}} {tf:>5} {tu:>6} ({_pct(tu,tf)}) "
      f"{trt:>8} ({_pct(trt,n_threads)}) "
      f"{ttl:>9,} {tthr:>10,} {thr_pct:>5.1f}% "
      f"{tunp:>11} {tv:>10}"
   )

   # Violation breakdown × repo
   hdr("VIOLATION BREAKDOWN BY KIND × REPO")
   viol_cols = [f"viol_{k.lower().replace(' ', '_')}" for k in VIOLATION_KINDS]
   print(f"  {'Repo':<{col_w}} " + "  ".join(f"{k:>14}" for k in VIOLATION_KINDS))
   print(f"  {'-'*col_w} " + "  ".join(f"{'------':>14}" for _ in VIOLATION_KINDS))
   for r in sorted(repo_rows, key=lambda x: x["sum_total_violations"], reverse=True):
      vals = "  ".join(f"{r.get(f'sum_{c}', 0):>14}" for c in viol_cols)
      print(f"  {dn(r['repo']):<{col_w}} {vals}")
   print(f"  {'-'*col_w} " + "  ".join(f"{'------':>14}" for _ in VIOLATION_KINDS))
   kind_totals = [sum(r.get(f"sum_{c}", 0) for r in repo_rows) for c in viol_cols]
   grand_viol  = sum(kind_totals)
   print(f"  {'TOTAL':<{col_w}} " +
         "  ".join(f"{t:>11} {_pct(t, grand_viol):>3}" for t in kind_totals))

   # Classification breakdown × repo
   hdr("CLASSIFICATION BREAKDOWN × REPO")
   cls_cols = [f"cls_{c.lower()}" for c in CLASSIFICATIONS]
   print(f"  {'Repo':<{col_w}} " + "  ".join(f"{c:>10}" for c in CLASSIFICATIONS))
   print(f"  {'-'*col_w} " + "  ".join(f"{'------':>10}" for _ in CLASSIFICATIONS))
   for r in sorted(repo_rows, key=lambda x: x["sum_n_shared_vars"], reverse=True):
      vals = "  ".join(f"{r.get(f'sum_{c}', 0):>10}" for c in cls_cols)
      print(f"  {dn(r['repo']):<{col_w}} {vals}")
   print(f"  {'-'*col_w} " + "  ".join(f"{'------':>10}" for _ in CLASSIFICATIONS))
   cls_totals     = [sum(r.get(f"sum_{c}", 0) for r in repo_rows) for c in cls_cols]
   grand_cls      = sum(cls_totals)
   print(f"  {'TOTAL':<{col_w}} " +
         "  ".join(f"{t:>7} {_pct(t, grand_cls):>3}" for t in cls_totals))

   print(f"\n  {'═'*W}\n")

# ── main ──────────────────────────────────────────────────────────────────────

def main():
   args = sys.argv[1:]
   if not args or args[0] in ("-h", "--help"):
      print("Usage: python stats.py [-h | --help] | <input.json> [-o | --out-dir <dir>]")
      print("   -h | --help                 Outputs this usage information; warns if no arguments provided")
      print("   <input.json>                JSON file produced by parse.py")
      print("   -o | --out-dir <filename>   Designate output directory for CSV files (default: current directory)")
      sys.exit(0)

   json_path = args[0]
   out_dir   = "."

   if "--out-dir" in args or "-o" in args:
      out_dir = args[args.index("--out-dir") + 1] if "--out-dir" in args else args[args.index("-o") + 1]
   os.makedirs(out_dir, exist_ok=True)

   with open(json_path) as f:
      data = json.load(f)
   print(f"Loaded {len(data)} entries from {json_path}")

   summary_rows = build_summary_rows(data)
   repo_rows    = build_repo_rows(data)
   target_rows  = build_target_rows(data)

   write_csv(summary_rows, os.path.join(out_dir, "summary.csv"))
   write_csv(repo_rows, os.path.join(out_dir, "repo.csv"))
   write_csv(target_rows, os.path.join(out_dir, "targets.csv"))

   print_aggregate(summary_rows, repo_rows)


if __name__ == "__main__":
   main()