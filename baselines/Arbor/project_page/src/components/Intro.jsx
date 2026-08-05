import { useEffect, useRef, useState } from 'react';
import { useReducedMotion } from './useReducedMotion';
import { useLang } from '../i18n.jsx';

const HOLD = 2000; // ms the splash stays before exiting
const EXIT = 800; // ms exit transition

/**
 * Opening splash. Rendered on top of the page (which is already mounted beneath),
 * so if anything fails the page is still there. Auto-dismisses after HOLD+EXIT,
 * and can be skipped by any interaction. Skipped entirely under reduced-motion.
 */
export default function Intro() {
  const reduced = useReducedMotion();
  const { lang } = useLang();
  const [show, setShow] = useState(!reduced);
  const [exiting, setExiting] = useState(false);
  const dismissed = useRef(false);

  useEffect(() => {
    if (reduced) return;

    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = 'hidden';

    let exitTimer;
    const finish = () => {
      html.style.overflow = prev;
      setShow(false);
    };
    const dismiss = () => {
      if (dismissed.current) return;
      dismissed.current = true;
      setExiting(true);
      exitTimer = setTimeout(finish, EXIT);
    };

    const holdTimer = setTimeout(dismiss, HOLD);
    const safety = setTimeout(() => {
      // hard guarantee the overlay never gets stuck
      clearTimeout(holdTimer);
      html.style.overflow = prev;
      setExiting(true);
      setTimeout(() => setShow(false), EXIT);
    }, HOLD + EXIT + 2500);

    const onSkip = () => dismiss();
    window.addEventListener('pointerdown', onSkip);
    window.addEventListener('keydown', onSkip);
    window.addEventListener('wheel', onSkip, { passive: true });
    window.addEventListener('touchstart', onSkip, { passive: true });

    return () => {
      clearTimeout(holdTimer);
      clearTimeout(exitTimer);
      clearTimeout(safety);
      window.removeEventListener('pointerdown', onSkip);
      window.removeEventListener('keydown', onSkip);
      window.removeEventListener('wheel', onSkip);
      window.removeEventListener('touchstart', onSkip);
      html.style.overflow = prev;
    };
  }, [reduced]);

  if (!show) return null;

  const wm = `url(${import.meta.env.BASE_URL}assets/images/arbor-wordmark.png)`;

  return (
    <div className={`intro${exiting ? ' intro-exit' : ''}`} aria-hidden="true">
      <div className="intro-inner">
        <span
          className="wordmark-mask intro-word"
          role="img"
          aria-label="Arbor"
          style={{ WebkitMaskImage: wm, maskImage: wm }}
        />
        <p className="intro-tag">{lang === 'zh' ? '自主研究系统' : 'Autonomous Research System'}</p>
        <span className="intro-bar"><i /></span>
      </div>
    </div>
  );
}
