import { useLayoutEffect, useRef } from 'react';
import { useReducedMotion } from './useReducedMotion';

function decimalsOf(num) {
  const s = num.toString();
  if (s.includes('.')) {
    const d = s.split('.')[1];
    if (parseInt(d, 10) !== 0) return d.length;
  }
  return 0;
}

/**
 * Robust count-up. Renders the REAL target value by default (correct with no JS,
 * never shows a misleading 0). When JS + IntersectionObserver are available and
 * motion isn't reduced, it counts from 0 to the target on first view, with a
 * safety timeout that snaps to the final value.
 */
export default function Counter({ to, duration = 1.6, decimals }) {
  const ref = useRef(null);
  const reduced = useReducedMotion();
  const dec = decimals ?? decimalsOf(to);
  const fmt = (n) =>
    new Intl.NumberFormat('en-US', {
      minimumFractionDigits: dec,
      maximumFractionDigits: dec,
    }).format(n);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || reduced || !('IntersectionObserver' in window)) return;

    el.textContent = fmt(0); // arm before paint
    let raf = 0;
    let done = false;

    const run = () => {
      if (done) return;
      done = true;
      const start = performance.now();
      const tick = (now) => {
        const p = Math.min(1, (now - start) / (duration * 1000));
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = fmt(to * eased);
        if (p < 1) raf = requestAnimationFrame(tick);
        else el.textContent = fmt(to);
      };
      raf = requestAnimationFrame(tick);
    };

    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => {
        if (e.isIntersecting) {
          run();
          io.unobserve(e.target);
        }
      }),
      { threshold: 0.4 }
    );
    io.observe(el);
    const safety = setTimeout(run, 2600);

    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
      clearTimeout(safety);
    };
  }, [to, duration, dec, reduced]);

  return <span ref={ref}>{fmt(to)}</span>;
}
