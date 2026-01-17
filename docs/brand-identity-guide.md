# WebCalTides Brand Identity Guide

**Version:** 1.0
**Last Updated:** January 2026

---

## Brand Overview

**WebCalTides** is a free service providing tide, current, solar, and lunar calendar subscriptions for sailors, boaters, and coastal enthusiasts. The brand embodies reliability, clarity, and a modern nautical aesthetic.

### Brand Personality
- **Trustworthy** — Accurate data from official sources
- **Approachable** — Easy to use, no technical barriers
- **Modern** — Contemporary design, not old-school nautical clichés
- **Helpful** — Focused on user needs, not feature bloat

### Tagline
> Tides & Currents for Your Calendar

---

## Logo

### Primary Logo
The WebCalTides logo features a stylized wave/tide icon.

### Logo Variants
| Variant | Use Case |
|---------|----------|
| **Dark (black)** | Light backgrounds, print materials |
| **Light (white/inverted)** | Dark backgrounds, hero sections |

### Logo Spacing
Maintain clear space around the logo equal to the height of the "W" in WebCalTides.

### Logo Don'ts
- Don't stretch or distort the logo
- Don't add effects (shadows, glows) beyond the approved styles
- Don't place on busy backgrounds without sufficient contrast
- Don't rotate the logo

---

## Color Palette

### Primary Colors

| Name | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Navy 950** | `#0a0f1a` | 10, 15, 26 | Page backgrounds, darkest elements |
| **Navy 900** | `#0f172a` | 15, 23, 42 | Card backgrounds, secondary dark |
| **Navy 800** | `#1e293b` | 30, 41, 59 | Elevated surfaces, inputs |
| **Navy 700** | `#334155` | 51, 65, 85 | Borders, dividers |

### Accent Colors

| Name | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Ocean 400** | `#38bdf8` | 56, 189, 248 | Links, highlights, accents |
| **Ocean 500** | `#0ea5e9` | 14, 165, 233 | Primary buttons, CTAs |
| **Ocean 600** | `#0284c7` | 2, 132, 199 | Hover states |
| **Ocean 700** | `#0369a1` | 3, 105, 161 | Active states |

### Secondary Accent

| Name | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Seafoam 400** | `#5eead4` | 94, 234, 212 | Current stations, secondary CTAs |
| **Seafoam 500** | `#2dd4bf` | 45, 212, 191 | Current station buttons |

### Semantic Colors

| Name | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Emerald 400** | `#34d399` | 52, 211, 153 | Success, official sources (NOAA/CHS) |
| **Amber 400** | `#fbbf24` | 251, 191, 36 | Warnings, XTide/TICON badges |
| **Amber 500** | `#f59e0b` | 245, 158, 11 | Warning hover states |

### Neutral Colors

| Name | Hex | Usage |
|------|-----|-------|
| **Slate 100** | `#f1f5f9` | Primary text on dark |
| **Slate 300** | `#cbd5e1` | Secondary text |
| **Slate 400** | `#94a3b8` | Muted text, placeholders |
| **Slate 500** | `#64748b` | Disabled text |
| **Slate 600** | `#475569` | Very muted text |

### Color Usage Guidelines

1. **Dark theme is primary** — The site uses a dark nautical theme
2. **Ocean blue for tides** — Use ocean colors for tide-related elements
3. **Seafoam for currents** — Use seafoam to differentiate current stations
4. **Amber for warnings** — Reserve amber exclusively for XTide/TICON warnings
5. **Emerald for trust** — Use emerald to indicate official/verified sources

---

## Typography

### Primary Font

**Figtree** — A friendly, modern sans-serif with approachable character.

```css
font-family: 'Figtree', system-ui, sans-serif;
```

**Source:** [Google Fonts](https://fonts.google.com/specimen/Figtree)

### Type Scale

| Element | Size | Weight | Line Height |
|---------|------|--------|-------------|
| **Hero Title** | 48-60px | 700 (Bold) | 1.1 |
| **Section Heading** | 24-30px | 600 (Semibold) | 1.2 |
| **Card Title** | 18-20px | 600 (Semibold) | 1.3 |
| **Body Text** | 16-18px | 400 (Regular) | 1.6 |
| **Small/Caption** | 12-14px | 400-500 | 1.4 |
| **Monospace (IDs)** | 12px | 400 | 1.4 |

### Font Weights
- **400** — Regular: Body text, descriptions
- **500** — Medium: Emphasized text, labels
- **600** — Semibold: Headings, card titles
- **700** — Bold: Hero title, strong emphasis

### Typography Guidelines

1. Use **bold (700)** sparingly — reserve for the main title and critical emphasis
2. Station names use **semibold (600)** for scannability
3. Body text stays at **regular (400)** for comfortable reading
4. Avoid using all-caps except for badges (NOAA, XTide, etc.)

---

## Spacing & Layout

### Spacing Scale

| Token | Value | Usage |
|-------|-------|-------|
| `space-1` | 4px | Tight spacing, inline elements |
| `space-2` | 8px | Icon gaps, small padding |
| `space-3` | 12px | Button padding, tight margins |
| `space-4` | 16px | Card padding, standard gaps |
| `space-5` | 20px | Comfortable padding |
| `space-6` | 24px | Section gaps |
| `space-8` | 32px | Large section margins |
| `space-12` | 48px | Section separators |
| `space-16` | 64px | Major page sections |

### Border Radius

| Element | Radius |
|---------|--------|
| Buttons | 12px (`rounded-xl`) |
| Cards | 16px (`rounded-2xl`) |
| Badges | 8px (`rounded-lg`) |
| Inputs | 16px (`rounded-2xl`) |
| Toggle switches | 10px |

### Layout Principles

1. **Max content width:** 1280px (6xl)
2. **Card grid:** 1 column mobile, 2 columns tablet, 3 columns desktop
3. **Generous whitespace:** Let elements breathe
4. **Consistent gutters:** 16px mobile, 24px desktop

---

## Components

### Buttons

#### Primary Button (Subscribe)
```css
background: #0ea5e9;  /* ocean-500 */
color: white;
padding: 10px 16px;
border-radius: 12px;
font-weight: 500;
```

#### Secondary Button (Copy, Download)
```css
background: rgba(51, 65, 85, 0.5);  /* navy-700/50 */
border: 1px solid rgba(71, 85, 105, 0.5);  /* navy-600/50 */
color: #cbd5e1;  /* slate-300 */
padding: 10px 12px;
border-radius: 12px;
```

### Badges

#### Official Source (NOAA, CHS)
```css
background: rgba(52, 211, 153, 0.2);  /* emerald-500/20 */
color: #34d399;  /* emerald-400 */
border: 1px solid rgba(52, 211, 153, 0.3);
padding: 4px 8px;
border-radius: 8px;
font-size: 12px;
font-weight: 500;
```

#### Warning Source (XTide, TICON)
```css
background: rgba(245, 158, 11, 0.2);  /* amber-500/20 */
color: #fbbf24;  /* amber-400 */
border: 1px solid rgba(245, 158, 11, 0.3);
padding: 4px 8px;
border-radius: 8px;
font-size: 12px;
font-weight: 500;
```

### Cards

```css
background: rgba(30, 41, 59, 0.5);  /* navy-800/50 */
border: 1px solid rgba(51, 65, 85, 0.5);  /* navy-700/50 */
border-radius: 16px;
backdrop-filter: blur(4px);
```

### Toggle Switches

```css
/* Inactive */
background: #cbd5e1;
width: 36px;
height: 20px;
border-radius: 10px;

/* Active */
background: #0ea5e9;  /* ocean-500 */
```

---

## Iconography

### Icon Style
- Line icons preferred (stroke-based)
- 1.5-2px stroke width
- Rounded line caps and joins
- Consistent 24px base size

### Key Icons

| Purpose | Description |
|---------|-------------|
| Search | Magnifying glass |
| Compass | Navigation/GPS |
| Calendar | Subscribe action |
| Download | Arrow pointing down |
| Copy | Overlapping rectangles |
| Tide | Water droplet |
| Current | Globe/flow |
| Solar | Sun symbol (☀︎) |
| Lunar | Moon crescent (☾) |

---

## Motion & Animation

### Timing

| Type | Duration | Easing |
|------|----------|--------|
| Micro-interactions | 150-200ms | ease-out |
| State changes | 200-300ms | ease-in-out |
| Page transitions | 300-500ms | cubic-bezier(0.4, 0, 0.2, 1) |
| Ambient (waves) | 12-16s | ease-in-out |

### Animation Principles

1. **Subtle is better** — Animations enhance, not distract
2. **Purpose-driven** — Every animation should communicate something
3. **Performance-first** — Use CSS transforms, avoid layout thrashing
4. **Reduced motion** — Respect `prefers-reduced-motion`

### Key Animations

- **Wave background:** Gentle floating motion (12-16s cycles)
- **Card hover:** Subtle lift (translateY -4px) with shadow
- **Fade-in:** Cards stagger in with 50ms delays
- **Compass spin:** Rotates 45° when search input focused

---

## Voice & Tone

### Writing Style
- **Clear and direct** — No jargon or nautical clichés
- **Helpful** — Guide users, don't lecture
- **Concise** — Fewer words, more clarity
- **Friendly** — Approachable but professional

### Examples

| Instead of... | Write... |
|---------------|----------|
| "Utilize our comprehensive tide prediction service" | "Subscribe to tide calendars" |
| "Navigate to the station selection interface" | "Search for a station" |
| "Data is sourced from authoritative governmental agencies" | "Data from NOAA and CHS" |

---

## File Formats

### Web Assets
- **Logo:** SVG (preferred), PNG with transparency
- **Icons:** SVG inline or sprite
- **Images:** WebP with PNG fallback

### Print Assets
- **Logo:** PDF, EPS, or high-res PNG (300dpi)
- **Colors:** Use Pantone equivalents for print

---

## Contact

For brand questions or asset requests:
- **GitHub:** [github.com/jpr5/webcaltides](https://github.com/jpr5/webcaltides)
- **Author:** Jordan Ritter

---

*This guide is a living document. Last updated January 2026.*
