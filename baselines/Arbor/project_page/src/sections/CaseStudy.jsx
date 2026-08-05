import SpotlightCard from '../bits/SpotlightCard.jsx';
import Reveal from '../components/Reveal.jsx';
import IdeaTree from '../components/IdeaTree.jsx';
import { useLang } from '../i18n.jsx';

// Held-out (test) accuracy on BrowseComp — matches the paper's main results table.
const AXIS_MAX = 80;
const BARS = {
  en: [
    { name: 'Initial', val: 45.33 },
    { name: 'Codex', val: 50.0 },
    { name: 'Claude Code', val: 53.33 },
    { name: 'Arbor', val: 67.67, win: true, gain: '+22.34' },
  ],
  zh: [
    { name: '初始', val: 45.33 },
    { name: 'Codex', val: 50.0 },
    { name: 'Claude Code', val: 53.33 },
    { name: 'Arbor', val: 67.67, win: true, gain: '+22.34' },
  ],
};

const FINDINGS = {
  en: [
    {
      n: '01',
      t: 'Candidate coverage was the bottleneck',
      d: 'When all independent trajectories missed the entity, ordinary judging could not recover it.',
    },
    {
      n: '02',
      t: 'Prompt-only control regressed',
      d: 'Structured belief tables, decomposition, and persona-diversified agents spent budget without widening useful evidence.',
    },
    {
      n: '03',
      t: 'Override authority broke the ceiling',
      d: 'A judge allowed to search beyond the candidate set, under constraint-PASS gating, lifted held-out accuracy.',
    },
  ],
  zh: [
    {
      n: '01',
      t: '候选覆盖率是瓶颈',
      d: '当所有独立轨迹都漏掉了目标实体时，普通的裁决无法把它找回来。',
    },
    {
      n: '02',
      t: '仅靠 prompt 的控制反而退化',
      d: '结构化信念表、问题分解与采用多样化角色（persona）的智能体消耗了预算，却没有拓宽有用证据。',
    },
    {
      n: '03',
      t: '赋予越界检索权限突破了上限',
      d: '一个被允许在候选集之外检索、并受约束-PASS 门控的裁决器，提升了留出准确率。',
    },
  ],
};

export default function CaseStudy() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  const bars = BARS[lang];
  const findings = FINDINGS[lang];
  return (
    <section className="section" id="case" aria-label="BrowseComp run">
      <div className="container-wide">
        <Reveal>
          <div className="section-head">
            <span className="kicker">{zh ? '案例研究 · BrowseComp' : 'Case Study · BrowseComp'}</span>
            <h2>{zh ? '从相关联的检索失败，到一个被工具赋能的裁决器。' : 'From correlated search failures to a tool-empowered judge.'}</h2>
            <p className="lead">
              {zh
                ? 'Arbor 先后探索了仅靠 prompt 的信念状态修复、对抗式证伪、检索枚举与跨轨迹集成，最终合并了一个允许越界检索（override）的裁决器设计——把留出准确率远远推过了强大的单智能体基线。'
                : 'Arbor explored prompt-only belief-state fixes, adversarial falsification, retrieval enumeration, and cross-trajectory ensembling before merging a judge-with-override design — lifting held-out accuracy well past strong single-agent baselines.'}
            </p>
          </div>
        </Reveal>

        <Reveal delay={0.05}>
          <div className="bc-panel">
            <div className="bc-head">
              <span className="bc-title">{zh ? '留出准确率' : 'Held-out accuracy'}</span>
              <span className="bc-sub">{zh ? 'BrowseComp · 越高越好' : 'BrowseComp · higher is better'}</span>
            </div>
            <div className="bc-chart">
              {bars.map((b) => (
                <div className={`bc-row${b.win ? ' win' : ''}`} key={b.name}>
                  <span className="bc-name">{b.name}</span>
                  <span className="bc-track">
                    <span className="bc-fill" style={{ width: `${(b.val / AXIS_MAX) * 100}%` }} />
                  </span>
                  <span className="bc-val">
                    {b.val.toFixed(2)}
                    {b.gain && <span className="bc-gain">{b.gain}</span>}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </Reveal>

        <IdeaTree />

        <Reveal delay={0.05}>
          <div className="findings">
            {findings.map((f) => (
              <SpotlightCard key={f.n} className="tile finding" spotlightColor="rgba(70, 224, 196, 0.16)">
                <span className="finding-num">{f.n}</span>
                <h3>{f.t}</h3>
                <p>{f.d}</p>
              </SpotlightCard>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
