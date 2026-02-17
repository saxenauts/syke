# YouTube Browser Extraction Strategy

## Source
YouTube via browser-use + Playwright

## Authentication
- Requires logged-in Google account in browser
- Use existing Chrome profile

## Extraction Approach
1. Navigate to youtube.com/feed/history — watch history
2. Scroll and extract video titles, channels, timestamps
3. Navigate to youtube.com/playlist?list=LL — liked videos
4. Navigate to youtube.com/feed/channels — subscriptions

## Event Types
- `watch` — watched videos
- `like` — liked videos
- `subscribe` — channel subscriptions

## Browser Automation Notes
- YouTube uses infinite scroll for history
- Video elements: `ytd-video-renderer` or `ytd-rich-item-renderer`
- Extract: title, channel name, view count, publish date
- Watch history shows approximate watch time

## Known Issues
- Watch history can be very long — limit scroll depth
- Some videos may be deleted/private — handle gracefully
- YouTube DOM changes frequently
- May need to dismiss cookie/consent banners
