import { useLang } from '../i18n.jsx';

export default function Footer() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  return (
    <footer className="site-footer">
      <div className="container">
        <span className="f-brand">Arbor</span>
        <span className="f-mid">{zh ? '中国人民大学 · 微软研究院' : 'Renmin University of China · Microsoft Research'}</span>
        <a href="mailto:jinjiajie@ruc.edu.cn">{zh ? '联系我们' : 'Contact'}</a>
      </div>
    </footer>
  );
}
