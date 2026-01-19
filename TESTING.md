# Testing WebCalTides

## Quick Start

```bash
# Run all tests
bundle exec rspec

# Run with verbose output
bundle exec rspec --format documentation

# Run specific test file
bundle exec rspec spec/clients/noaa_tides_spec.rb

# Run specific test by line number
bundle exec rspec spec/clients/noaa_tides_spec.rb:68
```

## Test Stack

| Gem | Purpose |
|-----|---------|
| rspec | Test framework |
| rack-test | HTTP endpoint testing for Sinatra |
| webmock | Stub external HTTP calls (error cases) |
| vcr | Record/replay API responses |
| timecop | Time travel for time-dependent tests |
| simplecov | Code coverage (reports to `coverage/`) |

## Directory Structure

```
spec/
├── spec_helper.rb          # Test configuration
├── support/
│   ├── vcr_setup.rb        # VCR cassette configuration
│   └── helpers.rb          # Shared test utilities
├── fixtures/
│   └── cassettes/          # Recorded API responses
├── unit/                   # Pure function tests
├── clients/                # API client tests
├── integration/            # Business logic tests
└── api/                    # HTTP endpoint tests
```

## VCR Cassettes

Tests that hit external APIs (NOAA, CHS) use VCR to record and replay HTTP responses. This allows tests to run offline and quickly in CI.

### Normal Test Run (uses recorded responses)

```bash
bundle exec rspec
```

### Re-record All Cassettes

When external APIs change their format or behavior:

```bash
VCR_RECORD=1 bundle exec rspec spec/clients/
```

This hits the live APIs and overwrites all existing cassettes.

### Re-record Specific Cassettes

Delete the cassettes you want to refresh, then run tests:

```bash
# Delete NOAA tides cassettes
rm -rf spec/fixtures/cassettes/Clients_NoaaTides/

# Re-run those tests (will record fresh responses)
bundle exec rspec spec/clients/noaa_tides_spec.rb
```

### Cassette Location

Cassettes are stored by spec class and test description:

```
spec/fixtures/cassettes/
├── Clients_NoaaTides/
│   ├── _tide_stations/
│   │   └── with_real_API/
│   │       └── fetches_tide_stations_from_NOAA_API.yml
│   └── _tide_data_for/
│       └── fetches_tide_data_for_a_station.yml
├── Clients_NoaaCurrents/
└── Clients_ChsTides/
```

## Test Types

### Unit Tests (`spec/unit/`)

Pure function tests with no external dependencies.

```bash
bundle exec rspec spec/unit/
```

### Client Tests (`spec/clients/`)

Test API clients with VCR cassettes for happy paths, WebMock stubs for error cases.

```bash
# Uses recorded cassettes
bundle exec rspec spec/clients/

# Re-record from live APIs
VCR_RECORD=1 bundle exec rspec spec/clients/
```

### API Tests (`spec/api/`)

Test HTTP endpoints via rack-test. No external calls.

```bash
bundle exec rspec spec/api/
```

### Integration Tests (`spec/integration/`)

Test business logic with mocked dependencies.

```bash
bundle exec rspec spec/integration/
```

## Code Coverage

Coverage reports are generated automatically to `coverage/index.html`.

```bash
bundle exec rspec
open coverage/index.html
```

## CI/CD

GitHub Actions runs tests on push to `master` and `jpr5/*` branches. Tests use recorded VCR cassettes so no network calls are made in CI.

See `.github/workflows/test.yml` for configuration.

## Skipped/Pending Tests

Some tests are marked pending:

- **Harmonics tests**: Require optional harmonics data files (`data/*.tcd`)
- Tests that document known bugs are skipped with explanatory messages

## Troubleshooting

### Tests fail with "VCR cassette not found"

The cassette was deleted or never recorded. Run:

```bash
VCR_RECORD=1 bundle exec rspec path/to/failing_spec.rb
```

### Tests fail with "Real HTTP connections are disabled"

A test is making an HTTP call without a VCR cassette. Either:
1. Add `:vcr` tag to the test
2. Use WebMock to stub the request
3. Record a cassette with `VCR_RECORD=1`

### Cassettes contain stale data

External API changed. Re-record:

```bash
VCR_RECORD=1 bundle exec rspec spec/clients/
git diff spec/fixtures/cassettes/  # Review changes
```

### Time-dependent tests fail

Tests using `freeze_time` should work consistently. If not, check that the test properly freezes time before making assertions.
