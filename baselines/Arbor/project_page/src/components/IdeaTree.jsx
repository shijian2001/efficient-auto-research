import Reveal from './Reveal.jsx';
import { useLang } from '../i18n.jsx';

// Hypothesis-exploration trace from the BrowseComp run (dev-set, 40Q bc_val).
// Scores are dev-set iteration scores; baseline 50%, merged trunk 62.5%.
const BASELINE = 50;

const BRANCHES = {
  en: [
    {
      id: '1',
      score: 42.5,
      status: 'pruned',
      label: 'Track multiple candidates in a PASS / FAIL constraint table',
    },
    {
      id: '2',
      score: 52.5,
      status: 'done',
      label: "Spawn a devil's-advocate agent to rebut the first answer",
    },
    {
      id: '3',
      score: 45,
      status: 'pruned',
      label: 'Enumerate type-anchored candidate lists up front',
    },
    {
      id: '4',
      score: 25,
      status: 'pruned',
      label: 'Decompose into independent sub-questions, then intersect',
    },
    {
      id: '5',
      score: 55,
      status: 'done',
      label: 'Run four independent trajectories + a selecting judge',
      children: [
        { id: '5.1', score: 47.5, status: 'pruned', label: 'Persona-diversified agents (style per trajectory)' },
        { id: '5.2', score: 57.5, status: 'done', label: 'Give the judge its own 8-call search budget' },
        { id: '5.3', score: 52.5, status: 'done', label: 'Fix the "Candidate N" answer-parser bug' },
        {
          id: '5.4',
          score: 62.5,
          status: 'merged',
          win: true,
          label: 'Let the judge search beyond the candidate set (20-step budget)',
        },
      ],
    },
  ],
  zh: [
    {
      id: '1',
      score: 42.5,
      status: 'pruned',
      label: '用 PASS / FAIL 约束表追踪多个候选',
    },
    {
      id: '2',
      score: 52.5,
      status: 'done',
      label: '派生一个"魔鬼代言人"智能体来反驳第一个答案',
    },
    {
      id: '3',
      score: 45,
      status: 'pruned',
      label: '预先枚举按类型锚定的候选清单',
    },
    {
      id: '4',
      score: 25,
      status: 'pruned',
      label: '分解为独立子问题，再取交集',
    },
    {
      id: '5',
      score: 55,
      status: 'done',
      label: '运行四条独立轨迹 + 一个择优裁决器',
      children: [
        { id: '5.1', score: 47.5, status: 'pruned', label: '多样化角色的智能体（每条轨迹一种风格）' },
        { id: '5.2', score: 57.5, status: 'done', label: '给裁决器自己的 8 次检索预算' },
        { id: '5.3', score: 52.5, status: 'done', label: '修复"Candidate N"答案解析器的 bug' },
        {
          id: '5.4',
          score: 62.5,
          status: 'merged',
          win: true,
          label: '让裁决器在候选集之外检索（20 步预算）',
        },
      ],
    },
  ],
};

const STATUS_LABEL = {
  en: { merged: 'Merged ★', done: 'Explored', pruned: 'Pruned' },
  zh: { merged: '已合并 ★', done: '已探索', pruned: '已剪枝' },
};

function scoreClass(score) {
  if (score > BASELINE) return 'sc-up';
  if (score < BASELINE) return 'sc-down';
  return 'sc-mid';
}

function TreeNode({ node, statusLabel }) {
  return (
    <li className="tree-node">
      <div className={`tnode${node.win ? ' win' : ''}`}>
        <span className="tnode-id">{node.id}</span>
        <div className="tnode-body">
          <p className="tnode-label">{node.label}</p>
          <div className="tnode-meta">
            <span className={`tnode-score ${scoreClass(node.score)}`}>{node.score.toFixed(1)}%</span>
            <span className={`tnode-status st-${node.status}`}>{statusLabel[node.status]}</span>
          </div>
        </div>
      </div>
      {node.children && (
        <ul className="tree-children">
          {node.children.map((c) => (
            <TreeNode key={c.id} node={c} statusLabel={statusLabel} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function IdeaTree() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  const branches = BRANCHES[lang];
  const statusLabel = STATUS_LABEL[lang];
  return (
    <Reveal delay={0.05}>
      <div className="tree-panel">
        <div className="tree-head">
          <div>
            <span className="tree-title">{zh ? '假设探索' : 'Hypothesis exploration'}</span>
            <span className="tree-sub">{zh ? '20 循环 BrowseComp 运行 · 开发集分数（40 题）' : '20-cycle BrowseComp run · dev-set scores (40Q)'}</span>
          </div>
          <div className="tree-runmeta">
            <span><b>9</b> {zh ? '实验' : 'experiments'}</span>
            <span><b>1</b> {zh ? '合并' : 'merged'}</span>
            <span><b>3</b> {zh ? '剪枝' : 'pruned'}</span>
          </div>
        </div>

        <div className="tree-root">
          <span className="tnode-id root">ROOT</span>
          <div className="tnode-body">
            <p className="tnode-label">{zh ? 'BrowseComp ReAct 基线' : 'BrowseComp ReAct baseline'}</p>
            <div className="tnode-meta">
              <span className="tnode-score sc-mid">dev 50.0%</span>
              <span className="tree-arrow">{zh ? '→ 合并主干 62.5%' : '→ merged trunk 62.5%'}</span>
            </div>
          </div>
        </div>

        <ul className="tree-children root-children">
          {branches.map((n) => (
            <TreeNode key={n.id} node={n} statusLabel={statusLabel} />
          ))}
        </ul>

        <div className="tree-legend">
          <span><i className="dot sc-up" /> {zh ? '高于基线' : 'above baseline'}</span>
          <span><i className="dot sc-mid" /> {zh ? '等于基线' : 'at baseline'}</span>
          <span><i className="dot sc-down" /> {zh ? '低于基线' : 'below baseline'}</span>
        </div>
      </div>
    </Reveal>
  );
}
