# Design System Document: The Curated Nebula

## 1. Overview & Creative North Star
This design system is engineered to transform a standard deal-finding landing page into a high-end digital discovery experience. We move away from the cluttered, "discount-aisle" aesthetic and toward a **"Digital Curator"** North Star. 

The goal is to treat every "achadinho" (find) as a premium discovery. We achieve this through **Atmospheric Depth**: using the deep `#0e0e0e` background as an infinite canvas where elements don't just sit on top, but emerge from the darkness. By utilizing intentional asymmetry, wide-tracking typography, and the interplay of light and shadow, we create a tech-forward environment that feels both authoritative and mysterious.

## 2. Colors & Atmospheric Tones
The palette is rooted in a deep, obsidian base, punctuated by "nebula" accents of violet, coral, and mint.

*   **Primary (`#be99ff`):** Our core action color. Use this for high-priority CTA states and focal points.
*   **Secondary (`#ffb3ae`):** Used for urgency and hot deals (Shopee/Red accents).
*   **Tertiary (`#b5ffc2`):** Used for "confirmed" or "verified" deals (Green accents).
*   **Neutral Layers:** We utilize `surface_container_low` through `surface_container_highest` to define spatial importance.

### The "No-Line" Rule
To maintain a high-end editorial feel, **1px solid borders for sectioning are strictly prohibited.** Do not use lines to separate content. Instead, define boundaries through:
1.  **Tonal Shifts:** Placing a `surface_container_low` card against a `surface_dim` background.
2.  **Negative Space:** Using generous vertical padding from the spacing scale to denote section breaks.
3.  **Light Bleed:** Using the subtle gradients (purple to green) to softly edge a container.

### Glassmorphism & Texture
Floating elements (like featured deal cards) should utilize a "Frosted Tech" effect. Use a semi-transparent `surface_variant` at 40% opacity with a `backdrop-filter: blur(20px)`. This allows the background gradients to bleed through, ensuring the UI feels integrated into the atmosphere rather than "pasted" on top.

## 3. Typography
We use a high-contrast typographic pairing to establish a tech-editorial hierarchy.

*   **Display & Headlines (Space Grotesk):** This typeface provides the "Technological" soul. Use `display-lg` for hero statements. To achieve an editorial look, use `headline-sm` with slightly increased letter-spacing (0.05em) for category headers.
*   **Body & Labels (Manrope):** Chosen for its extreme legibility at small scales. 
    *   **Body-lg:** Used for descriptions to ensure a premium reading experience.
    *   **Label-md:** Use this for metadata (e.g., "POSTED 2H AGO") in all-caps with 0.1em tracking to mimic high-fashion layouts.

## 4. Elevation & Depth
In this design system, depth is a function of light, not lines.

*   **The Layering Principle:** Treat the UI as a series of physical layers.
    *   **Base:** `surface_dim` (#0e0e0e).
    *   **Sectioning:** `surface_container_low`.
    *   **Interactive Cards:** `surface_container_high`.
    *   **Overlays/Modals:** `surface_container_highest`.
*   **Ambient Shadows:** If a card requires a "lift," use a shadow that mimics a light source. Avoid black shadows. Use a 32px blur shadow with 8% opacity, tinted with the `primary` token color.
*   **The Ghost Border Fallback:** If a container requires definition against a similar background, use a "Ghost Border": the `outline_variant` token at 15% opacity. It should be felt, not seen.

## 5. Components

### Primary Buttons
*   **Style:** Fully rounded (`full` token). 
*   **Visual:** A linear gradient from `primary` to `primary_container`. 
*   **Interaction:** On hover, increase the `backdrop-blur` of the background and slightly scale the button (1.02x).

### Deal Cards (Achadinho Cards)
*   **Background:** Glassmorphic `surface_container_high` (60% opacity).
*   **Structure:** No dividers. Use `body-md` for descriptions and `title-lg` for the deal name.
*   **Accent:** Use a "Glow Bar"—a 4px vertical strip on the left edge using the `secondary` (Red/Shopee) or `tertiary` (Green/Mercado Livre) color to categorize the source visually without text labels.

### Chips & Tags
*   **Selection Chips:** Use `surface_container_highest` with a `ghost border`.
*   **Status Chips:** Tiny, all-caps `label-sm` text. Use `secondary_container` for "Limited Time" and `tertiary_container` for "Best Price."

### Input Fields
*   **Visual:** Minimalist `surface_container_lowest`. No bottom line.
*   **Focus State:** The border should transition from transparent to a 10% opacity `primary` glow.

## 6. Do's and Don'ts

### Do:
*   **Embrace Asymmetry:** Place the hero text on the left and a floating, glassmorphic card overlapping a gradient orb on the right.
*   **Use Generous Leading:** Give typography room to breathe. High-end design is defined by the space you *don't* fill.
*   **Color as Information:** Use the subtle green/purple gradients to guide the eye toward the most important "finds."

### Don't:
*   **Don't Use Pure White:** Use `on_surface` (#e7e5e4) for text. Pure white (#FFFFFF) is too harsh against the `#0e0e0e` background and breaks the premium "dark mode" feel.
*   **Don't Use Standard Grids:** Avoid the "3-column card row" look. Try staggered heights or overlapping elements to create a more custom, curated feel.
*   **Don't Over-Glow:** Gradients should be "subtle." If the background looks like a rainbow, it's too much. It should look like light reflecting off a dark liquid.