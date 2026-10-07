#!/usr/bin/env ruby
# frozen_string_literal: true

##
## Check upstream for XTide or TICON harmonics releases newer than the ones we
## know about. Run monthly by .github/workflows/harmonics-data-monitor.yml.
##
## Usage: ruby scripts/check_harmonics_releases.rb
##
## Exit codes: 0 = nothing new, 1 = new release found, 2 = a source could not
## be checked (fetch or parse failure), a baseline override is invalid, or the
## script itself failed.
##
## Bump the baselines below when we adopt or acknowledge a release.
## XTIDE_BASELINE (exactly 8 digits, YYYYMMDD) and TICON_BASELINE (a positive
## integer) environment variables override them. An empty value means unset.
##

require 'net/http'
require 'json'
require 'uri'

module HarmonicsReleaseCheck
    # Newest XTide harmonics release we know about (harmonics-dwf-YYYYMMDD-free).
    KNOWN_XTIDE_RELEASE = '20251228'
    # Newest TICON-N release we know about. We use TICON-3; TICON-4 is known
    # and its adoption is a separate decision.
    KNOWN_TICON_RELEASE = 4

    XTIDE_URL = 'https://flaterco.com/files/xtide/'
    TICON_URL = 'https://api.datacite.org/dois?query=TICON&page%5Bsize%5D=1000'

    # A release is detected from its date alone: any harmonics-dwf-YYYYMMDD not
    # followed by another digit, whatever comes after it (upstream lists -free
    # and -SQL variants, and moved from .tar.bz2 to .tar.xz in 2019). Captures
    # the file name (up to a quote, tag or whitespace) and the date.
    XTIDE_FILE_PATTERN = /(harmonics-dwf-(\d{8})(?!\d)[^\s"'<>]*)/
    # The -free variant, with or without more name parts after it.
    XTIDE_FREE_PATTERN = /\Aharmonics-dwf-\d{8}-free(?![A-Za-z0-9])/i
    # TICON then a 1- or 2-digit release number, with optional spaces or
    # dashes (hyphen or Unicode dash) between: TICON-5, TICON 5, TICON5,
    # TICON–5 (en dash), TICON-12. A longer number, such as the year in
    # "TICON 2025 workshop", is not a release.
    TICON_TITLE_PATTERN = /\ATICON[\s\-\u2010-\u2015]*(\d{1,2})(?!\d)/i
    # Signature and checksum files published next to an archive.
    SIDECAR_EXTENSIONS = %w[.sig .sha256 .md5 .asc].freeze

    XTIDE_BASELINE_PATTERN = /\A\d{8}\z/
    TICON_BASELINE_PATTERN = /\A[1-9]\d*\z/

    EXIT_OK           = 0
    EXIT_NEW_RELEASE  = 1
    EXIT_CHECK_FAILED = 2

    RETRY_DELAY = 5

    class CheckError < StandardError; end

    Result = Struct.new(:source, :status, :message, keyword_init: true)

    module_function

    def fetch(url)
        attempts = 0
        begin
            attempts += 1
            uri = URI(url)
            response = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https',
                                       open_timeout: 15, read_timeout: 30) do |http|
                http.get(uri.request_uri, 'User-Agent' => 'webcaltides-harmonics-monitor')
            end
            raise CheckError, "HTTP #{response.code} from #{url}" unless response.is_a?(Net::HTTPSuccess)

            response.body.to_s
        rescue StandardError => e
            if attempts < 2
                sleep RETRY_DELAY
                retry
            end
            raise if e.is_a?(CheckError)

            raise CheckError, "#{e.class}: #{e.message}"
        end
    end

    def check_xtide(baseline: KNOWN_XTIDE_RELEASE)
        files = fetch(XTIDE_URL).scan(XTIDE_FILE_PATTERN).uniq
        raise CheckError, "no harmonics-dwf-YYYYMMDD files found at #{XTIDE_URL}" if files.empty?

        # The newest date counts whatever files carry it, whatever the variant.
        # Name the -free archive for that date if there is one, else any
        # archive, and a signature or checksum sidecar only as a last resort.
        latest = files.map { |_, date| date }.max
        candidates = files.select { |_, date| date == latest }
        archives = candidates.reject { |name, _| SIDECAR_EXTENSIONS.include?(File.extname(name).downcase) }
        file, = archives.find { |name, _| name.match?(XTIDE_FREE_PATTERN) } || archives.first || candidates.first
        if latest > baseline.to_s
            Result.new(source: 'XTide', status: :new,
                       message: "NEW RELEASE #{file} (known: #{baseline}) #{XTIDE_URL}#{file}")
        else
            Result.new(source: 'XTide', status: :ok, message: "up to date, latest #{latest} (known: #{baseline})")
        end
    rescue StandardError => e
        Result.new(source: 'XTide', status: :error, message: "CHECK FAILED: #{e.class}: #{e.message}")
    end

    def check_ticon(baseline: KNOWN_TICON_RELEASE)
        body = fetch(TICON_URL)
        parsed = begin
            JSON.parse(body)
        rescue JSON::ParserError => e
            raise CheckError, "unparseable DataCite response: #{e.message}"
        end
        raise CheckError, 'unexpected DataCite response shape' unless parsed.is_a?(Hash)

        records = parsed.fetch('data', [])
        raise CheckError, 'unexpected DataCite response shape' unless records.is_a?(Array)

        total = parsed['meta']['total'] if parsed['meta'].is_a?(Hash)
        if total.is_a?(Integer) && total > records.size
            raise CheckError, "DataCite returned #{records.size} of #{total} records; results truncated at #{TICON_URL}"
        end

        releases = []
        records.each do |record|
            next unless record.is_a?(Hash) && record['attributes'].is_a?(Hash)

            attrs = record['attributes']
            Array(attrs['titles']).each do |t|
                next unless t.is_a?(Hash) && (m = t['title'].to_s.match(TICON_TITLE_PATTERN))

                releases << [m[1].to_i, attrs['doi'] || record['id']]
            end
        end
        raise CheckError, "no TICON-N records found at #{TICON_URL}" if releases.empty?

        number, doi = releases.max_by(&:first)
        if number > baseline.to_i
            Result.new(source: 'TICON', status: :new,
                       message: "NEW RELEASE TICON-#{number} (known: TICON-#{baseline}) https://doi.org/#{doi}")
        else
            Result.new(source: 'TICON', status: :ok,
                       message: "up to date, latest TICON-#{number} (known: TICON-#{baseline})")
        end
    rescue StandardError => e
        Result.new(source: 'TICON', status: :error, message: "CHECK FAILED: #{e.class}: #{e.message}")
    end

    # Returns the override from env, or default when it is unset or empty.
    # Raises CheckError when the override does not match pattern.
    def baseline_override(env, name, pattern, default, expected)
        value = env[name]
        return default if value.nil? || value.empty?
        raise CheckError, "invalid #{name} #{value.inspect}: must be #{expected}" unless value.match?(pattern)

        value
    end

    def run(env: ENV, out: $stdout, err: $stderr)
        xtide_baseline = baseline_override(env, 'XTIDE_BASELINE', XTIDE_BASELINE_PATTERN,
                                           KNOWN_XTIDE_RELEASE, 'exactly 8 digits (YYYYMMDD)')
        ticon_baseline = baseline_override(env, 'TICON_BASELINE', TICON_BASELINE_PATTERN,
                                           KNOWN_TICON_RELEASE, 'a positive integer').to_i

        results = [
            check_xtide(baseline: xtide_baseline),
            check_ticon(baseline: ticon_baseline)
        ]

        results.each { |r| out.puts "#{r.source}: #{r.message}" }

        if (path = env['GITHUB_STEP_SUMMARY']) && !path.empty?
            File.open(path, 'a') do |f|
                f.puts '## Harmonics data releases', ''
                results.each { |r| f.puts "- **#{r.source}**: #{r.message}" }
            end
        end

        return EXIT_CHECK_FAILED if results.any? { |r| r.status == :error }
        return EXIT_NEW_RELEASE  if results.any? { |r| r.status == :new }

        EXIT_OK
    rescue StandardError => e
        # Ruby exits 1 on an uncaught error, which would read as a new release.
        err.puts "CHECK FAILED: #{e.class}: #{e.message}"
        EXIT_CHECK_FAILED
    end
end

exit HarmonicsReleaseCheck.run if $PROGRAM_NAME == __FILE__
