import Counter from '../components/Counter.jsx';
import Reveal from '../components/Reveal.jsx';
import { useLang } from '../i18n.jsx';

const STATS = {
  en: [
    { to: 6, decimals: 0, suffix: ' / 6', label: 'best held-out results across real AO tasks' },
    { to: 2.5, decimals: 1, suffix: 'x', label: 'average relative held-out gain over Codex / Claude Code' },
    { to: 86.36, decimals: 2, suffix: '%', label: 'Any Medal on MLE-Bench Lite with GPT-5.5' },
  ],
  zh: [
    { to: 6, decimals: 0, suffix: ' / 6', label: '在真实自主优化任务上取得最佳留出结果' },
    { to: 2.5, decimals: 1, suffix: 'x', label: '相对 Codex / Claude Code 的平均留出增益' },
    { to: 86.36, decimals: 2, suffix: '%', label: 'MLE-Bench Lite（GPT-5.5）任意奖牌率' },
  ],
};

export default function ProofStrip() {
  const { lang } = useLang();
  const stats = STATS[lang];
  return (
    <section className="proof" aria-label="Headline results">
      <div className="container">
        <Reveal distance={24}>
          <div className="proof-grid">
            {stats.map((s) => (
              <div className="proof-cell" key={s.label}>
                <div className="proof-value">
                  <Counter to={s.to} decimals={s.decimals} duration={1.6} />
                  <span className="suffix">{s.suffix}</span>
                </div>
                <p className="proof-label">{s.label}</p>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
