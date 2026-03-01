import Nav from "@/components/Nav";
import ProductHero from "@/components/product/ProductHero";
import ProductContextGap from "@/components/product/ProductContextGap";
import FeatureHighlights from "@/components/product/FeatureHighlights";
import ProductGetStarted from "@/components/product/ProductGetStarted";
import ManifestoSection from "@/components/ManifestoSection";
import Footer from "@/components/Footer";
import AgentMosaic from "@/components/product/AgentMosaic";

export default function Home() {
  return (
    <div className="min-h-screen w-full overflow-x-hidden bg-[var(--bg-primary)] text-[var(--text-primary)] selection:bg-[var(--accent-acid)]/30 selection:text-[var(--accent-acid)] relative">
      <Nav />
      <main>
        <ProductHero />
        <AgentMosaic />
        <ProductContextGap />
        <FeatureHighlights />
        <ManifestoSection />
        <ProductGetStarted />
      </main>
      <Footer />
    </div>
  );
}
