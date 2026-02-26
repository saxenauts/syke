# Syke Viz

Product site and synthesis visualization for [Syke](https://github.com/saxenauts/syke) — agentic memory for your AI tools.

**Live**: [syke-ai.vercel.app](https://syke-ai.vercel.app)

## Stack

- Next.js 15 (App Router)
- Tailwind CSS
- Framer Motion
- Three.js / WebGL (LatentFluid background)
- Deployed on Vercel

## Development

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Structure

`src/components/product/` — Product page sections (hero, architecture, features, get started)
`src/components/` — Shared components (SectionHeader, LatentFluid WebGL background)
`public/` — Static assets

## Related

- [Syke repo](https://github.com/saxenauts/syke) — Core package
- [Docs site](https://syke-docs.vercel.app) — Reference documentation
