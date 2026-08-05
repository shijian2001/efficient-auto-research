import Reveal from '../components/Reveal.jsx';
import { useLang } from '../i18n.jsx';

export default function Problem() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  return (
    <section className="section" aria-label="Research problem">
      <div className="container">
        <div className="split">
          <Reveal>
            <div>
              <span className="kicker">{zh ? '研究问题' : 'Research Problem'}</span>
              <h2>{zh ? '自主研究需要一个持久的研究状态。' : 'Autonomous research needs a durable research state.'}</h2>
            </div>
          </Reveal>
          <Reveal delay={0.1} className="text-block">
            <p>
              {zh ? (
                <>
                  Arbor 研究<strong style={{ color: 'var(--ink)' }}>自主优化（Autonomous Optimization）</strong>：
                  智能体接收一个初始产物、一个目标、一个开发评估器与一个留出评估器，然后通过迭代实验改进该产物，
                  无需步骤级的监督。
                </>
              ) : (
                <>
                  Arbor studies <strong style={{ color: 'var(--ink)' }}>Autonomous Optimization</strong>:
                  an agent receives an initial artifact, an objective, a development evaluator, and a
                  held-out evaluator, then improves the artifact through iterative experimentation
                  without step-level supervision.
                </>
              )}
            </p>

            <div className="eqn" role="img" aria-label="P equals tuple of M-zero, O, E-dev, E-test">
              <div className="eqn-line">
                <span className="v">P</span>
                <span className="op">=</span>
                <span className="par">(</span>
                <span className="v">M<sub>0</sub></span><span className="sep">,</span>
                <span className="v">O</span><span className="sep">,</span>
                <span className="v">E<sub>dev</sub></span><span className="sep">,</span>
                <span className="v">E<sub>test</sub></span>
                <span className="par">)</span>
              </div>
              <div className="eqn-legend">
                <div className="eqn-term"><span className="sym">M<sub>0</sub></span><span className="desc">{zh ? '初始产物' : 'initial artifact'}</span></div>
                <div className="eqn-term"><span className="sym">O</span><span className="desc">{zh ? '目标' : 'objective'}</span></div>
                <div className="eqn-term"><span className="sym">E<sub>dev</sub></span><span className="desc">{zh ? '开发评估器' : 'development evaluator'}</span></div>
                <div className="eqn-term"><span className="sym">E<sub>test</sub></span><span className="desc">{zh ? '留出评估器' : 'held-out evaluator'}</span></div>
              </div>
            </div>

            <p>
              {zh
                ? '难点不只在于运行更久。系统必须记住试过什么、什么失败了、什么成功迁移，以及哪条分支值得下一次实验。'
                : 'The hard part is not merely running longer. The system must preserve what was tried, what failed, what transferred, and which branch deserves the next experiment.'}
            </p>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
