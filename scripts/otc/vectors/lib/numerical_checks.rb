# frozen_string_literal: true

module OTCCaseOracle
    class Runner
        # A direct hand sum from fixture numbers. It does not call Strict::Sum.
        def hand_function(table, terms, order: 0, mean: 0.0, sigma: 1)
            origins = {}
            lambda do |time, year|
                origin = origins[year] ||= Time.utc(year).to_i
                hours = (time - origin) / 3600.0
                row_index = year - table.fetch('first_year')
                value = order.zero? ? mean : 0.0
                terms.each do |name, amplitude, phase|
                    row = table.fetch('constituents').fetch(name)
                    speed = row.fetch('speed_deg_per_hour')
                    angle = speed * hours + row.fetch('v0u_deg').fetch(row_index) - phase
                    angle = (angle - 360.0 * (angle / 360.0).floor) * Math::PI / 180.0
                    a = amplitude * row.fetch('f').fetch(row_index)
                    value += order.zero? ? a * Math.cos(angle) : -a * speed * Math::PI / 180.0 * Math.sin(angle)
                end
                sigma * value
            end
        end

        def hand_functions(raw_table, terms, mean: 0.0, sigma: 1)
            [hand_function(raw_table, terms, mean: mean, sigma: sigma),
             hand_function(raw_table, terms, order: 1, sigma: sigma)]
        end

        def tide_terms(constituents)
            constituents.map { |term| term.values_at('name', 'amplitude_m', 'phase_deg') }
        end

        def current_terms(bin)
            bin.fetch('constituents').map { |term| term.values_at('name', 'major_amplitude_ms', 'major_phase_deg') }
        end

        def current_sigma(bin)
            flood = bin['mean_flood_dir_deg']
            flood.nil? || Math.cos((bin.fetch('azimuth_deg') - flood) * Math::PI / 180.0) >= 0 ? 1 : -1
        end

        def refine_root(function, root, year)
            a, b = root - 0.02, root + 0.02
            fa, fb = function.call(a, year), function.call(b, year)
            return root unless fa * fb <= 0 && !(fa.zero? && fb.zero?)
            while b - a > 1e-6
                mid = (a + b) / 2.0
                break if mid == a || mid == b
                fm = function.call(mid, year)
                if (fa.positive? && fm <= 0) || (fa.negative? && fm >= 0)
                    b = mid
                else
                    a, fa = mid, fm
                end
            end
            (a + b) / 2.0
        end

        def scan(function, start, stop, step_s = 5.0)
            start, stop = start.to_i, stop.to_i
            year = Time.at(start).utc.year
            a = start.to_f
            fa = function.call(a, year)
            roots = []
            while a < stop
                b = [a + step_s, stop.to_f].min
                y = Time.at(a).utc.year
                fa = function.call(a, y) if y != year
                year = y
                fb = function.call(b, year)
                if (fa.positive? && fb <= 0) || (fa.negative? && fb >= 0)
                    lo, hi, flo = a, b, fa
                    while hi - lo > 1e-6
                        mid = (lo + hi) / 2.0
                        break if mid == lo || mid == hi
                        fm = function.call(mid, year)
                        if (flo.positive? && fm <= 0) || (flo.negative? && fm >= 0)
                            hi = mid
                        else
                            lo, flo = mid, fm
                        end
                    end
                    root = (lo + hi) / 2.0
                    rounded = (root + 0.5).floor
                    roots << { r: root, kind: fa.positive? ? :down : :up } if rounded >= start && rounded < stop
                end
                a, fa = b, fb
            end
            roots
        end

        def scan_check(label, mode, args, table, list)
            if args['constituents']
                value, derivative = hand_functions(table, tide_terms(args.fetch('constituents')))
                counts = scan(derivative, args['start'], args['stop']).size
                expected = list.size
                raise ProofError, "#{label}: 5-second scan count #{counts}, strict #{expected}" unless counts == expected
            else
                bin = args.fetch('bin')
                value, derivative = hand_functions(table, current_terms(bin), mean: bin.fetch('mean_major_ms', 0.0), sigma: current_sigma(bin))
                observed = {}
                %w[max_flood max_ebb slack_before_flood slack_before_ebb].each do |type|
                    next if mode == 'scan_5s_peaks' && type.start_with?('slack')
                    next if mode == 'scan_5s_slacks' && type.start_with?('max')
                    function = type.start_with?('max') ? derivative : value
                    candidates = scan(function, args['start'], args['stop'])
                    count = candidates.count do |c|
                        if type == 'max_flood'
                            c[:kind] == :down && value.call(c[:r], Time.at(c[:r]).utc.year).positive?
                        elsif type == 'max_ebb'
                            c[:kind] == :up && value.call(c[:r], Time.at(c[:r]).utc.year).negative?
                        else
                            c[:kind] == (type == 'slack_before_flood' ? :up : :down)
                        end
                    end
                    wanted = list.count { |item| item['type'] == type }
                    raise ProofError, "#{label}: 5-second #{type} count #{count}, strict #{wanted}" unless count == wanted
                    observed[type] = count
                end
                counts, expected = observed, list.size
            end
            @checks['scans'] << { 'step' => label, 'mode' => mode, 'count' => counts, 'strict_count' => expected, 'pass' => true }
        end

        def search_band_checks(label, table, sum, function, value, hand, start, stop, order, slack: false)
            strict = Harmonics::Strict
            g0, gn = strict.grid(table, start.to_r, stop.to_r)
            raw = strict.crossings(function, ->(year) { sum.search_bound(year, order) }, g0, gn)
            raw.each { |candidate| candidate[:value] = value.call(candidate[:r], candidate[:year]) }
            precise = raw.map { |candidate| candidate.merge(r: refine_root(hand, candidate[:r], candidate[:year])) }
            band_errors = []
            strict.new_years(g0, gn).each do |time, year|
                old, new = hand.call(time, year), hand.call(time, year + 1)
                band_errors << "#{label}: band 4 at #{time}" if old.abs < 1e-9 || new.abs < 1e-9
                next unless old * new < 0
                left = precise.rindex { |c| c[:r] <= time }
                right = left ? left + 1 : 0
                l, r = left && precise[left], precise[right]
                if l && r && (3599.0..3601.0).cover?(r[:r] - l[:r])
                    band_errors << "#{label}: band 2 separation #{r[:r] - l[:r]}"
                end
            end
            strict.reconcile_new_years!(precise, g0, gn, function, value) do |left, right|
                slack || !strict::MORE_EXTREME.call(right, left)
            end
            precise.each_cons(2) do |left, right|
                gap = right[:r] - left[:r]
                band_errors << "#{label}: band 1 separation #{gap}" if (0.99..1.01).cover?(gap)
            end
            @checks['problems'].concat(band_errors)
            @checks['bands'] << { 'step' => label, 'searched_candidates' => precise.size, 'bands' => [1, 2, 4], 'pass' => band_errors.empty? }
        end

        def rounding_check(label, candidates, start, stop, exception, shifts: nil)
            problems, minimum, excepted = [], nil, 0
            candidates.each do |candidate|
                time = candidate[:r]
                time += shifts.call(candidate) if shifts
                rounded = (time + 0.5).floor
                next unless rounded >= start.to_i && rounded < stop.to_i
                distance = ((time - time.floor) - 0.5).abs
                minimum = minimum ? [minimum, distance].min : distance
                next unless distance < 0.01
                if exception
                    excepted += 1
                else
                    problems << "#{label}: band 3 root #{time}"
                end
            end
            @checks['problems'].concat(problems)
            @checks['bands'] << { 'step' => label, 'bands' => [3], 'nearest_half_second_s' => minimum,
                                  'excepted_roots' => excepted, 'pass' => problems.empty? }
        end

        def validate_exception(c, s, doc)
            declaration = c['band_exception']
            return false unless declaration
            ids = %w[predict-extremes/single-constituent predict-extremes/negative-amplitude]
            raise ProofError, "unlisted band exception #{c['id']}" unless ids.include?(c['id'])
            raise ProofError, "invalid exception #{c['id']}" unless declaration['band'] == 3 && declaration['event_time_tolerance_s'] == 1 &&
                declaration['case_id'] == c['id'] && declaration['root'] == c['root'] && declaration['release'] == c['release'] &&
                declaration['start'] == s.dig('args', 'start') && declaration['stop'] == s.dig('args', 'stop')
            source = doc.fetch('stations').flat_map { |st| st.fetch('constant_sets', []) }.find { |set| set['set_id'] == declaration['set_id'] }
            constituents = source.fetch('constituents')
            amplitude = c['id'].end_with?('negative-amplitude') ? -1.0 : 1.0
            raise ProofError, 'exception fixture scope changed' unless constituents.size == 1 && constituents[0]['name'] == 'M2' &&
                constituents[0]['amplitude_m'] == amplitude && constituents[0]['phase_deg'] == declaration['phase_deg'] &&
                # The approved exception requires this exact fixture coefficient.
                declaration['phase_deg'].eql?(20.903277) && # rubocop:disable Lint/FloatComparison
                declaration['amplitude_m'] == amplitude && declaration['set_id'] == 'OTC-P-0005/noaa' &&
                declaration['astro_table_id'] == 'otc-test-predict-a' && declaration['start'] == '2010-03-01T00:00:00Z' &&
                declaration['stop'] == '2010-03-31T00:00:00Z'
            true
        end

        def event_bands(label, c, s, args, raw_table)
            entry = s.fetch('strict').fetch('entry')
            return unless entry.include?('extremes') || entry.include?('current_events')
            strict = Harmonics::Strict
            astro, start, stop = args.values_at('astro', 'start', 'stop')
            exception = validate_exception(c, s, fixture(c))
            if entry == 'extremes_strict' || entry == 'folded_extremes'
                consts = args['constituents']
                if entry == 'folded_extremes'
                    consts = @engine.subordinate_folded_strict(args['ref_constituents'], args['chart_datum_term'], args['offsets'],
                                                             datum_shift: args['datum_shift'], astro: astro).fetch('constituents')
                end
                sum = strict.tide_sum(astro, consts)
                _hand, derivative = hand_functions(raw_table, tide_terms(consts))
                search_band_checks(label, astro, sum, ->(t, y) { sum.derivative(t, y) }, ->(t, y) { sum.value(t, y) }, derivative, start, stop, 1)
                candidates = strict.extreme_candidates(astro, consts, start, stop, 0)
                candidates.each { |v| v[:r] = refine_root(derivative, v[:r], v[:year]) }
                rounding_check(label, candidates, start, stop, exception)
            elsif entry == 'current_events_strict'
                bin = args.fetch('bin')
                b = strict.current_bin(astro, bin)
                sum, sigma, mean = b.values_at(:major, :sigma, :mean_major)
                value, derivative = hand_functions(raw_table, current_terms(bin), mean: mean, sigma: sigma)
                native_value = ->(t, y) { sigma * (mean + sum.value(t, y)) }
                native_derivative = ->(t, y) { sigma * sum.derivative(t, y) }
                search_band_checks(label + '/peaks', astro, sum, native_derivative, native_value, derivative, start, stop, 1)
                search_band_checks(label + '/slacks', astro, sum, native_value, ->(_t, _y) { 0.0 }, value, start, stop, 0, slack: true)
                candidates = strict.current_event_candidates(astro, bin, start, stop)
                candidates.each { |v| v[:r] = refine_root(v[:type].start_with?('slack') ? value : derivative, v[:r], v[:year]) }
                rounding_check(label, candidates, start, stop, false)
            else
                offset_bands(label, entry, args, raw_table)
            end
        end

        def offset_bands(label, entry, args, raw_table)
            strict = Harmonics::Strict
            astro, start, stop = args.values_at('astro', 'start', 'stop')
            if entry == 'subordinate_extremes_strict'
                offsets = strict.tide_offsets(args.fetch('offsets'))
                pad = [offsets.values_at(:time_high, :time_low).map { |v| v.abs * 60 }.max + 3600, 7200].max
                first, last = start - pad, stop + pad
                consts = args.fetch('ref_constituents')
                sum = strict.tide_sum(astro, consts)
                value, derivative = hand_functions(raw_table, tide_terms(consts))
                search_band_checks(label, astro, sum, ->(t, y) { sum.derivative(t, y) }, ->(t, y) { sum.value(t, y) }, derivative, first, last, 1)
                candidates = strict.extreme_candidates(astro, consts, first, last, args.fetch('chart_datum_term'))
                candidates.each { |v| v[:r] = refine_root(derivative, v[:r], v[:year]) }
                rounding_check(label, candidates, start, stop, false,
                               shifts: ->(c) { 60 * offsets[c[:kind] == :down ? :time_high : :time_low] })
            else
                offset = args.fetch('offset')
                retained = strict::CURRENT_OFFSET_KEYS.filter_map do |type, (adj, ratio)|
                    [type, offset.fetch(adj)] unless offset.fetch(adj).nil? || (ratio && offset.fetch(ratio).nil?)
                end.to_h
                pad = [retained.values.map { |v| v.abs * 60 }.max.to_f + 3600, 7200].max
                first, last = start - pad, stop + pad
                bin = args.fetch('ref_bin')
                b = strict.current_bin(astro, bin)
                sum, sigma, mean = b.values_at(:major, :sigma, :mean_major)
                value, derivative = hand_functions(raw_table, current_terms(bin), mean: mean, sigma: sigma)
                native_value = ->(t, y) { sigma * (mean + sum.value(t, y)) }
                search_band_checks(label + '/peaks', astro, sum, ->(t, y) { sigma * sum.derivative(t, y) }, native_value, derivative, first, last, 1)
                search_band_checks(label + '/slacks', astro, sum, native_value, ->(_t, _y) { 0.0 }, value, first, last, 0, slack: true)
                candidates = strict.current_event_candidates(astro, bin, first, last).select { |v| retained.key?(v[:type]) }
                candidates.each { |v| v[:r] = refine_root(v[:type].start_with?('slack') ? value : derivative, v[:r], v[:year]) }
                rounding_check(label, candidates, start, stop, false, shifts: ->(c) { 60 * retained.fetch(c[:type]) })
            end
        end

        def check_numeric(label, c, s, doc, args, list)
            raw_table = doc.fetch('astro_tables').find { |t| t['astro_table_id'] == s.dig('strict', 'args', 'astro') }
            if s.dig('strict', 'entry') == 'predict_strict' && !list.empty?
                value = hand_function(raw_table, tide_terms(args.fetch('constituents')))
                time = args.fetch('times').first
                hand = value.call(time.to_i, time.year) + args.fetch('datum_term')
                delta = (hand - list.first.fetch('height_m')).abs
                raise ProofError, "#{label}: independent hand sum differs #{delta}" unless delta <= 1e-9
                @checks['hand_sums'] << { 'step' => label, 'difference_m' => delta, 'pass' => true }
            end
            scan_check(label, s['strict']['crosscheck'], args, raw_table, list) if s.dig('strict', 'crosscheck')
            event_bands(label, c, s, args, raw_table)
        end
    end
end
