import { useLayoutEffect, useRef } from 'react';
import { useReducedMotion } from './useReducedMotion';

/**
 * Progressive-enhancement reveal. Content is VISIBLE by default; JS only adds the
 * hidden->reveal animation. If JS/IntersectionObserver never runs, content still
 * shows (no blank sections). A safety timeout guarantees reveal even if the
 * observer misfires.
 */
export default function Reveal({ children, delay = 0, distance, className = '', as: Tag = 'div', ...rest }) {
  void distance; // accepted for call-site compatibility; amount is set in CSS
  const ref = useRef(null);
  const reduced = useReducedMotion();

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;

    // Arm synchronously before paint -> no flash of un-hidden content.
    el.classList.add('reveal-armed');
    el.style.setProperty('--reveal-delay', `${delay * 1000}ms`);

    let done = false;
    const reveal = () => {
      if (done) return;
      done = true;
      el.classList.add('reveal-in');
    };

    if (!('IntersectionObserver' in window)) {
      reveal();
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            reveal();
            io.unobserve(e.target);
          }
        });
      },
      { rootMargin: '0px 0px -10% 0px', threshold: 0.12 }
    );
    io.observe(el);

    // Safety net: never leave content hidden.
    const t = setTimeout(reveal, 2600);

    return () => {
      io.disconnect();
      clearTimeout(t);
    };
  }, [delay, reduced]);

  return (
    <Tag ref={ref} className={`reveal ${className}`.trim()} {...rest}>
      {children}
    </Tag>
  );
}
