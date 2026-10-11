/* tcd_dump: print the constituent table of a TCD file, read with libtcd (public domain).
 *
 *   tcd_dump FILE.tcd FIRST_YEAR LAST_YEAR
 *
 * One line per constituent, in the file's order, tab separated:
 *   name  speed(%.7f)  v0u(%.2f, one per year, comma separated)  f(%.4f, one per year)
 * The TCD stores V0+u to 0.01 degree, f to 0.0001 and speeds to 1e-7 degree per hour, so these
 * formats print the stored values exactly. congen_tables.sh turns the lines into the JSON dump
 * that scripts/gesla/dump_tcd.rb writes (same keys and values); a test checks the two are
 * byte-identical on data/harmonics-dwf-20251228-free.tcd.
 *
 * It exists because the tcd gem dump_tcd.rb uses reads a file correctly only when the constituent
 * count is a multiple of 8 (dump_tcd.rb check_gem_alignment), and the OTC TCD has 178.
 */
#include <stdio.h>
#include <stdlib.h>
#include "tcd.h"

int main (int argc, char **argv) {
    if (argc != 4) {
        fprintf (stderr, "usage: %s FILE.tcd FIRST_YEAR LAST_YEAR\n", argv[0]);
        return 2;
    }
    long first = strtol (argv[2], NULL, 10), last = strtol (argv[3], NULL, 10);
    if (!open_tide_db (argv[1])) {
        fprintf (stderr, "%s: libtcd could not open it\n", argv[1]);
        return 1;
    }
    DB_HEADER_PUBLIC db = get_tide_db_header ();
    long y0 = db.start_year, y1 = db.start_year + (long) db.number_of_years - 1;
    if (first > last || first < y0 || last > y1) {
        fprintf (stderr, "%s: years %ld-%ld, the file covers %ld-%ld\n", argv[1], first, last, y0, y1);
        return 1;
    }
    for (NV_INT32 c = 0; c < (NV_INT32) db.constituents; ++c) {
        printf ("%s\t%.7f\t", get_constituent (c), get_speed (c));
        for (long y = first; y <= last; ++y)
            printf (y == first ? "%.2f" : ",%.2f", get_equilibrium (c, (NV_INT32) (y - y0)));
        printf ("\t");
        for (long y = first; y <= last; ++y)
            printf (y == first ? "%.4f" : ",%.4f", get_node_factor (c, (NV_INT32) (y - y0)));
        printf ("\n");
    }
    close_tide_db ();
    return ferror (stdout) ? 1 : 0;
}
