import Counter from '../components/Counter.jsx';
import Reveal from '../components/Reveal.jsx';
import { useLang } from '../i18n.jsx';

// Held-out (test) metrics from the paper's main results table.
const ROWS = {
  en: [
    { task: 'Optimizer Design', dir: 'steps ↓', init: '3325', codex: '3325', claude: '3287.5', arbor: '3237.5', gain: 2.63, dec: 2, suf: '%' },
    { task: 'Architecture Design', dir: 'loss ↓', init: '1.098', codex: '1.083', claude: '1.033', arbor: '1.028', gain: 6.38, dec: 2, suf: '%' },
    { task: 'Terminal-Bench 2.0', dir: 'pass ↑', init: '69.81', codex: '73.59', claude: '71.70', arbor: '77.36', gain: 7.55, dec: 2, suf: '' },
    { task: 'BrowseComp', dir: 'acc ↑', init: '45.33', codex: '50.00', claude: '53.33', arbor: '67.67', gain: 22.34, dec: 2, suf: '' },
    { task: 'Search-Agent Data', dir: 'gap ↑', init: '5.00', codex: '9.00', claude: '12.00', arbor: '18.00', gain: 13.0, dec: 2, suf: '' },
    { task: 'Math-Reasoning Data', dir: 'gap ↑', init: '1.04', codex: '6.25', claude: '8.33', arbor: '20.83', gain: 19.79, dec: 2, suf: '' },
  ],
  zh: [
    { task: '优化器设计', dir: '步数 ↓', init: '3325', codex: '3325', claude: '3287.5', arbor: '3237.5', gain: 2.63, dec: 2, suf: '%' },
    { task: '架构设计', dir: 'loss ↓', init: '1.098', codex: '1.083', claude: '1.033', arbor: '1.028', gain: 6.38, dec: 2, suf: '%' },
    { task: 'Terminal-Bench 2.0', dir: 'pass ↑', init: '69.81', codex: '73.59', claude: '71.70', arbor: '77.36', gain: 7.55, dec: 2, suf: '' },
    { task: 'BrowseComp', dir: 'acc ↑', init: '45.33', codex: '50.00', claude: '53.33', arbor: '67.67', gain: 22.34, dec: 2, suf: '' },
    { task: '搜索智能体数据', dir: 'gap ↑', init: '5.00', codex: '9.00', claude: '12.00', arbor: '18.00', gain: 13.0, dec: 2, suf: '' },
    { task: '数学推理数据', dir: 'gap ↑', init: '1.04', codex: '6.25', claude: '8.33', arbor: '20.83', gain: 19.79, dec: 2, suf: '' },
  ],
};

const MLE = {
  en: [
    { v: 100.0, suf: '%', l: 'valid submissions' },
    { v: 95.45, suf: '%', l: 'above median' },
    { v: 77.27, suf: '%', l: 'gold medal', gold: true },
    { v: 86.36, suf: '%', l: 'any medal', gold: true },
  ],
  zh: [
    { v: 100.0, suf: '%', l: '有效提交' },
    { v: 95.45, suf: '%', l: '中位数以上' },
    { v: 77.27, suf: '%', l: '金牌', gold: true },
    { v: 86.36, suf: '%', l: '任意奖牌', gold: true },
  ],
};

export default function Results() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  const rows = ROWS[lang];
  const mle = MLE[lang];
  return (
    <section className="band band-deep" id="results" aria-label="Results">
      <div className="container-wide">
        <Reveal>
          <div className="section-head">
            <span className="kicker">{zh ? '结果' : 'Results'}</span>
            <h2>{zh ? '在每个任务上都取得最佳留出结果。' : 'Best held-out results on every task.'}</h2>
            <p className="lead">
              {zh
                ? '同一个控制器横跨模型训练、评测框架工程与数据合成——只有材料、目标、评估器与预算在变。在全部六个任务上，Arbor 都在留出测试上胜过强大的单智能体基线。'
                : 'One controller across model training, harness engineering, and data synthesis — only the material, objective, evaluator, and budget change. Arbor wins the held-out test on all six tasks against strong single-agent baselines.'}
            </p>
          </div>
        </Reveal>

        <Reveal delay={0.08}>
          <div className="table-wrap">
            <table className="result-table results-main">
              <thead>
                <tr>
                  <th>{zh ? '任务' : 'Task'} <span className="th-sub">{zh ? '留出测试' : 'held-out test'}</span></th>
                  <th>{zh ? '初始' : 'Initial'}</th>
                  <th>Codex</th>
                  <th>Claude Code</th>
                  <th className="col-arbor">Arbor</th>
                  <th>{zh ? '增益' : 'Gain'}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.task}>
                    <td>
                      {r.task}
                      <span className="td-dir">{r.dir}</span>
                    </td>
                    <td className="num">{r.init}</td>
                    <td className="num">{r.codex}</td>
                    <td className="num">{r.claude}</td>
                    <td className="num col-arbor">{r.arbor}</td>
                    <td className="gain">
                      +<Counter to={r.gain} decimals={r.dec} duration={1.4} />
                      {r.suf}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Reveal>

        <Reveal delay={0.1}>
          <div className="mle-band">
            <div className="mle-head">
              <span className="panel-kicker">MLE-Bench Lite · GPT-5.5</span>
              <p>
                {zh
                  ? '在我们的对比中取得最佳任意奖牌率——领先于次优系统的 81.82%，且遵循官方基准协议。'
                  : 'Best Any-Medal rate in our comparison — ahead of the next-best system at 81.82%, under the official benchmark protocol.'}
              </p>
            </div>
            <div className="mle-stats">
              {mle.map((m) => (
                <div className={`mle-stat${m.gold ? ' gold' : ''}`} key={m.l}>
                  <div className="v">
                    <Counter to={m.v} decimals={2} duration={1.5} />
                    {m.suf}
                  </div>
                  <div className="l">{m.l}</div>
                </div>
              ))}
            </div>
          </div>
        </Reveal>

        <Reveal delay={0.12}>
          <figure className="figure">
            <div className="figure-canvas">
              <img src="assets/images/fig-overview.png" alt="Arbor overview and normalized held-out gains" />
            </div>
            <figcaption>
              {zh
                ? '一棵实时的假设树、开发轨迹，以及跨全部任务的归一化留出增益。'
                : 'A live hypothesis tree, development trajectory, and normalized held-out gains across all tasks.'}
            </figcaption>
          </figure>
        </Reveal>
      </div>
    </section>
  );
}
