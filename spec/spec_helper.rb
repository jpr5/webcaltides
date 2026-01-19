# frozen_string_literal: true

# Coverage must be started before loading any application code
require 'simplecov'
SimpleCov.start do
    add_filter '/spec/'
    add_filter '/vendor/'

    add_group 'Models', 'models'
    add_group 'Clients', 'clients'
    add_group 'Lib', 'lib'
    add_group 'Core', %w[webcaltides.rb gps.rb server.rb]
end

ENV['RACK_ENV'] = 'test'

# Suppress logging during tests
require 'logger'
$LOG = Logger.new('/dev/null')

# Load the application
require_relative '../webcaltides'
require_relative '../server'

# Load all models
Dir[File.join(__dir__, '../models/*.rb')].each { |f| require f }

# Testing dependencies
require 'rspec'
require 'rack/test'
require 'webmock/rspec'
require 'vcr'
require 'timecop'
require 'json'

# Load support files
Dir[File.join(__dir__, 'support/**/*.rb')].each { |f| require f }

RSpec.configure do |config|
    # Enable flags like --only-failures and --next-failure
    config.example_status_persistence_file_path = 'spec/.rspec_status'

    # Disable RSpec exposing methods globally on `Module` and `main`
    config.disable_monkey_patching!

    # Run specs in random order
    config.order = :random

    # Seed global randomization
    Kernel.srand config.seed

    # Include Rack::Test methods in API specs
    config.include Rack::Test::Methods, type: :api

    # Configure expectations
    config.expect_with :rspec do |expectations|
        expectations.include_chain_clauses_in_custom_matcher_descriptions = true
        expectations.syntax = :expect
    end

    # Configure mocks
    config.mock_with :rspec do |mocks|
        mocks.verify_partial_doubles = true
    end

    # Shared context behaviors
    config.shared_context_metadata_behavior = :apply_to_host_groups

    # Filter run options
    config.filter_run_when_matching :focus

    # Timecop cleanup
    config.after(:each) do
        Timecop.return
    end

    # Stub sleep to speed up retry tests
    config.before(:each) do
        allow_any_instance_of(Object).to receive(:sleep)
    end

    # Reset WebMock after each test
    config.after(:each) do
        WebMock.reset!
    end
end

# Sinatra app helper for API tests
def app
    Server
end
