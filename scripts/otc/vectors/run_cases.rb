# frozen_string_literal: true

require 'optparse'
require_relative 'lib/case_oracle'

options = {}
OptionParser.new do |parser|
    parser.banner = 'run_cases.rb --calls JSON --fixtures DIR --out DIR [--case REGEXP]'
    parser.on('--calls PATH') { |value| options[:calls] = value }
    parser.on('--fixtures PATH') { |value| options[:fixtures] = value }
    parser.on('--out PATH') { |value| options[:out] = value }
    parser.on('--case REGEXP') { |value| options[:filter] = Regexp.new(value) }
    parser.on('--strict-source PATH', 'An isolated strict-engine mutation; never edits the oracle') do |value|
        options[:strict_source] = value
    end
end.parse!

begin
    %i[calls fixtures out].each { |key| raise ArgumentError, "missing --#{key}" unless options[key] }
    oracle = OTCCaseOracle::Runner.new(**options)
    oracle.run
rescue OTCCaseOracle::ProofError, ArgumentError, KeyError => error
    warn "FAIL: #{error.message}"
    exit 1
end
