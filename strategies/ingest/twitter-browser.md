# Twitter/X Browser Extraction Strategy

## Source
Twitter/X via browser-use + Playwright

## Authentication
- Requires logged-in browser session
- Use existing Chrome profile or manual login

## Extraction Approach
1. Navigate to profile page
2. Scroll through tweets tab — extract tweets
3. Navigate to likes tab — extract liked tweets
4. Navigate to bookmarks — extract bookmarked tweets
5. Navigate to following — extract following list

## Event Types
- `tweet` — user's own tweets
- `like` — liked tweets
- `bookmark` — bookmarked tweets
- `follow` — accounts followed

## Browser Automation Notes
- Twitter uses infinite scroll — need scroll + wait pattern
- Content loads dynamically — wait for tweet elements
- Rate limiting: don't scroll too fast
- Use `article[data-testid="tweet"]` selector for tweet elements

## Known Issues
- Twitter frequently changes DOM structure
- May need to handle login prompts/CAPTCHAs
- Bookmarks are private — requires authenticated session
- Following list may be very long — limit to first few pages
