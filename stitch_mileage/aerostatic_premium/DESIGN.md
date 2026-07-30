---
name: Aerostatic Premium
colors:
  surface: '#f8faf8'
  surface-dim: '#d8dad8'
  surface-bright: '#f8faf8'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f4f2'
  surface-container: '#eceeec'
  surface-container-high: '#e7e9e7'
  surface-container-highest: '#e1e3e1'
  on-surface: '#191c1b'
  on-surface-variant: '#424843'
  inverse-surface: '#2e3130'
  inverse-on-surface: '#eff1ef'
  outline: '#737972'
  outline-variant: '#c2c8c1'
  surface-tint: '#4a6451'
  primary: '#47614e'
  on-primary: '#ffffff'
  primary-container: '#5f7a66'
  on-primary-container: '#f0fff1'
  inverse-primary: '#b1ceb6'
  secondary: '#4a6450'
  on-secondary: '#ffffff'
  secondary-container: '#ccead1'
  on-secondary-container: '#506a56'
  tertiary: '#525e54'
  on-tertiary: '#ffffff'
  tertiary-container: '#6b776c'
  on-tertiary-container: '#f2fff1'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ccead2'
  primary-fixed-dim: '#b1ceb6'
  on-primary-fixed: '#072011'
  on-primary-fixed-variant: '#334c3b'
  secondary-fixed: '#ccead1'
  secondary-fixed-dim: '#b1ceb5'
  on-secondary-fixed: '#072011'
  on-secondary-fixed-variant: '#334c3a'
  tertiary-fixed: '#d9e6d9'
  tertiary-fixed-dim: '#bdcabd'
  on-tertiary-fixed: '#131e16'
  on-tertiary-fixed-variant: '#3e4a40'
  background: '#f8faf8'
  on-background: '#191c1b'
  surface-variant: '#e1e3e1'
typography:
  display-cpp:
    fontFamily: Plus Jakarta Sans
    fontSize: 40px
    fontWeight: '800'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  title-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-caps:
    fontFamily: Plus Jakarta Sans
    fontSize: 12px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  container-padding: 20px
  gutter: 12px
---

## Brand & Style
The design system is built on the narrative of "Effortless Ascent." It targets high-velocity travelers who value clarity over clutter. The aesthetic merges **Modern Minimalism** with **Glassmorphism** to create a sense of depth and atmospheric perspective, reflecting the experience of flight.

The brand personality is honest and sophisticated. It avoids typical "salesy" travel tropes in favor of a data-driven, editorial approach. Visual interest is generated through subtle environmental effects—radial ombre washes and diagonal gradients that mimic the shifting light in a cabin at sunset—rather than heavy decorative elements. The presence of 'Pointy,' the paper-plane mascot, should be executed as a minimal vector stroke to maintain the premium feel.

## Colors
The palette is rooted in organic Sage tones to evoke a sense of calm and reliability. 

- **Primary (#5F7A66):** Used for primary actions and brand anchoring.
- **Secondary & Tertiary:** Used for categorical differentiation and soft UI surfaces.
- **Gradients:** Use linear gradients at 135° mixing `#F7F8F6` with `#C9D6C9` for page backgrounds. Radial blurs of `#8CA891` (at 10% opacity) should be placed in the top-right corner of card elements to create a subtle glow.
- **Data Contrast:** All "Cents-Per-Point" (CPP) metrics must use the `accent_high_contrast` color to ensure immediate legibility against the softer sage backgrounds.

## Typography
This design system utilizes **Plus Jakarta Sans** for its clean, geometric, and slightly rounded grotesk character, which provides a friendly yet professional tone.

- **CPP Emphasis:** For the "Cents-Per-Point" value, use the `display-cpp` style. This is the highest hierarchy element on any flight card.
- **Hierarchy:** Use `label-caps` for overlines (e.g., "ECONOMY" or "TRANSFER PARTNER") to provide clear structure without adding visual weight.
- **Readability:** Maintain generous line-heights in body text to reflect the "airiness" of the brand.

## Layout & Spacing
The layout follows a **fluid mobile-first grid** optimized for the iPhone 15 Pro form factor.

- **Grid:** Use a 4-column layout for mobile with `20px` side margins. 
- **Rhythm:** All vertical spacing should be a multiple of the `4px` base unit.
- **Airflow:** Use `lg` (24px) spacing between distinct content sections to maintain the "premium" feel. Elements within a card should use `sm` (12px) or `md` (16px) spacing.
- **Safe Areas:** Ensure all bottom-fixed buttons account for the iOS Home Indicator with an additional `16px` of padding.

## Elevation & Depth
Depth is created through **Tonal Layering** and **Glassmorphism** rather than traditional black shadows.

- **Surfaces:** Main cards use a white background at 80% opacity with a `20px` backdrop blur (`saturate(180%)`).
- **Shadows:** Use "Ambient Sage Shadows"—soft, diffused offsets (0, 8, 24) with a color of `rgba(95, 122, 102, 0.08)`.
- **Subtle Accents:** Integrate thin, 1px "Ring Outlines" (`#DCDEDC`) in the background. These arcs and circles should be partially clipped by the screen edges to suggest a larger, continuous space.
- **Transitions:** Elements should appear to "float" into place using slight vertical translations and opacity fades.

## Shapes
The shape language is "Soft Geometric." 

- **Primary Radius:** Use `0.5rem` (8px) for small components like tags and input fields.
- **Container Radius:** Use `1.5rem` (24px) for main flight cards and bottom sheets to echo the rounded corners of modern mobile hardware.
- **Geometric Motifs:** Use "Rounded Triangles" as directional indicators for flight paths. Use perfect circles for status indicators or point-source icons.

## Components

### Buttons
- **Primary:** Filled with `#5F7A66`, white text, `1.5rem` roundedness. 
- **Secondary:** Transparent with a `1px` border of `#5F7A66`, using the same roundedness.
- **Ghost:** Minimal text-only buttons for "Cancel" or "Back" actions.

### Flight Cards
- Use the Glassmorphic treatment (80% opacity, blur).
- Feature the CPP value in the top right corner using `display-cpp` typography.
- Use a "thin ring" background element that subtly connects the departure and arrival airport codes.

### Chips & Tags
- Used for airline names or cabin classes. 
- Style: Solid `#C9D6C9` background with `#5F7A66` text, fully rounded (pill-shape).

### Input Fields
- Soft `#DCDEDC` borders that turn `#5F7A66` on focus.
- Backgrounds should be slightly off-white (`#F7F8F6`) to distinguish them from the main card surface.

### Points Calculator (Special Component)
- A dedicated card with a radial ombre background. 
- Large, high-contrast numerals for the point conversion result to provide the "Honest" brand promise of clear value.