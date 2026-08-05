import Reveal from '../components/Reveal.jsx';
import { useLang } from '../i18n.jsx';

export default function Demo() {
  const { lang } = useLang();
  const zh = lang === 'zh';
  const src = `${import.meta.env.BASE_URL}assets/demo/demo.mp4`;

  return (
    <section className="section section-demo" id="demo" aria-label="Product demo">
      <div className="container-wide">
        <Reveal>
          <div className="demo-frame">
            <video
              className="demo-video"
              src={src}
              controls
              playsInline
              preload="metadata"
              aria-label={zh ? 'Arbor 运行演示视频' : 'Arbor run demo video'}
            >
              {zh
                ? '你的浏览器不支持视频播放。'
                : 'Your browser does not support the video tag.'}
            </video>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
