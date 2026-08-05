import { useState } from 'react';
import Magnet from '../bits/Magnet.jsx';
import Reveal from '../components/Reveal.jsx';
import { IconPaper, IconGithub, IconCopy, IconCheck } from '../components/icons.jsx';
import { useLang } from '../i18n.jsx';

const BIBTEX = `@misc{jin2026arbor,
  title  = {Toward Generalist Autonomous Research via Hypothesis-Tree Refinement},
  author = {Jiajie Jin and Yuyang Hu and Kai Qiu and Qi Dai and Chong Luo and
            Guanting Dong and Xiaoxi Li and Tong Zhao and Xiaolong Ma and
            Gongrui Zhang and Zhirong Wu and Bei Liu and Zhengyuan Yang and
            Linjie Li and Lijuan Wang and Hongjin Qian and Yutao Zhu and Zhicheng Dou},
  year   = {2026},
  eprint = {2606.11926},
  archivePrefix = {arXiv},
  url    = {https://arxiv.org/abs/2606.11926}
}`;

export default function Resources() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(BIBTEX);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = BIBTEX;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <section className="section" id="resources" aria-label="Resources">
      <div className="container">
        <div className="resources-grid">
          <Reveal>
            <div>
              <span className="kicker">{zh ? '资源' : 'Resources'}</span>
              <h2>{zh ? '论文、代码与引用。' : 'Paper, code, and citation.'}</h2>
              <p className="lead" style={{ marginTop: 18 }}>
                {zh
                  ? 'Arbor 作为面向自主优化的开源研究系统发布。作为一个持续进行的项目，本报告会随研究推进不断更新。'
                  : 'Arbor is released as an open-source research system for Autonomous Optimization. The report is a living technical document for an ongoing project.'}
              </p>
              <div className="hero-actions" style={{ justifyContent: 'flex-start', marginTop: 28 }}>
                <Magnet padding={70} magnetStrength={4}>
                  <a className="btn btn-primary" href="https://arxiv.org/pdf/2606.11926" target="_blank" rel="noreferrer">
                    <IconPaper /> {zh ? '阅读论文' : 'Read Paper'}
                  </a>
                </Magnet>
                <Magnet padding={70} magnetStrength={4}>
                  <a className="btn" href="https://github.com/RUC-NLPIR/Arbor" target="_blank" rel="noreferrer">
                    <IconGithub /> GitHub
                  </a>
                </Magnet>
              </div>
            </div>
          </Reveal>

          <Reveal delay={0.1}>
            <div className="citation">
              <div className="citation-head">
                <span>BibTeX</span>
                <button type="button" onClick={copy}>
                  {copied ? <IconCheck /> : <IconCopy />}
                  {copied ? (zh ? '已复制' : 'Copied') : (zh ? '复制' : 'Copy')}
                </button>
              </div>
              <pre><code>{BIBTEX}</code></pre>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
