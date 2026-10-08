# Plan 5.4 sanity rules over one station's event list (UTC).
# events: [{t: Time(utc), k: 'High'|'Low'|'flood'|'ebb'|'slack', h: Float|nil}] sorted by t.
# Returns [[day 'YYYY-MM-DD', rule, detail], ...].
require 'date'

module SafetyRules
    WY_FROM = Date.new(2026, 9, 1)
    WY_TO   = Date.new(2027, 9, 30)
    YB      = Time.utc(2027, 1, 1)
    YB_DAYS = %w[2026-12-31 2027-01-01].freeze # +-1 day of 2027-01-01T00:00Z

    module_function

    # F = (K1+O1)/(M2+S2); diurnal when F > 3.0
    def form_factor(consts)
        a = ->(n) { (consts.find { |c| c['name'] == n } || {})['amp'].to_f }
        semi = a['M2'] + a['S2']
        semi.zero? ? Float::INFINITY : (a['K1'] + a['O1']) / semi
    end

    def day(t) = t.strftime('%Y-%m-%d')

    def tide(events, f)
        v = []
        lo = f > 3.0 ? 1 : 3
        by_day = events.group_by { |e| day(e[:t]) }
        (WY_FROM..WY_TO).each do |d|
            n = (by_day[d.to_s] || []).size
            v << [d.to_s, 'count', "#{n} not in [#{lo},5]"] unless n.between?(lo, 5)
        end
        events.each_cons(2) do |a, b|
            v << [day(b[:t]), 'alternation', "#{a[:k]}->#{b[:k]} #{b[:t].strftime('%H:%M')}"] if a[:k] == b[:k]
        end
        events.each do |e|
            h = e[:h]
            v << [day(e[:t]), 'nan', "#{e[:k]} #{e[:t].strftime('%H:%M')} h=#{h.inspect}"] if h.nil? || !h.finite?
            v << [day(e[:t]), 'zero', "#{e[:k]} #{e[:t].strftime('%H:%M')} h=#{h.inspect}"] if h&.finite? && h.zero?
        end
        events.each_with_index do |e, i|
            next unless e[:k] == 'High' && e[:h]&.finite?
            [events[i - 1], events[i + 1]].each do |n|
                next if n.nil? || n.equal?(e) || i.zero? && n.equal?(events[-1])
                next unless n[:k] == 'Low' && n[:h]&.finite?
                v << [day(e[:t]), 'hw_le_lw', "HW #{e[:h].round(3)} <= LW #{n[:h].round(3)} #{e[:t].strftime('%H:%M')}"] if e[:h] <= n[:h]
            end
        end
        v + year_boundary(events, %w[High Low])
    end

    # currents: flood/ebb alternation (slacks ignored), finite non-zero velocity at peaks,
    # flood > 0 > ebb, per-day peak count in [1,6]
    def current(events)
        v = []
        peaks = events.reject { |e| e[:k] == 'slack' }
        by_day = peaks.group_by { |e| day(e[:t]) }
        (WY_FROM..WY_TO).each do |d|
            n = (by_day[d.to_s] || []).size
            v << [d.to_s, 'count', "#{n} peaks not in [1,6]"] unless n.between?(1, 6)
        end
        peaks.each_cons(2) do |a, b|
            v << [day(b[:t]), 'alternation', "#{a[:k]}->#{b[:k]} #{b[:t].strftime('%H:%M')}"] if a[:k] == b[:k]
        end
        peaks.each do |e|
            h = e[:h]
            if h.nil? || !h.finite?
                v << [day(e[:t]), 'nan', "#{e[:k]} #{e[:t].strftime('%H:%M')} v=#{h.inspect}"]
            elsif h.zero? || (e[:k] == 'flood' ? h < 0 : h > 0)
                v << [day(e[:t]), 'zero', "#{e[:k]} #{e[:t].strftime('%H:%M')} v=#{h.inspect}"]
            end
        end
        v + year_boundary(peaks, %w[flood ebb])
    end

    # +-1 day of 2027-01-01T00:00Z: alternation, no duplicate (same kind < 2 h apart), and no
    # dropped event (gap > 1.6x the station's median gap over WY).
    def year_boundary(evs, kinds)
        v = []
        gaps = evs.each_cons(2).map { |a, b| b[:t] - a[:t] }.sort
        med = gaps.empty? ? 0 : gaps[gaps.size / 2]
        from = YB - 86_400; to = YB + 86_400
        w = evs.select { |e| e[:t] >= from - 43_200 && e[:t] <= to + 43_200 }
        w.each_cons(2) do |a, b|
            next unless b[:t] >= from && a[:t] <= to
            d = day(b[:t])
            v << [d, 'yb_alternation', "#{a[:k]}->#{b[:k]}"] if a[:k] == b[:k] && kinds.include?(a[:k])
            v << [d, 'yb_duplicate', "#{a[:k]} #{a[:t].strftime('%H:%M')}/#{b[:t].strftime('%H:%M')}"] if a[:k] == b[:k] && b[:t] - a[:t] < 7200
            v << [d, 'yb_gap', "#{((b[:t] - a[:t]) / 60).round} min > 1.6x median #{(med / 60).round}"] if med > 0 && b[:t] - a[:t] > 1.6 * med
        end
        cnt = w.group_by { |e| day(e[:t]) }
        YB_DAYS.each { |d| v << [d, 'yb_count', "0 events"] if (cnt[d] || []).empty? }
        v
    end
end
