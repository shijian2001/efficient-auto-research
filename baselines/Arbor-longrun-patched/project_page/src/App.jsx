import Intro from './components/Intro.jsx';
import Header from './sections/Header.jsx';
import Hero from './sections/Hero.jsx';
import ProofStrip from './sections/ProofStrip.jsx';
import Demo from './sections/Demo.jsx';
import Problem from './sections/Problem.jsx';
import Method from './sections/Method.jsx';
import Results from './sections/Results.jsx';
import CaseStudy from './sections/CaseStudy.jsx';
import WhyItMatters from './sections/WhyItMatters.jsx';
import Resources from './sections/Resources.jsx';
import Footer from './sections/Footer.jsx';
import { LangProvider } from './i18n.jsx';

export default function App() {
  return (
    <LangProvider>
      <Intro />
      <Header />
      <main>
        <Hero />
        <Demo />
        <ProofStrip />
        <Problem />
        <Method />
        <Results />
        <CaseStudy />
        <WhyItMatters />
        <Resources />
      </main>
      <Footer />
    </LangProvider>
  );
}
