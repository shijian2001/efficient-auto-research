import SpotlightCard from '../bits/SpotlightCard.jsx';
import Reveal from '../components/Reveal.jsx';
import { useLang } from '../i18n.jsx';

const ITEMS = {
  en: [
    {
      n: '01',
      t: 'Understanding deepens, not just scores',
      d: 'Early branches test whether a broad mechanism holds; later ones probe where it breaks; ancestor insights compress every finding into constraints that shape the next round of hypotheses.',
    },
    {
      n: '02',
      t: 'Each experiment de-randomizes the next',
      d: "Arbor's best candidates arrive mid-to-late, conditioned on accumulated evidence. The same compute buys a less repetitive, more constrained search — not a longer gamble on stumbling into a result.",
    },
    {
      n: '03',
      t: 'Ideas are evidence-conditioned, not guesses',
      d: 'Each proposal is a local, executable move that answers an earlier failure — turning a half-right result into the next hypothesis instead of a reason to abandon the direction.',
    },
  ],
  zh: [
    {
      n: '01',
      t: '加深的是理解，而不只是分数',
      d: '早期分支检验一个宽泛机制是否成立；后期分支探测它在哪里失效；祖先洞见把每条发现压缩成约束，塑造下一轮假设。',
    },
    {
      n: '02',
      t: '每个实验都让下一个不再随机',
      d: 'Arbor 最好的候选出现在中后期，以累积的证据为条件。同样的算力换来的是一次重复更少、约束更强的搜索——而不是一场靠运气碰中结果的更长的盲目尝试。',
    },
    {
      n: '03',
      t: '想法以证据为条件，而非猜测',
      d: '每个提议都是一步局部、可执行、回应了先前失败的动作——把一个半对的结果变成下一个假设，而不是放弃这个方向的理由。',
    },
  ],
};

export default function WhyItMatters() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  const items = ITEMS[lang];
  return (
    <section className="section" aria-label="Why it matters">
      <div className="container">
        <div className="split">
          <Reveal>
            <div>
              <span className="kicker">{zh ? '为何重要' : 'Why It Matters'}</span>
              <h2>{zh ? '会复利的自主研究。' : 'Autonomous research that compounds.'}</h2>
              <p className="lead">
                {zh
                  ? '在 Arbor 的轨迹中，被排除、被验证，或被证明存在边界条件的东西，都会成为下一个提议的先验。结果是一个让研究累积起来的过程——不是更多次尝试，而是重复更少、更具记忆性的搜索。'
                  : "Across Arbor's traces, what gets ruled out, validated, or shown to have boundary conditions becomes a prior on the next proposal. The result is a process that makes research cumulative — not more attempts, but less repetitive, more memory-aware search."}
              </p>
            </div>
          </Reveal>
          <Reveal delay={0.1}>
            <div className="why-list">
              {items.map((i) => (
                <SpotlightCard key={i.n} className="tile" spotlightColor="rgba(110, 168, 255, 0.16)">
                  <span className="why-num">{i.n}</span>
                  <div>
                    <h3>{i.t}</h3>
                    <p>{i.d}</p>
                  </div>
                </SpotlightCard>
              ))}
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
