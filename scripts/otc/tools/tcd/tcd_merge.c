/* tcd_merge: the OTC TCD = a source TCD with constituents added from a congen run, through libtcd
 * (public domain).
 *
 *   tcd_merge SOURCE.tcd CONGEN.tcd OUT.tcd STAMP NAME...
 *
 * OUT.tcd has every constituent of SOURCE.tcd, in its order and with its stored speed, V0+u and f,
 * then each NAME, with the speed, V0+u and f of CONGEN.tcd (the congen run). Every station record of
 * SOURCE.tcd is copied in order (same record numbers, so subordinate references stay right), its
 * string tables mapped by name, with amplitude and epoch 0 for the added constituents: no station
 * uses them. A NAME the source already has, or the congen run lacks, is refused, and so is a congen
 * run whose years differ from the source's.
 *
 * libtcd stamps [LAST MODIFIED] with the clock; this program fixes the clock at STAMP (UTC,
 * "YYYY-MM-DD HH:MM"), so OUT.tcd is the same bytes on every run. libtcd keeps one database open at a
 * time, so the source records go through a temporary file (OUT.tcd.records, removed at the end).
 *
 * After writing, OUT.tcd is reopened and checked: the header counts, every source constituent's
 * stored values equal to the source's, every added one equal to the congen run's, and every record
 * equal to its source record field by field (string-table fields compared by name). Any difference
 * exits non-zero and removes OUT.tcd.
 */
#define _XOPEN_SOURCE 700
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "tcd.h"

static time_t fixed_clock;

/* libtcd's curtime() asks for the clock; the definition here wins over libc's at link time. */
time_t time (time_t *t) {
    if (t) *t = fixed_clock;
    return fixed_clock;
}

static void die (const char *msg, const char *arg) {
    fprintf (stderr, "tcd_merge: %s%s%s\n", msg, arg ? ": " : "", arg ? arg : "");
    exit (1);
}

typedef struct {
    NV_U_INT32 n, years;
    NV_INT32 start;
    char **name;
    double *speed;
    float **eq, **nf;
} Table;

static void read_table (Table *t) {
    DB_HEADER_PUBLIC db = get_tide_db_header ();
    t->n = db.constituents; t->years = db.number_of_years; t->start = db.start_year;
    t->name = calloc (t->n, sizeof *t->name); t->speed = calloc (t->n, sizeof *t->speed);
    t->eq = calloc (t->n, sizeof *t->eq); t->nf = calloc (t->n, sizeof *t->nf);
    for (NV_U_INT32 c = 0; c < t->n; ++c) {
        t->name[c] = strdup (get_constituent (c));
        t->speed[c] = get_speed (c);
        t->eq[c] = malloc (t->years * sizeof (float)); t->nf[c] = malloc (t->years * sizeof (float));
        for (NV_U_INT32 y = 0; y < t->years; ++y) {
            t->eq[c][y] = get_equilibrium (c, y);
            t->nf[c][y] = get_node_factor (c, y);
        }
    }
}

static int find (const Table *t, const char *name) {
    for (NV_U_INT32 c = 0; c < t->n; ++c) if (!strcmp (t->name[c], name)) return (int) c;
    return -1;
}

/* the string tables a record refers to by index, as names */
typedef struct { char *country, *tzfile, *restriction, *legalese, *datum, *level_units, *dir_units; } Names;

static char *dup (const char *s) { return strdup (s ? s : ""); }

static Names names_of (const TIDE_RECORD *r) {
    Names n = { dup (get_country (r->country)), dup (get_tzfile (r->header.tzfile)),
                dup (get_restriction (r->restriction)), dup (get_legalese (r->legalese)),
                dup (get_datum (r->datum)), dup (get_level_units (r->level_units)),
                dup (get_dir_units (r->direction_units)) };
    return n;
}

static int same_names (Names a, Names b) {
    return !strcmp (a.country, b.country) && !strcmp (a.tzfile, b.tzfile) && !strcmp (a.restriction, b.restriction)
        && !strcmp (a.legalese, b.legalese) && !strcmp (a.datum, b.datum) && !strcmp (a.level_units, b.level_units)
        && !strcmp (a.dir_units, b.dir_units);
}

static void free_names (Names n) {
    free (n.country); free (n.tzfile); free (n.restriction); free (n.legalese); free (n.datum);
    free (n.level_units); free (n.dir_units);
}

/* every field of a record but the string-table indices; amplitude/epoch over the first n constituents */
static const char *record_diff (const TIDE_RECORD *a, const TIDE_RECORD *b, NV_U_INT32 n_src, NV_U_INT32 n_out) {
    if (a->header.record_number != b->header.record_number) return "record_number";
    if (a->header.record_type != b->header.record_type) return "record_type";
    if (a->header.latitude != b->header.latitude || a->header.longitude != b->header.longitude) return "position";
    if (a->header.reference_station != b->header.reference_station) return "reference_station";
    if (strcmp (a->header.name, b->header.name)) return "name";
    if (strcmp (a->source, b->source) || strcmp (a->comments, b->comments) || strcmp (a->notes, b->notes)
        || strcmp (a->station_id_context, b->station_id_context) || strcmp (a->station_id, b->station_id)
        || strcmp (a->xfields, b->xfields)) return "text field";
    if (a->date_imported != b->date_imported || a->expiration_date != b->expiration_date
        || a->months_on_station != b->months_on_station || a->last_date_on_station != b->last_date_on_station
        || a->confidence != b->confidence) return "dates or confidence";
    if (a->min_direction != b->min_direction || a->max_direction != b->max_direction) return "directions";
    if (a->datum_offset != b->datum_offset || a->zone_offset != b->zone_offset) return "datum or zone offset";
    if (a->min_time_add != b->min_time_add || a->min_level_add != b->min_level_add
        || a->min_level_multiply != b->min_level_multiply || a->max_time_add != b->max_time_add
        || a->max_level_add != b->max_level_add || a->max_level_multiply != b->max_level_multiply
        || a->flood_begins != b->flood_begins || a->ebb_begins != b->ebb_begins) return "subordinate offsets";
    for (NV_U_INT32 c = 0; c < n_src; ++c)
        if (a->amplitude[c] != b->amplitude[c] || a->epoch[c] != b->epoch[c]) return "amplitude or epoch";
    for (NV_U_INT32 c = n_src; c < n_out; ++c)
        if (b->amplitude[c] != 0.0f || b->epoch[c] != 0.0f) return "added constituent not zero";
    return NULL;
}

int main (int argc, char **argv) {
    if (argc < 6) {
        fprintf (stderr, "usage: %s SOURCE.tcd CONGEN.tcd OUT.tcd 'YYYY-MM-DD HH:MM' NAME...\n", argv[0]);
        return 2;
    }
    const char *src = argv[1], *cg = argv[2], *out = argv[3], *stamp = argv[4];
    int n_add = argc - 5;
    char **add = argv + 5;
    setenv ("TZ", "UTC", 1);
    tzset ();
    struct tm tm;
    memset (&tm, 0, sizeof tm);
    const char *end = strptime (stamp, "%Y-%m-%d %H:%M", &tm);
    if (!end || *end) die ("STAMP is not YYYY-MM-DD HH:MM", stamp);
    tm.tm_isdst = 0;
    fixed_clock = mktime (&tm);   /* TZ is UTC here */
    if (fixed_clock == (time_t) -1) die ("STAMP is not a time", stamp);

    Table congen, source;
    if (!open_tide_db (cg)) die ("libtcd could not open the congen TCD", cg);
    read_table (&congen);
    close_tide_db ();
    if (!open_tide_db (src)) die ("libtcd could not open the source TCD", src);
    read_table (&source);
    DB_HEADER_PUBLIC sdb = get_tide_db_header ();
    if (congen.start != source.start || congen.years != source.years)
        die ("the congen run's years differ from the source TCD's", NULL);
    int idx[MAX_CONSTITUENTS];
    for (int i = 0; i < n_add; ++i) {
        if (find (&source, add[i]) >= 0) die ("the source TCD already has", add[i]);
        if ((idx[i] = find (&congen, add[i])) < 0) die ("the congen run has no", add[i]);
        for (int j = 0; j < i; ++j) if (!strcmp (add[i], add[j])) die ("named twice", add[i]);
    }
    NV_U_INT32 n_out = source.n + (NV_U_INT32) n_add;
    if (n_out > MAX_CONSTITUENTS) die ("too many constituents for libtcd", NULL);

    /* the source records and their string-table names, through a temporary file */
    char tmp_path[4096];
    snprintf (tmp_path, sizeof tmp_path, "%s.records", out);
    FILE *tmp = fopen (tmp_path, "w+b");
    if (!tmp) die ("cannot write", tmp_path);
    TIDE_RECORD *rec = malloc (sizeof *rec), *chk = malloc (sizeof *chk);
    Names *names = calloc (sdb.number_of_records, sizeof *names);
    for (NV_U_INT32 r = 0; r < sdb.number_of_records; ++r) {
        if (read_tide_record ((NV_INT32) r, rec) != (NV_INT32) r) die ("cannot read a source record", NULL);
        names[r] = names_of (rec);
        if (fwrite (rec, sizeof *rec, 1, tmp) != 1) die ("cannot write", tmp_path);
    }
    /* the string tables in their source order (an entry no record uses is kept too) */
    NV_U_INT32 nt[5] = { sdb.countries, sdb.tzfiles, sdb.restriction_types, sdb.legaleses, sdb.datum_types };
    char **tables[5];
    for (int k = 0; k < 5; ++k) {
        tables[k] = calloc (nt[k], sizeof (char *));
        for (NV_U_INT32 i = 0; i < nt[k]; ++i)
            tables[k][i] = dup (k == 0 ? get_country (i) : k == 1 ? get_tzfile (i) : k == 2 ? get_restriction (i)
                                : k == 3 ? get_legalese (i) : get_datum (i));
    }
    close_tide_db ();

    const char **cname = calloc (n_out, sizeof *cname);
    double *speed = calloc (n_out, sizeof *speed);
    const float **eq = calloc (n_out, sizeof *eq), **nf = calloc (n_out, sizeof *nf);
    for (NV_U_INT32 c = 0; c < source.n; ++c) {
        cname[c] = source.name[c]; speed[c] = source.speed[c]; eq[c] = source.eq[c]; nf[c] = source.nf[c];
    }
    for (int i = 0; i < n_add; ++i) {
        NV_U_INT32 c = source.n + (NV_U_INT32) i;
        cname[c] = congen.name[idx[i]]; speed[c] = congen.speed[idx[i]];
        eq[c] = congen.eq[idx[i]]; nf[c] = congen.nf[idx[i]];
    }
    remove (out);
    if (!create_tide_db (out, n_out, cname, speed, source.start, source.years, eq, nf))
        die ("libtcd could not create", out);
    DB_HEADER_PUBLIC odb = get_tide_db_header ();
    for (int k = 0; k < 5; ++k)
        for (NV_U_INT32 i = 0; i < nt[k]; ++i) {
            const char *s = tables[k][i];
            NV_INT32 got = k == 0 ? find_or_add_country (s, &odb) : k == 1 ? find_or_add_tzfile (s, &odb)
                         : k == 2 ? find_or_add_restriction (s, &odb) : k == 3 ? find_or_add_legalese (s, &odb)
                         : find_or_add_datum (s, &odb);
            if (got < 0) die ("libtcd could not add a table entry", s);
        }
    rewind (tmp);
    for (NV_U_INT32 r = 0; r < sdb.number_of_records; ++r) {
        if (fread (rec, sizeof *rec, 1, tmp) != 1) die ("cannot read", tmp_path);
        Names n = names[r];
        rec->country = (NV_INT16) find_country (n.country);
        rec->header.tzfile = (NV_INT16) find_tzfile (n.tzfile);
        rec->restriction = (NV_U_BYTE) find_restriction (n.restriction);
        rec->legalese = (NV_U_BYTE) find_legalese (n.legalese);
        rec->datum = (NV_INT16) find_datum (n.datum);
        NV_INT32 lu = find_level_units (n.level_units), du = find_dir_units (n.dir_units);
        if (lu < 0 || du < 0) die ("a unit name the new file does not have", n.level_units);
        rec->level_units = (NV_U_BYTE) lu; rec->direction_units = (NV_U_BYTE) du;
        for (NV_U_INT32 c = source.n; c < n_out; ++c) { rec->amplitude[c] = 0.0f; rec->epoch[c] = 0.0f; }
        if (!add_tide_record (rec, &odb)) die ("libtcd refused a record", rec->header.name);
    }
    close_tide_db ();

    /* check what was written */
    int bad = 0;
    if (!open_tide_db (out)) die ("libtcd could not reopen", out);
    odb = get_tide_db_header ();
    if (odb.constituents != n_out || odb.number_of_records != sdb.number_of_records
        || odb.start_year != sdb.start_year || odb.number_of_years != sdb.number_of_years) {
        fprintf (stderr, "tcd_merge: header counts differ\n"); bad = 1;
    }
    Table written;
    read_table (&written);
    for (NV_U_INT32 c = 0; c < n_out && !bad; ++c) {
        const Table *want = c < source.n ? &source : &congen;
        int w = c < source.n ? (int) c : idx[c - source.n];
        if (strcmp (written.name[c], want->name[w]) || written.speed[c] != want->speed[w]) bad = 1;
        for (NV_U_INT32 y = 0; y < written.years && !bad; ++y)
            if (written.eq[c][y] != want->eq[w][y] || written.nf[c][y] != want->nf[w][y]) bad = 1;
        if (bad) fprintf (stderr, "tcd_merge: constituent %s differs from its source\n", written.name[c]);
    }
    rewind (tmp);
    for (NV_U_INT32 r = 0; r < sdb.number_of_records && !bad; ++r) {
        if (fread (rec, sizeof *rec, 1, tmp) != 1) die ("cannot read", tmp_path);
        if (read_tide_record ((NV_INT32) r, chk) != (NV_INT32) r) { bad = 1; break; }
        const char *why = record_diff (rec, chk, source.n, n_out);
        Names got = names_of (chk);
        if (!why && !same_names (names[r], got)) why = "string table field";
        free_names (got);
        if (why) { fprintf (stderr, "tcd_merge: record %u (%s): %s differs\n", r, rec->header.name, why); bad = 1; }
    }
    close_tide_db ();
    fclose (tmp);
    remove (tmp_path);
    if (bad) { remove (out); return 1; }
    printf ("%s: %u constituents (%u + %d added), %u records\n", out, n_out, source.n, n_add, sdb.number_of_records);
    return 0;
}
