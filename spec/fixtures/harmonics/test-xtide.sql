-- Minimal XTide SQL fixture for testing
-- Encoding: ISO-8859-1

COPY public.constituents (name, definition, speed) FROM stdin;
M2	Basic 2 -2 2 0 0 0 2 -2 0 0 0 0 0 78	28.984104
S2	Basic 2 0 0 0 0 0 0 0 0 0 0 0 0 1	30.0
N2	Basic 2 -3 2 1 0 0 2 -2 0 0 0 0 0 78	28.43973
K1	Basic 1 0 1 0 0 -90 0 0 -1 0 0 0 0 227	15.041069
O1	Basic 1 -2 1 0 0 90 2 -1 0 0 0 0 0 75	13.943036
P1	Basic 1 0 -1 0 0 90 0 0 0 0 0 0 0 1	14.958931
Q1	Basic 1 -3 1 1 0 90 2 -1 0 0 0 0 0 75	13.398661
K2	Basic 2 0 2 0 0 0 0 0 0 -1 0 0 0 235	30.082138
\.

COPY public.data_sets (index, name, station_id_context, station_id, lat, lng, timezone, country, units, min_dir, max_dir, legalese, notes, comments, source, restriction, date_imported, xfields, meridian, datumkind, datum, months_on_station, last_date_on_station, ref_index, min_time_add, min_level_add, min_level_multiply, max_time_add, max_level_add, max_level_multiply, flood_begins, ebb_begins, original_name, state) FROM stdin;
1	Boston, Massachusetts	\N	8443970	42.3584	-71.0511	:America/New_York	USA	ft	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	05:00:00	MLLW	0.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Boston, Massachusetts	MA
2	Portland, Casco Bay, Maine	\N	8418150	43.6567	-70.2483	:America/New_York	USA	ft	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	05:00:00	MLLW	0.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Portland, Casco Bay, Maine	ME
3	San Francisco, California	\N	9414290	37.8067	-122.4650	:America/Los_Angeles	USA	ft	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	08:00:00	MLLW	0.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	San Francisco, California	CA
4	Seattle, Washington	\N	9447130	47.6025	-122.3397	:America/Los_Angeles	USA	ft	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	08:00:00	MLLW	0.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Seattle, Washington	WA
5	Miami, Florida	\N	8723214	25.7743	-80.1308	:America/New_York	USA	ft	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	05:00:00	MLLW	0.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Miami, Florida	FL
6	Honolulu, Hawaii	\N	1612340	21.3067	-157.8667	:Pacific/Honolulu	USA	ft	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	10:00:00	MLLW	0.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Honolulu, Hawaii	HI
7	Golden Gate (depth 30 ft), San Francisco Bay, California Current	\N	SFB1203	37.8199	-122.4783	:America/Los_Angeles	USA	knots	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	08:00:00	MLLW	30.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Golden Gate (depth 30 ft), San Francisco Bay, California Current	CA
8	Golden Gate (depth 60 ft), San Francisco Bay, California Current	\N	SFB1203_60	37.8199	-122.4785	:America/Los_Angeles	USA	knots	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	08:00:00	MLLW	60.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Golden Gate (depth 60 ft), San Francisco Bay, California Current	CA
9	Boston Harbor (depth 15 ft), Massachusetts Current	\N	BOS0101	42.3480	-70.9700	:America/New_York	USA	knots	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	05:00:00	MLLW	15.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Boston Harbor (depth 15 ft), Massachusetts Current	MA
10	Puget Sound, Washington Current	\N	PUG0101	47.5500	-122.3500	:America/Los_Angeles	USA	knots	\N	\N	\N	\N	\N	NOAA	\N	\N	\N	08:00:00	MLLW	0.0	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	\N	Puget Sound, Washington Current	WA
\.

COPY public.constants (index, name, phase, amp) FROM stdin;
1	M2	112.3	4.25
1	S2	145.6	0.72
1	N2	85.4	0.89
1	K1	201.5	0.35
1	O1	198.7	0.28
2	M2	115.8	4.45
2	S2	148.2	0.68
2	N2	88.1	0.92
2	K1	205.3	0.32
2	O1	202.1	0.25
3	M2	342.5	1.85
3	S2	15.8	0.45
3	N2	315.2	0.38
3	K1	98.3	1.22
3	O1	95.6	0.78
4	M2	278.4	3.42
4	S2	305.7	0.85
4	N2	251.3	0.72
4	K1	145.2	2.15
4	O1	142.8	1.38
5	M2	325.1	1.15
5	S2	358.4	0.22
5	N2	298.7	0.24
5	K1	175.6	0.15
5	O1	172.3	0.12
6	M2	45.2	0.58
6	S2	78.5	0.12
6	N2	18.1	0.12
6	K1	285.4	0.48
6	O1	282.1	0.32
7	M2	125.4	2.85
7	S2	158.7	0.52
7	N2	98.3	0.58
7	K1	215.6	0.42
7	O1	212.3	0.35
8	M2	128.4	2.95
8	S2	161.7	0.55
8	N2	101.3	0.62
8	K1	218.6	0.45
8	O1	215.3	0.38
9	M2	118.5	1.45
9	S2	151.8	0.28
9	N2	91.4	0.32
9	K1	208.7	0.22
9	O1	205.4	0.18
10	M2	285.4	2.15
10	S2	318.7	0.42
10	N2	258.3	0.45
10	K1	152.6	0.85
10	O1	149.3	0.68
\.
