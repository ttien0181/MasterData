'use client';
import { useEffect, useLayoutEffect, useRef } from 'react';
import { useSessionCache } from './SessionCache';
type View = { y: number; horizontal: Record<string, number>; details: Record<string, boolean> };
export function useTabView(run: string, tab: string, ready: boolean) {
  const { ui } = useSessionCache();
  const root = useRef<HTMLElement>(null);
  const restored = useRef(false);
  const restoring = useRef(false);
  const interacted = useRef(false);
  const pending = useRef(false);
  const targetY = useRef(0);
  const key = `${JSON.stringify(run)}:view:${tab}`;
  const currentKey = useRef(key);
  currentKey.current = key;
  const seed = useRef<View>({ y: 0, horizontal: {}, details: {} });
  useLayoutEffect(() => { restored.current = false; interacted.current = false; pending.current = false; }, [key]);
  useEffect(() => {
    const element = root.current;
    if (!element || !ready) return;
    let lastY = ui.read(key, seed.current).y;
    const capture = () => {
      // On a Group change React may reuse the table DOM for the next scope
      // before the old passive effect cleans up. Never save that DOM as the old Group.
      if (!element.isConnected || currentKey.current !== key) return;
      const previous = ui.read(key, seed.current);
      const horizontal = { ...previous.horizontal }, details = { ...previous.details };
      element.querySelectorAll<HTMLElement>('[data-scroll-key]').forEach(item => { horizontal[item.dataset.scrollKey!] = item.scrollLeft; });
      element.querySelectorAll<HTMLDetailsElement>('details[data-view-key]').forEach(item => { details[item.dataset.viewKey!] = item.open; });
      ui.write(key, { y: lastY, horizontal, details });
    };
    const scroll = () => { if (!element.isConnected || restoring.current || (pending.current && !interacted.current)) return; lastY = window.scrollY; capture(); };
    const interaction = () => { interacted.current = true; pending.current = false; lastY = window.scrollY; capture(); };
    const keyboard = (event: KeyboardEvent) => { if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(event.key)) interaction(); };
    window.addEventListener('scroll', scroll, { passive: true });
    window.addEventListener('wheel', interaction, { passive: true });
    window.addEventListener('touchmove', interaction, { passive: true });
    window.addEventListener('keydown', keyboard);
    element.addEventListener('pointerdown', interaction);
    element.addEventListener('scroll', capture, true);
    element.addEventListener('toggle', capture, true);
    return () => {
      capture();
      window.removeEventListener('scroll', scroll); window.removeEventListener('wheel', interaction); window.removeEventListener('touchmove', interaction);
      window.removeEventListener('keydown', keyboard); element.removeEventListener('pointerdown', interaction);
      element.removeEventListener('scroll', capture, true); element.removeEventListener('toggle', capture, true);
    };
  }, [ui, key, ready]);
  useLayoutEffect(() => {
    if (!ready) return;
    const element = root.current;
    if (!element) return;
    if (!restored.current) { targetY.current = ui.read(key, seed.current).y; pending.current = true; }
    let frame = 0, finish = 0;
    const restoreVertical = () => {
      // Explicit sidebar deep links take precedence over the saved tab position.
      if (window.location.hash) { pending.current = false; restored.current = true; return; }
      if (!pending.current || interacted.current) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        restoring.current = true;
        if (!interacted.current) {
          if (window.scrollY !== targetY.current) window.scrollTo({ top: targetY.current, behavior: 'instant' });
          pending.current = Math.abs(window.scrollY - targetY.current) > 1;
        }
        restored.current = true;
        finish = requestAnimationFrame(() => { restoring.current = false; });
      });
    };
    const seen = new WeakSet<Element>();
    const restoreElements = () => {
      const view = ui.read(key, seed.current);
      element.querySelectorAll<HTMLDetailsElement>('details[data-view-key]').forEach(item => { if (!seen.has(item)) { item.open = view.details[item.dataset.viewKey!] || false; seen.add(item); } });
      element.querySelectorAll<HTMLElement>('[data-scroll-key]').forEach(item => { if (!seen.has(item)) { item.scrollLeft = view.horizontal[item.dataset.scrollKey!] || 0; seen.add(item); } });
    };
    restoreElements();
    // Nested reads (e.g. a resolution proposal) can finish after the main page.
    const observer = new MutationObserver(() => { restoreElements(); restoreVertical(); });
    observer.observe(element, { childList: true, subtree: true });
    restoreVertical();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); cancelAnimationFrame(finish); restoring.current = false; };
  }, [ui, key, ready]);
  return root;
}
