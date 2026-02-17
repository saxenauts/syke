import Nav from "@/components/Nav";
import ProductHero from "@/components/product/ProductHero";
import ProductGetStarted from "@/components/product/ProductGetStarted";
import ProductContextGap from "@/components/product/ProductContextGap";
import PlatformGrid from "@/components/product/PlatformGrid";
import FeatureHighlights from "@/components/product/FeatureHighlights";
import ProductArchitecture from "@/components/product/ProductArchitecture";
import Footer from "@/components/Footer";

export default function Home() {
  return (
    <>
      <Nav mode="light" />
      <main>
        <ProductHero />
        <ProductGetStarted />
        <ProductContextGap />
        <PlatformGrid />
        <FeatureHighlights />
        <ProductArchitecture />
      </main>
      <Footer mode="light" />
    </>
  );
}
