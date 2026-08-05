import { useEffect, useState } from 'react';
import { useLang } from '../i18n.jsx';

const NAV = [
  { id: 'demo', label: { en: 'Demo', zh: '演示' } },
  { id: 'method', label: { en: 'Method', zh: '方法' } },
  { id: 'results', label: { en: 'Results', zh: '结果' } },
  { id: 'case', label: { en: 'Case Study', zh: '案例研究' } },
  { id: 'resources', label: { en: 'Resources', zh: '资源' } },
];

export default function Header() {
  const { lang, setLang } = useLang();
  const [scrolled, setScrolled] = useState(false);
  const [active, setActive] = useState('');

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    const sections = NAV.map((n) => document.getElementById(n.id)).filter(Boolean);
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible) setActive(visible.target.id);
      },
      { rootMargin: '-30% 0px -55% 0px', threshold: [0.08, 0.25, 0.5] }
    );
    sections.forEach((s) => obs.observe(s));
    return () => obs.disconnect();
  }, []);

  return (
    <header className={`site-header${scrolled ? ' scrolled' : ''}`}>
      <a className="brand" href="#top" aria-label="Arbor home">
        <img src="assets/images/arbor-logo.png" alt="Arbor" />
      </a>
      <nav className="nav" aria-label="Sections">
        {NAV.map((n) => (
          <a key={n.id} href={`#${n.id}`} className={active === n.id ? 'active' : undefined}>
            {n.label[lang]}
          </a>
        ))}
        <a href="docs/">{lang === 'zh' ? '文档' : 'Docs'}</a>
      </nav>
      <div className="header-right">
        <button
          type="button"
          className="lang-toggle"
          onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}
          aria-label={lang === 'zh' ? 'Switch to English' : '切换到中文'}
        >
          {lang === 'zh' ? 'EN' : '中文'}
        </button>
        <a className="header-cta" href="https://github.com/RUC-NLPIR/Arbor" target="_blank" rel="noreferrer">
          <span>{lang === 'zh' ? 'Star on' : 'Star on'}</span> GitHub
        </a>
      </div>
    </header>
  );
}
