#!/usr/bin/env ruby
# frozen_string_literal: true

# Dumps the constituent table of the TCD the harmonics engine reads: per constituent
# the speed (deg/h) and, for every year 1900-2100, V0+u (deg, Jan 1 00:00 UTC) and f.
# It makes the same tcd gem calls as Harmonics::Engine#calculate_tcd_nodal_factors
# (equilibrium_for_year and node_factor_for_year, with the TCD's first year), so the
# GESLA fit uses the engine's own values. The output records the TCD SHA-256.
#
#   ruby scripts/gesla/dump_tcd.rb [--tcd PATH] --out PATH [--require M2,EP2,...]
#
# --require checks that each listed name maps to a TCD constituent (TICON spellings
# are mapped as in tcd_constituent_name); an unknown name exits non-zero.

require 'bundler/setup'
require 'digest'
require 'json'
require 'optparse'
require 'tcd'

module GeslaTcdDump
    DEFAULT_TCD = File.expand_path('../../data/latest-xtide.tcd', __dir__)
    YEARS = (1900..2100).freeze

    # TICON names that the TCD (and so the harmonics engine) spells differently.
    # Same table as scripts/build_ticon_dataset.rb on harmonics-b2-fixes (65cbf52).
    TICON_TO_TCD_NAMES = {
        'MI2' => 'MU2',
        'NI2' => 'NU2',
        'LM2' => 'LDA2',
        'EP2' => 'EPS2',
        'MKS' => 'MKS2'
    }.freeze

    module_function

    # Returns the TCD name for a constituent name, mapping the TICON spellings.
    # Raises when the TCD does not define the name.
    def tcd_constituent_name(raw_name, tcd_names)
        upcased = raw_name.to_s.strip.upcase
        name = TICON_TO_TCD_NAMES.fetch(upcased, upcased)
        return name if tcd_names.include?(name)

        raise ArgumentError, "constituent #{raw_name.inspect} has no TCD definition; " \
                             'map it in TICON_TO_TCD_NAMES'
    end

    # Returns the dump as a Hash with sorted keys:
    # { 'aliases', 'constituents' => { name => { 'f', 'speed', 'v0u' } }, 'tcd_sha256', 'years' }.
    def dump(tcd_path = DEFAULT_TCD, years: YEARS)
        constituents = TCD.open(tcd_path) do |db|
            unless db.year_range.cover?(years.first) && db.year_range.cover?(years.last)
                raise ArgumentError, "TCD years #{db.year_range} do not cover #{years}"
            end

            first_year = db.year_range.first
            db.constituents.to_h do |c|
                [c.name, { 'f' => years.map { |y| c.node_factor_for_year(y, first_year) },
                           'speed' => c.speed,
                           'v0u' => years.map { |y| c.equilibrium_for_year(y, first_year) } }]
            end
        end
        bad = constituents.select { |_, c| (c['f'] + c['v0u']).any?(&:nil?) }.keys
        raise ArgumentError, "TCD has no V0+u or f for #{bad.join(' ')}" unless bad.empty?

        { 'aliases' => TICON_TO_TCD_NAMES.sort.to_h,
          'constituents' => constituents.sort.to_h,
          'tcd_sha256' => Digest::SHA256.file(tcd_path).hexdigest,
          'years' => [years.first, years.last] }
    end

    def main(argv)
        opts = { tcd: DEFAULT_TCD, require: [] }
        OptionParser.new do |o|
            o.on('--tcd PATH') { |v| opts[:tcd] = v }
            o.on('--out PATH') { |v| opts[:out] = v }
            o.on('--require NAMES', Array) { |v| opts[:require] = v }
        end.parse!(argv)
        abort 'usage: dump_tcd.rb [--tcd PATH] --out PATH [--require NAMES]' unless opts[:out]

        table = dump(opts[:tcd])
        opts[:require].each { |n| tcd_constituent_name(n, table['constituents'].keys) }

        tmp = "#{opts[:out]}.tmp.#{$$}"
        File.binwrite(tmp, JSON.generate(table))
        File.rename(tmp, opts[:out])
        puts "years #{table['years'].join('-')} n=#{table['constituents'].size} tcd_sha256=#{table['tcd_sha256']}"
    rescue ArgumentError => e
        File.unlink(tmp) if tmp && File.exist?(tmp)
        warn "dump_tcd: #{e.message}"
        exit 1
    end
end

GeslaTcdDump.main(ARGV) if $PROGRAM_NAME == __FILE__
