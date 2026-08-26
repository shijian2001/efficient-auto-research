import Threads from '../bits/Threads.jsx';
import Magnet from '../bits/Magnet.jsx';
import RotatingText from '../bits/RotatingText.jsx';
import Reveal from '../components/Reveal.jsx';
import ErrorBoundary from '../components/ErrorBoundary.jsx';
import { useReducedMotion } from '../components/useReducedMotion';
import { IconPaper, IconGithub, IconPlay } from '../components/icons.jsx';
import { useLang } from '../i18n.jsx';

export default function Hero() {
  const reduced = useReducedMotion();
  const { lang } = useLang();
  const zh = lang === 'zh';
  const wm = `url(${import.meta.env.BASE_URL}assets/images/arbor-wordmark.png)`;

  return (
    <section className="hero" id="top">
      <div className="hero-threads" aria-hidden="true">
        {!reduced && (
          <ErrorBoundary>
            <Threads color={[0.27, 0.88, 0.77]} amplitude={1.6} distance={0.4} enableMouseInteraction />
          </ErrorBoundary>
        )}
      </div>

      <div className="hero-inner">
        <span className="hero-eyebrow">
          <span className="dot" /> {zh ? '自主研究系统' : 'Autonomous Research System'}
        </span>

        <h1 className="hero-wordmark" aria-label="Arbor">
          <span
            className="wordmark-mask"
            role="img"
            aria-hidden="true"
            style={{ WebkitMaskImage: wm, maskImage: wm }}
          />
        </h1>

        <div className="hero-rotate" aria-label="Optimize anything">
          <span className="hero-rotate-pre">{zh ? '优化' : 'Optimize'}</span>
          <RotatingText
            texts={
              zh
                ? ['一切', 'ML 模型', '数据流水线', '假设', 'Kaggle 分数', '研究']
                : ['anything', 'ML models', 'data pipelines', 'hypotheses', 'Kaggle scores', 'research']
            }
            mainClassName="hero-rotate-word"
            splitLevelClassName="hero-rotate-split"
            staggerFrom="last"
            staggerDuration={0.025}
            rotationInterval={2200}
            auto={!reduced}
            transition={{ type: 'spring', damping: 26, stiffness: 320 }}
          />
        </div>

        <Reveal delay={0.15} distance={26}>
          <p className="hero-title">
            {zh ? (
              <>
                迈向通用自主研究：基于
                <span className="grad-text">假设树精炼</span>
              </>
            ) : (
              <>
                Toward Generalist Autonomous Research via{' '}
                <span className="grad-text">Hypothesis-Tree Refinement</span>
              </>
            )}
          </p>
        </Reveal>

        <Reveal delay={0.25} distance={20}>
          <p className="hero-desc">
            {zh
              ? 'Arbor 把长周期 AI 研究从一次次孤立的尝试，变成一个累积的过程：假设分叉、实验回传证据、洞见向上传播，只有在留出集上验证有效的改进才会被采纳。'
              : 'Arbor turns long-horizon AI research from isolated attempts into a cumulative process: hypotheses branch, experiments return evidence, insights propagate, and only held-out improvements are promoted.'}
          </p>
        </Reveal>

        <Reveal delay={0.32} distance={18}>
          <div className="hero-actions">
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
            <Magnet padding={70} magnetStrength={4}>
              <a className="btn" href="#demo">
                <IconPlay /> {zh ? '实时演示' : 'Live Demo'}
              </a>
            </Magnet>
          </div>
        </Reveal>

        <Reveal delay={0.42} distance={14}>
          <div className="hero-authors">
            Jiajie Jin<sup>1,†,‡</sup>, Yuyang Hu<sup>1,†</sup>, Kai Qiu<sup>2</sup>,
            Qi Dai<sup>2</sup>, Chong Luo<sup>2</sup>, Guanting Dong<sup>1</sup>,
            Xiaoxi Li<sup>1</sup>, Tong Zhao<sup>1</sup>, Xiaolong Ma<sup>2</sup>,
            Gongrui Zhang<sup>2</sup>, Zhirong Wu<sup>2</sup>, Bei Liu<sup>2</sup>,
            Zhengyuan Yang<sup>2</sup>, Linjie Li<sup>2</sup>, Lijuan Wang<sup>2</sup>,
            Hongjin Qian<sup>1</sup>, Yutao Zhu<sup>1</sup>, Zhicheng Dou<sup>1,*</sup>
          </div>
          <div className="hero-affil">
            <sup>1</sup> {zh ? '中国人民大学高瓴人工智能学院' : 'Gaoling School of Artificial Intelligence, Renmin University of China'}
            <span className="sep" />
            <sup>2</sup> {zh ? '微软研究院' : 'Microsoft Research'}
          </div>
          <div className="hero-note">
            <sup>†</sup> {zh ? '同等贡献' : 'Equal contribution'}
            <span className="sep" />
            <sup>‡</sup> {zh ? '于 MSRA 实习期间完成' : 'Work done during an internship at MSRA'}
            <span className="sep" />
            <sup>*</sup> {zh ? '通讯作者' : 'Corresponding author'}
          </div>
          <div className="author-compact">
            Jiajie Jin, Yuyang Hu, Kai Qiu, Qi Dai, Chong Luo, et al. · {zh ? '中国人民大学' : 'Renmin University of China'} · {zh ? '微软研究院' : 'Microsoft Research'}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
