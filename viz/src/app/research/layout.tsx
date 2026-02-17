"use client";

export default function ResearchLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="dark bg-background min-h-screen">
      {children}
    </div>
  );
}
