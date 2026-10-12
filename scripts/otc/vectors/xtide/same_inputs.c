/* Replace comparison station records only; retain the accepted TCD tables. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <tcd.h>

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static int reference(const char *id, TIDE_RECORD *record) {
    DB_HEADER_PUBLIC db = get_tide_db_header();
    for (unsigned i = 0; i < db.number_of_records; ++i) {
        if (read_tide_record(i, record) < 0) fail("cannot read TCD record");
        if (record->header.record_type == REFERENCE_STATION &&
            !strcmp(record->station_id, id)) return (int)i;
    }
    fail("the accepted TCD has no requested reference station");
    return -1;
}

int main(int argc, char **argv) {
    if (argc != 3) fail("usage: same_inputs COPY_OF_OWN_TCD INPUT_TSV");
    if (!open_tide_db(argv[1])) fail("cannot open comparison TCD");
    FILE *input = fopen(argv[2], "r");
    if (!input) fail("cannot read unchanged agency constants");
    char station[32], ref[32], type;
    double datum, offset_min, height;
    unsigned count;
    while (fscanf(input, "%31s %31s %lf %lf %c %lf %u", station, ref, &datum,
                  &offset_min, &type, &height, &count) == 7) {
        TIDE_RECORD record;
        int original = reference(ref, &record);
        (void)original;
        memset(record.amplitude, 0, sizeof(record.amplitude));
        memset(record.epoch, 0, sizeof(record.epoch));
        for (unsigned i = 0; i < count; ++i) {
            char name[100];
            double amplitude, phase;
            if (fscanf(input, "%99s %lf %lf", name, &amplitude, &phase) != 3)
                fail("incomplete agency constituent input");
            int index = find_constituent(name);
            if (index < 0) fail("agency constituent missing from accepted TCD");
            record.amplitude[index] = amplitude;
            record.epoch[index] = phase;
        }
        int units = find_level_units("meters");
        if (units < 0) fail("accepted TCD has no metre unit");
        record.level_units = units;
        record.datum_offset = datum;
        record.zone_offset = 0;
        snprintf(record.header.name, sizeof(record.header.name), "OTC-KP1T-%s-reference", station);
        snprintf(record.station_id, sizeof(record.station_id), "KP1T-%s-reference", station);
        record.header.reference_station = -1;
        DB_HEADER_PUBLIC db = get_tide_db_header();
        int ref_index = db.number_of_records;
        if (!add_tide_record(&record, &db)) fail("cannot add unchanged comparison reference");
        TIDE_RECORD sub = record;
        sub.header.record_type = SUBORDINATE_STATION;
        sub.header.reference_station = ref_index;
        snprintf(sub.header.name, sizeof(sub.header.name), "OTC-KP1T-%s-folded", station);
        snprintf(sub.station_id, sizeof(sub.station_id), "KP1T-%s-folded", station);
        int minutes = (int)offset_min;
        if (offset_min != minutes) fail("comparison offset is not a whole minute");
        int encoded = (abs(minutes) / 60) * 100 + abs(minutes) % 60;
        if (minutes < 0) encoded = -encoded;
        sub.min_time_add = sub.max_time_add = encoded;
        if (type == 'R') {
            sub.min_level_multiply = sub.max_level_multiply = height;
            sub.min_level_add = sub.max_level_add = 0;
        } else if (type == 'A') {
            sub.min_level_multiply = sub.max_level_multiply = 1;
            sub.min_level_add = sub.max_level_add = height;
        } else fail("not an accepted R or A offset");
        sub.flood_begins = sub.ebb_begins = NULLSLACKOFFSET;
        if (!add_tide_record(&sub, &db)) fail("cannot add unchanged comparison offsets");
        printf("%s %d %u\n", station, ref_index, db.number_of_records - 1);
    }
    if (!feof(input)) fail("malformed comparison input");
    fclose(input);
    close_tide_db();
    return 0;
}
