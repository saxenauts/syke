# Local Development Guide

This repository contains two separate Vercel projects for iterative local development.

## Project Structure

```
claude-hack/
├── docs-site/          # Documentation (Nextra) → https://syke-docs.vercel.app
│   ├── pages/          # MDX content
│   ├── theme.config.tsx
│   └── package.json
│
└── viz/                # Interactive Demo → https://syke-ai.vercel.app
    ├── src/
    │   ├── app/        # Next.js App Router
    │   └── components/ # React components
    └── package.json
```

## Quick Start

### Docs Site (Nextra)

```bash
cd docs-site
npm install
npm run dev
```

Open http://localhost:3000

**Hot reload:** Edit any `.mdx` file in `pages/` and see live updates.

### Viz Site (Interactive Demo)

```bash
cd viz
npm install
npm run dev
```

Open http://localhost:3000

**Hot reload:** Edit components in `src/` and see live updates.

## Auto-Deploy

Both projects auto-deploy to Vercel on push to `main`:

- **docs-site/** changes → triggers `.github/workflows/deploy-docs.yml`
  - Deploys to https://syke-docs.vercel.app
  - Also available at https://docs-syke.vercel.app

- **viz/** changes → triggers `.github/workflows/deploy-viz.yml`
  - Deploys to https://syke-ai.vercel.app

Deployments complete in ~1-2 minutes.

## Local Development Workflow

### 1. Start local dev server
```bash
cd docs-site  # or viz
npm run dev
```

### 2. Make changes
- Edit files (MDX, React components, styles)
- See live updates at localhost:3000

### 3. Commit and push
```bash
git add .
git commit -m "feat: your changes"
git push origin main
```

### 4. Auto-deploy happens
- GitHub Action triggers automatically
- Watch at: https://github.com/saxenauts/syke/actions
- Live in ~2 minutes

## Manual Deployment (if needed)

If auto-deploy fails or you need to deploy immediately:

```bash
# From docs-site/ or viz/
vercel --prod
```

## Vercel Project IDs

Configured in GitHub Actions secrets and Vercel dashboard. See `.github/workflows/` for deployment config.

## Cache Configuration

Both projects use the same cache strategy (configured in `vercel.json`):

- **HTML pages**: Cache for 1 hour, serve stale for 24 hours while revalidating
- **Static assets** (`_next/static/`): Cache forever (immutable, content-hashed)

## Common Commands

```bash
# Install dependencies
npm install

# Start dev server (with hot reload)
npm run dev

# Build for production (test locally)
npm run build
npm start

# Deploy to Vercel
vercel --prod

# Check Vercel deployment status
vercel list
```

## Troubleshooting

**Port already in use:**
```bash
# Kill process on port 3000
lsof -ti:3000 | xargs kill -9
```

**Dependencies out of sync:**
```bash
rm -rf node_modules package-lock.json
npm install
```

**Auto-deploy not triggering:**
- Check GitHub Actions: https://github.com/saxenauts/syke/actions
- Verify secrets are set: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`
- Check workflow file paths match your changes

**Vercel password protection:**
- Go to your Vercel project → Settings → Deployment Protection
- Disable "Vercel Authentication" to make the site public
