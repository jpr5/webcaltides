"""Expose the release builder's validated own-TCD tables to vector generators."""
import argparse
import json
from pathlib import Path

from otc_pipeline.astro_tables import astro_table, load_congen_run


def build_entry(run_dir, names, table_id, tcd_sha256):
    run = load_congen_run(Path(run_dir), tcd_sha256=tcd_sha256)
    return astro_table(*run, names, table_id=table_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--tcd-sha256", required=True)
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()
    print(json.dumps(build_entry(args.run_dir, args.names, args.table_id, args.tcd_sha256),
                     sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
