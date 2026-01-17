# WebCalTides UX Redesign

**Date:** 2026-01-17
**Status:** Approved
**Branch:** claude/ux-redesign

## Overview

Complete redesign of the WebCalTides web interface with a focus on first-time discovery, nautical theming, and improved mobile experience.

## Design Goals

- **Primary use case:** First-time discovery
- **Discovery method:** Search by name (primary), GPS/location nearby (secondary)
- **Visual style:** Maritime/nautical theme (blues, wave motifs)
- **Device support:** Desktop and mobile equally

## Design Specification

### 1. Overall Layout & First Impression

#### Hero Area
- Full-width header with subtle animated wave pattern
- Gradient: deep navy (#1e3a5a) to light blue (#3b82f6)
- Logo left-aligned
- Tagline: "Tides & Currents for Your Calendar"

#### Search as Centerpiece
- Large, prominent search bar center-stage below hero
- Placeholder text rotates through examples: "Try 'Alcatraz Island' or 'San Francisco Bay'..."
- Subtle compass icon on the left
- Auto-suggestions as user types (station names + regions)
- Secondary "Use my location" button beneath for GPS-based discovery

#### Above the Fold (Initial Load)
Only show:
1. Nautical header with branding
2. The search bar
3. Brief one-liner about what the service does

No results tables, warnings, or dense information on initial load.

#### Results Behavior
- Results animate in below search bar when user searches
- Page scrolls smoothly to show results

---

### 2. Search Results - Card-Based Layout

Replace HTML tables with responsive card grid:
- 1 column on mobile
- 2-3 columns on desktop

#### Station Card Anatomy

```
┌─────────────────────────────────────────────┐
│  [Mini Map]          NOAA ← provider badge  │
│   (thumbnail)        ━━━━                   │
│                      Alcatraz Island        │
│                      San Francisco Bay, CA  │
├─────────────────────────────────────────────┤
│  Next: High 5.2ft @ 2:34pm · Low 1.1ft @... │
├─────────────────────────────────────────────┤
│  ☀ Solar  ☾ Lunar     [Copy] [⬇] [Subscribe]│
│    ✓        ○                               │
└─────────────────────────────────────────────┘
```

#### Card Elements
- **Mini map thumbnail** (left): Static map image showing pin location for visual confirmation
- **Provider badge** (top right): Color-coded pill
  - Green: NOAA, CHS (official sources)
  - Amber: XTide, TICON (with hover tooltip for warning)
- **Station name**: Large, bold text
- **Region**: Smaller, muted text beneath name
- **Data preview strip**: Next 2-3 tide events in compact row
- **Options row**:
  - Solar/Lunar as visible toggle switches (not hidden)
  - Action buttons: Copy, Download, Subscribe

#### Action Buttons
- **Copy** (clipboard icon): Copies webcal URL to clipboard
- **Download** (down arrow icon): Downloads .ics file directly
- **Subscribe** (calendar+ icon, primary blue): webcal:// link - visually emphasized as primary action

---

### 3. Tides vs Currents Organization

#### Section Headers
When search returns both types, group with clear headers:

```
🌊 Tide Stations (12 results)
[cards...]

〰️ Current Stations (4 results)
[cards...]
```

- Nautical icons on section headers
- Show result count
- Collapsible if > 6 cards in a section

#### Current Station Card Variation
Include depth field prominently:

```
┌─────────────────────────────────────────────┐
│  [Mini Map]          NOAA                   │
│                      ━━━━                   │
│                      Golden Gate Bridge     │
│                      San Francisco, CA      │
│                      📍 Depth: 15ft         │
├─────────────────────────────────────────────┤
│  Next: Slack @ 2:34pm · Flood 2.1kts @...   │
├─────────────────────────────────────────────┤
│  ☀ Solar  ☾ Lunar     [Copy] [⬇] [Subscribe]│
└─────────────────────────────────────────────┘
```

#### "Find Nearby" Feature
- Small link on each card instead of dropdown with radius input
- Clicking re-runs search centered on station's GPS
- Results sorted by distance with badges ("0.3 mi", "1.2 mi", etc.)

---

### 4. Mobile Experience

#### Mobile-First Cards
- Single-column stack
- Slightly more compact layout
- Mini map shrinks but stays visible
- Data preview shows 2 events instead of 3
- Action buttons become icon-only (labels on tap/hold)
- Solar/Lunar toggles remain visible

#### Sticky Search
Search bar becomes sticky at top when scrolling through results.

#### Touch-Friendly
- All interactive elements minimum 44px tap targets
- Subscribe button intentionally larger than Copy/Download

#### Swipe Gesture (Progressive Enhancement)
Left-swipe on cards reveals quick "Subscribe" action for power users.

---

### 5. Footer & Warnings

#### Contextual Warnings
- XTide/TICON warning moves INTO the card (amber badge)
- Expands on hover/tap to show disclaimer
- Users see warning when relevant, not as upfront wall of text

#### Slim Footer
Minimal footer:
- One line: "Built by Jordan Ritter · GitHub · Licenses"
- Licenses opens modal (not inline accordion)
- "What's new" link with tooltip for update notes

---

## Technical Considerations

### Dependencies
- Keep Tailwind CSS (already in use)
- Keep Alpine.js (already in use)
- Add: Static map provider (Mapbox static API, or OpenStreetMap tiles)
- Consider: Simple autocomplete library or custom Alpine component

### Data Requirements
- Station lat/lon already available (for mini maps)
- Need endpoint for "next N tide events" preview (or compute client-side from cached data)
- Autocomplete needs station list accessible to frontend (JSON endpoint)

### Performance
- Lazy-load mini maps (only load when card in viewport)
- Cache static map images
- Autocomplete should debounce and limit results

---

## Implementation Priority

1. **Phase 1:** Hero + search bar + card layout (replace tables)
2. **Phase 2:** Mini maps + data preview
3. **Phase 3:** Autocomplete + mobile polish
4. **Phase 4:** Animations + progressive enhancements (swipe, etc.)
