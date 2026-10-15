# frozen_string_literal: true

require 'optparse'
require_relative 'lib/vector_generator'

options = {}
OptionParser.new do |parser|
    parser.banner = 'generate.rb --release JSON --run-dir PATH --output JSON [--only NOAA_ID]'
    parser.on('--release PATH') { |value| options[:release] = value }
    parser.on('--run-dir PATH') { |value| options[:run_dir] = value }
    parser.on('--output PATH') { |value| options[:output] = value }
    parser.on('--only ID') { |value| options[:only] = value }
    parser.on('--instant UTC') { |value| options[:instant] = value }
    parser.on('--strict-source PATH', 'Isolated strict-engine source for a mutation proof') { |value| options[:strict_source] = value }
end.parse!

begin
    %i[release run_dir output].each { |key| raise ArgumentError, "missing --#{key}" unless options[key] }
    OTCVectors::Generator.new(**options).run
rescue OTCCaseOracle::ProofError, ArgumentError, KeyError, Harmonics::Strict::Error => error
    warn "FAIL: #{error.message}"
    exit 1
end
