import Nav from "@/components/Nav";
import ProductHero from "@/components/product/ProductHero";
import ProductContextGap from "@/components/product/ProductContextGap";
import PlatformGrid from "@/components/product/PlatformGrid";
import FeatureHighlights from "@/components/product/FeatureHighlights";
import ProductArchitecture from "@/components/product/ProductArchitecture";
import ProductTryIt from "@/components/product/ProductTryIt";
import Footer from "@/components/Footer";

export default function Home() {
  return (
    <>
      <Nav mode="light" />
      <main>
        <ProductHero />
        <ProductContextGap />
        <PlatformGrid />
        <FeatureHighlights />
        <ProductArchitecture />
        <ProductTryIt />
      </main>
      <Footer mode="light" />
    </>
  );
}
