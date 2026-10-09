# frozen_string_literal: true

require 'tcd'

module Harmonics
    # The strict prediction mode that makes the OpenTideConstants SDK reference
    # vectors (OTC SDK prediction spec, revision 14: section 4 for the rules,
    # section 6.1 for this mode).  It takes an OTC set's constants directly and
    # applies every rule exactly as the SDKs do:
    #
    # - no station lookup, no TCD station record and no datum_offset: heights are
    #   the harmonic sum above the set's mean level, plus only the datum term the
    #   caller passes;
    # - speed, V0+u and f come from the astronomical table (the TCD by default),
    #   never from the set's own speed_deg_per_hour;
    # - meridian 0, with t in hours since 1 January 00:00 UTC of the year;
    # - a constituent with no table row, or any evaluated instant (grid padding
    #   included) in a year outside the table, raises Strict::Error and is never
    #   skipped or predicted in legacy mode;
    # - every input is checked where it enters: a missing key, a nil, a String
    #   or a non-finite number raises invalid_argument, never a NaN or an empty
    #   result;
    # - extremes, current maxima and slacks come from the specified search
    #   (6-minute grid, root-free subdivision to 0.703125 s, bisection to 0.01 s,
    #   New Year reconciliation, close-root filter, rounding to whole seconds).
    #
    # The default engine paths do not use any of this, so the site's output does
    # not change.  Engine#predict_strict and the other *_strict methods (at the
    # end of this file) are the entry points.
    module Strict
        # A refusal.  code is the SDK error code (spec section 5.4):
        # unsupported_constituent, time_out_of_range, extremes_only,
        # datum_unavailable or invalid_argument.
        class Error < StandardError
            attr_reader :code

            def initialize(code, message)
                @code = code
                super("#{code}: #{message}")
            end
        end

        STEP_S = 360                # grid step (section 4.6 step 1)
        PAD_S = 3600                # grid padding on each side
        MIN_LEAF_DEPTH = 9          # 360 s / 2**9 = 0.703125 s minimum leaf
        BISECT_S = 0.01             # bisection stops at a bracket this wide
        CLUSTER_S = 1.0             # close-root filter (step 6)
        NEW_YEAR_S = 3600           # New Year reconciliation threshold (step 6a)
        SPEED_NULL_MS = 1e-9        # direction is null below this speed
        SUBORDINATE_PAD_MIN_S = 7200
        CURRENT_EVENT_TYPES = %w[max_flood max_ebb slack_before_flood slack_before_ebb].freeze

        # ---- input checks ----

        def self.invalid(message)
            raise Error.new('invalid_argument', message)
        end

        # A finite real number (Integer, Float or Rational), never nil or a String.
        def self.number(x, what)
            invalid("#{what} must be a finite number, got #{x.inspect}") unless x.is_a?(Numeric) && x.real? && x.finite?
            x
        end

        def self.hash_arg(h, what)
            invalid("#{what} must be a Hash, got #{h.class}") unless h.is_a?(Hash)
            h
        end

        def self.array_arg(a, what)
            invalid("#{what} must be an Array, got #{a.class}") unless a.is_a?(Array)
            a
        end

        # A required number.
        def self.required(h, key, what)
            invalid("#{what} has no #{key}") unless h.key?(key)
            number(h[key], "#{what} #{key}")
        end

        # An optional number: absent gives default; an explicit null is an error.
        def self.optional(h, key, what, default = nil)
            return default unless h.key?(key)

            number(h[key], "#{what} #{key}")
        end

        # A required key whose value may be null (a current offset).
        def self.nullable(h, key, what)
            invalid("#{what} has no #{key}") unless h.key?(key)
            h[key].nil? ? nil : number(h[key], "#{what} #{key}")
        end

        # Seconds since the epoch, exactly, from a Time or a finite number.
        def self.seconds(time, what = 'time')
            return time.to_r if time.is_a?(Time)

            number(time, what).to_r
        end

        # Per-year astronomical values: for each constituent its speed (degrees
        # per hour), V0+u (degrees, at 1 January 00:00 UTC) and f, for
        # first_year..last_year.  The same shape as an OTC release's
        # astro_tables entry (spec section 3.3).  A row with no speed is an
        # unsupported constituent; a value that is not a finite number is an
        # invalid table, raised when it is used.
        class AstroTable
            Row = Struct.new(:name, :speed, :v0u, :f)

            attr_reader :first_year, :last_year

            # The tables of a TCD file, as the default engine's tcd nodal mode
            # reads them.
            def self.from_tcd(path)
                TCD.open(path) do |db|
                    years = db.year_range
                    rows = db.constituents.to_h { |c| [c.name, Row.new(c.name, c.speed, c.equilibrium.dup, c.node_factors.dup)] }
                    new(years.first, years.last, rows)
                end
            end

            # From a hash shaped like an astro_tables entry: first_year,
            # last_year and constituents => { name => { speed_deg_per_hour,
            # v0u_deg, f } }.
            def self.from_h(table)
                Strict.hash_arg(table, 'astronomical table')
                rows = Strict.hash_arg(table['constituents'], 'astronomical table constituents').to_h do |name, r|
                    Strict.hash_arg(r, "astronomical table row #{name}")
                    [name, Row.new(name, r['speed_deg_per_hour'], r['v0u_deg'], r['f'])]
                end
                new(table['first_year'], table['last_year'], rows)
            end

            def initialize(first_year, last_year, rows)
                unless first_year.is_a?(Integer) && last_year.is_a?(Integer) && first_year <= last_year
                    Strict.invalid("astronomical table years #{first_year.inspect}-#{last_year.inspect} are not a range of years")
                end

                n = last_year - first_year + 1
                rows.each_value do |row|
                    Strict.number(row.speed, "speed of #{row.name}") unless row.speed.nil?
                    unless row.v0u.is_a?(Array) && row.f.is_a?(Array) && row.v0u.size == n && row.f.size == n
                        Strict.invalid("astronomical table row #{row.name} must have #{n} V0+u and f values")
                    end
                end
                @first_year = first_year
                @last_year = last_year
                @rows = rows
            end

            def row(name)
                row = @rows[name]
                raise Error.new('unsupported_constituent', "no astronomical table row for constituent #{name}") unless row
                raise Error.new('unsupported_constituent', "constituent #{name} has no speed in the astronomical table") if row.speed.nil?

                row
            end

            def check_year(year)
                return if year >= @first_year && year <= @last_year

                raise Error.new('time_out_of_range', "year #{year} is outside the astronomical table (#{@first_year}-#{@last_year})")
            end

            # [V0+u, f] of a row for a year.
            def values(row, year)
                check_year(year)
                i = year - @first_year
                [Strict.number(row.v0u[i], "V0+u of #{row.name} in #{year}"), Strict.number(row.f[i], "f of #{row.name} in #{year}")]
            end
        end

        # One harmonic sum, terms in file order: [[name, amplitude, phase], ...],
        # already checked.  Every name is looked up in the table when the sum is
        # built.
        class Sum
            attr_reader :table

            def initialize(table, terms)
                @table = table
                @terms = terms.map { |name, amp, phase| [table.row(name), amp.to_f, phase.to_f] }
                @years = {}
            end

            # [omega, A, F*H, g, omega_hat] per term for a year.
            def coeffs(year)
                @years[year] ||= begin
                    @table.check_year(year)
                    @terms.map do |row, amp, phase|
                        a, f = @table.values(row, year)
                        [row.speed, a, f * amp, phase, row.speed * Math::PI / 180.0]
                    end
                end
            end

            # The sum at t (seconds since the epoch) with year's tables.
            def value(t, year)
                d = (t - Strict.year_start(year)) / 3600.0
                sum = 0.0
                coeffs(year).each { |w, a, fh, g, _| sum += fh * Math.cos(Strict.rad(w * d + a - g)) }
                sum
            end

            # Its derivative, per hour.
            def derivative(t, year)
                d = (t - Strict.year_start(year)) / 3600.0
                sum = 0.0
                coeffs(year).each { |w, a, fh, g, wh| sum += fh * wh * Math.sin(Strict.rad(w * d + a - g)) }
                -sum
            end

            # Bound on |second derivative| for the year: sum of F*H*omega_hat**2.
            def bound2(year)
                coeffs(year).sum(0.0) { |_, _, fh, _, wh| fh.abs * wh * wh }
            end

            # Bound on |first derivative| for the year: sum of F*H*omega_hat.
            def bound1(year)
                coeffs(year).sum(0.0) { |_, _, fh, _, wh| fh.abs * wh }
            end
        end

        # The signed "more extreme" rule of section 4.6 steps 6a and 6 (and 4.7):
        # for a maximum (a high, or a maximum of W) the greater value, for a
        # minimum the lesser.  Strict, so the earlier candidate wins a tie.
        MORE_EXTREME = lambda do |x, y|
            x[:kind] == :down ? x[:value] > y[:value] : x[:value] < y[:value]
        end

        module_function

        # [0, 360) by the section 4.4 rule: x - 360*floor(x/360), and a result
        # of 360.0 after rounding (from a tiny negative x) becomes 0.0.
        def reduce_deg(x)
            r = x - 360.0 * (x / 360.0).floor
            r == 360.0 ? 0.0 : r
        end

        def rad(x)
            reduce_deg(x) * Math::PI / 180.0
        end

        def year_start(year)
            (@year_starts ||= {})[year] ||= Time.utc(year).to_i
        end

        def year_of(t)
            Time.at(t).utc.year
        end

        def sign(x)
            x.positive? ? 1 : (x.negative? ? -1 : 0)
        end

        # Checked [[name, amplitude, phase], ...] from a list of hashes: not
        # empty, every name a String used once, every value a finite number.
        def terms(constituents, what, amp_key, phase_key)
            array_arg(constituents, "#{what} constituents")
            invalid("#{what} has no constituents") if constituents.empty?
            names = {}
            constituents.map do |c|
                hash_arg(c, "#{what} constituent")
                name = c['name']
                invalid("#{what} constituent name must be a non-empty String, got #{name.inspect}") unless name.is_a?(String) && !name.empty?
                invalid("#{what} has constituent #{name} twice") if names[name]

                names[name] = true
                [name, required(c, amp_key, "#{what} constituent #{name}"), required(c, phase_key, "#{what} constituent #{name}")]
            end
        end

        def tide_sum(table, constituents)
            Sum.new(table, terms(constituents, 'the set', 'amplitude_m', 'phase_deg'))
        end

        def times_arg(times)
            array_arg(times, 'times').map { |t| seconds(t) }
        end

        # [start, stop] in exact seconds; raises invalid_argument for start > stop.
        def window(start, stop)
            start = seconds(start, 'start')
            stop = seconds(stop, 'stop')
            invalid("start #{start.to_f} is after stop #{stop.to_f}") if start > stop

            [start, stop]
        end

        def datum_term_arg(datum_term, what = 'datum_term')
            number(datum_term, what).to_f
        end

        # ---- heights (section 4.4) ----

        # The harmonic sum plus datum_term at each instant, with the year of the
        # instant.
        def heights(table, constituents, times, datum_term = 0.0)
            sum = tide_sum(table, constituents)
            datum_term = datum_term_arg(datum_term)
            times_arg(times).map do |time|
                t = time.to_f
                sum.value(t, year_of(t)) + datum_term
            end
        end

        # ---- the search (section 4.6 steps 1 to 5) ----

        # [g0, gn]: the padded 6-minute grid of [start, stop), in whole seconds.
        # Every grid point must be in a table year (section 4.3).
        def grid(table, start, stop)
            g0 = (start / STEP_S).floor * STEP_S - PAD_S
            gn = g0 + ((stop + PAD_S - g0) / STEP_S).ceil * STEP_S
            table.check_year(year_of(g0))
            table.check_year(year_of(gn))
            [g0, gn]
        end

        # 1 January 00:00 UTC instants strictly inside the grid, with the old year.
        def new_years(g0, gn)
            (year_of(g0) + 1..year_of(gn)).filter_map do |y|
                t = year_start(y)
                [t, y - 1] if t > g0 && t < gn
            end
        end

        # Sign changes of f over the grid.  f.call(t, year) is the function,
        # bound.call(year) bounds |f'| per hour.  Each grid step uses the year of
        # its left end, at both ends and inside.  Returns candidates
        # { r:, kind:, year: } in time order; kind :down is f going from > 0 to
        # <= 0, :up from < 0 to >= 0.
        def crossings(f, bound, g0, gn)
            out = []
            a = g0
            year = nil
            fb = nil
            while a < gn
                b = a + STEP_S
                step_year = year_of(a)
                fa = step_year == year ? fb : f.call(a.to_f, step_year)
                year = step_year
                fb = f.call(b.to_f, year)
                subdivide(f, bound.call(year), year, a.to_f, b.to_f, fa, fb, 0, out)
                a = b
            end
            out
        end

        # Root-free test, then split to the minimum leaf (step 3).
        def subdivide(f, m, year, a, b, fa, fb, depth, out)
            return if fa.abs + fb.abs > m * ((b - a) / 3600.0)

            if depth == MIN_LEAF_DEPTH
                bracket(f, year, a, b, fa, fb, out)
                return
            end

            mid = (a + b) / 2.0
            fm = f.call(mid, year)
            subdivide(f, m, year, a, mid, fa, fm, depth + 1, out)
            subdivide(f, m, year, mid, b, fm, fb, depth + 1, out)
        end

        # Bracket a leaf and bisect to 0.01 s (steps 4 and 5).
        def bracket(f, year, a, b, fa, fb, out)
            kind = if fa.positive? && fb <= 0 then :down
                   elsif fa.negative? && fb >= 0 then :up
                   end
            return unless kind

            while b - a > BISECT_S
                mid = (a + b) / 2.0
                fm = f.call(mid, year)
                if kind == :down ? fm.positive? : fm.negative?
                    a = mid
                else
                    b = mid
                end
            end
            out << { r: (a + b) / 2.0, kind: kind, year: year }
        end

        # New Year reconciliation (step 6a).  f is the searched function and
        # value.call(t, year) the candidate's value.  On a doubled event, keep
        # decides which survives: true keeps L.
        def reconcile_new_years!(cands, g0, gn, f, value, &keep_left)
            new_years(g0, gn).each do |t, y|
                s_old = sign(f.call(t.to_f, y))
                s_new = sign(f.call(t.to_f, y + 1))
                next if s_old.zero? || s_new.zero? || s_old == s_new

                li = cands.rindex { |c| c[:r] <= t }
                ri = li ? li + 1 : 0
                l = li && cands[li]
                r = cands[ri]
                if l && r && r[:r] - l[:r] < NEW_YEAR_S
                    cands.delete_at(keep_left.call(l, r) ? ri : li)
                else
                    kind = s_new.positive? ? :up : :down
                    cands.insert(ri, { r: t.to_f, kind: kind, year: y + 1, value: value.call(t.to_f, y + 1) })
                end
            end
            cands
        end

        # Maximal runs of candidates each less than 1 s after the one before.
        def clusters(cands)
            cands.slice_when { |x, y| y[:r] - x[:r] >= CLUSTER_S }.to_a
        end

        # Close-root filter for extremes (step 6): an even cluster is dropped; an
        # odd one keeps the candidate of its first type that more_extreme ranks
        # first, the earliest on a tie.
        def filter_extrema(cands, &more_extreme)
            clusters(cands).flat_map do |cluster|
                next [] if cluster.size.even?

                kind = cluster.first[:kind]
                best = nil
                cluster.each { |c| best = c if c[:kind] == kind && (best.nil? || more_extreme.call(c, best)) }
                [best]
            end
        end

        # Close-root filter for slacks (section 4.7): an odd cluster keeps the
        # candidate at index 2*floor((n - 1)/4).
        def filter_slacks(cands)
            clusters(cands).flat_map do |cluster|
                cluster.size.even? ? [] : [cluster[2 * ((cluster.size - 1) / 4)]]
            end
        end

        # Rounds to whole seconds and keeps start <= time < stop (step 7).
        # Each candidate keeps its unrounded r for the offset methods.
        def report(cands, start, stop)
            cands.sort_by { |c| c[:r] }.filter_map do |c|
                time = (c[:r] + 0.5).floor
                next unless time >= start && time < stop

                c.merge(time: time)
            end
        end

        # ---- extremes (section 4.6) ----

        # Reported candidates { r:, time:, kind:, value: } of [start, stop).
        def extreme_candidates(table, constituents, start, stop, datum_term)
            sum = tide_sum(table, constituents)
            datum_term = datum_term_arg(datum_term)
            start, stop = window(start, stop)
            return [] if start == stop

            g0, gn = grid(table, start, stop)
            dh = ->(t, y) { sum.derivative(t, y) }
            value = ->(t, y) { sum.value(t, y) + datum_term }
            cands = crossings(dh, ->(y) { sum.bound2(y) }, g0, gn)
            cands.each { |c| c[:value] = value.call(c[:r], c[:year]) }
            reconcile_new_years!(cands, g0, gn, dh, value) { |l, r| !MORE_EXTREME.call(r, l) }
            report(filter_extrema(cands, &MORE_EXTREME), start, stop)
        end

        def extremes(table, constituents, start, stop, datum_term = 0.0)
            extreme_candidates(table, constituents, start, stop, datum_term).map do |c|
                { 'time' => Time.at(c[:time]).utc, 'type' => c[:kind] == :down ? 'high' : 'low', 'height' => c[:value] }
            end
        end

        # ---- currents (section 4.7) ----

        # The checked bin: [major sum, minor sum, sigma, mean major, mean minor,
        # azimuth, flood direction, ebb direction].
        def current_bin(table, bin)
            hash_arg(bin, 'current bin')
            consts = bin['constituents']
            major = Sum.new(table, terms(consts, 'the current bin', 'major_amplitude_ms', 'major_phase_deg'))
            minor = Sum.new(table, terms(consts, 'the current bin', 'minor_amplitude_ms', 'minor_phase_deg'))
            azimuth = required(bin, 'azimuth_deg', 'the current bin')
            flood = optional(bin, 'mean_flood_dir_deg', 'the current bin')
            ebb = optional(bin, 'mean_ebb_dir_deg', 'the current bin')
            {
                major: major, minor: minor,
                sigma: flood.nil? || Math.cos((azimuth - flood) * Math::PI / 180.0) >= 0 ? 1 : -1,
                mean_major: optional(bin, 'mean_major_ms', 'the current bin', 0.0).to_f,
                mean_minor: optional(bin, 'mean_minor_ms', 'the current bin', 0.0).to_f,
                azimuth: azimuth.to_f, flood_dir: flood, ebb_dir: ebb
            }
        end

        def currents(table, bin, times, minor_sign = 1)
            b = current_bin(table, bin)
            invalid("minor_sign must be +1 or -1, got #{minor_sign.inspect}") unless minor_sign.is_a?(Numeric) && [1, -1].include?(minor_sign)
            times = times_arg(times)
            theta = b[:azimuth] * Math::PI / 180.0
            theta_minor = (b[:azimuth] + minor_sign * 90.0) * Math::PI / 180.0
            times.map do |time|
                t = time.to_f
                year = year_of(t)
                u = b[:mean_major] + b[:major].value(t, year)
                v = b[:mean_minor] + b[:minor].value(t, year)
                e = u * Math.sin(theta) + v * Math.sin(theta_minor)
                n = u * Math.cos(theta) + v * Math.cos(theta_minor)
                speed = Math.hypot(e, n)
                {
                    'velocity_major_ms' => b[:sigma] * u,
                    'velocity_minor_ms' => b[:sigma] * v,
                    'speed_ms' => speed,
                    'direction_deg' => speed < SPEED_NULL_MS ? nil : reduce_deg(Math.atan2(e, n) * 180.0 / Math::PI)
                }
            end
        end

        # Reported candidates { r:, time:, type:, value:, direction: } of [start, stop).
        def current_event_candidates(table, bin, start, stop)
            b = current_bin(table, bin)
            start, stop = window(start, stop)
            return [] if start == stop

            sigma = b[:sigma]
            major = b[:major]
            mean_major = b[:mean_major]
            w = ->(t, y) { sigma * (mean_major + major.value(t, y)) }
            dw = ->(t, y) { sigma * major.derivative(t, y) }
            g0, gn = grid(table, start, stop)

            # Maxima (:down of W') and minima (:up of W'), by the signed rule.
            peaks = crossings(dw, ->(y) { major.bound2(y) }, g0, gn)
            peaks.each { |c| c[:value] = w.call(c[:r], c[:year]) }
            reconcile_new_years!(peaks, g0, gn, dw, w) { |l, r| !MORE_EXTREME.call(r, l) }
            peaks = filter_extrema(peaks, &MORE_EXTREME).filter_map do |c|
                if c[:kind] == :down && c[:value].positive?
                    c.merge(type: 'max_flood', direction: b[:flood_dir])
                elsif c[:kind] == :up && c[:value].negative?
                    c.merge(type: 'max_ebb', direction: b[:ebb_dir])
                end
            end

            # Slacks: zeros of W, :up before flood and :down before ebb.
            slacks = crossings(w, ->(y) { major.bound1(y) }, g0, gn)
            reconcile_new_years!(slacks, g0, gn, w, ->(_t, _y) { 0.0 }) { true }
            slacks = filter_slacks(slacks).map do |c|
                c.merge(type: c[:kind] == :up ? 'slack_before_flood' : 'slack_before_ebb', value: 0.0, direction: nil)
            end

            report(peaks + slacks, start, stop)
        end

        def current_events(table, bin, start, stop)
            current_event_candidates(table, bin, start, stop).map do |c|
                { 'time' => Time.at(c[:time]).utc, 'type' => c[:type], 'velocity_ms' => c[:value], 'direction_deg' => c[:direction] }
            end
        end

        # ---- the offset methods (sections 4.8 and 4.9) ----

        # A number as written (a decimal string for a float), so that minutes
        # convert to seconds exactly.
        def exact(x)
            x.is_a?(Float) ? Rational(x.to_s) : x.to_r
        end

        # An event time: the unrounded reference root plus the offset, rounded
        # once with floor(t + 0.5) (sections 4.8 step 3, 4.9 step 3).
        def shifted_time(r, offset_min)
            (r.to_r + exact(offset_min) * 60 + Rational(1, 2)).floor
        end

        # Ordered by time; events that round to the same second keep the order
        # of their reference events.
        def by_time(events)
            events.each_with_index.sort_by { |(time, _), i| [time, i] }.map { |(_, e), _| e }
        end

        def chart_datum_term_arg(term)
            raise Error.new('datum_unavailable', 'the reference has no chart datum term (msl_offset_m - named[chart_datum])') if term.nil?

            datum_term_arg(term, 'chart_datum_term')
        end

        # The offsets with the absent-offset rule applied: an absent time offset
        # is 0, an absent height offset is the identity.
        def tide_offsets(offsets)
            hash_arg(offsets, 'subordinate_offsets')
            type = offsets['height_adjusted_type']
            invalid("height_adjusted_type #{type.inspect} is not R or A") unless %w[R A].include?(type)

            identity = type == 'R' ? 1 : 0
            what = 'subordinate_offsets'
            {
                type: type,
                time_high: optional(offsets, 'time_offset_high_min', what, 0),
                time_low: optional(offsets, 'time_offset_low_min', what, 0),
                height_high: optional(offsets, 'height_offset_high', what, identity),
                height_low: optional(offsets, 'height_offset_low', what, identity)
            }
        end

        def subordinate_extremes(table, ref_constituents, chart_datum_term, offsets, start, stop)
            o = tide_offsets(offsets)
            datum_term = chart_datum_term_arg(chart_datum_term)
            tide_sum(table, ref_constituents)
            start, stop = window(start, stop)
            return [] if start == stop

            pad = [[exact(o[:time_high]).abs, exact(o[:time_low]).abs].max * 60 + 3600, SUBORDINATE_PAD_MIN_S].max
            ref = extreme_candidates(table, ref_constituents, start - pad, stop + pad, datum_term)
            by_time(ref.filter_map do |c|
                high = c[:kind] == :down
                time = shifted_time(c[:r], high ? o[:time_high] : o[:time_low])
                next unless time >= start && time < stop

                k = high ? o[:height_high] : o[:height_low]
                height = o[:type] == 'R' ? c[:value] * k : c[:value] + k
                [time, { 'time' => Time.at(time).utc, 'type' => high ? 'high' : 'low', 'height' => height }]
            end)
        end

        # The folded constants of a subordinate tide station with equal high and
        # low offsets (section 4.8a): { 'constituents', 'datum_term', 'method' }.
        # Raises extremes_only when the station has no exact curve.
        def fold(table, ref_constituents, chart_datum_term, offsets)
            o = tide_offsets(offsets)
            c_ref = chart_datum_term_arg(chart_datum_term)
            tide_sum(table, ref_constituents)
            unless o[:time_high] == o[:time_low] && o[:height_high] == o[:height_low]
                raise Error.new('extremes_only', 'high and low water offsets differ, so the station has no exact curve')
            end

            k = o[:height_high]
            raise Error.new('extremes_only', 'the height ratio is not positive') if o[:type] == 'R' && k <= 0

            m, add = o[:type] == 'R' ? [k.to_f, 0.0] : [1.0, k.to_f]
            dh = o[:time_high].to_f / 60.0
            consts = ref_constituents.map do |c|
                speed = table.row(c['name']).speed
                {
                    'name' => c['name'],
                    'amplitude_m' => m * c['amplitude_m'],
                    'phase_deg' => reduce_deg(c['phase_deg'] + speed * dh)
                }
            end
            { 'constituents' => consts, 'datum_term' => m * c_ref + add, 'method' => 'folded_offsets' }
        end

        CURRENT_OFFSET_KEYS = {
            'max_flood' => %w[time_adj_max_flood_min flood_amp_ratio],
            'max_ebb' => %w[time_adj_max_ebb_min ebb_amp_ratio],
            'slack_before_flood' => ['time_adj_slack_before_flood_min', nil],
            'slack_before_ebb' => ['time_adj_slack_before_ebb_min', nil]
        }.freeze

        def subordinate_current_events(table, ref_bin, offset, start, stop)
            hash_arg(offset, 'current_offset')
            what = 'current_offset'
            values = CURRENT_OFFSET_KEYS.to_h do |type, (adj, ratio)|
                [type, [nullable(offset, adj, what), ratio && nullable(offset, ratio, what)]]
            end
            directions = { 'max_flood' => optional(offset, 'mean_flood_dir_deg', what), 'max_ebb' => optional(offset, 'mean_ebb_dir_deg', what) }
            current_bin(table, ref_bin)
            start, stop = window(start, stop)

            kept = values.reject { |type, (adj, ratio)| adj.nil? || (CURRENT_OFFSET_KEYS[type][1] && ratio.nil?) }
            omitted = CURRENT_EVENT_TYPES - kept.keys
            return { 'events' => [], 'omitted_event_types' => omitted } if start == stop

            largest = kept.values.map { |adj, _| exact(adj).abs }.max || 0
            pad = [largest * 60 + 3600, SUBORDINATE_PAD_MIN_S].max
            ref = current_event_candidates(table, ref_bin, start - pad, stop + pad)
            events = ref.filter_map do |c|
                adj, ratio = kept[c[:type]]
                next unless adj

                time = shifted_time(c[:r], adj)
                next unless time >= start && time < stop

                velocity = ratio ? c[:value] * ratio : c[:value]
                [time, { 'time' => Time.at(time).utc, 'type' => c[:type], 'velocity_ms' => velocity, 'direction_deg' => directions[c[:type]] }]
            end
            { 'events' => by_time(events), 'omitted_event_types' => omitted }
        end
    end

    # Strict entry points (spec section 6.1).  Each takes OTC-shaped hashes with
    # string keys and, by default, the TCD's astronomical table; pass astro: an
    # AstroTable for another.  Times are Time objects or numbers of seconds
    # since the epoch (UTC instants); event times are whole seconds.
    class Engine
        # The TCD's per-year tables, read once.  Reading them does not parse the
        # stations.
        def strict_astro_table
            @strict_astro_table ||= Strict::AstroTable.from_tcd(@xtide_file)
        end

        # Heights above the set's mean level (plus datum_term) at each instant.
        # constituents: [{ 'name', 'amplitude_m', 'phase_deg' }, ...].
        def predict_strict(constituents, times, datum_term: 0.0, astro: strict_astro_table)
            Strict.heights(astro, constituents, times, datum_term)
        end

        # High and low water in [start, stop): [{ 'time', 'type' (high|low), 'height' }].
        def extremes_strict(constituents, start, stop, datum_term: 0.0, astro: strict_astro_table)
            Strict.extremes(astro, constituents, start, stop, datum_term)
        end

        # A current bin (OTC current_bin) at each instant: velocity_major_ms
        # (flood positive), velocity_minor_ms, speed_ms and direction_deg (null
        # below 1e-9 m/s).  minor_sign is the minor-axis sign s, +1 or -1; +1
        # (the minor axis 90 degrees clockwise of the major) is the measured
        # value (spec section 4.7, Q-P4).
        def currents_strict(bin, times, minor_sign: 1, astro: strict_astro_table)
            Strict.currents(astro, bin, times, minor_sign)
        end

        # max_flood, max_ebb, slack_before_flood and slack_before_ebb in
        # [start, stop): [{ 'time', 'type', 'velocity_ms', 'direction_deg' }].
        def current_events_strict(bin, start, stop, astro: strict_astro_table)
            Strict.current_events(astro, bin, start, stop)
        end

        # A subordinate tide station's extremes by NOAA's method, above its chart
        # datum.  chart_datum_term is the reference's msl_offset_m minus
        # named[chart_datum] (nil raises datum_unavailable); offsets is the
        # station's subordinate_offsets.
        def subordinate_extremes_strict(ref_constituents, chart_datum_term, offsets, start, stop, astro: strict_astro_table)
            Strict.subordinate_extremes(astro, ref_constituents, chart_datum_term, offsets, start, stop)
        end

        # The folded constants of a subordinate tide station with equal offsets:
        # { 'constituents', 'datum_term', 'method' }.  Pass them to
        # predict_strict and extremes_strict.  Raises extremes_only otherwise.
        def subordinate_folded_strict(ref_constituents, chart_datum_term, offsets, astro: strict_astro_table)
            Strict.fold(astro, ref_constituents, chart_datum_term, offsets)
        end

        # A subordinate current bin's events: { 'events', 'omitted_event_types' }.
        # offset is the bin's current_offset entry.
        def subordinate_current_events_strict(ref_bin, offset, start, stop, astro: strict_astro_table)
            Strict.subordinate_current_events(astro, ref_bin, offset, start, stop)
        end
    end
end
