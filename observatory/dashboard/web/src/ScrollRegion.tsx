import type { ReactNode } from 'react';

/** Wraps content that can overflow so keyboard users can scroll it (axe: scrollable-region-focusable). */
export function ScrollRegion({ label, children }: { label: string; children: ReactNode }) {
  return (
    // Intentional tab stop: overflowing content must be keyboard-scrollable (WCAG 2.1.1).
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    <div className="scroll" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
