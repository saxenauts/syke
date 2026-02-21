"use client";
import { Suspense, lazy } from "react";
const Manifesto = lazy(() => import("./Manifesto"));

export default function ManifestoSection() {
  return (
    <Suspense fallback={null}>
      <Manifesto />
    </Suspense>
  );
}
