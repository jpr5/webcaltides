# frozen_string_literal: true

require 'vcr'

VCR.configure do |config|
  config.cassette_library_dir = 'spec/fixtures/cassettes'
  config.hook_into :webmock
  config.configure_rspec_metadata!

  # Allow localhost connections (for rack-test)
  config.ignore_localhost = true

  # Record mode:
  #   VCR_RECORD=1 - re-record all cassettes from live APIs
  #   (unset)      - use existing cassettes, record only new requests
  record_mode = ENV['VCR_RECORD'] ? :all : :new_episodes

  config.default_cassette_options = {
    record: record_mode,
    match_requests_on: [:method, :uri, :body],
    allow_playback_repeats: true
  }

  # Filter sensitive data from cassettes
  config.filter_sensitive_data('<GEONAMES_USERNAME>') { ENV['GEONAMES_USERNAME'] }
  config.filter_sensitive_data('<GOOGLE_MAPS_API_KEY>') { ENV['GOOGLE_MAPS_API_KEY'] }
  config.filter_sensitive_data('<GEOAPIFY_API_KEY>') { ENV['GEOAPIFY_API_KEY'] }

  # Allow real HTTP connections when recording
  config.allow_http_connections_when_no_cassette = false
end

RSpec.configure do |config|
  # Use VCR cassette automatically for examples tagged with :vcr
  config.around(:each, :vcr) do |example|
    cassette_name = example.metadata[:vcr]
    if cassette_name.is_a?(String)
      VCR.use_cassette(cassette_name) { example.run }
    else
      # Auto-generate cassette name from example description
      VCR.use_cassette(example.metadata[:full_description].parameterize) { example.run }
    end
  end
end
