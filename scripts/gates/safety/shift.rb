# Old->new event matching for plan 5.4 "old vs new": each new event is matched to the nearest
# old event of the same kind within +-3 h. Returns per-station stats (minutes / native units).
require 'time'

module Shift
    WIN = 3 * 3600

    module_function

    def load(events)
        events.map { |t, k, h| [Time.iso8601(t).to_i, k, h.is_a?(Numeric) ? h : nil] }
    end

    def pct(sorted, p)
        return nil if sorted.empty?
        sorted[[(p * (sorted.size - 1)).round, sorted.size - 1].min]
    end

    def stats(old_ev, new_ev)
        old_by = load(old_ev).group_by { |e| e[1] }.transform_values { |a| a.sort_by(&:first) }
        dts = []; dhs = []; unmatched_new = 0; used = Hash.new { |h, k| h[k] = {} }
        load(new_ev).each do |t, k, h|
            arr = old_by[k] || []
            i = arr.bsearch_index { |e| e[0] >= t } || arr.size
            best = [i - 1, i].select { |j| j >= 0 && j < arr.size }.min_by { |j| (arr[j][0] - t).abs }
            if best.nil? || (arr[best][0] - t).abs > WIN
                unmatched_new += 1
                next
            end
            used[k][best] = true
            dts << (t - arr[best][0]) / 60.0
            dhs << (h - arr[best][2]) if h && arr[best][2]
        end
        unmatched_old = old_by.sum { |k, a| a.size - used[k].size }
        abs = dts.map(&:abs).sort
        sdt = dts.sort
        habs = dhs.map(&:abs).sort
        {
            n_old: old_ev.size, n_new: new_ev.size, matched: dts.size,
            unmatched_new: unmatched_new, unmatched_old: unmatched_old,
            median_abs_min: pct(abs, 0.5)&.round(2), p95_abs_min: pct(abs, 0.95)&.round(2),
            max_abs_min: abs.last&.round(2), median_signed_min: pct(sdt, 0.5)&.round(2),
            median_abs_dh: pct(habs, 0.5)&.round(4), max_abs_dh: habs.last&.round(4)
        }
    end
end
