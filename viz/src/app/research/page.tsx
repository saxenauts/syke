import Nav from "@/components/Nav";
import ResearchHero from "@/components/research/ResearchHero";
import PerceptionReplay from "@/components/research/PerceptionReplay";
import PerceptionTimeline from "@/components/research/PerceptionTimeline";
import HookDemo from "@/components/research/HookDemo";
import ALMALearning from "@/components/research/ALMALearning";
import ProfileEvolution from "@/components/research/ProfileEvolution";
import Footer from "@/components/Footer";

export default function ResearchPage() {
  return (
    <>
      <Nav mode="dark" />
      <main>
        <ResearchHero />

        <div id="process">
          <PerceptionReplay />
          <PerceptionTimeline />
          <HookDemo />
        </div>

        <div id="learning">
          <ALMALearning />
          <ProfileEvolution />
        </div>
      </main>
      <Footer mode="dark" />
    </>
  );
}
