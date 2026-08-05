import SpotlightCard from '../bits/SpotlightCard.jsx';
import Reveal from '../components/Reveal.jsx';
import { useLang } from '../i18n.jsx';

const STEPS = {
  en: [
    { n: '01', t: 'Observe', d: 'Re-ground in the tree — read the active frontier, recent evidence, ancestor insights, and the current best artifact.' },
    { n: '02', t: 'Ideate', d: 'Propose child hypotheses under a chosen parent, conditioned on validated insights and pruned-node constraints.' },
    { n: '03', t: 'Select', d: 'Choose which pending nodes to run next — frontier control under partial, delayed feedback.' },
    { n: '04', t: 'Dispatch', d: 'Run selected hypotheses in isolated worktrees; each executor evaluates on the dev evaluator and returns a compact report.' },
    { n: '05', t: 'Backpropagate', d: 'Write evidence into leaf nodes and lift causal lessons up the path to the root.' },
    { n: '06', t: 'Decide', d: 'Continue, prune, stop, or merge — promoting only what passes the held-out merge gate.' },
  ],
  zh: [
    { n: '01', t: '观察', d: '重新审视整棵树——读取活跃前沿、近期证据、祖先洞见，以及当前最优产物。' },
    { n: '02', t: '构思', d: '在选定的父节点下提出子假设，以已验证的洞见与被剪枝节点的约束为条件。' },
    { n: '03', t: '选择', d: '挑出接下来运行哪些待定节点——在部分、延迟反馈下的前沿控制。' },
    { n: '04', t: '派发', d: '在隔离的 worktree 中运行所选假设；每个 executor 在开发评估器上评估并回传一份精简报告。' },
    { n: '05', t: '反向传播', d: '把证据写入叶节点，并将因果教训沿路径上提至根节点。' },
    { n: '06', t: '决策', d: '继续、剪枝、停止或合并——只采纳通过留出合并闸门的改动。' },
  ],
};

export default function Method() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  const steps = STEPS[lang];
  return (
    <section className="section" id="method" aria-label="Method">
      <div className="container">
        <Reveal>
          <div className="section-head">
            <span className="kicker">{zh ? '方法' : 'Method'}</span>
            <h2>{zh ? '假设树精炼' : 'Hypothesis-Tree Refinement'}</h2>
            <p className="lead">
              {zh
                ? '一个长生命周期的 coordinator 掌管假设树并运行一个六步循环；短生命周期的 executor 在干净的 worktree 中测试单个节点并回传结构化证据。'
                : 'A long-lived coordinator owns the hypothesis tree and runs a six-step loop; short-lived executors test individual nodes in clean worktrees and return structured evidence.'}
            </p>
          </div>
        </Reveal>

        <Reveal delay={0.08}>
          <div className="process-grid six">
            {steps.map((s) => (
              <SpotlightCard key={s.n} className="tile process-step" spotlightColor="rgba(70, 224, 196, 0.16)">
                <span className="step-num">{s.n}</span>
                <h3>{s.t}</h3>
                <p>{s.d}</p>
              </SpotlightCard>
            ))}
          </div>
        </Reveal>
      </div>

      <Reveal delay={0.1}>
        <div className="container-wide">
          <figure className="figure framework-figure">
            <div className="figure-canvas">
              <img src="assets/images/fig-framework.png" alt="Overall framework of Arbor" />
            </div>
            <figcaption>
              {zh
                ? '这棵树同时是搜索空间、长期记忆、分支级审计轨迹，以及用于验证产物改进的合并策略。'
                : 'The tree is search space, long-term memory, branch-level audit trail, and merge policy for verified artifact improvement.'}
            </figcaption>
          </figure>
        </div>
      </Reveal>
    </section>
  );
}
